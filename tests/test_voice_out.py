"""Tests for CameraVoice (Phase 9E)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.voice_out import CameraVoice  # noqa: E402


def test_disabled_voice_logs_line_without_playing():
    voice = CameraVoice(
        Config({"voice_out": {"enabled": False, "max_chars": 100}}),
        synthesize_fn=lambda text: b"fake",
    )
    result = voice.speak("Hello at the door")
    assert result["ok"] is True
    assert result["spoken"] is False
    assert "Hello at the door" in result["text"]


def test_missing_synthesizer_fails_when_enabled():
    voice = CameraVoice(
        Config({"voice_out": {"enabled": True}}),
        synthesize_fn=None,
    )
    result = voice.speak("Hi")
    assert result["ok"] is False
