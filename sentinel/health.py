"""Raspberry Pi (and PC) health monitor for AI Home Sentinel.

This module reports how the machine is doing:

- CPU usage (percent)
- RAM usage (percent and amounts)
- Disk usage (percent and amounts)
- CPU temperature (degrees Celsius)
- Throttling state (is the Pi being slowed down to protect itself?)

It is designed to work on a Raspberry Pi AND on a normal PC. Anything that is
Pi-specific (like the ``vcgencmd`` command) is wrapped in try/except. If it is
not available, the function returns a clear "unavailable" message instead of
crashing. This lets you test the dashboard on your PC before the Pi is ready.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, Optional

try:
    import psutil
except ImportError as exc:  # pragma: no cover - handled with a clear message
    raise ImportError(
        "psutil is not installed. Run 'pip install -r requirements.txt' first."
    ) from exc


def _round(value: float, digits: int = 1) -> float:
    """Round a number for nicer display."""
    return round(float(value), digits)


def get_cpu_percent() -> float:
    """Return the overall CPU usage as a percentage (0-100).

    The very first call may read 0.0 because psutil needs a baseline; the
    dashboard polls repeatedly, so later readings are accurate.
    """
    return _round(psutil.cpu_percent(interval=None))


def get_memory() -> Dict[str, Any]:
    """Return RAM usage details in human-friendly units (megabytes)."""
    mem = psutil.virtual_memory()
    return {
        "percent": _round(mem.percent),
        "used_mb": _round(mem.used / (1024 * 1024)),
        "total_mb": _round(mem.total / (1024 * 1024)),
    }


def get_disk(path: str = "/") -> Dict[str, Any]:
    """Return disk usage for the drive that holds the project (gigabytes).

    On Windows the root path ``/`` is automatically mapped to the current
    drive, so this works for testing on a PC too.
    """
    try:
        usage = psutil.disk_usage(path)
    except Exception:  # noqa: BLE001 - fall back to current directory
        usage = psutil.disk_usage(".")
    return {
        "percent": _round(usage.percent),
        "used_gb": _round(usage.used / (1024 ** 3)),
        "total_gb": _round(usage.total / (1024 ** 3)),
    }


def _run_vcgencmd(argument: str) -> Optional[str]:
    """Run a ``vcgencmd`` command and return its text output, or None.

    ``vcgencmd`` only exists on Raspberry Pi OS. On any other system (or if the
    command fails) this safely returns None instead of raising an error.
    """
    if shutil.which("vcgencmd") is None:
        return None
    try:
        result = subprocess.run(
            ["vcgencmd", argument],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - never let a Pi call crash the app
        return None


def get_temperature() -> Dict[str, Any]:
    """Return the CPU temperature in Celsius.

    Order of attempts:
    1. Raspberry Pi ``vcgencmd measure_temp`` (most accurate on a Pi).
    2. psutil sensor readings (works on many Linux machines).
    3. If neither works (e.g. on Windows), report it as unavailable.
    """
    # Attempt 1: Raspberry Pi command. Output looks like: temp=48.3'C
    raw = _run_vcgencmd("measure_temp")
    if raw and "=" in raw:
        try:
            number = raw.split("=")[1].replace("'C", "").replace("C", "").strip()
            return {"celsius": _round(float(number)), "source": "vcgencmd"}
        except Exception:  # noqa: BLE001
            pass

    # Attempt 2: psutil temperature sensors (not present on Windows/macOS).
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is not None:
        try:
            readings = sensors()
            for entries in readings.values():
                for entry in entries:
                    if entry.current is not None:
                        return {
                            "celsius": _round(entry.current),
                            "source": "psutil",
                        }
        except Exception:  # noqa: BLE001
            pass

    # Attempt 3: give up gracefully.
    return {"celsius": None, "source": "unavailable"}


def get_throttling() -> Dict[str, Any]:
    """Return the Raspberry Pi throttling state, if available.

    ``vcgencmd get_throttled`` returns a hex code whose bits tell us whether
    the Pi has hit under-voltage or thermal limits. We decode the common bits
    into plain-English flags. On non-Pi systems this returns "unavailable".
    """
    raw = _run_vcgencmd("get_throttled")
    if not raw or "=" not in raw:
        return {"available": False, "raw": None}

    try:
        value = int(raw.split("=")[1].strip(), 16)
    except Exception:  # noqa: BLE001
        return {"available": False, "raw": raw}

    # Bit meanings from the Raspberry Pi documentation.
    return {
        "available": True,
        "raw": hex(value),
        "under_voltage_now": bool(value & 0x1),
        "frequency_capped_now": bool(value & 0x2),
        "throttled_now": bool(value & 0x4),
        "soft_temp_limit_now": bool(value & 0x8),
        "under_voltage_occurred": bool(value & 0x10000),
        "throttled_occurred": bool(value & 0x40000),
    }


def get_health(max_cpu_temp_celsius: float = 75.0) -> Dict[str, Any]:
    """Collect everything into one dictionary for the /status API.

    ``max_cpu_temp_celsius`` comes from config and is used to set a simple
    "temperature too high" warning flag. Each piece is gathered independently
    so one failing reading never blocks the others.
    """
    temperature = get_temperature()

    temp_high = False
    temp_value = temperature.get("celsius")
    if isinstance(temp_value, (int, float)):
        temp_high = temp_value >= max_cpu_temp_celsius

    return {
        "cpu_percent": get_cpu_percent(),
        "memory": get_memory(),
        "disk": get_disk(),
        "temperature": temperature,
        "throttling": get_throttling(),
        "temperature_high": temp_high,
        "max_cpu_temp_celsius": max_cpu_temp_celsius,
    }
