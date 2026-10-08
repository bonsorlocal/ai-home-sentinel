"""Persistent conversational memory for brain feedback and preferences."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sentinel.utils import now_iso


MEMORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversational_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    response_path TEXT NOT NULL DEFAULT '',
    helpful INTEGER,
    correction TEXT NOT NULL DEFAULT '',
    preference_key TEXT NOT NULL DEFAULT '',
    preference_value TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memory_created_at ON conversational_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_preference_key ON conversational_memory(preference_key);
"""

OWNER_PROFILE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS owner_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}'
);
"""

RESIDENT_PROFILE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS resident_profiles (
    resident_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_resident_profiles_updated_at
ON resident_profiles(updated_at DESC);
"""

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{10,}\b"),
    re.compile(r"(?i)\bxai-[A-Za-z0-9]{10,}\b"),
    re.compile(r"(?i)\bAIza[0-9A-Za-z\-_]{20,}\b"),
)


class MemoryStore:
    """Bounded persistent memory for explicit and inferred chat feedback."""

    def __init__(
        self,
        database_path: str,
        *,
        enabled: bool,
        max_entries: int,
        retention_days: int = 90,
    ) -> None:
        self._enabled = bool(enabled)
        self._max_entries = max(10, int(max_entries or 200))
        self._retention_days = max(1, int(retention_days or 90))
        self._lock = threading.Lock()
        self._db_path = str(database_path or "").strip()
        if self._enabled and self._db_path:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            self._init_db()
        else:
            self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def capture_explicit_feedback(
        self,
        *,
        question: str,
        answer: str,
        mode: str,
        source: str,
        path: str,
        helpful: Optional[bool],
        correction: str,
        remember_preference: bool,
    ) -> None:
        if not self._enabled:
            return
        question = self._redact(question or "")
        answer = self._redact(answer or "")
        correction = self._redact(correction or "")
        pref_key = ""
        pref_value = ""
        if remember_preference and correction:
            pref_key, pref_value = self._extract_preference(correction)
        payload = {
            "created_at": now_iso(),
            "memory_type": "explicit_feedback",
            "question": question,
            "answer": answer,
            "mode": mode or "",
            "source": source or "",
            "response_path": path or "",
            "helpful": None if helpful is None else int(bool(helpful)),
            "correction": correction,
            "preference_key": pref_key,
            "preference_value": pref_value,
            "metadata": json.dumps({"remember_preference": bool(remember_preference)}),
        }
        self._insert(payload)
        if pref_key and pref_value:
            self._insert(
                {
                    "created_at": now_iso(),
                    "memory_type": "explicit_preference",
                    "question": question,
                    "answer": answer,
                    "mode": mode or "",
                    "source": source or "",
                    "response_path": path or "",
                    "helpful": None,
                    "correction": correction,
                    "preference_key": pref_key,
                    "preference_value": pref_value,
                    "metadata": "{}",
                }
            )
        self._prune()

    def capture_inferred_feedback(self, *, question: str, answer: str) -> None:
        if not self._enabled:
            return
        question = self._redact(question or "")
        answer = self._redact(answer or "")
        preferences = self._extract_preferences(question)
        if not preferences:
            return
        for pref_key, pref_value in preferences:
            self._insert(
                {
                    "created_at": now_iso(),
                    "memory_type": "inferred_preference",
                    "question": question,
                    "answer": answer,
                    "mode": "hybrid",
                    "source": "conversation",
                    "response_path": "cloud_primary",
                    "helpful": None,
                    "correction": "",
                    "preference_key": pref_key,
                    "preference_value": pref_value,
                    "metadata": "{}",
                }
            )
        self._prune()

    def build_prompt_context(self, *, limit: int) -> str:
        if not self._enabled:
            return ""
        rows = self._list_recent_preferences(limit=max(1, int(limit or 5)))
        if not rows:
            return ""
        lines = [f"- {row['preference_key']}: {row['preference_value']}" for row in rows]
        return "\n".join(lines)

    def build_profile_context(self, *, resident_limit: int = 5) -> str:
        """Owner/resident profile block for brain prompts (tone, routines, appearance)."""
        if not self._enabled:
            return ""
        lines: List[str] = []
        owner = self.get_owner_profile()
        if owner:
            name = str(owner.get("name", "")).strip() or "owner"
            role = str(owner.get("role", "owner_admin")).strip()
            lines.append(f"- owner: {name} ({role})")
            appearance = owner.get("appearance_signature") or {}
            if isinstance(appearance, dict) and appearance:
                parts = [f"{k}={v}" for k, v in sorted(appearance.items())]
                lines.append(f"- owner_appearance: {', '.join(parts)}")
            prefs = owner.get("preferences") if isinstance(owner.get("preferences"), dict) else {}
            if prefs:
                pref_parts = [f"{k}={v}" for k, v in sorted(prefs.items())]
                lines.append(f"- owner_preferences: {', '.join(pref_parts)}")
            tone = owner.get("personality_tone") if isinstance(owner.get("personality_tone"), dict) else {}
            if tone:
                tone_parts = [f"{k}={v}" for k, v in sorted(tone.items())]
                lines.append(f"- owner_tone: {', '.join(tone_parts)}")
        residents = self.list_resident_profiles(limit=max(1, int(resident_limit)))
        for resident in residents[: max(1, int(resident_limit))]:
            rid = str(resident.get("resident_id", "")).strip()
            rname = str(resident.get("name", rid)).strip() or rid
            lines.append(f"- resident: {rname} ({resident.get('role', 'resident')})")
            appearance = resident.get("appearance_signature") or {}
            if isinstance(appearance, dict) and appearance:
                parts = [f"{k}={v}" for k, v in sorted(appearance.items())]
                lines.append(f"  appearance: {', '.join(parts)}")
        return "\n".join(lines)

    def status(self, *, preference_limit: int = 5) -> Dict[str, Any]:
        if not self._enabled:
            return {
                "enabled": False,
                "entry_count": 0,
                "max_entries": self._max_entries,
                "retention_days": self._retention_days,
                "preferences": [],
                "owner_profile": None,
            }
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM conversational_memory").fetchone()
                count = int(row["n"]) if row else 0
        return {
            "enabled": True,
            "entry_count": count,
            "max_entries": self._max_entries,
            "retention_days": self._retention_days,
            "preferences": self._list_recent_preferences(limit=max(1, int(preference_limit))),
            "owner_profile": self.get_owner_profile(),
        }

    def clear_all(self) -> int:
        """Delete all conversational memory rows. Returns deleted count."""
        if not self._enabled:
            return 0
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM conversational_memory").fetchone()
                count = int(row["n"]) if row else 0
                conn.execute("DELETE FROM conversational_memory")
                conn.commit()
        return count

    def delete_preference(self, preference_key: str) -> int:
        """Delete rows for one preference key. Returns deleted count."""
        if not self._enabled:
            return 0
        key = str(preference_key or "").strip()
        if not key:
            return 0
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM conversational_memory WHERE preference_key = ?",
                    (key,),
                )
                conn.commit()
                return int(cur.rowcount or 0)

    def upsert_owner_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled:
            return {}
        cleaned = dict(profile or {})
        cleaned["updated_at"] = now_iso()
        payload = json.dumps(cleaned)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO owner_profile (id, updated_at, profile_json)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        profile_json = excluded.profile_json
                    """,
                    (cleaned["updated_at"], payload),
                )
                conn.commit()
        return cleaned

    def get_owner_profile(self) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT profile_json FROM owner_profile WHERE id = 1"
                ).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(str(row["profile_json"] or "{}"))
        except json.JSONDecodeError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def upsert_resident_profile(self, resident_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled:
            return {}
        rid = str(resident_id or "").strip().lower()
        if not rid:
            return {}
        cleaned = dict(profile or {})
        cleaned["resident_id"] = rid
        cleaned["updated_at"] = now_iso()
        payload = json.dumps(cleaned)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO resident_profiles (resident_id, updated_at, profile_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(resident_id) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        profile_json = excluded.profile_json
                    """,
                    (rid, cleaned["updated_at"], payload),
                )
                conn.commit()
        return cleaned

    def list_resident_profiles(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT profile_json FROM resident_profiles
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 500)),),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(str(row["profile_json"] or "{}"))
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    def set_owner_preference(self, key: str, value: Any) -> Optional[Dict[str, Any]]:
        current = self.get_owner_profile() or {}
        if not current:
            return None
        prefs = current.get("preferences") if isinstance(current.get("preferences"), dict) else {}
        prefs = dict(prefs)
        prefs[str(key)] = value
        current["preferences"] = prefs
        return self.upsert_owner_profile(current)

    def _extract_preference(self, text: str) -> Tuple[str, str]:
        prefs = self._extract_preferences(text)
        if not prefs:
            return "", ""
        return prefs[0]

    def _extract_preferences(self, text: str) -> List[Tuple[str, str]]:
        value = (text or "").strip()
        if not value:
            return []
        found: List[Tuple[str, str]] = []
        lower = value.lower()

        name_patterns = (
            r"\bmy name is ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
            r"\bi(?:'m| am) ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
            r"\bcall me ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
            r"\bremember me as ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
        )
        for pattern in name_patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match:
                name = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?:;")
                # Avoid treating role phrases as names.
                if name and name.lower() not in {
                    "the owner",
                    "owner",
                    "admin",
                    "an owner",
                }:
                    found.append(("preferred_name", name))
                    break

        if any(p in lower for p in ("be brief", "keep it short", "shorter answers", "be concise")):
            found.append(("response_length", "brief"))
        elif any(p in lower for p in ("more detail", "be detailed", "longer answers")):
            found.append(("response_length", "detailed"))

        if any(p in lower for p in ("be direct", "no fluff", "straight to the point")):
            found.append(("tone", "direct"))
        elif any(p in lower for p in ("be warmer", "friendly tone", "be friendly")):
            found.append(("tone", "warm"))
        elif any(p in lower for p in ("be formal", "formal tone")):
            found.append(("tone", "formal"))

        if any(
            p in lower
            for p in (
                "only if certain",
                "only answer if certain",
                "if certain",
                "high confidence",
                "don't speculate",
                "do not speculate",
                "be certain",
            )
        ):
            found.append(("certainty_level", "high"))
        elif any(p in lower for p in ("it's ok to guess", "best guess", "speculate if needed")):
            found.append(("certainty_level", "exploratory"))

        # Deduplicate by key, keep first match.
        seen = set()
        unique: List[Tuple[str, str]] = []
        for key, pref_value in found:
            if key in seen:
                continue
            seen.add(key)
            unique.append((key, pref_value))
        return unique

    def _redact(self, text: str) -> str:
        value = str(text or "")
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub("[redacted]", value)
        return value

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(MEMORY_TABLE_SQL)
                conn.executescript(OWNER_PROFILE_TABLE_SQL)
                conn.executescript(RESIDENT_PROFILE_TABLE_SQL)
                conn.commit()

    def _insert(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO conversational_memory (
                        created_at, memory_type, question, answer, mode, source,
                        response_path, helpful, correction, preference_key,
                        preference_value, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.get("created_at", now_iso()),
                        str(payload.get("memory_type", "")),
                        str(payload.get("question", "")),
                        str(payload.get("answer", "")),
                        str(payload.get("mode", "")),
                        str(payload.get("source", "")),
                        str(payload.get("response_path", "")),
                        payload.get("helpful"),
                        str(payload.get("correction", "")),
                        str(payload.get("preference_key", "")),
                        str(payload.get("preference_value", "")),
                        str(payload.get("metadata", "{}")),
                    ),
                )
                conn.commit()

    def _list_recent_preferences(self, *, limit: int) -> List[Dict[str, str]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT preference_key, preference_value, MAX(id) AS last_id
                    FROM conversational_memory
                    WHERE preference_key <> '' AND preference_value <> ''
                    GROUP BY preference_key
                    ORDER BY last_id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
        return [
            {
                "preference_key": str(row["preference_key"]),
                "preference_value": str(row["preference_value"]),
            }
            for row in rows
        ]

    def _prune(self) -> None:
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).isoformat(
            timespec="seconds"
        )
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM conversational_memory WHERE created_at < ?",
                    (cutoff,),
                )
                row = conn.execute("SELECT COUNT(*) AS n FROM conversational_memory").fetchone()
                count = int(row["n"]) if row else 0
                over = count - self._max_entries
                if over > 0:
                    conn.execute(
                        """
                        DELETE FROM conversational_memory
                        WHERE id IN (
                            SELECT id FROM conversational_memory
                            ORDER BY id ASC
                            LIMIT ?
                        )
                        """,
                        (int(over),),
                    )
                conn.commit()
