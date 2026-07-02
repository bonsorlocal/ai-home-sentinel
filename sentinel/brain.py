"""Language brain (B1) for AI Home Sentinel.

This adds a small "brain" that can answer plain-language questions about what
the system has seen, using xAI's Grok in the cloud. It does two things:

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
import threading
import urllib.error
import urllib.request
from datetime import date
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

FrameGetter = Callable[[], Any]
CameraActiveFn = Callable[[], bool]
DvrContextFn = Callable[[str, bool], Dict[str, Any]]


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
    "way. If context does not contain the answer, say so plainly instead of guessing."
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
    "and 'when did X happen' questions. Be brief, friendly, and concrete."
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


def classify_query_source(question: str) -> str:
    """Return ``live``, ``ledger``, or ``both`` for routing ask() context."""
    q = (question or "").strip().lower()
    if not q:
        return "ledger"
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
    ) -> None:
        self._ledger = ledger
        self._frame_getter = frame_getter
        self._camera_active_fn = camera_active_fn
        self._dvr_context_fn = dvr_context_fn
        brain_cfg: Dict[str, Any] = {}
        try:
            brain_cfg = config.get("brain") or {}
        except Exception:  # noqa: BLE001 - config is best-effort
            brain_cfg = {}

        self._enabled = bool(brain_cfg.get("enabled", True))
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
        self._max_events = int(brain_cfg.get("max_events_in_context", 40))
        self._max_tokens = int(brain_cfg.get("max_answer_tokens", 500))
        self._include_clip_metadata = bool(
            brain_cfg.get("include_clip_metadata_in_context", True)
        )
        self._live_vision_enabled = bool(brain_cfg.get("live_vision_enabled", True))
        self._live_jpeg_quality = int(brain_cfg.get("live_jpeg_quality", 70))
        self._live_max_width = int(brain_cfg.get("live_max_width", 640))
        self._dvr_max_segments_per_query = int(brain_cfg.get("dvr_max_segments_per_query", 8))
        self._dvr_max_analysis_segments = int(brain_cfg.get("dvr_max_analysis_segments", 2))
        self._dvr_timeout_budget_seconds = float(
            brain_cfg.get("dvr_timeout_budget_seconds", 35)
        )

        secrets_file = str(brain_cfg.get("secrets_file", "secrets.yaml"))
        if secrets_path is None:
            secrets_path = os.path.join(_project_root(), secrets_file)
        self._secrets_path = secrets_path
        self._http_post = http_post or _default_http_post

        # Daily cap bookkeeping (in memory; resets on a new day or restart).
        self._lock = threading.Lock()
        self._calls_today = 0
        self._call_date = date.today()

        # The key is loaded lazily and never stored alongside logs.
        self._api_key = self._load_api_key()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def is_available(self) -> bool:
        """True when the brain is turned on AND a key is present."""
        return self._enabled and bool(self._api_key)

    def status(self) -> Dict[str, Any]:
        """A small, safe status dict (never includes the key)."""
        with self._lock:
            self._roll_day_if_needed()
            used = self._calls_today
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "has_key": bool(self._api_key),
            "fast_model": self._fast_model,
            "smart_model": self._smart_model,
            "calls_used_today": used,
            "daily_call_cap": self._daily_cap,
            "calls_remaining": max(0, self._daily_cap - used),
        }

    def ask(self, question: str) -> Dict[str, Any]:
        """Answer a question using live video, event notes, or both. Never raises."""
        question = (question or "").strip()
        if not question:
            return self._reply(False, "Please type a question first.", offline=False)

        source = classify_query_source(question)
        use_live = source in ("live", "both") and self._live_vision_enabled
        use_ledger = source in ("ledger", "both")

        live_jpeg = self._capture_live_jpeg() if use_live else None
        if source == "live" and live_jpeg is None:
            return self._reply(
                False,
                "I can't see the live camera right now. Check that the camera is "
                "active on the dashboard, then try again.",
                offline=False,
            )

        include_raw = _wants_raw_timeline(question)
        ledger_context = (
            self._build_context(self._max_events, include_raw=include_raw)
            if use_ledger
            else ""
        )
        use_heavy_dvr = _should_use_heavy_dvr(question)
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
        messages = self._build_ask_messages(
            question=question,
            source=source,
            ledger_context=ledger_context,
            live_jpeg=live_jpeg,
            dvr_context=dvr_context,
            dvr_analysis_context=dvr_analysis_context,
        )
        model = self._vision_model if live_jpeg is not None else self._fast_model
        result = self._complete(messages, model, self._fast_effort)
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
        if result.get("ok"):
            result["source"] = source
        return result

    def summarize_day(self) -> Dict[str, Any]:
        """Return a short recap of today's events. Never raises."""
        context = self._build_context(self._max_events, today_only=True, include_raw=False)
        messages = [
            {"role": "system", "content": _DIGEST_PROMPT},
            {"role": "user", "content": f"Today's event notes (newest first):\n{context}"},
        ]
        return self._complete(messages, self._smart_model, self._smart_effort)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _complete(
        self, messages: List[Dict[str, Any]], model: str, effort: str
    ) -> Dict[str, Any]:
        """Shared path for ask/summarize: cap check, call, error handling."""
        if not self._enabled:
            return self._reply(False, "The brain is turned off in config.yaml.", offline=True)
        if not self._api_key:
            return self._reply(
                False,
                "The brain is offline: no Grok API key found in secrets.yaml.",
                offline=True,
            )

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

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_tokens,
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

        try:
            data = self._http_post(url, headers, payload, self._timeout)
        except urllib.error.HTTPError as error:
            detail = _safe_http_error_detail(error)
            return self._reply(
                False,
                f"Grok returned an error ({error.code}). {detail}",
                offline=False,
            )
        except (urllib.error.URLError, TimeoutError) as error:
            return self._reply(
                False,
                "The brain is offline: could not reach Grok "
                f"({_short_reason(error)}). The rest of the system is fine.",
                offline=True,
            )
        except Exception as error:  # noqa: BLE001 - never crash the request
            return self._reply(False, f"Unexpected brain error: {error}", offline=False)

        answer = _extract_answer(data)
        if not answer:
            return self._reply(
                False, "Grok replied but the answer was empty.", offline=False
            )
        return self._reply(True, answer, offline=False)

    def _build_ask_messages(
        self,
        *,
        question: str,
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
        if source == "live" and live_jpeg is not None:
            system = _LIVE_SYSTEM_PROMPT
            text = f"Question: {question}"
        elif source == "both" and live_jpeg is not None:
            system = _COMBINED_SYSTEM_PROMPT
            text = (
                f"Recent event notes (newest first):\n{ledger_context}\n\n"
                f"{dvr_block}\n\n"
                f"The attached image is the live camera view right now.\n\n"
                f"Question: {question}"
            )
        else:
            system = _LEDGER_SYSTEM_PROMPT
            text = (
                f"Recent event notes (newest first):\n{ledger_context}\n\n"
                f"{dvr_block}\n\n"
                f"Question: {question}"
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

    def _reply(self, ok: bool, message: str, *, offline: bool) -> Dict[str, Any]:
        with self._lock:
            self._roll_day_if_needed()
            remaining = max(0, self._daily_cap - self._calls_today)
        return {
            "ok": ok,
            "answer": message,
            "offline": offline,
            "calls_remaining": remaining,
        }

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
