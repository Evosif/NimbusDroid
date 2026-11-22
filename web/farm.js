import RFB from "/core/rfb.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const DOCK_TITLES = {
  controls: "Keys & URL Broadcast",
  text: "Text Broadcast",
  apps: "Batch APK & Apps",
  gps: "Batch GPS & Scatter",
  identity: "Batch Real Device Identity",
  shell: "Batch Shell & Macros",
};

const PAGE_SIZES = { 2: 4, 3: 6, 4: 8, 6: 12 };

const STEP_DEFAULTS = {
  tap: { type: "tap", x: 0.5, y: 0.5 },
  swipe: { type: "swipe", x: 0.5, y: 0.78, x2: 0.5, y2: 0.24, durationMs: 280 },
  key: { type: "key", key: "home" },
  text: { type: "text", text: "", submit: true },
  url: { type: "url", url: "https://example.com" },
  launch: { type: "launch", package: "com.android.chrome" },
  shell: { type: "shell", command: "getprop ro.build.version.release" },
  wait: { type: "wait", ms: 600 },
};

const MACRO_KEYS = [
  ["wake", "Wake"],
  ["home", "Home"],
  ["back", "Back"],
  ["recents", "Recents"],
  ["rotate", "Rotate"],
  ["power", "Power"],
  ["volume-up", "Volume +"],
  ["volume-down", "Volume -"],
  ["enter", "Enter"],
  ["notification", "Notifications"],
];

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatDate(ts) {
  if (!ts) return "Never";
  try {
    return new Date(ts * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (_) {
    return "—";
  }
}

export function createFarmController(ctx) {
  const {
    api,
    toast,
    copyText,
    escapeHtml,
    icon,
    statusLabel,
    statusClass,
    isActive,
    formatGb,
    getMachines,
    getSummary,
    getOptions,
    getMachine,
    loadMachines,
    openCreateDialog,
    navigate,
    showLogin,
  } = ctx;

  const farm = {
    view: "devices",
    syncEnabled: true,
    masterId: null,
    masterMode: "sync",
    streamMode: localStorage.getItem("dc_wall_stream") === "snapshot" ? "snapshot" : "live",
    cols: Number(localStorage.getItem("dc_wall_cols")) || 3,
    groupFilter: "all",
    selectedIds: new Set(),
    selectionInitialized: false,
    page: 1,
    activeDock: null,
    textMode: "same",
    masterRfb: null,
    masterToken: null,
    masterExpectedDisconnect: false,
    tileRfbs: new Map(),
    renderedGridKey: "",
    snapTimer: null,
    snapBusy: false,
    gesture: null,
    gestureClearTimer: null,
    macros: [],
    macrosLoaded: false,
    tokens: [],
    tokensLoaded: false,
    fingerprints: [],
    carriers: [],
    isos: [],
    fpQuery: "",
    isoPollTimer: null,
    draftSteps: [
      { type: "key", key: "wake" },
      { type: "wait", ms: 400 },
      { type: "key", key: "home" },
    ],
  };

  if (!PAGE_SIZES[farm.cols]) farm.cols = 3;

  function parseJitter() {
    const raw = $("#wall-jitter")?.value || "0|0";
    const [ms, px] = raw.split("|").map((v) => Number(v) || 0);
    return { jitterMs: ms, jitterPx: px };
  }

  function getFilteredWallMachines() {
    const list = getMachines();
    if (farm.groupFilter === "all") return list;
    return list.filter((m) => (m.group || "default") === farm.groupFilter);
  }

  function getRunningTargets() {
    const byId = new Map(getMachines().map((m) => [m.id, m]));
    if (!farm.syncEnabled) {
      const master = byId.get(farm.masterId);
      return master?.phase === "running" ? [master.id] : [];
    }
    const ids = [...farm.selectedIds].filter((id) => byId.get(id)?.phase === "running");
    return ids;
  }

  function getSelectedAnyTargets() {
    const byId = new Map(getMachines().map((m) => [m.id, m]));
    return [...farm.selectedIds].filter((id) => byId.has(id));
  }

  /* =========================
     MASTER FRAME FIT & RFB
     ========================= */
  function fitMasterFrame() {
    const stage = $("#wall-master-stage");
    const frame = $("#wall-master-frame");
    if (!stage || !frame || farm.view !== "wall") return;
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    if (sw <= 0 || sh <= 0) return;

    const m = getMachine(farm.masterId);
    const rawW = m?.screen?.width || 1080;
    const rawH = m?.screen?.height || 2400;
    const aspect = rawW / rawH;
    const availW = Math.max(140, sw - 20);
    const availH = Math.max(200, sh - 20);

    let w = Math.min(availW, availH * aspect);
    let h = w / aspect;
    if (h > availH) {
      h = availH;
      w = h * aspect;
    }
    frame.style.width = `${Math.round(w)}px`;
    frame.style.height = `${Math.round(h)}px`;
    if (farm.masterRfb) farm.masterRfb.scaleViewport = true;
  }

  function disconnectMaster() {
    farm.masterToken = null;
    if (farm.masterRfb) {
      farm.masterExpectedDisconnect = true;
      try {
        farm.masterRfb.disconnect();
      } catch (_) {}
      farm.masterRfb = null;
    }
    const scr = $("#wall-master-screen");
    if (scr) scr.innerHTML = "";
  }

  function connectMaster(machine) {
    if (!machine || machine.phase !== "running") {
      disconnectMaster();
      $("#wall-master-empty").hidden = false;
      return;
    }
    $("#wall-master-empty").hidden = true;
    if (farm.masterRfb && farm.masterToken === machine.consoleToken) return;
    disconnectMaster();
    farm.masterToken = machine.consoleToken;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/websockify?token=${encodeURIComponent(machine.consoleToken)}`;
    try {
      const rfb = new RFB($("#wall-master-screen"), url, {
        shared: true,
        credentials: {},
        wsProtocols: ["binary"],
      });
      farm.masterRfb = rfb;
      farm.masterExpectedDisconnect = false;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.background = "#030406";
      rfb.addEventListener("connect", () => {
        if (farm.masterRfb !== rfb) return;
        requestAnimationFrame(fitMasterFrame);
      });
      rfb.addEventListener("disconnect", () => {
        if (farm.masterRfb !== rfb) return;
        farm.masterRfb = null;
        farm.masterToken = null;
        if (!farm.masterExpectedDisconnect && farm.view === "wall") {
          setTimeout(() => {
            if (farm.view !== "wall") return;
            const latest = getMachine(farm.masterId);
            if (latest?.phase === "running") connectMaster(latest);
          }, 2200);
        }
      });
    } catch (_) {}
  }

  /* =========================
     WALL TILE STREAMS & SNAPSHOTS
     ========================= */
  function disconnectAllTileRfbs() {
    for (const entry of farm.tileRfbs.values()) {
      try {
        entry.rfb.disconnect();
      } catch (_) {}
    }
    farm.tileRfbs.clear();
  }

  function stopSnapshotPolling() {
    if (farm.snapTimer) {
      clearInterval(farm.snapTimer);
      farm.snapTimer = null;
    }
  }

  function refreshSnapshots() {
    if (farm.view !== "wall" || farm.streamMode !== "snapshot" || farm.snapBusy) return;
    const imgs = $$("#wall-grid img[data-wall-snap]");
    if (!imgs.length) return;
    farm.snapBusy = true;
    let pending = imgs.length;
    const done = () => {
      pending -= 1;
      if (pending <= 0) farm.snapBusy = false;
    };
    const stamp = Date.now();
    imgs.forEach((img) => {
      const id = img.dataset.wallSnap;
      const m = getMachine(id);
      if (!m || m.phase !== "running") return done();
      const url = `/api/machines/${encodeURIComponent(id)}/screenshot?fast=1&t=${stamp}`;
      const pre = new Image();
      pre.onload = () => {
        if (img.isConnected) img.src = url;
        done();
      };
      pre.onerror = done;
      pre.src = url;
    });
  }

  function startSnapshotPolling() {
    stopSnapshotPolling();
    if (farm.view !== "wall" || farm.streamMode !== "snapshot") return;
    refreshSnapshots();
    farm.snapTimer = setInterval(refreshSnapshots, 2000);
  }

  function connectTileRfb(machine, hostEl) {
    const existing = farm.tileRfbs.get(machine.id);
    if (existing && existing.token === machine.consoleToken && existing.el === hostEl) return;
    if (existing) {
      try {
        existing.rfb.disconnect();
      } catch (_) {}
      farm.tileRfbs.delete(machine.id);
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/websockify?token=${encodeURIComponent(machine.consoleToken)}`;
    try {
      const rfb = new RFB(hostEl, url, {
        shared: true,
        credentials: {},
        wsProtocols: ["binary"],
      });
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.background = "#040507";
      farm.tileRfbs.set(machine.id, { rfb, token: machine.consoleToken, el: hostEl });
      rfb.addEventListener("disconnect", () => {
        const cur = farm.tileRfbs.get(machine.id);
        if (cur?.rfb === rfb) farm.tileRfbs.delete(machine.id);
      });
    } catch (_) {}
  }

  function updateWallControlsUI() {
    const targets = getRunningTargets();
    const syncBtn = $("#wall-sync-toggle");
    syncBtn.classList.toggle("active", farm.syncEnabled);
    $("#wall-sync-label").textContent = farm.syncEnabled ? "Sync ON" : "Master Only";
    $("#wall-sync-count").textContent = String(targets.length);

    const capture = $("#wall-master-capture");
    capture.classList.toggle("direct", farm.masterMode === "direct");
    $$("[data-master-mode]").forEach((b) => b.classList.toggle("active", b.dataset.masterMode === farm.masterMode));
    $$("[data-wall-stream]").forEach((b) => b.classList.toggle("active", b.dataset.wallStream === farm.streamMode));
    $$("[data-wall-cols]").forEach((b) => b.classList.toggle("active", Number(b.dataset.wallCols) === farm.cols));
    $("#wall-grid").style.setProperty("--wall-cols", String(farm.cols));

    const hasRunning = targets.length > 0;
    $$("[data-farm-key]").forEach((b) => {
      b.disabled = !hasRunning;
    });
    $("#farm-apk-btn").disabled = !hasRunning || !$("#farm-apk-file").files.length;
  }

  function syncWallGroupSelect() {
    const select = $("#wall-group");
    if (!select) return;
    const groups = getSummary()?.groups || [];
    const current = farm.groupFilter;
    const opts = [`<option value="all">All groups</option>`].concat(
      groups.map((g) => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`),
    );
    const html = opts.join("");
    if (select.dataset.opts !== html) {
      select.dataset.opts = html;
      select.innerHTML = html;
    }
    if (current !== "all" && !groups.includes(current)) {
      farm.groupFilter = "all";
    }
    select.value = farm.groupFilter;
  }

  function syncWall() {
    if (farm.view !== "wall") return;
    syncWallGroupSelect();

    const all = getMachines();
    const validIds = new Set(all.map((m) => m.id));
    for (const id of [...farm.selectedIds]) {
      if (!validIds.has(id)) farm.selectedIds.delete(id);
    }

    const filtered = getFilteredWallMachines();
    const running = filtered.filter((m) => m.phase === "running");

    if (!farm.selectionInitialized && running.length > 0) {
      running.forEach((m) => farm.selectedIds.add(m.id));
      farm.selectionInitialized = true;
    }

    // Choose Master device
    const currentMaster = filtered.find((m) => m.id === farm.masterId);
    if (!currentMaster || (currentMaster.phase !== "running" && running.length > 0)) {
      farm.masterId = running[0]?.id || filtered[0]?.id || null;
    }

    const masterSelect = $("#wall-master-select");
    const masterOpts = filtered.length
      ? filtered
          .map(
            (m) =>
              `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}${
                m.phase === "running" ? "" : ` (${statusLabel(m)})`
              }</option>`,
          )
          .join("")
      : `<option value="">No devices</option>`;
    if (masterSelect.dataset.opts !== masterOpts) {
      masterSelect.dataset.opts = masterOpts;
      masterSelect.innerHTML = masterOpts;
    }
    if (farm.masterId) masterSelect.value = farm.masterId;

    const masterMachine = getMachine(farm.masterId);
    connectMaster(masterMachine);
    requestAnimationFrame(fitMasterFrame);

    // Wall Matrix pagination
    const pageSize = PAGE_SIZES[farm.cols] || 6;
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    if (farm.page > pages) farm.page = pages;
    if (farm.page < 1) farm.page = 1;

    $("#wall-pager").hidden = pages <= 1;
    $("#wall-page-label").textContent = `${farm.page}/${pages}`;
    $("#wall-prev").disabled = farm.page <= 1;
    $("#wall-next").disabled = farm.page >= pages;

    const start = (farm.page - 1) * pageSize;
    const visible = filtered.slice(start, start + pageSize);
    const grid = $("#wall-grid");

    if (!visible.length) {
      disconnectAllTileRfbs();
      stopSnapshotPolling();
      farm.renderedGridKey = "empty";
      grid.innerHTML = `
        <div class="empty" style="grid-column:1 / -1">
          <span class="empty-badge">${icon("wall")}</span>
          <h3>No devices in this view</h3>
          <p>Create or start virtual devices, or switch the group filter.</p>
        </div>`;
      updateWallControlsUI();
      return;
    }

    const nextKey = `${farm.streamMode}|${visible
      .map((m) => `${m.id}:${m.phase}:${m.consoleToken}:${m.name}`)
      .join(",")}`;

    if (nextKey !== farm.renderedGridKey) {
      farm.renderedGridKey = nextKey;
      disconnectAllTileRfbs();
      grid.innerHTML = visible
        .map((m) => {
          const isRunning = m.phase === "running";
          const isMaster = m.id === farm.masterId;
          const selected = farm.selectedIds.has(m.id);
          const cls = statusClass(m);
          const screenBody = isRunning
            ? farm.streamMode === "live"
              ? `<div class="wt-rfb" data-wall-rfb="${escapeHtml(m.id)}"></div>`
              : `<img class="wt-snap" data-wall-snap="${escapeHtml(m.id)}" alt="${escapeHtml(m.name)} screen">`
            : `<div class="wt-state">
                 <span class="status-badge ${cls}">${statusLabel(m)}</span>
                 <span>${m.phase === "starting" ? "Booting Android..." : "Device offline"}</span>
                 ${
                   isActive(m)
                     ? ""
                     : `<button class="btn sm primary" data-power="${escapeHtml(m.id)}" type="button">${icon("play")}Start</button>`
                 }
               </div>`;
          return `
            <article class="wall-tile ${selected ? "selected" : ""} ${isMaster ? "is-master" : ""}" data-wall-tile="${escapeHtml(m.id)}">
              <header class="wt-head">
                <input type="checkbox" data-wall-check="${escapeHtml(m.id)}" ${selected ? "checked" : ""} aria-label="Sync ${escapeHtml(m.name)}">
                <span class="dot ${cls}"></span>
                <span class="wt-name" title="${escapeHtml(m.name)} (${escapeHtml(m.group || "default")})">${escapeHtml(m.name)}</span>
                <button class="wt-btn ${isMaster ? "pinned" : ""}" data-wall-pin="${escapeHtml(m.id)}" type="button" title="Pin as Master">${icon("star")}</button>
                <button class="wt-btn" data-open="${escapeHtml(m.id)}" type="button" title="Open Single Console">${icon("expand")}</button>
              </header>
              <div class="wt-screen-box">${screenBody}</div>
            </article>`;
        })
        .join("");

      if (farm.streamMode === "live") {
        stopSnapshotPolling();
        visible.forEach((m) => {
          if (m.phase !== "running") return;
          const host = grid.querySelector(`[data-wall-rfb="${CSS.escape(m.id)}"]`);
          if (host) connectTileRfb(m, host);
        });
      } else {
        startSnapshotPolling();
      }
    } else {
      // Cheap state-only sync without tearing down live RFBs
      $$("[data-wall-tile]", grid).forEach((tile) => {
        const id = tile.dataset.wallTile;
        const selected = farm.selectedIds.has(id);
        const isMaster = id === farm.masterId;
        tile.classList.toggle("selected", selected);
        tile.classList.toggle("is-master", isMaster);
        const cb = tile.querySelector("[data-wall-check]");
        if (cb) cb.checked = selected;
        const pin = tile.querySelector("[data-wall-pin]");
        if (pin) pin.classList.toggle("pinned", isMaster);
      });
    }

    updateWallControlsUI();
  }

  /* =========================
     SYNC TOUCH CAPTURE LAYER
     ========================= */
  function renderGestureOverlay(x1, y1, x2, y2, active) {
    const svg = $("#wall-gesture-svg");
    if (!svg) return;
    const px1 = (x1 * 100).toFixed(1);
    const py1 = (y1 * 100).toFixed(1);
    const px2 = (x2 * 100).toFixed(1);
    const py2 = (y2 * 100).toFixed(1);
    const moved = Math.hypot(x2 - x1, y2 - y1) > 0.02;
    const stroke = active ? "#3b82f6" : "#22c55e";
    svg.innerHTML = moved
      ? `<line x1="${px1}" y1="${py1}" x2="${px2}" y2="${py2}" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" stroke-dasharray="2.5 2"/>
         <circle cx="${px1}" cy="${py1}" r="2.2" fill="none" stroke="${stroke}" stroke-width="1.2"/>
         <circle cx="${px2}" cy="${py2}" r="2.8" fill="${stroke}" fill-opacity="0.35" stroke="${stroke}" stroke-width="1.2"/>`
      : `<circle cx="${px1}" cy="${py1}" r="3.4" fill="${stroke}" fill-opacity="0.28" stroke="${stroke}" stroke-width="1.3"/>`;
  }

  function clearGestureOverlay(delayMs = 0) {
    clearTimeout(farm.gestureClearTimer);
    if (delayMs <= 0) {
      const svg = $("#wall-gesture-svg");
      if (svg) svg.innerHTML = "";
      return;
    }
    farm.gestureClearTimer = setTimeout(() => {
      const svg = $("#wall-gesture-svg");
      if (svg) svg.innerHTML = "";
    }, delayMs);
  }

  function bindSyncTouchLayer() {
    const layer = $("#wall-master-capture");
    if (!layer) return;

    const coordsFromEvent = (e) => {
      const rect = layer.getBoundingClientRect();
      return {
        x: clamp((e.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
        y: clamp((e.clientY - rect.top) / Math.max(1, rect.height), 0, 1),
        clientX: e.clientX,
        clientY: e.clientY,
      };
    };

    layer.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || farm.masterMode !== "sync") return;
      const m = getMachine(farm.masterId);
      if (!m || m.phase !== "running") return;
      e.preventDefault();
      layer.setPointerCapture?.(e.pointerId);
      clearGestureOverlay(0);
      const pt = coordsFromEvent(e);
      farm.gesture = {
        pointerId: e.pointerId,
        t0: performance.now(),
        x1: pt.x,
        y1: pt.y,
        x2: pt.x,
        y2: pt.y,
        cX1: pt.clientX,
        cY1: pt.clientY,
        cX2: pt.clientX,
        cY2: pt.clientY,
      };
      renderGestureOverlay(pt.x, pt.y, pt.x, pt.y, true);
    });

    layer.addEventListener("pointermove", (e) => {
      if (!farm.gesture || farm.gesture.pointerId !== e.pointerId) return;
      const pt = coordsFromEvent(e);
      farm.gesture.x2 = pt.x;
      farm.gesture.y2 = pt.y;
      farm.gesture.cX2 = pt.clientX;
      farm.gesture.cY2 = pt.clientY;
      renderGestureOverlay(farm.gesture.x1, farm.gesture.y1, pt.x, pt.y, true);
    });

    const finishGesture = async (e, cancelled) => {
      if (!farm.gesture || farm.gesture.pointerId !== e.pointerId) return;
      const g = farm.gesture;
      farm.gesture = null;
      if (cancelled) {
        clearGestureOverlay(0);
        return;
      }
      const pt = coordsFromEvent(e);
      g.x2 = pt.x;
      g.y2 = pt.y;
      const distPx = Math.hypot(pt.clientX - g.cX1, pt.clientY - g.cY1);
      const elapsed = Math.round(performance.now() - g.t0);
      let type = "tap";
      let durationMs = 260;
      if (distPx > 12) {
        type = "swipe";
        durationMs = clamp(elapsed, 120, 1200);
      } else if (elapsed >= 450) {
        type = "longpress";
        durationMs = clamp(elapsed, 500, 2000);
      }
      renderGestureOverlay(g.x1, g.y1, g.x2, g.y2, false);
      clearGestureOverlay(320);

      // Build target list: always include Master + synced selection (or Master only if Sync OFF)
      const targets = new Set();
      if (farm.syncEnabled) {
        getRunningTargets().forEach((id) => targets.add(id));
      }
      const master = getMachine(farm.masterId);
      if (master?.phase === "running") targets.add(master.id);
      const machineIds = [...targets];
      if (!machineIds.length) return;

      const { jitterMs, jitterPx } = parseJitter();
      try {
        await api("/api/farm/touch", {
          method: "POST",
          body: {
            machineIds,
            type,
            x: Number(g.x1.toFixed(4)),
            y: Number(g.y1.toFixed(4)),
            x2: Number(g.x2.toFixed(4)),
            y2: Number(g.y2.toFixed(4)),
            durationMs,
            jitterMs,
            jitterPx,
          },
        });
      } catch (err) {
        toast(err.message, true);
      }
    };

    layer.addEventListener("pointerup", (e) => finishGesture(e, false));
    layer.addEventListener("pointercancel", (e) => finishGesture(e, true));
  }

  /* =========================
     BROADCAST DOCK & RESULTS
     ========================= */
  function openDock(name) {
    const panel = $("#farm-dock");
    if (farm.activeDock === name && !panel.hidden) {
      closeDock();
      return;
    }
    farm.activeDock = name;
    panel.hidden = false;
    $("#farm-dock-title").textContent = DOCK_TITLES[name] || "Broadcast";
    $$("[data-farm-dock]").forEach((b) => b.classList.toggle("active", b.dataset.farmDock === name));
    $$("[data-farm-panel]").forEach((s) => s.classList.toggle("active", s.dataset.farmPanel === name));
    if (name === "shell") loadMacros();
    requestAnimationFrame(fitMasterFrame);
  }

  function closeDock() {
    farm.activeDock = null;
    $("#farm-dock").hidden = true;
    $$("[data-farm-dock]").forEach((b) => b.classList.remove("active"));
    requestAnimationFrame(fitMasterFrame);
  }

  function showBroadcastResults(title, res) {
    const box = $("#farm-results-box");
    const list = $("#farm-results-list");
    if (!res || !Array.isArray(res.results)) return;
    box.hidden = false;
    $("#farm-results-title").textContent = `${title} · ${res.succeeded}/${res.total} OK`;
    list.innerHTML = res.results
      .map((r) => {
        let detail = "";
        if (!r.ok) {
          detail = r.error || "Failed";
        } else if (r.skipped) {
          detail = "Skipped (no line assigned)";
        } else if (typeof r.stdout === "string" || typeof r.stderr === "string") {
          const out = [r.stdout, r.stderr].filter(Boolean).join("\n").trim();
          detail = out ? `${out.slice(0, 180)} [exit ${r.exitCode}]` : `OK [exit ${r.exitCode}]`;
        } else if (r.location) {
          detail = `${r.location.latitude}, ${r.location.longitude}`;
        } else if (typeof r.steps === "number") {
          detail = `${r.steps} steps completed`;
        } else if (r.model && r.serial) {
          detail = `${r.label || r.model} · ${r.serial}`;
        } else if (r.message) {
          detail = r.message;
        } else {
          detail = "OK";
        }
        return `
          <div class="pkg-row">
            <div class="pkg-row-inline">
              <strong class="pkg-name">${escapeHtml(r.name || r.id)}</strong>
              <span class="tag ${r.ok ? "green" : ""}" style="${r.ok ? "" : "color:var(--red-ink);border-color:var(--red-border)"}">${r.ok ? "OK" : "ERR"}</span>
            </div>
            <span class="hint mono" style="word-break:break-word">${escapeHtml(detail)}</span>
          </div>`;
      })
      .join("");
  }

  function requireRunningTargets() {
    const ids = getRunningTargets();
    if (!ids.length) {
      toast("Select at least one running device on the Control Wall.", true);
      return null;
    }
    return ids;
  }

  async function broadcastKey(key) {
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const { jitterMs } = parseJitter();
    try {
      const res = await api("/api/farm/key", {
        method: "POST",
        body: { machineIds, key, jitterMs },
      });
      toast(`Key '${key}' sent (${res.succeeded}/${res.total}).`, res.failed > 0);
      if (!($("#farm-dock").hidden)) showBroadcastResults(`Key: ${key}`, res);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function broadcastUrl(e) {
    e.preventDefault();
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const input = $("#farm-url-input");
    const url = input.value.trim();
    if (!url) return;
    try {
      const res = await api("/api/farm/url", {
        method: "POST",
        body: { machineIds, url },
      });
      toast(`Opened URL on ${res.succeeded}/${res.total} devices.`, res.failed > 0);
      showBroadcastResults("Open URL", res);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function broadcastText(e) {
    e.preventDefault();
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const input = $("#farm-text-input");
    const text = input.value;
    if (!text.trim()) return;
    const btn = $("#farm-text-btn");
    btn.disabled = true;
    const { jitterMs } = parseJitter();
    try {
      const res = await api("/api/farm/text", {
        method: "POST",
        body: {
          machineIds,
          text,
          mode: farm.textMode,
          submit: $("#farm-text-submit").checked,
          jitterMs,
        },
      });
      toast(`Text broadcast complete (${res.succeeded}/${res.total}).`, res.failed > 0);
      showBroadcastResults(farm.textMode === "per-line" ? "1-Line Text" : "Text Broadcast", res);
      if (res.failed === 0) input.value = "";
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function setupFarmApkUpload() {
    const input = $("#farm-apk-file");
    const zone = $("#farm-apk-zone");
    const wrap = $("#farm-apk-prog-wrap");
    const bar = $("#farm-apk-prog-bar");
    const btn = $("#farm-apk-btn");

    const setFile = (file) => {
      $("#farm-apk-label").textContent = file ? file.name : "Drop or browse .apk";
      $("#farm-apk-sub").textContent = file
        ? `${(file.size / (1024 * 1024)).toFixed(2)} MB`
        : "Parallel fleet install";
      btn.disabled = !file || getRunningTargets().length === 0;
      wrap.hidden = true;
      bar.style.width = "0%";
    };

    input.addEventListener("change", () => setFile(input.files[0]));
    ["dragenter", "dragover"].forEach((ev) =>
      zone.addEventListener(ev, (e) => {
        e.preventDefault();
        zone.classList.add("dragover");
      }),
    );
    ["dragleave", "drop"].forEach((ev) =>
      zone.addEventListener(ev, (e) => {
        e.preventDefault();
        zone.classList.remove("dragover");
      }),
    );
    zone.addEventListener("drop", (e) => {
      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      setFile(file);
    });

    $("#farm-apk-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const machineIds = requireRunningTargets();
      const file = input.files[0];
      if (!machineIds || !file) return;
      if (!file.name.toLowerCase().endsWith(".apk") || file.size > 250 * 1024 * 1024) {
        toast("Select a valid .apk file under 250 MB.", true);
        return;
      }
      btn.disabled = true;
      input.disabled = true;
      wrap.hidden = false;
      bar.style.width = "0%";
      toast(`Deploying ${file.name} to ${machineIds.length} device(s)...`);

      const req = new XMLHttpRequest();
      req.open("POST", "/api/farm/install");
      req.setRequestHeader("Content-Type", "application/vnd.android.package-archive");
      req.setRequestHeader("X-Machine-Ids", machineIds.join(","));
      req.upload.onprogress = (p) => {
        if (!p.lengthComputable) return;
        bar.style.width = `${Math.round((p.loaded / p.total) * 100)}%`;
      };
      req.onload = () => {
        let res = {};
        try {
          res = JSON.parse(req.responseText);
        } catch (_) {}
        if (req.status === 401) {
          showLogin();
          return;
        }
        if (req.status >= 200 && req.status < 300 && res.ok) {
          toast(`APK installed on ${res.succeeded}/${res.total} devices.`, res.failed > 0);
          showBroadcastResults("Batch APK Install", res);
          input.value = "";
          setFile(null);
        } else {
          toast(res.message || "Batch APK install failed.", true);
        }
      };
      req.onerror = () => toast("Upload connection lost.", true);
      req.onloadend = () => {
        input.disabled = false;
        wrap.hidden = true;
        btn.disabled = !input.files.length || getRunningTargets().length === 0;
      };
      req.send(file);
    });
  }

  async function broadcastPackage(action) {
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const pkg = $("#farm-pkg-name").value.trim();
    if (!pkg) {
      toast("Enter a package ID first (e.g. com.android.chrome).", true);
      $("#farm-pkg-name").focus();
      return;
    }
    try {
      const res = await api("/api/farm/package", {
        method: "POST",
        body: { machineIds, package: pkg, action },
      });
      toast(`Package ${action}: ${res.succeeded}/${res.total} OK.`, res.failed > 0);
      showBroadcastResults(`App ${action}`, res);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function broadcastGps(e, presetLabel = "") {
    if (e) e.preventDefault();
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const lat = Number($("#farm-lat").value);
    const lon = Number($("#farm-lon").value);
    const scatterMeters = Number($("#farm-scatter").value) || 0;
    if (Number.isNaN(lat) || Number.isNaN(lon)) return;
    try {
      const res = await api("/api/farm/gps", {
        method: "POST",
        body: { machineIds, latitude: lat, longitude: lon, scatterMeters, label: presetLabel },
      });
      toast(`GPS applied (${res.succeeded}/${res.total}, ±${scatterMeters}m).`, res.failed > 0);
      showBroadcastResults("Batch GPS", res);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function broadcastShell(e) {
    e.preventDefault();
    const machineIds = requireRunningTargets();
    if (!machineIds) return;
    const cmd = $("#farm-shell-cmd").value.trim();
    if (!cmd) return;
    const btn = $("#farm-shell-run");
    btn.disabled = true;
    try {
      const res = await api("/api/farm/shell", {
        method: "POST",
        body: { machineIds, command: cmd },
      });
      toast(`Shell completed (${res.succeeded}/${res.total}).`, res.failed > 0);
      showBroadcastResults(`$ ${cmd.slice(0, 22)}`, res);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function broadcastIdentity(e) {
    e.preventDefault();
    const machineIds = getSelectedAnyTargets();
    if (!machineIds.length) {
      toast("Select one or more devices on the Control Wall first.", true);
      return;
    }
    const btn = $("#farm-id-btn");
    btn.disabled = true;
    try {
      const profileId = $("#farm-id-profile").value || "random";
      const carrierId = $("#farm-id-carrier").value || "telkomsel";
      const reboot = $("#farm-id-reboot").checked;
      const res = await api("/api/farm/identity", {
        method: "POST",
        body: { machineIds, profileId, carrierId, randomize: true, reboot },
      });
      toast(
        `Real identity applied (${res.succeeded}/${res.total})${res.rebootScheduled ? " · staggered reboot started" : ""}.`,
        res.failed > 0,
      );
      showBroadcastResults("Real Device Identity", res);
      await loadMachines(true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function populateIdentityAndIsoSelects() {
    const fps = farm.fingerprints || [];
    const carriers = farm.carriers || [];
    const isos = farm.isos || [];

    const fpOpts = fps
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label)} (${escapeHtml(p.model)})</option>`)
      .join("");
    const carrierOpts = carriers
      .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)} (${escapeHtml(c.country)})</option>`)
      .join("");

    const farmIdSel = $("#farm-id-profile");
    if (farmIdSel) {
      const prev = farmIdSel.value;
      farmIdSel.innerHTML = `<option value="random">Random Real Phone (unique per device)</option>${fpOpts}`;
      if (prev && [...farmIdSel.options].some((o) => o.value === prev)) farmIdSel.value = prev;
    }
    const singleIdSel = $("#id-profile-select");
    if (singleIdSel) {
      const prev = singleIdSel.value;
      singleIdSel.innerHTML = `<option value="random">Random Real Phone</option>${fpOpts}`;
      if (prev && [...singleIdSel.options].some((o) => o.value === prev)) singleIdSel.value = prev;
    }
    const createIdSel = $("#machine-identity");
    if (createIdSel) {
      const prev = createIdSel.value;
      createIdSel.innerHTML =
        `<option value="none">Stock / Default</option>` +
        `<option value="random">Random Real Phone (unique per device)</option>` +
        fpOpts;
      if (prev && [...createIdSel.options].some((o) => o.value === prev)) createIdSel.value = prev;
    }

    for (const selId of ["#farm-id-carrier", "#id-carrier-select"]) {
      const sel = $(selId);
      if (sel && carrierOpts) {
        const prev = sel.value;
        sel.innerHTML = carrierOpts;
        if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
      }
    }

    const isoSel = $("#machine-iso");
    if (isoSel) {
      const prev = isoSel.value;
      const installed = isos.filter((i) => i.installed);
      isoSel.innerHTML = installed.length
        ? installed
            .map((i) => `<option value="${escapeHtml(i.id)}">${escapeHtml(i.name)}</option>`)
            .join("")
        : `<option value="">No ISO downloaded (download in Templates)</option>`;
      if (prev && [...isoSel.options].some((o) => o.value === prev)) isoSel.value = prev;
    }
  }

  function renderIsoTable() {
    const tbody = $("#iso-table");
    if (!tbody) return;
    const isos = farm.isos || [];
    if (!isos.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="hint">No community ISOs listed.</td></tr>`;
      return;
    }
    let anyDownloading = false;
    tbody.innerHTML = isos
      .map((iso) => {
        const dl = iso.download;
        const isDl = dl?.status === "downloading";
        if (isDl) anyDownloading = true;
        let statusHtml = `<span class="tag">${escapeHtml(iso.sizeLabel || "Not downloaded")}</span>`;
        if (iso.installed) {
          statusHtml = `<span class="tag green">Installed · ${iso.sizeMb} MB</span>`;
        } else if (isDl) {
          statusHtml = `<span class="tag accent">Downloading ${dl.percent || 0}% (${dl.downloadedMb || 0} MB)</span>`;
        } else if (dl?.status === "error") {
          statusHtml = `<span class="tag" style="color:var(--red-ink);border-color:var(--red-border)">Error</span>`;
        }
        const actionBtn = iso.installed
          ? `<button class="btn sm primary" data-create-iso="${escapeHtml(iso.id)}" type="button">${icon("plus")}Create ISO VM</button>`
          : `<button class="btn sm" data-dl-iso="${escapeHtml(iso.id)}" type="button" ${isDl ? "disabled" : ""}>${isDl ? "Downloading..." : "Download ISO"}</button>`;
        return `
          <tr>
            <td><strong>${escapeHtml(iso.name)}</strong><div class="os-sub">${escapeHtml(iso.filename)}</div></td>
            <td>${escapeHtml(iso.androidVersion)}</td>
            <td><span class="hint">${escapeHtml(iso.variant)}</span></td>
            <td>${statusHtml}</td>
            <td class="col-actions">${actionBtn}</td>
          </tr>`;
      })
      .join("");

    if (anyDownloading && farm.view === "templates") {
      if (!farm.isoPollTimer) {
        farm.isoPollTimer = setInterval(loadIsos, 2500);
      }
    } else if (farm.isoPollTimer) {
      clearInterval(farm.isoPollTimer);
      farm.isoPollTimer = null;
    }
  }

  function renderFingerprintsTable() {
    const tbody = $("#fp-table");
    if (!tbody) return;
    const q = farm.fpQuery.trim().toLowerCase();
    const list = (farm.fingerprints || []).filter(
      (p) =>
        !q ||
        p.label.toLowerCase().includes(q) ||
        p.brand.toLowerCase().includes(q) ||
        p.model.toLowerCase().includes(q) ||
        p.device.toLowerCase().includes(q) ||
        p.fingerprint.toLowerCase().includes(q),
    );
    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="hint">No matching device fingerprints.</td></tr>`;
      return;
    }
    tbody.innerHTML = list
      .map(
        (p) => `
          <tr>
            <td><strong>${escapeHtml(p.label)}</strong></td>
            <td class="mono">${escapeHtml(p.model)} · ${escapeHtml(p.device)}</td>
            <td><span class="os-primary">${escapeHtml(p.socManufacturer || "")} ${escapeHtml(p.socModel || "")}</span><span class="os-sub">Patch ${escapeHtml(p.securityPatch || "—")}</span></td>
            <td><div class="fp-cell" title="${escapeHtml(p.fingerprint)}">${escapeHtml(p.fingerprint)}</div></td>
            <td class="col-actions">
              <div class="row-actions">
                <span class="tag ${p.custom ? "accent" : ""}">${escapeHtml(p.source || "Community")}</span>
                ${
                  p.custom
                    ? `<button class="icon-btn ghost" data-del-fp="${escapeHtml(p.id)}" type="button" title="Delete custom fingerprint">${icon("trash")}</button>`
                    : ""
                }
              </div>
            </td>
          </tr>`,
      )
      .join("");
  }

  async function loadIsos() {
    try {
      const res = await api("/api/isos");
      farm.isos = res.isos || [];
      populateIdentityAndIsoSelects();
      renderIsoTable();
    } catch (_) {}
  }

  async function loadFingerprints() {
    try {
      const res = await api("/api/fingerprints");
      farm.fingerprints = res.fingerprints || [];
      farm.carriers = res.carriers || [];
      populateIdentityAndIsoSelects();
      renderFingerprintsTable();
    } catch (_) {}
  }

  async function downloadIso(isoId) {
    try {
      await api(`/api/isos/${encodeURIComponent(isoId)}/download`, { method: "POST", body: {} });
      toast("ISO download started in background.");
      await loadIsos();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function syncFingerprints() {
    const btn = $("#fp-sync-btn");
    btn.disabled = true;
    toast("Syncing official Google Pixel OTA fingerprints...");
    try {
      const res = await api("/api/fingerprints/sync", { method: "POST", body: {} });
      farm.fingerprints = res.fingerprints || [];
      populateIdentityAndIsoSelects();
      renderFingerprintsTable();
      toast(`Synced ${res.syncedCount || 0} official real-device fingerprints.`);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function importFingerprint(e) {
    e.preventDefault();
    const btn = $("#fp-import-submit");
    const msg = $("#fp-import-message");
    btn.disabled = true;
    msg.classList.remove("error");
    msg.textContent = "Importing...";
    try {
      const body = {
        raw: $("#fp-import-raw").value.trim(),
      };
      const label = $("#fp-import-label").value.trim();
      const model = $("#fp-import-model").value.trim();
      if (label) body.label = label;
      if (model) body.model = model;
      const res = await api("/api/fingerprints/import", { method: "POST", body });
      farm.fingerprints = res.fingerprints || [];
      populateIdentityAndIsoSelects();
      renderFingerprintsTable();
      $("#fp-import-dialog").close();
      $("#fp-import-form").reset();
      toast(`Imported '${res.profile?.label || "fingerprint"}'.`);
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    } finally {
      btn.disabled = false;
    }
  }

  async function wallPower(action) {
    const machineIds = getSelectedAnyTargets();
    if (!machineIds.length) {
      toast("Select one or more devices on the Control Wall first.", true);
      return;
    }
    try {
      const res = await api("/api/farm/power", {
        method: "POST",
        body: { machineIds, action, staggerSeconds: 2.5 },
      });
      if (res.scheduled) {
        toast(`Staggered ${action} scheduled for ${res.scheduled} devices (2.5s interval).`);
      } else {
        toast(`Power ${action}: ${res.succeeded}/${res.total} OK.`, res.failed > 0);
      }
      await loadMachines(true);
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* =========================
     VIEW 3: TEMPLATES & IMAGES
     ========================= */
  function renderTemplates() {
    renderIsoTable();
    renderFingerprintsTable();
    const machines = getMachines();
    const options = getOptions();
    const templates = machines.filter((m) => m.isTemplate);
    const grid = $("#templates-grid");

    if (!templates.length) {
      grid.innerHTML = `
        <div class="empty" style="grid-column:1 / -1;padding:28px 16px">
          <span class="empty-badge">${icon("layers")}</span>
          <h3>No Golden Templates marked yet</h3>
          <p>Stop any configured device and select "Edit details &amp; group" to promote it as a reusable Golden Template, or batch clone any stopped device directly.</p>
          <button class="btn" data-open-bclone type="button">${icon("clone")}Batch Clone Stopped Device</button>
        </div>`;
    } else {
      grid.innerHTML = templates
        .map(
          (m) => `
            <article class="device-card">
              <div class="dc-top">
                <button class="dc-head" data-open="${escapeHtml(m.id)}" type="button">
                  <span class="vm-avatar running">${icon("layers")}</span>
                  <span>
                    <strong>${escapeHtml(m.name)}</strong>
                    <small>${escapeHtml(m.deviceLabel)} · ${escapeHtml(m.androidVersion)}</small>
                  </span>
                </button>
                <span class="tag green">TEMPLATE</span>
              </div>
              <div class="dc-specs">
                <div class="dc-spec"><span>CPU</span><strong>${m.cpu} vCPU</strong></div>
                <div class="dc-spec"><span>RAM</span><strong>${formatGb(m.ram)}</strong></div>
                <div class="dc-spec"><span>Disk</span><strong>${m.storage} GB</strong></div>
              </div>
              <div class="dc-meta">
                <span class="tag">${escapeHtml(m.group || "default")}</span>
                <div class="dc-tags">${m.root ? `<span class="tag accent">ROOT</span>` : ""}</div>
              </div>
              <div class="dc-foot">
                <button class="btn primary open-btn" data-bclone-source="${escapeHtml(m.id)}" type="button">${icon("clone")}Batch Clone × N</button>
                <button class="btn" data-unmark-tpl="${escapeHtml(m.id)}" type="button">Unmark</button>
              </div>
            </article>`,
        )
        .join("");
    }

    if (options) {
      $("#images-table").innerHTML = (options.systems || [])
        .map(
          (s) => `
            <tr>
              <td><strong>${escapeHtml(s.label)}</strong></td>
              <td class="mono">API ${s.api}</td>
              <td><span class="tag ${s.installed ? "green" : ""}">${s.installed ? "Installed" : "Auto-download"}</span></td>
            </tr>`,
        )
        .join("");

      const lim = options.limits || {};
      $("#profiles-limits").textContent = `Max ${lim.cpu || "—"} vCPU/VM · ${lim.ram || "—"} GB RAM · ${lim.machines || 64} slots`;
      $("#profiles-table").innerHTML = (options.devices || [])
        .map(
          (d) => `
            <tr>
              <td><strong>${escapeHtml(d.name)}</strong></td>
              <td>${escapeHtml(d.kind)}</td>
              <td class="mono">${d.width}×${d.height} @ ${d.density}dpi</td>
            </tr>`,
        )
        .join("");
    }
  }

  function openBatchCloneDialog(preselectId = null) {
    const stopped = getMachines().filter((m) => !isActive(m));
    if (!stopped.length) {
      toast("Stop a source device first before running Batch Clone.", true);
      return;
    }
    stopped.sort((a, b) => Number(Boolean(b.isTemplate)) - Number(Boolean(a.isTemplate)));
    $("#bclone-source").innerHTML = stopped
      .map(
        (m) =>
          `<option value="${escapeHtml(m.id)}">${m.isTemplate ? "[Template] " : ""}${escapeHtml(m.name)} (${escapeHtml(m.androidVersion)})</option>`,
      )
      .join("");
    if (preselectId && stopped.some((m) => m.id === preselectId)) {
      $("#bclone-source").value = preselectId;
    }
    const src = stopped.find((m) => m.id === $("#bclone-source").value) || stopped[0];
    $("#bclone-prefix").value = `${src.name}-farm`.slice(0, 36);
    $("#bclone-group").value = src.group || "default";
    $("#bclone-count").value = "3";
    $("#bclone-start").checked = false;
    $("#bclone-message").textContent = "";
    $("#bclone-message").classList.remove("error");
    $("#batch-clone-dialog").showModal();
    setTimeout(() => $("#bclone-prefix").select(), 40);
  }

  async function submitBatchClone(e) {
    e.preventDefault();
    const btn = $("#bclone-submit");
    const msg = $("#bclone-message");
    const payload = {
      sourceId: $("#bclone-source").value,
      prefix: $("#bclone-prefix").value.trim(),
      count: Number($("#bclone-count").value) || 1,
      group: $("#bclone-group").value.trim() || "default",
      start: $("#bclone-start").checked,
    };
    btn.disabled = true;
    msg.classList.remove("error");
    msg.textContent = `Cloning ${payload.count} device(s)...`;
    try {
      const res = await api("/api/farm/batch-clone", { method: "POST", body: payload });
      $("#batch-clone-dialog").close();
      await loadMachines(true);
      toast(`Deployed ${res.count} cloned device(s).`);
      navigate("devices");
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    } finally {
      btn.disabled = false;
    }
  }

  /* =========================
     VIEW 4: MACROS & AUTOMATION
     ========================= */
  function stepSummary(s) {
    if (s.type === "tap") return `tap(${s.x}, ${s.y})`;
    if (s.type === "swipe") return `swipe(${s.x},${s.y}→${s.x2},${s.y2})`;
    if (s.type === "key") return `key:${s.key}`;
    if (s.type === "text") return `text:"${String(s.text || "").slice(0, 14)}"`;
    if (s.type === "url") return `url:${String(s.url || "").slice(0, 20)}`;
    if (s.type === "launch") return `launch:${s.package}`;
    if (s.type === "shell") return `$ ${String(s.command || "").slice(0, 18)}`;
    if (s.type === "wait") return `wait ${s.ms}ms`;
    return s.type;
  }

  function renderMacros() {
    const running = getMachines().filter((m) => m.phase === "running");
    const hint = $("#macro-running-hint");
    if (hint) hint.textContent = `${running.length} running target(s) ready`;

    const list = $("#macros-list");
    if (list) {
      if (!farm.macros.length) {
        list.innerHTML = `<p class="hint" style="padding:12px">No macros saved yet. Build one on the right.</p>`;
      } else {
        list.innerHTML = farm.macros
          .map(
            (m) => `
              <article class="macro-card">
                <div class="macro-top">
                  <strong>${escapeHtml(m.name)}</strong>
                  <span class="tag">${(m.steps || []).length} steps</span>
                </div>
                ${m.description ? `<p class="macro-desc">${escapeHtml(m.description)}</p>` : ""}
                <div class="dc-tags" style="flex-wrap:wrap;margin-bottom:10px">
                  ${(m.steps || [])
                    .slice(0, 6)
                    .map((s) => `<span class="tag">${escapeHtml(stepSummary(s))}</span>`)
                    .join("")}
                  ${(m.steps || []).length > 6 ? `<span class="tag">+${m.steps.length - 6}</span>` : ""}
                </div>
                <div class="macro-foot">
                  <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <button class="btn sm primary" data-run-macro="${escapeHtml(m.id)}" data-macro-scope="all" type="button" ${running.length ? "" : "disabled"}>${icon("play")}Run on Running (${running.length})</button>
                    <button class="btn sm" data-run-macro="${escapeHtml(m.id)}" data-macro-scope="wall" type="button" ${getRunningTargets().length ? "" : "disabled"}>Run on Wall (${getRunningTargets().length})</button>
                  </div>
                  <button class="icon-btn ghost" data-del-macro="${escapeHtml(m.id)}" type="button" title="Delete macro" aria-label="Delete macro">${icon("trash")}</button>
                </div>
              </article>`,
          )
          .join("");
      }
    }

    const quick = $("#farm-macro-quick");
    if (quick) {
      quick.innerHTML = farm.macros.length
        ? farm.macros
            .map(
              (m) => `
                <div class="pkg-row">
                  <div class="pkg-row-inline">
                    <span class="pkg-name" title="${escapeHtml(m.description || m.name)}">${escapeHtml(m.name)}</span>
                    <button class="btn sm primary" data-run-macro="${escapeHtml(m.id)}" data-macro-scope="wall" type="button">${icon("play")}Run</button>
                  </div>
                </div>`,
            )
            .join("")
        : `<p class="hint">Create macros in the Macros tab.</p>`;
    }
  }

  function renderStepFields(step, idx) {
    if (step.type === "wait") {
      return `<input type="number" min="50" max="15000" step="50" data-step-idx="${idx}" data-step-key="ms" value="${escapeHtml(step.ms ?? 500)}" placeholder="ms">`;
    }
    if (step.type === "key") {
      return `<select data-step-idx="${idx}" data-step-key="key">${MACRO_KEYS.map(
        ([val, lbl]) => `<option value="${val}" ${step.key === val ? "selected" : ""}>${lbl}</option>`,
      ).join("")}</select>`;
    }
    if (step.type === "tap") {
      return `
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="x" value="${escapeHtml(step.x ?? 0.5)}" placeholder="x (0-1)">
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="y" value="${escapeHtml(step.y ?? 0.5)}" placeholder="y (0-1)">`;
    }
    if (step.type === "swipe") {
      return `
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="x" value="${escapeHtml(step.x ?? 0.5)}" title="Start X">
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="y" value="${escapeHtml(step.y ?? 0.78)}" title="Start Y">
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="x2" value="${escapeHtml(step.x2 ?? 0.5)}" title="End X">
        <input type="number" min="0" max="1" step="0.01" data-step-idx="${idx}" data-step-key="y2" value="${escapeHtml(step.y2 ?? 0.24)}" title="End Y">`;
    }
    if (step.type === "text") {
      return `<input type="text" data-step-idx="${idx}" data-step-key="text" value="${escapeHtml(step.text || "")}" placeholder="Text to type">`;
    }
    if (step.type === "url") {
      return `<input type="text" data-step-idx="${idx}" data-step-key="url" value="${escapeHtml(step.url || "")}" placeholder="https://...">`;
    }
    if (step.type === "launch") {
      return `<input type="text" data-step-idx="${idx}" data-step-key="package" value="${escapeHtml(step.package || "")}" placeholder="com.example.app">`;
    }
    return `<input type="text" data-step-idx="${idx}" data-step-key="command" value="${escapeHtml(step.command || "")}" placeholder="adb shell command">`;
  }

  function renderStepsEditor() {
    const editor = $("#macro-steps-editor");
    if (!editor) return;
    const types = ["tap", "swipe", "key", "text", "url", "launch", "shell", "wait"];
    editor.innerHTML = farm.draftSteps
      .map(
        (step, idx) => `
          <div class="step-row">
            <select data-step-type="${idx}" aria-label="Step type">
              ${types.map((t) => `<option value="${t}" ${step.type === t ? "selected" : ""}>${t}</option>`).join("")}
            </select>
            <div class="step-fields">${renderStepFields(step, idx)}</div>
            <button class="wt-btn" data-step-rm="${idx}" type="button" title="Remove step" ${farm.draftSteps.length <= 1 ? "disabled" : ""}>${icon("close")}</button>
          </div>`,
      )
      .join("");
  }

  async function loadMacros() {
    try {
      const res = await api("/api/macros");
      farm.macros = res.macros || [];
      farm.macrosLoaded = true;
      renderMacros();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function saveMacro(e) {
    e.preventDefault();
    const name = $("#macro-name").value.trim();
    const description = $("#macro-desc").value.trim();
    if (!name || !farm.draftSteps.length) return;
    try {
      await api("/api/macros", {
        method: "POST",
        body: { name, description, steps: farm.draftSteps },
      });
      $("#macro-name").value = "";
      $("#macro-desc").value = "";
      farm.draftSteps = [{ type: "key", key: "wake" }, { type: "wait", ms: 400 }];
      renderStepsEditor();
      await loadMacros();
      toast(`Macro '${name}' saved.`);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function runMacro(macroId, scope) {
    const runningAll = getMachines().filter((m) => m.phase === "running").map((m) => m.id);
    const machineIds = scope === "wall" ? getRunningTargets() : runningAll;
    if (!machineIds.length) {
      toast("No running target devices available.", true);
      return;
    }
    const { jitterMs } = parseJitter();
    toast(`Running macro on ${machineIds.length} device(s)...`);
    try {
      const res = await api(`/api/macros/${encodeURIComponent(macroId)}/run`, {
        method: "POST",
        body: { machineIds, jitterMs },
      });
      toast(`Macro finished (${res.succeeded}/${res.total} OK).`, res.failed > 0);
      if (farm.view === "wall") showBroadcastResults("Macro Run", res);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteMacro(macroId) {
    try {
      await api(`/api/macros/${encodeURIComponent(macroId)}`, { method: "DELETE" });
      await loadMacros();
      toast("Macro deleted.");
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* =========================
     VIEW 5: API & CLUSTER
     ========================= */
  function renderClusterMetrics() {
    const s = getSummary();
    if (!s) return;
    $("#cl-cpu").textContent = `${s.hostCpu} vCPU`;
    $("#cl-cpu-sub").textContent = `${s.cpuAllocated} allocated · ${s.cpuPercent}% util · max ${s.maxVmCpu}/VM`;
    $("#cl-ram").textContent = `${s.hostRam} GB`;
    $("#cl-ram-sub").textContent = `${s.hostRamAvailable} GB free · ${s.ramPercent}% used`;
    $("#cl-disk").textContent = `${s.diskFreeGb} GB free`;
    $("#cl-disk-sub").textContent = `${s.diskTotalGb} GB total · ${s.diskPercent}% used`;
    $("#cl-slots").textContent = `${s.total} / ${s.maxMachines}`;

    const origin = location.origin;
    const sampleId = getMachines()[0]?.id || "device-01";
    $("#api-examples").textContent = [
      `# List all virtual devices`,
      `curl -H "Authorization: Bearer YOUR_TOKEN" \\`,
      `  ${origin}/api/machines`,
      ``,
      `# Synchronized tap across multiple devices (normalized 0.0–1.0)`,
      `curl -X POST -H "Authorization: Bearer YOUR_TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"machineIds":["${sampleId}"],"type":"tap","x":0.5,"y":0.5}' \\`,
      `  ${origin}/api/farm/touch`,
      ``,
      `# Batch clone 5 devices with staggered boot`,
      `curl -X POST -H "Authorization: Bearer YOUR_TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"sourceId":"${sampleId}","prefix":"worker","count":5,"start":true}' \\`,
      `  ${origin}/api/farm/batch-clone`,
    ].join("\n");
  }

  function renderTokens() {
    const list = $("#tokens-list");
    if (!list) return;
    if (!farm.tokens.length) {
      list.innerHTML = `<p class="hint">No API tokens created yet.</p>`;
      return;
    }
    list.innerHTML = farm.tokens
      .map(
        (t) => `
          <div class="pkg-row">
            <div class="pkg-row-inline">
              <div>
                <strong class="pkg-name">${escapeHtml(t.name)}</strong>
                <div class="hint mono">${escapeHtml(t.prefix)} · Last used: ${escapeHtml(formatDate(t.lastUsedAt))}</div>
              </div>
              <button class="btn sm danger-outline" data-del-token="${escapeHtml(t.id)}" type="button">Revoke</button>
            </div>
          </div>`,
      )
      .join("");
  }

  async function loadTokens() {
    try {
      const res = await api("/api/tokens");
      farm.tokens = res.tokens || [];
      farm.tokensLoaded = true;
      renderTokens();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function createToken(e) {
    e.preventDefault();
    const input = $("#token-name");
    const name = input.value.trim();
    if (!name) return;
    try {
      const res = await api("/api/tokens", { method: "POST", body: { name } });
      input.value = "";
      $("#token-secret-val").value = res.token?.token || "";
      $("#token-reveal").hidden = false;
      await loadTokens();
      toast("API token generated.");
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteToken(id) {
    try {
      await api(`/api/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
      await loadTokens();
      toast("API token revoked.");
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* =========================
     BIND EVENTS
     ========================= */
  function bindEvents() {
    bindSyncTouchLayer();
    setupFarmApkUpload();
    renderStepsEditor();

    $("#wall-sync-toggle").addEventListener("click", () => {
      farm.syncEnabled = !farm.syncEnabled;
      updateWallControlsUI();
    });

    $("#wall-group").addEventListener("change", (e) => {
      farm.groupFilter = e.target.value;
      farm.page = 1;
      syncWall();
    });

    $("#wall-master-select").addEventListener("change", (e) => {
      farm.masterId = e.target.value || null;
      syncWall();
    });

    $$("[data-master-mode]").forEach((btn) =>
      btn.addEventListener("click", () => {
        farm.masterMode = btn.dataset.masterMode === "direct" ? "direct" : "sync";
        updateWallControlsUI();
      }),
    );

    $$("[data-wall-stream]").forEach((btn) =>
      btn.addEventListener("click", () => {
        farm.streamMode = btn.dataset.wallStream === "snapshot" ? "snapshot" : "live";
        localStorage.setItem("dc_wall_stream", farm.streamMode);
        syncWall();
      }),
    );

    $$("[data-wall-cols]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const cols = Number(btn.dataset.wallCols) || 3;
        if (!PAGE_SIZES[cols]) return;
        farm.cols = cols;
        farm.page = 1;
        localStorage.setItem("dc_wall_cols", String(cols));
        syncWall();
      }),
    );

    $("#wall-select-running").addEventListener("click", () => {
      farm.selectionInitialized = true;
      farm.selectedIds.clear();
      getFilteredWallMachines()
        .filter((m) => m.phase === "running")
        .forEach((m) => farm.selectedIds.add(m.id));
      syncWall();
    });

    $("#wall-select-all").addEventListener("click", () => {
      farm.selectionInitialized = true;
      farm.selectedIds.clear();
      getFilteredWallMachines().forEach((m) => farm.selectedIds.add(m.id));
      syncWall();
    });

    $("#wall-clear-sel").addEventListener("click", () => {
      farm.selectionInitialized = true;
      farm.selectedIds.clear();
      syncWall();
    });

    $("#wall-power-start").addEventListener("click", () => wallPower("start"));
    $("#wall-power-stop").addEventListener("click", () => wallPower("stop"));
    $("#wall-power-reboot").addEventListener("click", () => wallPower("restart"));

    $("#wall-prev").addEventListener("click", () => {
      if (farm.page > 1) {
        farm.page -= 1;
        syncWall();
      }
    });
    $("#wall-next").addEventListener("click", () => {
      farm.page += 1;
      syncWall();
    });

    $("#wall-grid").addEventListener("change", (e) => {
      const id = e.target.dataset?.wallCheck;
      if (!id) return;
      farm.selectionInitialized = true;
      if (e.target.checked) farm.selectedIds.add(id);
      else farm.selectedIds.delete(id);
      syncWall();
    });

    $("#wall-grid").addEventListener("click", (e) => {
      const pin = e.target.closest("[data-wall-pin]")?.dataset.wallPin;
      if (pin) {
        farm.masterId = pin;
        syncWall();
      }
    });

    $$("[data-farm-dock]").forEach((btn) =>
      btn.addEventListener("click", () => openDock(btn.dataset.farmDock)),
    );
    $("#farm-dock-close").addEventListener("click", closeDock);

    $$("[data-farm-key]").forEach((btn) =>
      btn.addEventListener("click", () => broadcastKey(btn.dataset.farmKey)),
    );
    $("#farm-url-form").addEventListener("submit", broadcastUrl);

    $$("[data-farm-text-mode]").forEach((btn) =>
      btn.addEventListener("click", () => {
        farm.textMode = btn.dataset.farmTextMode === "per-line" ? "per-line" : "same";
        $$("[data-farm-text-mode]").forEach((b) => b.classList.toggle("active", b === btn));
      }),
    );
    $("#farm-text-form").addEventListener("submit", broadcastText);

    $("#farm-pkg-form").addEventListener("submit", (e) => {
      e.preventDefault();
      broadcastPackage("launch");
    });
    $$("[data-farm-pkg]").forEach((btn) =>
      btn.addEventListener("click", () => broadcastPackage(btn.dataset.farmPkg)),
    );

    $("#farm-scatter").addEventListener("input", (e) => {
      $("#farm-scatter-val").textContent = e.target.value;
    });
    $$("[data-farm-loc]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const [label, lat, lon] = btn.dataset.farmLoc.split("|");
        $("#farm-lat").value = lat;
        $("#farm-lon").value = lon;
        broadcastGps(null, label);
      }),
    );
    $("#farm-gps-form").addEventListener("submit", (e) => broadcastGps(e, ""));
    $("#farm-shell-form").addEventListener("submit", broadcastShell);
    $("#farm-identity-form").addEventListener("submit", broadcastIdentity);
    $("#farm-results-clear").addEventListener("click", () => {
      $("#farm-results-box").hidden = true;
      $("#farm-results-list").innerHTML = "";
    });

    // Community ISOs & Fingerprints
    $("#iso-refresh-btn").addEventListener("click", loadIsos);
    $("#iso-table").addEventListener("click", (e) => {
      const dl = e.target.closest("[data-dl-iso]")?.dataset.dlIso;
      if (dl) return downloadIso(dl);
      const createIso = e.target.closest("[data-create-iso]")?.dataset.createIso;
      if (createIso) return openCreateDialog({ backend: "iso", isoId: createIso });
    });
    $("#fp-search").addEventListener("input", (e) => {
      farm.fpQuery = e.target.value;
      renderFingerprintsTable();
    });
    $("#fp-sync-btn").addEventListener("click", syncFingerprints);
    $("#fp-import-btn").addEventListener("click", () => {
      $("#fp-import-message").textContent = "";
      $("#fp-import-message").classList.remove("error");
      $("#fp-import-dialog").showModal();
      setTimeout(() => $("#fp-import-raw").focus(), 40);
    });
    $("#fp-import-form").addEventListener("submit", importFingerprint);
    $("#fp-table").addEventListener("click", async (e) => {
      const del = e.target.closest("[data-del-fp]")?.dataset.delFp;
      if (!del) return;
      try {
        const res = await api(`/api/fingerprints/${encodeURIComponent(del)}`, { method: "DELETE" });
        farm.fingerprints = res.fingerprints || [];
        populateIdentityAndIsoSelects();
        renderFingerprintsTable();
        toast("Custom fingerprint deleted.");
      } catch (err) {
        toast(err.message, true);
      }
    });

    // Templates & Batch Clone
    $("#open-batch-clone-btn").addEventListener("click", () => openBatchCloneDialog());
    $("#batch-clone-form").addEventListener("submit", submitBatchClone);
    $("#templates-grid").addEventListener("click", async (e) => {
      const bcloneSrc = e.target.closest("[data-bclone-source]")?.dataset.bcloneSource;
      if (bcloneSrc) return openBatchCloneDialog(bcloneSrc);
      if (e.target.closest("[data-open-bclone]")) return openBatchCloneDialog();
      const unmark = e.target.closest("[data-unmark-tpl]")?.dataset.unmarkTpl;
      if (unmark) {
        try {
          await api(`/api/machines/${encodeURIComponent(unmark)}/meta`, {
            method: "POST",
            body: { isTemplate: false },
          });
          await loadMachines(true);
          toast("Removed template flag.");
        } catch (err) {
          toast(err.message, true);
        }
      }
    });

    // Macros
    $("#macro-add-step").addEventListener("click", () => {
      if (farm.draftSteps.length >= 30) return toast("Maximum 30 steps per macro.", true);
      farm.draftSteps.push({ ...STEP_DEFAULTS.tap });
      renderStepsEditor();
    });
    $("#macro-steps-editor").addEventListener("change", (e) => {
      const typeIdx = e.target.dataset?.stepType;
      if (typeIdx !== undefined) {
        const nextType = e.target.value;
        farm.draftSteps[Number(typeIdx)] = { ...(STEP_DEFAULTS[nextType] || { type: nextType }) };
        renderStepsEditor();
        return;
      }
      const idx = e.target.dataset?.stepIdx;
      const key = e.target.dataset?.stepKey;
      if (idx !== undefined && key) {
        const numKeys = new Set(["x", "y", "x2", "y2", "ms", "durationMs"]);
        farm.draftSteps[Number(idx)][key] = numKeys.has(key) ? Number(e.target.value) : e.target.value;
      }
    });
    $("#macro-steps-editor").addEventListener("input", (e) => {
      const idx = e.target.dataset?.stepIdx;
      const key = e.target.dataset?.stepKey;
      if (idx !== undefined && key) {
        const numKeys = new Set(["x", "y", "x2", "y2", "ms", "durationMs"]);
        farm.draftSteps[Number(idx)][key] = numKeys.has(key) ? Number(e.target.value) : e.target.value;
      }
    });
    $("#macro-steps-editor").addEventListener("click", (e) => {
      const rm = e.target.closest("[data-step-rm]")?.dataset.stepRm;
      if (rm !== undefined && farm.draftSteps.length > 1) {
        farm.draftSteps.splice(Number(rm), 1);
        renderStepsEditor();
      }
    });
    $("#macro-form").addEventListener("submit", saveMacro);

    document.addEventListener("click", (e) => {
      const runBtn = e.target.closest("[data-run-macro]");
      if (runBtn) {
        runMacro(runBtn.dataset.runMacro, runBtn.dataset.macroScope);
        return;
      }
      const delMacro = e.target.closest("[data-del-macro]")?.dataset.delMacro;
      if (delMacro) {
        deleteMacro(delMacro);
        return;
      }
      const delTok = e.target.closest("[data-del-token]")?.dataset.delToken;
      if (delTok) deleteToken(delTok);
    });

    // Cluster & API Tokens
    $("#token-form").addEventListener("submit", createToken);
    $("#token-copy-btn").addEventListener("click", async () => {
      const val = $("#token-secret-val").value;
      const ok = await copyText(val);
      toast(ok ? "API token copied." : "Copy failed.", !ok);
    });

    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => fitMasterFrame()).observe($("#wall-master-stage"));
    }
    window.addEventListener("resize", () => fitMasterFrame());
  }

  bindEvents();

  return {
    onRouteChange(view) {
      const leavingWall = farm.view === "wall" && view !== "wall";
      farm.view = view;
      if (leavingWall) {
        disconnectMaster();
        disconnectAllTileRfbs();
        stopSnapshotPolling();
        farm.renderedGridKey = "";
      }
      if (view === "wall") {
        syncWall();
        if (!farm.macrosLoaded) loadMacros();
      } else if (view === "templates") {
        renderTemplates();
        loadIsos();
        loadFingerprints();
      } else if (view === "macros") {
        loadMacros();
      } else if (view === "cluster") {
        renderClusterMetrics();
        loadTokens();
      }
      if (view !== "templates" && farm.isoPollTimer) {
        clearInterval(farm.isoPollTimer);
        farm.isoPollTimer = null;
      }
    },
    syncCatalogs(opts) {
      if (!opts) return;
      if (Array.isArray(opts.fingerprints)) farm.fingerprints = opts.fingerprints;
      if (Array.isArray(opts.carriers)) farm.carriers = opts.carriers;
      if (Array.isArray(opts.isos)) farm.isos = opts.isos;
      populateIdentityAndIsoSelects();
      if (farm.view === "templates") renderTemplates();
    },
    onDataUpdated() {
      if (farm.view === "wall") syncWall();
      if (farm.view === "templates") renderTemplates();
      if (farm.view === "macros") renderMacros();
      if (farm.view === "cluster") renderClusterMetrics();
    },
    onLogout() {
      farm.view = "devices";
      disconnectMaster();
      disconnectAllTileRfbs();
      stopSnapshotPolling();
      farm.renderedGridKey = "";
      farm.macrosLoaded = false;
      farm.tokensLoaded = false;
    },
    openWallWithSelection(ids) {
      farm.selectedIds = new Set(ids);
      farm.selectionInitialized = true;
      farm.syncEnabled = true;
      const firstRunning = ids.find((id) => getMachine(id)?.phase === "running");
      if (firstRunning) farm.masterId = firstRunning;
      navigate("wall");
    },
    openBatchCloneDialog,
    fitMasterFrame,
  };
}
