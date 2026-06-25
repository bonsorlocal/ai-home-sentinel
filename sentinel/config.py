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
        "width": 1280,
        "height": 720,
        "ai_width": 640,
        "ai_height": 360,
        "target_fps": 20,
    },
    "motion": {
        "enabled": True,
        "min_area": 1200,
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
        "save_snapshot_on_motion": True,
        "save_snapshot_on_person": True,
    },
    "dashboard": {
        "host": "0.0.0.0",
        "port": 5000,
        "require_token": False,
        "token": "",
    },
    "performance": {
        "ai_queue_size": 1,
        "drop_old_frames": True,
        "log_fps_every_seconds": 10,
        "max_cpu_temp_celsius": 75,
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
