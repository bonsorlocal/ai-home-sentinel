# AI Home Sentinel — PRD

## Original Problem Statement
MVP for "AI Home Sentinel": AI security/home-awareness system (Raspberry Pi 5 backend, built in Cursor).
Monitors cameras, recognizes known/unknown people, detects motion/objects, saves searchable events/video,
answers natural-language questions grounded in its own data, learns routines, detects unusual activity.
Wake word "Sentinel". Separate facts / detections / inference. 8 pages required.

## User Choices
- Assistant model: **Gemini 3.1 Pro** (gemini-3.1-pro-preview) via EMERGENT_LLM_KEY.
- Voice I/O: **deferred** to later phase (toggle present, engine not built).
- Auth: **none** (single-user local system).
- Theme: dark "control room" (design_agent guidelines).
- Delivery: full web app in cloud (React+FastAPI+MongoDB) with labeled DEMO data + a Pi Integration
  API contract for the user to wire their real Cursor/Python/Pi/DVR code into later.

## Architecture
- Frontend: React (CRA/craco), Tailwind, Phosphor icons, react-router, sonner. Pages in `frontend/src/pages/*`.
- Backend: FastAPI modular — `server.py` (routes + Pi contract), `models.py`, `seed.py` (labeled demo data),
  `sentinel_ai.py` (Event-Log-first → DVR-fallback retrieval + Gemini grounding).
- DB: MongoDB, uuid string ids, datetimes as ISO strings, `is_demo` flag distinguishes demo vs real Pi data.
- Pi contract: `/api/pi/heartbeat|events|dvr/segment|cameras|people/{id}/encodings` (see `PI_INTEGRATION.md`).
  Heartbeat has 30s TTL → reverts to demo mode when Pi offline.

## Implemented (2026-07-08)
- All 8 pages: Dashboard, Live Events, Event Log, DVR (segment 15/30/60 + retention controls, timeline,
  segment search/detail), People (CRUD + trust status), Sentinel AI (grounded chat + source citations),
  Rules/Patterns (CRUD + toggle + honest "learning scaffolded not trained" note), Settings/System.
- Sentinel AI: Event-Log-first then DVR-fallback retrieval, FACT vs INFERENCE separation, confidence, sources.
- Pi Integration Contract + README + PI_INTEGRATION.md with beginner run/test instructions.
- Backend tested: 15/15 endpoints passing.

## Backlog / Next
- P1: Automated frontend Playwright pass (backend fully tested; dashboard visually verified only).
- P1: Real video streaming/playback (wire Pi `stream_url`/`clip_url`; DVR playback currently mock frames).
- P2: Voice I/O — wake word "Sentinel", Vosk STT, Web Speech TTS (deferred by user).
- P2: Statistical routine-learning / anomaly baseline engine (architecture scaffolded via `scheduled_expectation` rules).
- P2: WebSocket push for true real-time Live Events (currently polling).
- P2: Rule evaluation engine on Pi agent side + proactive notifications.

## Known Notes
- Camera/event/DVR/people data is seeded DEMO (purple DEMO tag) until Pi posts real data.
- CORS `*` with credentials is harmless here (no auth).
