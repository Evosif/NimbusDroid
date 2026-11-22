import RFB from "/core/rfb.js";
import { createFarmController } from "/farm.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const WIDGET_TITLES = {
  info: "Device Info",
  identity: "Real Device Identity",
  text: "Text Input",
  gps: "GPS Location",
  battery: "Battery & Power",
  apps: "Apps & APK",
  shell: "ADB Shell",
  logs: "Device Logs",
  adb: "ADB Tunnel",
};

const VIEW_IDS = {
  devices: "#devices-view",
  wall: "#wall-view",
  templates: "#templates-view",
  macros: "#macros-view",
  cluster: "#cluster-view",
  detail: "#detail-view",
};

const state = {
  authenticated: false,
  currentView: "devices",
  machines: [],
  summary: null,
  options: null,
  selectedId: null,
  selectedBulk: new Set(),
  loading: false,
  refreshTimer: null,
  toastTimer: null,
  confirmAction: null,
  cloneSource: null,
  configureTarget: null,
  metaTarget: null,
  rfb: null,
  consoleToken: null,
  consoleConnected: false,
  consoleReconnectTimer: null,
  disconnectExpected: false,
  tableFilter: "all",
  groupFilter: "all",
  tableQuery: "",
  viewMode: localStorage.getItem("dc_view_mode") === "grid" ? "grid" : "table",
  sidebarQuery: "",
  sidebarPage: 1,
  sidebarPageSize: 10,
  activeWidget: null,
  packages: [],
  pkgQuery: "",
  logKind: "emulator",
  telemetry: null,
};

let farm = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name) {
  return `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

function getMachine(id) {
  return state.machines.find((m) => m.id === id) || null;
}

function statusLabel(m) {
  return { running: "Running", starting: "Booting", stopped: "Stopped", error: "Error" }[m.phase] || "Unknown";
}

function statusClass(m) {
  return ["running", "starting", "error"].includes(m.phase) ? m.phase : "stopped";
}

function isActive(m) {
  return m?.phase === "running" || m?.phase === "starting";
}

function formatGb(ramMb) {
  const gb = Number(ramMb || 0) / 1024;
  return Number.isInteger(gb) ? `${gb} GB` : `${gb.toFixed(1)} GB`;
}

/* =========================
   THEME (DARK / LIGHT)
   ========================= */
function applyTheme(theme) {
  const mode = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = mode;
  localStorage.setItem("dc_theme", mode);
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.content = mode === "light" ? "#f3f5f9" : "#090b10";
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  applyTheme(current === "light" ? "dark" : "light");
}

/* =========================
   HTTP & FEEDBACK
   ========================= */
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof Blob) && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_) {}
  if (res.status === 401 && path !== "/api/login") showLogin();
  if (!res.ok) throw new Error(data.message || `Request failed (${res.status}).`);
  return data;
}

function toast(message, error = false) {
  const el = $("#toast");
  clearTimeout(state.toastTimer);
  el.textContent = message;
  el.classList.toggle("error", error);
  el.hidden = false;
  state.toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

async function copyText(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {}
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.readOnly = true;
  ta.style.cssText = "position:fixed;left:-9999px;opacity:0";
  document.body.append(ta);
  ta.select();
  try {
    return document.execCommand("copy");
  } catch (_) {
    return false;
  } finally {
    ta.remove();
  }
}

/* =========================
   SIDEBAR COLLAPSE & DRAWER
   ========================= */
function applySidebarState() {
  const collapsed = localStorage.getItem("dc_sidebar_collapsed") === "1";
  $("#app").classList.toggle("sidebar-collapsed", collapsed);
}

function toggleSidebar() {
  if (window.innerWidth <= 960) {
    const opening = !$("#app").classList.contains("sidebar-open");
    $("#app").classList.toggle("sidebar-open", opening);
    $("#sidebar-backdrop").hidden = !opening;
  } else {
    const next = !$("#app").classList.contains("sidebar-collapsed");
    $("#app").classList.toggle("sidebar-collapsed", next);
    localStorage.setItem("dc_sidebar_collapsed", next ? "1" : "0");
    setTimeout(() => {
      fitDeviceFrame();
      farm?.fitMasterFrame();
    }, 200);
  }
}

function closeMobileSidebar() {
  $("#app").classList.remove("sidebar-open");
  $("#sidebar-backdrop").hidden = true;
}

/* =========================
   6-VIEW NAVIGATION ROUTER
   ========================= */
function navigate(view) {
  if (!VIEW_IDS[view]) view = "devices";
  if (view !== "detail") {
    state.selectedId = null;
    disconnectConsole();
    closeWidget();
  }
  state.currentView = view;
  closeMobileSidebar();
  closeMenu();

  for (const [key, selector] of Object.entries(VIEW_IDS)) {
    const el = $(selector);
    if (el) el.hidden = key !== view;
  }
  $$(".sb-nav [data-nav]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.nav === view);
  });

  renderSidebarList();
  farm?.onRouteChange(view);
}

/* =========================
   PHONE FRAME FIT (ALWAYS VISIBLE & RESPONSIVE)
   ========================= */
function fitDeviceFrame() {
  const stage = $("#console-stage");
  const frame = $("#device-frame");
  if (!stage || !frame || $("#detail-view").hidden) return;
  const sw = stage.clientWidth;
  const sh = stage.clientHeight;
  if (sw <= 0 || sh <= 0) return;

  const machine = getMachine(state.selectedId);
  const rawW = machine?.screen?.width || 1080;
  const rawH = machine?.screen?.height || 2400;
  const landscape = state.telemetry?.rotation === 1;
  const aspect = landscape ? rawH / rawW : rawW / rawH;

  const navH = ($("#nav-keys")?.offsetHeight || 38) + 12;
  const availW = Math.max(160, sw - 36);
  const availH = Math.max(200, sh - navH - 28);
  const isFs = Boolean(document.fullscreenElement);
  const maxW = isFs ? availW : landscape ? Math.min(820, availW) : Math.min(380, availW);
  const maxH = isFs ? availH : Math.min(760, availH);

  let w = Math.min(maxW, maxH * aspect);
  let h = w / aspect;
  if (h > maxH) {
    h = maxH;
    w = h * aspect;
  }
  frame.style.width = `${Math.round(w)}px`;
  frame.style.height = `${Math.round(h)}px`;

  if (state.rfb) state.rfb.scaleViewport = true;
}

/* =========================
   GENYMOTION WIDGET DRAWER
   ========================= */
function openWidget(name) {
  const panel = $("#widget-panel");
  if (state.activeWidget === name && !panel.hidden) {
    closeWidget();
    return;
  }
  state.activeWidget = name;
  panel.hidden = false;
  $("#widget-title").textContent = WIDGET_TITLES[name] || name;
  $$("[data-widget]").forEach((btn) => btn.classList.toggle("active", btn.dataset.widget === name));
  $$("[data-panel]").forEach((sec) => sec.classList.toggle("active", sec.dataset.panel === name));
  if (name === "info") loadTelemetry();
  if (name === "identity") loadIdentityAudit();
  if (name === "apps") loadPackages();
  if (name === "logs") loadLogs();
  requestAnimationFrame(fitDeviceFrame);
}

function closeWidget() {
  state.activeWidget = null;
  $("#widget-panel").hidden = true;
  $$("[data-widget]").forEach((btn) => btn.classList.remove("active"));
  requestAnimationFrame(fitDeviceFrame);
}

/* =========================
   CONTEXT MANAGE MENU
   ========================= */
function closeMenu() {
  $("#ctx-menu").hidden = true;
}

function openManageMenu(machineId, anchor) {
  const m = getMachine(machineId);
  if (!m) return;
  const active = isActive(m);
  const running = m.phase === "running";
  const menu = $("#ctx-menu");
  menu.innerHTML = `
    <button data-menu-op="power" data-mid="${escapeHtml(m.id)}" type="button">${icon(active ? "stop" : "play")}${active ? "Stop" : "Start"}</button>
    <button data-menu-op="restart" data-mid="${escapeHtml(m.id)}" type="button" ${running ? "" : "disabled"}>${icon("restart")}Reboot</button>
    <hr>
    <button data-menu-op="meta" data-mid="${escapeHtml(m.id)}" type="button">${icon("layers")}Edit details &amp; group</button>
    <button data-menu-op="configure" data-mid="${escapeHtml(m.id)}" type="button" ${active ? "disabled" : ""}>${icon("edit")}Edit hardware</button>
    <button data-menu-op="clone" data-mid="${escapeHtml(m.id)}" type="button" ${active ? "disabled" : ""}>${icon("clone")}Clone device</button>
    <button data-menu-op="batch-clone" data-mid="${escapeHtml(m.id)}" type="button" ${active ? "disabled" : ""}>${icon("wall")}Batch clone × N</button>
    <button data-menu-op="wipe" data-mid="${escapeHtml(m.id)}" type="button">${icon("eraser")}Reset Android data</button>
    <button data-menu-op="adb" data-mid="${escapeHtml(m.id)}" type="button">${icon("copy")}Copy adb connect</button>
    <hr>
    <button class="danger" data-menu-op="delete" data-mid="${escapeHtml(m.id)}" type="button">${icon("trash")}Delete device</button>
  `;
  menu.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth || 200;
  const mh = menu.offsetHeight || 310;
  const left = Math.max(8, Math.min(rect.right - mw, window.innerWidth - mw - 8));
  const top = rect.bottom + mh + 8 > window.innerHeight ? Math.max(8, rect.top - mh - 4) : rect.bottom + 4;
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

/* =========================
   AUTH & INIT
   ========================= */
function showLogin() {
  state.authenticated = false;
  clearInterval(state.refreshTimer);
  state.refreshTimer = null;
  disconnectConsole();
  farm?.onLogout();
  closeMobileSidebar();
  closeMenu();
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-password").focus();
}

function showApp() {
  state.authenticated = true;
  applySidebarState();
  applyViewMode(state.viewMode);
  $("#login").hidden = true;
  $("#app").hidden = false;
  navigate("devices");
  requestAnimationFrame(() => {
    recomputeSidebarPageSize();
    renderSidebarList();
  });
}

async function initialize() {
  applyTheme(localStorage.getItem("dc_theme") || "dark");
  try {
    const session = await api("/api/session");
    if (!session.authenticated) {
      showLogin();
      return;
    }
    showApp();
    await loadConfiguration();
    await loadMachines();
    startPolling();
  } catch (err) {
    showLogin();
    $("#login-message").textContent = err.message;
    $("#login-message").classList.add("error");
  }
}

async function login(e) {
  e.preventDefault();
  const btn = $("#login-form button[type=submit]");
  const msg = $("#login-message");
  btn.disabled = true;
  msg.classList.remove("error");
  msg.textContent = "Verifying credentials...";
  try {
    await api("/api/login", { method: "POST", body: { password: $("#login-password").value } });
    msg.textContent = "";
    $("#login-password").value = "";
    showApp();
    await loadConfiguration();
    await loadMachines();
    startPolling();
  } catch (err) {
    msg.textContent = err.message === "Kata sandi salah." ? "Invalid password. Please try again." : err.message;
    msg.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

async function loadConfiguration() {
  if (state.options) return;
  state.options = await api("/api/options");
  fillOptions();
  farm?.syncCatalogs(state.options);
}

function fillOptions() {
  const o = state.options;
  if (!o) return;
  const devHtml = o.devices
    .map((d) => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)} · ${escapeHtml(d.kind)}</option>`)
    .join("");
  $("#machine-device").innerHTML = devHtml;
  $("#configure-device").innerHTML = devHtml;
  $("#machine-storage").innerHTML = [8, 16, 32, 64, 128, 256, 512, 1024, 2048]
    .map((v) => `<option value="${v}">${v} GB</option>`)
    .join("");
  $("#machine-system").innerHTML = o.systems
    .map(
      (s) =>
        `<option value="${s.api}">${escapeHtml(s.label)} · API ${s.api}${
          s.installed ? "" : " (auto-download)"
        }</option>`,
    )
    .join("");
  const installed = [...o.systems].filter((s) => s.installed).sort((a, b) => b.api - a.api)[0];
  if (installed) $("#machine-system").value = String(installed.api);
  $("#machine-cpu").max = String(o.limits.cpu);
  $("#machine-ram").max = String(o.limits.ram);
  $("#machine-cpu").value = String(Math.min(4, o.limits.cpu));
  $("#machine-ram").value = String(Math.min(8, o.limits.ram));
  $("#machine-storage").value = "64";
  $("#configure-cpu").max = String(o.limits.cpu);
  $("#configure-ram").max = String(o.limits.ram);
  $("#create-limits-hint").textContent = `Per-VM limit: up to ${o.limits.cpu} vCPU · ${o.limits.ram} GB RAM · ${o.limits.machines} total slots`;
}

async function loadMachines(silent = false) {
  if (state.loading || !state.authenticated) return;
  state.loading = true;
  if (!silent) $("#refresh-button").classList.add("loading");
  try {
    const res = await api("/api/machines");
    if (!res.machines) return;
    state.machines = res.machines;
    state.summary = res.summary || null;
    const ids = new Set(state.machines.map((m) => m.id));
    for (const id of [...state.selectedBulk]) if (!ids.has(id)) state.selectedBulk.delete(id);
    renderAll();
    if (state.selectedId) {
      const sel = getMachine(state.selectedId);
      if (sel) updateDetail(sel);
      else navigate("devices");
    }
  } catch (err) {
    if (!silent) toast(err.message, true);
  } finally {
    state.loading = false;
    $("#refresh-button").classList.remove("loading");
  }
}

function startPolling() {
  clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(() => loadMachines(true), 3000);
}

/* =========================
   SIDEBAR PAGINATION (ZERO SCROLL)
   ========================= */
function recomputeSidebarPageSize() {
  const list = $("#quick-list");
  if (!list || list.clientHeight <= 0) return;
  state.sidebarPageSize = Math.max(4, Math.floor((list.clientHeight + 2) / 34));
}

function getFilteredSidebar() {
  const q = state.sidebarQuery.trim().toLowerCase();
  if (!q) return state.machines;
  return state.machines.filter(
    (m) =>
      m.name.toLowerCase().includes(q) ||
      String(m.consolePort).includes(q) ||
      (m.group || "").toLowerCase().includes(q) ||
      m.deviceLabel.toLowerCase().includes(q),
  );
}

function renderSidebarList() {
  const list = $("#quick-list");
  const pager = $("#quick-pager");
  const running = state.machines.filter((m) => m.phase === "running").length;
  $("#nav-count").textContent = String(state.machines.length);
  $("#nav-wall-count").textContent = String(running);

  const filtered = getFilteredSidebar();
  const pageSize = Math.max(4, state.sidebarPageSize || 8);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  if (state.sidebarPage > pages) state.sidebarPage = pages;
  if (state.sidebarPage < 1) state.sidebarPage = 1;

  pager.hidden = pages <= 1;
  $("#quick-page-label").textContent = `${state.sidebarPage}/${pages}`;
  $("#quick-prev").disabled = state.sidebarPage <= 1;
  $("#quick-next").disabled = state.sidebarPage >= pages;

  if (filtered.length === 0) {
    list.innerHTML = `<div class="sb-empty">${state.machines.length ? "No matches" : "No devices"}</div>`;
    return;
  }
  const start = (state.sidebarPage - 1) * pageSize;
  list.innerHTML = filtered
    .slice(start, start + pageSize)
    .map(
      (m) => `
        <button class="sb-vm ${state.selectedId === m.id ? "active" : ""}" data-open="${escapeHtml(m.id)}" type="button" title="${escapeHtml(m.name)} (${m.serial})">
          <span class="dot ${statusClass(m)}"></span>
          <span class="sb-vm-name">${escapeHtml(m.name)}</span>
          <span class="sb-vm-port">${m.consolePort}</span>
        </button>`,
    )
    .join("");
}

function ensureVisibleInSidebar(id) {
  const idx = getFilteredSidebar().findIndex((m) => m.id === id);
  if (idx >= 0) state.sidebarPage = Math.floor(idx / Math.max(4, state.sidebarPageSize || 8)) + 1;
}

/* =========================
   DEVICES FLEET VIEW (METRICS, TABLE & CARDS)
   ========================= */
function setMeter(el, pct) {
  el.style.width = `${pct}%`;
  el.classList.toggle("warn", pct >= 75 && pct < 90);
  el.classList.toggle("critical", pct >= 90);
}

function renderMetrics() {
  const s = state.summary;
  const running = state.machines.filter((m) => m.phase === "running").length;
  const starting = state.machines.filter((m) => m.phase === "starting").length;
  const stopped = state.machines.length - running - starting;
  const rooted = state.machines.filter((m) => m.root).length;
  const templates = state.machines.filter((m) => m.isTemplate).length;

  $("#stat-running").textContent = String(running);
  $("#stat-total").textContent = `/ ${state.machines.length}`;
  $("#stat-sub-vms").textContent = `${starting} booting · ${stopped} stopped`;

  const cpuPct = Math.max(0, Math.min(100, s?.cpuPercent || 0));
  const ramPct = Math.max(0, Math.min(100, s?.ramPercent || 0));
  const diskPct = Math.max(0, Math.min(100, s?.diskPercent || 0));
  const hostRam = s?.hostRam || 0;
  const availRam = s?.hostRamAvailable || 0;

  $("#stat-host-cpu-percent").textContent = `${cpuPct}%`;
  $("#stat-host-ram-percent").textContent = `${ramPct}%`;
  setMeter($("#cpu-meter"), cpuPct);
  setMeter($("#ram-meter"), ramPct);
  setMeter($("#disk-meter"), diskPct);
  $("#stat-host-cpu").textContent = `${s?.cpuAllocated || 0} vCPU allocated · ${s?.hostCpu || "—"} host`;
  $("#stat-host-ram").textContent = `${Math.max(0, hostRam - availRam)} / ${hostRam || "—"} GB used`;
  $("#stat-disk-free").textContent = s?.diskFreeGb !== undefined ? `${s.diskFreeGb} GB` : "—";
  $("#stat-root-summary").textContent = `${templates} templates · ${rooted} Magisk root`;
}

function syncGroupFilterOptions() {
  const sel = $("#group-filter");
  if (!sel) return;
  const groups = state.summary?.groups || [];
  const opts = [`<option value="all">All groups</option>`].concat(
    groups.map((g) => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`),
  );
  const html = opts.join("");
  if (sel.dataset.opts !== html) {
    sel.dataset.opts = html;
    sel.innerHTML = html;
  }
  if (state.groupFilter !== "all" && !groups.includes(state.groupFilter)) {
    state.groupFilter = "all";
  }
  sel.value = state.groupFilter;
}

function getFilteredDevices() {
  const q = state.tableQuery.trim().toLowerCase();
  return state.machines.filter((m) => {
    if (state.tableFilter === "running" && !isActive(m)) return false;
    if (state.tableFilter === "stopped" && isActive(m)) return false;
    if (state.tableFilter === "root" && !m.root) return false;
    if (state.groupFilter !== "all" && (m.group || "default") !== state.groupFilter) return false;
    if (!q) return true;
    return (
      m.name.toLowerCase().includes(q) ||
      String(m.consolePort).includes(q) ||
      (m.group || "").toLowerCase().includes(q) ||
      (m.tags || []).some((t) => t.toLowerCase().includes(q)) ||
      m.deviceLabel.toLowerCase().includes(q) ||
      m.androidVersion.toLowerCase().includes(q) ||
      m.serial.toLowerCase().includes(q)
    );
  });
}

function applyViewMode(mode) {
  state.viewMode = mode === "grid" ? "grid" : "table";
  localStorage.setItem("dc_view_mode", state.viewMode);
  $("#view-list-btn").classList.toggle("active", state.viewMode === "table");
  $("#view-grid-btn").classList.toggle("active", state.viewMode === "grid");
  renderDevicesList();
}

function updateBulkBar() {
  const n = state.selectedBulk.size;
  $("#bulk-bar").hidden = n === 0;
  $("#bulk-count").textContent = `${n} selected`;
}

function renderDevicesList() {
  syncGroupFilterOptions();
  const tbody = $("#machine-table");
  const tableBox = $("#table-container");
  const grid = $("#card-grid");
  const runningCount = state.machines.filter((m) => isActive(m)).length;

  $("#filter-count-all").textContent = String(state.machines.length);
  $("#filter-count-running").textContent = String(runningCount);
  $("#filter-count-stopped").textContent = String(state.machines.length - runningCount);
  $("#filter-count-root").textContent = String(state.machines.filter((m) => m.root).length);

  if (state.machines.length === 0) {
    $("#empty-state").hidden = false;
    $("#filter-empty-state").hidden = true;
    tableBox.hidden = true;
    grid.hidden = true;
    tbody.innerHTML = "";
    grid.innerHTML = "";
    updateBulkBar();
    return;
  }

  $("#empty-state").hidden = true;
  const filtered = getFilteredDevices();
  if (filtered.length === 0) {
    $("#filter-empty-state").hidden = false;
    tableBox.hidden = true;
    grid.hidden = true;
    tbody.innerHTML = "";
    grid.innerHTML = "";
    updateBulkBar();
    return;
  }

  $("#filter-empty-state").hidden = true;
  tableBox.hidden = state.viewMode !== "table";
  grid.hidden = state.viewMode !== "grid";

  tbody.innerHTML = filtered
    .map((m) => {
      const active = isActive(m);
      const cls = statusClass(m);
      return `
        <tr>
          <td class="col-check"><input type="checkbox" data-select-vm="${escapeHtml(m.id)}" ${state.selectedBulk.has(m.id) ? "checked" : ""} aria-label="Select ${escapeHtml(m.name)}"></td>
          <td>
            <button class="vm-link" data-open="${escapeHtml(m.id)}" type="button">
              <span class="vm-avatar ${cls}">${icon("phone")}</span>
              <span>
                <span class="vm-title-row">
                  <strong>${escapeHtml(m.name)}</strong>
                  ${m.backend === "iso" ? `<span class="tag green">ISO</span>` : ""}
                  ${m.isTemplate ? `<span class="tag green">TEMPLATE</span>` : ""}
                  ${m.root && m.backend !== "iso" ? `<span class="tag accent">ROOT</span>` : ""}
                  ${m.autostart ? `<span class="tag">AUTO</span>` : ""}
                </span>
                <span class="vm-sub">${escapeHtml(m.identity?.marketname || m.deviceLabel)}${m.identity ? ` · ${escapeHtml(m.identity.model)}` : ""}${m.screen ? ` · ${m.screen.width}×${m.screen.height}` : ""}</span>
              </span>
            </button>
          </td>
          <td><span class="tag">${escapeHtml(m.group || "default")}</span></td>
          <td><span class="status-badge ${cls}">${statusLabel(m)}</span></td>
          <td>
            <span class="os-primary">${escapeHtml(m.androidVersion)}</span>
            <span class="os-sub">API ${m.apiLevel}</span>
          </td>
          <td>
            <span class="spec-primary">${m.cpu} vCPU · ${formatGb(m.ram)}</span>
            <span class="spec-sub">${m.storage} GB disk</span>
          </td>
          <td>
            <button class="adb-btn" data-copy-serial="127.0.0.1:${m.adbPort}" type="button" title="Copy adb connect command">
              <span>${escapeHtml(m.serial)}</span>${icon("copy")}
            </button>
          </td>
          <td class="col-actions">
            <div class="row-actions">
              <button class="icon-btn ghost" data-power="${escapeHtml(m.id)}" type="button" title="${active ? "Stop" : "Start"}">${icon(active ? "stop" : "play")}</button>
              <button class="btn" data-open="${escapeHtml(m.id)}" type="button">Open</button>
              <button class="icon-btn ghost" data-manage="${escapeHtml(m.id)}" type="button" title="Manage">${icon("more")}</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");

  grid.innerHTML = filtered
    .map((m) => {
      const active = isActive(m);
      const cls = statusClass(m);
      return `
        <article class="device-card">
          <div class="dc-top">
            <button class="dc-head" data-open="${escapeHtml(m.id)}" type="button">
              <span class="vm-avatar ${cls}">${icon("phone")}</span>
              <span>
                <strong>${escapeHtml(m.name)}</strong>
                <small>${escapeHtml(m.deviceLabel)} · ${escapeHtml(m.androidVersion)}</small>
              </span>
            </button>
            <span class="status-badge ${cls}">${statusLabel(m)}</span>
          </div>
          <div class="dc-specs">
            <div class="dc-spec"><span>CPU</span><strong>${m.cpu} vCPU</strong></div>
            <div class="dc-spec"><span>Memory</span><strong>${formatGb(m.ram)}</strong></div>
            <div class="dc-spec"><span>Disk</span><strong>${m.storage} GB</strong></div>
          </div>
          <div class="dc-meta">
            <button class="adb-btn" data-copy-serial="127.0.0.1:${m.adbPort}" type="button" title="Copy adb connect">
              <span>${escapeHtml(m.serial)}</span>${icon("copy")}
            </button>
            <div class="dc-tags">
              <span class="tag">${escapeHtml(m.group || "default")}</span>
              ${m.backend === "iso" ? `<span class="tag green">ISO</span>` : ""}
              ${m.isTemplate ? `<span class="tag green">TPL</span>` : ""}
              ${m.root && m.backend !== "iso" ? `<span class="tag accent">ROOT</span>` : ""}
            </div>
          </div>
          <div class="dc-foot">
            <button class="icon-btn" data-power="${escapeHtml(m.id)}" type="button" title="${active ? "Stop" : "Start"}">${icon(active ? "stop" : "play")}</button>
            <button class="btn primary open-btn" data-open="${escapeHtml(m.id)}" type="button">Open Console</button>
            <button class="icon-btn" data-manage="${escapeHtml(m.id)}" type="button" title="Manage">${icon("more")}</button>
          </div>
        </article>`;
    })
    .join("");

  $("#select-all-vms").checked = filtered.length > 0 && filtered.every((m) => state.selectedBulk.has(m.id));
  updateBulkBar();
}

function renderAll() {
  renderMetrics();
  renderDevicesList();
  recomputeSidebarPageSize();
  renderSidebarList();
  farm?.onDataUpdated();
}

/* =========================
   SINGLE DEVICE PLAYER DETAIL
   ========================= */
function openMachine(id) {
  const m = getMachine(id);
  if (!m) return;
  const changed = state.selectedId !== id;
  state.selectedId = id;
  navigate("detail");
  ensureVisibleInSidebar(id);
  renderSidebarList();
  if (changed) {
    state.telemetry = null;
    state.packages = [];
    renderTelemetry();
    $("#pkg-list").innerHTML = "";
  }
  updateDetail(m);
  requestAnimationFrame(fitDeviceFrame);
  if (changed) {
    loadTelemetry();
    if (state.activeWidget === "identity") loadIdentityAudit();
    if (state.activeWidget === "apps") loadPackages();
    if (state.activeWidget === "logs") loadLogs();
  }
}

function updateDetail(m) {
  const active = isActive(m);
  const running = m.phase === "running";
  const res = m.screen ? `${m.screen.width}×${m.screen.height}` : "—";

  $("#detail-dot").className = `dot ${statusClass(m)}`;
  $("#detail-name").textContent = m.name;
  $("#detail-subtitle").textContent = `${m.identity?.marketname || m.deviceLabel} · ${m.androidVersion} · ${m.serial}`;
  $("#detail-group-badge").textContent = m.group || "default";
  $("#detail-backend-badge").hidden = m.backend !== "iso";
  $("#detail-root-badge").hidden = !m.root || m.backend === "iso";

  $("#dock-status").textContent = statusLabel(m);
  $("#prop-group").textContent = m.group || "default";
  $("#prop-system").textContent = `${m.androidVersion} (API ${m.apiLevel})`;
  $("#prop-device").textContent = m.deviceLabel;
  $("#prop-screen").textContent = m.screen ? `${res} @ ${m.screen.density}dpi` : "—";
  $("#dock-specs").textContent = `${m.cpu} vCPU · ${formatGb(m.ram)}`;
  $("#prop-storage").textContent = `${m.storage} GB`;
  $("#prop-root").textContent = m.root ? "Magisk" : "Disabled";
  $("#dock-adb").textContent = m.adbState === "device" ? "Online" : m.adbState;
  $("#sum-serial").textContent = m.serial;
  $("#sum-vnc-port").textContent = `127.0.0.1:${m.vncPort}`;
  $("#autostart-toggle").checked = Boolean(m.autostart);

  const errEl = $("#detail-error");
  errEl.hidden = !m.error;
  errEl.textContent = m.error || "";

  const power = $("#power-button");
  power.className = active ? "btn danger-outline" : "btn primary";
  power.innerHTML = `${icon(active ? "stop" : "play")}${active ? "Stop" : "Start"}`;
  $("#header-restart-button").disabled = !running;

  $$("[data-key]").forEach((b) => {
    b.disabled = !running;
  });
  $("#screenshot-button").disabled = !running;
  $("#device-text-btn").disabled = !running;
  $("#battery-apply").disabled = !running;
  $("#shell-run").disabled = !running;
  $("#install-button").disabled = !running || !$("#apk-file").files.length;

  $("#adb-state").textContent = m.adbState === "device" ? "Online" : m.adbState;
  const host = location.hostname || "localhost";
  const sshUser = m.sshUser || "ubuntu";
  $("#adb-command").textContent = [
    `ssh -i YOUR_KEY.pem -N -L 127.0.0.1:${m.adbPort}:127.0.0.1:${m.adbPort} ${sshUser}@${host}`,
    `adb connect 127.0.0.1:${m.adbPort}`,
  ].join("\n");

  const loc = m.lastLocation;
  $("#location-state").textContent = loc ? `${loc.label || "Custom"} (${loc.latitude}, ${loc.longitude})` : "not set";
  if (loc && !$("#location-latitude").matches(":focus")) {
    $("#location-latitude").value = loc.latitude;
    $("#location-longitude").value = loc.longitude;
  }
  $("#location-form button[type=submit]").disabled = !running;
  $$("[data-location]").forEach((b) => {
    b.disabled = !running;
  });

  $("#overlay-start-btn").hidden = active;
  if (running) {
    if (state.consoleToken !== m.consoleToken || (!state.consoleConnected && !state.rfb)) {
      connectConsole(m);
    }
  } else {
    disconnectConsole();
    if (m.phase === "starting") setConsoleOverlay("starting", "Booting Android...", "The stream will connect automatically once ready.");
    else if (m.phase === "error") setConsoleOverlay("error", "Device error", m.error || "Open Device Logs for details.");
    else setConsoleOverlay("stopped", "Device stopped", "Click Start to power on this virtual device.");
  }
}

function setConsoleOverlay(kind, title, message) {
  const ov = $("#console-overlay");
  ov.hidden = false;
  $(".loader", ov).hidden = kind !== "connecting" && kind !== "starting";
  $("#console-overlay-title").textContent = title;
  $("#console-overlay-message").textContent = message;
  const dot = $("#console-dot");
  dot.className = "dot";
  if (kind === "starting" || kind === "connecting") dot.classList.add("starting");
  if (kind === "connected") dot.classList.add("online");
  if (kind === "error") dot.classList.add("error");
  $("#console-label").textContent =
    { connected: "Live", starting: "Booting", connecting: "Connecting", error: "Error" }[kind] || "Offline";
}

function disconnectConsole() {
  clearTimeout(state.consoleReconnectTimer);
  state.consoleReconnectTimer = null;
  state.consoleConnected = false;
  state.consoleToken = null;
  if (state.rfb) {
    state.disconnectExpected = true;
    try {
      state.rfb.disconnect();
    } catch (_) {}
    state.rfb = null;
  }
}

function connectConsole(machine) {
  clearTimeout(state.consoleReconnectTimer);
  state.consoleReconnectTimer = null;
  if (state.rfb) {
    state.disconnectExpected = true;
    try {
      state.rfb.disconnect();
    } catch (_) {}
  }
  state.consoleConnected = false;
  state.consoleToken = machine.consoleToken;
  setConsoleOverlay("connecting", "Connecting...", "Establishing display stream.");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/websockify?token=${encodeURIComponent(machine.consoleToken)}`;
  try {
    const rfb = new RFB($("#screen"), url, { shared: true, credentials: {}, wsProtocols: ["binary"] });
    state.rfb = rfb;
    state.disconnectExpected = false;
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.background = "#030406";
    rfb.addEventListener("connect", () => {
      if (state.rfb !== rfb) return;
      state.consoleConnected = true;
      setConsoleOverlay("connected", "", "");
      $("#console-overlay").hidden = true;
      requestAnimationFrame(fitDeviceFrame);
    });
    rfb.addEventListener("disconnect", () => {
      if (state.rfb !== rfb) return;
      state.consoleConnected = false;
      state.rfb = null;
      if (state.disconnectExpected) return;
      const sel = getMachine(state.selectedId);
      if (sel?.phase === "running") {
        setConsoleOverlay("connecting", "Reconnecting...", "Stream disconnected. Retrying...");
        state.consoleReconnectTimer = setTimeout(() => {
          const latest = getMachine(state.selectedId);
          if (latest?.phase === "running") connectConsole(latest);
        }, 2500);
      }
    });
    rfb.addEventListener("securityfailure", () => {
      if (state.rfb !== rfb) return;
      setConsoleOverlay("error", "Stream rejected", "Reload the page and try again.");
    });
  } catch (err) {
    setConsoleOverlay("error", "Stream failed", err.message || "Unable to open stream.");
  }
}

/* =========================
   SINGLE DEVICE WIDGETS
   ========================= */
function renderTelemetry() {
  const t = state.telemetry;
  const bat =
    t && t.batteryLevel !== null && t.batteryLevel !== undefined
      ? `${t.batteryLevel}% · ${t.acPowered ? "AC" : "Battery"}`
      : "—";
  $("#dock-ip").textContent = t?.ipAddress || "—";
  $("#dock-battery").textContent = bat;
  $("#tel-uptime").textContent = t?.uptime || "—";
  if (t && t.batteryLevel !== null && t.batteryLevel !== undefined) {
    $("#battery-level").value = String(t.batteryLevel);
    $("#battery-slider-val").textContent = String(t.batteryLevel);
    $("#battery-ac").checked = Boolean(t.acPowered);
  }
}

async function loadTelemetry() {
  const m = getMachine(state.selectedId);
  if (!m || m.phase !== "running") {
    state.telemetry = null;
    renderTelemetry();
    return;
  }
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/telemetry`);
    if (state.selectedId !== m.id) return;
    state.telemetry = res.telemetry || null;
    renderTelemetry();
    fitDeviceFrame();
  } catch (_) {}
}

function renderIdentityAudit(data) {
  const badge = $("#id-audit-badge");
  const scoreEl = $("#id-audit-score");
  const warnsEl = $("#id-audit-warnings");
  const cfg = data?.configured;
  if (cfg?.profileId && [...$("#id-profile-select").options].some((o) => o.value === cfg.profileId)) {
    $("#id-profile-select").value = cfg.profileId;
  }
  if (cfg?.carrierId && [...$("#id-carrier-select").options].some((o) => o.value === cfg.carrierId)) {
    $("#id-carrier-select").value = cfg.carrierId;
  }

  const audit = data?.audit;
  if (!audit) {
    badge.className = "status-badge stopped";
    badge.textContent = data?.ready === false ? "Device Offline" : "Unchecked";
    scoreEl.textContent = cfg ? `Configured: ${cfg.model}` : "—";
    warnsEl.innerHTML = "";
    for (const id of [
      "#id-live-model", "#id-live-brand", "#id-live-device", "#id-live-devname",
      "#id-live-hw", "#id-live-soc", "#id-live-serial", "#id-live-carrier", "#id-live-qemu", "#id-live-fp",
    ]) {
      $(id).textContent = "—";
    }
    return;
  }

  const cls = audit.status === "real" ? "running" : audit.status === "partial" ? "starting" : "error";
  badge.className = `status-badge ${cls}`;
  badge.textContent = audit.status === "real" ? "Real Device" : audit.status === "partial" ? "Partial Spoof" : "Emulator Detected";
  scoreEl.textContent = `${audit.score}/100`;
  warnsEl.innerHTML = (audit.warnings || [])
    .map((w) => `<div class="id-warn-item">• ${escapeHtml(w)}</div>`)
    .join("");

  const p = audit.props || {};
  $("#id-live-model").textContent = p.model || "—";
  $("#id-live-brand").textContent = [p.brand, p.manufacturer].filter(Boolean).join(" / ") || "—";
  $("#id-live-device").textContent = p.device || "—";
  $("#id-live-devname").textContent = p.deviceName || "—";
  $("#id-live-hw").textContent = p.hardware || "—";
  $("#id-live-soc").textContent = [p.board, p.socModel].filter(Boolean).join(" · ") || "—";
  $("#id-live-serial").textContent = p.serial || "—";
  $("#id-live-carrier").textContent = p.carrier || "—";
  $("#id-live-qemu").textContent = p.qemu === "1" || p.bootQemu === "1" ? `qemu=${p.qemu || "0"} boot=${p.bootQemu || "0"}` : "Clean (0)";
  $("#id-live-fp").textContent = p.fingerprint || "—";
}

async function loadIdentityAudit() {
  const m = getMachine(state.selectedId);
  if (!m) return;
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/identity`);
    if (state.selectedId !== m.id) return;
    renderIdentityAudit(res);
  } catch (err) {
    toast(err.message, true);
  }
}

async function applySingleIdentity(reboot = false) {
  const m = getMachine(state.selectedId);
  if (!m) return;
  const msg = $("#id-form-message");
  const btn1 = $("#id-apply-btn");
  const btn2 = $("#id-apply-reboot-btn");
  btn1.disabled = true;
  btn2.disabled = true;
  msg.classList.remove("error");
  msg.textContent = reboot ? "Applying identity & rebooting..." : "Applying real device identity...";
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/identity`, {
      method: "POST",
      body: {
        profileId: $("#id-profile-select").value || "random",
        carrierId: $("#id-carrier-select").value || "telkomsel",
        randomize: $("#id-randomize").checked,
        reboot,
      },
    });
    replaceMachine(res.machine);
    updateDetail(res.machine);
    msg.textContent = res.rebooting
      ? `Applied ${res.identity.label}. Rebooting VM for full Build.* reload...`
      : `Applied ${res.identity.label} (${res.identity.serial}).`;
    toast(`Identity updated: ${res.identity.model}`);
    if (!res.rebooting) await loadIdentityAudit();
    await loadMachines(true);
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    btn1.disabled = false;
    btn2.disabled = false;
  }
}

async function sendText(e) {
  e.preventDefault();
  const m = getMachine(state.selectedId);
  const input = $("#device-text-input");
  const text = input.value;
  if (!m || m.phase !== "running" || !text) return;
  const btn = $("#device-text-btn");
  btn.disabled = true;
  try {
    await api(`/api/machines/${encodeURIComponent(m.id)}/text`, {
      method: "POST",
      body: { text, submit: $("#device-text-submit").checked },
    });
    input.value = "";
    toast("Text sent to device.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function renderPackages() {
  const list = $("#pkg-list");
  const q = state.pkgQuery.trim().toLowerCase();
  const items = state.packages.filter((p) => !q || p.toLowerCase().includes(q));
  $("#pkg-count").textContent = String(items.length);
  if (!items.length) {
    list.innerHTML = `<p class="hint">${state.packages.length ? "No matching packages." : "No third-party apps installed."}</p>`;
    return;
  }
  list.innerHTML = items
    .map(
      (pkg) => `
        <div class="pkg-row">
          <span class="pkg-name" title="${escapeHtml(pkg)}">${escapeHtml(pkg)}</span>
          <div class="pkg-actions">
            <button data-pkg="${escapeHtml(pkg)}" data-pkg-action="launch" type="button">Launch</button>
            <button data-pkg="${escapeHtml(pkg)}" data-pkg-action="stop" type="button">Force Stop</button>
            <button data-pkg="${escapeHtml(pkg)}" data-pkg-action="clear" type="button">Clear</button>
            <button class="danger" data-pkg="${escapeHtml(pkg)}" data-pkg-action="uninstall" type="button">Uninstall</button>
          </div>
        </div>`,
    )
    .join("");
}

async function loadPackages() {
  const m = getMachine(state.selectedId);
  const list = $("#pkg-list");
  if (!m || m.phase !== "running") {
    state.packages = [];
    $("#pkg-count").textContent = "0";
    list.innerHTML = `<p class="hint">Start the device to inspect installed apps.</p>`;
    return;
  }
  list.innerHTML = `<p class="hint">Loading packages...</p>`;
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/packages`);
    if (state.selectedId !== m.id) return;
    state.packages = res.packages || [];
    renderPackages();
  } catch (err) {
    list.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
  }
}

async function runPackageAction(pkg, action) {
  const m = getMachine(state.selectedId);
  if (!m) return;
  try {
    await api(`/api/machines/${encodeURIComponent(m.id)}/package`, {
      method: "POST",
      body: { package: pkg, action },
    });
    toast(`${action} succeeded: ${pkg}`);
    if (action === "uninstall") await loadPackages();
  } catch (err) {
    toast(err.message, true);
  }
}

async function applyBattery(e) {
  e.preventDefault();
  const m = getMachine(state.selectedId);
  if (!m || m.phase !== "running") return;
  const level = Number($("#battery-level").value);
  const ac = $("#battery-ac").checked;
  const msg = $("#battery-message");
  msg.classList.remove("error");
  msg.textContent = "Applying...";
  try {
    await api(`/api/machines/${encodeURIComponent(m.id)}/battery`, {
      method: "POST",
      body: { level, ac },
    });
    const label = `${level}% · ${ac ? "AC" : "Battery"}`;
    $("#dock-battery").textContent = label;
    msg.textContent = `Battery set to ${label}.`;
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
}

async function runShell(cmdText) {
  const m = getMachine(state.selectedId);
  const out = $("#shell-output");
  if (!m || m.phase !== "running") {
    toast("Device must be running.", true);
    return;
  }
  const cmd = String(cmdText || "").trim();
  if (!cmd) return;
  $("#shell-run").disabled = true;
  out.textContent = `$ ${cmd}\nRunning...`;
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/shell`, {
      method: "POST",
      body: { command: cmd },
    });
    const text = [res.stdout, res.stderr].filter(Boolean).join("\n").trim();
    out.textContent = `$ ${cmd}\n${text || "(no output)"}\n\n[exit ${res.exitCode}]`;
  } catch (err) {
    out.textContent = `$ ${cmd}\nError: ${err.message}`;
  } finally {
    $("#shell-run").disabled = false;
  }
}

async function loadLogs() {
  const m = getMachine(state.selectedId);
  const viewer = $("#log-viewer");
  if (!m) return;
  viewer.textContent = "Loading logs...";
  try {
    const res = await api(
      `/api/machines/${encodeURIComponent(m.id)}/logs?kind=${encodeURIComponent(state.logKind)}&lines=200`,
    );
    if (state.selectedId !== m.id) return;
    viewer.textContent = res.exists ? res.content || "(Empty log)" : `No ${state.logKind} log yet.`;
    viewer.scrollTop = viewer.scrollHeight;
  } catch (err) {
    viewer.textContent = err.message;
  }
}

/* =========================
   LIFECYCLE ACTIONS & MODALS
   ========================= */
function replaceMachine(machine) {
  const idx = state.machines.findIndex((x) => x.id === machine.id);
  if (idx >= 0) state.machines[idx] = machine;
  else state.machines.push(machine);
}

async function toggleMachine(id) {
  const m = getMachine(id);
  if (!m) return;
  const action = isActive(m) ? "stop" : "start";
  try {
    toast(action === "stop" ? `Stopping ${m.name}...` : `Starting ${m.name}...`);
    const res = await api(`/api/machines/${encodeURIComponent(id)}/${action}`, { method: "POST", body: {} });
    replaceMachine(res.machine);
    await loadMachines(true);
  } catch (err) {
    toast(err.message, true);
  }
}

async function restartMachine(id = state.selectedId) {
  const m = getMachine(id);
  if (!m || m.phase !== "running") return;
  try {
    await api(`/api/machines/${encodeURIComponent(m.id)}/restart`, { method: "POST", body: {} });
    if (state.selectedId === m.id) disconnectConsole();
    toast(`Rebooting ${m.name}...`);
    await loadMachines(true);
  } catch (err) {
    toast(err.message, true);
  }
}

async function runBulk(action) {
  const machineIds = [...state.selectedBulk];
  if (!machineIds.length) return;
  try {
    const res = await api("/api/farm/power", {
      method: "POST",
      body: { machineIds, action, staggerSeconds: 2.5 },
    });
    state.selectedBulk.clear();
    await loadMachines(true);
    toast(
      res.scheduled
        ? `Staggered ${action} scheduled for ${res.scheduled} devices.`
        : `Bulk ${action} completed (${res.succeeded}/${res.total}).`,
    );
  } catch (err) {
    toast(err.message, true);
  }
}

async function setAutostart(enabled) {
  const m = getMachine(state.selectedId);
  if (!m) return;
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/autostart`, {
      method: "POST",
      body: { enabled },
    });
    replaceMachine(res.machine);
    toast(enabled ? "Autostart enabled." : "Autostart disabled.");
    await loadMachines(true);
  } catch (err) {
    $("#autostart-toggle").checked = !enabled;
    toast(err.message, true);
  }
}

function updateCreateSubmitLabel() {
  const count = Math.max(1, Math.min(20, Number($("#machine-count").value) || 1));
  $("#create-submit").textContent = count > 1 ? `Deploy ${count} Devices` : "Create Device";
}

function syncCreateBackendUI() {
  const isIso = $("#machine-backend").value === "iso";
  $("#machine-system-wrap").hidden = isIso;
  $("#machine-iso-wrap").hidden = !isIso;
  if (isIso) {
    $("#machine-root").checked = true;
    $("#machine-root").disabled = true;
    if (Number($("#machine-ram").value) < 4) $("#machine-ram").value = "4";
    if ($("#machine-identity").value === "none") {
      $("#machine-identity").value = "pixel-7-pro-a13";
    }
  } else {
    $("#machine-root").disabled = false;
  }
}

function openCreateDialog(preset = {}) {
  if (!state.options) return;
  closeMobileSidebar();
  $("#machine-form").reset();
  fillOptions();
  farm?.syncCatalogs(state.options);
  $("#machine-group").value = state.groupFilter !== "all" ? state.groupFilter : "default";
  $("#machine-count").value = "1";
  $("#machine-backend").value = preset.backend === "iso" ? "iso" : "avd";
  if (preset.isoId && [...$("#machine-iso").options].some((o) => o.value === preset.isoId)) {
    $("#machine-iso").value = preset.isoId;
  }
  if (preset.backend === "iso") {
    $("#machine-name").value = "BlissOS Phone";
    $("#machine-cpu").value = "4";
    $("#machine-ram").value = "4";
    $("#machine-storage").value = "32";
  }
  syncCreateBackendUI();
  updateCreateSubmitLabel();
  $("#machine-form-message").textContent = "";
  $("#machine-form-message").classList.remove("error");
  $("#machine-dialog").showModal();
  setTimeout(() => $("#machine-name").focus(), 40);
}

async function createMachine(e) {
  e.preventDefault();
  const btn = $("#create-submit");
  const msg = $("#machine-form-message");
  const count = Math.max(1, Math.min(20, Number($("#machine-count").value) || 1));
  const backend = $("#machine-backend").value === "iso" ? "iso" : "avd";
  const identityProfile = $("#machine-identity").value || "none";
  const payload = {
    name: $("#machine-name").value.trim(),
    backend,
    isoId: backend === "iso" ? $("#machine-iso").value : undefined,
    identityProfile: identityProfile !== "none" ? identityProfile : undefined,
    group: $("#machine-group").value.trim() || "default",
    device: $("#machine-device").value,
    api: Number($("#machine-system").value),
    cpu: Number($("#machine-cpu").value),
    ram: Math.round(Number($("#machine-ram").value) * 1024),
    storage: Number($("#machine-storage").value),
    root: backend === "iso" ? true : $("#machine-root").checked,
    start: $("#machine-start").checked,
    autostart: $("#machine-autostart").checked,
  };
  btn.disabled = true;
  msg.classList.remove("error");
  msg.textContent = count > 1 ? `Deploying ${count} virtual devices...` : "Creating virtual device...";
  try {
    if (count > 1) {
      const res = await api("/api/farm/batch-create", {
        method: "POST",
        body: { ...payload, count },
      });
      $("#machine-dialog").close();
      await loadMachines(true);
      navigate("devices");
      toast(`Created ${res.count} devices in group '${payload.group}'.`);
    } else {
      const res = await api("/api/machines", { method: "POST", body: payload });
      replaceMachine(res.machine);
      $("#machine-dialog").close();
      await loadMachines(true);
      openMachine(res.machine.id);
      toast("Device created.");
    }
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function openMetaDialog(id = state.selectedId) {
  const m = getMachine(id);
  if (!m) return;
  state.metaTarget = m.id;
  $("#meta-name").value = m.name;
  $("#meta-group").value = m.group || "default";
  $("#meta-tags").value = (m.tags || []).join(", ");
  const tplBox = $("#meta-template");
  tplBox.checked = Boolean(m.isTemplate);
  tplBox.disabled = isActive(m) && !m.isTemplate;
  $("#meta-message").textContent = "";
  $("#meta-message").classList.remove("error");
  $("#meta-dialog").showModal();
  setTimeout(() => $("#meta-name").select(), 40);
}

async function saveMeta(e) {
  e.preventDefault();
  const m = getMachine(state.metaTarget);
  if (!m) return;
  const btn = $("#meta-form button[type=submit]");
  const msg = $("#meta-message");
  btn.disabled = true;
  msg.classList.remove("error");
  msg.textContent = "Saving...";
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/meta`, {
      method: "POST",
      body: {
        name: $("#meta-name").value.trim(),
        group: $("#meta-group").value.trim() || "default",
        tags: $("#meta-tags").value,
        isTemplate: $("#meta-template").checked,
      },
    });
    replaceMachine(res.machine);
    $("#meta-dialog").close();
    if (state.selectedId === m.id) updateDetail(res.machine);
    await loadMachines(true);
    toast("Device details updated.");
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function openConfigureDialog(id = state.selectedId) {
  const m = getMachine(id);
  if (!m || !state.options) return;
  if (isActive(m)) {
    toast("Stop the device before editing hardware.", true);
    return;
  }
  state.configureTarget = m.id;
  $("#configure-device").value = m.device;
  $("#configure-cpu").value = String(m.cpu);
  $("#configure-ram").value = String(m.ram / 1024);
  $("#configure-message").textContent = "";
  $("#configure-message").classList.remove("error");
  $("#configure-dialog").showModal();
}

async function configureMachine(e) {
  e.preventDefault();
  const m = getMachine(state.configureTarget || state.selectedId);
  if (!m) return;
  const btn = $("#configure-form button[type=submit]");
  const msg = $("#configure-message");
  btn.disabled = true;
  msg.classList.remove("error");
  msg.textContent = "Saving...";
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/configure`, {
      method: "POST",
      body: {
        device: $("#configure-device").value,
        cpu: Number($("#configure-cpu").value),
        ram: Math.round(Number($("#configure-ram").value) * 1024),
      },
    });
    replaceMachine(res.machine);
    $("#configure-dialog").close();
    if (state.selectedId === m.id) updateDetail(res.machine);
    await loadMachines(true);
    toast("Hardware updated.");
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function openCloneDialog(id = state.selectedId) {
  const m = getMachine(id);
  if (!m) return;
  if (isActive(m)) {
    toast("Stop the device before cloning.", true);
    return;
  }
  state.cloneSource = m;
  $("#clone-name").value = `${m.name} copy`.slice(0, 48);
  $("#clone-message").textContent = "";
  $("#clone-message").classList.remove("error");
  $("#clone-dialog").showModal();
  setTimeout(() => $("#clone-name").select(), 40);
}

async function cloneMachine(e) {
  e.preventDefault();
  const src = state.cloneSource;
  if (!src) return;
  const btn = $("#clone-form button[type=submit]");
  const msg = $("#clone-message");
  btn.disabled = true;
  msg.classList.remove("error");
  msg.textContent = "Cloning disk image...";
  try {
    const res = await api(`/api/machines/${encodeURIComponent(src.id)}/clone`, {
      method: "POST",
      body: { name: $("#clone-name").value.trim(), start: $("#clone-start").checked },
    });
    $("#clone-dialog").close();
    await loadMachines(true);
    openMachine(res.machine.id);
    toast("Device cloned.");
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function confirmWipe(id = state.selectedId) {
  const m = getMachine(id);
  if (!m) return;
  $("#confirm-title").textContent = "Reset Android data?";
  $("#confirm-copy").textContent = `All accounts, apps, and user data on ${m.name} will be wiped. Hardware settings remain intact.`;
  $("#confirm-action").textContent = "Reset Data";
  state.confirmAction = async () => {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/wipe`, { method: "POST", body: {} });
    replaceMachine(res.machine);
    $("#confirm-dialog").close();
    await loadMachines(true);
    toast("Device data reset.");
  };
  $("#confirm-dialog").showModal();
}

function confirmDelete(id = state.selectedId) {
  const m = getMachine(id);
  if (!m) return;
  $("#confirm-title").textContent = "Delete virtual device?";
  $("#confirm-copy").textContent = `${m.name} (${m.serial}) and all of its stored data will be permanently removed.`;
  $("#confirm-action").textContent = "Delete";
  state.confirmAction = async () => {
    await api(`/api/machines/${encodeURIComponent(m.id)}`, { method: "DELETE" });
    $("#confirm-dialog").close();
    if (state.selectedId === m.id) {
      state.selectedId = null;
      disconnectConsole();
      navigate("devices");
    }
    await loadMachines(true);
    toast("Device deleted.");
  };
  $("#confirm-dialog").showModal();
}

async function runConfirm() {
  const btn = $("#confirm-action");
  if (!state.confirmAction) return;
  btn.disabled = true;
  try {
    await state.confirmAction();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    state.confirmAction = null;
  }
}

async function sendKey(key) {
  const m = getMachine(state.selectedId);
  if (!m || m.phase !== "running") return;
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/key`, {
      method: "POST",
      body: { key },
    });
    if (key === "rotate" && typeof res.rotation === "number") {
      if (!state.telemetry) state.telemetry = {};
      state.telemetry.rotation = res.rotation;
      fitDeviceFrame();
      toast(res.rotation === 1 ? "Landscape" : "Portrait");
    }
  } catch (err) {
    toast(err.message, true);
  }
}

async function applyLocation(label, lat, lon) {
  const m = getMachine(state.selectedId);
  if (!m || m.phase !== "running") {
    toast("Device must be running.", true);
    return;
  }
  const msg = $("#location-message");
  msg.classList.remove("error");
  msg.textContent = "Applying coordinates...";
  try {
    const res = await api(`/api/machines/${encodeURIComponent(m.id)}/location`, {
      method: "POST",
      body: { label, latitude: Number(lat), longitude: Number(lon) },
    });
    replaceMachine(res.machine);
    $("#location-latitude").value = res.location.latitude;
    $("#location-longitude").value = res.location.longitude;
    $("#location-state").textContent = `${res.location.label || "Custom"} (${res.location.latitude}, ${res.location.longitude})`;
    msg.textContent = "GPS location updated.";
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
}

async function downloadScreenshot() {
  const m = getMachine(state.selectedId);
  if (!m || m.phase !== "running") return;
  const btn = $("#screenshot-button");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/machines/${encodeURIComponent(m.id)}/screenshot`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new Error("Screenshot failed.");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${m.id}-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast("Screenshot saved.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function setupUpload() {
  const input = $("#apk-file");
  const zone = $("#upload-zone");
  const wrap = $("#upload-progress-wrap");
  const bar = $("#upload-progress-bar");

  const setFile = (file) => {
    $("#file-label").textContent = file ? file.name : "Drop or browse .apk";
    $("#file-sublabel").textContent = file ? `${(file.size / (1024 * 1024)).toFixed(2)} MB` : "Max 250 MB";
    $("#install-button").disabled = !file || getMachine(state.selectedId)?.phase !== "running";
    $("#upload-message").textContent = "";
    $("#upload-message").classList.remove("error");
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

  $("#upload-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const m = getMachine(state.selectedId);
    const file = input.files[0];
    if (!m || !file) return;
    if (!file.name.toLowerCase().endsWith(".apk") || file.size > 250 * 1024 * 1024) {
      $("#upload-message").textContent = "Select a valid .apk file under 250 MB.";
      $("#upload-message").classList.add("error");
      return;
    }
    const btn = $("#install-button");
    const msg = $("#upload-message");
    btn.disabled = true;
    input.disabled = true;
    wrap.hidden = false;
    msg.classList.remove("error");
    msg.textContent = "Uploading...";

    const req = new XMLHttpRequest();
    req.open("POST", `/api/machines/${encodeURIComponent(m.id)}/install`);
    req.setRequestHeader("Content-Type", "application/vnd.android.package-archive");
    req.upload.onprogress = (p) => {
      if (!p.lengthComputable) return;
      const pct = Math.round((p.loaded / p.total) * 100);
      bar.style.width = `${pct}%`;
      msg.textContent = pct === 100 ? "Installing APK..." : `Uploading ${pct}%`;
    };
    req.onload = () => {
      let res = {};
      try {
        res = JSON.parse(req.responseText);
      } catch (_) {}
      const ok = req.status >= 200 && req.status < 300 && res.ok;
      msg.textContent = ok ? "APK installed successfully." : res.message || "APK installation failed.";
      msg.classList.toggle("error", !ok);
      if (ok) {
        input.value = "";
        setFile(null);
        msg.textContent = "APK installed successfully.";
        loadPackages();
      }
      if (req.status === 401) showLogin();
    };
    req.onerror = () => {
      msg.textContent = "Upload connection lost.";
      msg.classList.add("error");
    };
    req.onloadend = () => {
      input.disabled = false;
      wrap.hidden = true;
      btn.disabled = !input.files.length || getMachine(state.selectedId)?.phase !== "running";
    };
    req.send(file);
  });
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: {} });
  } catch (_) {}
  showLogin();
}

/* =========================
   EVENTS & OBSERVERS
   ========================= */
async function onDocumentClick(e) {
  const menuOp = e.target.closest("[data-menu-op]");
  if (menuOp) {
    const id = menuOp.dataset.mid;
    const op = menuOp.dataset.menuOp;
    closeMenu();
    if (op === "power") toggleMachine(id);
    else if (op === "restart") restartMachine(id);
    else if (op === "meta") openMetaDialog(id);
    else if (op === "configure") openConfigureDialog(id);
    else if (op === "clone") openCloneDialog(id);
    else if (op === "batch-clone") farm?.openBatchCloneDialog(id);
    else if (op === "wipe") confirmWipe(id);
    else if (op === "delete") confirmDelete(id);
    else if (op === "adb") {
      const m = getMachine(id);
      if (m) {
        const ok = await copyText(`adb connect 127.0.0.1:${m.adbPort}`);
        toast(ok ? `Copied: adb connect 127.0.0.1:${m.adbPort}` : "Copy failed.", !ok);
      }
    }
    return;
  }

  const manage = e.target.closest("[data-manage]");
  if (manage) {
    e.stopPropagation();
    openManageMenu(manage.dataset.manage, manage);
    return;
  }

  if (!e.target.closest("#ctx-menu") && !e.target.closest("#detail-more")) closeMenu();

  const nav = e.target.closest("[data-nav]")?.dataset.nav;
  if (nav) return navigate(nav);

  const pkgBtn = e.target.closest("[data-pkg-action]");
  if (pkgBtn) return runPackageAction(pkgBtn.dataset.pkg, pkgBtn.dataset.pkgAction);

  const copySerial = e.target.closest("[data-copy-serial]")?.dataset.copySerial;
  if (copySerial) {
    e.stopPropagation();
    const ok = await copyText(`adb connect ${copySerial}`);
    toast(ok ? `Copied: adb connect ${copySerial}` : "Copy failed.", !ok);
    return;
  }

  const action = e.target.closest("[data-action]")?.dataset.action;
  if (action === "toggle-theme") return toggleTheme();
  if (action === "toggle-sidebar") return toggleSidebar();
  if (action === "create") return openCreateDialog();

  const open = e.target.closest("[data-open]")?.dataset.open;
  if (open) return openMachine(open);

  const power = e.target.closest("[data-power]")?.dataset.power;
  if (power) toggleMachine(power);
}

function bindEvents() {
  document.addEventListener("click", onDocumentClick);

  $("#login-form").addEventListener("submit", login);
  $("#toggle-password").addEventListener("click", () => {
    const inp = $("#login-password");
    const show = inp.type === "password";
    inp.type = show ? "text" : "password";
    $("#toggle-password").innerHTML = icon(show ? "eye-off" : "eye");
    $("#toggle-password").setAttribute("aria-label", show ? "Hide password" : "Show password");
  });

  $("#logout-button").addEventListener("click", logout);
  $("#refresh-button").addEventListener("click", () => loadMachines());
  $("#sidebar-backdrop").addEventListener("click", closeMobileSidebar);

  $("#sidebar-search-input").addEventListener("input", (e) => {
    state.sidebarQuery = e.target.value;
    state.sidebarPage = 1;
    renderSidebarList();
  });
  $("#sidebar-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = getFilteredSidebar()[0];
      if (first) openMachine(first.id);
    }
  });
  $("#quick-prev").addEventListener("click", () => {
    if (state.sidebarPage > 1) {
      state.sidebarPage -= 1;
      renderSidebarList();
    }
  });
  $("#quick-next").addEventListener("click", () => {
    state.sidebarPage += 1;
    renderSidebarList();
  });

  document.addEventListener("keydown", (e) => {
    if (!state.authenticated) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (e.key === "/") {
      e.preventDefault();
      if ($("#app").classList.contains("sidebar-collapsed")) toggleSidebar();
      $("#sidebar-search-input").focus();
    } else if (e.key === "[") {
      toggleSidebar();
    } else if (e.key === "Escape") {
      closeMenu();
    }
  });

  $$("[data-filter]").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.tableFilter = btn.dataset.filter;
      $$("[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      renderDevicesList();
    }),
  );
  $("#group-filter").addEventListener("change", (e) => {
    state.groupFilter = e.target.value;
    renderDevicesList();
  });
  $$("[data-view-mode]").forEach((btn) =>
    btn.addEventListener("click", () => applyViewMode(btn.dataset.viewMode)),
  );
  $("#table-search-input").addEventListener("input", (e) => {
    state.tableQuery = e.target.value;
    renderDevicesList();
  });
  $("#reset-filters-button").addEventListener("click", () => {
    state.tableFilter = "all";
    state.groupFilter = "all";
    state.tableQuery = "";
    $("#table-search-input").value = "";
    $("#group-filter").value = "all";
    $$("[data-filter]").forEach((b) => b.classList.toggle("active", b.dataset.filter === "all"));
    renderDevicesList();
  });
  $("#machine-table").addEventListener("change", (e) => {
    const id = e.target.dataset?.selectVm;
    if (!id) return;
    if (e.target.checked) state.selectedBulk.add(id);
    else state.selectedBulk.delete(id);
    updateBulkBar();
  });
  $("#select-all-vms").addEventListener("change", (e) => {
    getFilteredDevices().forEach((m) => {
      if (e.target.checked) state.selectedBulk.add(m.id);
      else state.selectedBulk.delete(m.id);
    });
    renderDevicesList();
  });
  $("#bulk-wall").addEventListener("click", () => {
    const ids = [...state.selectedBulk];
    if (ids.length) farm?.openWallWithSelection(ids);
  });
  $("#bulk-start").addEventListener("click", () => runBulk("start"));
  $("#bulk-stop").addEventListener("click", () => runBulk("stop"));

  // Single Device Player controls
  $("#power-button").addEventListener("click", () => toggleMachine(state.selectedId));
  $("#overlay-start-btn").addEventListener("click", () => toggleMachine(state.selectedId));
  $("#header-restart-button").addEventListener("click", () => restartMachine(state.selectedId));
  $("#detail-more").addEventListener("click", (e) => {
    e.stopPropagation();
    openManageMenu(state.selectedId, e.currentTarget);
  });

  $$("[data-widget]").forEach((btn) =>
    btn.addEventListener("click", () => openWidget(btn.dataset.widget)),
  );
  $("#widget-close").addEventListener("click", closeWidget);

  $$("[data-key]").forEach((btn) =>
    btn.addEventListener("click", () => sendKey(btn.dataset.key)),
  );
  $("#screenshot-button").addEventListener("click", downloadScreenshot);
  $("#reconnect-button").addEventListener("click", () => {
    const m = getMachine(state.selectedId);
    if (m?.phase === "running") connectConsole(m);
  });
  $("#fullscreen-button").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $("#console-stage").requestFullscreen();
    } catch (_) {
      toast("Fullscreen is not supported in this browser.", true);
    }
  });
  document.addEventListener("fullscreenchange", () => requestAnimationFrame(fitDeviceFrame));

  $("#autostart-toggle").addEventListener("change", (e) => setAutostart(e.target.checked));
  $("#refresh-telemetry-btn").addEventListener("click", loadTelemetry);
  $("#id-audit-refresh").addEventListener("click", loadIdentityAudit);
  $("#identity-form").addEventListener("submit", (e) => {
    e.preventDefault();
    applySingleIdentity(false);
  });
  $("#id-apply-reboot-btn").addEventListener("click", () => applySingleIdentity(true));
  $("#text-input-form").addEventListener("submit", sendText);

  $("#location-form").addEventListener("submit", (e) => {
    e.preventDefault();
    applyLocation("", $("#location-latitude").value, $("#location-longitude").value);
  });
  $$("[data-location]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const [label, lat, lon] = btn.dataset.location.split("|");
      $("#location-latitude").value = lat;
      $("#location-longitude").value = lon;
      applyLocation(label, lat, lon);
    }),
  );

  $("#battery-level").addEventListener("input", (e) => {
    $("#battery-slider-val").textContent = e.target.value;
  });
  $("#battery-form").addEventListener("submit", applyBattery);

  $("#pkg-refresh").addEventListener("click", loadPackages);
  $("#pkg-search").addEventListener("input", (e) => {
    state.pkgQuery = e.target.value;
    renderPackages();
  });

  $("#shell-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runShell($("#shell-command").value);
  });
  $$("[data-shell-preset]").forEach((btn) =>
    btn.addEventListener("click", () => {
      $("#shell-command").value = btn.dataset.shellPreset;
      runShell(btn.dataset.shellPreset);
    }),
  );
  $("#shell-clear").addEventListener("click", () => {
    $("#shell-output").textContent = "";
  });

  $$("[data-log-kind]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.logKind = btn.dataset.logKind;
      $$("[data-log-kind]").forEach((b) => b.classList.toggle("active", b === btn));
      loadLogs();
    });
  });
  $("#log-refresh").addEventListener("click", loadLogs);
  $("#log-copy").addEventListener("click", async () => {
    const ok = await copyText($("#log-viewer").textContent);
    toast(ok ? "Logs copied." : "Copy failed.", !ok);
  });

  $("#copy-adb").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const label = $("span", btn);
    const ok = await copyText($("#adb-command").textContent.trim());
    if (!ok) return toast("Copy failed.", true);
    btn.disabled = true;
    label.textContent = "Copied";
    setTimeout(() => {
      btn.disabled = false;
      label.textContent = "Copy command";
    }, 1500);
  });

  $("#machine-form").addEventListener("submit", createMachine);
  $("#machine-count").addEventListener("input", updateCreateSubmitLabel);
  $("#machine-backend").addEventListener("change", syncCreateBackendUI);
  $("#machine-identity").addEventListener("change", (e) => {
    if ($("#machine-backend").value === "avd" && e.target.value !== "none") {
      $("#machine-root").checked = true;
      const curApi = Number($("#machine-system").value);
      if (curApi !== 35 && curApi !== 36) $("#machine-system").value = "36";
    }
  });
  $("#machine-root").addEventListener("change", (e) => {
    if (e.target.checked) {
      const curApi = Number($("#machine-system").value);
      if (curApi !== 35 && curApi !== 36) $("#machine-system").value = "36";
    }
  });
  $("#meta-form").addEventListener("submit", saveMeta);
  $("#clone-form").addEventListener("submit", cloneMachine);
  $("#configure-form").addEventListener("submit", configureMachine);
  $("#confirm-action").addEventListener("click", runConfirm);
  $$("[data-close-modal]").forEach((btn) =>
    btn.addEventListener("click", () => btn.closest("dialog")?.close()),
  );
  $$("dialog").forEach((d) =>
    d.addEventListener("click", (e) => {
      if (e.target === d) d.close();
    }),
  );

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => fitDeviceFrame()).observe($("#console-stage"));
    new ResizeObserver(() => {
      recomputeSidebarPageSize();
      renderSidebarList();
    }).observe($("#quick-list"));
  }
  window.addEventListener("resize", () => {
    if (window.innerWidth > 960) closeMobileSidebar();
    recomputeSidebarPageSize();
    renderSidebarList();
    fitDeviceFrame();
  });

  $("#screen").addEventListener("pointerdown", () => $("#screen").focus());
  setupUpload();
}

farm = createFarmController({
  api,
  toast,
  copyText,
  escapeHtml,
  icon,
  statusLabel,
  statusClass,
  isActive,
  formatGb,
  getMachines: () => state.machines,
  getSummary: () => state.summary,
  getOptions: () => state.options,
  getMachine,
  openMachine,
  loadMachines,
  openCreateDialog,
  openMetaDialog,
  navigate,
  showLogin,
});

bindEvents();
initialize();
