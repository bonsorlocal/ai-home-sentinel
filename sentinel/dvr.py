"""Continuous DVR recording and metadata index."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

from sentinel.utils import now_iso

ALLOWED_SEGMENT_MINUTES = (15, 30, 45, 60)


def _iso_to_dt(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def _infer_question_window(question: str, now: Optional[datetime] = None) -> Dict[str, Optional[str]]:
    """Infer a DVR evidence window from natural-language timing hints."""
    text = str(question or "").strip().lower()
    if not text:
        return {"start_ts": None, "end_ts": None, "source": None}
    ref = now or datetime.utcnow()
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    source: Optional[str] = None

    between = re.search(
        r"\bbetween\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+and\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        text,
    )
    if between:
        h1 = int(between.group(1))
        m1 = int(between.group(2) or 0)
        p1 = (between.group(3) or "").lower()
        h2 = int(between.group(4))
        m2 = int(between.group(5) or 0)
        p2 = (between.group(6) or p1).lower()
        if p1 == "pm" and h1 < 12:
            h1 += 12
        elif p1 == "am" and h1 == 12:
            h1 = 0
        if p2 == "pm" and h2 < 12:
            h2 += 12
        elif p2 == "am" and h2 == 12:
            h2 = 0
        base = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = base + timedelta(hours=h1, minutes=m1)
        window_end = base + timedelta(hours=h2, minutes=m2)
        if window_end <= window_start:
            window_end += timedelta(days=1)
        source = "between_times"

    rel = re.search(r"\b(last|past)\s+(\d{1,3})\s*(minute|minutes|hour|hours)\b", text)
    if rel and window_start is None:
        amount = int(rel.group(2))
        unit = rel.group(3)
        delta = timedelta(minutes=amount) if unit.startswith("minute") else timedelta(hours=amount)
        window_start = ref - delta
        window_end = ref
        source = "relative_recent"

    if window_start is None and re.search(r"\b(?:a\s+)?few\s+minutes\s+(?:ago|earlier|back)\b", text):
        window_start = ref - timedelta(minutes=10)
        window_end = ref
        source = "relative_recent"

    ago = re.search(r"\b(\d{1,3})\s*(minute|minutes|hour|hours)\s+ago\b", text)
    if ago and window_start is None:
        amount = int(ago.group(1))
        unit = ago.group(2)
        delta = timedelta(minutes=amount) if unit.startswith("minute") else timedelta(hours=amount)
        anchor = ref - delta
        window_start = anchor - timedelta(minutes=20)
        window_end = anchor + timedelta(minutes=20)
        source = "relative_ago"

    if "yesterday" in text and window_start is None:
        y = (ref - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = y
        window_end = y + timedelta(days=1)
        source = "yesterday"
    elif "today" in text and window_start is None:
        t = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = t
        window_end = ref
        source = "today"
    elif any(k in text for k in ("tonight", "last night")) and window_start is None:
        day = ref - timedelta(days=1) if "last night" in text else ref
        night = day.replace(hour=18, minute=0, second=0, microsecond=0)
        window_start = night
        window_end = night + timedelta(hours=12)
        source = "night"
    elif "this morning" in text and window_start is None:
        day = ref.replace(hour=6, minute=0, second=0, microsecond=0)
        window_start = day
        window_end = day + timedelta(hours=6)
        source = "morning"
    elif "this afternoon" in text and window_start is None:
        day = ref.replace(hour=12, minute=0, second=0, microsecond=0)
        window_start = day
        window_end = day + timedelta(hours=6)
        source = "afternoon"
    elif "this evening" in text and window_start is None:
        day = ref.replace(hour=18, minute=0, second=0, microsecond=0)
        window_start = day
        window_end = day + timedelta(hours=6)
        source = "evening"

    if window_start is None or window_end is None:
        return {"start_ts": None, "end_ts": None, "source": None}
    return {
        "start_ts": window_start.isoformat(),
        "end_ts": window_end.isoformat(),
        "source": source,
    }


def build_local_segment_summary(
    *,
    motion_score: float,
    person_count: int,
    object_labels: List[str],
    start_ts: str,
    end_ts: str,
) -> str:
    """Compose a short browse-friendly summary from segment metadata."""
    start_dt = _iso_to_dt(start_ts)
    end_dt = _iso_to_dt(end_ts)
    if start_dt and end_dt and end_dt >= start_dt:
        window = (
            f"{start_dt.strftime('%I:%M %p').lstrip('0')}–"
            f"{end_dt.strftime('%I:%M %p').lstrip('0')}"
        )
    else:
        window = "this hour"

    labels = [str(x).strip().lower() for x in object_labels if str(x).strip()]
    unique_labels = sorted(set(labels))
    motion = float(motion_score or 0.0)
    if motion >= 0.08:
        motion_text = "high motion"
    elif motion >= 0.03:
        motion_text = "moderate motion"
    elif motion >= 0.01:
        motion_text = "light motion"
    else:
        motion_text = "very quiet"

    people = int(person_count or 0)
    if people > 0:
        people_text = f"{people} person{'s' if people != 1 else ''} seen"
    else:
        people_text = "no people detected"

    label_text = ", ".join(unique_labels[:5]) if unique_labels else "no notable objects"
    return f"{window}: {people_text}; {motion_text}; objects: {label_text}."


@dataclass
class DvrSegment:
    id: int
    start_ts: str
    end_ts: str
    path: str
    size_bytes: int
    motion_score: float
    person_count: int
    object_labels: List[str]
    summary: str
    analyzed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "motion_score": round(float(self.motion_score), 4),
            "person_count": int(self.person_count),
            "object_labels": list(self.object_labels),
            "summary": self.summary,
            "analyzed": bool(self.analyzed),
        }


class DvrIndex:
    """SQLite metadata index for DVR segments."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS dvr_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts TEXT NOT NULL,
        end_ts TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        motion_score REAL NOT NULL DEFAULT 0.0,
        person_count INTEGER NOT NULL DEFAULT 0,
        object_labels TEXT NOT NULL DEFAULT '[]',
        summary TEXT NOT NULL DEFAULT '',
        analyzed INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_dvr_segments_start ON dvr_segments(start_ts);
    CREATE INDEX IF NOT EXISTS idx_dvr_segments_end ON dvr_segments(end_ts);
    """

    def __init__(self, db_path: str):
        self._path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(self._SCHEMA)
                conn.commit()

    def add_segment(
        self,
        *,
        start_ts: str,
        end_ts: str,
        path: str,
        size_bytes: int,
        motion_score: float,
    ) -> int:
        payload = (
            str(start_ts),
            str(end_ts),
            os.path.abspath(path),
            int(size_bytes),
            float(motion_score),
        )
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR REPLACE INTO dvr_segments (
                        start_ts, end_ts, path, size_bytes, motion_score
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    payload,
                )
                conn.commit()
                row_id = int(cursor.lastrowid)
                if row_id <= 0:
                    row = conn.execute(
                        "SELECT id FROM dvr_segments WHERE path = ?", (os.path.abspath(path),)
                    ).fetchone()
                    row_id = int(row["id"]) if row else 0
        return row_id

    def get(self, segment_id: int) -> Optional[DvrSegment]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM dvr_segments WHERE id = ?",
                    (int(segment_id),),
                ).fetchone()
        return self._row_to_segment(row) if row else None

    def list_recent(self, limit: int = 50) -> List[DvrSegment]:
        limit = max(1, min(500, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM dvr_segments ORDER BY start_ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_segment(r) for r in rows]

    def list_range(self, start_ts: str, end_ts: str, limit: int = 500) -> List[DvrSegment]:
        limit = max(1, min(1000, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM dvr_segments
                    WHERE end_ts >= ? AND start_ts <= ?
                    ORDER BY start_ts ASC
                    LIMIT ?
                    """,
                    (str(start_ts), str(end_ts), limit),
                ).fetchall()
        return [self._row_to_segment(r) for r in rows]

    def list_all(self) -> List[DvrSegment]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM dvr_segments ORDER BY start_ts ASC"
                ).fetchall()
        return [self._row_to_segment(r) for r in rows]

    def delete_segment(self, segment_id: int) -> Optional[str]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT path FROM dvr_segments WHERE id = ?",
                    (int(segment_id),),
                ).fetchone()
                if row is None:
                    return None
                path = str(row["path"])
                conn.execute(
                    "DELETE FROM dvr_segments WHERE id = ?",
                    (int(segment_id),),
                )
                conn.commit()
        return path

    def prune_older_than(self, cutoff_ts: str) -> List[str]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT path FROM dvr_segments WHERE end_ts < ?",
                    (str(cutoff_ts),),
                ).fetchall()
                paths = [str(r["path"]) for r in rows]
                conn.execute(
                    "DELETE FROM dvr_segments WHERE end_ts < ?",
                    (str(cutoff_ts),),
                )
                conn.commit()
        return paths

    def annotate_overlap(
        self,
        *,
        start_ts: str,
        end_ts: str,
        person_count: int = 0,
        object_labels: Optional[List[str]] = None,
        summary: str = "",
    ) -> int:
        object_labels = object_labels or []
        updated = 0
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, person_count, object_labels, summary FROM dvr_segments
                    WHERE end_ts >= ? AND start_ts <= ?
                    """,
                    (str(start_ts), str(end_ts)),
                ).fetchall()
                for row in rows:
                    labels = set(self._decode_labels(row["object_labels"]))
                    labels.update(str(x).lower() for x in object_labels if str(x).strip())
                    prior_summary = str(row["summary"] or "").strip()
                    merged_summary = summary.strip() or prior_summary
                    conn.execute(
                        """
                        UPDATE dvr_segments
                        SET person_count = ?, object_labels = ?, summary = ?
                        WHERE id = ?
                        """,
                        (
                            max(int(row["person_count"]), int(person_count)),
                            json.dumps(sorted(labels)),
                            merged_summary,
                            int(row["id"]),
                        ),
                    )
                    updated += 1
                conn.commit()
        return updated

    def update_summary(self, segment_id: int, summary: str, *, analyzed: Optional[bool] = None) -> bool:
        summary = str(summary or "").strip()
        if not summary:
            return False
        with self._lock:
            with self._connect() as conn:
                if analyzed is None:
                    cursor = conn.execute(
                        "UPDATE dvr_segments SET summary = ? WHERE id = ?",
                        (summary, int(segment_id)),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE dvr_segments SET summary = ?, analyzed = ? WHERE id = ?",
                        (summary, 1 if analyzed else 0, int(segment_id)),
                    )
                conn.commit()
                return cursor.rowcount > 0

    def save_analysis(self, segment_id: int, summary: str) -> bool:
        return self.update_summary(segment_id, summary, analyzed=True)

    def search(self, query: str, limit: int = 12) -> List[DvrSegment]:
        limit = max(1, min(100, int(limit)))
        tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", str(query or "")) if len(t) >= 3]
        # No useful query terms: return latest segments.
        if not tokens:
            return self.list_recent(limit=limit)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM dvr_segments ORDER BY start_ts DESC LIMIT 500"
                ).fetchall()
        ranked: List[tuple[int, DvrSegment]] = []
        for row in rows:
            seg = self._row_to_segment(row)
            hay = " ".join(
                [
                    seg.summary.lower(),
                    " ".join(seg.object_labels).lower(),
                    "person" if seg.person_count > 0 else "",
                ]
            )
            score = 0
            for token in tokens:
                if token in hay:
                    score += 3
                elif token in os.path.basename(seg.path).lower():
                    score += 1
            if score > 0:
                ranked.append((score, seg))
        ranked.sort(key=lambda item: (item[0], item[1].start_ts), reverse=True)
        if ranked:
            return [item[1] for item in ranked[:limit]]
        return self.list_recent(limit=limit)

    @staticmethod
    def _decode_labels(raw: Any) -> List[str]:
        if isinstance(raw, list):
            return [str(v) for v in raw if str(v).strip()]
        try:
            parsed = json.loads(str(raw or "[]"))
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return []

    def _row_to_segment(self, row: sqlite3.Row) -> DvrSegment:
        return DvrSegment(
            id=int(row["id"]),
            start_ts=str(row["start_ts"]),
            end_ts=str(row["end_ts"]),
            path=str(row["path"]),
            size_bytes=int(row["size_bytes"] or 0),
            motion_score=float(row["motion_score"] or 0.0),
            person_count=int(row["person_count"] or 0),
            object_labels=self._decode_labels(row["object_labels"]),
            summary=str(row["summary"] or ""),
            analyzed=bool(row["analyzed"]),
        )


class ContinuousRecorder:
    """Always-on DVR segment recorder backed by a USB path."""

    def __init__(self, config: Any, frame_getter: Callable[[], Any]):
        self._cfg = config.get("dvr") or {}
        self._enabled = bool(self._cfg.get("enabled", False))
        self._primary_storage_root = os.path.abspath(
            str(self._cfg.get("storage_root", "/media/sentinel-dvr"))
        )
        self._fallback_storage_root = str(self._cfg.get("fallback_storage_root", "")).strip()
        self._storage_root = self._primary_storage_root
        self._segments_root = os.path.join(self._storage_root, "segments")
        self._using_fallback = False
        self._retention_hours = max(1.0, float(self._cfg.get("retention_hours", 48)))
        self._segment_seconds = max(10.0, float(self._cfg.get("segment_seconds", 3600)))
        self._record_fps = max(1.0, float(self._cfg.get("record_fps", 10)))
        self._record_width = max(64, int(self._cfg.get("record_width", 640)))
        self._record_height = max(64, int(self._cfg.get("record_height", 360)))
        self._prefer_h264 = bool(self._cfg.get("prefer_h264", True))
        self._max_query_segments = max(1, int(self._cfg.get("max_query_segments", 12)))
        self._max_analysis_segments = max(
            1, int(self._cfg.get("max_analysis_segments_per_query", 2))
        )
        self._auto_summarize = bool(self._cfg.get("auto_summarize", True))
        self._auto_summarize_cloud = bool(self._cfg.get("auto_summarize_cloud", True))
        self._segment_summary_daily_cap = max(
            1, int(self._cfg.get("segment_summary_daily_cap", 24))
        )
        self._align_to_clock_hours = bool(self._cfg.get("align_to_clock_hours", True))
        self._segment_analyzer: Optional[Callable[[str], Dict[str, Any]]] = None
        self._summary_queue: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=200)
        self._summary_thread: Optional[threading.Thread] = None
        self._summary_calls_today = 0
        self._summary_call_date = date.today()
        self._frame_getter = frame_getter
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._segment_seq = 0
        self._message = "DVR disabled."
        self._active = False
        self._last_error: str = ""
        self._active_segment_start: Optional[datetime] = None
        self._active_segment_deadline: Optional[datetime] = None
        self._force_finalize_active = False
        self._frames_received = 0
        self._last_frame_at: Optional[datetime] = None
        self._migration_status = "idle"
        self._migration_message = ""
        self._migration_progress = 0
        self._migration_total = 0
        self._migration_thread: Optional[threading.Thread] = None
        self.index: Optional[DvrIndex] = None
        self._available = self._check_storage_available()
        if self._available:
            try:
                self.index = DvrIndex(os.path.join(self._storage_root, "dvr_index.db"))
                self._load_persisted_settings()
            except Exception as error:  # noqa: BLE001
                self._available = False
                self._message = f"DVR index init failed: {error}"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def available(self) -> bool:
        return self._available

    @property
    def storage_root(self) -> str:
        return self._storage_root

    def _is_storage_ready(self, path: str, *, create: bool) -> bool:
        if not os.path.isdir(path):
            if not create:
                return False
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                return False
        return os.access(path, os.W_OK)

    def _resolve_storage_root(self) -> tuple[str, bool]:
        if self._is_storage_ready(self._primary_storage_root, create=False):
            return self._primary_storage_root, False
        if self._fallback_storage_root:
            fallback = os.path.abspath(self._fallback_storage_root)
            if self._is_storage_ready(fallback, create=True):
                return fallback, True
        return self._primary_storage_root, False

    def _check_storage_available(self) -> bool:
        if not self._enabled:
            self._message = "DVR disabled in config."
            return False
        if cv2 is None:
            self._message = "OpenCV unavailable; DVR cannot encode segments."
            return False
        resolved, using_fallback = self._resolve_storage_root()
        self._storage_root = resolved
        self._using_fallback = using_fallback
        self._segments_root = os.path.join(self._storage_root, "segments")
        if not self._is_storage_ready(self._storage_root, create=using_fallback):
            if self._fallback_storage_root:
                self._message = (
                    f"DVR storage unavailable at {self._primary_storage_root} "
                    f"and fallback {os.path.abspath(self._fallback_storage_root)}."
                )
            else:
                self._message = (
                    f"DVR storage root missing: {self._primary_storage_root}. "
                    "Attach/mount USB storage at this path."
                )
            return False
        os.makedirs(self._segments_root, exist_ok=True)
        if using_fallback:
            self._message = f"DVR ready on fallback storage: {self._storage_root}"
        else:
            self._message = "DVR ready."
        return True

    def configure_auto_summaries(
        self,
        *,
        analyzer_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        """Wire optional cloud analyzer for post-segment summaries."""
        self._segment_analyzer = analyzer_fn
        if self._auto_summarize_cloud and analyzer_fn is not None and self._available:
            if self._summary_thread is None or not self._summary_thread.is_alive():
                self._summary_thread = threading.Thread(
                    target=self._run_summary_worker,
                    name="dvr-summary-worker",
                    daemon=True,
                )
                self._summary_thread.start()

    def _settings_path(self) -> str:
        return os.path.join(self._storage_root, "dvr_settings.json")

    def _segment_minutes(self) -> int:
        minutes = int(round(self._segment_seconds / 60.0))
        if minutes in ALLOWED_SEGMENT_MINUTES:
            return minutes
        return 60

    def _load_persisted_settings(self) -> None:
        path = self._settings_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        minutes = int(data.get("segment_minutes", 0) or 0)
        if minutes in ALLOWED_SEGMENT_MINUTES:
            self._segment_seconds = float(minutes) * 60.0
            self._align_to_clock_hours = False

    def _persist_settings(self, minutes: int) -> None:
        payload = {
            "segment_minutes": int(minutes),
            "updated_at": now_iso(),
        }
        path = self._settings_path()
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "segment_minutes": self._segment_minutes(),
                "allowed_segment_minutes": list(ALLOWED_SEGMENT_MINUTES),
                "align_to_clock_hours": self._align_to_clock_hours,
                "segment_seconds": self._segment_seconds,
                "migration": {
                    "status": self._migration_status,
                    "message": self._migration_message,
                    "progress": self._migration_progress,
                    "total": self._migration_total,
                },
            }

    def set_segment_minutes(self, minutes: int) -> Dict[str, Any]:
        if minutes not in ALLOWED_SEGMENT_MINUTES:
            return {
                "ok": False,
                "error": f"segment_minutes must be one of {list(ALLOWED_SEGMENT_MINUTES)}",
            }
        unchanged = (
            self._segment_minutes() == minutes
            and self._migration_status in ("idle", "done")
        )
        if unchanged:
            return {
                "ok": True,
                "message": "Segment length unchanged.",
                **self.get_settings(),
            }

        with self._lock:
            self._segment_seconds = float(minutes) * 60.0
            self._align_to_clock_hours = False
            self._force_finalize_active = True
            if self._active_segment_start is not None:
                self._active_segment_deadline = self._segment_end_deadline(
                    self._active_segment_start
                )
            self._migration_status = "applying"
            self._migration_message = "Applying new segment length..."
            self._migration_progress = 0
            self._migration_total = 0

        self._persist_settings(minutes)
        self._start_migration_worker()
        return {
            "ok": True,
            "message": (
                f"Segment length set to {minutes} minutes. "
                "Existing footage will be migrated in the background."
            ),
            **self.get_settings(),
        }

    def _start_migration_worker(self) -> None:
        if self._migration_thread and self._migration_thread.is_alive():
            return
        self._migration_thread = threading.Thread(
            target=self._run_migration,
            name="dvr-migration",
            daemon=True,
        )
        self._migration_thread.start()

    def _segment_duration_seconds(self, seg: DvrSegment) -> float:
        start = _iso_to_dt(seg.start_ts)
        end = _iso_to_dt(seg.end_ts)
        if start is None or end is None or end <= start:
            return 0.0
        return (end - start).total_seconds()

    def _run_migration(self) -> None:
        if self.index is None:
            with self._lock:
                self._migration_status = "done"
                self._migration_message = "Migration complete."
            return

        target_seconds = self._segment_seconds
        todo = [
            seg
            for seg in self.index.list_all()
            if self._segment_duration_seconds(seg) > target_seconds * 1.02
        ]
        with self._lock:
            self._migration_status = "migrating"
            self._migration_total = len(todo)
            self._migration_progress = 0
            if todo:
                self._migration_message = f"Migrating {len(todo)} older segment(s)..."
            else:
                self._migration_message = "No older segments need migration."

        for seg in todo:
            try:
                self._migrate_segment(seg, target_seconds)
            except Exception as error:  # noqa: BLE001
                with self._lock:
                    self._migration_status = "error"
                    self._migration_message = f"Migration failed: {str(error)[:120]}"
                return
            with self._lock:
                self._migration_progress += 1

        with self._lock:
            self._migration_status = "done"
            self._migration_message = "Migration complete."

    def _migrate_segment(self, seg: DvrSegment, chunk_seconds: float) -> None:
        if self.index is None or cv2 is None:
            return
        path = str(seg.path)
        if not os.path.exists(path):
            self.index.delete_segment(seg.id)
            return

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            fps = 0.0
        if fps <= 0:
            fps = self._record_fps
        frames_per_chunk = max(1, int(round(fps * chunk_seconds)))

        start_dt = _iso_to_dt(seg.start_ts) or datetime.utcnow()
        end_dt = _iso_to_dt(seg.end_ts) or start_dt + timedelta(seconds=chunk_seconds)
        total_duration = max(1.0, (end_dt - start_dt).total_seconds())

        new_rows: List[Dict[str, Any]] = []
        frames_buffer: List[Any] = []
        chunk_index = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            resized = self._resize_frame(frame)
            if resized is None:
                continue
            frames_buffer.append(resized)
            if len(frames_buffer) < frames_per_chunk:
                continue
            chunk_start = start_dt + timedelta(seconds=chunk_index * chunk_seconds)
            chunk_end = chunk_start + timedelta(seconds=len(frames_buffer) / fps)
            out_path = self._write_frames_to_path(frames_buffer, chunk_start, fps)
            if out_path:
                new_rows.append(
                    self._chunk_metadata(seg, chunk_start, chunk_end, out_path, total_duration)
                )
            frames_buffer = []
            chunk_index += 1

        if frames_buffer:
            chunk_start = start_dt + timedelta(seconds=chunk_index * chunk_seconds)
            chunk_end = chunk_start + timedelta(seconds=len(frames_buffer) / fps)
            out_path = self._write_frames_to_path(frames_buffer, chunk_start, fps)
            if out_path:
                new_rows.append(
                    self._chunk_metadata(seg, chunk_start, chunk_end, out_path, total_duration)
                )

        cap.release()

        if len(new_rows) <= 1:
            return

        new_ids: List[int] = []
        for row in new_rows:
            segment_id = self.index.add_segment(
                start_ts=row["start_ts"],
                end_ts=row["end_ts"],
                path=row["path"],
                size_bytes=row["size_bytes"],
                motion_score=row["motion_score"],
                person_count=row["person_count"],
                object_labels=row["object_labels"],
                summary=row["summary"],
                analyzed=row["analyzed"],
            )
            if segment_id:
                new_ids.append(segment_id)
                if not str(row["summary"] or "").strip():
                    self._finalize_segment_summary(segment_id)

        if not new_ids:
            return

        old_path = self.index.delete_segment(seg.id)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    def _chunk_metadata(
        self,
        source: DvrSegment,
        chunk_start: datetime,
        chunk_end: datetime,
        path: str,
        total_duration: float,
    ) -> Dict[str, Any]:
        chunk_duration = max(1.0, (chunk_end - chunk_start).total_seconds())
        share = min(1.0, chunk_duration / max(1.0, total_duration))
        person_count = int(round(source.person_count * share)) if source.person_count else 0
        return {
            "start_ts": chunk_start.isoformat(),
            "end_ts": chunk_end.isoformat(),
            "path": path,
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "motion_score": float(source.motion_score),
            "person_count": person_count,
            "object_labels": list(source.object_labels),
            "summary": "",
            "analyzed": False,
        }

    def _write_frames_to_path(
        self,
        frames: List[Any],
        start_ts: datetime,
        fps: float,
    ) -> Optional[str]:
        if not frames or cv2 is None:
            return None
        path = self._segment_path(start_ts)
        return self._encode_segment_frames(frames, path, fps=max(1.0, float(fps)))

    def _segment_end_deadline(self, segment_start: datetime) -> datetime:
        if self._align_to_clock_hours:
            hour_floor = segment_start.replace(minute=0, second=0, microsecond=0)
            return hour_floor + timedelta(hours=1)
        return segment_start + timedelta(seconds=self._segment_seconds)

    def _initial_segment_start(self) -> datetime:
        now = datetime.utcnow()
        if not self._align_to_clock_hours:
            return now
        return now.replace(minute=0, second=0, microsecond=0)

    def start(self) -> None:
        if not self._available:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="continuous-dvr", daemon=True)
        self._thread.start()
        self._active = True
        self._message = "DVR started; waiting for camera frames."

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._summary_thread and self._summary_thread.is_alive():
            self._summary_thread.join(timeout=2.0)
        self._active = False

    def status(self) -> Dict[str, Any]:
        with self._lock:
            segments = self.index.list_recent(limit=1) if self.index is not None else []
            newest = segments[0].start_ts if segments else None
            oldest_list = self.index.list_recent(limit=500) if self.index is not None else []
            oldest = oldest_list[-1].start_ts if oldest_list else None
            segment_count = len(oldest_list)
            active_segment = self._active_segment_snapshot()
        oldest_dt = _iso_to_dt(oldest or "")
        newest_dt = _iso_to_dt(newest or "")
        effective_retention_hours = None
        if oldest_dt and newest_dt and newest_dt >= oldest_dt:
            effective_retention_hours = round(
                (newest_dt - oldest_dt).total_seconds() / 3600.0,
                2,
            )
        disk = shutil.disk_usage(self._storage_root) if os.path.isdir(self._storage_root) else None
        return {
            "enabled": self._enabled,
            "available": self._available,
            "active": bool(self._thread and self._thread.is_alive()),
            "message": self._message,
            "storage_root": self._storage_root,
            "primary_storage_root": self._primary_storage_root,
            "using_fallback": self._using_fallback,
            "retention_hours": self._retention_hours,
            "segment_seconds": self._segment_seconds,
            "segment_minutes": self._segment_minutes(),
            "allowed_segment_minutes": list(ALLOWED_SEGMENT_MINUTES),
            "align_to_clock_hours": self._align_to_clock_hours,
            "migration": {
                "status": self._migration_status,
                "message": self._migration_message,
                "progress": self._migration_progress,
                "total": self._migration_total,
            },
            "auto_summarize": self._auto_summarize,
            "auto_summarize_cloud": self._auto_summarize_cloud,
            "record_fps": self._record_fps,
            "max_query_segments": self._max_query_segments,
            "max_analysis_segments_per_query": self._max_analysis_segments,
            "segment_count": segment_count,
            "newest_segment_start": newest,
            "oldest_segment_start": oldest,
            "active_segment": active_segment,
            "effective_retention_hours": effective_retention_hours,
            "disk_total_gb": round(disk.total / (1024**3), 2) if disk else None,
            "disk_free_gb": round(disk.free / (1024**3), 2) if disk else None,
            "last_error": self._last_error or None,
        }

    def list_range(self, start_ts: str, end_ts: str, limit: int = 500) -> List[Dict[str, Any]]:
        if self.index is None:
            return []
        segments = self.index.list_range(start_ts, end_ts, limit=limit)
        if self._auto_summarize:
            for seg in segments:
                if str(seg.summary or "").strip():
                    continue
                summary = build_local_segment_summary(
                    motion_score=seg.motion_score,
                    person_count=seg.person_count,
                    object_labels=seg.object_labels,
                    start_ts=seg.start_ts,
                    end_ts=seg.end_ts,
                )
                self.index.update_summary(seg.id, summary, analyzed=False)
                seg.summary = summary
        data = [s.to_dict() for s in segments]
        active = self._active_segment_snapshot()
        # Include the in-progress hour even when waiting for camera frames so
        # the timeline never has a silent gap at "now".
        if active and self._segment_overlaps_range(active, start_ts, end_ts):
            data.append(active)
        return data[: max(1, min(1000, int(limit)))]

    def _active_segment_snapshot(self) -> Optional[Dict[str, Any]]:
        start = self._active_segment_start
        deadline = self._active_segment_deadline
        if start is None:
            return None
        now = datetime.utcnow()
        end = min(deadline or now, now)
        receiving = self._frames_received > 0 and self._last_frame_at is not None
        if receiving and self._last_frame_at is not None:
            age = (now - self._last_frame_at).total_seconds()
            receiving = age <= 5.0
        if receiving:
            summary = (
                "Recording now. Playback and final summary will be available "
                "after this segment closes."
            )
        else:
            summary = (
                "Waiting for camera frames. No video is being written until "
                "the Pi camera is online."
            )
        return {
            "id": None,
            "start_ts": start.isoformat(),
            "end_ts": end.isoformat(),
            "expected_end_ts": deadline.isoformat() if deadline else None,
            "path": "",
            "size_bytes": 0,
            "motion_score": 0.0,
            "person_count": 0,
            "object_labels": [],
            "summary": summary,
            "analyzed": False,
            "active": receiving,
            "playable": False,
            "waiting_for_camera": not receiving,
        }

    def _segment_overlaps_range(
        self,
        segment: Dict[str, Any],
        start_ts: str,
        end_ts: str,
    ) -> bool:
        start_dt = _iso_to_dt(start_ts)
        end_dt = _iso_to_dt(end_ts)
        seg_start = _iso_to_dt(str(segment.get("start_ts") or ""))
        seg_end = _iso_to_dt(
            str(segment.get("expected_end_ts") or segment.get("end_ts") or "")
        )
        if start_dt is None or end_dt is None or seg_start is None or seg_end is None:
            return False
        return seg_end >= start_dt and seg_start <= end_dt

    def _question_targets_active_segment(self, question: str) -> bool:
        text = str(question or "").strip().lower()
        if not text:
            return False
        active_hints = (
            "current segment",
            "current hour",
            "right now",
            "currently",
            "just now",
            "a few minutes",
            "few minutes",
            "last few minutes",
            "past few minutes",
        )
        if any(hint in text for hint in active_hints):
            return True
        inferred = _infer_question_window(text)
        start = _iso_to_dt(str(inferred.get("start_ts") or ""))
        end = _iso_to_dt(str(inferred.get("end_ts") or ""))
        active = self._active_segment_snapshot()
        if not active or start is None or end is None:
            return False
        return self._segment_overlaps_range(active, start.isoformat(), end.isoformat())

    def get_segment(self, segment_id: int) -> Optional[DvrSegment]:
        if self.index is None:
            return None
        return self.index.get(segment_id)

    def get_playback_path(self, segment_id: int) -> Optional[str]:
        seg = self.get_segment(segment_id)
        if seg is None:
            return None
        path = os.path.abspath(seg.path)
        if not os.path.exists(path):
            return None
        if self._segment_is_browser_playable(path):
            return path
        return self._transcode_for_browser(path, seg.id)

    @property
    def pinned_dir(self) -> str:
        path = os.path.join(self._storage_root, "pinned")
        os.makedirs(path, exist_ok=True)
        return path

    def pin_segment(self, segment_id: int) -> Dict[str, Any]:
        """Copy a segment into the pinned folder (never auto-pruned)."""
        seg = self.get_segment(segment_id)
        if seg is None:
            return {"ok": False, "message": "Segment not found."}
        src = os.path.abspath(seg.path)
        if not os.path.exists(src):
            return {"ok": False, "message": "Segment file missing."}
        dest_name = f"pinned_{segment_id}_{os.path.basename(src)}"
        dest = os.path.join(self.pinned_dir, dest_name)
        try:
            if not os.path.exists(dest):
                shutil.copy2(src, dest)
            return {
                "ok": True,
                "segment_id": segment_id,
                "path": dest,
                "message": "Segment pinned.",
            }
        except OSError as error:
            return {"ok": False, "message": f"Pin failed: {error}"}

    def export_range(
        self,
        start_ts: str,
        end_ts: str,
        *,
        pin: bool = True,
        max_segments: int = 12,
    ) -> Dict[str, Any]:
        """Export overlapping segments for a time window; optionally pin copies."""
        if self.index is None:
            return {"ok": False, "message": "DVR index unavailable.", "segments": []}
        segments = self.index.list_range(start_ts, end_ts, limit=max(1, int(max_segments)))
        if not segments:
            return {
                "ok": False,
                "message": "No DVR segments in that range.",
                "segments": [],
                "start": start_ts,
                "end": end_ts,
            }
        exported: List[Dict[str, Any]] = []
        for seg in segments:
            item: Dict[str, Any] = {
                "id": seg.id,
                "start_ts": seg.start_ts,
                "end_ts": seg.end_ts,
                "path": seg.path,
                "summary": seg.summary,
            }
            if pin:
                pinned = self.pin_segment(seg.id)
                item["pinned"] = pinned
                if pinned.get("ok"):
                    item["pinned_path"] = pinned.get("path")
            exported.append(item)
        return {
            "ok": True,
            "message": f"Exported {len(exported)} segment(s).",
            "segments": exported,
            "start": start_ts,
            "end": end_ts,
            "count": len(exported),
        }

    def search(self, question: str, limit: Optional[int] = None) -> List[DvrSegment]:
        if self.index is None:
            return []
        n = self._max_query_segments if limit is None else max(1, int(limit))
        return self.index.search(question, limit=n)

    def annotate_event(
        self,
        *,
        start_ts: str,
        end_ts: str,
        detections: Optional[List[Dict[str, Any]]] = None,
        summary: str = "",
    ) -> int:
        if self.index is None:
            return 0
        labels: List[str] = []
        person_count = 0
        for item in detections or []:
            label = str(item.get("label", "")).lower().strip()
            if not label:
                continue
            labels.append(label)
            if label == "person":
                person_count += 1
        return self.index.annotate_overlap(
            start_ts=start_ts,
            end_ts=end_ts,
            person_count=person_count,
            object_labels=labels,
            summary=summary,
        )

    def build_query_context(
        self,
        question: str,
        *,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
        include_recent_fallback: bool = True,
    ) -> Dict[str, Any]:
        if self.index is None:
            return {
                "segments": [],
                "context": "(DVR index unavailable)",
                "window_start": None,
                "window_end": None,
                "window_source": None,
            }
        start_dt = _iso_to_dt(start_ts or "")
        end_dt = _iso_to_dt(end_ts or "")
        inferred = _infer_question_window(question)
        inferred_start = _iso_to_dt(str(inferred.get("start_ts") or ""))
        inferred_end = _iso_to_dt(str(inferred.get("end_ts") or ""))
        window_source = str(inferred.get("source") or "")
        if start_dt is None and end_dt is None and inferred_start and inferred_end:
            start_dt = inferred_start
            end_dt = inferred_end
        used_window = bool(start_dt and end_dt and end_dt >= start_dt)
        if start_dt and end_dt and end_dt >= start_dt:
            candidates = self.index.list_range(start_dt.isoformat(), end_dt.isoformat(), limit=300)
            if not candidates and include_recent_fallback and not window_source:
                candidates = self.search(question)
        else:
            candidates = self.search(question)
        lines: List[str] = []
        if used_window:
            lines.append(
                f"Evidence window: {start_dt.isoformat()} -> {end_dt.isoformat()} "
                f"(source={window_source or 'explicit'})"
            )
        for seg in candidates[: self._max_query_segments]:
            labels = ", ".join(seg.object_labels[:6]) if seg.object_labels else "none"
            lines.append(
                f"- segment#{seg.id} [{seg.start_ts} -> {seg.end_ts}] "
                f"person_count={seg.person_count} labels={labels} "
                f"motion_score={seg.motion_score:.3f} "
                f"summary={seg.summary or 'n/a'}"
            )
        active = self._active_segment_snapshot()
        include_active = bool(active) and (
            self._question_targets_active_segment(question)
            or (
                used_window
                and self._segment_overlaps_range(
                    active,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                )
            )
        )
        if include_active and active:
            lines.append(
                "- active segment (recording now) "
                f"[{active.get('start_ts')} -> {active.get('expected_end_ts') or active.get('end_ts')}] "
                "status=in_progress playable=false "
                "summary=Current DVR hour is still being recorded; finalized video analysis "
                "is not available until this segment closes. Use recent event notes and "
                "live-frame context for activity inside this current hour."
            )
        return {
            "segments": candidates[: self._max_query_segments],
            "context": "\n".join(lines) if lines else "(no DVR segments matched query)",
            "window_start": start_dt.isoformat() if used_window else None,
            "window_end": end_dt.isoformat() if used_window else None,
            "window_source": window_source or (("explicit") if used_window else None),
        }

    def analyze_segments(
        self,
        segments: List[DvrSegment],
        analyzer_fn: Callable[[str], Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        analyzed: List[Dict[str, Any]] = []
        for seg in segments[: self._max_analysis_segments]:
            if not os.path.exists(seg.path):
                continue
            try:
                result = analyzer_fn(seg.path) or {}
            except Exception as error:  # noqa: BLE001
                result = {"error": str(error)[:180]}
            scene = str(result.get("scene_summary", "")).strip()
            if scene and self.index is not None:
                self.index.save_analysis(seg.id, scene)
            analyzed.append({"segment_id": seg.id, "start_ts": seg.start_ts, "end_ts": seg.end_ts, "analysis": result})
        return analyzed

    def _finalize_segment_summary(self, segment_id: int) -> None:
        if self.index is None or not self._auto_summarize:
            return
        seg = self.index.get(segment_id)
        if seg is None:
            return
        if str(seg.summary or "").strip():
            return
        summary = build_local_segment_summary(
            motion_score=seg.motion_score,
            person_count=seg.person_count,
            object_labels=seg.object_labels,
            start_ts=seg.start_ts,
            end_ts=seg.end_ts,
        )
        self.index.update_summary(segment_id, summary, analyzed=False)

    def _enqueue_cloud_summary(self, segment_id: int, path: str) -> None:
        if not self._auto_summarize_cloud or self._segment_analyzer is None:
            return
        try:
            self._summary_queue.put_nowait((int(segment_id), str(path)))
        except queue.Full:
            pass

    def _run_summary_worker(self) -> None:
        while not self._stop.is_set():
            try:
                segment_id, path = self._summary_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._segment_analyzer is None or self.index is None:
                self._summary_queue.task_done()
                continue
            with self._lock:
                if date.today() != self._summary_call_date:
                    self._summary_call_date = date.today()
                    self._summary_calls_today = 0
                if self._summary_calls_today >= self._segment_summary_daily_cap:
                    self._summary_queue.task_done()
                    continue
                self._summary_calls_today += 1
            try:
                if not os.path.exists(path):
                    continue
                result = self._segment_analyzer(path) or {}
                scene = str(result.get("scene_summary", "")).strip()
                if scene:
                    self.index.update_summary(segment_id, scene, analyzed=True)
            except Exception as error:  # noqa: BLE001
                self._last_error = f"summary: {str(error)[:120]}"
            finally:
                self._summary_queue.task_done()

    def _write_and_index_segment(
        self,
        frames: List[Any],
        start_ts: datetime,
        end_ts: datetime,
        motion_score: float,
    ) -> None:
        if not frames or self.index is None:
            return
        path = self._write_segment(frames, start_ts)
        if not path:
            return
        size_bytes = os.path.getsize(path) if os.path.exists(path) else 0
        segment_id = self.index.add_segment(
            start_ts=start_ts.isoformat(),
            end_ts=end_ts.isoformat(),
            path=path,
            size_bytes=size_bytes,
            motion_score=motion_score,
        )
        if segment_id:
            self._finalize_segment_summary(segment_id)
            self._enqueue_cloud_summary(segment_id, path)
        self._prune_old_segments()

    def _maybe_finalize_for_reconfigure(
        self,
        frames: List[Any],
        segment_start: datetime,
        motion_acc: float,
        motion_samples: int,
    ) -> tuple[List[Any], datetime, float, int]:
        with self._lock:
            if not self._force_finalize_active:
                return frames, segment_start, motion_acc, motion_samples
            self._force_finalize_active = False
            chunk_seconds = self._segment_seconds

        if not frames:
            new_start = datetime.utcnow().replace(microsecond=0)
            with self._lock:
                self._active_segment_start = new_start
                self._active_segment_deadline = self._segment_end_deadline(new_start)
            return [], new_start, 0.0, 0

        chunk_frames = max(1, int(self._record_fps * chunk_seconds))
        remaining = list(frames)
        cursor_start = segment_start
        last_frame = remaining[-1]

        while len(remaining) >= chunk_frames:
            chunk = remaining[:chunk_frames]
            remaining = remaining[chunk_frames:]
            elapsed = timedelta(seconds=len(chunk) / self._record_fps)
            end_ts = cursor_start + elapsed
            motion_score = (motion_acc / max(1, motion_samples)) / 255.0
            self._write_and_index_segment(chunk, cursor_start, end_ts, motion_score)
            cursor_start = end_ts
            motion_acc = 0.0
            motion_samples = 0

        with self._lock:
            self._active_segment_start = cursor_start
            self._active_segment_deadline = self._segment_end_deadline(cursor_start)

        if remaining:
            if remaining[-1] is not last_frame:
                remaining.append(last_frame)
        else:
            remaining = [last_frame]

        return remaining, cursor_start, motion_acc, motion_samples

    def _run(self) -> None:
        frames: List[Any] = []
        segment_start = self._initial_segment_start()
        with self._lock:
            self._active_segment_start = segment_start
            self._active_segment_deadline = self._segment_end_deadline(segment_start)
        last_frame = None
        last_luma: Optional[float] = None
        motion_acc = 0.0
        motion_samples = 0
        frame_interval = 1.0 / self._record_fps
        last_sample_at = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if (now - last_sample_at) < frame_interval:
                time.sleep(0.01)
                continue
            last_sample_at = now
            frame = self._frame_getter()
            if frame is None:
                if self._frames_received == 0 or (
                    self._last_frame_at is not None
                    and (datetime.utcnow() - self._last_frame_at).total_seconds() > 5.0
                ):
                    self._message = "DVR waiting for camera frames."
                continue
            resized = self._resize_frame(frame)
            if resized is None:
                continue
            self._frames_received += 1
            self._last_frame_at = datetime.utcnow()
            self._message = "DVR recording active."
            frames.append(resized)
            if len(frames) > int(self._record_fps * (self._segment_seconds + 3)):
                frames = frames[-int(self._record_fps * (self._segment_seconds + 3)) :]
            # Motion score proxy from luma deltas.
            try:
                gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)  # type: ignore[arg-type]
                luma = float(gray.mean())
            except Exception:
                luma = None
            if luma is not None and last_luma is not None:
                motion_acc += abs(luma - last_luma)
                motion_samples += 1
            if luma is not None:
                last_luma = luma
            last_frame = resized

            frames, segment_start, motion_acc, motion_samples = self._maybe_finalize_for_reconfigure(
                frames,
                segment_start,
                motion_acc,
                motion_samples,
            )


            with self._lock:
                deadline = self._active_segment_deadline or self._segment_end_deadline(segment_start)
            if datetime.utcnow() < deadline:
                continue
            start_ts = segment_start
            end_ts = datetime.utcnow()
            segment_start = end_ts.replace(microsecond=0) if self._align_to_clock_hours else end_ts
            if self._align_to_clock_hours:
                segment_start = segment_start.replace(minute=0, second=0)
            with self._lock:
                self._active_segment_start = segment_start
                self._active_segment_deadline = self._segment_end_deadline(segment_start)
            payload = frames
            frames = [last_frame] if last_frame is not None else []
            motion_score = (motion_acc / max(1, motion_samples)) / 255.0
            motion_acc = 0.0
            motion_samples = 0
            try:
                self._write_and_index_segment(payload, start_ts, end_ts, motion_score)
            except Exception as error:  # noqa: BLE001
                self._last_error = str(error)[:180]
                self._message = f"DVR write error: {self._last_error}"

    def _resize_frame(self, frame):
        try:
            return cv2.resize(frame, (self._record_width, self._record_height))
        except Exception:
            return None

    def _segment_path(self, start_ts: datetime) -> str:
        self._segment_seq += 1
        day = start_ts.strftime("%Y-%m-%d")
        hour = start_ts.strftime("%H")
        folder = os.path.join(self._segments_root, day, hour)
        os.makedirs(folder, exist_ok=True)
        name = f"seg_{start_ts.strftime('%Y%m%d_%H%M%S')}_{self._segment_seq}.mp4"
        return os.path.join(folder, name)

    def _write_segment(self, frames: List[Any], start_ts: datetime) -> Optional[str]:
        if not frames:
            return None
        path = self._segment_path(start_ts)
        return self._encode_segment_frames(frames, path, fps=self._record_fps)

    def _encode_segment_frames(self, frames: List[Any], path: str, *, fps: float) -> Optional[str]:
        if not frames or cv2 is None:
            return None
        if self._prefer_h264 and self._save_via_ffmpeg(frames, path, fps=fps):
            return path
        for fourcc in ("avc1", "H264", "mp4v"):
            if self._save_via_opencv(frames, path, fps=fps, fourcc=fourcc):
                return path
        return None

    def _save_via_ffmpeg(self, frames: List[Any], path: str, *, fps: float) -> bool:
        if cv2 is None or shutil.which("ffmpeg") is None:
            return False
        tmp = tempfile.mkdtemp(prefix="sentinel_dvr_")
        try:
            for index, frame in enumerate(frames):
                cv2.imwrite(os.path.join(tmp, f"f{index:05d}.jpg"), frame)
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(max(1.0, float(fps))),
                "-i",
                os.path.join(tmp, "f%05d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                path,
            ]
            subprocess.run(cmd, check=True, timeout=180)
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _save_via_opencv(self, frames: List[Any], path: str, *, fps: float, fourcc: str) -> bool:
        if cv2 is None:
            return False
        writer = None
        try:
            codec = cv2.VideoWriter_fourcc(*fourcc)
            writer = cv2.VideoWriter(
                path,
                codec,
                max(1.0, float(fps)),
                (self._record_width, self._record_height),
            )
            if not writer.isOpened():
                return False
            for frame in frames:
                writer.write(frame)
            writer.release()
            writer = None
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            return False
        finally:
            if writer is not None:
                writer.release()

    def _segment_is_browser_playable(self, path: str) -> bool:
        if shutil.which("ffprobe") is None:
            return path.lower().endswith(".mp4")
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,codec_tag_string",
                "-of",
                "json",
                path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams") or []
            if not streams:
                return False
            stream = streams[0] or {}
            codec_name = str(stream.get("codec_name") or "").lower()
            codec_tag = str(stream.get("codec_tag_string") or "").lower()
            return codec_name in {"h264", "hevc", "vp8", "vp9", "av1"} or codec_tag in {"avc1", "hvc1"}
        except Exception:
            return path.lower().endswith(".mp4")

    def _transcode_for_browser(self, source_path: str, segment_id: int) -> Optional[str]:
        if shutil.which("ffmpeg") is None:
            return source_path
        cached_path = os.path.splitext(source_path)[0] + f".web-{int(segment_id)}.mp4"
        if os.path.exists(cached_path) and os.path.getmtime(cached_path) >= os.path.getmtime(source_path):
            return cached_path
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            source_path,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            cached_path,
        ]
        try:
            subprocess.run(cmd, check=True, timeout=180)
            if os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                return cached_path
        except Exception as error:  # noqa: BLE001
            self._last_error = f"playback transcode: {str(error)[:120]}"
        try:
            if os.path.exists(cached_path):
                os.remove(cached_path)
        except OSError:
            pass
        return source_path

    def _prune_old_segments(self) -> None:
        if self.index is None:
            return
        cutoff = datetime.utcnow() - timedelta(hours=self._retention_hours)
        paths = self.index.prune_older_than(cutoff.isoformat())
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
