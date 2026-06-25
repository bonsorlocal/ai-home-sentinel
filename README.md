# AI Home Sentinel

A friendly, do-it-yourself home security assistant that runs on a Raspberry Pi 5.
It watches a camera, notices movement, recognizes people and objects, and saves
snapshots of important events - all on your own device, with your data staying
at home.

This README is written for beginners. If a step is unclear, copy the exact
commands shown and follow the "What success looks like" notes.

---

## What is built so far: Phases 1 & 2

The project is built in small, safe phases. **Phases 1 and 2 are done.**

**Phase 1 (Foundation)** builds and proves the groundwork - no camera or AI yet:

- Reads settings from a single `config.yaml` file.
- Monitors the machine's health: CPU usage, memory, disk, temperature, and
  (on a Raspberry Pi) throttling state.
- Runs a small web dashboard you open in a browser.
- Provides a `/status` page that returns the health info as data (JSON).

**Phase 2 (Camera & Live Video)** adds a live picture to the dashboard:

- A single camera capture loop reads the camera once and shares the newest
  frame with the rest of the app (old frames are dropped so the view stays
  fresh).
- The dashboard shows live video at `/video` (an MJPEG stream).
- The "Camera" badge turns **on** when the camera is really producing frames.
- It works with the Raspberry Pi camera module (Picamera2) or a USB webcam
  (OpenCV), chosen by `camera.type` in `config.yaml`.

Motion detection, object detection, face recognition, and voice features come
in later phases. If no camera is attached (for example on a PC), everything
still runs and the dashboard simply shows the camera as unavailable.

---

## What you need

- A computer or a Raspberry Pi 5.
- Python 3.9 or newer installed.
  - To check, open a terminal and type: `python --version`
  - On some systems the command is `python3` instead of `python`.

You can test Phase 1 on a normal PC first. On a PC the temperature may show
"unavailable" - that is expected, because the Pi-only temperature tool isn't
there. Everything else still works.

---

## Step-by-step setup

A "terminal" is the app where you type commands:
- **Windows:** open "PowerShell" from the Start menu.
- **Raspberry Pi / Linux / Mac:** open the "Terminal" app.

### 1. Go into the project folder

```bash
cd ai-home-sentinel
```

(If your folder name is different, use that name.)

### 2. Create a virtual environment

A virtual environment is a private box that holds this project's Python
packages, so they don't mix with the rest of your system.

```bash
python -m venv venv
```

If `python` isn't found, try `python3 -m venv venv`.

**What success looks like:** a new `venv` folder appears in the project.

### 3. Turn the virtual environment on (activate it)

- **Windows (PowerShell):**

```powershell
venv\Scripts\Activate.ps1
```

  - If you see a red error about "running scripts is disabled", run this once,
    then try the activate command again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

- **Raspberry Pi / Linux / Mac:**

```bash
source venv/bin/activate
```

**What success looks like:** your prompt now starts with `(venv)`.

### 4. Install the required packages

```bash
pip install -r requirements.txt
```

**What success looks like:** it downloads PyYAML, Flask, and psutil and ends
with something like "Successfully installed ...". No red error at the end.

### 5. Start AI Home Sentinel

```bash
python run.py
```

**What success looks like:** you see a box of text saying the dashboard is
starting on `http://0.0.0.0:5000`.

### 6. Open the dashboard

Open a web browser and go to:

```
http://localhost:5000
```

To view it from your phone or another computer on the same home network, use
the Pi's address instead, for example `http://192.168.1.50:5000`.
(To find the Pi's address, run `hostname -I` on the Pi.)

**What success looks like:**
- The page subtitle says "Phase 2 - Camera & Live Video".
- "Connection" shows a green "connected" badge.
- CPU usage, RAM usage, and Disk usage show real numbers.
- CPU temperature shows a number on a Raspberry Pi (or "unavailable" on a PC).
- The numbers refresh by themselves every few seconds.
- The "Live View" card shows your camera (or a friendly "camera unavailable"
  message if no camera is connected - see the Phase 2 section below to add one).

### 7. Stop the program

In the terminal where it is running, press **CTRL + C**.

**What success looks like:** it prints "AI Home Sentinel stopped. Goodbye!".

---

## The `/status` data endpoint

If you open `http://localhost:5000/status` you'll see the raw health data as
JSON. This is the same information the dashboard uses. It's handy for testing
and will be used by other tools later. In Phase 2 it also includes a `camera`
section and a `camera_active` flag.

---

## Phase 2: the camera and live video

Phase 2 adds a live picture to the dashboard. The steps below are written for a
Raspberry Pi 5 with the official camera module. (A USB webcam works too and
needs none of the Picamera2 steps - just plug it in.)

### 1. Connect the camera

Plug the camera module's ribbon cable into the Pi's camera port (with the Pi
powered off), then power the Pi back on. If you already did this, great - move
on.

### 2. Install the Raspberry Pi camera software

The Pi camera library, **Picamera2**, is installed as a system package - it is
**not** reliably installable with `pip`. Install it once with:

```bash
sudo apt update && sudo apt install -y python3-picamera2
```

### 3. Recreate the virtual environment so it can see the camera library

Here's the catch that confuses most beginners: a plain `python3 -m venv venv`
cannot see system packages, so `import picamera2` fails inside it. The fix is to
recreate the venv with the `--system-site-packages` option. From inside the
project folder (`~/ai-home-sentinel`):

```bash
deactivate                                   # turn off the old venv (ok if it says "not found")
rm -rf venv                                  # remove the old venv
python3 -m venv --system-site-packages venv  # new venv that CAN see python3-picamera2
source venv/bin/activate                     # turn the new one on
pip install -r requirements.txt              # reinstall the project packages
```

**What success looks like:** your prompt shows `(venv)` again and the install
ends with "Successfully installed ..." and no red error.

### 4. Run it and open the dashboard

```bash
python run.py
```

Then open `http://localhost:5000` on the Pi, or from another device on your
network use the Pi's name or address, for example
`http://sentinel.local:5000` or `http://192.168.1.50:5000`.

### What success looks like (Phase 2)

- The "Live View" card at the top shows your camera's live picture.
- The "Camera" badge under "System status" turns green and says **on**.
- The startup box in the terminal says "Phase 2 (Camera & Live Video)".

If the camera can't be opened, nothing crashes: the Live View shows a plain
message, the badge stays **off**, and the health numbers keep updating. The
exact reason (for example "Picamera2 is not available...") is shown under the
"System status" card and printed in the terminal with a `[camera]` tag.

### Choosing the camera type

In `config.yaml`, the `camera.type` setting controls which camera is used:

- `auto` (default) - try the Pi camera first, then fall back to a USB webcam.
- `picamera2` - force the Raspberry Pi camera module.
- `opencv` - force a USB / V4L2 webcam.

You can also change `width`, `height`, and `target_fps` there.

---

## If something goes wrong

- **`python` is not recognized / not found:** try `python3` instead, or
  reinstall Python and check the box "Add Python to PATH" during install.
- **Activate command fails on Windows:** run the `Set-ExecutionPolicy` command
  shown in Step 3, then try again.
- **`pip install` fails:** make sure the virtual environment is active (your
  prompt shows `(venv)`), then run the install command again.
- **Browser says it can't connect:** make sure the terminal still shows the
  program running, and that you used the correct address and port `5000`.
- **Temperature shows "unavailable":** normal on a PC. On a Raspberry Pi it
  should show a number; if not, the Pi tool `vcgencmd` may be missing.
- **Live View says the camera is unavailable on the Pi:** make sure you did the
  Phase 2 steps - install `python3-picamera2` AND recreate the venv with
  `--system-site-packages` (a plain venv cannot see the system camera library).
  The message under "System status" tells you the exact reason.
- **`pip install` is slow on opencv-python:** that's normal on a Pi; it can take
  a few minutes. If it fails, run `sudo apt install -y python3-opencv` instead.

---

## Configuration

All settings live in `config.yaml`. You can open it in any text editor and
change values (like the dashboard `port` or the `camera` settings) without
touching the code. The full file is already filled in for future phases;
Phases 1 and 2 use the `dashboard`, `performance`, and `camera` sections.

If a setting is missing or the file has a typo, the program falls back to safe
built-in defaults and keeps running.

---

## Privacy and safety

- Everything runs and stays on your device. Nothing is uploaded to the cloud.
- The dashboard is meant for your home network only. Do not expose it to the
  public internet.
- Saved recordings, snapshots, known faces, and secrets are kept out of Git by
  the `.gitignore` file.

---

## Project layout

```
ai-home-sentinel/
  README.md            <- this file
  requirements.txt     <- list of Python packages
  config.yaml          <- all your settings
  run.py               <- the file you run to start everything
  .gitignore           <- tells Git what to ignore

  sentinel/            <- the program's code
    config.py          <- loads settings (Phase 1)
    health.py          <- reads CPU/RAM/disk/temperature (Phase 1)
    dashboard.py       <- the web dashboard + /status (Phase 1)
    utils.py           <- small helpers
    camera.py          <- camera capture loop    (Phase 2)
    frame_store.py     <- shared latest frame    (Phase 2)
    motion.py          <- motion detection       (Phase 3, placeholder)
    events.py          <- event log (SQLite)     (Phase 3, placeholder)
    storage.py         <- snapshots/clips        (Phase 3, placeholder)
    detector.py        <- object detection (YOLO)(Phase 4, placeholder)
    face_recognition_module.py <- faces          (Phase 5, placeholder)

  web/
    templates/         <- web pages (HTML)
    static/            <- styling (CSS) and page logic (JavaScript)

  data/                <- saved events, snapshots, known faces (kept local)
  scripts/             <- helper scripts (added in later phases)
  tests/               <- automated tests
```

---

## Note about Git

This folder is not a Git repository yet, and Git may not be installed on this
machine. That is fine for Phase 1 - you don't need Git to run the project. If
you decide to use Git later, the included `.gitignore` is already set up to
protect your private data.

---

## Next phase

Once you've confirmed the dashboard runs, shows live numbers, and displays the
live camera view, the next step is **Phase 3: motion detection, the Event
Ledger, and saving snapshots of important events.**
