#!/usr/bin/env python3
"""Probe Google Generative Language API key without printing the secret."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_key() -> str:
    path = Path("/home/sentinel/ai-home-sentinel/secrets.yaml")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        key = str(data.get("google_api_key") or "").strip()
        if key:
            return key
    except Exception:
        pass
    for line in text.splitlines():
        if line.strip().startswith("google_api_key:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def main() -> int:
    key = load_key()
    if not key:
        print("RESULT=missing_key")
        return 2
    print(f"RESULT=key_present length={len(key)} prefix={key[:4]}...")

    list_url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + key
    try:
        with urllib.request.urlopen(list_url, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"RESULT=list_http_{exc.code}")
        print(body)
        return 1
    except Exception as exc:  # noqa: BLE001
        print("RESULT=list_error", type(exc).__name__, exc)
        return 1

    models = [str(m.get("name") or "") for m in data.get("models", [])]
    flash = [m for m in models if "flash" in m.lower()]
    print(f"RESULT=list_ok model_count={len(models)}")
    print("FLASH_MODELS=" + ",".join(flash[:8]))

    model = "gemini-3.8-flash"
    if not any(model in m for m in models):
        for name in flash:
            if "gemini" in name.lower():
                model = name.split("/")[-1]
                break

    gen_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    payload = json.dumps(
        {"contents": [{"parts": [{"text": "Reply with exactly: GOOGLE_OK"}]}]}
    ).encode()
    req = urllib.request.Request(
        gen_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"RESULT=generate_http_{exc.code} model={model}")
        print(body)
        return 1
    except Exception as exc:  # noqa: BLE001
        print("RESULT=generate_error", type(exc).__name__, exc)
        return 1

    text = ""
    for candidate in data.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            text += str(part.get("text") or "")
    print(f"RESULT=generate_ok model={model}")
    print("REPLY=" + text.strip()[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
