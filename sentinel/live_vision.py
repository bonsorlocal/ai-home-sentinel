"""Cloud live vision during motion sessions (Phase 9C).

While a motion session is active, periodically send 1–2 JPEGs to a cloud
vision model and return structured door-scene JSON for the reasoner.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Callable, Dict, List, Optional

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

SceneCallback = Callable[[Dict[str, Any], object], None]


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_DOOR_PROMPT = (
    "Analyze these home security camera frames at a front door. "
    "Return strict JSON with keys: visitor_at_door (bool), "
    "activity (one of: knocking, waiting, delivering, passing, none), "
    "person_count (int), confidence (0..1 float), short_summary (string). "
    "Do not include markdown."
)


class CloudLiveVisionWorker:
    """Session-gated cloud scene analyzer."""

    def __init__(
        self,
        config: Any,
        frame_getter: Callable[[], Any],
        session_active_fn: Callable[[], bool],
        on_scene: Optional[SceneCallback] = None,
    ) -> None:
        cfg = config.get("live_vision") or {}
        brain_cfg = config.get("brain") or {}

        self._enabled = bool(cfg.get("enabled", True))
        self._interval = max(3.0, float(cfg.get("analyze_every_seconds", 10)))
        self._daily_cap = max(1, int(cfg.get("daily_call_cap", 80)))
        self._timeout = float(cfg.get("request_timeout_seconds", 20))
        self._jpeg_quality = int(cfg.get("jpeg_quality", 70))
        self._max_width = int(cfg.get("max_width", 640))
        self._min_confidence = float(cfg.get("min_confidence", 0.55))
        self._provider = str(
            cfg.get("provider", brain_cfg.get("provider", "auto"))
        ).strip().lower() or "auto"
        self._base_url = str(
            cfg.get("base_url", brain_cfg.get("base_url", "https://api.x.ai/v1"))
        ).rstrip("/")
        self._model = str(
            cfg.get("model", brain_cfg.get("vision_model", "grok-4.3"))
        )
        self._google_base_url = str(
            cfg.get(
                "google_base_url",
                brain_cfg.get(
                    "google_base_url",
                    "https://generativelanguage.googleapis.com/v1beta",
                ),
            )
        ).rstrip("/")
        self._google_model = str(
            cfg.get(
                "google_model",
                brain_cfg.get("google_vision_model", "gemini-1.5-flash"),
            )
        )
        secrets_file = str(
            cfg.get("secrets_file", brain_cfg.get("secrets_file", "secrets.yaml"))
        )
        if os.path.isabs(secrets_file):
            self._secrets_path = secrets_file
        else:
            self._secrets_path = os.path.join(_project_root(), secrets_file)
        self._api_key = self._load_key("grok_api_key", "GROK_API_KEY")
        self._google_api_key = self._load_key("google_api_key", "GOOGLE_API_KEY")

        self._frame_getter = frame_getter
        self._session_active_fn = session_active_fn
        self._on_scene = on_scene

        self._lock = threading.Lock()
        self._calls_today = 0
        self._call_date = date.today()
        self._last_scene: Optional[Dict[str, Any]] = None
        self._message = "Live vision idle."
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def is_available(self) -> bool:
        if not self._enabled or cv2 is None:
            return False
        provider = self._effective_provider()
        if provider == "google":
            return bool(self._google_api_key)
        if provider == "grok":
            return bool(self._api_key)
        return bool(self._google_api_key or self._api_key)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._roll_day()
            used = self._calls_today
            last = dict(self._last_scene) if self._last_scene else None
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "provider": self._effective_provider(),
            "running": self.is_running(),
            "analyze_every_seconds": self._interval,
            "calls_used_today": used,
            "daily_call_cap": self._daily_cap,
            "calls_remaining": max(0, self._daily_cap - used),
            "last_scene": last,
            "message": self._message,
        }

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if not self.is_available():
            self._message = "Live vision unavailable (disabled or no API key)."
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="cloud-live-vision", daemon=True
        )
        self._thread.start()
        self._message = "Live vision watching motion sessions."

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def analyze_frame(self, frame) -> Optional[Dict[str, Any]]:
        """Analyze one frame (tests / manual). Returns scene dict or None."""
        jpeg = self._encode_jpeg(frame)
        if not jpeg:
            return None
        return self._analyze_jpegs([jpeg])

    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(0.5)
            if not self._session_active_fn():
                continue
            with self._lock:
                self._roll_day()
                if self._calls_today >= self._daily_cap:
                    self._message = "Daily live-vision cap reached."
                    continue
            frame = None
            try:
                frame = self._frame_getter()
            except Exception:  # noqa: BLE001
                frame = None
            if frame is None:
                continue
            jpeg = self._encode_jpeg(frame)
            if not jpeg:
                continue
            try:
                scene = self._analyze_jpegs([jpeg])
            except Exception as error:  # noqa: BLE001
                self._message = f"Live vision error: {error}"
                print(f"[live_vision] {self._message}")
                self._stop.wait(self._interval)
                continue
            if not scene:
                self._stop.wait(self._interval)
                continue
            with self._lock:
                self._last_scene = scene
                self._message = str(scene.get("short_summary") or "Scene analyzed.")
            confidence = float(scene.get("confidence", 0) or 0)
            if confidence >= self._min_confidence and self._on_scene is not None:
                try:
                    self._on_scene(scene, frame)
                except Exception as error:  # noqa: BLE001
                    print(f"[live_vision] Scene callback error: {error}")
            self._stop.wait(self._interval)

    def _analyze_jpegs(self, jpegs: List[bytes]) -> Optional[Dict[str, Any]]:
        if not jpegs:
            return None
        with self._lock:
            self._roll_day()
            if self._calls_today >= self._daily_cap:
                raise RuntimeError("Daily live-vision cap reached.")
            self._calls_today += 1

        provider = self._effective_provider()
        if provider == "google":
            text = self._call_google(jpegs)
        else:
            text = self._call_grok(jpegs)
        parsed = self._parse_json(text)
        if parsed is None:
            raise RuntimeError("Could not parse live-vision JSON.")
        return self._normalize_scene(parsed)

    def _call_grok(self, jpegs: List[bytes]) -> str:
        if not self._api_key:
            raise RuntimeError("No Grok API key.")
        content: List[Dict[str, Any]] = [{"type": "text", "text": _DOOR_PROMPT}]
        for jpeg in jpegs:
            b64 = base64.b64encode(jpeg).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 250,
            "temperature": 0.2,
            "stream": False,
        }
        data = self._post_json(
            f"{self._base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            payload=payload,
        )
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"Bad Grok response: {error}") from error

    def _call_google(self, jpegs: List[bytes]) -> str:
        if not self._google_api_key:
            raise RuntimeError("No Google API key.")
        parts: List[Dict[str, Any]] = [{"text": _DOOR_PROMPT}]
        for jpeg in jpegs:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    }
                }
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250},
        }
        url = (
            f"{self._google_base_url}/models/{self._google_model}:generateContent"
            f"?key={self._google_api_key}"
        )
        data = self._post_json(
            url,
            headers={"Content-Type": "application/json"},
            payload=payload,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(p.get("text", "")) for p in parts).strip()
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"Bad Google response: {error}") from error

    def _encode_jpeg(self, frame) -> Optional[bytes]:
        if frame is None or cv2 is None:
            return None
        try:
            img = frame
            height, width = img.shape[:2]
            if width > self._max_width > 0:
                scale = self._max_width / float(width)
                img = cv2.resize(
                    img, (self._max_width, max(1, int(height * scale)))
                )
            ok, encoded = cv2.imencode(
                ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if not ok:
                return None
            return encoded.tobytes()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _normalize_scene(raw: Dict[str, Any]) -> Dict[str, Any]:
        activity = str(raw.get("activity", "none")).strip().lower()
        allowed = {"knocking", "waiting", "delivering", "passing", "none"}
        if activity not in allowed:
            activity = "none"
        try:
            confidence = float(raw.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        try:
            person_count = int(raw.get("person_count", 0) or 0)
        except (TypeError, ValueError):
            person_count = 0
        visitor = bool(raw.get("visitor_at_door", False))
        if person_count > 0 and activity in ("knocking", "waiting", "delivering"):
            visitor = True
        return {
            "visitor_at_door": visitor,
            "activity": activity,
            "person_count": max(0, person_count),
            "confidence": round(confidence, 3),
            "short_summary": str(raw.get("short_summary", "")).strip()[:240],
        }

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _effective_provider(self) -> str:
        if self._provider == "auto":
            if self._google_api_key:
                return "google"
            return "grok"
        return self._provider

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._call_date:
            self._call_date = today
            self._calls_today = 0

    def _load_key(self, *names: str) -> str:
        if yaml is None or not os.path.exists(self._secrets_path):
            return ""
        try:
            with open(self._secrets_path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:  # noqa: BLE001
            return ""
        if not isinstance(data, dict):
            return ""
        for name in names:
            value = data.get(name)
            if value:
                return str(value).strip()
        return ""

    def _post_json(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:180]
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Network error: {error}") from error
