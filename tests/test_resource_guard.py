"""Tests for adaptive RAM ResourceGuard (Phase 9B)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.resource_guard import ResourceGuard  # noqa: E402


def _cfg(**perf):
    base = {
        "adaptive_ai_enabled": True,
        "memory_warn_percent": 75,
        "memory_offload_percent": 85,
        "memory_recover_percent": 70,
        "memory_poll_seconds": 10,
    }
    base.update(perf)
    return Config({"performance": base})


def test_stays_local_under_threshold():
    guard = ResourceGuard(
        _cfg(),
        memory_fn=lambda: {"percent": 50},
        cloud_available_fn=lambda: True,
    )
    assert guard.evaluate_once() == "local"


def test_offloads_to_cloud_when_ram_high():
    guard = ResourceGuard(
        _cfg(),
        memory_fn=lambda: {"percent": 90},
        cloud_available_fn=lambda: True,
    )
    assert guard.evaluate_once() == "cloud"


def test_degraded_when_cloud_unavailable():
    guard = ResourceGuard(
        _cfg(),
        memory_fn=lambda: {"percent": 90},
        cloud_available_fn=lambda: False,
    )
    assert guard.evaluate_once() == "degraded"


def test_recovers_to_local_when_ram_drops():
    percent = {"v": 90}
    guard = ResourceGuard(
        _cfg(),
        memory_fn=lambda: {"percent": percent["v"]},
        cloud_available_fn=lambda: True,
    )
    assert guard.evaluate_once() == "cloud"
    percent["v"] = 60
    assert guard.evaluate_once() == "local"


def test_disabled_stays_local():
    guard = ResourceGuard(
        _cfg(adaptive_ai_enabled=False),
        memory_fn=lambda: {"percent": 95},
        cloud_available_fn=lambda: True,
    )
    assert guard.evaluate_once() == "local"
