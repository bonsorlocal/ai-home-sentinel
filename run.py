"""AI Home Sentinel - main entry point.

This is the single file you run to start everything. It loads the settings from
config.yaml, starts the camera capture loop in the background (Phase 2), starts
motion detection and the Event Ledger (Phase 3), object detection (Phase 4),
and face recognition when enabled (Phase 5), then runs the web dashboard.

How to run it:

    python run.py

Then open http://localhost:5000 in a web browser. Press CTRL+C to stop.
"""

from __future__ import annotations

import sys

from sentinel.camera import Camera
from sentinel.config import load_config
from sentinel.dashboard import run_dashboard
from sentinel.frame_store import FrameStore
from sentinel.runtime import SentinelRuntime


def main() -> int:
    """Load settings, start all modules, and run the dashboard. Returns an exit code."""
    try:
        config = load_config()
    except Exception as error:  # noqa: BLE001 - show a clear message and exit
        print(f"Could not load configuration: {error}")
        return 1

    frame_store = FrameStore()
    camera = Camera(config, frame_store)
    runtime = SentinelRuntime(config, frame_store)

    camera.start()
    runtime.start()

    try:
        run_dashboard(config, camera=camera, runtime=runtime)
    except KeyboardInterrupt:
        print("\nAI Home Sentinel stopped. Goodbye!")
        return 0
    except Exception as error:  # noqa: BLE001 - never crash silently
        print(f"The dashboard stopped because of an error: {error}")
        return 1
    finally:
        runtime.stop()
        camera.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
