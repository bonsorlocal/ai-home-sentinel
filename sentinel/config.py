"""Configuration loader for AI Home Sentinel.

This module reads ``config.yaml`` and gives the rest of the program an easy,
safe way to ask for settings. It is written defensively: if the file is
missing, or a setting is left out, sensible default values are used instead
so the program never crashes just because of a small config mistake.

Typical use:

    from sentinel.config import load_config

    config = load_config()              # reads config.yaml from the project root
    port = config.get("dashboard", "port", default=5000)
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict

try:
    import yaml
except ImportError as exc:  # pragma: no cover - handled with a clear message
    raise ImportError(
        "PyYAML is not installed. Run 'pip install -r requirements.txt' first."
    ) from exc


# The default settings. These match config.yaml and act as a safety net so a
# missing key never breaks the program.
DEFAULT_CONFIG: Dict[str, Any] = {
    "camera": {
        "type": "auto",
        "index": None,
        "startup_wait_seconds": 15,
        "width": 1280,
        "height": 720,
        "ai_width": 640,
        "ai_height": 360,
        "target_fps": 15,
        "stream_width": 800,
        "stream_height": 450,
        "stream_fps": 8,
        "stream_jpeg_quality": 70,
    },
    "motion": {
        "enabled": True,
        "min_area": 1200,
        "start_min_area": 1200,
        "start_consecutive_frames": 2,
        "candidate_window_seconds": 1.5,
        "end_still_seconds": 3.0,
        "end_grace_seconds": 2.0,
        "min_session_seconds": 4.0,
        "max_event_seconds": 90.0,
        "session_update_every_seconds": 1.0,
        "person_missing_seconds": 10.0,
        "person_still_seconds": 15.0,
        "person_presence_stale_seconds": 4.0,
        "person_still_bbox_quiet_seconds": 2.0,
        "still_bbox_center_drift_px": 25.0,
        "still_bbox_area_change_ratio": 0.2,
        "still_motion_area_ratio": 0.35,
        "global_motion_ratio_max": 0.65,
        "max_noise_contours": 20,
        "brightness_jump_threshold": 38.0,
        "cooldown_seconds": 5,
        "background_learning_rate": 0.02,
    },
    "detector": {
        "enabled": True,
        "backend": "ultralytics",
        "model_path": "models/yolo_nano.pt",
        "confidence": 0.45,
        "run_every_n_seconds": 1.0,
        "classes": [
            "person",
            "dog",
            "cat",
            "car",
            "bicycle",
            "backpack",
            "suitcase",
        ],
    },
    "face_recognition": {
        "enabled": False,
        "known_faces_dir": "data/known_faces",
        "unknown_faces_dir": "data/events/unknown_faces",
        "tolerance": 0.5,
        "run_only_when_person_detected": True,
    },
    "storage": {
        "event_dir": "data/events",
        "snapshot_dir": "data/events/snapshots",
        "database_path": "data/sentinel.db",
        "max_snapshots_per_day": 500,
        "save_snapshot_on_motion": False,
        "save_snapshot_on_person": False,
        "save_snapshot_on_tier2": True,
    },
    "clips": {
        "enabled": False,
        "clip_dir": "data/events/clips",
        "save_on_motion": True,
        "save_on_detection": True,
        "min_seconds_between_clips": 15,
        "prefer_h264": True,
        "pre_roll_seconds": 3,
        "post_roll_seconds": 5,
        "record_fps": 10,
        "record_width": 640,
        "record_height": 360,
        "max_session_seconds": 90,
        "min_session_seconds_for_clip": 8,
        "max_duration_seconds": 60,
        "max_clips_per_day": 100,
        "prune_when_full": True,
    },
    "dvr": {
        "enabled": True,
        "storage_root": "data/dvr",
        "fallback_storage_root": "",
        "retention_hours": 48,
        "segment_seconds": 3600,
        "align_to_clock_hours": True,
        "auto_summarize": True,
        "auto_summarize_cloud": True,
        "segment_summary_daily_cap": 24,
        "record_fps": 10,
        "record_width": 640,
        "record_height": 360,
        "prefer_h264": True,
        "max_query_segments": 12,
        "max_analysis_segments_per_query": 2,
    },
    "dashboard": {
        "host": "0.0.0.0",
        "port": 5000,
        "require_token": False,
        "token": "",
        "secrets_file": "secrets.yaml",
    },
    "brain": {
        "enabled": True,
        "provider": "auto",
        "base_url": "https://api.x.ai/v1",
        "google_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "chat_model": "grok-4.3",
        "google_chat_model": "gemini-1.5-flash",
        "google_smart_model": "gemini-1.5-pro",
        "google_vision_model": "gemini-1.5-flash",
        "fast_model": "grok-4.3",
        "smart_model": "grok-4.3",
        "fallback_model": "grok-4.3",
        "fallback_on_cloud_error": True,
        "fast_reasoning_effort": "medium",
        "smart_reasoning_effort": "high",
        "daily_call_cap": 50,
        "request_timeout_seconds": 30,
        "request_retries": 1,
        "max_events_in_context": 40,
        "max_answer_tokens": 500,
        "max_casual_answer_tokens": 220,
        "include_clip_metadata_in_context": True,
        "live_vision_enabled": True,
        "vision_model": "grok-4.3",
        "live_jpeg_quality": 70,
        "live_max_width": 640,
        "strict_evidence_guardrails": True,
        "local_casual_fallback": True,
        "memory_enabled": True,
        "memory_max_entries": 300,
        "memory_max_context_items": 8,
        "secrets_file": "secrets.yaml",
    },
    "video_metadata": {
        "enabled": False,
        "provider": "auto",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "google_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "google_model": "gemini-1.5-flash",
        "max_keyframes": 3,
        "daily_call_cap": 30,
        "request_timeout_seconds": 20,
        "secrets_file": "secrets.yaml",
    },
    "reasoner": {
        "enabled": True,
        "night_hour_start": 22,
        "night_hour_end": 6,
        "repeat_person_window_minutes": 5,
        "repeat_person_threshold": 3,
        "motion_precedes_seconds": 90,
    },
    "notifications": {
        "enabled": False,
        "provider": "ntfy",
        "topic": "",
        "cooldown_seconds": 60,
        "notify_on_tier": 2,
        "request_timeout_seconds": 10,
        "secrets_file": "secrets.yaml",
    },
    "voice": {
        "enabled": True,
        "tts_provider": "auto",
        "speak_text_queries": False,
        "wake_word_enabled": False,
        "wake_word": "hey sentinel",
        "language": "en-US",
    },
    "google": {
        "project_id": "",
        "credentials_file": "secrets/gcp-service-account.json",
        "gcs_bucket": "",
        "secrets_file": "secrets.yaml",
        "video_intelligence": {
            "enabled": True,
            "fallback_only": True,
            "daily_job_cap": 3,
            "max_upload_mb": 80,
            "inline_max_mb": 9,
            "request_timeout_seconds": 120,
            "poll_interval_seconds": 3,
        },
        "text_to_speech": {
            "enabled": True,
            "voice_name": "en-US-Neural2-F",
            "language_code": "en-US",
            "speaking_rate": 1.0,
            "daily_char_cap": 20000,
            "request_timeout_seconds": 20,
        },
    },
    "performance": {
        "ai_queue_size": 1,
        "drop_old_frames": True,
        "log_fps_every_seconds": 10,
        "max_cpu_temp_celsius": 75,
    },
    "household": {
        "enabled": True,
        "max_residents": 8,
        "mobile_pwa_enabled": False,
        "telephony_enabled": False,
    },
    "emergent_app": {
        "enabled": False,
        "url": "",
        "label": "Emergent App",
    },
    "pi_bridge": {
        "enabled": False,
        "base_url": "http://127.0.0.1:8080",
        "heartbeat_seconds": 10,
        "camera_sync_seconds": 60,
        "post_events": True,
        "post_dvr_segments": True,
        "request_timeout_seconds": 3,
    },
}


def _project_root() -> str:
    """Return the project root folder (the folder that contains config.yaml)."""
    # This file lives in <root>/sentinel/config.py, so the root is one level up.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``override`` on top of ``base`` without losing default keys.

    Any key present in ``override`` replaces the one in ``base``. Nested
    dictionaries are merged section by section so a user only needs to set the
    values they care about; everything else keeps its default.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """A small wrapper around the loaded settings dictionary.

    It offers a friendly ``get`` method so the rest of the code can read
    settings without worrying about missing keys.
    """

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, section: str, key: str | None = None, default: Any = None) -> Any:
        """Read a setting.

        - ``config.get("dashboard")`` returns the whole dashboard section.
        - ``config.get("dashboard", "port")`` returns just the port.
        - ``default`` is returned if the section/key is missing.
        """
        section_data = self._data.get(section)
        if section_data is None:
            return default
        if key is None:
            return section_data
        if isinstance(section_data, dict):
            return section_data.get(key, default)
        return default

    def as_dict(self) -> Dict[str, Any]:
        """Return a copy of all settings as a plain dictionary."""
        return copy.deepcopy(self._data)


def load_config(path: str | None = None) -> Config:
    """Load settings from ``config.yaml`` merged on top of the defaults.

    If the file does not exist or cannot be read, the defaults are used and a
    warning is printed, so the program still starts.
    """
    if path is None:
        path = os.path.join(_project_root(), "config.yaml")

    file_data: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
            if isinstance(loaded, dict):
                file_data = loaded
            else:
                print(
                    f"[config] Warning: {path} did not contain settings; "
                    "using defaults."
                )
        except Exception as error:  # noqa: BLE001 - keep the app alive
            print(f"[config] Warning: could not read {path}: {error}. Using defaults.")
    else:
        print(f"[config] Warning: {path} not found. Using built-in defaults.")

    merged = _deep_merge(DEFAULT_CONFIG, file_data)
    return Config(merged)
