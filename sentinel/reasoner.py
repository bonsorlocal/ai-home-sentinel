"""Phase 7 Reasoner - contextual event scoring and tier promotion.

The reasoner sits between perception (motion/object/face) and the Event
Ledger. Before each event is written, classify() looks at:

  - What the event *is* (source, entities)
  - What happened *recently* (a window of prior events from the ledger)
  - What *time* it is (night vs. day)

...and returns a (tier, importance) recommendation that replaces the
hard-coded values previously inline in runtime.py.

Tier 2 events are "alert-worthy" -- they show with a distinct badge in the
dashboard and will eventually trigger phone notifications (Phase 7b).

Tier promotion rules (first matching rule wins, highest priority first):

  P1  Unknown face detected                              -> tier 2, imp 0.75
  P2  >=N person detections within W minutes             -> tier 2, imp 0.80
  P3  Person detected during night hours (22:00-05:59)   -> tier 2, imp 0.70

Default fallback (no rule fires):
  object - person                                        -> tier 1, imp 0.40
  object - other                                         -> tier 1, imp 0.30
  motion                                                 -> tier 1, imp 0.20
  face - known                                           -> tier 1, imp 0.50
  everything else                                        -> tier 1, imp 0.20
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sentinel.config import Config
from sentinel.events import EventRecord


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO-8601 timestamp to a timezone-aware UTC datetime."""
    try:
        ts = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


class Reasoner:
    """Rule-based event tier classifier for Phase 7."""

    def __init__(self, config: Config) -> None:
        cfg: Dict[str, Any] = config.get("reasoner") or {}
        self._enabled: bool = bool(cfg.get("enabled", True))
        self._night_start: int = int(cfg.get("night_hour_start", 22))
        self._night_end: int = int(cfg.get("night_hour_end", 6))
        self._repeat_window_minutes: int = int(cfg.get("repeat_person_window_minutes", 5))
        self._repeat_threshold: int = int(cfg.get("repeat_person_threshold", 3))
        self._motion_precedes_seconds: int = int(cfg.get("motion_precedes_seconds", 90))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        source: str,
        entities: Dict[str, Any],
        recent_events: List[EventRecord],
        ts: Optional[datetime] = None,
    ) -> Tuple[int, float]:
        """Return (tier, importance) for an event not yet in the ledger.

        Parameters
        ----------
        source:
            Event source string -- one of motion, object, face, audio, system.
        entities:
            Raw entities dict for the event.
        recent_events:
            Window of already-committed events for context. Pass [] if none.
        ts:
            Intended timestamp. Defaults to now (UTC).
        """
        if not self._enabled:
            return self._defaults(source, entities)

        now = ts or datetime.now(timezone.utc)
        with self._lock:
            return self._apply_rules(source, entities, recent_events, now)

    def score_record(self, record: EventRecord) -> Tuple[int, float]:
        """Re-score an existing EventRecord in isolation (no context window).

        Useful for back-filling tiers on historical data or display-only
        re-classification. Does NOT update the database.
        """
        ts = _parse_ts(record.timestamp)
        return self.classify(record.source, record.entities, [], ts)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "night_hours": f"{self._night_start:02d}:00-{self._night_end:02d}:00",
            "repeat_person_window_minutes": self._repeat_window_minutes,
            "repeat_person_threshold": self._repeat_threshold,
        }

    # ------------------------------------------------------------------
    # Rule engine
    # ------------------------------------------------------------------

    def _apply_rules(
        self,
        source: str,
        entities: Dict[str, Any],
        recent: List[EventRecord],
        ts: datetime,
    ) -> Tuple[int, float]:
        # ---- face events ------------------------------------------------
        if source == "face":
            face_data = entities.get("face", {})
            known = bool(face_data.get("known", False))
            name = str(face_data.get("name", "")).lower()
            if not known:
                return 2, 0.75  # P1 - unknown face
            if name.startswith("thief"):
                return 2, 0.90
            return 1, 0.50

        # ---- object detection events ------------------------------------
        if source == "object":
            detections: List[Dict[str, Any]] = entities.get("detections", [])
            labels = [str(d.get("label", "")) for d in detections]

            if "person" in labels:
                # P2 - repeated person sightings in window
                if self._recent_person_count(recent, ts) >= self._repeat_threshold - 1:
                    return 2, 0.80

                # P3 - nighttime person
                if self._is_night(ts):
                    return 2, 0.70

                return 1, 0.40

            return 1, 0.30  # non-person object

        # ---- motion events ----------------------------------------------
        if source == "motion":
            return 1, 0.20

        # ---- everything else -------------------------------------------
        return 1, 0.20

    def _defaults(self, source: str, entities: Dict[str, Any]) -> Tuple[int, float]:
        """Static defaults used when the reasoner is disabled."""
        if source == "face":
            face = entities.get("face", {})
            if not face.get("known", False):
                return 1, 0.60
            return 1, 0.50
        if source == "object":
            labels = [d.get("label", "") for d in entities.get("detections", [])]
            return (1, 0.40) if "person" in labels else (1, 0.30)
        if source == "motion":
            return 1, 0.20
        return 1, 0.20

    # ------------------------------------------------------------------
    # Time / window helpers
    # ------------------------------------------------------------------

    def _is_night(self, ts: datetime) -> bool:
        hour = ts.hour
        start, end = self._night_start, self._night_end
        if start > end:
            # Wraps midnight: e.g. 22-6 -> night if hour >= 22 OR hour < 6
            return hour >= start or hour < end
        return start <= hour < end

    def _recent_person_count(
        self, recent: List[EventRecord], ts: datetime
    ) -> int:
        """Count person-object events within the repeat window before ts."""
        cutoff = ts - timedelta(minutes=self._repeat_window_minutes)
        count = 0
        for ev in recent:
            if ev.source != "object":
                continue
            try:
                ev_ts = _parse_ts(ev.timestamp)
            except Exception:  # noqa: BLE001
                continue
            if ev_ts < cutoff:
                continue
            labels = [d.get("label", "") for d in ev.entities.get("detections", [])]
            if "person" in labels:
                count += 1
        return count

    def _recent_motion(self, recent: List[EventRecord], ts: datetime) -> bool:
        """Return True if a motion event occurred within _motion_precedes_seconds."""
        cutoff = ts - timedelta(seconds=self._motion_precedes_seconds)
        for ev in recent:
            if ev.source != "motion":
                continue
            try:
                ev_ts = _parse_ts(ev.timestamp)
            except Exception:  # noqa: BLE001
                continue
            if ev_ts >= cutoff:
                return True
        return False
