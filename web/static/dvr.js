// AI Home Sentinel - DVR timeline page

(function () {
  "use strict";

  var listEl = document.getElementById("segments-list");
  var summaryEl = document.getElementById("range-summary");
  var stateEl = document.getElementById("dvr-state");
  var statsEl = document.getElementById("dvr-stats");
  var segmentSelect = document.getElementById("segment-minutes");
  var migrationEl = document.getElementById("migration-status");
  var settingsBusy = false;
  var currentSegmentMinutes = null;
  var activeVideo = null;
  var activeCard = null;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function toLocalInput(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return (
      d.getFullYear() +
      "-" +
      pad(d.getMonth() + 1) +
      "-" +
      pad(d.getDate()) +
      "T" +
      pad(d.getHours()) +
      ":" +
      pad(d.getMinutes())
    );
  }

  function localInputToIso(value) {
    if (!value) return "";
    var d = new Date(value);
    if (isNaN(d.getTime())) return "";
    return d.toISOString();
  }

  function formatSegmentRange(startIso, endIso) {
    var start = new Date(startIso);
    var end = new Date(endIso);
    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
      return startIso + " → " + endIso;
    }
    var sameDay =
      start.getFullYear() === end.getFullYear() &&
      start.getMonth() === end.getMonth() &&
      start.getDate() === end.getDate();
    var dateFmt = new Intl.DateTimeFormat(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    var timeFmt = new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
    if (sameDay) {
      return dateFmt.format(start) + " · " + timeFmt.format(start) + " – " + timeFmt.format(end);
    }
    return timeFmt.format(start) + " → " + timeFmt.format(end);
  }

  function motionLabel(score) {
    var value = Number(score || 0);
    if (value >= 0.08) return { text: "High motion", kind: "badge-bad" };
    if (value >= 0.03) return { text: "Moderate motion", kind: "badge-warn" };
    if (value >= 0.01) return { text: "Light motion", kind: "badge-ok" };
    return { text: "Quiet", kind: "badge-ok" };
  }

  function formatBytes(bytes) {
    var size = Number(bytes || 0);
    if (size < 1024 * 1024) return Math.round(size / 1024) + " KB";
    return (size / (1024 * 1024)).toFixed(1) + " MB";
  }

  function readQueryRange() {
    var params = new URLSearchParams(window.location.search);
    return { start: params.get("start") || "", end: params.get("end") || "" };
  }

  function renderStats(data) {
    if (!statsEl) return;
    statsEl.innerHTML = "";
    if (!data || !data.available) return;

    var items = [
      { label: "Storage", value: data.storage_root || "—" },
      { label: "Retention", value: (data.retention_hours || "—") + " h" },
      {
        label: "Segment length",
        value:
          data.segment_minutes != null
            ? data.segment_minutes + " min"
            : data.align_to_clock_hours
              ? "1 hour (clock-aligned)"
              : Math.round((data.segment_seconds || 3600) / 60) + " min",
      },
      { label: "Segments stored", value: String(data.segment_count || 0) },
      {
        label: "Free disk",
        value: data.disk_free_gb != null ? data.disk_free_gb + " GB" : "—",
      },
    ];

    items.forEach(function (item) {
      var block = document.createElement("div");
      block.className = "dvr-stat";
      block.innerHTML =
        '<span class="dvr-stat-label">' +
        item.label +
        '</span><strong class="dvr-stat-value">' +
        item.value +
        "</strong>";
      statsEl.appendChild(block);
    });
  }

  function renderMigrationStatus(migration) {
    if (!migrationEl) return;
    if (!migration || !migration.status || migration.status === "idle") {
      migrationEl.textContent = "";
      migrationEl.className = "dvr-migration-status muted";
      return;
    }
    var label = migration.message || migration.status;
    if (
      migration.status === "migrating" &&
      migration.total > 0
    ) {
      label =
        "Migrating old footage (" +
        migration.progress +
        "/" +
        migration.total +
        ")…";
    } else if (migration.status === "applying") {
      label = "Applying new segment length…";
    } else if (migration.status === "done") {
      label = "Migration complete";
    } else if (migration.status === "error") {
      label = migration.message || "Migration error";
    }
    migrationEl.textContent = label;
    migrationEl.className =
      "dvr-migration-status " +
      (migration.status === "error" ? "dvr-migration-error" : "dvr-migration-active");
  }

  function syncSegmentSelect(minutes) {
    if (!segmentSelect || minutes == null) return;
    currentSegmentMinutes = Number(minutes);
    segmentSelect.value = String(minutes);
  }

  function loadSettings() {
    return fetch("/api/dvr/settings", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        syncSegmentSelect(data.segment_minutes);
        renderMigrationStatus(data.migration);
        return data;
      })
      .catch(function () {
        if (migrationEl) {
          migrationEl.textContent = "Could not load segment settings.";
        }
      });
  }

  function applySegmentLength(minutes) {
    if (settingsBusy) return;
    settingsBusy = true;
    if (segmentSelect) segmentSelect.disabled = true;
    renderMigrationStatus({ status: "applying", message: "Applying new segment length…" });

    fetch("/api/dvr/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segment_minutes: minutes }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.body.ok) {
          throw new Error((result.body && result.body.error) || "Could not update segment length.");
        }
        syncSegmentSelect(result.body.segment_minutes);
        renderMigrationStatus(result.body.migration);
        loadStatus();
      })
      .catch(function (err) {
        renderMigrationStatus({
          status: "error",
          message: err.message || "Could not update segment length.",
        });
        syncSegmentSelect(currentSegmentMinutes);
      })
      .finally(function () {
        settingsBusy = false;
        if (segmentSelect) segmentSelect.disabled = false;
      });
  }

  function closeActivePlayer() {
    if (activeVideo) {
      activeVideo.pause();
      activeVideo.removeAttribute("src");
      activeVideo.load();
      activeVideo.remove();
      activeVideo = null;
    }
    if (activeCard) {
      activeCard.classList.remove("dvr-segment-playing");
      activeCard = null;
    }
  }

  function renderSegments(segments) {
    if (!listEl) return;
    listEl.innerHTML = "";
    closeActivePlayer();

    if (!segments || !segments.length) {
      summaryEl.textContent = "No DVR segments found in this range.";
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "Try widening the time range or check that recording is active.";
      listEl.appendChild(empty);
      return;
    }

    var activeCount = segments.filter(function (seg) {
      return !!seg.active;
    }).length;
    summaryEl.textContent =
      "Showing " +
      segments.length +
      " segment(s), newest last" +
      (activeCount ? " — including the segment recording now." : ".");
    segments.forEach(function (seg) {
      var card = document.createElement("article");
      card.className = "dvr-segment" + (seg.active ? " dvr-segment-active" : "");

      var header = document.createElement("div");
      header.className = "dvr-segment-header";

      var title = document.createElement("h3");
      title.textContent = formatSegmentRange(seg.start_ts, seg.expected_end_ts || seg.end_ts);
      header.appendChild(title);

      var motion = motionLabel(seg.motion_score);
      var badge = document.createElement("span");
      badge.className = "badge " + (seg.active ? "badge-warn" : motion.kind);
      badge.textContent = seg.active ? "Recording now" : motion.text;
      header.appendChild(badge);
      card.appendChild(header);

      var summary = document.createElement("p");
      summary.className = "dvr-segment-summary";
      summary.textContent = seg.summary || "Summary pending — check back after this segment finishes.";
      card.appendChild(summary);

      var meta = document.createElement("div");
      meta.className = "dvr-segment-meta muted";
      var labels = (seg.object_labels || []).join(", ") || "none";
      var people = Number(seg.person_count || 0);
      meta.textContent = seg.active
        ? "This segment is still being filmed. The playable file and final summary appear when it closes."
        : "People: " +
          people +
          " · Objects: " +
          labels +
          " · Size: " +
          formatBytes(seg.size_bytes) +
          (seg.analyzed ? " · AI summary" : "");
      card.appendChild(meta);

      if (seg.object_labels && seg.object_labels.length) {
        var chips = document.createElement("div");
        chips.className = "dvr-chips";
        seg.object_labels.slice(0, 6).forEach(function (label) {
          var chip = document.createElement("span");
          chip.className = "dvr-chip";
          chip.textContent = label;
          chips.appendChild(chip);
        });
        card.appendChild(chips);
      }

      var actions = document.createElement("div");
      actions.className = "dvr-segment-actions";

      if (seg.active || seg.playable === false) {
        var pending = document.createElement("span");
        pending.className = "muted dvr-recording-note";
        pending.textContent = "Playback unlocks after the current segment finishes.";
        actions.appendChild(pending);
      } else {
        var playBtn = document.createElement("button");
        playBtn.type = "button";
        playBtn.className = "btn btn-secondary";
        playBtn.textContent = "Play segment";
        playBtn.addEventListener("click", function () {
          closeActivePlayer();
          var videoWrap = document.createElement("div");
          videoWrap.className = "dvr-player-wrap";
          var video = document.createElement("video");
          video.controls = true;
          video.preload = "metadata";
          video.className = "dvr-player";
          video.src = "/api/dvr/segment/" + seg.id;
          videoWrap.appendChild(video);
          card.appendChild(videoWrap);
          activeVideo = video;
          activeCard = card;
          card.classList.add("dvr-segment-playing");
          video.play().catch(function () {});
        });
        actions.appendChild(playBtn);
      }
      card.appendChild(actions);

      listEl.appendChild(card);
    });
  }

  function loadStatus() {
    fetch("/api/dvr/status", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        renderStats(data);
        if (data.available) {
          stateEl.textContent = data.message || "DVR recording active.";
        } else {
          stateEl.textContent = "DVR unavailable: " + (data.message || "unknown");
        }
        if (data.segment_minutes != null) {
          syncSegmentSelect(data.segment_minutes);
        }
        renderMigrationStatus(data.migration);
      })
      .catch(function () {
        stateEl.textContent = "Could not read DVR status.";
      });
  }

  function loadRange(startIso, endIso) {
    var url =
      "/api/dvr/range?start=" +
      encodeURIComponent(startIso) +
      "&end=" +
      encodeURIComponent(endIso) +
      "&limit=300";
    fetch(url, { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        renderSegments(data.segments || []);
      })
      .catch(function () {
        summaryEl.textContent = "Could not load DVR segments.";
      });
  }

  function setQuickRange(hours) {
    var now = new Date();
    var start = new Date(now.getTime() - hours * 60 * 60 * 1000);
    document.getElementById("start").value = toLocalInput(start.toISOString());
    document.getElementById("end").value = toLocalInput(now.toISOString());
    loadRange(start.toISOString(), now.toISOString());
  }

  var query = readQueryRange();
  var now = new Date();
  var defaultEnd = query.end || now.toISOString();
  var defaultStart =
    query.start || new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
  document.getElementById("start").value = toLocalInput(defaultStart);
  document.getElementById("end").value = toLocalInput(defaultEnd);

  loadStatus();
  loadSettings();
  loadRange(defaultStart, defaultEnd);

  if (segmentSelect) {
    segmentSelect.addEventListener("change", function () {
      var next = Number(segmentSelect.value);
      if (!next || next === currentSegmentMinutes) return;
      var confirmed = window.confirm(
        "Change segment length to " +
          next +
          " minutes?\n\nFuture recordings will use this length, and existing stored footage will be rewritten in the background."
      );
      if (!confirmed) {
        syncSegmentSelect(currentSegmentMinutes);
        return;
      }
      applySegmentLength(next);
    });
  }

  setInterval(function () {
    loadSettings().then(function (data) {
      if (
        data &&
        data.migration &&
        (data.migration.status === "migrating" || data.migration.status === "applying")
      ) {
        loadStatus();
      }
    });
  }, 5000);

  document.getElementById("range-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var startIso = localInputToIso(document.getElementById("start").value);
    var endIso = localInputToIso(document.getElementById("end").value);
    loadRange(startIso, endIso);
  });

  var quickRange = document.getElementById("quick-range");
  if (quickRange) {
    quickRange.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-hours]");
      if (!btn) return;
      setQuickRange(Number(btn.getAttribute("data-hours")));
    });
  }
})();
