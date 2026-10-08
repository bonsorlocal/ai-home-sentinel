"""Optional carrier phone bridge (Phase 10).

When ``household.telephony_enabled`` / ``telephony.enabled`` is true and Twilio
credentials exist, ``Connect me`` can place an outbound call to the owner.
Without credentials this module stays a safe no-op stub.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlencode

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

from sentinel.events import EventRecord


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TelephonyBridge:
    """Thin Twilio REST wrapper for Connect-me callouts."""

    def __init__(self, config: Any) -> None:
        household = config.get("household") or {}
        tel = config.get("telephony") or {}
        notify = config.get("notifications") or {}

        self._enabled = bool(
            tel.get("enabled", False) or household.get("telephony_enabled", False)
        )
        self._owner_number = str(tel.get("owner_number", "") or "").strip()
        self._from_number = str(tel.get("from_number", "") or "").strip()
        self._twiml_url = str(tel.get("twiml_url", "") or "").strip()
        self._timeout = float(tel.get("request_timeout_seconds", 15))
        self._public_base_url = str(
            tel.get("public_base_url") or notify.get("public_base_url") or ""
        ).rstrip("/")

        secrets_file = str(tel.get("secrets_file", "secrets.yaml"))
        path = secrets_file
        if not os.path.isabs(path):
            path = os.path.join(_project_root(), secrets_file)
        self._account_sid = ""
        self._auth_token = ""
        self._load_secrets(path)

    def is_available(self) -> bool:
        return bool(
            self._enabled
            and self._account_sid
            and self._auth_token
            and self._owner_number
            and self._from_number
        )

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "owner_number_set": bool(self._owner_number),
            "from_number_set": bool(self._from_number),
            "credentials_set": bool(self._account_sid and self._auth_token),
            "provider": "twilio",
        }

    def connect_owner(self, record: EventRecord) -> Dict[str, Any]:
        """Place an outbound call to the owner about ``record``. Never raises."""
        if not self._enabled:
            return {
                "ok": False,
                "message": "Telephony is disabled (set telephony.enabled or household.telephony_enabled).",
            }
        if not self.is_available():
            return {
                "ok": False,
                "message": (
                    "Telephony not fully configured. Need twilio_account_sid, "
                    "twilio_auth_token, owner_number, and from_number."
                ),
            }

        summary = str(record.summary or record.title or "visitor at the door")[:160]
        twiml = self._twiml_url
        if not twiml:
            # Inline TwiML via Twilio's echo-like approach is not available;
            # require a hosted TwiML URL or use say via Twilio Studio.
            # Fallback: use Twilio's Twimlets echo for a spoken prompt.
            say = f"Sentinel alert. {summary}. Open the dashboard to talk to the visitor."
            twiml = "http://twimlets.com/message?" + urlencode({"Message[0]": say})

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{self._account_sid}/Calls.json"
        )
        form = urlencode(
            {
                "To": self._owner_number,
                "From": self._from_number,
                "Url": twiml,
            }
        ).encode("utf-8")
        auth = base64.b64encode(
            f"{self._account_sid}:{self._auth_token}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            url,
            data=form,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            sid = str(data.get("sid", ""))
            return {
                "ok": True,
                "message": "Calling your phone now.",
                "call_sid": sid,
                "event_id": record.id,
            }
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:200]
            return {"ok": False, "message": f"Twilio HTTP {error.code}: {detail}"}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "message": f"Telephony failed: {error}"}

    def _load_secrets(self, path: str) -> None:
        if yaml is None or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(data, dict):
            return
        self._account_sid = str(
            data.get("twilio_account_sid") or data.get("TWILIO_ACCOUNT_SID") or ""
        ).strip()
        self._auth_token = str(
            data.get("twilio_auth_token") or data.get("TWILIO_AUTH_TOKEN") or ""
        ).strip()
        if not self._owner_number:
            self._owner_number = str(
                data.get("owner_phone") or data.get("OWNER_PHONE") or ""
            ).strip()
        if not self._from_number:
            self._from_number = str(
                data.get("twilio_from_number") or data.get("TWILIO_FROM_NUMBER") or ""
            ).strip()
