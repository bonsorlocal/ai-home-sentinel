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

from sentinel.brain import Brain, classify_query_mode, classify_query_source  # noqa: E402
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
    assert "general assistant chat" in second["answer"].lower()
    # Guardrails can answer locally before cloud calls when evidence is missing.
    assert calls["n"] <= 1
    assert brain.status()["calls_remaining"] in (0, 1)


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
    answer = result["answer"].lower()
    assert "general assistant chat" in answer


def test_ask_retries_transient_network_error(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    calls = {"n": 0}

    def flaky_post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary network hiccup")
        return _ok_response("Recovered after retry.")

    brain = Brain(
        _make_config(request_retries=1),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=flaky_post,
    )
    result = brain.ask("hello sentinel")
    assert result["ok"] is True
    assert result["offline"] is False
    assert result["answer"] == "Recovered after retry."
    assert calls["n"] == 2


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
    answer = result["answer"].lower()
    assert "general assistant chat" in answer


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
    assert classify_query_source("what just happened? i stepped out for a second") == "both"
    assert classify_query_source("what happened in the last 2 minutes?") == "both"
    assert classify_query_source("What time did motion happen?") == "ledger"
    assert classify_query_source("When did the person arrive yesterday?") == "ledger"
    assert (
        classify_query_source("What time did the person I see now first appear?")
        == "both"
    )


def test_classify_query_mode_routes_general_by_default():
    assert classify_query_mode("What happened near the driveway?") == "footage"
    assert classify_query_mode("can you help me plan dinner this week?") == "casual"
    assert classify_query_mode("hello there") == "casual"
    assert classify_query_mode("hi, what time was that clip from?") == "hybrid"


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
    assert result.get("mode") == "footage"
    assert result.get("path") in ("cloud_primary", "cloud_fallback_model")
    user = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user, list)


def test_ask_uses_local_recent_recap_for_just_happened(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="face",
        title="Known face: Jordan",
        summary="Jordan walked up to the camera and waved.",
        entities={"face": {"known": True, "name": "Jordan"}},
    )

    def should_not_call(*_args, **_kwargs):
        raise AssertionError("Cloud call should not be used for immediate recap")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=should_not_call,
    )
    result = brain.ask("what just happened? i stepped out for a second")
    assert result["ok"] is True
    assert result["path"] == "local_recent_recap"
    answer = result["answer"].lower()
    if "jordan" in answer:
        assert "waved" in answer
    else:
        assert "don't have a new event" in answer


def test_recent_window_recap_reports_nearest_activity_when_window_empty(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="motion",
        title="Motion detected",
        summary="Driveway motion",
        timestamp="2026-01-01T00:00:00",
    )

    def should_not_call(*_args, **_kwargs):
        raise AssertionError("Cloud call should not be used for immediate recap windows")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=should_not_call,
    )
    result = brain.ask("what happened in the last 2 minutes?")
    assert result["ok"] is True
    assert result["path"] == "local_recent_recap"
    answer = result["answer"].lower()
    assert "last 2 minutes" in answer
    assert "most recent activity was" in answer
    assert "driveway motion" in answer


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
    assert result.get("mode") == "footage"
    assert result.get("path") in ("cloud_primary", "cloud_fallback_model")
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
    assert result.get("path") == "local_evidence_fallback"


def test_reasoning_response_includes_confidence_fields(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(source="motion", title="Motion detected", summary="driveway movement")

    def fake_post(_url, _headers, _payload, _timeout):
        return _ok_response("Motion was seen near the driveway.")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    result = brain.ask("What happened near the driveway?")
    assert result["ok"] is True
    assert result.get("confidence") in ("low", "medium", "high")
    assert isinstance(result.get("evidence_count"), int)
    assert isinstance(result.get("evidence_flags"), list)


def test_reasoning_flags_time_window_ambiguity(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")

    def dvr_context(_question, _use_heavy):
        return {
            "context": "- segment#1 [2026-01-01T10:00:00 -> 2026-01-01T10:01:00] person_count=1 labels=person summary=person near door",
            "analysis_context": "",
            "window_start": "",
            "window_end": "",
            "window_source": "",
            "segment_count": 1,
        }

    def fake_post(_url, _headers, _payload, _timeout):
        return _ok_response("A person appeared near the door.")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
        dvr_context_fn=dvr_context,
    )
    result = brain.ask("When did someone come by?")
    assert result["ok"] is True
    assert "time_window_ambiguous" in result.get("evidence_flags", [])


def test_reasoning_flags_conflicting_identity_signals(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(
        source="face",
        title="Known face: Jordan",
        summary="known faces: Jordan",
        entities={"faces": [{"name": "Jordan", "known": True}]},
    )

    def dvr_context(_question, _use_heavy):
        return {
            "context": "- segment#9 [2026-01-01T10:00:00 -> 2026-01-01T10:01:00] person_count=1 labels=person summary=unknown person near door",
            "analysis_context": "",
            "window_start": "2026-01-01T10:00:00",
            "window_end": "2026-01-01T10:20:00",
            "window_source": "explicit",
            "segment_count": 1,
        }

    def fake_post(_url, _headers, _payload, _timeout):
        return _ok_response("I found mixed identity evidence.")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
        dvr_context_fn=dvr_context,
    )
    result = brain.ask("Who was it?")
    assert result["ok"] is True
    assert "conflicting_identity_signals" in result.get("evidence_flags", [])


def test_capture_feedback_without_context_fails(tmp_path):
    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(tmp_path / "missing-secrets.yaml"),
    )
    result = brain.capture_feedback({})
    assert result["ok"] is False


def test_casual_query_uses_conversation_mode(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Hey! I'm doing well.")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    result = brain.ask("hello sentinel")
    assert result["ok"] is True
    assert result["mode"] == "casual"
    assert result["source"] == "conversation"
    assert result["path"] in ("cloud_primary", "cloud_fallback_model")
    user_message = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user_message, str)
    assert "Recent event notes" not in user_message


def test_recipe_query_uses_general_assistant_lane(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Yes. Share your ingredients and I'll suggest a dinner plan.")

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    result = brain.ask("If I tell you the ingredients in my fridge can you help with dinner?")
    assert result["ok"] is True
    assert result["mode"] == "casual"
    assert result["source"] == "conversation"
    assert result["path"] in ("cloud_primary", "cloud_fallback_model")
    user_message = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user_message, str)
    assert "Recent event notes" not in user_message
    assert "Continuous DVR retrieval context" not in user_message


def test_offline_general_query_uses_general_fallback_text(tmp_path):
    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(tmp_path / "missing-secrets.yaml"),
    )
    result = brain.ask("I have eggs, spinach, and rice. What can I cook for dinner?")
    assert result["ok"] is True
    assert result["offline"] is True
    assert result["mode"] == "casual"
    assert result["source"] == "conversation"
    assert result["path"] == "local_casual_fallback"
    answer = result["answer"].lower()
    assert "general assistant chat" in answer
    assert "local evidence" not in answer


def test_hybrid_query_keeps_evidence_context(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    ledger.insert(source="motion", title="Motion detected", summary="kitchen movement")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return _ok_response("Motion was around 9:10 PM. For dinner, try veggie fried rice.")

    brain = Brain(
        _make_config(),
        ledger,
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    result = brain.ask("Hi, what time was that kitchen motion and what should I cook with eggs?")
    assert result["ok"] is True
    assert result["mode"] == "hybrid"
    assert result["path"] in ("cloud_primary", "cloud_fallback_model")
    user_message = captured["payload"]["messages"][-1]["content"]
    assert isinstance(user_message, str)
    assert "Recent event notes" in user_message


def test_owner_enrollment_from_natural_language(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('grok_api_key: "xai-test-key"\n', encoding="utf-8")
    captured = {}

    def enroll(payload):
        captured["payload"] = payload
        return {"ok": True, "message": "enrolled"}

    brain = Brain(
        _make_config(),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        owner_enroll_fn=enroll,
    )
    result = brain.ask("I'm Jordan, make me the owner admin. I have tattoos and I'm 6 ft.")
    assert result["ok"] is True
    assert result["path"] == "owner_enrollment"
    assert captured["payload"]["name"] == "Jordan"
    assert captured["payload"]["appearance_signature"]["tattoos"] is True


def test_google_provider_uses_generate_content_endpoint(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('google_api_key: "google-test-key"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "Hello from Gemini."}]}}]}

    brain = Brain(
        _make_config(provider="auto"),
        _ledger(tmp_path),
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    result = brain.ask("hello sentinel")
    assert result["ok"] is True
    assert result["answer"] == "Hello from Gemini."
    assert ":generateContent?key=google-test-key" in captured["url"]
