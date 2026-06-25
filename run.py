"""AI Home Sentinel - main entry point.

This is the single file you run to start everything. It loads the settings from
config.yaml, starts the camera capture loop in the background (Phase 2), and
starts the web dashboard (the status page, the /status API, and the /video live
stream).

How to run it:

    python run.py

Then open http://localhost:5000 in a web browser. Press CTRL+C to stop.

If no camera is attached (for example on a PC), the dashboard still runs and
simply shows the camera as unavailable. Later phases will start motion
detection and AI from here too.
"""

from __future__ import annotations

import sys

from sentinel.camera import Camera
from sentinel.config import load_config
from sentinel.dashboard import run_dashboard
from sentinel.frame_store import FrameStore


def main() -> int:
    """Load settings, start the camera, and run the dashboard. Returns an exit code."""
    try:
        config = load_config()
    except Exception as error:  # noqa: BLE001 - show a clear message and exit
        print(f"Could not load configuration: {error}")
        return 1

    # One shared frame store + one capture loop feed the whole app (Phase 2).
    frame_store = FrameStore()
    camera = Camera(config, frame_store)
    camera.start()

    try:
        run_dashboard(config, camera=camera)
    except KeyboardInterrupt:
        # The user pressed CTRL+C; this is a normal, clean shutdown.
        print("\nAI Home Sentinel stopped. Goodbye!")
        return 0
    except Exception as error:  # noqa: BLE001 - never crash silently
        print(f"The dashboard stopped because of an error: {error}")
        return 1
    finally:
        # Always release the camera cleanly, however we exit.
        camera.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
