"""Minimal Gemini chat shim for self-hosted Emergent apps on Pi."""

from __future__ import annotations

import httpx


class UserMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class LlmChat:
    def __init__(self, api_key: str, session_id: str, system_message: str) -> None:
        self._api_key = api_key
        self._session_id = session_id
        self._system_message = system_message
        self._provider = "gemini"
        self._model = "gemini-1.5-flash"

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self._provider = provider
        self._model = model
        return self

    async def send_message(self, message: UserMessage) -> str:
        if self._provider != "gemini":
            return f"Unsupported provider: {self._provider}"
        model = self._model
        if model == "gemini-3.1-pro-preview":
            model = "gemini-1.5-pro"
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self._api_key}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{self._system_message}\n\n{message.text}"}],
                }
            ]
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return "No response from Gemini."
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts).strip()
        return text or "Empty response from Gemini."
