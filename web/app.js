import RFB from "/core/rfb.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  authenticated: false,
  machines: [],
  options: null,
  selectedId: null,
  loading: false,
  refreshTimer: null,
  toastTimer: null,
  confirmAction: null,
  cloneSource: null,
  rfb: null,
  consoleToken: null,
  consoleConnected: false,
  consoleReconnectTimer: null,
  disconnectExpected: false,
};

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
  return state.machines.find((machine) => machine.id === id) || null;
}

function statusLabel(machine) {
  const labels = {
    running: "Running",
    starting: "Starting",
    stopped: "Stopped",
    error: "Error",
  };
  return labels[machine.phase] || "Unknown";
}

function statusClass(machine) {
  return ["running", "starting", "error"].includes(machine.phase)
    ? machine.phase
    : "stopped";
}

function isActive(machine) {
  return machine?.phase === "running" || machine?.phase === "starting";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof Blob) && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  });
  let data = {};
  try {
    data = await response.json();
  } catch (_) {}
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
  }
  if (!response.ok) {
    throw new Error(data.message || `Permintaan gagal (${response.status}).`);
  }
  return data;
}

function toast(message, error = false) {
  const element = $("#toast");
  clearTimeout(state.toastTimer);
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = false;
  state.toastTimer = setTimeout(() => {
    element.hidden = true;
  }, 3600);
}

function copyTextFallback(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute("aria-hidden", "true");
  Object.assign(textarea.style, {
    position: "fixed",
    top: "0",
    left: "-9999px",
    width: "1px",
    height: "1px",
    opacity: "0",
    pointerEvents: "none",
  });
  document.body.append(textarea);
  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, text.length);
  const handleCopy = (event) => {
    if (!event.clipboardData) return;
    event.clipboardData.setData("text/plain", text);
    event.preventDefault();
  };
  document.addEventListener("copy", handleCopy, { once: true });
  try {
    return document.execCommand("copy");
  } catch (_) {
    return false;
  } finally {
    document.removeEventListener("copy", handleCopy);
    textarea.remove();
    $("#screen")?.focus({ preventScroll: true });
  }
}

async function copyText(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {}
  }
  return copyTextFallback(text);
}

function showLogin() {
  state.authenticated = false;
  clearInterval(state.refreshTimer);
  state.refreshTimer = null;
  disconnectConsole();
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-password").focus();
}

function showApp() {
  state.authenticated = true;
  $("#login").hidden = true;
  $("#app").hidden = false;
}

async function initialize() {
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
  } catch (error) {
    showLogin();
    $("#login-message").textContent = error.message;
    $("#login-message").classList.add("error");
  }
}

async function login(event) {
  event.preventDefault();
  const button = $("#login-form button[type=submit]");
  const message = $("#login-message");
  button.disabled = true;
  message.classList.remove("error");
  message.textContent = "Memeriksa...";
  try {
    await api("/api/login", {
      method: "POST",
      body: { password: $("#login-password").value },
    });
    message.textContent = "";
    $("#login-password").value = "";
    showApp();
    await loadConfiguration();
    await loadMachines();
    startPolling();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

async function loadConfiguration() {
  if (state.options) return;
  const result = await api("/api/options");
  state.options = result;
  fillOptions();
}

function fillOptions() {
  const options = state.options;
  if (!options) return;
  $("#machine-device").innerHTML = options.devices
    .map(
      (device) =>
        `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name)} · ${escapeHtml(device.kind)}</option>`,
    )
    .join("");
  const numberOptions = (values, label) =>
    values
      .map((value) => {
        const text = label(value);
        return `<option value="${value}">${escapeHtml(text)}</option>`;
      })
      .join("");
  const storageValues = [
    8, 16, 32, 64, 128, 256, 512, 1024, 2048,
  ];
  $("#machine-storage").innerHTML = numberOptions(
    storageValues,
    (value) => `${value} GB`,
  );
  $("#machine-system").innerHTML = options.systems
    .map(
      (system) =>
        `<option value="${system.api}">${escapeHtml(
          system.label,
        )} · API ${system.api}${system.installed ? "" : " · dipasang otomatis"}</option>`,
    )
    .join("");
  const installed = [...options.systems]
    .filter((system) => system.installed)
    .sort((a, b) => b.api - a.api)[0];
  if (installed) $("#machine-system").value = String(installed.api);
  $("#machine-cpu").max = String(options.limits.cpu);
  $("#machine-ram").max = String(options.limits.ram);
  $("#machine-cpu").value = String(Math.min(16, options.limits.cpu));
  $("#machine-ram").value = String(Math.min(16, options.limits.ram));
  $("#machine-storage").value = "64";
  $("#configure-device").innerHTML = options.devices
    .map(
      (device) =>
        `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name)} · ${escapeHtml(device.kind)}</option>`,
    )
    .join("");
  $("#configure-cpu").max = String(options.limits.cpu);
  $("#configure-ram").max = String(options.limits.ram);
}

async function loadMachines(silent = false) {
  if (state.loading || !state.authenticated) return;
  state.loading = true;
  if (!silent) $("#refresh-button").classList.add("loading");
  try {
    const result = await api("/api/machines");
    if (!result.machines) return;
    state.machines = result.machines;
    renderMachines(result.summary);
    if (state.selectedId) {
      const selected = getMachine(state.selectedId);
      if (selected) updateDetail(selected);
      else showDashboard();
    }
    $("#last-updated").textContent = `Diperbarui ${new Date().toLocaleTimeString("id-ID", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`;
  } catch (error) {
    if (!silent) toast(error.message, true);
  } finally {
    state.loading = false;
    $("#refresh-button").classList.remove("loading");
  }
}

function startPolling() {
  clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(() => loadMachines(true), 3000);
}

function renderMachines(summary) {
  const table = $("#machine-table");
  const quickList = $("#quick-list");
  const empty = $("#empty-state");
  empty.hidden = state.machines.length > 0;
  table.closest("table").hidden = state.machines.length === 0;
  $("#quick-count").textContent = state.machines.length;
  $("#capacity-note").textContent = `Host ${summary?.hostCpu || "—"} vCPU · ${
    summary?.hostRam || "—"
  } GB`;

  table.innerHTML = state.machines
    .map((machine) => {
      const active = isActive(machine);
      const powerIcon = active ? "stop" : "play";
      const powerTitle = active ? "Matikan mesin" : "Mulai mesin";
      return `
        <tr data-machine-row="${escapeHtml(machine.id)}">
          <td>
            <button class="machine-cell plain-button" data-open="${escapeHtml(machine.id)}" type="button">
              <span class="machine-copy">
                <strong>${escapeHtml(machine.name)}</strong>
                <small>${escapeHtml(machine.androidVersion)} · ${escapeHtml(machine.deviceLabel)}</small>
                <span class="machine-mobile-meta">${machine.cpu} CPU · ${machine.ram / 1024} GB</span>
              </span>
            </button>
          </td>
          <td><span class="status-pill ${statusClass(machine)}">${statusLabel(machine)}</span></td>
          <td><span>${escapeHtml(machine.deviceLabel)}</span><br><small>${escapeHtml(machine.androidVersion)}</small></td>
          <td>${machine.cpu} CPU · ${machine.ram / 1024} GB</td>
          <td><code class="adopt-command">${escapeHtml(machine.serial)}</code></td>
          <td>
            <div class="row-actions">
              <button class="icon-button" data-power="${escapeHtml(machine.id)}" type="button" title="${powerTitle}">
                ${icon(powerIcon)}
              </button>
              <button class="icon-button" data-open="${escapeHtml(machine.id)}" type="button" title="Buka mesin">
                ${icon("chevron")}
              </button>
            </div>
          </td>
        </tr>`;
    })
    .join("");

  quickList.innerHTML = state.machines
    .map(
      (machine) => `
        <button class="quick-machine ${state.selectedId === machine.id ? "active" : ""}" data-open="${escapeHtml(machine.id)}" type="button">
          <span class="status-dot ${statusClass(machine)}"></span>
          <span>${escapeHtml(machine.name)}</span>
          <small>${statusLabel(machine)}</small>
        </button>`,
    )
    .join("");

  const running = state.machines.filter((machine) => machine.phase === "running").length;
  const autostart = state.machines.filter((machine) => machine.autostart).length;
  $("#stat-running").innerHTML = `${running} <small>aktif</small>`;
  $("#stat-total").textContent = `dari ${state.machines.length} mesin`;
  const cpuPercent = Math.max(0, Math.min(100, summary?.cpuPercent || 0));
  const ramPercent = Math.max(0, Math.min(100, summary?.ramPercent || 0));
  const hostRam = summary?.hostRam || 0;
  const availableRam = summary?.hostRamAvailable || 0;
  $("#stat-host-cpu-percent").textContent = `${cpuPercent}%`;
  $("#stat-host-ram-percent").textContent = `${ramPercent}%`;
  $("#cpu-meter").style.width = `${cpuPercent}%`;
  $("#ram-meter").style.width = `${ramPercent}%`;
  $("#stat-host-cpu").textContent = `${summary?.cpuAllocated || 0} vCPU untuk VM · ${
    summary?.hostCpu || "—"
  } total`;
  $("#stat-host-ram").textContent = `${Math.max(0, hostRam - availableRam)} / ${
    hostRam || "—"
  } GB terpakai`;
  $("#stat-autostart").innerHTML = `${autostart} <small>mesin</small>`;
}

function showDashboard() {
  state.selectedId = null;
  disconnectConsole();
  $("#app").classList.remove("detail-mode");
  $("#app").classList.remove("inspector-open");
  $("#dashboard-view").hidden = false;
  $("#detail-view").hidden = true;
  $("#page-title").textContent = "Virtual machines";
  $$(".quick-machine").forEach((item) => item.classList.remove("active"));
}

async function openMachine(id) {
  const machine = getMachine(id);
  if (!machine) return;
  state.selectedId = id;
  $("#app").classList.add("detail-mode");
  $("#dashboard-view").hidden = true;
  $("#detail-view").hidden = false;
  $("#page-title").textContent = machine.name;
  $$(".quick-machine").forEach((item) => {
    item.classList.toggle("active", item.dataset.open === id);
  });
  updateDetail(machine);
  if (window.innerWidth <= 800) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function updateDetail(machine) {
  $("#page-title").textContent = machine.name;
  $("#detail-name").textContent = machine.name;
  $("#detail-subtitle").textContent = `${machine.androidVersion} · ${machine.deviceLabel}`;
  const status = $("#detail-status-pill");
  status.className = `status-pill ${statusClass(machine)}`;
  status.textContent = statusLabel(machine);

  $("#prop-system").textContent = `${machine.androidVersion} · API ${machine.apiLevel}`;
  $("#prop-device").textContent = machine.deviceLabel;
  $("#prop-screen").textContent = machine.screen
    ? `${machine.screen.width} × ${machine.screen.height}`
    : "—";
  $("#prop-cpu").textContent = `${machine.cpu} vCPU`;
  $("#prop-ram").textContent = `${machine.ram / 1024} GB`;
  $("#prop-storage").textContent = `${machine.storage} GB`;
  $("#prop-root").textContent = machine.root ? "Ya · Google APIs" : "Tidak";
  $("#autostart-toggle").checked = Boolean(machine.autostart);

  const error = $("#detail-error");
  error.hidden = !machine.error;
  error.textContent = machine.error || "";

  const power = $("#power-button");
  const active = isActive(machine);
  power.innerHTML = `${icon(active ? "stop" : "play")}<span>${active ? "Matikan" : "Mulai"}</span>`;
  $("#restart-button").disabled = machine.phase !== "running";
  $("#clone-button").disabled = active;
  $("#clone-button").title = active ? "Matikan mesin sebelum clone" : "Clone mesin";
  $("#delete-button").disabled = false;
  $("#install-button").disabled = machine.phase !== "running" || !$("#apk-file").files.length;

  $("#adb-state").textContent = machine.adbState === "device" ? "Online" : machine.adbState;
  const host = location.hostname || "localhost";
  const sshUser = machine.sshUser || "ubuntu";
  $("#adb-command").textContent = [
    `ssh -i KUNCI.pem -N -L 127.0.0.1:${machine.adbPort}:127.0.0.1:${machine.adbPort} ${sshUser}@${host}`,
    `adb connect 127.0.0.1:${machine.adbPort}`,
  ].join("\n");

  const screen = $("#screen");
  const frame = screen.closest(".device-frame");
  if (machine.screen) {
    frame.style.setProperty("--device-aspect", `${machine.screen.width}/${machine.screen.height}`);
    frame.style.setProperty(
      "--frame-width",
      machine.screen.width / machine.screen.height > 0.7 ? "430px" : "286px",
    );
  }
  const lastLocation = machine.lastLocation;
  $("#location-state").textContent = lastLocation
    ? lastLocation.label || "Aktif"
    : "Belum diatur";
  if (lastLocation) {
    $("#location-latitude").value = lastLocation.latitude;
    $("#location-longitude").value = lastLocation.longitude;
  }
  $("#location-form").querySelector("button[type=submit]").disabled = machine.phase !== "running";
  $$("#location-presets button").forEach((button) => {
    button.disabled = machine.phase !== "running";
  });

  if (machine.phase === "running") {
    if (!state.consoleConnected && state.consoleToken !== machine.consoleToken) {
      connectConsole(machine);
    } else if (!state.consoleConnected && !state.rfb) {
      connectConsole(machine);
    }
  } else {
    disconnectConsole();
    if (machine.phase === "starting") {
      setConsoleOverlay(
        "starting",
        "Mesin sedang dimulai",
        "Layar akan terhubung otomatis setelah booting selesai.",
      );
    } else if (machine.phase === "error") {
      setConsoleOverlay("error", "Mesin bermasalah", machine.error || "Periksa status mesin.");
    } else {
      setConsoleOverlay("stopped", "Mesin tidak aktif", "Mulai mesin untuk membuka layar Android.");
    }
  }
}

function openInspector() {
  $("#app").classList.add("inspector-open");
}

function closeInspector() {
  $("#app").classList.remove("inspector-open");
}

function setConsoleOverlay(kind, title, message) {
  const overlay = $("#console-overlay");
  overlay.hidden = false;
  overlay.dataset.kind = kind;
  const loader = $(".loader", overlay);
  loader.hidden = kind !== "connecting";
  $("#console-overlay-title").textContent = title;
  $("#console-overlay-message").textContent = message;
  const dot = $("#console-dot");
  dot.className = "status-dot";
  if (kind === "starting" || kind === "connecting") dot.classList.add("starting");
  if (kind === "connected") dot.classList.add("online");
  if (kind === "error") dot.classList.add("error");
  $("#console-label").textContent =
    kind === "connected"
      ? "Live"
      : kind === "starting"
        ? "Booting"
        : kind === "connecting"
          ? "Menghubungkan"
          : kind === "error"
            ? "Bermasalah"
            : "Terputus";
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
  setConsoleOverlay("connecting", "Menghubungkan layar", "Menyiapkan stream perangkat...");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = `${protocol}//${location.host}/websockify?token=${encodeURIComponent(
    machine.consoleToken,
  )}`;
  try {
    const current = new RFB($("#screen"), socket, {
      shared: true,
      credentials: {},
      wsProtocols: ["binary"],
    });
    state.rfb = current;
    state.disconnectExpected = false;
    current.scaleViewport = true;
    current.resizeSession = false;
    current.background = "#050607";
    current.addEventListener("connect", () => {
      if (state.rfb !== current) return;
      state.consoleConnected = true;
      $("#console-overlay").hidden = true;
      setConsoleOverlay("connected", "", "");
      $("#console-overlay").hidden = true;
    });
    current.addEventListener("disconnect", () => {
      if (state.rfb !== current) return;
      state.consoleConnected = false;
      state.rfb = null;
      if (state.disconnectExpected) return;
      const selected = getMachine(state.selectedId);
      if (selected?.phase === "running") {
        setConsoleOverlay(
          "connecting",
          "Menyambungkan ulang",
          "Stream terputus. Mencoba kembali...",
        );
        state.consoleReconnectTimer = setTimeout(() => {
          const latest = getMachine(state.selectedId);
          if (latest?.phase === "running") connectConsole(latest);
        }, 2500);
      }
    });
    current.addEventListener("securityfailure", () => {
      if (state.rfb !== current) return;
      setConsoleOverlay("error", "Akses layar ditolak", "Muat ulang halaman lalu coba lagi.");
    });
  } catch (error) {
    setConsoleOverlay("error", "Layar tidak tersedia", error.message || "Gagal membuka stream.");
  }
}

async function toggleMachine(id) {
  const machine = getMachine(id);
  if (!machine) return;
  const action = isActive(machine) ? "stop" : "start";
  try {
    toast(action === "stop" ? "Menghentikan mesin..." : "Memulai mesin...");
    const result = await api(`/api/machines/${encodeURIComponent(id)}/${action}`, {
      method: "POST",
      body: {},
    });
    replaceMachine(result.machine);
    await loadMachines(true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function restartMachine() {
  const machine = getMachine(state.selectedId);
  if (!machine || machine.phase !== "running") return;
  try {
    await api(`/api/machines/${encodeURIComponent(machine.id)}/restart`, {
      method: "POST",
      body: {},
    });
    disconnectConsole();
    toast("Mesin sedang direstart.");
    await loadMachines(true);
  } catch (error) {
    toast(error.message, true);
  }
}

function replaceMachine(machine) {
  const index = state.machines.findIndex((item) => item.id === machine.id);
  if (index >= 0) state.machines[index] = machine;
  else state.machines.push(machine);
}

async function setAutostart(enabled) {
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  try {
    const result = await api(`/api/machines/${encodeURIComponent(machine.id)}/autostart`, {
      method: "POST",
      body: { enabled },
    });
    replaceMachine(result.machine);
    toast(enabled ? "Autostart diaktifkan." : "Autostart dimatikan.");
    await loadMachines(true);
  } catch (error) {
    $("#autostart-toggle").checked = !enabled;
    toast(error.message, true);
  }
}

function openConfigureDialog() {
  const machine = getMachine(state.selectedId);
  if (!machine || !state.options) return;
  if (isActive(machine)) {
    toast("Matikan mesin sebelum mengubah hardware.", true);
    return;
  }
  $("#configure-device").value = machine.device;
  $("#configure-cpu").value = String(machine.cpu);
  $("#configure-ram").value = String(machine.ram / 1024);
  $("#configure-message").textContent = "";
  $("#configure-message").classList.remove("error");
  $("#configure-dialog").showModal();
}

async function configureMachine(event) {
  event.preventDefault();
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  const submit = $("#configure-form button[type=submit]");
  const message = $("#configure-message");
  submit.disabled = true;
  message.classList.remove("error");
  message.textContent = "Menyimpan konfigurasi...";
  try {
    const result = await api(`/api/machines/${encodeURIComponent(machine.id)}/configure`, {
      method: "POST",
      body: {
        device: $("#configure-device").value,
        cpu: Number($("#configure-cpu").value),
        ram: Math.round(Number($("#configure-ram").value) * 1024),
      },
    });
    replaceMachine(result.machine);
    $("#configure-dialog").close();
    updateDetail(result.machine);
    await loadMachines(true);
    toast("Konfigurasi mesin disimpan.");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    submit.disabled = false;
  }
}

async function applyLocation(label, latitude, longitude) {
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  if (machine.phase !== "running") {
    toast("Mesin harus berjalan untuk mengatur GPS.", true);
    return;
  }
  const message = $("#location-message");
  message.classList.remove("error");
  message.textContent = "Mengirim koordinat...";
  try {
    const result = await api(`/api/machines/${encodeURIComponent(machine.id)}/location`, {
      method: "POST",
      body: { label, latitude: Number(latitude), longitude: Number(longitude) },
    });
    replaceMachine(result.machine);
    $("#location-latitude").value = result.location.latitude;
    $("#location-longitude").value = result.location.longitude;
    $("#location-state").textContent = result.location.label || "Aktif";
    message.textContent = `GPS aktif di ${Number(latitude).toFixed(4)}, ${Number(longitude).toFixed(4)}.`;
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

function resetMachineData() {
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  $("#confirm-title").textContent = "Reset data Android?";
  $("#confirm-copy").textContent =
    `Semua data, akun, dan aplikasi pada ${machine.name} akan dihapus. Konfigurasi hardware tetap disimpan.`;
  $("#confirm-action").textContent = "Reset data";
  state.confirmAction = async () => {
    const result = await api(`/api/machines/${encodeURIComponent(machine.id)}/wipe`, {
      method: "POST",
      body: {},
    });
    replaceMachine(result.machine);
    $("#configure-dialog").close();
    $("#confirm-dialog").close();
    await loadMachines(true);
    toast("Data mesin telah direset.");
  };
  $("#confirm-dialog").showModal();
}

async function downloadScreenshot() {
  const machine = getMachine(state.selectedId);
  if (!machine || machine.phase !== "running") {
    toast("Mesin harus berjalan untuk mengambil screenshot.", true);
    return;
  }
  const button = $("#screenshot-button");
  button.disabled = true;
  try {
    const response = await fetch(
      `/api/machines/${encodeURIComponent(machine.id)}/screenshot`,
      { credentials: "same-origin", cache: "no-store" },
    );
    if (response.status === 401) {
      showLogin();
      return;
    }
    if (!response.ok) {
      let message = "Screenshot gagal dibuat.";
      try {
        message = (await response.json()).message || message;
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${machine.id}-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.png`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast("Screenshot diunduh.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function openCreateDialog() {
  if (!state.options) return;
  $("#machine-form").reset();
  fillOptions();
  $("#machine-form-message").textContent = "";
  $("#machine-form-message").classList.remove("error");
  $("#machine-dialog").showModal();
  setTimeout(() => $("#machine-name").focus(), 40);
}

async function createMachine(event) {
  event.preventDefault();
  const button = $("#create-submit");
  const message = $("#machine-form-message");
  const payload = {
    name: $("#machine-name").value.trim(),
    device: $("#machine-device").value,
    api: Number($("#machine-system").value),
    cpu: Number($("#machine-cpu").value),
    ram: Math.round(Number($("#machine-ram").value) * 1024),
    storage: Number($("#machine-storage").value),
    root: $("#machine-root").checked,
    start: $("#machine-start").checked,
    autostart: $("#machine-autostart").checked,
  };
  button.disabled = true;
  message.classList.remove("error");
  message.textContent = "Membuat disk dan konfigurasi mesin...";
  try {
    const result = await api("/api/machines", { method: "POST", body: payload });
    replaceMachine(result.machine);
    $("#machine-dialog").close();
    await loadMachines(true);
    openMachine(result.machine.id);
    toast("Mesin berhasil dibuat.");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

function openCloneDialog() {
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  if (isActive(machine)) {
    toast("Matikan mesin sebelum membuat clone.", true);
    return;
  }
  state.cloneSource = machine;
  $("#clone-name").value = `${machine.name} copy`.slice(0, 48);
  $("#clone-message").textContent = "";
  $("#clone-message").classList.remove("error");
  $("#clone-dialog").showModal();
  setTimeout(() => $("#clone-name").select(), 40);
}

async function cloneMachine(event) {
  event.preventDefault();
  const source = state.cloneSource;
  if (!source) return;
  const submit = $("#clone-form button[type=submit]");
  const message = $("#clone-message");
  submit.disabled = true;
  message.classList.remove("error");
  message.textContent = "Menyalin disk mesin...";
  try {
    const result = await api(`/api/machines/${encodeURIComponent(source.id)}/clone`, {
      method: "POST",
      body: {
        name: $("#clone-name").value.trim(),
        start: $("#clone-start").checked,
      },
    });
    $("#clone-dialog").close();
    await loadMachines(true);
    openMachine(result.machine.id);
    toast("Clone berhasil dibuat.");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    submit.disabled = false;
  }
}

function confirmDelete() {
  const machine = getMachine(state.selectedId);
  if (!machine) return;
  $("#confirm-title").textContent = "Hapus mesin?";
  $("#confirm-copy").textContent = `${machine.name} beserta seluruh data Android-nya akan dihapus permanen.`;
  $("#confirm-action").textContent = "Hapus mesin";
  state.confirmAction = async () => {
    await api(`/api/machines/${encodeURIComponent(machine.id)}`, { method: "DELETE" });
    $("#confirm-dialog").close();
    state.selectedId = null;
    disconnectConsole();
    await loadMachines(true);
    showDashboard();
    toast("Mesin dihapus.");
  };
  $("#confirm-dialog").showModal();
}

async function runConfirm() {
  const button = $("#confirm-action");
  if (!state.confirmAction) return;
  button.disabled = true;
  try {
    await state.confirmAction();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    state.confirmAction = null;
  }
}

async function sendKey(key) {
  const machine = getMachine(state.selectedId);
  if (!machine || machine.phase !== "running") return;
  try {
    await api(`/api/machines/${encodeURIComponent(machine.id)}/key`, {
      method: "POST",
      body: { key },
    });
  } catch (error) {
    toast(error.message, true);
  }
}

function setupUpload() {
  const fileInput = $("#apk-file");
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    $("#file-label").textContent = file ? file.name : "Pilih file APK";
    $("#install-button").disabled = !file || getMachine(state.selectedId)?.phase !== "running";
    $("#upload-message").textContent = "";
    $("#upload-message").classList.remove("error");
  });

  $("#upload-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const machine = getMachine(state.selectedId);
    const file = fileInput.files[0];
    if (!machine || !file) return;
    if (!file.name.toLowerCase().endsWith(".apk") || file.size > 250 * 1024 * 1024) {
      $("#upload-message").textContent = "Pilih file APK dengan ukuran maksimal 250 MB.";
      $("#upload-message").classList.add("error");
      return;
    }
    const button = $("#install-button");
    const message = $("#upload-message");
    button.disabled = true;
    fileInput.disabled = true;
    message.classList.remove("error");
    message.textContent = "Mengunggah aplikasi...";
    const request = new XMLHttpRequest();
    request.open(
      "POST",
      `/api/machines/${encodeURIComponent(machine.id)}/install`,
    );
    request.setRequestHeader("Content-Type", "application/vnd.android.package-archive");
    request.upload.onprogress = (progress) => {
      if (!progress.lengthComputable) return;
      const percentage = Math.round((progress.loaded / progress.total) * 100);
      message.textContent =
        percentage === 100
          ? "Memasang aplikasi..."
          : `Mengunggah aplikasi... ${percentage}%`;
    };
    request.onload = () => {
      let result = {};
      try {
        result = JSON.parse(request.responseText);
      } catch (_) {}
      const success = request.status >= 200 && request.status < 300 && result.ok;
      message.textContent = result.message || (success ? "APK terpasang." : "APK gagal dipasang.");
      message.classList.toggle("error", !success);
      if (success) {
        fileInput.value = "";
        $("#file-label").textContent = "Pilih file APK";
      }
      if (request.status === 401) showLogin();
    };
    request.onerror = () => {
      message.textContent = "Koneksi terputus saat mengunggah.";
      message.classList.add("error");
    };
    request.onloadend = () => {
      fileInput.disabled = false;
      button.disabled =
        !fileInput.files.length || getMachine(state.selectedId)?.phase !== "running";
    };
    request.send(file);
  });
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: {} });
  } catch (_) {}
  showLogin();
}

function closeDialog(button) {
  button.closest("dialog")?.close();
}

function handleDelegatedClick(event) {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "create") {
    openCreateDialog();
    return;
  }
  if (action === "dashboard") {
    showDashboard();
    return;
  }
  const open = event.target.closest("[data-open]")?.dataset.open;
  if (open) {
    openMachine(open);
    return;
  }
  const power = event.target.closest("[data-power]")?.dataset.power;
  if (power) toggleMachine(power);
}

function bindEvents() {
  $("#login-form").addEventListener("submit", login);
  $("#logout-button").addEventListener("click", logout);
  $("#refresh-button").addEventListener("click", () => loadMachines());
  document.addEventListener("click", handleDelegatedClick);
  $("#machine-form").addEventListener("submit", createMachine);
  $("#clone-form").addEventListener("submit", cloneMachine);
  $("#clone-button").addEventListener("click", openCloneDialog);
  $("#delete-button").addEventListener("click", confirmDelete);
  $("#confirm-action").addEventListener("click", runConfirm);
  $("#power-button").addEventListener("click", () => toggleMachine(state.selectedId));
  $("#restart-button").addEventListener("click", restartMachine);
  $("#details-button").addEventListener("click", openInspector);
  $("#inspector-button").addEventListener("click", openInspector);
  $("#close-inspector").addEventListener("click", closeInspector);
  $("#inspector-button").hidden = window.innerWidth > 800;
  window.addEventListener("resize", () => {
    $("#inspector-button").hidden = window.innerWidth > 800;
    if (window.innerWidth > 800) closeInspector();
  });
  $("#autostart-toggle").addEventListener("change", (event) =>
    setAutostart(event.target.checked),
  );
  $("#machine-root").addEventListener("change", (event) => {
    if (event.target.checked) $("#machine-system").value = "36";
  });
  $("#configure-button").addEventListener("click", openConfigureDialog);
  $("#configure-form").addEventListener("submit", configureMachine);
  $("#wipe-button").addEventListener("click", resetMachineData);
  $("#screenshot-button").addEventListener("click", downloadScreenshot);
  $("#location-form").addEventListener("submit", (event) => {
    event.preventDefault();
    applyLocation(
      "",
      $("#location-latitude").value,
      $("#location-longitude").value,
    );
  });
  $$("#location-presets button").forEach((button) => {
    button.addEventListener("click", () => {
      const [label, latitude, longitude] = button.dataset.location.split("|");
      $("#location-latitude").value = latitude;
      $("#location-longitude").value = longitude;
      applyLocation(label, latitude, longitude);
    });
  });
  $$("[data-shortcut]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = {
        b: "back",
        h: "home",
        s: "recents",
        up: "volume-up",
        p: "power",
      }[button.dataset.shortcut];
      if (key) sendKey(key);
    });
  });
  $$("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button));
  });
  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  $("#fullscreen-button").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $("#console-stage").requestFullscreen();
    } catch (error) {
      toast("Layar penuh tidak didukung browser ini.", true);
    }
  });
  $("#copy-adb").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const label = $("span", button);
    const copied = await copyText($("#adb-command").textContent.trim());
    if (!copied) {
      toast("Browser menolak akses clipboard. Pilih teks perintah untuk menyalin.", true);
      return;
    }
    button.disabled = true;
    label.textContent = "Tersalin";
    toast("Perintah ADB disalin.");
    setTimeout(() => {
      button.disabled = false;
      label.textContent = "Salin perintah";
    }, 1600);
  });
  $("#screen").addEventListener("pointerdown", () => $("#screen").focus());
  setupUpload();
}

bindEvents();
initialize();
