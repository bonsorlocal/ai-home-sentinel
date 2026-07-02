"""Tests for sentinel.reasoner (Phase 7).

All tests are pure Python -- no Pi, no camera, no network required.
Events are constructed in-memory using EventRecord dataclasses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from sentinel.events import EventRecord
from sentinel.reasoner import Reasoner, _parse_ts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(overrides: Dict[str, Any] | None = None) -> MagicMock:
    """Return a minimal mock Config that serves the reasoner section."""
    cfg_data: Dict[str, Any] = {}
    if overrides:
        cfg_data.update(overrides)

    config = MagicMock()
    config.get = MagicMock(side_effect=lambda *args, **kwargs: cfg_data if args == ("reasoner",) else None)
    return config


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _motion_event(ts: datetime) -> EventRecord:
    return EventRecord(
        id=1,
        timestamp=_ts(ts),
        source="motion",
        tier=1,
        importance=0.2,
        title="Motion detected",
        summary="",
        entities={"area": 5000},
        snapshot_path=None,
        clip_path=None,
        acknowledged=False,
        notified=False,
    )


def _person_event(ts: datetime) -> EventRecord:
    return EventRecord(
        id=2,
        timestamp=_ts(ts),
        source="object",
        tier=1,
        importance=0.4,
        title="Detected: person",
        summary="person (85%)",
        entities={"detections": [{"label": "person", "confidence": 0.85}]},
        snapshot_path=None,
        clip_path=None,
        acknowledged=False,
        notified=False,
    )


def _face_event(known: bool = True, name: str = "Alice") -> EventRecord:
    return EventRecord(
        id=3,
        timestamp=_ts(datetime.now(timezone.utc)),
        source="face",
        tier=1,
        importance=0.5,
        title=f"Known face: {name}" if known else "Unknown face detected",
        summary="",
        entities={"face": {"known": known, "name": name, "distance": 0.3}},
        snapshot_path=None,
        clip_path=None,
        acknowledged=False,
        notified=False,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reasoner() -> Reasoner:
    return Reasoner(_make_config())


# ---------------------------------------------------------------------------
# _parse_ts tests
# ---------------------------------------------------------------------------

class TestParseTs:
    def test_iso_with_z(self) -> None:
        dt = _parse_ts("2024-01-15T03:00:00Z")
        assert dt.tzinfo is not None
        assert dt.hour == 3

    def test_iso_with_offset(self) -> None:
        dt = _parse_ts("2024-01-15T03:00:00+00:00")
        assert dt.tzinfo is not None

    def test_naive_iso(self) -> None:
        dt = _parse_ts("2024-01-15T03:00:00")
        assert dt.tzinfo is not None  # defaults to UTC

    def test_garbage_returns_now(self) -> None:
        dt = _parse_ts("not-a-date")
        assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# Motion event tests
# ---------------------------------------------------------------------------

class TestMotionClassification:
    def test_motion_always_tier1(self, reasoner: Reasoner) -> None:
        tier, imp = reasoner.classify("motion", {"area": 3000}, [])
        assert tier == 1
        assert imp == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# Object (person) event tests
# ---------------------------------------------------------------------------

class TestPersonClassification:
    def test_daytime_person_no_context_is_tier1(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc)  # 14:00 UTC
        tier, imp = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
            ts=ts,
        )
        assert tier == 1
        assert imp == pytest.approx(0.40)

    def test_nighttime_person_is_tier2(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 23, 30, tzinfo=timezone.utc)  # 23:30
        tier, imp = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
            ts=ts,
        )
        assert tier == 2
        assert imp == pytest.approx(0.70)

    def test_night_boundary_start(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 22, 0, tzinfo=timezone.utc)  # exactly 22:00
        tier, _ = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
            ts=ts,
        )
        assert tier == 2

    def test_night_boundary_end_just_before(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 5, 59, tzinfo=timezone.utc)  # 05:59 still night
        tier, _ = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
            ts=ts,
        )
        assert tier == 2

    def test_night_boundary_end_just_after(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 6, 0, tzinfo=timezone.utc)  # 06:00 daytime
        tier, _ = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
            ts=ts,
        )
        assert tier == 1

    def test_person_after_motion_stays_tier1(self, reasoner: Reasoner) -> None:
        """Motion context alone should not trigger an alert (avoids Ring-style spam)."""
        ts = datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc)
        motion_ts = ts - timedelta(seconds=30)
        recent = [_motion_event(motion_ts)]
        tier, imp = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            recent,
            ts=ts,
        )
        assert tier == 1
        assert imp == pytest.approx(0.40)

    def test_stale_motion_does_not_promote(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc)
        motion_ts = ts - timedelta(seconds=120)  # 120 s ago, older than 90 s window
        recent = [_motion_event(motion_ts)]
        tier, _ = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            recent,
            ts=ts,
        )
        assert tier == 1

    def test_repeat_person_sightings_is_tier2(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc)
        # Two prior person events within the 5-minute window
        recent = [
            _person_event(ts - timedelta(minutes=1)),
            _person_event(ts - timedelta(minutes=3)),
        ]
        tier, imp = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            recent,
            ts=ts,
        )
        assert tier == 2
        assert imp == pytest.approx(0.80)

    def test_old_repeat_persons_do_not_promote(self, reasoner: Reasoner) -> None:
        ts = datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc)
        # Two person events but both outside the 5-minute window
        recent = [
            _person_event(ts - timedelta(minutes=6)),
            _person_event(ts - timedelta(minutes=8)),
        ]
        tier, _ = reasoner.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            recent,
            ts=ts,
        )
        assert tier == 1

    def test_non_person_object_is_tier1(self, reasoner: Reasoner) -> None:
        tier, imp = reasoner.classify(
            "object",
            {"detections": [{"label": "car", "confidence": 0.9}]},
            [],
        )
        assert tier == 1
        assert imp == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# Face event tests
# ---------------------------------------------------------------------------

class TestFaceClassification:
    def test_unknown_face_is_tier2(self, reasoner: Reasoner) -> None:
        entities = {"face": {"known": False, "name": "unknown", "distance": 0.7}}
        tier, imp = reasoner.classify("face", entities, [])
        assert tier == 2
        assert imp == pytest.approx(0.75)

    def test_known_face_is_tier1(self, reasoner: Reasoner) -> None:
        entities = {"face": {"known": True, "name": "Alice", "distance": 0.2}}
        tier, imp = reasoner.classify("face", entities, [])
        assert tier == 1
        assert imp == pytest.approx(0.50)

    def test_thief_face_is_tier2_high(self, reasoner: Reasoner) -> None:
        entities = {"face": {"known": True, "name": "Thief #1", "distance": 0.1}}
        tier, imp = reasoner.classify("face", entities, [])
        assert tier == 2
        assert imp == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# Disabled reasoner tests
# ---------------------------------------------------------------------------

class TestDisabledReasoner:
    def test_disabled_uses_defaults(self) -> None:
        config = _make_config({"enabled": False})
        r = Reasoner(config)
        tier, imp = r.classify(
            "object",
            {"detections": [{"label": "person", "confidence": 0.9}]},
            [],
        )
        assert tier == 1
        assert imp == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# score_record tests
# ---------------------------------------------------------------------------

class TestScoreRecord:
    def test_scores_face_record(self, reasoner: Reasoner) -> None:
        rec = _face_event(known=False)
        tier, imp = reasoner.score_record(rec)
        assert tier == 2
