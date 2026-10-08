# Base44 + AI Home Sentinel — do this in order

Base44 **does not** import this whole GitHub repo as the website.  
You import **two things** from this folder into Base44:

1. **`openapi.json`** → Custom integration (connects Base44 to your camera/backend)  
2. **`PROMPT.txt`** → Paste into Base44 chat (builds the webpage)

Everything you need is in this folder: `base44-import/`

---

## Before Base44 (on your PC / Pi)

### Step A — Start Sentinel

```powershell
cd C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi
.\scripts\run_pc.ps1
```

Check: open http://localhost:5000 — camera/dashboard should load.

### Step B — Make Sentinel reachable from the internet

Base44 is in the cloud. It cannot use `localhost`.

**If using the Pi:**

```powershell
.\scripts\deploysshtunnel.ps1
```

Copy the printed URL, like `https://something.trycloudflare.com`.

**If using PC only:** create an HTTPS tunnel to `http://127.0.0.1:5000` and copy that URL.

### Step C — Test the public URL

In a browser open:

`https://YOUR-TUNNEL.trycloudflare.com/health`

You should get an OK response. If that fails, fix the tunnel before Base44.

### Step D — Token (only if dashboard auth is on)

If `dashboard.require_token` is true, copy `dashboard_token` from `secrets.yaml`.  
You will paste it into Base44 as header `X-Sentinel-Token`.

---

## In Base44 — import the API

### Step 1 — Open custom integrations

1. Sign in to Base44  
2. Click your **workspace name** (bottom left) → **Settings**  
3. Under Build tools → **Custom integrations**  
4. **Add integration** / **Create integration**

### Step 2 — Paste the OpenAPI file

1. Choose **Paste JSON** (preferred)  
2. Open this file on your PC:

   `C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi\base44-import\openapi.json`

3. Select all → copy → paste into Base44  
4. Click **Continue**

(If the UI wants a URL instead: push this repo and use a raw GitHub URL to `base44-import/openapi.json`, or paste anyway.)

### Step 3 — Select endpoints

Select **all** of these (or “select all” if offered):

- getHealth  
- getStatus  
- listEvents  
- getLiveJpeg  
- getMjpegStream (optional)  
- getDvrStatus  
- getDvrRange  
- postChat  
- postDaySummary  

Continue.

### Step 4 — Configure the integration

| Field | Value |
|--------|--------|
| Slug | `sentinel-api` |
| Name | AI Home Sentinel |
| Base URL | your tunnel URL from Step B (example `https://xxxx.trycloudflare.com`) |

If auth is on, add a custom header:

| Header | Value |
|--------|--------|
| `X-Sentinel-Token` | your `dashboard_token` |

Click **Create Integration**.

---

## In Base44 — build the webpage

### Step 5 — New app + paste the prompt

1. Create a **new app** in the same workspace  
2. Open the AI chat  
3. Open this file:

   `C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi\base44-import\PROMPT.txt`

4. Copy **everything** in that file  
5. Paste into Base44 and send  
6. Let Base44 build; if it asks which integration, pick **sentinel-api**

### Step 6 — Verify in the Base44 preview

1. **Live** — camera image updates  
2. **Events** — real events from your Sentinel  
3. **Chat** — ask “what do you see?”, then “why is that?” (follow-up should make sense)

If Live/Events fail: tunnel URL changed or token wrong — edit the custom integration Base URL / header.

---

## Files in this folder

| File | What you do with it |
|------|---------------------|
| `START_HERE.md` | This walkthrough |
| `openapi.json` | Paste into Base44 Custom integrations |
| `openapi.yaml` | Same spec (YAML form) |
| `PROMPT.txt` | Paste into Base44 app chat |
| `VALUES.txt` | Fill in your tunnel URL + token for yourself |

## Important limits

- You are **not** uploading the Python/Pi code into Base44. Sentinel stays on your PC/Pi.  
- Quick tunnels often get a **new URL** after restart — update Base URL in the integration.  
- Chat follow-ups only work if Base44 sends `history` with `postChat` (the prompt tells it to).
