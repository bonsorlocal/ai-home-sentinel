"""Camera capture module (Phase 2).

This runs a single background loop that opens the camera once, grabs frames as
fast as the target frame rate allows, turns each frame into JPEG bytes, and
pushes it into the shared :class:`~sentinel.frame_store.FrameStore`. Everything
else (the live video stream now, motion/AI later) reads from that one store, so
the camera stream is only ever decoded once.

Two camera backends are supported, chosen by ``camera.type`` in ``config.yaml``:

- ``"picamera2"`` - the official Raspberry Pi camera library (Pi camera module).
- ``"opencv"``    - OpenCV's ``VideoCapture`` (USB / V4L2 webcams).
- ``"auto"``      - try Picamera2 first, then fall back to OpenCV.

Following the project rule of "never crash silently", every hardware call is
wrapped in try/except. If no camera is available (for example when testing on a
PC), the loop reports the problem in plain language and the rest of the app -
the dashboard and ``/status`` - keeps running normally. The dashboard then
shows the camera as unavailable instead of the whole program dying.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple

from sentinel.config import Config
from sentinel.frame_store import FrameStore

# OpenCV is optional. It is needed for the USB-webcam backend and is also our
# preferred JPEG encoder. If it is missing we say so clearly rather than crash.
try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001 - any import problem means "not available"
    cv2 = None

# Picamera2 is normally a SYSTEM package on Raspberry Pi OS (python3-picamera2)
# and is not reliably pip-installable. Inside a plain venv the import fails;
# see the README for the one-time fix. We import it lazily so the app still
# runs everywhere, with or without it.
try:
    from picamera2 import Picamera2  # type: ignore
except Exception:  # noqa: BLE001 - missing on PCs and plain venvs; that's fine
    Picamera2 = None

# simplejpeg ships as a dependency of picamera2, so it is available whenever the
# Pi camera is. We use it as a JPEG encoder when OpenCV is not installed.
try:
    import simplejpeg  # type: ignore
except Exception:  # noqa: BLE001
    simplejpeg = None


def _log(message: str) -> None:
    """Print a clearly tagged camera message (matches the project's style)."""
    print(f"[camera] {message}")


# A "reader" returns the newest frame as a numpy array, or None on a hiccup.
# A "closer" releases the camera. Backends hand back this pair plus their name.
Reader = Callable[[], object]
Closer = Callable[[], None]


class Camera:
    """Owns the single capture loop and reports whether it is working.

    Create one of these, call :meth:`start`, and read frames from the
    ``frame_store`` you passed in. Call :meth:`stop` on shutdown.
    """

    def __init__(self, config: Config, frame_store: FrameStore):
        # Exposed publicly so the dashboard can read the latest frames from it.
        self.frame_store = frame_store

        cam = config.get("camera") or {}
        self._type = str(cam.get("type", "auto")).lower().strip()
        self._width = int(cam.get("width", 1280))
        self._height = int(cam.get("height", 720))
        self._target_fps = float(cam.get("target_fps", 20)) or 20.0
        self._jpeg_quality = 80

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Plain-language state the dashboard can show to the user.
        self._backend_name: Optional[str] = None
        self._message = "Camera not started yet."

    # -- public control ----------------------------------------------------

    def start(self) -> None:
        """Start the capture loop in a background thread (returns immediately)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._message = "Starting camera..."
        self._thread = threading.Thread(
            target=self._run, name="camera-capture", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the capture loop to stop and wait briefly for it to finish."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self._backend_name = None

    def is_running(self) -> bool:
        """True while the capture loop thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    def is_active(self) -> bool:
        """True when the camera is actually producing fresh frames right now."""
        return self.is_running() and self.frame_store.is_fresh()

    def status(self) -> dict:
        """A small snapshot of camera state for the /status API and dashboard."""
        return {
            "running": self.is_running(),
            "active": self.is_active(),
            "backend": self._backend_name,
            "type": self._type,
            "width": self._width,
            "height": self._height,
            "target_fps": self._target_fps,
            "message": self._message,
        }

    # -- the capture loop --------------------------------------------------

    def _run(self) -> None:
        """Open a backend and keep capturing, reconnecting if the camera drops."""
        while not self._stop_event.is_set():
            backend = self._open_backend()
            if backend is None:
                # Could not open any camera. For a forced backend we keep
                # retrying in case it gets plugged in; "auto" does too. We wait
                # a few seconds so we are not hammering the hardware.
                self._wait(5.0)
                continue

            read, close, name = backend
            self._backend_name = name
            self._message = f"Capturing from {name}."
            _log(self._message)
            try:
                self._capture_until_stopped(read)
            finally:
                self._safe_close(close)

            if not self._stop_event.is_set():
                self._message = "Lost the camera; trying to reconnect..."
                _log(self._message)
                self._wait(3.0)

        self._message = "Camera stopped."

    def _capture_until_stopped(self, read: Reader) -> None:
        """Grab, encode, and store frames until told to stop or the camera fails."""
        min_interval = 1.0 / self._target_fps
        consecutive_failures = 0

        while not self._stop_event.is_set():
            start = time.monotonic()
            frame = read()

            if frame is None:
                # An occasional empty read is normal; many in a row means the
                # camera went away, so we break out to reconnect.
                consecutive_failures += 1
                if consecutive_failures >= 30:
                    raise RuntimeError("camera stopped returning frames")
                self._wait(0.1)
                continue
            consecutive_failures = 0

            jpeg = _encode_jpeg(frame, self._jpeg_quality)
            if jpeg is not None:
                self.frame_store.update(jpeg, frame)

            # Pace the loop to roughly the target FPS so we don't peg the CPU.
            elapsed = time.monotonic() - start
            if elapsed < min_interval:
                self._wait(min_interval - elapsed)

    # -- backend selection -------------------------------------------------

    def _open_backend(self) -> Optional[Tuple[Reader, Closer, str]]:
        """Open the camera according to ``camera.type``, with auto-fallback."""
        if self._type == "picamera2":
            return self._open_picamera2()
        if self._type == "opencv":
            return self._open_opencv()

        # "auto" (or anything unexpected): prefer the Pi camera, fall back to USB.
        backend = self._open_picamera2()
        if backend is not None:
            return backend
        return self._open_opencv()

    def _open_picamera2(self) -> Optional[Tuple[Reader, Closer, str]]:
        """Open the Raspberry Pi camera via Picamera2, or return None on failure."""
        if Picamera2 is None:
            self._message = (
                "Picamera2 is not available in this environment. On a Raspberry "
                "Pi, install python3-picamera2 and recreate the venv with "
                "--system-site-packages (see the README)."
            )
            _log(self._message)
            return None

        try:
            picam2 = Picamera2()
            # Picamera2's format names are reversed from what you'd expect:
            # requesting "RGB888" actually hands back a BGR-ordered array, which
            # is exactly what OpenCV/JPEG encoding want, so colours come out right.
            video_config = picam2.create_video_configuration(
                main={"size": (self._width, self._height), "format": "RGB888"}
            )
            picam2.configure(video_config)
            picam2.start()

            def read():
                return picam2.capture_array()

            def close():
                picam2.stop()
                picam2.close()

            return read, close, "Picamera2 (Raspberry Pi camera)"
        except Exception as error:  # noqa: BLE001 - report, don't crash the app
            self._message = f"Could not start the Pi camera: {error}"
            _log(self._message)
            return None

    def _open_opencv(self) -> Optional[Tuple[Reader, Closer, str]]:
        """Open a USB/V4L2 webcam via OpenCV, or return None on failure."""
        if cv2 is None:
            self._message = (
                "OpenCV is not installed, so the USB-camera backend is "
                "unavailable. Run 'pip install -r requirements.txt'."
            )
            _log(self._message)
            return None

        try:
            capture = cv2.VideoCapture(0)
            if not capture.isOpened():
                capture.release()
                self._message = "No USB camera found at index 0."
                _log(self._message)
                return None

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            capture.set(cv2.CAP_PROP_FPS, self._target_fps)
            # Keep the driver buffer tiny so we always read the freshest frame
            # and drop stale ones, matching the "freshest frame" Pi-5 approach.
            try:
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001 - not all drivers support this
                pass

            def read():
                ok, frame = capture.read()
                return frame if ok else None

            def close():
                capture.release()

            return read, close, "OpenCV VideoCapture (USB camera)"
        except Exception as error:  # noqa: BLE001 - report, don't crash the app
            self._message = f"Could not start the USB camera: {error}"
            _log(self._message)
            return None

    # -- small helpers -----------------------------------------------------

    def _wait(self, seconds: float) -> None:
        """Sleep, but wake up immediately if we have been asked to stop."""
        self._stop_event.wait(seconds)

    @staticmethod
    def _safe_close(close: Closer) -> None:
        try:
            close()
        except Exception as error:  # noqa: BLE001 - closing should never crash us
            _log(f"Problem while closing the camera: {error}")


def _encode_jpeg(frame, quality: int) -> Optional[bytes]:
    """Turn a captured image array into JPEG bytes for the browser.

    Both backends hand us BGR-ordered arrays, so we encode them the same way.
    We prefer OpenCV and fall back to simplejpeg (which ships with Picamera2),
    so encoding works whether the Pi camera or a USB camera is in use.
    """
    if cv2 is not None:
        try:
            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
            )
            if ok:
                return buffer.tobytes()
        except Exception as error:  # noqa: BLE001
            _log(f"OpenCV could not encode a frame: {error}")

    if simplejpeg is not None:
        try:
            return simplejpeg.encode_jpeg(frame, quality=int(quality), colorspace="BGR")
        except Exception as error:  # noqa: BLE001
            _log(f"simplejpeg could not encode a frame: {error}")

    return None
