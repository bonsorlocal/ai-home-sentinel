"""Adaptive RAM pressure guard (Phase 9B).

Polls system memory and exposes a simple AI mode:
- ``local``     — run YOLO / face recognition on-device
- ``cloud``     — offload heavy vision to cloud APIs
- ``degraded``  — motion-only when cloud is also unavailable
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from sentinel import health

MemoryFn = Callable[[], Dict[str, Any]]


class ResourceGuard:
    """Watch RAM and recommend local vs cloud AI mode."""

    def __init__(
        self,
        config: Any,
        *,
        memory_fn: Optional[MemoryFn] = None,
        cloud_available_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        perf = {}
        try:
            perf = config.get("performance") or {}
        except Exception:  # noqa: BLE001
            perf = {}

        self._enabled = bool(perf.get("adaptive_ai_enabled", True))
        self._warn = float(perf.get("memory_warn_percent", 75))
        self._offload = float(perf.get("memory_offload_percent", 85))
        self._recover = float(perf.get("memory_recover_percent", 70))
        self._poll = max(2.0, float(perf.get("memory_poll_seconds", 10)))
        self._memory_fn = memory_fn or health.get_memory
        self._cloud_available_fn = cloud_available_fn or (lambda: True)

        self._lock = threading.Lock()
        self._mode = "local"
        self._last_percent: Optional[float] = None
        self._message = "Resource guard idle."
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def should_offload(self) -> bool:
        return self.mode in ("cloud", "degraded")

    def should_use_local(self) -> bool:
        return self.mode == "local"

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "ai_mode": self._mode,
                "memory_percent": self._last_percent,
                "memory_warn_percent": self._warn,
                "memory_offload_percent": self._offload,
                "memory_recover_percent": self._recover,
                "message": self._message,
            }

    def start(self) -> None:
        if not self._enabled:
            self._message = "Adaptive AI disabled in config."
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._evaluate()
        self._thread = threading.Thread(
            target=self._run, name="resource-guard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def evaluate_once(self) -> str:
        """Public hook for tests — recompute mode immediately."""
        self._evaluate()
        return self.mode

    def _run(self) -> None:
        while not self._stop.is_set():
            self._evaluate()
            self._stop.wait(self._poll)

    def _evaluate(self) -> None:
        try:
            mem = self._memory_fn() or {}
            percent = float(mem.get("percent", 0.0) or 0.0)
        except Exception as error:  # noqa: BLE001
            with self._lock:
                self._message = f"Memory read failed: {error}"
            return

        cloud_ok = False
        try:
            cloud_ok = bool(self._cloud_available_fn())
        except Exception:  # noqa: BLE001
            cloud_ok = False

        with self._lock:
            previous = self._mode
            self._last_percent = percent
            if not self._enabled:
                self._mode = "local"
                self._message = "Adaptive AI disabled; staying local."
                return

            if previous == "local":
                if percent >= self._offload:
                    self._mode = "cloud" if cloud_ok else "degraded"
            elif previous == "cloud":
                if not cloud_ok:
                    self._mode = "degraded"
                elif percent <= self._recover:
                    self._mode = "local"
            else:  # degraded
                if cloud_ok and percent >= self._offload:
                    self._mode = "cloud"
                elif percent <= self._recover:
                    self._mode = "local" if percent < self._offload else (
                        "cloud" if cloud_ok else "degraded"
                    )

            if self._mode == "local" and percent >= self._warn:
                self._message = f"RAM {percent:.0f}% (warn); still local."
            elif self._mode == "cloud":
                self._message = f"RAM {percent:.0f}% — offloaded to cloud."
            elif self._mode == "degraded":
                self._message = f"RAM {percent:.0f}% — degraded (motion only)."
            else:
                self._message = f"RAM {percent:.0f}% — local AI."

            if self._mode != previous:
                print(f"[resource_guard] mode {previous} -> {self._mode} ({self._message})")
