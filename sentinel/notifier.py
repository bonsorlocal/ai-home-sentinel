"""Phone notifications (Phase 7b) for tier-2 alerts.

Sends push notifications when the reasoner promotes an event to a configured
tier (default 2). Uses ntfy.sh by default — a simple HTTP POST with no extra
dependencies (stdlib ``urllib`` only, like ``brain.py``).

Design goals:
- **Graceful offline.** Missing topic, disabled config, or network errors never
  crash the runtime; failures are logged and ignored.
- **Cooldown.** Avoid spamming the phone when many tier-2 events fire close
  together.
- **Secrets stay local.** Optional ``ntfy_auth_token`` is read from
  ``secrets.yaml`` (git-ignored).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyYAML is not installed. Run 'pip install -r requirements.txt' first."
    ) from exc

from sentinel.events import EventRecord


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HttpPost = Callable[[str, Dict[str, str], bytes, float], int]


class Notifier:
    """Sends push notifications for high-tier events. Never raises."""

    def __init__(
        self,
        config: Any,
        *,
        secrets_path: Optional[str] = None,
        http_post: Optional[HttpPost] = None,
    ) -> None:
        notify_cfg: Dict[str, Any] = {}
        try:
            notify_cfg = config.get("notifications") or {}
        except Exception:  # noqa: BLE001
            notify_cfg = {}

        self._enabled = bool(notify_cfg.get("enabled", False))
        self._provider = str(notify_cfg.get("provider", "ntfy")).strip().lower()
        self._topic = str(notify_cfg.get("topic", "")).strip()
        self._cooldown = float(notify_cfg.get("cooldown_seconds", 60))
        self._notify_on_tier = int(notify_cfg.get("notify_on_tier", 2))
        self._timeout = float(notify_cfg.get("request_timeout_seconds", 10))

        secrets_file = str(notify_cfg.get("secrets_file", "secrets.yaml"))
        if secrets_path is None:
            secrets_path = os.path.join(_project_root(), secrets_file)
        self._secrets_path = secrets_path
        self._auth_token = self._load_auth_token()

        self._http_post = http_post or _default_http_post
        self._lock = threading.Lock()
        self._last_sent_at = 0.0

    def is_available(self) -> bool:
        """True when notifications are enabled and configured."""
        return self._enabled and bool(self._topic) and self._provider == "ntfy"

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "provider": self._provider,
            "topic_set": bool(self._topic),
            "notify_on_tier": self._notify_on_tier,
            "cooldown_seconds": self._cooldown,
        }

    def should_notify(self, event: EventRecord) -> bool:
        """Return True if this event qualifies for a push notification."""
        if not self.is_available():
            return False
        if event.tier < self._notify_on_tier:
            return False
        if event.notified:
            return False
        return True

    def notify(self, event: EventRecord) -> bool:
        """Attempt to send a notification. Returns True on success. Never raises."""
        if not self.should_notify(event):
            return False

        with self._lock:
            now = time.monotonic()
            if now - self._last_sent_at < self._cooldown:
                return False

        try:
            if self._provider == "ntfy":
                ok = self._send_ntfy(event)
            else:
                print(f"[notifier] Unknown provider '{self._provider}'; skipping.")
                return False
        except Exception as error:  # noqa: BLE001
            print(f"[notifier] Send failed: {error}")
            return False

        if ok:
            with self._lock:
                self._last_sent_at = time.monotonic()
        return ok

    def _send_ntfy(self, event: EventRecord) -> bool:
        url = f"https://ntfy.sh/{self._topic}"
        body = f"{event.title}\n{event.summary}".strip().encode("utf-8")
        headers = {
            "Title": event.title[:200],
            "Priority": "high" if event.tier >= 2 else "default",
            "Tags": "warning",
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        status = self._http_post(url, headers, body, self._timeout)
        if status < 200 or status >= 300:
            print(f"[notifier] ntfy returned HTTP {status}")
            return False
        return True

    def _load_auth_token(self) -> str:
        path = self._secrets_path
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception as error:  # noqa: BLE001
            print(f"[notifier] Warning: could not read secrets file: {error}")
            return ""
        if not isinstance(data, dict):
            return ""
        token = data.get("ntfy_auth_token") or data.get("NTFY_AUTH_TOKEN") or ""
        return str(token).strip()


def _default_http_post(
    url: str, headers: Dict[str, str], body: bytes, timeout: float
) -> int:
    """POST to ntfy.sh using only the standard library."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
