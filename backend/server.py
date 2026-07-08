"""AI Home Sentinel — FastAPI backend.
- App-facing REST API for all 8 pages.
- Pi Integration contract (/api/pi/*) so the existing Cursor/Python/Picamera2/face_recognition
  agent can POST real detections, events, DVR segments and health. Real data (is_demo=False)
  mixes in with the labeled demo data automatically.
See PI_INTEGRATION.md for the full contract.
"""
from fastapi import FastAPI, APIRouter, HTTPException
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
import seed as seed_module
import sentinel_ai

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("sentinel")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="AI Home Sentinel API")
api = APIRouter(prefix="/api")

NO_ID = {"_id": 0}


# ---------------- Startup: seed demo data if empty ----------------
@app.on_event("startup")
async def seed_if_empty():
    if await db.settings.count_documents({}) == 0:
        data = seed_module.build_seed()
        await db.cameras.insert_many(data["cameras"])
        await db.people.insert_many(data["people"])
        await db.events.insert_many(data["events"])
        await db.dvr_segments.insert_many(data["segments"])
        await db.rules.insert_many(data["rules"])
        await db.settings.insert_one(data["settings"])
        logger.info("Seeded demo data.")


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


HEARTBEAT_TTL_SECONDS = 30  # Pi is considered offline if no heartbeat within this window


@api.get("/system/health")
async def system_health():
    hb = await db.system_health.find_one({"id": "latest"}, NO_ID)
    cams_online = await db.cameras.count_documents({"status": "online"})
    cams_total = await db.cameras.count_documents({})
    settings = await db.settings.find_one({"id": "system"}, NO_ID) or {}
    pi_connected = False
    if hb and hb.get("received_at"):
        try:
            last = datetime.fromisoformat(hb["received_at"])
            age = (datetime.now(timezone.utc) - last).total_seconds()
            pi_connected = age <= HEARTBEAT_TTL_SECONDS
        except ValueError:
            pi_connected = False
    if not pi_connected:
        # No Pi heartbeat yet -> labeled demo/simulated metrics.
        hb = {"cpu_percent": 34.0, "temp_c": 52.4, "memory_percent": 61.0,
              "disk_percent": 47.0, "uptime_seconds": 187200, "cameras_online": cams_online}
    return {
        "pi_connected": pi_connected,
        "is_demo": not pi_connected,
        "cameras_online": cams_online,
        "cameras_total": cams_total,
        "processing_mode": settings.get("processing_mode", "hybrid"),
        "mode": settings.get("mode", "home"),
        "metrics": hb,
    }


# ---------------- Cameras ----------------
@api.get("/cameras", response_model=List[Camera])
async def list_cameras():
    docs = await db.cameras.find({}, NO_ID).to_list(100)
    return [Camera(**d) for d in docs]


# ---------------- Events (Live + Event Log) ----------------
@api.get("/events", response_model=List[Event])
async def list_events(
    saved: Optional[bool] = None, camera_id: Optional[str] = None,
    type: Optional[str] = None, person_id: Optional[str] = None,
    known: Optional[bool] = None, search: Optional[str] = None, limit: int = 100,
):
    q = {}
    if saved is not None:
        q["saved"] = saved
    if camera_id:
        q["camera_id"] = camera_id
    if type:
        q["type"] = type
    if person_id:
        q["person_id"] = person_id
    if known is not None:
        q["known"] = known
    if search:
        rx = {"$regex": search, "$options": "i"}
        q["$or"] = [{"ai_summary": rx}, {"ai_interpretation": rx}, {"person_name": rx},
                    {"tags": rx}, {"objects": rx}, {"camera_name": rx}, {"type": rx}]
    docs = await db.events.find(q, NO_ID).sort("timestamp", -1).to_list(limit)
    return [Event(**d) for d in docs]


@api.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: str):
    d = await db.events.find_one({"id": event_id}, NO_ID)
    if not d:
        raise HTTPException(404, "Event not found")
    return Event(**d)


@api.post("/events", response_model=Event)
async def create_event(payload: EventCreate):
    data = payload.model_dump()
    if not data.get("timestamp"):
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
    ev = Event(**data)
    await db.events.insert_one(ev.model_dump())
    return ev


@api.patch("/events/{event_id}/save", response_model=Event)
async def toggle_save(event_id: str, saved: bool = True):
    await db.events.update_one({"id": event_id}, {"$set": {"saved": saved}})
    d = await db.events.find_one({"id": event_id}, NO_ID)
    if not d:
        raise HTTPException(404, "Event not found")
    return Event(**d)


# ---------------- DVR ----------------
@api.get("/dvr/segments", response_model=List[DVRSegment])
async def list_segments(camera_id: Optional[str] = None, search: Optional[str] = None, limit: int = 200):
    q = {}
    if camera_id:
        q["camera_id"] = camera_id
    if search:
        rx = {"$regex": search, "$options": "i"}
        q["$or"] = [{"ai_summary": rx}, {"tags": rx}, {"people": rx}, {"objects": rx}, {"camera_name": rx}]
    docs = await db.dvr_segments.find(q, NO_ID).sort("start_time", -1).to_list(limit)
    return [DVRSegment(**d) for d in docs]


@api.post("/dvr/segments", response_model=DVRSegment)
async def create_segment(payload: DVRSegmentCreate):
    seg = DVRSegment(**payload.model_dump())
    await db.dvr_segments.insert_one(seg.model_dump())
    return seg


# ---------------- People ----------------
@api.get("/people", response_model=List[Person])
async def list_people():
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


# ---------------- Sentinel AI ----------------
@api.post("/assistant/chat")
async def assistant_chat(req: ChatRequest):
    events = await db.events.find({}, NO_ID).sort("timestamp", -1).to_list(300)
    segments = await db.dvr_segments.find({}, NO_ID).sort("start_time", -1).to_list(300)
    people = await db.people.find({}, NO_ID).to_list(200)
    cameras = await db.cameras.find({}, NO_ID).to_list(100)
    rules = await db.rules.find({}, NO_ID).to_list(200)
    settings = await db.settings.find_one({"id": "system"}, NO_ID) or {}

    context, sources = sentinel_ai.build_context(req.message, events, segments, people, cameras, rules, settings)

    # persist user turn
    now = datetime.now(timezone.utc).isoformat()
    await db.chat.insert_one({"session_id": req.session_id, "role": "user", "content": req.message, "timestamp": now})
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
