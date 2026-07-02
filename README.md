# AI Home Sentinel — Pi-based home security camera

Capture → motion → YOLO detection → face recognition → event ledger → reasoner → dashboard. Optional Grok "brain" for chat and daily recap. Phase 7b adds phone push alerts for tier-2 events via [ntfy.sh](https://ntfy.sh).

## Quick start (laptop / dev)

```powershell
# Windows
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
# CPU PyTorch first on Pi or Windows (avoids huge CUDA wheels):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install ultralytics
```

```bash
# Linux / macOS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

1. Copy `secrets.yaml.example` → `secrets.yaml` and add your `grok_api_key` (optional, for brain chat).
2. Edit `config.yaml` as needed.
3. Run: `python run.py` → open http://localhost:5000

Run tests: `python -m pytest tests/`

> **Note:** `face_recognition` is hard to build on Windows; it runs on the Pi. YOLO model tests are skipped unless `models/yolo_nano.pt` exists.

## Raspberry Pi setup

### 1. System packages (camera)

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-venv cmake build-essential \
  libopenblas-dev liblapack-dev
```

### 2. Python venv (must see Picamera2)

```bash
cd /home/sentinel/ai-home-sentinel
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### 3. YOLO model

Place `models/yolo_nano.pt` in the project, or let ultralytics download on first run.

### 4. secrets.yaml

```bash
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml — never commit this file
```

| Key | Purpose |
|-----|---------|
| `grok_api_key` | xAI Grok API key for brain chat/recap |
| `ntfy_auth_token` | Optional; only for private ntfy.sh topics |

### 5. Known faces

Drop labeled photos in `data/known_faces/` — filename (without extension) becomes the person's name:

```
data/known_faces/
  jordan.jpg
  partner.jpg
```

Restart the service after adding faces. Unknown faces at night promote to tier 2.

### 6. Phone notifications (ntfy.sh)

1. Install the [ntfy app](https://ntfy.sh) on your phone.
2. Subscribe to a unique topic (e.g. `my-home-sentinel-x7k2`).
3. In `config.yaml`:

```yaml
notifications:
  enabled: true
  topic: "my-home-sentinel-x7k2"
  cooldown_seconds: 60
  notify_on_tier: 2
```

Tier-2 events (unknown face, repeat person, night person) trigger a push within the cooldown window.

### 7. systemd service

```bash
sudo cp sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel
```

Working directory: `/home/sentinel/ai-home-sentinel` (see `sentinel.service`).

Verify: `curl http://localhost:5000/health` and `python scripts/smoke_brain.py`

See [docs/DEPLOY.md](docs/DEPLOY.md) for deploy steps from your laptop.

## Dashboard token auth

Set in `config.yaml`:

```yaml
dashboard:
  require_token: true
  token: "your-secret-token"
```

Pass the token via query string (`?token=...`), header `Authorization: Bearer ...`, or `X-Sentinel-Token`. `/health` stays open for monitoring.

## Voice queries (testing)

The dashboard uses your browser's built-in speech tools (no extra Pi packages):

1. Open the dashboard in **Chrome or Edge** (best support).
2. Tap the **microphone** button in "Ask the Sentinel".
3. Speak your question — Sentinel shows the text and **speaks the answer aloud**.

Settings in `config.yaml`:

```yaml
voice:
  enabled: true              # spoken replies (set false for text-only)
  speak_text_queries: false  # set true to also speak typed answers
  wake_word_enabled: false   # future: always-on wake word
  wake_word: "hey sentinel"
  language: "en-US"
```

Wake-word listening is planned for a later phase; for now use the mic button.

## Project layout

| Path | Purpose |
|------|---------|
| `run.py` | Entry point |
| `config.yaml` | All settings |
| `secrets.yaml` | API keys (git-ignored) |
| `sentinel/` | Core modules |
| `web/` | Dashboard templates/static |
| `tests/` | pytest suite |
| `scripts/smoke_brain.py` | Pi brain smoke test |

## Pi-only behavior (not testable on Windows)

- Picamera2 capture
- `face_recognition` / dlib
- Live camera + full detection pipeline under systemd

## Quick recovery checklist (plain-English)

Use this whenever the dashboard shows something "off."

1. Open `/status` and check `runtime` messages first.
2. If camera is unstable on Windows USB webcams, restart `python run.py`; camera reconnects automatically if possible.
3. If detector is off with a torch/DLL message, reinstall CPU torch first:
   - `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`
   - `pip install ultralytics`
4. If face recognition is off on Windows, this is expected unless `face_recognition` is installed successfully. Full support is on Pi.
5. For Phase 8 clip context:
   - In `config.yaml`, confirm `clips.enabled: true` and `video_metadata.enabled: true`.
   - Trigger motion, then open `/api/events` and look for:
     - `clip_path` (clip saved)
     - `clip_url` (clip playback endpoint)
     - `entities.clip_analysis` (scene summary metadata)
6. If clip analysis is missing:
   - confirm `secrets.yaml` has `grok_api_key`
   - check `runtime.video_metadata.available` and `calls_remaining` in `/status`
7. Before Pi deployment, run tests: `python -m pytest tests/`
8. Deploy to Pi with `scripts/deploy_pi.ps1` (Windows) or `scripts/deploy_pi.sh` (Linux/macOS).
