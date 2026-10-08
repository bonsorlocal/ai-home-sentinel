// AI Home Sentinel - status page logic (Phase 2).
//
// This script asks the server for /status on a timer and fills in the page.
// It is written to be forgiving: if the server is briefly unreachable, it
// shows "disconnected" and keeps trying instead of breaking. In Phase 2 it
// also turns the live video stream on or off based on the camera state.

(function () {
  "use strict";

  var REFRESH_MS = 5000; // status refresh; keep light on Wi-Fi so /video stays smooth
  var PROFILE_REFRESH_MS = 30000;
  var lastProfileRefreshAt = 0;

  // Live view uses one continuous MJPEG connection (/video). That is much
  // smoother on Wi-Fi than fetching a new JPEG for every frame.
  var videoStreaming = false;

  // Voice settings from /status (defaults until first refresh).
  var voiceSettings = {
    enabled: true,
    speak_text_queries: false,
    wake_word_enabled: false,
    wake_word: "hey sentinel",
    language: "en-US",
    tts_provider: "browser",
    google_tts_available: false,
  };
  var latestInteraction = null;
  var selectedHelpful = null;

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

  function formatDashboardTime(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }).format(d);
  }

  function updateLiveView(data) {
    var video = byId("video");
    var message = byId("video-message");
    var camera = data.camera || {};

    if (data.camera_active) {
      if (!videoStreaming) {
        // One long-lived MJPEG stream — cache-bust only when (re)starting.
        video.src = "/video?ts=" + Date.now();
        videoStreaming = true;
      }
      video.classList.remove("hidden");
      message.classList.add("hidden");
    } else {
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
    byId("timestamp").textContent = formatDashboardTime(data.timestamp);

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
      8: "Phase 8 - Clips & Metadata",
      9: "Phase 9 - DVR + Brain",
      10: "Phase 10 - Household Profiles",
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
      byId("face-active"),
      data.face_recognition_active ? "on" : "off",
      data.face_recognition_active ? "badge-ok" : "badge-unknown"
    );
    var faceInfo = (data.runtime && data.runtime.face_recognition) || {};
    var faceDetail = byId("face-detail");
    if (faceDetail) {
      faceDetail.textContent = faceInfo.message || "-";
    }
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
    if (data.voice) {
      voiceSettings = data.voice;
      updateVoiceHint();
    }
    var eventCount = byId("event-count");
    if (eventCount) {
      eventCount.textContent = data.event_count != null ? data.event_count : "0";
    }
    var dvrState = byId("dvr-state");
    if (dvrState) {
      var dvr = data.dvr || {};
      if (dvr.available) {
        dvrState.textContent = "ready";
      } else if (dvr.enabled) {
        dvrState.textContent = "unavailable";
      } else {
        dvrState.textContent = "off";
      }
    }
    refreshProfile(false);
  }

  function refreshProfile(force) {
    var now = Date.now();
    if (!force && lastProfileRefreshAt && now - lastProfileRefreshAt < PROFILE_REFRESH_MS) {
      return;
    }
    lastProfileRefreshAt = now;
    fetch("/api/profile/owner", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("profile status failed");
        return response.json();
      })
      .then(function (ownerData) {
        var state = byId("profile-state");
        var ownerName = byId("owner-name");
        var hint = byId("profile-enroll-hint");
        var configured = ownerData && ownerData.configured;
        if (state) {
          state.textContent = configured
            ? "Owner enrolled — privileged settings are gated to this profile."
            : "No owner enrolled yet.";
        }
        if (ownerName) {
          var profile = (ownerData && ownerData.profile) || {};
          ownerName.textContent = configured
            ? profile.name || "Owner"
            : "Not enrolled";
        }
        if (hint) {
          hint.classList.toggle("hidden", !!configured);
        }
        return fetch("/api/profile/residents", { cache: "no-store" });
      })
      .then(function (response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function (residentData) {
        var list = byId("resident-list");
        if (!list) return;
        list.innerHTML = "";
        var residents =
          (residentData && residentData.residents) || [];
        if (!residents.length) {
          var empty = document.createElement("li");
          empty.className = "muted";
          empty.textContent = "No resident profiles yet.";
          list.appendChild(empty);
          return;
        }
        residents.forEach(function (resident) {
          var item = document.createElement("li");
          var name = resident.name || resident.resident_id || "resident";
          item.textContent = name + " (" + (resident.role || "resident") + ")";
          list.appendChild(item);
        });
      })
      .catch(function () {
        var state = byId("profile-state");
        if (state) state.textContent = "Profile status unavailable.";
      });
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
  function updateVoiceHint() {
    var hint = byId("voice-hint");
    if (!hint) return;
    if (!voiceSettings.enabled) {
      hint.textContent =
        "Spoken replies are off (voice.enabled: false in config.yaml).";
      return;
    }
    if (voiceSettings.wake_word_enabled) {
      hint.textContent =
        'Say "' +
        voiceSettings.wake_word +
        '" then ask your question. Sentinel speaks answers back.';
      return;
    }
    hint.textContent =
      "Tap the microphone, ask aloud, then listen for Sentinel's spoken reply.";
  }

  // Browser speech: mic in, natural voice out. Wake word comes later.
  var Voice = (function () {
    var SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    var supported =
      !!SpeechRecognition && typeof window.speechSynthesis !== "undefined";
    var recognition = null;
    var listening = false;
    var voicesLoaded = false;
    var selectedVoice = null;

    function loadVoices() {
      if (!supported) return;
      var voices = window.speechSynthesis.getVoices();
      if (!voices.length) return;
      voicesLoaded = true;
      selectedVoice = pickNaturalVoice(voices, voiceSettings.language || "en-US");
    }

    function pickNaturalVoice(voices, lang) {
      var langPrefix = (lang || "en-US").split("-")[0].toLowerCase();
      var patterns = [
        /Microsoft .* Natural .* English/i,
        /Microsoft .* Online .* Natural/i,
        /Google .* English.*United States/i,
        /Google US English/i,
        /Samantha/i,
        /Karen/i,
        /Daniel/i,
        /Jenny/i,
        /Aria/i,
      ];
      var i;
      for (i = 0; i < patterns.length; i++) {
        var matched = voices.filter(function (v) {
          return (
            patterns[i].test(v.name) &&
            v.lang.toLowerCase().indexOf(langPrefix) === 0
          );
        });
        if (matched.length) return matched[0];
      }
      for (i = 0; i < voices.length; i++) {
        if (voices[i].lang.toLowerCase().indexOf(langPrefix) === 0) {
          return voices[i];
        }
      }
      return voices[0] || null;
    }

    if (supported) {
      loadVoices();
      if (window.speechSynthesis.onvoiceschanged !== undefined) {
        window.speechSynthesis.onvoiceschanged = loadVoices;
      }
    }

    function stopSpeaking() {
      if (!supported) return;
      window.speechSynthesis.cancel();
    }

    function speakWithBrowser(text) {
      if (!supported || !text) return;
      stopSpeaking();
      if (!voicesLoaded) loadVoices();
      var utter = new SpeechSynthesisUtterance(text);
      utter.lang = voiceSettings.language || "en-US";
      utter.rate = 0.95;
      utter.pitch = 1;
      if (selectedVoice) utter.voice = selectedVoice;
      window.speechSynthesis.speak(utter);
    }

    function speakWithGoogle(text) {
      fetch("/api/voice/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
      })
        .then(function (response) {
          if (!response.ok) throw new Error("TTS request failed");
          return response.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          var audio = new Audio(url);
          audio.onended = function () {
            URL.revokeObjectURL(url);
          };
          audio.play().catch(function () {});
        })
        .catch(function () {
          speakWithBrowser(text);
        });
    }

    function speak(text) {
      if (!text || !voiceSettings.enabled) return;
      if (voiceSettings.tts_provider === "google" && voiceSettings.google_tts_available) {
        speakWithGoogle(text);
        return;
      }
      if (!supported) return;
      speakWithBrowser(text);
    }

    function shouldSpeak(fromVoice) {
      if (!voiceSettings.enabled) return false;
      return fromVoice || !!voiceSettings.speak_text_queries;
    }

    function setListening(active) {
      listening = active;
      var btn = byId("voice-btn");
      if (!btn) return;
      btn.classList.toggle("listening", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    }

    function startListening(onResult, onError) {
      if (!supported || listening) return false;
      stopSpeaking();
      recognition = new SpeechRecognition();
      recognition.lang = voiceSettings.language || "en-US";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      recognition.continuous = false;

      recognition.onstart = function () {
        setListening(true);
      };
      recognition.onend = function () {
        setListening(false);
      };
      recognition.onerror = function (event) {
        setListening(false);
        if (onError) onError(event.error || "unknown");
      };
      recognition.onresult = function (event) {
        setListening(false);
        var transcript = "";
        if (event.results && event.results[0] && event.results[0][0]) {
          transcript = event.results[0][0].transcript.trim();
        }
        if (transcript && onResult) onResult(transcript);
      };

      try {
        recognition.start();
        return true;
      } catch (err) {
        setListening(false);
        if (onError) onError("start-failed");
        return false;
      }
    }

    function initUi() {
      var btn = byId("voice-btn");
      if (!btn) return;
      if (!supported) {
        btn.classList.add("unsupported");
        btn.disabled = true;
        btn.title = "Voice not supported in this browser (try Chrome or Edge)";
        return;
      }
      btn.addEventListener("click", function () {
        if (listening) {
          if (recognition) recognition.stop();
          return;
        }
        startListening(
          function (question) {
            appendChat("you", question);
            postBrain("/api/chat", { question: question }, "Thinking...", true);
          },
          function (err) {
            if (err === "no-speech") {
              appendChat("bot", "I didn't catch that. Tap the mic and try again.");
            } else if (err !== "aborted") {
              appendChat("bot", "Voice input failed (" + err + "). Try typing instead.");
            }
          }
        );
      });
    }

    return {
      supported: supported,
      speak: speak,
      shouldSpeak: shouldSpeak,
      stopSpeaking: stopSpeaking,
      initUi: initUi,
    };
  })();

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

  function setFeedbackState(text, isError) {
    var el = byId("feedback-state");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("feedback-error", !!isError);
  }

  function setFeedbackSelection(helpful) {
    selectedHelpful = helpful;
    var up = byId("feedback-up");
    var down = byId("feedback-down");
    if (up) up.classList.toggle("active", helpful === true);
    if (down) down.classList.toggle("active", helpful === false);
  }

  function setChatBusy(busy) {
    var input = byId("chat-input");
    var send = byId("chat-send");
    var summary = byId("summary-btn");
    var voiceBtn = byId("voice-btn");
    if (input) input.disabled = busy;
    if (send) send.disabled = busy;
    if (summary) summary.disabled = busy;
    if (voiceBtn && Voice.supported) voiceBtn.disabled = busy;
  }

  function postBrain(url, body, pendingText, fromVoice) {
    fromVoice = !!fromVoice;
    if (fromVoice) Voice.stopSpeaking();
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
        var answer = (data && data.answer) || "No answer was returned.";
        appendChat("bot", answer);
        latestInteraction = {
          response_id: data && data.response_id ? data.response_id : "",
          question: (body && body.question) || "",
          answer: answer,
          mode: (data && data.mode) || "footage",
          source: (data && data.source) || "ledger",
          path: (data && data.path) || "cloud_primary",
        };
        setFeedbackSelection(null);
        setFeedbackState("Rate Sentinel's last answer to improve future responses.", false);
        if (Voice.shouldSpeak(fromVoice)) {
          Voice.speak(answer);
        }
      })
      .catch(function () {
        if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
        var msg =
          "I couldn't reach the dashboard just now. If Sentinel is rebooting, wait ~20 seconds and ask again. If it keeps failing, reload this page and verify /status is reachable.";
        appendChat("bot", msg);
        if (Voice.shouldSpeak(fromVoice)) {
          Voice.speak(msg);
        }
      })
      .finally(function () {
        setChatBusy(false);
        var input = byId("chat-input");
        if (input) input.focus();
      });
  }

  function postFeedback() {
    if (!latestInteraction) {
      setFeedbackState("Ask a question first so there is an answer to rate.", true);
      return;
    }
    var correction = byId("feedback-correction");
    var remember = byId("feedback-remember");
    var payload = {
      response_id: latestInteraction.response_id,
      question: latestInteraction.question,
      answer: latestInteraction.answer,
      mode: latestInteraction.mode,
      source: latestInteraction.source,
      path: latestInteraction.path,
      helpful: selectedHelpful,
      correction: correction ? correction.value.trim() : "",
      remember_preference: remember ? !!remember.checked : false,
    };
    setFeedbackState("Saving feedback...", false);
    fetch("/api/chat/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (data && data.ok) {
          setFeedbackState("Feedback saved. Thanks - Sentinel will learn from this.", false);
          if (correction) correction.value = "";
          if (remember) remember.checked = false;
          setFeedbackSelection(null);
        } else {
          setFeedbackState(
            (data && data.message) || "Could not save feedback right now.",
            true
          );
        }
      })
      .catch(function () {
        setFeedbackState("Could not save feedback right now.", true);
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
      postBrain("/api/chat", { question: question }, "Thinking...", false);
    });
  }

  var summaryBtn = byId("summary-btn");
  if (summaryBtn) {
    summaryBtn.addEventListener("click", function () {
      appendChat("you", "Summarize my day");
      postBrain("/api/summary", {}, "Summarizing your day...", false);
    });
  }

  var feedbackUp = byId("feedback-up");
  if (feedbackUp) {
    feedbackUp.addEventListener("click", function () {
      setFeedbackSelection(true);
      setFeedbackState("Marked as helpful. Add an optional note, then submit.", false);
    });
  }
  var feedbackDown = byId("feedback-down");
  if (feedbackDown) {
    feedbackDown.addEventListener("click", function () {
      setFeedbackSelection(false);
      setFeedbackState("Marked as needs fix. Add correction text, then submit.", false);
    });
  }
  var feedbackSubmit = byId("feedback-submit");
  if (feedbackSubmit) {
    feedbackSubmit.addEventListener("click", postFeedback);
  }

  var memoryClear = byId("memory-clear");
  if (memoryClear) {
    memoryClear.addEventListener("click", function () {
      if (
        !window.confirm(
          "Clear all remembered chat preferences and feedback on this device?"
        )
      ) {
        return;
      }
      setFeedbackState("Clearing remembered preferences...", false);
      fetch("/api/chat/memory/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          if (data && data.ok) {
            setFeedbackState(
              "Cleared " + (data.deleted || 0) + " remembered item(s).",
              false
            );
          } else {
            setFeedbackState(
              (data && data.message) || "Could not clear memory right now.",
              true
            );
          }
        })
        .catch(function () {
          setFeedbackState("Could not clear memory right now.", true);
        });
    });
  }

  Voice.initUi();
  updateVoiceHint();

  // Run once right away, then on a repeating timer.
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
