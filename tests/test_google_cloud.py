"""Tests for Google Cloud Video Intelligence + TTS helpers."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.google_cloud import (  # noqa: E402
    GoogleCloudServices,
    GoogleTextToSpeech,
    GoogleVideoIntelligence,
    gemini_keyframe_analysis_is_weak,
)


def test_gemini_keyframe_analysis_is_weak_detects_empty_and_low_confidence():
    assert gemini_keyframe_analysis_is_weak([]) is True
    assert gemini_keyframe_analysis_is_weak([{"analysis": {"scene_summary": ""}}]) is True
    assert gemini_keyframe_analysis_is_weak(
        [{"analysis": {"scene_summary": "Person at door.", "confidence": 0.2}}]
    ) is True
    assert gemini_keyframe_analysis_is_weak(
        [{"analysis": {"scene_summary": "Person at door.", "confidence": 0.8}}]
    ) is False


def test_google_tts_synthesize_uses_rest(monkeypatch):
    creds = MagicMock()
    creds.available = True
    creds.access_token.return_value = "token-123"

    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        body = json.dumps({"audioContent": "c29ub3Vy"}).encode("utf-8")

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tts = GoogleTextToSpeech(
        creds,
        {
            "enabled": True,
            "voice_name": "en-US-Neural2-F",
            "language_code": "en-US",
            "daily_char_cap": 1000,
        },
    )
    audio = tts.synthesize("Hello from Sentinel.")
    assert audio == b"sonour"
    assert "texttospeech.googleapis.com" in captured["url"]
    assert captured["auth"] == "Bearer token-123"


def test_video_intelligence_inline_small_file(tmp_path, monkeypatch):
    creds = MagicMock()
    creds.available = True
    creds.access_token.return_value = "token-abc"

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video-bytes")

    posts = []

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if "videos:annotate" in url:
            posts.append(json.loads(request.data.decode("utf-8")))
            body = json.dumps({"name": "operations/op-1"}).encode("utf-8")

            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return body

            return Resp()
        if "operations/op-1" in url:
            payload = {
                "done": True,
                "response": {
                    "annotationResults": [
                        {
                            "segmentLabelAnnotations": [
                                {"entity": {"description": "person"}}
                            ],
                            "shotAnnotations": [{}, {}],
                        }
                    ]
                },
            }
            body = json.dumps(payload).encode("utf-8")

            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return body

            return Resp()
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    vi = GoogleVideoIntelligence(
        creds,
        {"enabled": True, "daily_job_cap": 2, "inline_max_mb": 9, "max_upload_mb": 80},
        gcs_bucket="my-project-sentinel-video",
    )
    result = vi.analyze_file(str(video_path))
    assert "person" in result["scene_summary"]
    assert posts
    assert "inputContent" in posts[0]


def test_google_cloud_services_default_bucket_from_project_id():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "google": {
            "project_id": "my-home-sentinel",
            "credentials_file": "missing.json",
            "gcs_bucket": "",
            "video_intelligence": {"enabled": True},
            "text_to_speech": {"enabled": True},
        }
    }.get(key, default)
    services = GoogleCloudServices(cfg)
    assert services.video_intelligence.status()["gcs_bucket"] == "my-home-sentinel-sentinel-video"
