"""Client that PULLS live data from the on-Pi Sentinel camera stack (Flask at PI_BASE_URL).

LAN model: this Emergent backend runs on the Pi (port 8080) and proxies to the Pi-local
Sentinel service (default http://127.0.0.1:5000). The browser NEVER talks to the Pi stack
directly — everything goes through /api/* on the Emergent backend (same origin).

Expected Pi endpoints (the contract the Pi service must expose):
  GET  /status                -> JSON health (see normalize_status)
  GET  /video_feed[?camera=]  -> MJPEG multipart stream
  GET  /api/events            -> JSON list of events (Event schema)
  GET  /api/dvr/segments      -> JSON list of DVR segments (DVRSegment schema)
  GET  /api/people            -> JSON list of people (optional)
  GET  /api/rules             -> JSON list of rules (optional)
"""
import os
import time
import logging
import httpx

logger = logging.getLogger("pi")

PI_BASE_URL = os.environ.get("PI_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
STATUS_TIMEOUT = float(os.environ.get("PI_STATUS_TIMEOUT", "3.0"))
DATA_TIMEOUT = float(os.environ.get("PI_DATA_TIMEOUT", "6.0"))


async def check_status():
    """Health-check the Pi. Returns (online: bool, raw_status: dict|None)."""
    try:
        async with httpx.AsyncClient(timeout=STATUS_TIMEOUT) as c:
            r = await c.get(f"{PI_BASE_URL}/status")
            r.raise_for_status()
            return True, r.json()
    except Exception as e:  # noqa: BLE001 - any failure means Pi is offline
        logger.warning(f"Pi /status unreachable at {PI_BASE_URL}: {e}")
        return False, None


async def get_json(path):
    """GET JSON from the Pi. Returns parsed body or None if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=DATA_TIMEOUT) as c:
            r = await c.get(f"{PI_BASE_URL}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pi GET {path} failed: {e}")
        return None


async def open_stream(path):
    """Open an upstream MJPEG stream from the Pi. Returns (async_generator, content_type).

    Raises on connection failure so the caller can return 503.
    """
    client = httpx.AsyncClient(timeout=None)
    req = client.build_request("GET", f"{PI_BASE_URL}{path}")
    resp = await client.send(req, stream=True)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame")

    async def generator():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return generator, content_type


def normalize_status(data):
    """Map a flexible Pi /status payload into the app's health shape (best-effort)."""
    data = data or {}
    cameras = data.get("cameras") or []

    def norm_cam(c, i):
        if isinstance(c, str):
            return {"id": c, "name": c, "location": "", "status": "online",
                    "stream_url": None, "thumbnail_url": None, "is_demo": False}
        return {
            "id": c.get("id", f"cam-{i}"),
            "name": c.get("name", c.get("id", f"Camera {i}")),
            "location": c.get("location", ""),
            "status": c.get("status", "online"),
            "stream_url": c.get("stream_url"),
            "thumbnail_url": c.get("thumbnail_url"),
            "is_demo": False,
        }

    cams = [norm_cam(c, i) for i, c in enumerate(cameras)]
    online = data.get("cameras_online", len([c for c in cams if c["status"] == "online"]))
    metrics = {
        "cpu_percent": data.get("cpu_percent", data.get("cpu")),
        "temp_c": data.get("temp_c", data.get("temperature", data.get("temp"))),
        "memory_percent": data.get("memory_percent", data.get("memory")),
        "disk_percent": data.get("disk_percent", data.get("disk")),
        "uptime_seconds": data.get("uptime_seconds", data.get("uptime")),
        "cameras_online": online,
    }
    return {
        "cameras": cams,
        "cameras_online": online,
        "cameras_total": data.get("cameras_total", len(cams)),
        "mode": data.get("mode"),
        "processing_mode": data.get("processing_mode"),
        "metrics": metrics,
    }


def as_list(data, key):
    """Accept either a bare list or {key: [...]} from the Pi."""
    if data is None:
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get(key, [])
    return []


# ---------------------------------------------------------------------------
# Camera video pipeline discovery + diagnostics
# ---------------------------------------------------------------------------
# The existing on-Pi camera process (Picamera2/OpenCV/Flask) OWNS the camera. We must
# never open the camera device ourselves (only one process may control it) — we only
# PROXY the existing MJPEG feed. PI_VIDEO_URL pins an exact feed; otherwise we probe.

PI_VIDEO_URL = os.environ.get("PI_VIDEO_URL", "").strip()
CAMERA_NAME = os.environ.get("PI_CAMERA_NAME", "Pi Camera")
VIDEO_CANDIDATES = ["/video_feed", "/api/camera/stream", "/camera/stream", "/stream.mjpg",
                    "/stream", "/mjpg", "/video", "/cam", "/camera"]

_video_url_cache = {"url": None, "ts": 0.0}
_video_ok_cache = {"ok": False, "ts": 0.0}
_RESOLVE_TTL = 30.0
_OK_TTL = 5.0


async def _probe(url):
    """Open `url` and read one chunk. Returns (ok, content_type)."""
    try:
        async with httpx.AsyncClient(timeout=STATUS_TIMEOUT) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return False, None
                ctype = r.headers.get("content-type", "")
                async for chunk in r.aiter_raw():
                    if chunk:
                        return True, ctype
                    break
                return True, ctype
    except Exception as e:  # noqa: BLE001
        logger.debug(f"probe {url} failed: {e}")
        return False, None


async def resolve_video_url(force=False):
    """Find a working MJPEG feed on the Pi (cached). Returns URL or None."""
    now = time.time()
    if not force and _video_url_cache["url"] and now - _video_url_cache["ts"] < _RESOLVE_TTL:
        return _video_url_cache["url"]
    if PI_VIDEO_URL:
        _video_url_cache.update(url=PI_VIDEO_URL, ts=now)
        return PI_VIDEO_URL
    for path in VIDEO_CANDIDATES:
        url = f"{PI_BASE_URL}{path}"
        ok, _ = await _probe(url)
        if ok:
            logger.info(f"Resolved Pi camera feed: {url}")
            _video_url_cache.update(url=url, ts=now)
            return url
    _video_url_cache.update(url=None, ts=now)
    return None


async def video_reachable():
    """Fast, cached check of whether the camera feed is currently streaming."""
    now = time.time()
    if now - _video_ok_cache["ts"] < _OK_TTL:
        return _video_ok_cache["ok"]
    url = await resolve_video_url()
    ok = (await _probe(url))[0] if url else False
    _video_ok_cache.update(ok=ok, ts=now)
    return ok


async def open_video_stream():
    """Open a long-lived proxy to the resolved MJPEG feed. Raises if none reachable."""
    url = await resolve_video_url(force=True)
    if not url:
        raise RuntimeError(f"No reachable camera feed on the Pi (tried {VIDEO_CANDIDATES} on {PI_BASE_URL})")
    client = httpx.AsyncClient(timeout=None)
    req = client.build_request("GET", url)
    resp = await client.send(req, stream=True)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame")

    async def generator():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return generator, content_type


async def diagnostics():
    """Deep, on-demand check of the camera pipeline for the UI diagnostics panel."""
    status_ok, status = await check_status()
    video_url = await resolve_video_url(force=True)
    stream_ok, ctype = (await _probe(video_url)) if video_url else (False, None)

    camera_detected = None
    if status_ok and isinstance(status, dict) and status.get("cameras"):
        camera_detected = any(
            (c.get("status", "online") == "online") if isinstance(c, dict) else True
            for c in status["cameras"]
        )
    if camera_detected is None:
        camera_detected = stream_ok

    errors = []
    if not status_ok:
        errors.append(f"/status not reachable at {PI_BASE_URL} (optional; used for metrics/events).")
    if not video_url:
        errors.append(f"No MJPEG feed found — probed {VIDEO_CANDIDATES} on {PI_BASE_URL}. "
                      "Set PI_VIDEO_URL to the exact feed if it uses a non-standard path.")
    elif not stream_ok:
        errors.append(f"Feed {video_url} did not return a frame.")

    return {
        "pi_base_url": PI_BASE_URL,
        "video_url": video_url,
        "camera_detected": bool(camera_detected),
        "camera_service_running": bool(status_ok or stream_ok),
        "status_endpoint_reachable": bool(status_ok),
        "stream_endpoint_reachable": bool(stream_ok),
        "frame_received": bool(stream_ok),
        "stream_content_type": ctype,
        "backend_connected_to_pipeline": bool(stream_ok),
        "errors": errors,
    }


PI_CAMERA = {"id": "pi-camera", "name": CAMERA_NAME, "location": "", "status": "online",
             "stream_url": None, "thumbnail_url": None, "is_demo": False}
