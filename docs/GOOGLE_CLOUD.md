# Google Cloud setup for AI Home Sentinel

Sentinel uses **three Google surfaces**:

| Service | Auth | Purpose |
|---------|------|---------|
| **Gemini API** | `google_api_key` in `secrets.yaml` | Chat, keyframe analysis, live camera Q&A |
| **Video Intelligence** | Service account JSON | Fallback for heavy DVR footage questions |
| **Text-to-Speech** | Same service account | Spoken dashboard replies (`/api/voice/tts`) |

## What is a bucket name?

A **Cloud Storage bucket** is a folder in Google Cloud where large video files are stored temporarily.
Video Intelligence reads video from `gs://YOUR-BUCKET/path/to/file.mp4`.

If you leave `google.gcs_bucket` empty in `config.yaml`, Sentinel defaults to:

```
{your-project-id}-sentinel-video
```

Create it once with:

```bash
chmod +x scripts/setup_gcp_bucket.sh
./scripts/setup_gcp_bucket.sh YOUR_PROJECT_ID us-west1
```

## Quick setup checklist

### 1. Gemini (required first)

1. [Google AI Studio → API keys](https://aistudio.google.com/apikey)
2. Add to `secrets.yaml`:
   ```yaml
   google_api_key: "AIza..."
   ```

### 2. GCP project (Video Intelligence + TTS)

1. [Google Cloud Console](https://console.cloud.google.com) — use your existing project
2. Enable **billing**
3. Run `scripts/setup_gcp_bucket.sh YOUR_PROJECT_ID` (enables APIs + creates bucket)

### 3. Service account

1. IAM → Service accounts → Create `sentinel-pi`
2. Roles: **Storage Object Admin**, **Video Intelligence Admin**, **Cloud Text-to-Speech User**
3. Create JSON key → save as `secrets/gcp-service-account.json` on the Pi
4. `chmod 600 secrets/gcp-service-account.json`

### 4. config.yaml

```yaml
google:
  project_id: "YOUR_PROJECT_ID"
  gcs_bucket: "YOUR_PROJECT_ID-sentinel-video"   # or leave empty for default
  credentials_file: "secrets/gcp-service-account.json"
  video_intelligence:
    enabled: true
    fallback_only: true
    daily_job_cap: 3
  text_to_speech:
    enabled: true

voice:
  tts_provider: "auto"   # uses Google TTS when credentials exist
```

### 5. Verify on Pi

```bash
sudo systemctl restart sentinel
curl -s http://localhost:5000/status | python3 -m json.tool | grep -A20 '"google"'
```

Look for:

- `runtime.google.video_intelligence.available: true`
- `runtime.google.text_to_speech.available: true`
- `voice.tts_provider: "google"`

## How Video Intelligence fallback works

1. User asks a heavy question (e.g. “what happened to my package?”)
2. Sentinel runs **Gemini keyframe analysis** on matching DVR segment(s)
3. If results are weak or empty → **one** Video Intelligence job on the first segment (daily cap applies)
4. Answer uses combined context

Small clips (≤9 MB) upload inline; larger files go to your GCS bucket first.

## Cursor / Agent setup

In Agent mode you can ask Cursor to:

- Run `gcloud` commands after you `gcloud auth login`
- Deploy updated code to the Pi
- Fill `config.yaml` with your project ID

You still need to create the Gemini API key and download the service account JSON once in the browser.
