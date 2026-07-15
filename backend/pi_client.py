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
