"""Continuous DVR recording and metadata index."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

from sentinel.utils import now_iso


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

    def save_analysis(self, segment_id: int, summary: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE dvr_segments
                    SET summary = ?, analyzed = 1
                    WHERE id = ?
                    """,
                    (str(summary).strip(), int(segment_id)),
                )
                conn.commit()
                return cursor.rowcount > 0

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
        self._storage_root = os.path.abspath(str(self._cfg.get("storage_root", "/media/sentinel-dvr")))
        self._segments_root = os.path.join(self._storage_root, "segments")
        self._retention_hours = max(1.0, float(self._cfg.get("retention_hours", 48)))
        self._segment_seconds = max(10.0, float(self._cfg.get("segment_seconds", 60)))
        self._record_fps = max(1.0, float(self._cfg.get("record_fps", 10)))
        self._record_width = max(64, int(self._cfg.get("record_width", 640)))
        self._record_height = max(64, int(self._cfg.get("record_height", 360)))
        self._prefer_h264 = bool(self._cfg.get("prefer_h264", True))
        self._max_query_segments = max(1, int(self._cfg.get("max_query_segments", 12)))
        self._max_analysis_segments = max(
            1, int(self._cfg.get("max_analysis_segments_per_query", 2))
        )
        self._frame_getter = frame_getter
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._segment_seq = 0
        self._message = "DVR disabled."
        self._active = False
        self._last_error: str = ""
        self.index: Optional[DvrIndex] = None
        self._available = self._check_storage_available()
        if self._available:
            try:
                self.index = DvrIndex(os.path.join(self._storage_root, "dvr_index.db"))
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

    def _check_storage_available(self) -> bool:
        if not self._enabled:
            self._message = "DVR disabled in config."
            return False
        if cv2 is None:
            self._message = "OpenCV unavailable; DVR cannot encode segments."
            return False
        if not os.path.isdir(self._storage_root):
            self._message = (
                f"DVR storage root missing: {self._storage_root}. "
                "Attach/mount USB storage at this path."
            )
            return False
        if not os.access(self._storage_root, os.W_OK):
            self._message = f"DVR storage root not writable: {self._storage_root}"
            return False
        os.makedirs(self._segments_root, exist_ok=True)
        self._message = "DVR ready."
        return True

    def start(self) -> None:
        if not self._available:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="continuous-dvr", daemon=True)
        self._thread.start()
        self._active = True
        self._message = "DVR recording active."

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._active = False

    def status(self) -> Dict[str, Any]:
        with self._lock:
            segments = self.index.list_recent(limit=1) if self.index is not None else []
            newest = segments[0].start_ts if segments else None
            oldest_list = self.index.list_recent(limit=500) if self.index is not None else []
            oldest = oldest_list[-1].start_ts if oldest_list else None
        disk = shutil.disk_usage(self._storage_root) if os.path.isdir(self._storage_root) else None
        return {
            "enabled": self._enabled,
            "available": self._available,
            "active": bool(self._thread and self._thread.is_alive()),
            "message": self._message,
            "storage_root": self._storage_root,
            "retention_hours": self._retention_hours,
            "segment_seconds": self._segment_seconds,
            "record_fps": self._record_fps,
            "max_query_segments": self._max_query_segments,
            "max_analysis_segments_per_query": self._max_analysis_segments,
            "newest_segment_start": newest,
            "oldest_segment_start": oldest,
            "disk_total_gb": round(disk.total / (1024**3), 2) if disk else None,
            "disk_free_gb": round(disk.free / (1024**3), 2) if disk else None,
            "last_error": self._last_error or None,
        }

    def list_range(self, start_ts: str, end_ts: str, limit: int = 500) -> List[Dict[str, Any]]:
        if self.index is None:
            return []
        return [s.to_dict() for s in self.index.list_range(start_ts, end_ts, limit=limit)]

    def get_segment(self, segment_id: int) -> Optional[DvrSegment]:
        if self.index is None:
            return None
        return self.index.get(segment_id)

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

    def _run(self) -> None:
        frames: List[Any] = []
        segment_start = datetime.utcnow()
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
                continue
            resized = self._resize_frame(frame)
            if resized is None:
                continue
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

            if (datetime.utcnow() - segment_start).total_seconds() < self._segment_seconds:
                continue
            start_ts = segment_start
            end_ts = datetime.utcnow()
            segment_start = datetime.utcnow()
            payload = frames
            frames = [last_frame] if last_frame is not None else []
            motion_score = (motion_acc / max(1, motion_samples)) / 255.0
            motion_acc = 0.0
            motion_samples = 0
            try:
                path = self._write_segment(payload, start_ts)
                if path and self.index is not None:
                    size_bytes = os.path.getsize(path) if os.path.exists(path) else 0
                    self.index.add_segment(
                        start_ts=start_ts.isoformat(),
                        end_ts=end_ts.isoformat(),
                        path=path,
                        size_bytes=size_bytes,
                        motion_score=motion_score,
                    )
                    self._prune_old_segments()
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
        # mp4v is broadly available on Pi OpenCV builds.
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(path, fourcc, self._record_fps, (self._record_width, self._record_height))
        if not writer.isOpened():
            return None
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            return None
        return path

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
