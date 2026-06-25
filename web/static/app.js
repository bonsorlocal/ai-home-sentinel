// AI Home Sentinel - status page logic (Phase 2).
//
// This script asks the server for /status on a timer and fills in the page.
// It is written to be forgiving: if the server is briefly unreachable, it
// shows "disconnected" and keeps trying instead of breaking. In Phase 2 it
// also turns the live video stream on or off based on the camera state.

(function () {
  "use strict";

  var REFRESH_MS = 3000; // how often to refresh the status (3 seconds)

  // Tracks whether the live <img> is currently pointed at the /video stream,
  // so we only start or stop it when the camera state actually changes.
  var videoStreaming = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function setBadge(el, text, kind) {
    if (!el) return;
    el.textContent = text;
    el.className = "badge " + (kind || "badge-unknown");
  }

  function formatPercent(value) {
    if (value === null || value === undefined) return "-";
    return value + "%";
  }

  function updateLiveView(data) {
    var video = byId("video");
    var message = byId("video-message");
    var camera = data.camera || {};

    if (data.camera_active) {
      // Camera is producing frames: point the <img> at the live stream once.
      if (!videoStreaming) {
        // The query string busts the cache so a fresh stream starts each time.
        video.src = "/video?ts=" + Date.now();
        videoStreaming = true;
      }
      video.classList.remove("hidden");
      message.classList.add("hidden");
    } else {
      // No live frames: stop the stream and explain why in plain language.
      if (videoStreaming) {
        video.removeAttribute("src");
        videoStreaming = false;
      }
      video.classList.add("hidden");
      message.classList.remove("hidden");
      message.textContent =
        camera.message || "Camera unavailable. Check that it is connected.";
    }
  }

  function render(data) {
    // Connection / timestamp
    setBadge(byId("connection"), "connected", "badge-ok");
    byId("timestamp").textContent = data.timestamp || "-";

    // Live camera view reflects the real camera state (works even if health
    // reading failed below).
    updateLiveView(data);

    // If the server reported an internal error, show it and stop here.
    var errorCard = byId("error-card");
    if (data.ok === false) {
      errorCard.classList.remove("hidden");
      byId("error-message").textContent =
        data.error || "Unknown error while reading system health.";
      return;
    }
    errorCard.classList.add("hidden");

    // Temperature
    var temp = data.temperature || {};
    if (temp.celsius === null || temp.celsius === undefined) {
      byId("temp").textContent = "unavailable";
    } else {
      byId("temp").textContent = temp.celsius + " \u00B0C";
    }
    byId("temp-source").textContent = temp.source || "-";

    var tempWarn = byId("temp-warning");
    if (data.temperature_high) {
      tempWarn.classList.remove("hidden");
    } else {
      tempWarn.classList.add("hidden");
    }

    // CPU and memory
    byId("cpu").textContent = formatPercent(data.cpu_percent);
    var mem = data.memory || {};
    if (mem.percent === undefined) {
      byId("ram").textContent = "-";
    } else {
      byId("ram").textContent =
        formatPercent(mem.percent) +
        " (" +
        mem.used_mb +
        " / " +
        mem.total_mb +
        " MB)";
    }

    // Disk
    var disk = data.disk || {};
    if (disk.percent === undefined) {
      byId("disk").textContent = "-";
    } else {
      byId("disk").textContent =
        formatPercent(disk.percent) +
        " (" +
        disk.used_gb +
        " / " +
        disk.total_gb +
        " GB)";
    }

    // Throttling
    var throttle = data.throttling || {};
    var throttleEl = byId("throttle");
    if (!throttle.available) {
      throttleEl.textContent = "unavailable (not a Raspberry Pi)";
    } else {
      var problems = [];
      if (throttle.under_voltage_now) problems.push("under-voltage");
      if (throttle.throttled_now) problems.push("throttled");
      if (throttle.frequency_capped_now) problems.push("frequency capped");
      if (throttle.soft_temp_limit_now) problems.push("soft temp limit");
      throttleEl.textContent =
        problems.length === 0 ? "OK (" + throttle.raw + ")" : problems.join(", ");
    }

    // System markers
    setBadge(
      byId("camera-active"),
      data.camera_active ? "on" : "off",
      data.camera_active ? "badge-ok" : "badge-unknown"
    );
    var cameraInfo = data.camera || {};
    byId("camera-detail").textContent = cameraInfo.message || "-";
    setBadge(
      byId("motion-active"),
      data.motion_active ? "on" : "off",
      data.motion_active ? "badge-ok" : "badge-unknown"
    );
    setBadge(
      byId("detector-active"),
      data.detector_active ? "on" : "off",
      data.detector_active ? "badge-ok" : "badge-unknown"
    );
  }

  function refresh() {
    fetch("/status", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Server returned " + response.status);
        }
        return response.json();
      })
      .then(render)
      .catch(function () {
        setBadge(byId("connection"), "disconnected", "badge-bad");
        // Can't reach the server, so we can't trust the live view either.
        updateLiveView({ camera_active: false, camera: {
          message: "Lost connection to the dashboard. Reconnecting..."
        } });
      });
  }

  // Run once right away, then on a repeating timer.
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
