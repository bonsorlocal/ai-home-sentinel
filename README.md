# AI Home Sentinel — MVP

AI security / home-awareness dashboard. React (frontend) + FastAPI (backend) + MongoDB.
The **Raspberry Pi agent** (your existing Cursor/Python/Flask/Picamera2/OpenCV/face_recognition/DVR
code) pushes real data into this app via the **Pi Integration Contract** (see `PI_INTEGRATION.md`).

> ⚠️ **Demo data is labeled** with a purple `DEMO` tag everywhere in the UI. It is replaced
> automatically once your Pi agent posts real data (`is_demo=false`).

## Pages
1. **Dashboard** — live preview grid, current activity, unknown-person warning, mode switch (Home/Away/Night), recent events, system health, quick Ask-Sentinel.
2. **Live Events** — real-time detection stream (auto-refresh), confidence/importance, FACT vs AI inference, save-to-log & DVR links.
3. **Event Log** — saved important events, search/filter by keyword, camera, type, person; detail panel with clip placeholder.
4. **DVR** — continuous ~48h rolling recording, 15/30/60-min segments (changeable), timeline scrubber, segment list, AI summaries/tags/people/objects/linked events, search.
5. **People** — add/edit/remove, relationship, trusted/normal/watch, encodings (from Pi), recognition history, last seen.
6. **Sentinel AI** — grounded assistant (Gemini 3.1 Pro). Event-Log-first → DVR-fallback retrieval, source citations, follow-ups.
7. **Rules / Patterns** — deterministic triggers + scaffolded (not faked) routine/anomaly architecture.
8. **Settings / System** — local/hybrid/cloud, inference FPS, motion gating, recognition, mic (deferred), privacy, DVR segment/retention, device health.

## Run / Test
Services are managed by **supervisor** (already running).

```bash
sudo supervisorctl status
sudo supervisorctl restart backend
tail -n 100 /var/log/supervisor/backend.err.log
curl $REACT_APP_BACKEND_URL/api/system/health
curl -X POST $REACT_APP_BACKEND_URL/api/assistant/chat -H "Content-Type: application/json" \
  -d '{"session_id":"test","message":"Did any packages arrive today?"}'
```

- Backend: `server.py` (routes), `models.py`, `seed.py` (demo data), `sentinel_ai.py` (retrieval + Gemini).
- Frontend: `frontend/src/pages/*`, `components/*`, `lib/api.js`.
- Connecting your Pi: see **`PI_INTEGRATION.md`**.

## Deferred (future phases, intentionally not faked)
- Voice I/O / wake-word "Sentinel" (STT/TTS) — toggle exists, engine deferred.
- Statistical routine-learning / anomaly baselines — scaffolded via `scheduled_expectation` rules.
- Real video streaming/playback — DVR playback shows mock frames; wire `stream_url`/`clip_url` from Pi.
