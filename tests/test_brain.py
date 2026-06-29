"""Tests for the language brain (B1).

These run completely offline: the Grok HTTP call is replaced with a fake, so no
real API key or network is ever used. They check that:

1. With no API key the brain reports it is offline and never calls the network.
2. With a key and a mocked Grok, a question gets an answer and the daily counter
   moves.
3. The strict daily cap stops further calls once it is reached.
4. Network/HTTP problems degrade gracefully (no exceptions escape).
5. Recent events are fed into the prompt context.

Run them with:  python -m pytest
"""

from __future__ import annotations

import io
import os
import sys
import urllib.error

# Make sure the project root is importable when running pytest from anywhere.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.brain import Brain  # noqa: E402
from sentinel.config import Config  # noqa: E402
from sentinel.events import EventLedger  # noqa: E402


def _make_config(**overrides):
    brain = {
        "enabled": True,
        "base_url": "https://api.x.ai/v1",
        "fast_model": "grok-4.3",
        "smart_model": "grok-smart",
        "daily_call_cap": 50,
        "request_timeout_seconds": 5,
        "max_events_in_context": 40,
    }
    brain.update(overrides)
    return Config({"brain": brain})


def _ledger(tmp_path):
    return EventLedger(str(tmp_path / "test.db"))


def _ok_response(content="mocked answer"):
    return {"choices": [{"message": {"content": content}}]}


def test_offline_without_key(tmp_path):
    """No secrets file -> brain is offline and never hits the network."""

    def boom(*_args, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("network should not be called without a key")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(tmp_path / "missing-secrets.yaml"),
        http_post=boom,
    )

    assert brain.is_available() is False
    result = brain.ask("what happened?")
    assert result["ok"] is False
    assert result["offline"] is True
    assert "no Grok API key" in result["answer"]


def test_ask_returns_answer_with_mock(tmp_path):
    """With a key + mocked Grok, ask() returns the answer and counts the call."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["auth"] = headers.get("Authorization")
        return _ok_response("All quiet today.")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    assert brain.is_available() is True
    result = brain.ask("anything happen?")
    assert result["ok"] is True
    assert result["answer"] == "All quiet today."
    assert result["offline"] is False
    # The call was counted against the daily cap.
    assert brain.status()["calls_used_today"] == 1
    # The request went to the right endpoint with the bearer key (not leaked here).
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer xai-test-key"


def test_daily_cap_blocks_further_calls(tmp_path):
    """Once the cap is hit, the brain declines without calling the network."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    calls = {"n": 0}

    def fake_post(url, headers, payload, timeout):
        calls["n"] += 1
        return _ok_response()

    brain = Brain(
        _make_config(daily_call_cap=1),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    first = brain.ask("q1")
    assert first["ok"] is True
    second = brain.ask("q2")
    assert second["ok"] is False
    assert "limit reached" in second["answer"].lower()
    # The network was used only for the first question.
    assert calls["n"] == 1
    assert brain.status()["calls_remaining"] == 0


def test_network_error_is_graceful(tmp_path):
    """A URLError (offline) is reported, not raised."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    def fake_post(*_args, **_kwargs):
        raise urllib.error.URLError("network down")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    result = brain.ask("hello?")
    assert result["ok"] is False
    assert result["offline"] is True
    assert "offline" in result["answer"].lower()


def test_http_error_is_graceful(tmp_path):
    """An HTTP error (e.g. bad key) is reported with a short message."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-bad-key"\n', encoding="utf-8")

    def fake_post(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url="https://api.x.ai/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": {"message": "Invalid API key"}}'),
        )

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    result = brain.ask("hi")
    assert result["ok"] is False
    assert "401" in result["answer"]


def test_context_includes_recent_events(tmp_path):
    """Recent events should be passed into the prompt sent to Grok."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    ledger = _ledger(tmp_path)
    ledger.insert(source="object", title="Detected: person", summary="person (91%)")

    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response()

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    brain.ask("who did you see?")
    user_message = captured["payload"]["messages"][-1]["content"]
    assert "Detected: person" in user_message


def test_summary_uses_smart_model(tmp_path):
    """The daily digest should use the configured smart model."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["model"] = payload["model"]
        return _ok_response("Quiet day overall.")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )

    result = brain.summarize_day()
    assert result["ok"] is True
    assert captured["model"] == "grok-smart"
