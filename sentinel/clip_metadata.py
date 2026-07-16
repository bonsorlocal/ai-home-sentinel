"""Async clip metadata pipeline (Phase 8).

Extracts a few keyframes from saved clips and asks a cloud model for a compact
JSON analysis. This worker is best-effort: any failure leaves the clip/event in
place and simply marks analysis as failed in entities.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import threading
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

from sentinel.events import EventLedger

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ClipMetadataWorker:
    """Background queue worker for cloud clip analysis."""

    def __init__(self, config: Any, ledger: EventLedger):
        self._ledger = ledger
        cfg = config.get("video_metadata") or {}
        self._enabled = bool(cfg.get("enabled", False))
        self._provider = str(cfg.get("provider", "auto")).strip().lower() or "auto"
        self._base_url = str(cfg.get("base_url", "https://api.x.ai/v1")).rstrip("/")
        self._model = str(cfg.get("model", "grok-4.3"))
        self._google_base_url = str(
            cfg.get("google_base_url", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")
        self._google_model = str(cfg.get("google_model", "gemini-1.5-flash"))
        self._max_keyframes = max(1, int(cfg.get("max_keyframes", 3)))
        self._daily_cap = max(1, int(cfg.get("daily_call_cap", 30)))
        self._timeout = float(cfg.get("request_timeout_seconds", 20))
        self._secrets_file = str(cfg.get("secrets_file", "secrets.yaml"))
        self._api_key = self._load_api_key()
        self._google_api_key = self._load_google_api_key()

        self._lock = threading.Lock()
        self._calls_today = 0
        self._call_date = date.today()

        self._queue: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def is_available(self) -> bool:
        if not self._enabled or cv2 is None:
            return False
        provider = self._effective_provider()
        if provider == "google":
            return bool(self._google_api_key)
        return bool(self._api_key)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._roll_day_if_needed()
            used = self._calls_today
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "provider": self._effective_provider(),
            "model": self._google_model if self._effective_provider() == "google" else self._model,
            "queue_size": self._queue.qsize(),
            "calls_used_today": used,
            "daily_call_cap": self._daily_cap,
            "calls_remaining": max(0, self._daily_cap - used),
        }

    def start(self) -> None:
        if not self.is_available():
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="clip-metadata-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def enqueue(self, event_id: int, clip_path: str) -> bool:
        if not self.is_available() or not clip_path:
            return False
        try:
            self._queue.put_nowait((int(event_id), str(clip_path)))
            return True
        except queue.Full:
            print("[clip_metadata] queue full; dropping analysis job.")
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event_id, clip_path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            status = "ok"
            analysis: Dict[str, Any] = {}
            try:
                analysis = self._analyze_clip(clip_path)
            except Exception as error:  # noqa: BLE001
                status = "failed"
                analysis = {"error": str(error)[:180]}

            record = self._ledger.get_by_id(event_id)
            if record is not None:
                entities = dict(record.entities or {})
                entities["clip_analysis_status"] = status
                if analysis:
                    entities["clip_analysis"] = analysis
                self._ledger.update_entities(event_id, entities)

            self._queue.task_done()

    def analyze_video(self, video_path: str) -> Dict[str, Any]:
        """Analyze a saved clip or DVR segment path (sync, uses daily cap)."""
        return self._analyze_clip(video_path)

    def _analyze_clip(self, clip_path: str) -> Dict[str, Any]:
        keyframes = self._extract_keyframes(clip_path, max_frames=self._max_keyframes)
        if not keyframes:
            raise RuntimeError("No keyframes extracted from clip.")

        with self._lock:
            self._roll_day_if_needed()
            if self._calls_today >= self._daily_cap:
                raise RuntimeError("Daily metadata cap reached.")
            self._calls_today += 1
        provider = self._effective_provider()
        if provider == "google":
            return self._analyze_clip_google(keyframes)
        return self._analyze_clip_grok(keyframes)

    def _analyze_clip_grok(self, keyframes: List[bytes]) -> Dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("Grok key unavailable.")

        prompt = (
            "Analyze these home-security keyframes and return strict JSON with keys: "
            "scene_summary (string), actors (array of short labels), actions "
            "(array of short labels), confidence (0..1 float), key_events "
            "(array of 1-4 short bullets). Do not include markdown."
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for jpeg in keyframes:
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
            "max_tokens": 300,
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
            timeout=self._timeout,
        )
        text = self._extract_answer(data)
        if not text:
            raise RuntimeError("Cloud metadata returned an empty answer.")
        parsed = self._parse_analysis_json(text)
        if parsed is None:
            raise RuntimeError("Could not parse metadata JSON response.")
        return parsed

    def _analyze_clip_google(self, keyframes: List[bytes]) -> Dict[str, Any]:
        if not self._google_api_key:
            raise RuntimeError("Google key unavailable.")
        prompt = (
            "Analyze these home-security keyframes and return strict JSON with keys: "
            "scene_summary (string), actors (array of short labels), actions "
            "(array of short labels), confidence (0..1 float), key_events "
            "(array of 1-4 short bullets). Do not include markdown."
        )
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for jpeg in keyframes:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    }
                }
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 320},
        }
        data = self._post_json(
            f"{self._google_base_url}/models/{self._google_model}:generateContent?key={self._google_api_key}",
            headers={"Content-Type": "application/json"},
            payload=payload,
            timeout=self._timeout,
        )
        text = self._extract_google_answer(data)
        if not text:
            raise RuntimeError("Google metadata returned an empty answer.")
        parsed = self._parse_analysis_json(text)
        if parsed is None:
            raise RuntimeError("Could not parse metadata JSON response.")
        return parsed

    def _extract_keyframes(self, clip_path: str, max_frames: int = 3) -> List[bytes]:
        if cv2 is None or not os.path.exists(clip_path):
            return []
        cap = cv2.VideoCapture(clip_path)
        if not cap.isOpened():
            return []
        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total <= 0:
                return []
            positions = [0, max(total // 2, 0), max(total - 1, 0)]
            positions = positions[: max(1, max_frames)]
            keyframes: List[bytes] = []
            for idx in positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                ok2, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok2:
                    keyframes.append(encoded.tobytes())
            return keyframes
        finally:
            cap.release()

    @staticmethod
    def _extract_answer(data: Dict[str, Any]) -> str:
        try:
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return str(message.get("content") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _extract_google_answer(data: Dict[str, Any]) -> str:
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return ""
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            chunks = [str(p.get("text", "")).strip() for p in parts if str(p.get("text", "")).strip()]
            return "\n".join(chunks).strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_analysis_json(raw: str) -> Optional[Dict[str, Any]]:
        text = raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _post_json(
        url: str, *, headers: Dict[str, str], payload: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._call_date:
            self._call_date = today
            self._calls_today = 0

    def _load_api_key(self) -> str:
        if yaml is None:
            return ""
        path = os.path.join(_project_root(), self._secrets_file)
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:  # noqa: BLE001
            return ""
        if not isinstance(data, dict):
            return ""
        key = data.get("grok_api_key") or data.get("GROK_API_KEY") or ""
        return str(key).strip()

    def _load_google_api_key(self) -> str:
        if yaml is None:
            return ""
        path = os.path.join(_project_root(), self._secrets_file)
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        key = data.get("google_api_key") or data.get("GOOGLE_API_KEY") or ""
        return str(key).strip()

    def _effective_provider(self) -> str:
        if self._provider in ("grok", "google"):
            return self._provider
        if self._google_api_key:
            return "google"
        return "grok"
