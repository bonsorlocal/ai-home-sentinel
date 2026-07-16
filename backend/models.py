"""Pydantic models for AI Home Sentinel. UUID string ids; datetimes stored as ISO strings."""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
import uuid


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Cameras ----------
class Camera(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    location: str
    status: str = "online"  # online | offline
    stream_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    is_demo: bool = True


# ---------- People ----------
class Person(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    relationship: Optional[str] = None
    status: str = "normal"  # trusted | normal | watch
    photo_url: Optional[str] = None
    encodings_count: int = 0        # supplied by Pi/Python face_recognition backend
    recognition_count: int = 0
    last_seen: Optional[str] = None
    notes: Optional[str] = None
    is_demo: bool = True
    created_at: str = Field(default_factory=_now)


class PersonCreate(BaseModel):
    name: str
    relationship: Optional[str] = None
    status: str = "normal"
    photo_url: Optional[str] = None
    notes: Optional[str] = None


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    relationship: Optional[str] = None
    status: Optional[str] = None
    photo_url: Optional[str] = None
    notes: Optional[str] = None


# ---------- Events (Live + Event Log) ----------
class Event(BaseModel):
    id: str = Field(default_factory=_uid)
    timestamp: str = Field(default_factory=_now)
    camera_id: str
    camera_name: str
    type: str  # motion | person | object | unknown_person
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    known: bool = False
    objects: List[str] = []
    confidence: float = 0.0          # 0..1
    importance: str = "low"          # low | medium | high | critical
    ai_interpretation: Optional[str] = None   # inference, separate from raw detection
    ai_summary: Optional[str] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    clip_url: Optional[str] = None
    saved: bool = False              # promoted to Event Log
    is_demo: bool = True


class EventCreate(BaseModel):
    camera_id: str
    camera_name: str
    type: str
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    known: bool = False
    objects: List[str] = []
    confidence: float = 0.0
    importance: str = "low"
    ai_interpretation: Optional[str] = None
    ai_summary: Optional[str] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    clip_url: Optional[str] = None
    saved: bool = False
    timestamp: Optional[str] = None
    is_demo: bool = False


# ---------- DVR ----------
class DVRSegment(BaseModel):
    id: str = Field(default_factory=_uid)
    camera_id: str
    camera_name: str
    start_time: str
    end_time: str
    length_minutes: int = 30
    ai_summary: Optional[str] = None
    tags: List[str] = []
    people: List[str] = []
    objects: List[str] = []
    linked_event_ids: List[str] = []
    thumbnail_url: Optional[str] = None
    is_demo: bool = True


class DVRSegmentCreate(BaseModel):
    camera_id: str
    camera_name: str
    start_time: str
    end_time: str
    length_minutes: int = 30
    ai_summary: Optional[str] = None
    tags: List[str] = []
    people: List[str] = []
    objects: List[str] = []
    linked_event_ids: List[str] = []
    thumbnail_url: Optional[str] = None
    is_demo: bool = False


# ---------- Rules / Patterns ----------
class Rule(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    description: str
    trigger_type: str  # unknown_after_hours | package_arrival | loitering | scheduled_expectation | mode_violation
    active: bool = True
    severity: str = "medium"  # low | medium | high | critical
    schedule: Optional[str] = None
    mode: Optional[str] = None
    triggered_count: int = 0
    last_triggered: Optional[str] = None
    is_demo: bool = True


class RuleCreate(BaseModel):
    name: str
    description: str
    trigger_type: str
    active: bool = True
    severity: str = "medium"
    schedule: Optional[str] = None
    mode: Optional[str] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    severity: Optional[str] = None
    schedule: Optional[str] = None
    mode: Optional[str] = None


# ---------- System settings & health ----------
class Settings(BaseModel):
    id: str = "system"
    mode: str = "home"              # home | away | night
    processing_mode: str = "hybrid" # local | hybrid | cloud
    dvr_segment_minutes: int = 30   # 15 | 30 | 60
    dvr_retention_hours: int = 48
    recognition_enabled: bool = True
    mic_enabled: bool = False
    motion_gating: bool = True
    inference_fps: int = 5
    privacy_cloud_upload: bool = True
    updated_at: str = Field(default_factory=_now)


class SettingsUpdate(BaseModel):
    mode: Optional[str] = None
    processing_mode: Optional[str] = None
    dvr_segment_minutes: Optional[int] = None
    dvr_retention_hours: Optional[int] = None
    recognition_enabled: Optional[bool] = None
    mic_enabled: Optional[bool] = None
    motion_gating: Optional[bool] = None
    inference_fps: Optional[int] = None
    privacy_cloud_upload: Optional[bool] = None


class Heartbeat(BaseModel):
    cpu_percent: float = 0.0
    temp_c: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    uptime_seconds: int = 0
    cameras_online: int = 0


# ---------- Assistant ----------
class ChatRequest(BaseModel):
    session_id: str
    message: str
