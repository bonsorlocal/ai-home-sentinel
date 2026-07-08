# AI Home Sentinel — Pi Integration Contract

This web app (React + FastAPI + MongoDB) is the **dashboard + brain**. Your existing
Raspberry Pi 5 Python stack (Flask, Picamera2, OpenCV, `face_recognition`/dlib, motion
detection, Vosk, **working DVR**) is the **Pi agent**. The Pi agent PUSHES data to the
backend over the REST endpoints below.

- Base URL: value of `REACT_APP_BACKEND_URL` (e.g. `https://your-app.preview.emergentagent.com`)
- All Pi endpoints are under `/api/pi/*`.
- Everything the Pi posts is stored with `is_demo=false`, so real data is visually
  distinguished from the seeded demo/mock data (purple `DEMO` tag in the UI).
- Keep your **DVR recording, segmenting and overwrite logic on the Pi** (local/private).
  The Pi only posts *segment metadata + AI summary*; raw video stays local and is served
  via your Pi's own `/video_feed` or a segment URL you include.

## Recommended Pi loop (respect Pi limits)
1. Motion gating -> only run inference on motion.
2. Frame skip / reduced inference FPS (see `GET /api/settings` -> `inference_fps`, `motion_gating`).
3. On detection: run `face_recognition` + object detection locally.
4. POST an event. For continuous DVR, when a segment closes, POST its metadata.
5. Heartbeat every ~10s.

---

## Endpoints

### POST /api/pi/heartbeat
Report device health (drives Dashboard/Settings health widgets; sets `pi_connected=true`).
```json
{ "cpu_percent": 34.2, "temp_c": 51.8, "memory_percent": 60.1,
  "disk_percent": 47.0, "uptime_seconds": 187200, "cameras_online": 3 }
```

### POST /api/pi/cameras
Register/update a camera (id is your stable camera id).
```json
{ "id": "cam-porch", "name": "Front Porch", "location": "Front entrance",
  "status": "online", "stream_url": "http://pi.local:5000/video_feed", "thumbnail_url": null }
```

### POST /api/pi/events
Post a detection/event. `type` ∈ `motion | person | object | unknown_person`.
Separate FACT (`ai_summary`) from INFERENCE (`ai_interpretation`).
```json
{ "camera_id": "cam-porch", "camera_name": "Front Porch", "type": "unknown_person",
  "person_id": null, "person_name": null, "known": false,
  "objects": ["person","backpack"], "confidence": 0.91, "importance": "high",
  "ai_summary": "Unknown person at Front Porch, no face match.",
  "ai_interpretation": "Lingered ~40s without ringing the bell.",
  "tags": ["unknown","front-door","loitering"],
  "thumbnail_url": "http://pi.local/snap/123.jpg", "clip_url": null,
  "saved": true, "timestamp": "2026-07-08T23:42:00Z" }
```
Set `saved: true` to also promote it into the **Event Log** (important footage). Omit
`timestamp` to use server time.

### POST /api/pi/dvr/segment
Post metadata for a closed continuous-DVR segment (15/30/60 min per `dvr_segment_minutes`).
```json
{ "camera_id": "cam-porch", "camera_name": "Front Porch",
  "start_time": "2026-07-08T14:00:00Z", "end_time": "2026-07-08T14:30:00Z",
  "length_minutes": 30, "ai_summary": "Quiet; Jane arrived once.",
  "tags": ["quiet","known"], "people": ["Jane Doe"], "objects": ["person","car"],
  "linked_event_ids": ["<event-id>"], "thumbnail_url": "http://pi.local/seg/1.jpg" }
```

### POST /api/pi/people/{person_id}/encodings?encodings_count=8
Report how many multi-angle face encodings you hold for a person (enrollment progress).

---

## App-facing endpoints (read by the UI, also usable by the Pi)
- `GET /api/settings`, `PUT /api/settings` — mode, processing_mode, dvr_segment_minutes, retention, toggles.
- `GET /api/events?saved=&camera_id=&type=&person_id=&search=` — Live/Event Log feed.
- `GET /api/dvr/segments?camera_id=&search=` — DVR segment list.
- `GET /api/people`, `POST/PUT/DELETE /api/people` — enrolled people.
- `GET /api/rules`, `POST/PUT/DELETE /api/rules` — rules/patterns.
- `POST /api/assistant/chat` `{session_id, message}` — Sentinel AI (Event-Log-first, DVR fallback, Gemini 3.1 Pro).

## Sentinel AI retrieval order (implemented in `backend/sentinel_ai.py`)
1. Parse question into terms (people/objects/location/time).
2. Search **Event Log first** (ranked; saved + high-importance boosted).
3. If thin (<3 hits), fall back to **DVR segment summaries/tags/metadata**.
4. Build grounded context with `[source: event:id]` / `[source: dvr:id]` refs.
5. Gemini answers, separating FACT vs INFERENCE, with confidence + source links.
