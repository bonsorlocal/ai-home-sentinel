# Connect Base44 to AI Home Sentinel

**Use the ready-to-import pack:** [../base44-import/START_HERE.md](../base44-import/START_HERE.md)

Base44 can build the webpage. Sentinel stays the camera + brain backend.

Base44 runs in the cloud, so it **cannot** use `http://localhost:5000`. You need a public HTTPS URL to Sentinel.

## What you need

1. Sentinel running (PC `:5000` or Pi).
2. A public tunnel URL to that `:5000` service.
3. A Base44 workspace that can add **custom integrations** (or backend functions).
4. This OpenAPI file: [openapi-sentinel.yaml](openapi-sentinel.yaml).

## Step-by-step

### 1. Start Sentinel

```powershell
cd C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi
.\scripts\run_pc.ps1
```

Or use the Pi `sentinel` service.

### 2. Publish a public URL

**Pi (recommended for always-on):**

```powershell
.\scripts\deploysshtunnel.ps1
```

Copy the printed `https://….trycloudflare.com` URL.

**PC-only demo:** run any HTTPS tunnel pointed at `http://127.0.0.1:5000` and use that URL.

Quick-tunnel URLs change when restarted — update Base44 when they do.

### 3. Create a Base44 custom integration

1. Base44 → workspace **Settings** → **Custom integrations** → **Add integration**.
2. Choose **Paste JSON** (or host the YAML at a public URL).
3. Paste the contents of `docs/openapi-sentinel.yaml` (or convert to JSON first if the UI requires JSON).
4. Select these operations (or all of them):
   - `getHealth`, `getStatus`
   - `listEvents`, `getLiveJpeg`
   - `getDvrStatus`, `getDvrRange`
   - `postChat`, `postDaySummary`
5. Set **Base URL** to your tunnel URL (no trailing slash issues — match what works in a browser).
6. If dashboard auth is on, add header:
   - Name: `X-Sentinel-Token`
   - Value: your `dashboard_token` from `secrets.yaml`
7. Slug example: `sentinel-api`. Save.

### 4. Prompt Base44 to build the page

Create a new app and paste something like:

> Build a home security dashboard using my workspace integration `sentinel-api`.
> Pages: Live (poll getLiveJpeg every 1–2s), Events (listEvents), DVR status, and a ChatGPT-style chat.
> Chat must call postChat with `{ question, history }` where history is the last 10 `{role, content}` turns so follow-ups like "why is that?" work.
> Do not invent camera events — only show data from the API.

Iterate in Base44 until the UI looks right.

### 5. Verify

From a browser or curl against the **tunnel** URL:

- `/health` → OK
- `/status` → camera/brain fields
- `POST /api/chat` with a short history → natural follow-up answers

## Alternate path (no OpenAPI UI)

If custom integrations are unavailable on your plan, use Base44 **backend functions** that `fetch` the tunnel URL with `SENTINEL_BASE_URL` + `SENTINEL_TOKEN` secrets, then prompt Base44 to call those functions from the UI.

## Chat behavior note

Sentinel `/api/chat` now accepts `history` for multi-turn conversation. Base44 (and the native dashboard) should send prior turns — otherwise follow-ups will still feel broken.
