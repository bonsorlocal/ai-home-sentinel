"""Camera-site spoken output (Phase 9E).

Synthesizes short replies via cloud TTS (Google when available) and plays them
on a Pi-attached speaker using ffplay/aplay. Dashboard browser TTS stays separate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Callable, Dict, Optional


class CameraVoice:
    """Speak short phrases at the camera location."""

    def __init__(
        self,
        config: Any,
        *,
        synthesize_fn: Optional[Callable[[str], bytes]] = None,
    ) -> None:
        cfg = config.get("voice_out") or {}
        self._enabled = bool(cfg.get("enabled", False))
        self._device = str(cfg.get("device", "default") or "default")
        self._max_chars = max(40, int(cfg.get("max_chars", 300)))
        self._player = str(cfg.get("player", "auto")).strip().lower() or "auto"
        self._synthesize_fn = synthesize_fn
        self._lock = threading.Lock()
        self._last_line = ""
        self._message = "Camera voice idle."

    def is_available(self) -> bool:
        return self._enabled and self._synthesize_fn is not None

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "available": self.is_available(),
            "device": self._device,
            "player": self._resolve_player() or "none",
            "last_line": self._last_line,
            "message": self._message,
        }

    def speak(self, text: str) -> Dict[str, Any]:
        """Synthesize and play ``text``. Never raises."""
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "message": "Nothing to speak."}
        line = line[: self._max_chars]
        with self._lock:
            self._last_line = line
            if not self._enabled:
                self._message = f"(voice_out disabled) Would say: {line}"
                print(f"[voice_out] {self._message}")
                return {"ok": True, "spoken": False, "message": self._message, "text": line}
            if self._synthesize_fn is None:
                self._message = "No TTS synthesizer configured."
                return {"ok": False, "spoken": False, "message": self._message, "text": line}

        try:
            audio = self._synthesize_fn(line)
        except Exception as error:  # noqa: BLE001
            message = f"TTS failed: {error}"
            print(f"[voice_out] {message}")
            return {"ok": False, "spoken": False, "message": message, "text": line}

        if not audio:
            return {"ok": False, "spoken": False, "message": "Empty TTS audio.", "text": line}

        played = self._play_audio(audio)
        with self._lock:
            self._message = "Spoke at camera." if played.get("ok") else played.get("message", "Play failed.")
        return {
            "ok": bool(played.get("ok")),
            "spoken": bool(played.get("ok")),
            "message": self._message,
            "text": line,
            "player": played.get("player"),
        }

    def _resolve_player(self) -> Optional[str]:
        if self._player != "auto":
            return self._player if shutil.which(self._player) else None
        for name in ("ffplay", "aplay", "paplay"):
            if shutil.which(name):
                return name
        return None

    def _play_audio(self, audio: bytes) -> Dict[str, Any]:
        player = self._resolve_player()
        if player is None:
            return {
                "ok": False,
                "message": "No audio player found (install ffmpeg/ffplay or alsa-utils).",
            }
        suffix = ".mp3" if player == "ffplay" else ".wav"
        # Google TTS returns MP3; aplay needs WAV — use ffplay when possible.
        if player in ("aplay", "paplay") and not shutil.which("ffmpeg"):
            # Still try ffplay path if we can fall back.
            if shutil.which("ffplay"):
                player = "ffplay"
                suffix = ".mp3"
            else:
                return {
                    "ok": False,
                    "message": "MP3 playback needs ffplay; install ffmpeg.",
                }
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio)
                tmp_path = handle.name
            if player == "ffplay":
                cmd = [
                    "ffplay",
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    tmp_path,
                ]
            elif player == "aplay":
                cmd = ["aplay", "-D", self._device, tmp_path]
            else:
                cmd = [player, tmp_path]
            subprocess.run(cmd, check=True, timeout=60)
            return {"ok": True, "player": player}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "message": f"Playback failed: {error}", "player": player}
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
