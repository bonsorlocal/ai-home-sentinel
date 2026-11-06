"""Session-end AI importance gate (notable_only ledger policy).

Runs after a motion session closes. Decides whether the moment is routine
(no ledger row / no Ring-style clip) or notable/urgent (ledger + clip).
Routine moments still get a short DVR context note for later Q&A.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class GateDecision:
    importance: str = "routine"  # routine | notable | urgent
    reason: str = ""
    subjects: List[str] = field(default_factory=list)
    activities: List[str] = field(default_factory=list)
    appearance_notes: List[str] = field(default_factory=list)
    save_clip: bool = False
    ledger: bool = False
    dvr_context: str = ""
    source: str = "offline"  # ai | hard_tier | heuristics | offline
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def keep_event(self) -> bool:
        return bool(self.ledger) or self.importance in ("notable", "urgent")


class SessionGate:
    """Cloud judge for whether a closed motion session should become an event."""

    def __init__(self, config: Any):
        cfg = config.get("session_gate") or {}
        self._enabled = bool(cfg.get("enabled", True))
        self._provider = str(cfg.get("provider", "auto")).strip().lower() or "auto"
        self._base_url = str(cfg.get("base_url", "https://api.x.ai/v1")).rstrip("/")
        self._model = str(cfg.get("model", "grok-4.3"))
        self._google_base_url = str(
            cfg.get("google_base_url", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")
        self._google_model = str(cfg.get("google_model", "gemini-3.8-flash"))
        self._max_keyframes = max(1, int(cfg.get("max_keyframes", 3)))
        self._daily_cap = max(1, int(cfg.get("daily_call_cap", 80)))
        self._timeout = float(cfg.get("timeout_seconds", cfg.get("request_timeout_seconds", 20)))
        self._ledger_policy = str(cfg.get("ledger_policy", "notable_only")).strip().lower()
        self._fallback = str(cfg.get("fallback_when_offline", "heuristics")).strip().lower()
        self._known_resident_bias = bool(cfg.get("known_resident_routine_bias", True))
        self._secrets_file = str(cfg.get("secrets_file", "secrets.yaml"))
        self._api_key = self._load_key("grok_api_key", "GROK_API_KEY")
        self._google_api_key = self._load_key("google_api_key", "GOOGLE_API_KEY")
        self._calls_today = 0
        self._call_date = date.today()
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def notable_only(self) -> bool:
        return self._ledger_policy in ("notable_only", "notable", "b")

    def is_available(self) -> bool:
        if not self._enabled:
            return False
        provider = self._effective_provider()
        if provider == "google":
            return bool(self._google_api_key)
        return bool(self._api_key)

    def status(self) -> Dict[str, Any]:
        self._roll_day_if_needed()
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "provider": self._effective_provider(),
            "model": self._google_model if self._effective_provider() == "google" else self._model,
            "ledger_policy": self._ledger_policy,
            "fallback_when_offline": self._fallback,
            "calls_used_today": self._calls_today,
            "daily_call_cap": self._daily_cap,
            "calls_remaining": max(0, self._daily_cap - self._calls_today),
            "last_error": self._last_error,
        }

    def decide(
        self,
        *,
        entities: Dict[str, Any],
        tier: int,
        source: str,
        keyframes: Optional[List[bytes]] = None,
        heuristic_save_clip: bool = False,
    ) -> GateDecision:
        """Return keep/skip decision for a closed session."""
        if int(tier) >= 2:
            return GateDecision(
                importance="urgent",
                reason="Hard tier-2 safety rule (unknown/night/repeat/door).",
                subjects=self._subjects_from_entities(entities),
                activities=[],
                save_clip=True,
                ledger=True,
                dvr_context=str(entities.get("summary") or entities.get("event_type") or "Alert event"),
                source="hard_tier",
            )

        if not self._enabled:
            # Legacy path: always create a ledger row; clip still follows heuristics.
            return GateDecision(
                importance="notable",
                reason="Session gate disabled; legacy always-ledger path.",
                subjects=self._subjects_from_entities(entities),
                save_clip=bool(heuristic_save_clip),
                ledger=True,
                dvr_context=self._default_dvr_context(entities, "Motion session"),
                source="disabled",
            )

        if not self.is_available():
            self._last_error = "Session gate offline (no API key or disabled provider)."
            return self._offline_decision(entities, heuristic_save_clip)

        frames = list(keyframes or [])[: self._max_keyframes]
        try:
            self._roll_day_if_needed()
            if self._calls_today >= self._daily_cap:
                self._last_error = "Daily session_gate cap reached."
                return self._offline_decision(entities, heuristic_save_clip)
            self._calls_today += 1
            raw = self._call_model(entities=entities, source=source, keyframes=frames)
            decision = self._normalize(raw, entities)
            decision.source = "ai"
            self._last_error = ""
            return decision
        except Exception as error:  # noqa: BLE001
            self._last_error = str(error)[:180]
            return self._offline_decision(entities, heuristic_save_clip)

    def _offline_decision(self, entities: Dict[str, Any], heuristic_save_clip: bool) -> GateDecision:
        if self._fallback == "heuristics":
            return self._heuristics_decision(entities, heuristic_save_clip, source="offline")
        # Under notable_only, offline without heuristics means skip ledger.
        note = self._default_dvr_context(entities, "Routine motion (gate offline).")
        return GateDecision(
            importance="routine",
            reason=self._last_error or "Gate offline; skipping ledger under notable_only.",
            subjects=self._subjects_from_entities(entities),
            save_clip=False,
            ledger=False,
            dvr_context=note,
            source="offline",
        )

    def _heuristics_decision(
        self, entities: Dict[str, Any], heuristic_save_clip: bool, *, source: str
    ) -> GateDecision:
        faces = entities.get("faces") if isinstance(entities.get("faces"), list) else []
        known = [
            str(f.get("name", "")).strip()
            for f in faces
            if isinstance(f, dict) and f.get("known") and str(f.get("name", "")).strip()
        ]
        unknown = sum(1 for f in faces if isinstance(f, dict) and not f.get("known", False))
        if unknown:
            return GateDecision(
                importance="urgent",
                reason="Unknown face (heuristics fallback).",
                subjects=known or ["unknown"],
                save_clip=True,
                ledger=True,
                dvr_context="Unknown face during motion session.",
                source=source,
            )
        if self.notable_only and known and self._known_resident_bias:
            names = ", ".join(known[:3])
            note = f"Known resident activity ({names}); treated as routine by heuristics."
            return GateDecision(
                importance="routine",
                reason="Known resident; notable_only heuristics skip ledger.",
                subjects=known,
                save_clip=False,
                ledger=False,
                dvr_context=note,
                source=source,
            )
        if self.notable_only:
            # No hard signal → do not create ledger noise when offline.
            return GateDecision(
                importance="routine",
                reason="Heuristics under notable_only: no hard alert signal.",
                subjects=self._subjects_from_entities(entities),
                save_clip=False,
                ledger=False,
                dvr_context=self._default_dvr_context(entities, "Routine motion (heuristics)."),
                source=source,
            )
        return GateDecision(
            importance="notable" if heuristic_save_clip else "routine",
            reason="Legacy heuristics fallback.",
            subjects=self._subjects_from_entities(entities),
            save_clip=bool(heuristic_save_clip),
            ledger=True,
            dvr_context=self._default_dvr_context(entities, "Motion session"),
            source=source,
        )

    def _call_model(
        self, *, entities: Dict[str, Any], source: str, keyframes: List[bytes]
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(entities=entities, source=source)
        provider = self._effective_provider()
        if provider == "google":
            return self._call_google(prompt, keyframes)
        return self._call_grok(prompt, keyframes)

    def _build_prompt(self, *, entities: Dict[str, Any], source: str) -> str:
        faces = entities.get("faces") if isinstance(entities.get("faces"), list) else []
        detections = entities.get("detections") if isinstance(entities.get("detections"), list) else []
        session = entities.get("session") if isinstance(entities.get("session"), dict) else {}
        known = [
            str(f.get("name", "")).strip()
            for f in faces
            if isinstance(f, dict) and f.get("known") and str(f.get("name", "")).strip()
        ]
        unknown = sum(1 for f in faces if isinstance(f, dict) and not f.get("known", False))
        labels = sorted(
            {
                str(d.get("label", "")).lower()
                for d in detections
                if isinstance(d, dict) and d.get("label")
            }
        )
        bias = (
            "If a known household resident is the only person and the activity looks "
            "ordinary (walking, sitting, drinking, leaving frame), prefer importance=routine."
            if self._known_resident_bias
            else ""
        )
        return (
            "You are the importance judge for AI Home Sentinel, a home camera. "
            "Decide whether this closed motion session should become a saved security "
            "event/clip or only a quiet DVR context note.\n"
            "Return STRICT JSON with keys: "
            "importance (routine|notable|urgent), reason (string), "
            "subjects (array of strings), activities (array of strings), "
            "appearance_notes (array of short strings, e.g. hat/clothing), "
            "save_clip (bool), ledger (bool), dvr_context (one sentence for later Q&A). "
            "No markdown.\n"
            f"Session source={source}. duration_s={session.get('duration_seconds')}. "
            f"known_faces={known}. unknown_face_count={unknown}. objects={labels}. "
            f"{bias}"
        )

    def _call_google(self, prompt: str, keyframes: List[bytes]) -> Dict[str, Any]:
        if not self._google_api_key:
            raise RuntimeError("Google key unavailable.")
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for jpeg in keyframes:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    }
                }
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
        }
        data = self._post_json(
            f"{self._google_base_url}/models/{self._google_model}:generateContent"
            f"?key={self._google_api_key}",
            headers={"Content-Type": "application/json"},
            payload=payload,
            timeout=self._timeout,
        )
        text = self._extract_google_answer(data)
        parsed = self._parse_json(text)
        if not parsed:
            raise RuntimeError("Could not parse session_gate Google JSON.")
        return parsed

    def _call_grok(self, prompt: str, keyframes: List[bytes]) -> Dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("Grok key unavailable.")
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for jpeg in keyframes:
            b64 = base64.b64encode(jpeg).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 400,
            "temperature": 0.2,
            "stream": False,
        }
        data = self._post_json(
            f"{self._base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            payload=payload,
            timeout=self._timeout,
        )
        text = self._extract_grok_answer(data)
        parsed = self._parse_json(text)
        if not parsed:
            raise RuntimeError("Could not parse session_gate Grok JSON.")
        return parsed

    def _normalize(self, raw: Dict[str, Any], entities: Dict[str, Any]) -> GateDecision:
        importance = str(raw.get("importance") or "routine").strip().lower()
        if importance not in ("routine", "notable", "urgent"):
            importance = "routine"
        ledger = bool(raw.get("ledger")) if "ledger" in raw else importance != "routine"
        save_clip = bool(raw.get("save_clip")) if "save_clip" in raw else ledger
        if importance == "urgent":
            ledger = True
            save_clip = True
        if importance == "routine":
            ledger = False
            save_clip = False
        subjects = raw.get("subjects") if isinstance(raw.get("subjects"), list) else []
        activities = raw.get("activities") if isinstance(raw.get("activities"), list) else []
        appearance = (
            raw.get("appearance_notes") if isinstance(raw.get("appearance_notes"), list) else []
        )
        dvr_context = str(raw.get("dvr_context") or "").strip()
        if not dvr_context:
            dvr_context = self._default_dvr_context(
                entities, str(raw.get("reason") or importance)
            )
        return GateDecision(
            importance=importance,
            reason=str(raw.get("reason") or "")[:240],
            subjects=[str(s) for s in subjects][:8],
            activities=[str(a) for a in activities][:8],
            appearance_notes=[str(a) for a in appearance][:8],
            save_clip=save_clip,
            ledger=ledger,
            dvr_context=dvr_context[:400],
            raw=dict(raw),
        )

    @staticmethod
    def _subjects_from_entities(entities: Dict[str, Any]) -> List[str]:
        faces = entities.get("faces") if isinstance(entities.get("faces"), list) else []
        known = [
            str(f.get("name", "")).strip()
            for f in faces
            if isinstance(f, dict) and f.get("known") and str(f.get("name", "")).strip()
        ]
        if known:
            return known[:5]
        identity = str(entities.get("known_identity") or "").strip()
        if identity:
            return [identity]
        subject = str(entities.get("primary_subject") or "").strip()
        return [subject] if subject else []

    @staticmethod
    def _default_dvr_context(entities: Dict[str, Any], fallback: str) -> str:
        session = entities.get("session") if isinstance(entities.get("session"), dict) else {}
        duration = session.get("duration_seconds")
        subject = entities.get("known_identity") or entities.get("primary_subject") or "activity"
        if duration is not None:
            return f"{subject} during a {duration}s motion session. {fallback}".strip()[:400]
        return str(fallback)[:400]

    def _load_key(self, *names: str) -> str:
        if yaml is None:
            return ""
        path = os.path.join(_project_root(), self._secrets_file)
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:  # noqa: BLE001
            return ""
        if not isinstance(data, dict):
            return ""
        for name in names:
            val = str(data.get(name) or "").strip()
            if val:
                return val
        return ""

    def _effective_provider(self) -> str:
        if self._provider in ("grok", "google"):
            return self._provider
        if self._google_api_key:
            return "google"
        return "grok"

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._call_date:
            self._call_date = today
            self._calls_today = 0

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_grok_answer(data: Dict[str, Any]) -> str:
        try:
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return str(message.get("content") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _extract_google_answer(data: Dict[str, Any]) -> str:
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return ""
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            chunks = [str(p.get("text", "")).strip() for p in parts if str(p.get("text", "")).strip()]
            return "\n".join(chunks).strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _post_json(
        url: str, *, headers: Dict[str, str], payload: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)
