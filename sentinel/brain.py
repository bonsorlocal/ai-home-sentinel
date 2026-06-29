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

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Callable, Dict, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML is already required
    raise ImportError(
        "PyYAML is not installed. Run 'pip install -r requirements.txt' first."
    ) from exc

from sentinel.events import EventLedger


def _project_root() -> str:
    """Return the project root folder (the folder that contains config.yaml)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# The system prompt keeps Grok grounded: answer only from the notes we give it,
# stay short, and admit when it does not know.
_SYSTEM_PROMPT = (
    "You are the assistant for a home security camera called AI Home Sentinel. "
    "You answer the owner's questions using ONLY the recent event notes provided. "
    "Each note is something the camera noticed (motion, an object like a person "
    "or car, or a face). Be brief, friendly, and concrete. Refer to times in a "
    "natural way. If the notes do not contain the answer, say so plainly instead "
    "of guessing."
)

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
    ) -> None:
        self._ledger = ledger
        brain_cfg: Dict[str, Any] = {}
        try:
            brain_cfg = config.get("brain") or {}
        except Exception:  # noqa: BLE001 - config is best-effort
            brain_cfg = {}

        self._enabled = bool(brain_cfg.get("enabled", True))
        self._base_url = str(brain_cfg.get("base_url", "https://api.x.ai/v1")).rstrip("/")
        self._fast_model = str(brain_cfg.get("fast_model", "grok-4.3"))
        self._smart_model = str(brain_cfg.get("smart_model", "grok-4.3"))
        self._fast_effort = str(brain_cfg.get("fast_reasoning_effort", "")).strip()
        self._smart_effort = str(brain_cfg.get("smart_reasoning_effort", "")).strip()
        self._daily_cap = int(brain_cfg.get("daily_call_cap", 50))
        self._timeout = float(brain_cfg.get("request_timeout_seconds", 30))
        self._max_events = int(brain_cfg.get("max_events_in_context", 40))
        self._max_tokens = int(brain_cfg.get("max_answer_tokens", 500))

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
        """Answer a question about recent events. Never raises."""
        question = (question or "").strip()
        if not question:
            return self._reply(False, "Please type a question first.", offline=False)

        context = self._build_context(self._max_events)
        user_content = (
            f"Recent event notes (newest first):\n{context}\n\n"
            f"Question: {question}"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        return self._complete(messages, self._fast_model, self._fast_effort)

    def summarize_day(self) -> Dict[str, Any]:
        """Return a short recap of today's events. Never raises."""
        context = self._build_context(self._max_events, today_only=True)
        messages = [
            {"role": "system", "content": _DIGEST_PROMPT},
            {"role": "user", "content": f"Today's event notes (newest first):\n{context}"},
        ]
        return self._complete(messages, self._smart_model, self._smart_effort)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _complete(
        self, messages: List[Dict[str, str]], model: str, effort: str
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
                return self._reply(
                    False,
                    "Daily question limit reached. The brain will reset tomorrow "
                    "(you can raise 'daily_call_cap' in config.yaml).",
                    offline=False,
                )
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

    def _build_context(self, limit: int, today_only: bool = False) -> str:
        """Pull recent events and trim them into a compact text block."""
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
            line = f"- [{r.timestamp}] {r.title}"
            if r.summary:
                line += f": {r.summary}"
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
