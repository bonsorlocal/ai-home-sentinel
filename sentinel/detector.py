"""Object detection module (Phase 4 + 9B cloud/auto backends).

YOLO-based object detector behind a clean interface. Runs only when motion is
active or on a low fixed interval. Backend modes:
- ``ultralytics`` — local YOLO
- ``cloud`` — Grok/Gemini vision labels
- ``auto`` — local until ResourceGuard offloads to cloud
- ``off`` — disabled
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

from sentinel.config import Config
from sentinel.frame_store import FrameStore
from sentinel.motion import MotionDetector

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

DetectionCallback = Callable[[List[Dict[str, Any]], object], None]
ModeFn = Callable[[], str]


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ObjectDetector:
    """Motion-triggered object detector (local YOLO and/or cloud)."""

    def __init__(
        self,
        config: Config,
        frame_store: FrameStore,
        motion: MotionDetector,
        on_detection: Optional[DetectionCallback] = None,
        mode_fn: Optional[ModeFn] = None,
    ):
        det = config.get("detector") or {}
        camera = config.get("camera") or {}
        brain = config.get("brain") or {}

        self._enabled = bool(det.get("enabled", True))
        self._backend = str(det.get("backend", "ultralytics")).strip().lower() or "ultralytics"
        self._model_path = str(det.get("model_path", "models/yolo_nano.pt"))
        self._confidence = float(det.get("confidence", 0.45))
        self._interval = float(det.get("run_every_n_seconds", 1.0))
        self._cloud_interval = float(det.get("cloud_run_every_n_seconds", max(4.0, self._interval)))
        self._classes = set(str(c).lower() for c in (det.get("classes") or []))
        self._ai_width = int(camera.get("ai_width", 640))
        self._ai_height = int(camera.get("ai_height", 360))
        self._mode_fn = mode_fn

        self._cloud_provider = str(det.get("cloud_provider", brain.get("provider", "auto"))).strip().lower()
        self._base_url = str(brain.get("base_url", "https://api.x.ai/v1")).rstrip("/")
        self._vision_model = str(brain.get("vision_model", "grok-4.3"))
        self._google_base_url = str(
            brain.get("google_base_url", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")
        self._google_model = str(brain.get("google_vision_model", "gemini-1.5-flash"))
        self._timeout = float(det.get("cloud_timeout_seconds", 20))
        self._daily_cap = max(1, int(det.get("cloud_daily_call_cap", 100)))
        secrets_file = str(brain.get("secrets_file", "secrets.yaml"))
        self._secrets_path = os.path.join(_project_root(), secrets_file)
        self._api_key = self._load_key("grok_api_key", "GROK_API_KEY")
        self._google_api_key = self._load_key("google_api_key", "GOOGLE_API_KEY")

        self._frame_store = frame_store
        self._motion = motion
        self._on_detection = on_detection

        self._model = None
        self._local_loaded = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_run_at: float = 0.0
        self._message = "Object detector not started."
        self._active_backend = "off"
        self._lock = threading.Lock()
        self._calls_today = 0
        self._call_date = date.today()

    def start(self) -> None:
        if not self._enabled or self._backend == "off":
            self._message = "Object detection disabled in config."
            self._active_backend = "off"
            return
        if cv2 is None:
            self._message = "OpenCV not available; object detection disabled."
            print(f"[detector] {self._message}")
            return

        desired = self._desired_backend()
        if desired == "ultralytics":
            self._ensure_local_model()
        elif desired == "cloud":
            if not self._cloud_available():
                self._message = "Cloud detector unavailable (no API key); trying local."
                self._ensure_local_model()
            else:
                self._active_backend = "cloud"
                self._message = "Cloud detector ready."
                print(f"[detector] {self._message}")
        elif desired == "degraded":
            self._active_backend = "degraded"
            self._message = "Detector degraded (motion only)."
            print(f"[detector] {self._message}")
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="object-detector", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self._unload_local_model()

    def unload_for_offload(self) -> None:
        """Drop local YOLO weights to free RAM (ResourceGuard hook)."""
        self._unload_local_model()
        self._active_backend = "cloud" if self._cloud_available() else "degraded"
        self._message = f"Offloaded; backend={self._active_backend}"
        print(f"[detector] {self._message}")

    def ensure_local(self) -> None:
        """Reload local YOLO when RAM recovers."""
        self._ensure_local_model()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_active(self) -> bool:
        if not self.is_running():
            return False
        backend = self._active_backend
        if backend == "ultralytics":
            return self._model is not None
        if backend == "cloud":
            return self._cloud_available()
        return False

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "active": self.is_active(),
            "enabled": self._enabled,
            "backend": self._backend,
            "active_backend": self._active_backend,
            "model_path": self._model_path,
            "message": self._message,
            "cloud_available": self._cloud_available(),
        }

    def detect_frame(self, frame) -> List[Dict[str, Any]]:
        """Run detection on a single frame (for tests and manual use)."""
        if frame is None or cv2 is None:
            return []
        backend = self._desired_backend()
        if backend == "cloud" or (backend == "auto" and self._active_backend == "cloud"):
            return self._detect_cloud(frame)
        if self._model is None:
            return []
        try:
            small = cv2.resize(frame, (self._ai_width, self._ai_height))
            results = self._model.predict(
                small,
                conf=self._confidence,
                verbose=False,
            )
            return self._parse_results(results)
        except Exception as error:  # noqa: BLE001
            print(f"[detector] Detection error: {error}")
            return []

    def _desired_backend(self) -> str:
        if self._backend == "off":
            return "off"
        if self._backend in ("ultralytics", "cloud"):
            return self._backend
        # auto
        if self._mode_fn is not None:
            try:
                mode = str(self._mode_fn() or "local")
            except Exception:  # noqa: BLE001
                mode = "local"
            if mode == "cloud":
                return "cloud"
            if mode == "degraded":
                return "degraded"
        return "ultralytics"

    def _ensure_local_model(self) -> None:
        if self._model is not None:
            self._active_backend = "ultralytics"
            return
        try:
            from ultralytics import YOLO  # type: ignore

            self._model = YOLO(self._model_path)
            self._local_loaded = True
            self._active_backend = "ultralytics"
            self._message = f"Loaded model from {self._model_path}"
            print(f"[detector] {self._message}")
        except Exception as error:  # noqa: BLE001
            self._model = None
            self._local_loaded = False
            if self._cloud_available():
                self._active_backend = "cloud"
                self._message = f"Local YOLO failed ({error}); using cloud."
            else:
                self._active_backend = "off"
                self._message = f"Could not load YOLO model: {error}"
            print(f"[detector] {self._message}")

    def _unload_local_model(self) -> None:
        if self._model is None:
            return
        self._model = None
        self._local_loaded = False
        try:
            import gc

            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    def _parse_results(self, results) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        if not results:
            return detections

        result = results[0]
        names = result.names or {}
        boxes = result.boxes
        if boxes is None:
            return detections

        for box in boxes:
            cls_id = int(box.cls[0])
            label = str(names.get(cls_id, cls_id)).lower()
            if self._classes and label not in self._classes:
                continue
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            detections.append(
                {
                    "label": label,
                    "confidence": round(conf, 3),
                    "bbox": [round(v, 1) for v in xyxy],
                }
            )
        return detections

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(0.2)
            desired = self._desired_backend()
            if desired == "degraded" or desired == "off":
                if self._model is not None:
                    self._unload_local_model()
                self._active_backend = desired
                continue

            if desired == "cloud":
                if self._model is not None:
                    self._unload_local_model()
                self._active_backend = "cloud"
                interval = self._cloud_interval
            else:
                if self._model is None:
                    self._ensure_local_model()
                if self._model is None and self._cloud_available():
                    self._active_backend = "cloud"
                    interval = self._cloud_interval
                elif self._model is None:
                    continue
                else:
                    self._active_backend = "ultralytics"
                    interval = self._interval

            now = time.monotonic()
            if now - self._last_run_at < interval:
                continue

            if not self._motion.is_active():
                continue

            frame = self._frame_store.get_frame()
            if frame is None:
                continue

            self._last_run_at = now
            if self._active_backend == "cloud":
                detections = self._detect_cloud(frame)
            else:
                detections = self.detect_frame(frame)
            if detections and self._on_detection is not None:
                try:
                    self._on_detection(detections, frame)
                except Exception as error:  # noqa: BLE001
                    print(f"[detector] Detection callback error: {error}")

    def _cloud_available(self) -> bool:
        provider = self._effective_provider()
        if provider == "google":
            return bool(self._google_api_key)
        return bool(self._api_key)

    def _effective_provider(self) -> str:
        if self._cloud_provider == "auto":
            if self._google_api_key:
                return "google"
            return "grok"
        return self._cloud_provider

    def _detect_cloud(self, frame) -> List[Dict[str, Any]]:
        if not self._cloud_available() or cv2 is None:
            return []
        with self._lock:
            today = date.today()
            if today != self._call_date:
                self._call_date = today
                self._calls_today = 0
            if self._calls_today >= self._daily_cap:
                self._message = "Cloud detector daily cap reached."
                return []
            self._calls_today += 1

        try:
            small = cv2.resize(frame, (self._ai_width, self._ai_height))
            ok, encoded = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                return []
            jpeg = encoded.tobytes()
            labels = self._cloud_labels(jpeg)
        except Exception as error:  # noqa: BLE001
            print(f"[detector] Cloud detection error: {error}")
            return []

        detections: List[Dict[str, Any]] = []
        for item in labels:
            label = str(item.get("label", "")).lower().strip()
            if not label:
                continue
            if self._classes and label not in self._classes:
                continue
            try:
                conf = float(item.get("confidence", 0.5) or 0.5)
            except (TypeError, ValueError):
                conf = 0.5
            if conf < self._confidence:
                continue
            detections.append(
                {
                    "label": label,
                    "confidence": round(conf, 3),
                    "bbox": item.get("bbox") or [],
                    "source": "cloud",
                }
            )
        return detections

    def _cloud_labels(self, jpeg: bytes) -> List[Dict[str, Any]]:
        prompt = (
            "List objects visible in this home security frame. Return strict JSON: "
            '{"detections":[{"label":"person|dog|cat|car|bicycle|backpack|suitcase",'
            '"confidence":0.0}]} using only those labels. Empty list if none.'
        )
        provider = self._effective_provider()
        if provider == "google":
            text = self._call_google(jpeg, prompt)
        else:
            text = self._call_grok(jpeg, prompt)
        data = self._parse_json(text) or {}
        items = data.get("detections")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        return []

    def _call_grok(self, jpeg: bytes, prompt: str) -> str:
        b64 = base64.b64encode(jpeg).decode("ascii")
        payload = {
            "model": self._vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.1,
            "stream": False,
        }
        data = self._post_json(
            f"{self._base_url}/chat/completions",
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            payload,
        )
        return str(data["choices"][0]["message"]["content"]).strip()

    def _call_google(self, jpeg: bytes, prompt: str) -> str:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(jpeg).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
        }
        url = (
            f"{self._google_base_url}/models/{self._google_model}:generateContent"
            f"?key={self._google_api_key}"
        )
        data = self._post_json(url, {"Content-Type": "application/json"}, payload)
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(str(p.get("text", "")) for p in parts).strip()

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

    def _post_json(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:160]
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error

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
