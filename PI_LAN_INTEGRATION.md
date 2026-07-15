# AI Home Sentinel — Pi LAN (Live-Data) Mode

This mode runs the Emergent web app **on the Raspberry Pi** and shows **only live data**
from the on-Pi Sentinel camera stack. There is **no demo/mock data** and **no demo fallback**.

```
Browser ──/api/*──►  Emergent backend (Pi :8080)  ──proxy──►  Pi Sentinel stack (127.0.0.1:5000)
        (same origin, never hits the Pi directly)             (Flask / Picamera2 / OpenCV / face_recognition / DVR)
```

## Environment (backend/.env)
```
PI_BASE_URL=http://127.0.0.1:5000            # Pi-local Sentinel service (PULL source)
EMERGENT_BACKEND_PUBLIC_URL=http://192.168.1.244:8080
EMERGENT_LLM_KEY=...                          # Gemini 3.1 Pro for Sentinel AI
MONGO_URL / DB_NAME                           # app config only (settings, rules, chat history)
```
Frontend build must set `REACT_APP_BACKEND_URL=""` (empty) so API calls are **relative `/api`**
(same origin as the app served on :8080). `deploy_emergent_pi.ps1` should build the frontend
with that empty value and serve the FastAPI backend on port 8080.

## Contract the Pi Sentinel service (:5000) MUST expose (PULL model)
The Emergent backend proxies these; shapes are best-effort normalized (see `pi_client.py`).

| Pi endpoint | Used by backend | Notes |
|---|---|---|
| `GET /status` | `/api/system/health`, `/api/cameras`, health-check | JSON: `cpu_percent, temp_c, memory_percent, disk_percent, uptime_seconds, mode, processing_mode, cameras:[{id,name,location,status}]` |
| `GET /video_feed?camera={id}` | `/api/pi/stream` | MJPEG `multipart/x-mixed-replace` |
| `GET /api/events` | `/api/events`, `/api/events/{id}`, chat | JSON list of events (Event schema: id,timestamp,camera_id,camera_name,type,person_name,known,objects,confidence,importance,ai_summary,ai_interpretation,tags,thumbnail_url,clip_url,saved) |
| `GET /api/events/{id}/save?saved=` | `PATCH /api/events/{id}/save` | promote/demote in Event Log |
| `GET /api/dvr/segments` | `/api/dvr/segments`, chat | JSON list of DVR segments |
| `GET /api/people` | `/api/people`, chat | optional; falls back to app store if absent |

## Live-data-first behavior
1. Every relevant request health-checks `PI_BASE_URL/status` (3s timeout).
2. **Pi healthy** → serve real camera stream + events + DVR + health (`pi_connected:true, is_demo:false`).
3. **Pi down** → explicit offline state (`offline:true, metrics:null`, empty lists). The UI shows a
   **PI OFFLINE** banner / empty states. **Never** demo content.

## Files changed in this reconfiguration
- `backend/.env` — `PI_BASE_URL=http://127.0.0.1:5000`, `EMERGENT_BACKEND_PUBLIC_URL`.
- `backend/pi_client.py` — **new**: Pi health-check, JSON pull, MJPEG stream proxy, status normalizer.
- `backend/server.py` — startup no longer seeds demo (purges any `is_demo` rows); `system_health`,
  `cameras`, `events`, `events/{id}`, `events/{id}/save`, `dvr/segments`, `people`, `assistant/chat`
  now pull live from the Pi; **new** `/api/pi/stream` and `/api/pi/status` proxies.
- `backend/requirements.txt` — added `httpx`.
- `frontend/src/lib/api.js` — `API_BASE` relative when `REACT_APP_BACKEND_URL` empty; `streamUrl()`.
- `frontend/src/components/common.jsx` — `DemoTag` now renders nothing; added `OfflineBanner`, `EmptyState`.
- `frontend/src/pages/Dashboard.jsx` — live MJPEG camera tiles, offline banner, live/real health.
- `frontend/src/pages/LiveEvents.jsx` — live events + offline/empty states, no demo badges.
- `frontend/src/pages/Settings.jsx` — device health reflects real `/status`, offline messaging.
- `tools/mock_pi.py` — **QA harness only** (stand-in Pi for cloud preview; NOT part of the app/deploy).

## Verified (testing agent, iteration_4: 100% backend + frontend)
- Pi up → live stream (HTTP 200 MJPEG), live events, real health, zero demo artifacts.
- Pi down → `offline:true`, empty data, PI OFFLINE UI, no fake/demo content.

> Note on real-hardware verification: the cloud preview has no physical Pi, so the live path was
> verified against `tools/mock_pi.py`. On the Pi, point `PI_BASE_URL` at your real `:5000` service.
