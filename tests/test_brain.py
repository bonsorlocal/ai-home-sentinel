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

from sentinel.brain import Brain, classify_query_source  # noqa: E402
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
    assert result["ok"] is True
    assert result["offline"] is True
    assert "local evidence" in result["answer"].lower()


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
    assert second["ok"] is True
    assert second["offline"] is True
    assert "local evidence" in second["answer"].lower()
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
    assert result["ok"] is True
    assert result["offline"] is True
    assert "local evidence" in result["answer"].lower()


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
    assert result["ok"] is True
    assert result["offline"] is True
    assert "local evidence" in result["answer"].lower()


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


def test_context_prefers_clip_metadata_when_present(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="object",
        title="Detected: person",
        summary="person (91%)",
        clip_path="/tmp/clip.mp4",
        entities={
            "clip_status": "saved",
            "clip_analysis": {
                "scene_summary": "A person approached the front door.",
                "actors": ["person"],
            }
        },
    )

    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("ok")

    brain = Brain(
        _make_config(include_clip_metadata_in_context=True),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    brain.ask("what happened?")
    user_message = captured["payload"]["messages"][-1]["content"]
    assert "clip_summary: A person approached the front door." in user_message
    assert "[clip saved]" in user_message


def test_classify_live_vs_ledger():
    assert classify_query_source("I'm Jordan, remember me") == "live"
    assert classify_query_source("What do you see right now?") == "live"
    assert classify_query_source("tell me what the scene is right now") == "live"
    assert classify_query_source("What time did motion happen?") == "ledger"
    assert classify_query_source("When did the person arrive yesterday?") == "ledger"
    assert (
        classify_query_source("What time did the person I see now first appear?")
        == "both"
    )


def test_ask_live_attaches_image(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Hi Jordan, I see you on camera.")

    import numpy as np

    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
        frame_getter=lambda: frame,
        camera_active_fn=lambda: True,
    )
    result = brain.ask("I'm Jordan, remember me")
    assert result["ok"] is True
    assert result.get("source") == "live"
    user = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user, list)
    assert user[0]["type"] == "text"
    assert user[1]["type"] == "image_url"
    assert captured["payload"]["model"] == "grok-4.3"


def test_ask_ledger_question_uses_text_only(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(source="motion", title="Motion detected", summary="area=900")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Around 11:43 this morning.")

    import numpy as np

    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
        frame_getter=lambda: frame,
        camera_active_fn=lambda: True,
    )
    result = brain.ask("What time did motion happen today?")
    assert result["ok"] is True
    assert result.get("source") == "ledger"
    user = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user, str)
    assert "Motion detected" in user
    assert "image_url" not in user


def test_session_context_is_default(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="object",
        title="Detected: person",
        summary="Session lasted 9.0s; objects: person",
        entities={
            "session": {"duration_seconds": 9.0, "peak_area": 2100, "motion_updates": 7},
            "detections": [{"label": "person", "confidence": 0.93}],
        },
    )
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("A person was seen.")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    brain.ask("What happened recently?")
    user = captured["payload"]["messages"][-1]["content"]
    assert "Session lasted 9.0s" in user
    assert "peak_area: 2100" in user


def test_raw_timeline_when_explicitly_requested(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="motion",
        title="Motion session",
        summary="Session lasted 6.0s",
        entities={"session": {"duration_seconds": 6.0, "peak_area": 1200}},
    )
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Here is the detailed timeline.")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    brain.ask("Show the full timeline with detailed log please")
    user = captured["payload"]["messages"][-1]["content"]
    assert "peak_area:" not in user
    assert "Motion session: Session lasted 6.0s" in user


def test_ask_includes_dvr_fallback_context(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Based on DVR evidence, the person carried the laptop away.")

    def dvr_context(_question, _use_heavy):
        return {
            "context": "- segment#12 [2026-07-01T21:10:00 -> 2026-07-01T21:11:00] person_count=1 labels=person,laptop motion_score=0.42 summary=person unplugged laptop",
            "analysis_context": "- segment#12 [2026-07-01T21:10:00 -> 2026-07-01T21:11:00] scene=person unplugged TV laptop and carried it out actors=['person'] actions=['unplug', 'carry']",
        }

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
        dvr_context_fn=dvr_context,
    )
    result = brain.ask("did he steal the laptop?")
    assert result["ok"] is True
    user = captured["payload"]["messages"][-1]["content"]
    assert "Continuous DVR retrieval context" in user
    assert "segment#12" in user


def test_ask_uses_local_fallback_when_grok_unreachable(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="object",
        title="Detected: person",
        summary="Person carried a package toward the driveway.",
    )

    def failing_post(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=failing_post,
    )
    result = brain.ask("Who was outside?")
    assert result["ok"] is True
    assert result["offline"] is True
    assert "local evidence" in result["answer"].lower()
    assert "Detected: person" in result["answer"]


def test_ask_local_fallback_mentions_window(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    def failing_post(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    def dvr_context(_question, _use_heavy):
        return {
            "context": "- segment#4 [2026-07-01T21:10:00 -> 2026-07-01T21:11:00] person_count=1 labels=person summary=person carrying a backpack",
            "analysis_context": "",
            "window_start": "2026-07-01T20:00:00",
            "window_end": "2026-07-01T22:00:00",
            "window_source": "relative_recent",
            "segment_count": 1,
        }

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=failing_post,
        dvr_context_fn=dvr_context,
    )
    result = brain.ask("Did anyone come by in the last 2 hours?")
    assert result["ok"] is True
    assert "2026-07-01T20:00:00" in result["answer"]
    assert "segment#4" in result["answer"]
