"""AI Home Sentinel — FastAPI backend (LAN / Pi live-data mode).

Runs ON the Raspberry Pi (port 8080) and PROXIES the Pi-local Sentinel camera stack
(Flask at PI_BASE_URL, default http://127.0.0.1:5000). Live-data-first:
- /status is health-checked; when the Pi is up we serve ONLY real camera/events/DVR/health.
- When the Pi is down we return an explicit offline state (never demo/mock content).
The browser only ever calls /api/* on this backend (same origin) — never the Pi directly.
See PI_INTEGRATION.md for the pull contract the Pi service must expose.
"""
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
import os
import logging

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from models import (
    Camera, Person, PersonCreate, PersonUpdate, Event, EventCreate,
    DVRSegment, DVRSegmentCreate, Rule, RuleCreate, RuleUpdate,
    Settings, SettingsUpdate, Heartbeat, ChatRequest,
)
import pi_client
import sentinel_ai

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("sentinel")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="AI Home Sentinel API")
api = APIRouter(prefix="/api")

NO_ID = {"_id": 0}


# ---------------- Startup: live-data mode (NO demo seeding) ----------------
@app.on_event("startup")
async def startup():
    # Ensure a settings/config doc exists (app config, not mock content).
    if await db.settings.count_documents({"id": "system"}) == 0:
        await db.settings.insert_one(Settings().model_dump())
    # Purge any previously-seeded demo/mock records so LAN mode shows ONLY live Pi data.
    for coll in ("cameras", "events", "dvr_segments", "people", "rules"):
        res = await db[coll].delete_many({"is_demo": True})
        if res.deleted_count:
            logger.info(f"Removed {res.deleted_count} demo records from {coll}.")
    logger.info(f"Live-data mode. Pi stack: {pi_client.PI_BASE_URL}")


@api.get("/")
async def root():
    return {"service": "AI Home Sentinel", "status": "ok"}


# ---------------- System / Settings / Health ----------------
@api.get("/settings", response_model=Settings)
async def get_settings():
    s = await db.settings.find_one({"id": "system"}, NO_ID)
    if not s:
        s = Settings().model_dump()
        await db.settings.insert_one(s)
    return Settings(**s)


@api.put("/settings", response_model=Settings)
async def update_settings(patch: SettingsUpdate):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one({"id": "system"}, {"$set": updates}, upsert=True)
    s = await db.settings.find_one({"id": "system"}, NO_ID)
    return Settings(**s)


HEARTBEAT_TTL_SECONDS = 30  # (legacy push contract) still supported for Pi-push agents


@api.get("/system/health")
async def system_health():
    """Live-data-first: reflect the real Pi /status. Never returns demo metrics."""
    settings = await db.settings.find_one({"id": "system"}, NO_ID) or {}
    online, raw = await pi_client.check_status()
    if not online:
        return {
            "pi_connected": False,
            "is_demo": False,
            "offline": True,
            "pi_base_url": pi_client.PI_BASE_URL,
            "cameras_online": 0,
            "cameras_total": 0,
            "processing_mode": settings.get("processing_mode", "hybrid"),
            "mode": settings.get("mode", "home"),
            "metrics": None,
        }
    norm = pi_client.normalize_status(raw)
    return {
        "pi_connected": True,
        "is_demo": False,
        "offline": False,
        "pi_base_url": pi_client.PI_BASE_URL,
        "cameras_online": norm["cameras_online"],
        "cameras_total": norm["cameras_total"],
        "processing_mode": norm["processing_mode"] or settings.get("processing_mode", "hybrid"),
        "mode": norm["mode"] or settings.get("mode", "home"),
        "metrics": norm["metrics"],
        "raw_status": raw,
    }


# ---------------- Live camera stream proxy (browser -> backend -> Pi) ----------------
@api.get("/pi/status")
async def pi_status_proxy():
    online, raw = await pi_client.check_status()
    if not online:
        raise HTTPException(503, "Pi Sentinel stack offline")
    return raw


@api.get("/pi/stream")
async def pi_stream_proxy(camera: Optional[str] = None):
    """Proxy the Pi MJPEG feed so the browser stays same-origin (never hits the Pi/LAN)."""
    path = "/video_feed" + (f"?camera={camera}" if camera else "")
    try:
        generator, content_type = await pi_client.open_stream(path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Stream proxy failed: {e}")
        raise HTTPException(503, "Camera stream unavailable — Pi offline")
    return StreamingResponse(generator(), media_type=content_type,
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- Cameras (live from Pi /status) ----------------
@api.get("/cameras")
async def list_cameras():
    online, raw = await pi_client.check_status()
    if not online:
        return []
    return pi_client.normalize_status(raw)["cameras"]


# ---------------- Events (Live + Event Log) — proxied from Pi ----------------
def _match(ev, saved, camera_id, type, person_id, known, search):
    if saved is not None and bool(ev.get("saved")) != saved:
        return False
    if camera_id and ev.get("camera_id") != camera_id:
        return False
    if type and ev.get("type") != type:
        return False
    if person_id and ev.get("person_id") != person_id:
        return False
    if known is not None and bool(ev.get("known")) != known:
        return False
    if search:
        blob = " ".join(str(ev.get(k, "")) for k in
                        ("ai_summary", "ai_interpretation", "person_name", "camera_name", "type"))
        blob += " " + " ".join(map(str, ev.get("tags", []) or [])) + " " + " ".join(map(str, ev.get("objects", []) or []))
        if search.lower() not in blob.lower():
            return False
    return True


@api.get("/events")
async def list_events(
    saved: Optional[bool] = None, camera_id: Optional[str] = None,
    type: Optional[str] = None, person_id: Optional[str] = None,
    known: Optional[bool] = None, search: Optional[str] = None, limit: int = 100,
):
    data = pi_client.as_list(await pi_client.get_json("/api/events"), "events")
    if data is None:
        return []
    events = [e for e in data if _match(e, saved, camera_id, type, person_id, known, search)]
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:limit]


@api.get("/events/{event_id}")
async def get_event(event_id: str):
    data = pi_client.as_list(await pi_client.get_json("/api/events"), "events") or []
    for e in data:
        if e.get("id") == event_id:
            return e
    raise HTTPException(404, "Event not found (Pi offline or unknown id)")


@api.patch("/events/{event_id}/save")
async def toggle_save(event_id: str, saved: bool = True):
    """Promote/demote an event in the Event Log — forwarded to the Pi."""
    res = await pi_client.get_json(f"/api/events/{event_id}/save?saved={str(saved).lower()}")
    if res is None:
        raise HTTPException(503, "Pi offline — cannot update event")
    return res


# ---------------- DVR (proxied from Pi) ----------------
@api.get("/dvr/segments")
async def list_segments(camera_id: Optional[str] = None, search: Optional[str] = None, limit: int = 200):
    data = pi_client.as_list(await pi_client.get_json("/api/dvr/segments"), "segments")
    if data is None:
        return []
    segs = data
    if camera_id:
        segs = [s for s in segs if s.get("camera_id") == camera_id]
    if search:
        sl = search.lower()
        def hit(s):
            blob = " ".join([str(s.get("ai_summary", "")), str(s.get("camera_name", "")),
                             " ".join(map(str, s.get("tags", []) or [])),
                             " ".join(map(str, s.get("people", []) or [])),
                             " ".join(map(str, s.get("objects", []) or []))])
            return sl in blob.lower()
        segs = [s for s in segs if hit(s)]
    segs.sort(key=lambda s: s.get("start_time", ""), reverse=True)
    return segs[:limit]


# ---------------- People ----------------
@api.get("/people")
async def list_people():
    """People/encodings come from the Pi face_recognition backend when available."""
    data = pi_client.as_list(await pi_client.get_json("/api/people"), "people")
    if data is not None:
        return data
    # Pi has no people endpoint or is offline -> app-managed (non-demo) store.
    docs = await db.people.find({}, NO_ID).sort("created_at", -1).to_list(200)
    return [Person(**d) for d in docs]


@api.post("/people", response_model=Person)
async def create_person(payload: PersonCreate):
    p = Person(**payload.model_dump(), is_demo=False)
    await db.people.insert_one(p.model_dump())
    return p


@api.put("/people/{person_id}", response_model=Person)
async def update_person(person_id: str, patch: PersonUpdate):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    r = await db.people.update_one({"id": person_id}, {"$set": updates})
    if r.matched_count == 0:
        raise HTTPException(404, "Person not found")
    d = await db.people.find_one({"id": person_id}, NO_ID)
    return Person(**d)


@api.delete("/people/{person_id}")
async def delete_person(person_id: str):
    r = await db.people.delete_one({"id": person_id})
    if r.deleted_count == 0:
        raise HTTPException(404, "Person not found")
    return {"deleted": person_id}


# ---------------- Rules / Patterns ----------------
@api.get("/rules", response_model=List[Rule])
async def list_rules():
    docs = await db.rules.find({}, NO_ID).to_list(200)
    return [Rule(**d) for d in docs]


@api.post("/rules", response_model=Rule)
async def create_rule(payload: RuleCreate):
    r = Rule(**payload.model_dump(), is_demo=False)
    await db.rules.insert_one(r.model_dump())
    return r


@api.put("/rules/{rule_id}", response_model=Rule)
async def update_rule(rule_id: str, patch: RuleUpdate):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    res = await db.rules.update_one({"id": rule_id}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(404, "Rule not found")
    d = await db.rules.find_one({"id": rule_id}, NO_ID)
    return Rule(**d)


@api.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    r = await db.rules.delete_one({"id": rule_id})
    if r.deleted_count == 0:
        raise HTTPException(404, "Rule not found")
    return {"deleted": rule_id}


# ---------------- Sentinel AI (grounded in LIVE Pi data) ----------------
@api.post("/assistant/chat")
async def assistant_chat(req: ChatRequest):
    online, raw = await pi_client.check_status()
    now = datetime.now(timezone.utc).isoformat()
    await db.chat.insert_one({"session_id": req.session_id, "role": "user", "content": req.message, "timestamp": now})
    if not online:
        msg = ("⚠️ The Pi Sentinel stack is offline, so I have no live data to reason over. "
               "Please bring the Pi camera service online and try again.")
        await db.chat.insert_one({"session_id": req.session_id, "role": "assistant", "content": msg,
                                  "sources": [], "offline": True, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"answer": msg, "sources": [], "offline": True}

    events = pi_client.as_list(await pi_client.get_json("/api/events"), "events") or []
    segments = pi_client.as_list(await pi_client.get_json("/api/dvr/segments"), "segments") or []
    people = pi_client.as_list(await pi_client.get_json("/api/people"), "people") or []
    cameras = pi_client.normalize_status(raw)["cameras"]
    rules = await db.rules.find({}, NO_ID).to_list(200)
    settings = await db.settings.find_one({"id": "system"}, NO_ID) or {}

    context, sources = sentinel_ai.build_context(req.message, events, segments, people, cameras, rules, settings)
    try:
        answer = await sentinel_ai.ask_sentinel(req.session_id, req.message, context)
    except Exception as e:
        logger.exception("Sentinel AI error")
        raise HTTPException(500, f"AI error: {e}")
    await db.chat.insert_one({"session_id": req.session_id, "role": "assistant", "content": answer,
                              "sources": sources, "timestamp": datetime.now(timezone.utc).isoformat()})
    return {"answer": answer, "sources": sources}


@api.get("/assistant/history")
async def assistant_history(session_id: str):
    docs = await db.chat.find({"session_id": session_id}, NO_ID).sort("timestamp", 1).to_list(500)
    return docs


# ================= PI INTEGRATION CONTRACT =================
# The Raspberry Pi agent (Python/Flask/Picamera2/OpenCV/face_recognition/Vosk) posts here.
# All records are stored with is_demo=False so real data is distinguishable from demo data.

@api.post("/pi/heartbeat")
async def pi_heartbeat(hb: Heartbeat):
    doc = hb.model_dump()
    doc["id"] = "latest"
    doc["received_at"] = datetime.now(timezone.utc).isoformat()
    await db.system_health.update_one({"id": "latest"}, {"$set": doc}, upsert=True)
    return {"ok": True}


@api.post("/pi/cameras", response_model=Camera)
async def pi_register_camera(cam: Camera):
    cam.is_demo = False
    await db.cameras.update_one({"id": cam.id}, {"$set": cam.model_dump()}, upsert=True)
    return cam


@api.post("/pi/events", response_model=Event)
async def pi_event(payload: EventCreate):
    """Pi posts a detection/event (motion, person, unknown_person, object)."""
    data = payload.model_dump()
    data["is_demo"] = False
    if not data.get("timestamp"):
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
    ev = Event(**data)
    await db.events.insert_one(ev.model_dump())
    return ev


@api.post("/pi/dvr/segment", response_model=DVRSegment)
async def pi_dvr_segment(payload: DVRSegmentCreate):
    """Pi posts a completed DVR segment (with AI summary/tags/people/objects)."""
    data = payload.model_dump()
    seg = DVRSegment(**data)
    seg.is_demo = False
    await db.dvr_segments.insert_one(seg.model_dump())
    return seg


@api.post("/pi/people/{person_id}/encodings")
async def pi_update_encodings(person_id: str, encodings_count: int):
    """Pi reports how many face encodings it holds for a person (multi-angle enrollment)."""
    r = await db.people.update_one({"id": person_id}, {"$set": {"encodings_count": encodings_count}})
    if r.matched_count == 0:
        raise HTTPException(404, "Person not found")
    return {"ok": True, "person_id": person_id, "encodings_count": encodings_count}


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
