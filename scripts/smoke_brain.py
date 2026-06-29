#!/usr/bin/env python3
"""Quick smoke test for the B1 brain endpoints (run on the Pi)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:5000{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    status = json.loads(
        urllib.request.urlopen("http://localhost:5000/status", timeout=10).read()
    )
    print("brain_active:", status.get("brain_active"))
    print("brain:", json.dumps(status.get("brain"), indent=2))

    chat = post("/api/chat", {"question": "Was there motion today? One short sentence."})
    print("chat:", json.dumps(chat, indent=2))
    if not chat.get("ok"):
        return 1

    summary = post("/api/summary")
    print("summary:", json.dumps(summary, indent=2)[:500])
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(2)
