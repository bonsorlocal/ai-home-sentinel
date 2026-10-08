"""Language brain (B1) for AI Home Sentinel.

This adds a small "brain" that can answer plain-language questions about what
the system has seen, using Google Gemini first (when configured) with Grok fallback.
It does two things:

- ``ask(question)``     - answer a question using recent Event Ledger notes.
- ``summarize_day()``   - a short, friendly recap of today's events.

Design goals (kept deliberately defensive so the rest of the app is never put
at risk by the brain):

- **Secret stays local.** The Grok API key is read from ``secrets.yaml`` in the
  project root (which is git-ignored). The key is never logged or returned.
- **Strict daily cap.** A small per-day call limit so a bug or a busy night
  can't run up a surprise bill. Once the cap is hit the brain politely declines
  until the next day.
- **Graceful offline.** If the key is missing, the internet is down, or Grok
  returns an error, the brain returns a clear message instead of raising. The
  camera, motion, and detection keep working no matter what.

The only dependency is Python's standard library (``urllib``), so nothing extra
needs to be installed on the Pi.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML is already required
    raise ImportError(
        "PyYAML is not installed. Run 'pip install -r requirements.txt' first."
    ) from exc

from sentinel.events import EventLedger
from sentinel.memory import MemoryStore

FrameGetter = Callable[[], Any]
CameraActiveFn = Callable[[], bool]
DvrContextFn = Callable[[str, bool], Dict[str, Any]]
OwnerEnrollFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def _project_root() -> str:
    """Return the project root folder (the folder that contains config.yaml)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# The system prompt keeps Grok grounded: answer only from the notes we give it,
# stay short, and admit when it does not know.
_LEDGER_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "You answer the owner's questions using ONLY the recent context provided. "
    "Prefer footage-derived clip analysis when available. If a clip has no "
    "analysis, use the event note as fallback and say footage analysis is missing "
    "for that event. Be brief, friendly, and concrete. Refer to times in a natural "
    "way. When making factual security claims, cite the checked evidence window "
    "or event timestamps from context. If context does not contain the answer, "
    "say so plainly instead of guessing."
)

_LIVE_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "You are looking at a LIVE camera frame from right now. Answer using what you "
    "can see in the image. If the owner introduces themselves (for example "
    "'I'm Jordan, remember me'), acknowledge what you see and use their name in "
    "your reply. Be brief, friendly, and concrete. If the image is unclear or "
    "empty, say so plainly instead of guessing."
)

_COMBINED_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "You have BOTH a live camera frame (what is happening right now) AND recent "
    "event notes from the past (with timestamps). Use the live frame for "
    "present-tense or visual questions; use the event notes for times, history, "
    "and 'when did X happen' questions. Be brief, friendly, and concrete. "
    "When making factual security claims, cite checked windows or timestamps "
    "from the provided context instead of guessing."
)

_CASUAL_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "This is a general assistant request, not primarily a footage evidence query. "
    "Be friendly, concise, and useful for everyday asks like recipes, planning, "
    "explanations, and troubleshooting. Use the user's provided details and common "
    "knowledge. Do not claim specific sightings, times, or incidents unless explicit "
    "camera/event evidence context is attached."
)

_HYBRID_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "This question mixes security/footage and general assistant needs. "
    "Structure the reply in two short parts when both apply: "
    "(1) Evidence — only factual camera/event claims grounded in the provided "
    "ledger/DVR/live context, citing the checked time window when available; "
    "(2) General — everyday advice that does not invent camera events. "
    "If evidence is missing, say so plainly before offering general help."
)

# Backwards-compatible alias used in tests/docs.
_SYSTEM_PROMPT = _LEDGER_SYSTEM_PROMPT

# Simple phrase routing — no extra API call. Exported for tests.
_LIVE_HINTS = (
    "remember me",
    "remember my",
    "this is me",
    "i'm ",
    "im ",
    "call me ",
    "my name is",
    "what do you see",
    "what can you see",
    "tell me what",
    "describe the",
    "describe what",
    "what is the scene",
    "what's the scene",
    "whats the scene",
    "the scene",
    "what is happening",
    "what's happening",
    "whats happening",
    "going on",
    "look like",
    "look at me",
    "look at this",
    "right now",
    "see now",
    "i see now",
    "currently",
    "live feed",
    "in the frame",
    "in front of the camera",
    "in front of camera",
    "who am i",
    "describe me",
    "what am i wearing",
    "what am i holding",
    "what im holding",
    "what i'm holding",
    "do you see me",
    "can you see me",
)

_LEDGER_HINTS = (
    "what time",
    "when did",
    "when was",
    "when were",
    "what happened",
    "how many times",
    "last time",
    "earlier today",
    "yesterday",
    "last night",
    "this morning",
    "this afternoon",
    "this evening",
    "event log",
    "events today",
    "motion at",
    "detected at",
    "alert at",
    "at what time",
    "how long ago",
    "timeline",
    "history of",
    "footage",
    "clip",
    "video",
    "recording",
    "who was",
    "who did",
    "did you see",
    "did anyone",
    "come by",
    "outside",
    "in my yard",
    "at my door",
    "was someone",
    "steal",
    "stole",
    "took",
)

_RAW_DETAIL_HINTS = (
    "full timeline",
    "raw log",
    "raw events",
    "detailed log",
    "every event",
    "all events",
    "exact timestamp",
    "show details",
    "debug timeline",
)

_CASUAL_HINTS = (
    "hello",
    "hi",
    "hey",
    "how are you",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
    "tell me a joke",
    "joke",
    "what can you do",
    "who are you",
)

_GENERAL_HINTS = (
    "recipe",
    "cook",
    "cooking",
    "dinner",
    "lunch",
    "breakfast",
    "ingredients",
    "fridge",
    "meal",
    "shopping list",
    "grocery list",
    "what can i make",
    "help me make",
    "plan my",
    "advice",
    "explain",
    "summarize",
    "write",
    "draft",
    "calculate",
    "troubleshoot",
)

# Meta questions about the AI stack itself — never attach a live camera frame.
_META_HINTS = (
    "gemini",
    "google api",
    "google_api",
    "api key",
    "api keys",
    "which model",
    "what model",
    "are you using",
    "are you running",
    "running through",
    "powered by",
    "grok",
    "x.ai",
    "provider",
    "which api",
    "what api",
    "llm",
    "language model",
)


def is_meta_question(question: str) -> bool:
    """True when the user is asking about the brain/provider, not the camera scene."""
    q = (question or "").strip().lower()
    if not q:
        return False
    # Strong Gemini / Google API signals always win, even if "right now" appears.
    strong = (
        "gemini",
        "google api",
        "google_api",
        "api key",
        "api keys",
        "which model",
        "what model",
        "running through",
        "powered by",
        "which api",
        "what api",
        "language model",
    )
    if any(h in q for h in strong):
        return True
    asks_identity = any(
        h in q
        for h in (
            "are you using",
            "are you running",
            "which provider",
            "what provider",
            "your provider",
            "your model",
            "your api",
        )
    )
    mentions_stack = any(h in q for h in ("grok", "x.ai", "provider", "llm", "google"))
    return asks_identity and mentions_stack


def classify_query_mode(question: str) -> str:
    """Return ``casual``, ``footage``, or ``hybrid`` query mode."""
    q = (question or "").strip().lower()
    if not q:
        return "casual"
    if is_meta_question(question):
        return "casual"
    footage = any(h in q for h in _LEDGER_HINTS) or any(h in q for h in _LIVE_HINTS)
    casual = any(h in q for h in _CASUAL_HINTS) or any(h in q for h in _GENERAL_HINTS)
    if casual and footage:
        return "hybrid"
    if footage:
        return "footage"
    if casual:
        return "casual"
    # Unknown requests default to general assistant behavior.
    return "casual"


def classify_query_source(question: str) -> str:
    """Return ``live``, ``ledger``, or ``both`` for routing ask() context."""
    q = (question or "").strip().lower()
    if not q:
        return "ledger"
    if is_meta_question(question):
        return "ledger"
    immediate_recap = (
        "what just happened",
        "just happened",
        "just now",
        "what did i miss",
        "i stepped out",
        "i stepped away",
        "after i stepped out",
        "after i stepped away",
        "since i stepped out",
    )
    short_window_seconds = _parse_recent_window_seconds(question)
    historical_window = ("today", "yesterday", "last ", "ago", "between ")
    if short_window_seconds is not None and short_window_seconds <= 10 * 60:
        # "last 60 seconds/2 minutes" should use immediate recap behavior.
        return "both"
    if any(h in q for h in immediate_recap) and not any(h in q for h in historical_window):
        # Pull both context types for "I just stepped away" questions.
        return "both"
    live = any(h in q for h in _LIVE_HINTS) or _looks_like_live_question(q)
    ledger = any(h in q for h in _LEDGER_HINTS)
    if live and ledger:
        return "both"
    if live:
        return "live"
    if ledger:
        return "ledger"
    return "ledger"


def _looks_like_live_question(q: str) -> bool:
    """Catch present-tense / visual questions that miss exact phrase lists."""
    if " scene" in q or q.startswith("scene"):
        return True
    if q.endswith(" now") or q.endswith(" now?") or " now " in q:
        return True
    if q.startswith("what do you see") or q.startswith("what can you see"):
        return True
    return False


def _wants_raw_timeline(question: str) -> bool:
    q = (question or "").strip().lower()
    return any(h in q for h in _RAW_DETAIL_HINTS)


def _is_immediate_recap_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    short_window_seconds = _parse_recent_window_seconds(question)
    if short_window_seconds is not None:
        return short_window_seconds <= 10 * 60
    if any(h in q for h in ("today", "yesterday", "last ", "ago", "between ")):
        return False
    hints = (
        "what just happened",
        "just happened",
        "just now",
        "what did i miss",
        "i stepped out",
        "i stepped away",
        "after i stepped out",
        "after i stepped away",
        "since i stepped out",
    )
    return any(h in q for h in hints)


def _format_age_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s ago"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{minutes}m ago"
    hours = round(seconds / 3600.0, 1)
    return f"{hours}h ago"


def _parse_recent_window_seconds(question: str) -> Optional[int]:
    """Parse short 'last N seconds/minutes' windows for immediate recap queries."""
    q = (question or "").strip().lower()
    if not q:
        return None
    match = re.search(
        r"\b(?:last|past)\s+(\d{1,4})\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        q,
    )
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if value <= 0:
        return None
    if unit.startswith(("m", "min")):
        return value * 60
    return value


def _format_window_seconds(seconds: int) -> str:
    seconds = max(1, int(seconds))
    if seconds % 60 == 0:
        minutes = seconds // 60
        label = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {label}"
    label = "second" if seconds == 1 else "seconds"
    return f"{seconds} {label}"


def _parse_owner_enrollment(question: str) -> Optional[Dict[str, Any]]:
    """Infer owner enrollment intent from natural language text."""
    q = (question or "").strip()
    if not q:
        return None
    lower = q.lower()
    owner_hints = ("owner", "admin", "administrator", "household owner", "resident")
    enroll_hints = (
        "remember me",
        "this is me",
        "i am",
        "i'm",
        "im ",
        "my name is",
        "call me",
        "make me",
        "set me as",
        "enroll me",
        "register me",
    )
    if not any(h in lower for h in owner_hints) and not any(h in lower for h in enroll_hints):
        return None

    name = ""
    name_patterns = (
        r"\bmy name is ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
        r"\bi(?:'m| am) ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
        r"\bcall me ([A-Za-z][A-Za-z0-9_\- ]{1,39})\b",
    )
    for pattern in name_patterns:
        match = re.search(pattern, q, flags=re.IGNORECASE)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?:;")
            break
    if not name:
        return None

    role = "owner_admin" if any(h in lower for h in ("owner", "admin", "administrator")) else "resident"
    appearance = _extract_appearance_signature(q)
    if "gender" not in appearance:
        if "male" in lower or "man" in lower:
            appearance["gender"] = "male"
        elif "female" in lower or "woman" in lower:
            appearance["gender"] = "female"
        elif "nonbinary" in lower or "non-binary" in lower:
            appearance["gender"] = "nonbinary"
    return {
        "name": name,
        "role": role,
        "appearance_signature": appearance,
        "source_text": q,
    }


def _extract_appearance_signature(text: str) -> Dict[str, Any]:
    value = (text or "").strip().lower()
    if not value:
        return {}
    signature: Dict[str, Any] = {}
    tattoo = re.search(r"\b(?:tattoo|tattoos)\b", value)
    if tattoo:
        signature["tattoos"] = True
    height = re.search(r"\b(\d{1})\s*(?:ft|foot|feet)\s*(\d{1,2})?\b", value)
    if height:
        feet = int(height.group(1))
        inches = int(height.group(2) or 0)
        signature["height_estimate"] = f"{feet}ft {inches}in"
    elif "tall" in value:
        signature["height_estimate"] = "tall"
    elif "short" in value:
        signature["height_estimate"] = "short"
    size_keywords = ("slim", "athletic", "stocky", "large", "medium", "petite")
    for keyword in size_keywords:
        if keyword in value:
            signature["build"] = keyword
            break
    for token, key in (
        ("beard", "beard"),
        ("glasses", "glasses"),
        ("hat", "hat"),
        ("hoodie", "hoodie"),
        ("jacket", "jacket"),
    ):
        if token in value:
            signature[key] = True
    return signature


def _build_query_plan(question: str, mode: str, source: str) -> Dict[str, Any]:
    """Planner layer (Option B): map question to retrieval strategy."""
    q = (question or "").strip().lower()
    intent = "casual_chat" if mode == "casual" else "footage_investigation"
    if mode == "hybrid":
        intent = "hybrid_assistant"
    if _parse_owner_enrollment(question):
        intent = "owner_enrollment"
    needs_evidence = mode in ("footage", "hybrid")
    plan: Dict[str, Any] = {
        "intent": intent,
        "mode": mode,
        "source": source,
        "needs_live_frame": source in ("live", "both") and mode != "casual",
        "needs_ledger": needs_evidence,
        "needs_dvr": needs_evidence,
        "heavy_dvr": _should_use_heavy_dvr(question) if needs_evidence else False,
        "evidence_order": (
            ["dvr_analysis", "dvr_segments", "ledger", "live_frame"] if needs_evidence else []
        ),
        "response_style": "brief_concrete",
        "risk_flags": [],
    }
    if mode == "hybrid":
        plan["response_style"] = "separate_evidence_and_general"
    if any(k in q for k in ("why", "intention", "moved", "stole", "steal")):
        plan["risk_flags"].append("causal_inference")
    if any(k in q for k in ("between", "last", "ago", "yesterday", "tonight")):
        plan["risk_flags"].append("time_window")
    return plan


def _assess_evidence_quality(
    *,
    question: str,
    ledger_context: str,
    dvr_context: str,
    dvr_analysis_context: str,
    window_source: str,
) -> Dict[str, Any]:
    text_chunks = [ledger_context, dvr_context, dvr_analysis_context]
    non_empty = [chunk for chunk in text_chunks if str(chunk or "").strip()]
    evidence_count = sum(max(0, len([line for line in chunk.splitlines() if line.strip()])) for chunk in non_empty)
    q = (question or "").lower()
    wants_time = any(h in q for h in ("when", "what time", "last", "ago", "between", "yesterday", "tonight"))
    ambiguous_window = bool(wants_time and not (window_source or "").strip())
    merged = "\n".join(non_empty).lower()
    conflicting_identity = ("unknown" in merged) and ("known face" in merged or "known faces" in merged)
    flags: List[str] = []
    if ambiguous_window:
        flags.append("time_window_ambiguous")
    if conflicting_identity:
        flags.append("conflicting_identity_signals")
    if evidence_count <= 1:
        confidence = "low"
    elif flags:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "confidence": confidence,
        "evidence_count": evidence_count,
        "flags": flags,
    }

_DIGEST_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "Write a short, friendly recap of the day for the owner using ONLY the event "
    "notes provided. Group similar things together (for example, 'several motion "
    "alerts in the afternoon'), call out anything that looks important (like a "
    "person or an unknown face), and keep it to a few sentences. If there is "
    "almost nothing to report, say it was a quiet day."
)


# Type alias for the injectable HTTP function (makes testing easy / offline).
HttpPost = Callable[[str, Dict[str, str], Dict[str, Any], float], Dict[str, Any]]


class Brain:
    """A thin, safe wrapper around Grok for questions and daily digests."""

    def __init__(
        self,
        config: Any,
        ledger: EventLedger,
        *,
        secrets_path: Optional[str] = None,
        http_post: Optional[HttpPost] = None,
        frame_getter: Optional[FrameGetter] = None,
        camera_active_fn: Optional[CameraActiveFn] = None,
        dvr_context_fn: Optional[DvrContextFn] = None,
        owner_enroll_fn: Optional[OwnerEnrollFn] = None,
    ) -> None:
        self._ledger = ledger
        self._frame_getter = frame_getter
        self._camera_active_fn = camera_active_fn
        self._dvr_context_fn = dvr_context_fn
        self._owner_enroll_fn = owner_enroll_fn
        brain_cfg: Dict[str, Any] = {}
        try:
            brain_cfg = config.get("brain") or {}
        except Exception:  # noqa: BLE001 - config is best-effort
            brain_cfg = {}

        self._enabled = bool(brain_cfg.get("enabled", True))
        self._provider = str(brain_cfg.get("provider", "auto")).strip().lower() or "auto"
        self._base_url = str(brain_cfg.get("base_url", "https://api.x.ai/v1")).rstrip("/")
        self._fast_model = str(brain_cfg.get("fast_model", "grok-4.3"))
        self._smart_model = str(brain_cfg.get("smart_model", "grok-4.3"))
        self._vision_model = str(
            brain_cfg.get("vision_model", brain_cfg.get("fast_model", "grok-4.3"))
        )
        self._fast_effort = str(brain_cfg.get("fast_reasoning_effort", "")).strip()
        self._smart_effort = str(brain_cfg.get("smart_reasoning_effort", "")).strip()
        self._daily_cap = int(brain_cfg.get("daily_call_cap", 50))
        self._timeout = float(brain_cfg.get("request_timeout_seconds", 30))
        self._request_retries = max(0, int(brain_cfg.get("request_retries", 1)))
        self._max_events = int(brain_cfg.get("max_events_in_context", 40))
        self._max_tokens = int(brain_cfg.get("max_answer_tokens", 500))
        self._max_casual_tokens = int(brain_cfg.get("max_casual_answer_tokens", 220))
        self._include_clip_metadata = bool(
            brain_cfg.get("include_clip_metadata_in_context", True)
        )
        self._live_vision_enabled = bool(brain_cfg.get("live_vision_enabled", True))
        self._live_jpeg_quality = int(brain_cfg.get("live_jpeg_quality", 70))
        self._live_max_width = int(brain_cfg.get("live_max_width", 640))
        self._chat_model = str(brain_cfg.get("chat_model", self._fast_model))
        self._google_chat_model = str(brain_cfg.get("google_chat_model", "gemini-1.5-flash"))
        self._google_smart_model = str(brain_cfg.get("google_smart_model", "gemini-1.5-pro"))
        self._google_vision_model = str(
            brain_cfg.get("google_vision_model", self._google_chat_model)
        )
        self._fallback_model = str(brain_cfg.get("fallback_model", "")).strip()
        self._fallback_on_cloud_error = bool(brain_cfg.get("fallback_on_cloud_error", True))
        self._strict_evidence_guardrails = bool(brain_cfg.get("strict_evidence_guardrails", True))
        self._local_casual_fallback = bool(brain_cfg.get("local_casual_fallback", True))
        self._memory_enabled = bool(brain_cfg.get("memory_enabled", True))
        self._memory_max_entries = int(brain_cfg.get("memory_max_entries", 300))
        self._memory_max_context = int(brain_cfg.get("memory_max_context_items", 8))
        self._memory_retention_days = int(brain_cfg.get("memory_retention_days", 90))
        self._google_base_url = str(
            brain_cfg.get("google_base_url", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")

        secrets_file = str(brain_cfg.get("secrets_file", "secrets.yaml"))
        if secrets_path is None:
            secrets_path = os.path.join(_project_root(), secrets_file)
        self._secrets_path = secrets_path
        self._http_post = http_post or _default_http_post
        db_path = ""
        try:
            storage_cfg = config.get("storage") or {}
            db_path = str(storage_cfg.get("database_path", "")).strip()
        except Exception:  # noqa: BLE001
            db_path = ""
        self._memory = MemoryStore(
            db_path,
            enabled=self._memory_enabled,
            max_entries=self._memory_max_entries,
            retention_days=self._memory_retention_days,
        )
        self._recent_responses: Dict[str, Dict[str, Any]] = {}

        # Daily cap bookkeeping (in memory; resets on a new day or restart).
        self._lock = threading.Lock()
        self._calls_today = 0
        self._call_date = date.today()

        # The key is loaded lazily and never stored alongside logs.
        self._api_key = self._load_api_key()
        self._google_api_key = self._load_google_api_key()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def is_available(self) -> bool:
        """True when the brain is turned on AND a key is present."""
        return self._enabled and bool(self._api_key or self._google_api_key)

    def status(self) -> Dict[str, Any]:
        """A small, safe status dict (never includes the key)."""
        with self._lock:
            self._roll_day_if_needed()
            used = self._calls_today
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "has_key": bool(self._api_key),
            "provider": self._effective_provider(),
            "has_google_key": bool(self._google_api_key),
            "chat_model": self._chat_model,
            "fast_model": self._fast_model,
            "smart_model": self._smart_model,
            "fallback_model": self._fallback_model,
            "calls_used_today": used,
            "daily_call_cap": self._daily_cap,
            "calls_remaining": max(0, self._daily_cap - used),
            "memory_enabled": self._memory_enabled,
        }

    def _meta_provider_answer(self) -> str:
        """Plain-language answer about which cloud brain is configured (no secrets)."""
        provider = self._effective_provider()
        if provider == "google":
            model = self._google_chat_model or self._google_vision_model or "gemini"
            if self._google_api_key:
                return (
                    f"Yes — I am running through Google Gemini "
                    f"(model: {model}) using the google_api_key in secrets.yaml."
                )
            return (
                "I am configured for Google Gemini, but google_api_key is missing "
                "in secrets.yaml, so cloud chat cannot run yet."
            )
        if provider == "grok":
            model = self._chat_model or self._fast_model or "grok"
            if self._api_key:
                return (
                    f"No — right now I am using xAI Grok (model: {model}), "
                    "not Gemini. Set brain.provider to google and add google_api_key "
                    "to use Gemini."
                )
            return (
                "I am configured for Grok, but no API key is loaded. "
                "Add google_api_key to secrets.yaml (preferred) or grok_api_key."
            )
        return "The brain provider is not configured."

    def ask(self, question: str) -> Dict[str, Any]:
        """Answer a question using live video, event notes, or both. Never raises."""
        question = (question or "").strip()
        if not question:
            return self._reply(
                False,
                "Please type a question first.",
                offline=False,
                mode="casual",
                source="conversation",
                path="validation_error",
            )

        recent_recap = self._build_recent_recap_answer(question)
        if recent_recap:
            return self._reply(
                True,
                recent_recap,
                offline=True,
                mode="footage",
                source="ledger",
                path="local_recent_recap",
            )

        if is_meta_question(question):
            return self._reply(
                True,
                self._meta_provider_answer(),
                offline=False,
                mode="casual",
                source="conversation",
                path="meta_provider",
            )

        enrollment = _parse_owner_enrollment(question)
        if enrollment is not None and self._owner_enroll_fn is not None:
            enroll_result = self._owner_enroll_fn(enrollment) or {}
            ok = bool(enroll_result.get("ok", False))
            owner_name = str(enrollment.get("name", "owner")).strip() or "owner"
            if ok:
                appearance = enrollment.get("appearance_signature") or {}
                appearance_bits = ", ".join(
                    f"{k}={v}" for k, v in sorted(dict(appearance).items())
                )
                note = (
                    f" I saved appearance markers ({appearance_bits})."
                    if appearance_bits
                    else ""
                )
                return self._reply(
                    True,
                    f"Done. I enrolled {owner_name} as {enrollment.get('role', 'owner_admin')}.{note}",
                    offline=False,
                    mode="hybrid",
                    source="conversation",
                    path="owner_enrollment",
                )
            return self._reply(
                False,
                str(enroll_result.get("message", "Owner enrollment failed.")),
                offline=False,
                mode="hybrid",
                source="conversation",
                path="owner_enrollment_failed",
            )

        mode = classify_query_mode(question)
        route_source = classify_query_source(question)
        source = "conversation" if mode == "casual" else route_source
        plan = _build_query_plan(question, mode, route_source)
        use_live = bool(plan.get("needs_live_frame")) and self._live_vision_enabled
        use_ledger = bool(plan.get("needs_ledger"))

        live_jpeg = self._capture_live_jpeg() if use_live else None
        if route_source == "live" and mode != "casual" and live_jpeg is None:
            return self._reply(
                False,
                "I can't see the live camera right now. Check that the camera is "
                "active on the dashboard, then try again.",
                offline=False,
                mode=mode,
                source=source,
                path="live_unavailable",
            )

        include_raw = _wants_raw_timeline(question)
        ledger_context = (
            self._build_context(self._max_events, include_raw=include_raw)
            if use_ledger
            else ""
        )
        use_heavy_dvr = bool(plan.get("heavy_dvr"))
        dvr_context = ""
        dvr_analysis_context = ""
        dvr_window_start = ""
        dvr_window_end = ""
        dvr_window_source = ""
        dvr_segment_count = 0
        if self._dvr_context_fn is not None and use_ledger:
            try:
                dvr_data = self._dvr_context_fn(question, use_heavy_dvr) or {}
                dvr_context = str(dvr_data.get("context", "")).strip()
                dvr_analysis_context = str(dvr_data.get("analysis_context", "")).strip()
                dvr_window_start = str(dvr_data.get("window_start", "")).strip()
                dvr_window_end = str(dvr_data.get("window_end", "")).strip()
                dvr_window_source = str(dvr_data.get("window_source", "")).strip()
                dvr_segment_count = int(dvr_data.get("segment_count", 0) or 0)
            except Exception as error:  # noqa: BLE001 - DVR fallback is best-effort
                dvr_context = f"(DVR retrieval unavailable: {error})"
        if mode in ("footage", "hybrid") and self._strict_evidence_guardrails and live_jpeg is None:
            has_evidence = bool(ledger_context.strip()) and "(no events recorded yet)" not in ledger_context
            has_dvr = bool(dvr_context.strip()) and "unavailable" not in dvr_context.lower()
            if not has_evidence and not has_dvr:
                return self._reply(
                    True,
                    "I don't have local evidence yet for that footage question. "
                    "Try asking after an event is recorded or include a time window.",
                    offline=True,
                    mode=mode,
                    source=source,
                    path="guardrail_no_evidence",
                )
        evidence = _assess_evidence_quality(
            question=question,
            ledger_context=ledger_context,
            dvr_context=dvr_context,
            dvr_analysis_context=dvr_analysis_context,
            window_source=dvr_window_source,
        )
        messages = self._build_ask_messages(
            question=question,
            mode=mode,
            source=source,
            ledger_context=ledger_context,
            live_jpeg=live_jpeg,
            dvr_context=dvr_context,
            dvr_analysis_context=dvr_analysis_context,
        )
        provider = self._effective_provider()
        if live_jpeg is not None:
            model = self._google_vision_model if provider == "google" else self._vision_model
        else:
            model = self._google_chat_model if provider == "google" else self._chat_model
        token_limit = self._max_casual_tokens if mode == "casual" else self._max_tokens
        effort = self._smart_effort if (use_heavy_dvr and mode in ("footage", "hybrid")) else self._fast_effort
        result = self._complete(messages, model, effort, max_tokens=token_limit)
        if (
            not result.get("ok")
            and self._fallback_on_cloud_error
            and self._fallback_model
            and self._fallback_model != model
        ):
            result = self._complete(
                messages,
                self._fallback_model,
                self._smart_effort,
                count_call=False,
                max_tokens=token_limit,
            )
            result["path"] = "cloud_fallback_model"
        if not result.get("ok") and use_ledger:
            fallback = self._build_local_fallback_answer(
                question=question,
                ledger_context=ledger_context,
                dvr_context=dvr_context,
                dvr_analysis_context=dvr_analysis_context,
                window_start=dvr_window_start,
                window_end=dvr_window_end,
                window_source=dvr_window_source,
                segment_count=dvr_segment_count,
            )
            if fallback:
                result["ok"] = True
                result["answer"] = fallback
                result["offline"] = True
                result["path"] = "local_evidence_fallback"
        if not result.get("ok") and mode == "casual" and self._local_casual_fallback:
            result = self._reply(
                True,
                "I'm temporarily offline for general assistant chat right now. "
                "I can still help with local camera evidence questions like "
                "'what happened in the last hour?' while cloud chat is unavailable.",
                offline=True,
                mode=mode,
                source=source,
                path="local_casual_fallback",
            )
        if result.get("ok"):
            result["source"] = source
            result["mode"] = mode
            result["answer_path"] = self._answer_path(mode)
            result.setdefault("path", "cloud_primary")
            result["plan"] = plan
            result["confidence"] = evidence["confidence"]
            result["evidence_count"] = evidence["evidence_count"]
            result["evidence_flags"] = evidence["flags"]
            response_id = str(uuid.uuid4())
            result["response_id"] = response_id
            self._remember_response(
                response_id,
                question=question,
                answer=str(result.get("answer", "")),
                mode=mode,
                source=source,
                path=str(result.get("path", "cloud_primary")),
            )
            self._memory.capture_inferred_feedback(question=question, answer=str(result.get("answer", "")))
        else:
            result.setdefault("mode", mode)
            result.setdefault("source", source)
            result.setdefault("answer_path", self._answer_path(mode))
            result.setdefault("path", "cloud_error")
            result.setdefault("confidence", evidence["confidence"])
            result.setdefault("evidence_count", evidence["evidence_count"])
            result.setdefault("evidence_flags", evidence["flags"])
        return result

    def _build_recent_recap_answer(self, question: str) -> Optional[str]:
        """Return a deterministic summary for "what just happened" style questions."""
        if not _is_immediate_recap_question(question):
            return None
        window_seconds = _parse_recent_window_seconds(question)
        try:
            records = self._ledger.list_recent(limit=3)
        except Exception:
            return None
        if not records:
            if window_seconds is not None:
                return (
                    f"No new events were logged in the last {_format_window_seconds(window_seconds)}. "
                    "I also don't see any recent recorded activity yet."
                )
            return "I don't see any recent events yet."
        latest = records[0]
        latest_dt = None
        try:
            latest_dt = datetime.fromisoformat(str(latest.timestamp).replace("Z", ""))
        except ValueError:
            latest_dt = None
        entities = latest.entities if isinstance(latest.entities, dict) else {}
        face = entities.get("face") if isinstance(entities, dict) else None
        known_identity = ""
        if isinstance(face, dict) and bool(face.get("known")):
            known_identity = str(face.get("name", "")).strip()
        if not known_identity:
            faces = entities.get("faces") if isinstance(entities, dict) else None
            if isinstance(faces, list):
                for entry in faces:
                    if isinstance(entry, dict) and bool(entry.get("known")):
                        known_identity = str(entry.get("name", "")).strip()
                        if known_identity:
                            break

        clip_analysis = entities.get("clip_analysis") if isinstance(entities, dict) else None
        scene_summary = ""
        if isinstance(clip_analysis, dict):
            scene_summary = str(clip_analysis.get("scene_summary", "")).strip()
        detail = ""
        if scene_summary:
            detail = scene_summary
        elif latest.summary:
            if known_identity and "unknown face" not in latest.title.lower():
                detail = f"{known_identity} was seen on camera. {latest.summary}"
            else:
                detail = str(latest.summary)
        elif known_identity:
            detail = f"{known_identity} was seen near the camera."
        else:
            detail = str(latest.title or "Recent activity detected.")

        if latest_dt is not None:
            age_seconds = max(0.0, (datetime.now() - latest_dt).total_seconds())
            age_text = _format_age_seconds(age_seconds)
            if window_seconds is not None and age_seconds > window_seconds:
                return (
                    f"No new events were logged in the last {_format_window_seconds(window_seconds)}. "
                    f"The most recent activity was {age_text}: {detail}"
                )
            if window_seconds is None and age_seconds > 7 * 60:
                return f"The most recent activity was {age_text}: {detail}"
            return f"{age_text}: {detail}"
        return f"just now: {detail}"

    def summarize_day(self) -> Dict[str, Any]:
        """Return a short recap of today's events. Never raises."""
        context = self._build_context(self._max_events, today_only=True, include_raw=False)
        messages = [
            {"role": "system", "content": _DIGEST_PROMPT},
            {"role": "user", "content": f"Today's event notes (newest first):\n{context}"},
        ]
        model = self._google_smart_model if self._effective_provider() == "google" else self._smart_model
        return self._complete(messages, model, self._smart_effort)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _complete(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        effort: str,
        *,
        count_call: bool = True,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Shared path for ask/summarize: cap check, call, error handling."""
        provider = self._effective_provider()
        if not self._enabled:
            return self._reply(False, "The brain is turned off in config.yaml.", offline=True)
        if provider == "google" and not self._google_api_key:
            return self._reply(
                False,
                "The brain is offline: no Google API key found in secrets.yaml.",
                offline=True,
            )
        if provider == "grok" and not self._api_key:
            return self._reply(
                False,
                "The brain is offline: no Grok API key found in secrets.yaml.",
                offline=True,
            )

        if count_call:
            with self._lock:
                self._roll_day_if_needed()
                if self._calls_today >= self._daily_cap:
                    remaining = max(0, self._daily_cap - self._calls_today)
                    return {
                        "ok": False,
                        "answer": (
                            "Daily question limit reached. The brain will reset tomorrow "
                            "(you can raise 'daily_call_cap' in config.yaml)."
                        ),
                        "offline": False,
                        "calls_remaining": remaining,
                    }
                # Reserve the call up front so concurrent requests can't overshoot.
                self._calls_today += 1

        payload: Dict[str, Any]
        url: str
        headers: Dict[str, str]
        if provider == "google":
            payload = self._build_google_payload(
                messages,
                max_tokens=int(max_tokens if max_tokens is not None else self._max_tokens),
            )
            url = f"{self._google_base_url}/models/{model}:generateContent?key={self._google_api_key}"
            headers = {"Content-Type": "application/json"}
        else:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": int(max_tokens if max_tokens is not None else self._max_tokens),
                "temperature": 0.3,
                "stream": False,
            }
            if effort:
                payload["reasoning_effort"] = effort
            url = f"{self._base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            }

        last_error: Optional[Exception] = None
        for attempt in range(self._request_retries + 1):
            try:
                data = self._http_post(url, headers, payload, self._timeout)
                break
            except urllib.error.HTTPError as error:
                # Retry transient cloud failures.
                if error.code >= 500 and attempt < self._request_retries:
                    last_error = error
                    continue
                detail = _safe_http_error_detail(error)
                return self._reply(
                    False,
                    f"Grok returned an error ({error.code}). {detail}",
                    offline=False,
                )
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt < self._request_retries:
                    continue
                return self._reply(
                    False,
                    "The brain is offline: could not reach Grok "
                    f"({_short_reason(error)}). The rest of the system is fine.",
                    offline=True,
                )
            except Exception as error:  # noqa: BLE001 - never crash the request
                last_error = error
                if attempt < self._request_retries:
                    continue
                return self._reply(False, f"Unexpected brain error: {error}", offline=False)
        else:
            reason = _short_reason(last_error) if last_error is not None else "unknown"
            return self._reply(
                False,
                f"The brain is offline after retry attempts ({reason}).",
                offline=True,
            )

        answer = _extract_google_answer(data) if provider == "google" else _extract_answer(data)
        if not answer:
            return self._reply(
                False, "Cloud model replied but the answer was empty.", offline=False
            )
        return self._reply(True, answer, offline=False, path="cloud_primary")

    def _build_ask_messages(
        self,
        *,
        question: str,
        mode: str,
        source: str,
        ledger_context: str,
        live_jpeg: Optional[bytes],
        dvr_context: str = "",
        dvr_analysis_context: str = "",
    ) -> List[Dict[str, Any]]:
        dvr_block = ""
        if dvr_context:
            dvr_block += f"\n\nContinuous DVR retrieval context:\n{dvr_context}"
        if dvr_analysis_context:
            dvr_block += f"\n\nDVR heavy analysis:\n{dvr_analysis_context}"
        memory_context = self._memory.build_prompt_context(limit=self._memory_max_context)
        profile_context = self._memory.build_profile_context(resident_limit=5)
        memory_block = ""
        if memory_context:
            memory_block += f"\n\nRemembered user preferences:\n{memory_context}"
        if profile_context:
            memory_block += f"\n\nHousehold profile context:\n{profile_context}"
        if mode == "casual":
            system = _CASUAL_SYSTEM_PROMPT
            text = f"Question: {question}{memory_block}"
        elif mode == "hybrid":
            system = _HYBRID_SYSTEM_PROMPT
            if live_jpeg is not None:
                text = (
                    f"Recent event notes (newest first):\n{ledger_context}\n\n"
                    f"{dvr_block}\n\n"
                    f"The attached image is the live camera view right now.\n\n"
                    f"Question: {question}{memory_block}"
                )
            else:
                text = (
                    f"Recent event notes (newest first):\n{ledger_context}\n\n"
                    f"{dvr_block}\n\n"
                    f"Question: {question}{memory_block}"
                )
        elif source == "live" and live_jpeg is not None:
            system = _LIVE_SYSTEM_PROMPT
            text = f"Question: {question}{memory_block}"
        elif source == "both" and live_jpeg is not None:
            system = _COMBINED_SYSTEM_PROMPT
            text = (
                f"Recent event notes (newest first):\n{ledger_context}\n\n"
                f"{dvr_block}\n\n"
                f"The attached image is the live camera view right now.\n\n"
                f"Question: {question}{memory_block}"
            )
        else:
            system = _LEDGER_SYSTEM_PROMPT
            text = (
                f"Recent event notes (newest first):\n{ledger_context}\n\n"
                f"{dvr_block}\n\n"
                f"Question: {question}{memory_block}"
            )

        if live_jpeg is not None:
            b64 = base64.b64encode(live_jpeg).decode("ascii")
            user_content: Any = [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ]
        else:
            user_content = text

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def _capture_live_jpeg(self) -> Optional[bytes]:
        """Grab and encode the latest camera frame for vision queries."""
        if not self._live_vision_enabled or self._frame_getter is None:
            return None
        if self._camera_active_fn is not None and not self._camera_active_fn():
            return None
        if cv2 is None:
            return None
        try:
            frame = self._frame_getter()
            if frame is None:
                return None
            width = self._live_max_width
            if width > 0 and frame.shape[1] > width:
                scale = width / float(frame.shape[1])
                height = max(1, int(frame.shape[0] * scale))
                frame = cv2.resize(frame, (width, height))
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(self._live_jpeg_quality)],
            )
            if not ok:
                return None
            return encoded.tobytes()
        except Exception as error:  # noqa: BLE001 - vision is best-effort
            print(f"[brain] Could not capture live frame: {error}")
            return None

    def _build_context(
        self, limit: int, today_only: bool = False, include_raw: bool = False
    ) -> str:
        """Pull recent events into session-first context (raw optional)."""
        try:
            records = self._ledger.list_recent(limit=max(1, limit))
        except Exception as error:  # noqa: BLE001 - context is best-effort
            return f"(could not read recent events: {error})"

        if today_only:
            prefix = date.today().isoformat()
            records = [r for r in records if str(r.timestamp).startswith(prefix)]

        if not records:
            return "(no events recorded yet)"

        lines: List[str] = []
        for r in records:
            session = (r.entities or {}).get("session")
            clip_analysis = (r.entities or {}).get("clip_analysis")
            clip_status = str((r.entities or {}).get("clip_status", "")).strip().lower()
            clip_scene = ""
            clip_actors = ""
            if isinstance(clip_analysis, dict):
                clip_scene = str(clip_analysis.get("scene_summary", "")).strip()
                actors = clip_analysis.get("actors")
                if isinstance(actors, list) and actors:
                    clip_actors = ", ".join(str(a) for a in actors[:4])
            if isinstance(session, dict) and not include_raw:
                duration = session.get("duration_seconds")
                peak_area = session.get("peak_area")
                line = f"- [{r.timestamp}] {r.title}"
                if duration is not None:
                    line += f" ({duration}s)"
                if clip_scene:
                    line += f": footage={clip_scene}"
                elif r.summary:
                    line += f": note={r.summary}"
                elif r.clip_path:
                    line += ": footage recorded; clip analysis pending"
                if peak_area is not None:
                    line += f" | peak_area: {peak_area}"
                if clip_actors:
                    line += f" | actors: {clip_actors}"
                if r.clip_path and clip_status == "saved":
                    line += " | footage_available: true"
            else:
                line = f"- [{r.timestamp}] {r.title}"
                if r.summary:
                    line += f": {r.summary}"
            if r.clip_path:
                line += " [clip saved]"
            if self._include_clip_metadata:
                if isinstance(clip_analysis, dict):
                    if clip_scene and "footage=" not in line:
                        line += f" | clip_summary: {clip_scene}"
                    if clip_actors and "actors:" not in line:
                        line += f" | actors: {clip_actors}"
            lines.append(line)
        return "\n".join(lines)

    def _answer_path(self, mode: str) -> str:
        """Response contract: casual dialog vs evidence-backed security answers."""
        return "casual" if mode == "casual" else "evidence"

    def _reply(
        self,
        ok: bool,
        message: str,
        *,
        offline: bool,
        mode: str = "footage",
        source: str = "ledger",
        path: str = "cloud_primary",
    ) -> Dict[str, Any]:
        with self._lock:
            self._roll_day_if_needed()
            remaining = max(0, self._daily_cap - self._calls_today)
        return {
            "ok": ok,
            "answer": message,
            "offline": offline,
            "calls_remaining": remaining,
            "mode": mode,
            "source": source,
            "path": path,
            "answer_path": self._answer_path(mode),
        }

    def _remember_response(
        self,
        response_id: str,
        *,
        question: str,
        answer: str,
        mode: str,
        source: str,
        path: str,
    ) -> None:
        self._recent_responses[response_id] = {
            "question": question,
            "answer": answer,
            "mode": mode,
            "source": source,
            "path": path,
        }
        if len(self._recent_responses) > 50:
            first = next(iter(self._recent_responses))
            self._recent_responses.pop(first, None)

    def capture_feedback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response_id = str(payload.get("response_id", "")).strip()
        question = str(payload.get("question", "")).strip()
        answer = str(payload.get("answer", "")).strip()
        mode = str(payload.get("mode", "")).strip()
        source = str(payload.get("source", "")).strip()
        path = str(payload.get("path", "")).strip()
        if response_id and response_id in self._recent_responses:
            cached = self._recent_responses.get(response_id) or {}
            question = question or str(cached.get("question", ""))
            answer = answer or str(cached.get("answer", ""))
            mode = mode or str(cached.get("mode", ""))
            source = source or str(cached.get("source", ""))
            path = path or str(cached.get("path", ""))
        if not question and not answer:
            return {"ok": False, "message": "No response context was provided."}
        helpful = payload.get("helpful")
        correction = str(payload.get("correction", "")).strip()
        remember_preference = bool(payload.get("remember_preference", False))
        self._memory.capture_explicit_feedback(
            question=question,
            answer=answer,
            mode=mode or "footage",
            source=source or "ledger",
            path=path or "unknown",
            helpful=helpful if isinstance(helpful, bool) else None,
            correction=correction,
            remember_preference=remember_preference,
        )
        return {"ok": True, "stored": self._memory.is_enabled(), "response_id": response_id}

    def memory_status(self) -> Dict[str, Any]:
        status = self._memory.status(preference_limit=self._memory_max_context)
        status["memory_enabled"] = self._memory_enabled
        return status

    def clear_memory(self, *, preference_key: str = "") -> Dict[str, Any]:
        """Delete remembered preferences / feedback (bounded trust control)."""
        if preference_key:
            deleted = self._memory.delete_preference(preference_key)
            return {
                "ok": True,
                "deleted": deleted,
                "scope": "preference",
                "preference_key": preference_key,
            }
        deleted = self._memory.clear_all()
        return {"ok": True, "deleted": deleted, "scope": "all"}

    def owner_profile_status(self) -> Dict[str, Any]:
        profile = self._memory.get_owner_profile()
        return {
            "ok": True,
            "profile": profile,
            "configured": bool(profile),
        }

    def save_owner_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        return self._memory.upsert_owner_profile(profile)

    def list_resident_profiles(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return self._memory.list_resident_profiles(limit=limit)

    def save_resident_profile(self, resident_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        return self._memory.upsert_resident_profile(resident_id, profile)

    def set_owner_preference(self, key: str, value: Any) -> Optional[Dict[str, Any]]:
        return self._memory.set_owner_preference(key, value)

    def enroll_owner_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._owner_enroll_fn is None:
            return {"ok": False, "message": "Owner enrollment is not available in this runtime."}
        return self._owner_enroll_fn(payload or {})

    def _effective_provider(self) -> str:
        if self._provider in ("grok", "google"):
            return self._provider
        if self._google_api_key:
            return "google"
        return "grok"

    def _build_google_payload(self, messages: List[Dict[str, Any]], *, max_tokens: int) -> Dict[str, Any]:
        system_text = ""
        user_parts: List[Dict[str, Any]] = []
        for item in messages:
            role = str(item.get("role", "")).strip().lower()
            content = item.get("content")
            if role == "system":
                system_text = str(content or "")
                continue
            if role != "user":
                continue
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        user_parts.append({"text": str(part.get("text", ""))})
                    elif part.get("type") == "image_url":
                        url = str((part.get("image_url") or {}).get("url", ""))
                        if url.startswith("data:image/jpeg;base64,"):
                            b64 = url.split(",", 1)[1]
                            user_parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": "image/jpeg",
                                        "data": b64,
                                    }
                                }
                            )
            else:
                user_parts.append({"text": str(content or "")})
        if not user_parts:
            user_parts = [{"text": ""}]
        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": user_parts}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": int(max_tokens)},
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload

    def _build_local_fallback_answer(
        self,
        *,
        question: str,
        ledger_context: str,
        dvr_context: str,
        dvr_analysis_context: str,
        window_start: str,
        window_end: str,
        window_source: str,
        segment_count: int,
    ) -> str:
        """Best-effort deterministic answer when cloud model is unavailable."""
        evidence: List[str] = []
        analysis_lines = [line.strip() for line in dvr_analysis_context.splitlines() if line.strip()]
        retrieval_lines = [
            line.strip()
            for line in dvr_context.splitlines()
            if line.strip() and not line.strip().startswith("Evidence window:")
        ]
        ledger_lines = [line.strip() for line in ledger_context.splitlines() if line.strip()]
        if analysis_lines:
            evidence.extend(analysis_lines[:2])
        if retrieval_lines:
            evidence.extend(retrieval_lines[:2])
        if ledger_lines:
            evidence.extend(ledger_lines[:2])
        if not evidence:
            return (
                "I can't reach Grok right now, and local evidence does not yet "
                "contain a matching event for that question."
            )
        opening = "I can't reach Grok right now, but here's what local evidence shows:"
        window_note = ""
        if window_start and window_end:
            source_note = f" ({window_source})" if window_source else ""
            window_note = (
                f" I checked the DVR window {window_start} to {window_end}{source_note}"
                f" and found {max(0, segment_count)} segment(s)."
            )
        joined = " ".join(evidence[:3])
        return f"{opening}{window_note} {joined}"

    def _roll_day_if_needed(self) -> None:
        """Reset the daily counter when the date changes. Call under the lock."""
        today = date.today()
        if today != self._call_date:
            self._call_date = today
            self._calls_today = 0

    def _load_api_key(self) -> str:
        """Read the Grok key from secrets.yaml. Returns '' if not available."""
        path = self._secrets_path
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception as error:  # noqa: BLE001 - missing/broken secret is fine
            print(f"[brain] Warning: could not read secrets file: {error}")
            return ""
        if not isinstance(data, dict):
            return ""
        key = data.get("grok_api_key") or data.get("GROK_API_KEY") or ""
        return str(key).strip()

    def _load_google_api_key(self) -> str:
        path = self._secrets_path
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        key = data.get("google_api_key") or data.get("GOOGLE_API_KEY") or ""
        return str(key).strip()


# ---------------------------------------------------------------------- #
# Module-level helpers (also used by tests)
# ---------------------------------------------------------------------- #
def _default_http_post(
    url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float
) -> Dict[str, Any]:
    """Perform the real HTTP POST to Grok using only the standard library."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _extract_answer(data: Dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenAI-style chat response."""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()
    except Exception:  # noqa: BLE001 - tolerate unexpected shapes
        return ""


def _extract_google_answer(data: Dict[str, Any]) -> str:
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        chunks = [str(p.get("text", "")).strip() for p in parts if str(p.get("text", "")).strip()]
        return "\n".join(chunks).strip()
    except Exception:
        return ""


def _safe_http_error_detail(error: urllib.error.HTTPError) -> str:
    """Best-effort, short detail from an HTTP error (never leaks the key)."""
    try:
        raw = error.read().decode("utf-8")
        parsed = json.loads(raw)
        msg = parsed.get("error")
        if isinstance(msg, dict):
            msg = msg.get("message")
        if msg:
            return str(msg)[:200]
    except Exception:  # noqa: BLE001
        pass
    return "Check the API key and your xAI account."


def _short_reason(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    return str(reason or error)[:120]


def _should_use_heavy_dvr(question: str) -> bool:
    q = (question or "").lower()
    heavy_hints = (
        "steal",
        "stole",
        "took",
        "take",
        "unplug",
        "moved",
        "why",
        "how did",
        "intention",
        "what happened to",
    )
    return any(h in q for h in heavy_hints)
