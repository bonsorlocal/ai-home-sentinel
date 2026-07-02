#!/usr/bin/env python3
"""Quick Pi-side validation probe for DVR and chat endpoints."""

from __future__ import annotations

import json
import urllib.request


def _get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    status = _get_json("http://localhost:5000/status")
    dvr = _get_json("http://localhost:5000/api/dvr/status")
    dvr_range = _get_json("http://localhost:5000/api/dvr/range?limit=1")
    chat = _post_json(
        "http://localhost:5000/api/chat",
        {"question": "What happened in the last 12 hours? Keep it brief."},
    )

    output = {
        "health_ok": bool(status.get("ok", False)),
        "dvr": {
            "enabled": dvr.get("enabled"),
            "available": dvr.get("available"),
            "retention_hours": dvr.get("retention_hours"),
            "segment_seconds": dvr.get("segment_seconds"),
            "storage_root": dvr.get("storage_root"),
            "message": dvr.get("message"),
            "range_count": dvr_range.get("count"),
        },
        "chat": {
            "ok": chat.get("ok"),
            "offline": chat.get("offline"),
            "source": chat.get("source"),
            "answer_preview": str(chat.get("answer", ""))[:220],
        },
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
