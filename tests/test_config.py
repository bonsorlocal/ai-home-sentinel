"""Tests for the configuration loader.

These check that:
1. Loading the real config.yaml returns the expected sections.
2. Missing keys fall back to safe defaults instead of crashing.
3. A partial config still gets merged on top of the defaults.

Run them with:  python -m pytest   (or simply: pytest)
"""

from __future__ import annotations

import os
import sys

# Make sure the project root is importable when running pytest from anywhere.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config, DEFAULT_CONFIG, load_config  # noqa: E402


def test_load_real_config_has_expected_sections():
    """The real config.yaml should load and contain the main sections."""
    config = load_config()
    for section in (
        "camera",
        "motion",
        "detector",
        "face_recognition",
        "storage",
        "clips",
        "dvr",
        "dashboard",
        "video_metadata",
        "voice",
        "performance",
    ):
        assert config.get(section) is not None, f"missing section: {section}"


def test_dashboard_defaults_present():
    """Dashboard host and port should be readable (from file or defaults)."""
    config = load_config()
    assert config.get("dashboard", "port") is not None
    assert config.get("dashboard", "host") is not None


def test_missing_key_returns_default():
    """Asking for a key that does not exist returns the provided default."""
    config = load_config()
    assert config.get("dashboard", "does_not_exist", default="fallback") == "fallback"
    assert config.get("no_such_section", "x", default=42) == 42


def test_partial_config_is_merged_over_defaults(tmp_path):
    """A config file with only one value keeps all other defaults."""
    partial = tmp_path / "config.yaml"
    partial.write_text("dashboard:\n  port: 1234\n", encoding="utf-8")

    config = load_config(path=str(partial))

    # The overridden value is used...
    assert config.get("dashboard", "port") == 1234
    # ...while untouched values still come from the defaults.
    assert config.get("dashboard", "host") == DEFAULT_CONFIG["dashboard"]["host"]
    assert config.get("camera", "target_fps") == DEFAULT_CONFIG["camera"]["target_fps"]


def test_voice_defaults_present():
    """Voice settings should load with spoken replies enabled by default."""
    config = load_config()
    assert config.get("voice", "enabled") is True
    assert config.get("voice", "speak_text_queries") is False
    assert config.get("voice", "wake_word_enabled") is False
    assert config.get("voice", "language") == "en-US"


def test_missing_file_uses_defaults(tmp_path):
    """If the file does not exist, defaults are used and nothing crashes."""
    missing = tmp_path / "nope.yaml"
    config = load_config(path=str(missing))
    assert isinstance(config, Config)
    assert config.get("dashboard", "port") == DEFAULT_CONFIG["dashboard"]["port"]
