"""Google Cloud helpers: Text-to-Speech and Video Intelligence (REST + service account).

Gemini uses a separate API key path in brain.py / clip_metadata.py.
This module covers GCP APIs that require a service account JSON key:
- Cloud Text-to-Speech
- Cloud Video Intelligence (via Cloud Storage for larger videos)
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

try:
    import google.auth.transport.requests  # type: ignore
    from google.oauth2 import service_account  # type: ignore
except Exception:  # noqa: BLE001
    google = None  # type: ignore
    service_account = None


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GoogleCredentials:
    """Cached service-account access token for GCP REST calls."""

    SCOPES = (
        "https://www.googleapis.com/auth/cloud-platform",
    )

    def __init__(self, credentials_path: str, project_id: str = ""):
        self._path = credentials_path
        self._project_id = project_id.strip()
        self._credentials = None
        self._lock = threading.Lock()
        self._message = ""
        self._load()

    @property
    def project_id(self) -> str:
        if self._project_id:
            return self._project_id
        if self._credentials is not None:
            return str(getattr(self._credentials, "project_id", "") or "")
        return ""

    @property
    def available(self) -> bool:
        return self._credentials is not None

    @property
    def message(self) -> str:
        return self._message

    def _load(self) -> None:
        if service_account is None:
            self._message = (
                "google-auth is not installed. Run: pip install google-auth"
            )
            return
        path = self._path
        if not os.path.isabs(path):
            path = os.path.join(_project_root(), path)
        if not os.path.isfile(path):
            self._message = f"GCP credentials file not found: {path}"
            return
        try:
            self._credentials = service_account.Credentials.from_service_account_file(
                path,
                scopes=list(self.SCOPES),
            )
            if not self._project_id:
                self._project_id = str(getattr(self._credentials, "project_id", "") or "")
            self._message = "GCP credentials loaded."
        except Exception as error:  # noqa: BLE001
            self._message = f"Could not load GCP credentials: {error}"

    def access_token(self) -> str:
        if not self._credentials:
            raise RuntimeError(self._message or "GCP credentials unavailable.")
        with self._lock:
            if not self._credentials.valid:
                request = google.auth.transport.requests.Request()
                self._credentials.refresh(request)
            token = str(self._credentials.token or "")
            if not token:
                raise RuntimeError("GCP access token refresh returned empty token.")
            return token


class GoogleTextToSpeech:
    """Synthesize spoken replies via Cloud Text-to-Speech."""

    def __init__(self, creds: GoogleCredentials, cfg: Dict[str, Any]):
        self._creds = creds
        self._enabled = bool(cfg.get("enabled", False))
        self._voice_name = str(cfg.get("voice_name", "en-US-Neural2-F"))
        self._language_code = str(cfg.get("language_code", "en-US"))
        self._speaking_rate = float(cfg.get("speaking_rate", 1.0))
        self._daily_char_cap = max(100, int(cfg.get("daily_char_cap", 20000)))
        self._timeout = float(cfg.get("request_timeout_seconds", 20))
        self._lock = threading.Lock()
        self._chars_today = 0
        self._char_date = date.today()

    def is_available(self) -> bool:
        return self._enabled and self._creds.available

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._roll_day()
            used = self._chars_today
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "voice_name": self._voice_name,
            "language_code": self._language_code,
            "chars_used_today": used,
            "daily_char_cap": self._daily_char_cap,
            "chars_remaining": max(0, self._daily_char_cap - used),
        }

    def synthesize(self, text: str) -> bytes:
        if not self.is_available():
            raise RuntimeError("Google Text-to-Speech is not available.")
        cleaned = str(text or "").strip()
        if not cleaned:
            raise RuntimeError("TTS text is empty.")
        with self._lock:
            self._roll_day()
            if self._chars_today + len(cleaned) > self._daily_char_cap:
                raise RuntimeError("Daily TTS character cap reached.")
            self._chars_today += len(cleaned)

        payload = {
            "input": {"text": cleaned[:5000]},
            "voice": {
                "languageCode": self._language_code,
                "name": self._voice_name,
            },
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": self._speaking_rate,
            },
        }
        data = self._post_json(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            payload,
        )
        audio_b64 = str(data.get("audioContent", "")).strip()
        if not audio_b64:
            raise RuntimeError("TTS returned empty audio.")
        return base64.b64decode(audio_b64)

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._char_date:
            self._char_date = today
            self._chars_today = 0

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = self._creds.access_token()
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"TTS HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"TTS network error: {error}") from error
        return json.loads(raw)


class GoogleVideoIntelligence:
    """Analyze a local video file; upload to GCS when needed."""

    def __init__(
        self,
        creds: GoogleCredentials,
        cfg: Dict[str, Any],
        *,
        gcs_bucket: str,
    ):
        self._creds = creds
        self._enabled = bool(cfg.get("enabled", False))
        self._fallback_only = bool(cfg.get("fallback_only", True))
        self._daily_job_cap = max(1, int(cfg.get("daily_job_cap", 3)))
        self._max_upload_mb = max(1, int(cfg.get("max_upload_mb", 80)))
        self._inline_max_mb = max(1, min(10, int(cfg.get("inline_max_mb", 9))))
        self._timeout = float(cfg.get("request_timeout_seconds", 120))
        self._poll_interval = float(cfg.get("poll_interval_seconds", 3))
        self._gcs_bucket = gcs_bucket.strip()
        self._lock = threading.Lock()
        self._jobs_today = 0
        self._job_date = date.today()

    @property
    def fallback_only(self) -> bool:
        return self._fallback_only

    def is_available(self) -> bool:
        return self._enabled and self._creds.available and bool(self._gcs_bucket)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._roll_day()
            used = self._jobs_today
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "fallback_only": self._fallback_only,
            "gcs_bucket": self._gcs_bucket or None,
            "jobs_used_today": used,
            "daily_job_cap": self._daily_job_cap,
            "jobs_remaining": max(0, self._daily_job_cap - used),
            "max_upload_mb": self._max_upload_mb,
        }

    def analyze_file(self, video_path: str) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("Video Intelligence is not available.")
        if not os.path.isfile(video_path):
            raise RuntimeError(f"Video file not found: {video_path}")
        size_bytes = os.path.getsize(video_path)
        max_bytes = self._max_upload_mb * 1024 * 1024
        if size_bytes > max_bytes:
            raise RuntimeError(
                f"Video too large for analysis ({size_bytes // (1024 * 1024)} MB > "
                f"{self._max_upload_mb} MB cap)."
            )
        with self._lock:
            self._roll_day()
            if self._jobs_today >= self._daily_job_cap:
                raise RuntimeError("Daily Video Intelligence job cap reached.")
            self._jobs_today += 1

        inline_limit = self._inline_max_mb * 1024 * 1024
        if size_bytes <= inline_limit:
            with open(video_path, "rb") as handle:
                content = base64.b64encode(handle.read()).decode("ascii")
            request_body = {
                "inputContent": content,
                "features": [
                    {"type": "LABEL_DETECTION", "mode": "SHOT_AND_FRAME_MODE"},
                    {"type": "SHOT_CHANGE_DETECTION"},
                    {"type": "OBJECT_TRACKING"},
                ],
            }
        else:
            object_name = self._upload_to_gcs(video_path)
            request_body = {
                "inputUri": f"gs://{self._gcs_bucket}/{object_name}",
                "features": [
                    {"type": "LABEL_DETECTION", "mode": "SHOT_AND_FRAME_MODE"},
                    {"type": "SHOT_CHANGE_DETECTION"},
                    {"type": "OBJECT_TRACKING"},
                ],
            }

        op = self._post_json(
            "https://videointelligence.googleapis.com/v1/videos:annotate",
            request_body,
        )
        operation_name = str(op.get("name", "")).strip()
        if not operation_name:
            raise RuntimeError("Video Intelligence did not return an operation name.")
        result = self._poll_operation(operation_name)
        summary = self._summarize_annotation(result)
        return {
            "scene_summary": summary,
            "raw": result,
            "source_path": video_path,
            "size_bytes": size_bytes,
        }

    def _upload_to_gcs(self, video_path: str) -> str:
        basename = os.path.basename(video_path)
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", basename) or "segment.mp4"
        object_name = f"vi/{int(time.time())}_{safe}"
        token = self._creds.access_token()
        url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{self._gcs_bucket}/o?uploadType=media&name={urllib.request.quote(object_name, safe='')}"
        )
        with open(video_path, "rb") as handle:
            data = handle.read()
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"GCS upload HTTP {error.code}: {detail}") from error
        return object_name

    def _poll_operation(self, operation_name: str) -> Dict[str, Any]:
        if operation_name.startswith("https://"):
            url = operation_name
        elif operation_name.startswith("operations/"):
            url = f"https://videointelligence.googleapis.com/v1/{operation_name}"
        else:
            url = f"https://videointelligence.googleapis.com/v1/{operation_name.lstrip('/')}"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            data = self._get_json(url)
            if data.get("done"):
                if "error" in data:
                    raise RuntimeError(str(data["error"])[:240])
                return data.get("response") or {}
            time.sleep(self._poll_interval)
        raise RuntimeError("Video Intelligence operation timed out.")

    def _summarize_annotation(self, response: Dict[str, Any]) -> str:
        parts: List[str] = []
        annotations = response.get("annotationResults") or []
        if not annotations:
            return "Video Intelligence returned no annotation results."
        first = annotations[0] if isinstance(annotations[0], dict) else {}
        labels: List[str] = []
        for block in first.get("segmentLabelAnnotations") or []:
            entity = block.get("entity") or {}
            desc = str(entity.get("description", "")).strip()
            if desc and desc not in labels:
                labels.append(desc)
        for block in first.get("shotLabelAnnotations") or []:
            entity = block.get("entity") or {}
            desc = str(entity.get("description", "")).strip()
            if desc and desc not in labels:
                labels.append(desc)
        if labels:
            parts.append("labels: " + ", ".join(labels[:8]))
        shots = first.get("shotAnnotations") or []
        if shots:
            parts.append(f"shots: {len(shots)}")
        tracked: List[str] = []
        for block in first.get("objectAnnotations") or []:
            entity = block.get("entity") or {}
            desc = str(entity.get("description", "")).strip()
            if desc and desc not in tracked:
                tracked.append(desc)
        if tracked:
            parts.append("tracked objects: " + ", ".join(tracked[:6]))
        if not parts:
            return "Video Intelligence completed but found no strong labels."
        return "; ".join(parts)

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._job_date:
            self._job_date = today
            self._jobs_today = 0

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = self._creds.access_token()
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"Video Intelligence HTTP {error.code}: {detail}") from error
        return json.loads(raw)

    def _get_json(self, url: str) -> Dict[str, Any]:
        token = self._creds.access_token()
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"Video Intelligence poll HTTP {error.code}: {detail}") from error
        return json.loads(raw)


class GoogleCloudServices:
    """Facade for GCP speech + video analysis used by runtime and dashboard."""

    def __init__(self, config: Any, *, secrets_path: str = "secrets.yaml"):
        gcfg = config.get("google") or {}
        self._project_id = str(gcfg.get("project_id", "")).strip()
        creds_file = str(gcfg.get("credentials_file", "secrets/gcp-service-account.json"))
        bucket = str(gcfg.get("gcs_bucket", "")).strip()
        if not bucket and self._project_id:
            bucket = f"{self._project_id}-sentinel-video"

        secrets_file = str(gcfg.get("secrets_file", secrets_path))
        creds_path = creds_file
        if secrets_file and not os.path.isabs(secrets_file):
            root = _project_root()
            alt = self._read_credentials_path_from_secrets(
                os.path.join(root, secrets_file)
            )
            if alt:
                creds_path = alt

        self._creds = GoogleCredentials(creds_path, project_id=self._project_id)
        if not self._project_id:
            self._project_id = self._creds.project_id

        vi_cfg = gcfg.get("video_intelligence") or {}
        tts_cfg = gcfg.get("text_to_speech") or {}
        self.video_intelligence = GoogleVideoIntelligence(
            self._creds,
            vi_cfg,
            gcs_bucket=bucket,
        )
        self.text_to_speech = GoogleTextToSpeech(self._creds, tts_cfg)

    @staticmethod
    def _read_credentials_path_from_secrets(secrets_path: str) -> str:
        if not os.path.isfile(secrets_path):
            return ""
        try:
            import yaml  # type: ignore
        except Exception:
            return ""
        try:
            with open(secrets_path, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        return str(
            data.get("gcp_credentials_file")
            or data.get("google_credentials_file")
            or ""
        ).strip()

    def status(self) -> Dict[str, Any]:
        return {
            "project_id": self._project_id or None,
            "credentials": {
                "available": self._creds.available,
                "message": self._creds.message,
            },
            "video_intelligence": self.video_intelligence.status(),
            "text_to_speech": self.text_to_speech.status(),
        }


def gemini_keyframe_analysis_is_weak(analyzed: List[Dict[str, Any]]) -> bool:
    """True when Gemini keyframe analysis did not produce useful scene text."""
    if not analyzed:
        return True
    for item in analyzed:
        payload = item.get("analysis") or {}
        if payload.get("error"):
            continue
        scene = str(payload.get("scene_summary", "")).strip()
        if scene:
            confidence = payload.get("confidence")
            if confidence is None:
                return False
            try:
                if float(confidence) >= 0.35:
                    return False
            except (TypeError, ValueError):
                return False
    return True
