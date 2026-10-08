"""Notification action webhooks (Phase 9D).

Handles Answer / Ignore / Connect actions from ntfy push buttons.
Tokens are short-lived HMAC signatures over (action, event_id, expiry).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

from sentinel.events import EventLedger, EventRecord


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ActionHandler:
    """Validate action tokens and dispatch door / connect workflows."""

    def __init__(
        self,
        config: Any,
        ledger: EventLedger,
        *,
        speak_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        brain_ask_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        telephony_fn: Optional[Callable[[EventRecord], Dict[str, Any]]] = None,
        public_base_url: str = "",
    ) -> None:
        notify_cfg = config.get("notifications") or {}
        dash_cfg = config.get("dashboard") or {}
        self._enabled = bool(notify_cfg.get("actions_enabled", True))
        self._token_ttl = max(60, int(notify_cfg.get("action_token_ttl_seconds", 900)))
        self._public_base_url = str(
            public_base_url
            or notify_cfg.get("public_base_url", "")
            or ""
        ).rstrip("/")
        self._ledger = ledger
        self._speak_fn = speak_fn
        self._brain_ask_fn = brain_ask_fn
        self._telephony_fn = telephony_fn

        secret = str(dash_cfg.get("token", "") or "").strip()
        if not secret:
            secrets_file = str(dash_cfg.get("secrets_file", "secrets.yaml"))
            secret = self._load_secret(secrets_file)
        if not secret:
            secret = str(notify_cfg.get("topic", "") or "sentinel-actions")
        self._secret = secret.encode("utf-8")

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "action_token_ttl_seconds": self._token_ttl,
            "public_base_url_set": bool(self._public_base_url),
        }

    def make_token(self, action: str, event_id: int, *, expires_at: Optional[int] = None) -> str:
        exp = int(expires_at or (time.time() + self._token_ttl))
        payload = f"{action}:{int(event_id)}:{exp}"
        sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return f"{exp}.{sig}"

    def verify_token(self, action: str, event_id: int, token: str) -> bool:
        text = str(token or "").strip()
        if not text or "." not in text:
            return False
        exp_s, sig = text.split(".", 1)
        try:
            exp = int(exp_s)
        except ValueError:
            return False
        if exp < int(time.time()):
            return False
        expected = self.make_token(action, event_id, expires_at=exp)
        return hmac.compare_digest(expected, f"{exp}.{sig}")

    def action_urls(self, event_id: int) -> Dict[str, str]:
        """Build absolute URLs for ntfy action buttons when public_base_url is set."""
        if not self._public_base_url:
            return {}
        urls = {}
        for action in ("answer-door", "ignore", "connect"):
            token = self.make_token(action, event_id)
            qs = urlencode({"event_id": int(event_id), "token": token})
            urls[action] = f"{self._public_base_url}/api/actions/{action}?{qs}"
        return urls

    def handle(self, action: str, event_id: int, token: str) -> Dict[str, Any]:
        if not self._enabled:
            return {"ok": False, "message": "Notification actions are disabled."}
        action = str(action or "").strip().lower()
        if action not in ("answer-door", "ignore", "connect"):
            return {"ok": False, "message": f"Unknown action: {action}"}
        if not self.verify_token(action, event_id, token):
            return {"ok": False, "message": "Invalid or expired action token."}

        record = self._ledger.get_by_id(int(event_id))
        if record is None:
            return {"ok": False, "message": "Event not found."}

        if action == "ignore":
            entities = dict(record.entities or {})
            entities["action_taken"] = "ignore"
            self._ledger.update_entities(record.id, entities)
            return {"ok": True, "action": action, "message": "Alert dismissed."}

        if action == "answer-door":
            return self._answer_door(record)

        return self._connect(record)

    def _answer_door(self, record: EventRecord) -> Dict[str, Any]:
        scene = (record.entities or {}).get("cloud_scene") or {}
        summary = str(scene.get("short_summary") or record.summary or "a visitor").strip()
        prompt = (
            "You are answering the front door for the homeowner over a camera speaker. "
            f"Visitor context: {summary}. "
            "Say one short friendly sentence asking how you can help. No quotes."
        )
        line = "Hello! How can I help you today?"
        if self._brain_ask_fn is not None:
            try:
                result = self._brain_ask_fn(prompt) or {}
                if result.get("ok") and result.get("answer"):
                    line = str(result["answer"]).strip().split("\n")[0][:280]
            except Exception as error:  # noqa: BLE001
                print(f"[actions] Brain door script failed: {error}")

        spoken = {"ok": False, "message": "Voice output unavailable."}
        if self._speak_fn is not None:
            try:
                spoken = self._speak_fn(line) or spoken
            except Exception as error:  # noqa: BLE001
                spoken = {"ok": False, "message": str(error)[:180]}

        entities = dict(record.entities or {})
        entities["action_taken"] = "answer-door"
        entities["spoken_line"] = line
        self._ledger.update_entities(record.id, entities)
        return {
            "ok": True,
            "action": "answer-door",
            "message": line,
            "spoken": spoken,
        }

    def _connect(self, record: EventRecord) -> Dict[str, Any]:
        hold = "Please hold — connecting you to the homeowner now."
        spoken = {"ok": False, "message": "Voice output unavailable."}
        if self._speak_fn is not None:
            try:
                spoken = self._speak_fn(hold) or spoken
            except Exception as error:  # noqa: BLE001
                spoken = {"ok": False, "message": str(error)[:180]}

        telephony: Dict[str, Any] = {
            "ok": False,
            "message": "Telephony not configured; open the dashboard to talk.",
        }
        if self._telephony_fn is not None:
            try:
                telephony = self._telephony_fn(record) or telephony
            except Exception as error:  # noqa: BLE001
                telephony = {"ok": False, "message": str(error)[:180]}

        entities = dict(record.entities or {})
        entities["action_taken"] = "connect"
        entities["spoken_line"] = hold
        self._ledger.update_entities(record.id, entities)
        dashboard = self._public_base_url or "http://localhost:5000"
        return {
            "ok": True,
            "action": "connect",
            "message": hold,
            "spoken": spoken,
            "telephony": telephony,
            "dashboard_url": f"{dashboard}/?connect_event={record.id}",
        }

    def _load_secret(self, secrets_file: str) -> str:
        path = secrets_file
        if not os.path.isabs(path):
            path = os.path.join(_project_root(), path)
        if yaml is None or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:  # noqa: BLE001
            return ""
        if not isinstance(data, dict):
            return ""
        return str(
            data.get("dashboard_token")
            or data.get("DASHBOARD_TOKEN")
            or data.get("action_hmac_secret")
            or ""
        ).strip()
