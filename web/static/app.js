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
    var phaseNames = {
      2: "Phase 2 - Camera & Live Video",
      3: "Phase 3 - Motion & Event Ledger",
      4: "Phase 4 - Object Detection",
      5: "Phase 5 - Face Recognition",
      7: "Phase 7 - Reasoner",
    };
    var subtitle = byId("page-subtitle");
    if (subtitle) {
      subtitle.textContent = phaseNames[data.phase] || ("Phase " + data.phase);
    }

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
    setBadge(
      byId("brain-active"),
      data.brain_active ? "on" : "off",
      data.brain_active ? "badge-ok" : "badge-unknown"
    );
    // Phase 7: reasoner badge
    var reasonerInfo = (data.runtime && data.runtime.reasoner) || {};
    var reasonerOn = reasonerInfo.enabled !== false;
    setBadge(
      byId("reasoner-active"),
      reasonerOn ? "on" : "off",
      reasonerOn ? "badge-ok" : "badge-unknown"
    );
    updateBrainState(data.brain || {});
    var eventCount = byId("event-count");
    if (eventCount) {
      eventCount.textContent = data.event_count != null ? data.event_count : "0";
    }
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

  // -------------------------------------------------------------------- //
  // Brain / chat
  // -------------------------------------------------------------------- //
  function updateBrainState(brain) {
    var stateEl = byId("brain-state");
    if (!stateEl) return;
    if (brain.available) {
      var remaining = brain.calls_remaining;
      stateEl.textContent =
        "Ready" +
        (remaining != null ? " - " + remaining + " questions left today" : "");
    } else if (brain.enabled === false) {
      stateEl.textContent = "The brain is turned off in config.yaml.";
    } else if (brain.has_key === false) {
      stateEl.textContent =
        "Offline: no Grok API key found in secrets.yaml on the Pi.";
    } else {
      stateEl.textContent = "The brain is currently unavailable.";
    }
  }

  function appendChat(role, text) {
    var out = byId("chat-output");
    if (!out) return;
    var line = document.createElement("p");
    line.className = "chat-line chat-" + role;
    var who = document.createElement("span");
    who.className = "chat-who";
    who.textContent = role === "you" ? "You: " : "Sentinel: ";
    line.appendChild(who);
    line.appendChild(document.createTextNode(text));
    out.appendChild(line);
    out.scrollTop = out.scrollHeight;
    return line;
  }

  function setChatBusy(busy) {
    var input = byId("chat-input");
    var send = byId("chat-send");
    var summary = byId("summary-btn");
    if (input) input.disabled = busy;
    if (send) send.disabled = busy;
    if (summary) summary.disabled = busy;
  }

  function postBrain(url, body, pendingText) {
    setChatBusy(true);
    var pending = appendChat("bot", pendingText);
    if (pending) pending.classList.add("chat-pending");

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
        appendChat("bot", (data && data.answer) || "No answer was returned.");
      })
      .catch(function () {
        if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
        appendChat(
          "bot",
          "Could not reach the dashboard. Check your connection and try again."
        );
      })
      .finally(function () {
        setChatBusy(false);
        var input = byId("chat-input");
        if (input) input.focus();
      });
  }

  var chatForm = byId("chat-form");
  if (chatForm) {
    chatForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = byId("chat-input");
      var question = input ? input.value.trim() : "";
      if (!question) return;
      appendChat("you", question);
      if (input) input.value = "";
      postBrain("/api/chat", { question: question }, "Thinking...");
    });
  }

  var summaryBtn = byId("summary-btn");
  if (summaryBtn) {
    summaryBtn.addEventListener("click", function () {
      appendChat("you", "Summarize my day");
      postBrain("/api/summary", {}, "Summarizing your day...");
    });
  }

  // Run once right away, then on a repeating timer.
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
