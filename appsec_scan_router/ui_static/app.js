const form = document.querySelector("#scanForm");
const startScanButton = document.querySelector("#startScan");
const stopScanButton = document.querySelector("#stopScan");
const refreshButton = document.querySelector("#refreshScans");
const resetDefaultsButton = document.querySelector("#resetDefaults");
const toggleTokenButton = document.querySelector("#toggleToken");
const copyCommandButton = document.querySelector("#copyCommand");
const clearLogsButton = document.querySelector("#clearLogs");
const downloadLogsButton = document.querySelector("#downloadLogs");
const activeStatus = document.querySelector("#activeStatus");
const activeTitle = document.querySelector("#activeTitle");
const detectedCount = document.querySelector("#detectedCount");
const progressValue = document.querySelector("#progressValue");
const etaValue = document.querySelector("#etaValue");
const runtimeValue = document.querySelector("#runtimeValue");
const commandLine = document.querySelector("#commandLine");
const runList = document.querySelector("#runList");
const reportList = document.querySelector("#reportList");
const reportCount = document.querySelector("#reportCount");
const scanCount = document.querySelector("#scanCount");
const logsElement = document.querySelector("#logs");
const toast = document.querySelector("#toast");
const formStatus = document.querySelector("#formStatus");

const state = {
  scans: [],
  activeScan: null,
  eventSource: null,
  logs: [],
  timer: null,
};

const defaultValues = {
  outPrefix: "appsec_inventory_service",
  minConfidence: "medium",
  activityMode: "latest",
  branchAgeDays: "90",
  maxWorkers: "8",
  branchWorkers: "16",
  contentWorkers: "16",
  maxCommitsPerRepo: "0",
  timeout: "30",
  storeCountry: "US",
  storeTimeout: "15",
  storeLookup: false,
  verbose: false,
};

const persistedFields = [
  "provider",
  "org",
  "project",
  "repo",
  "baseUrl",
  "outPrefix",
  "minConfidence",
  "activityMode",
  "branchAgeDays",
  "maxWorkers",
  "branchWorkers",
  "contentWorkers",
  "maxCommitsPerRepo",
  "timeout",
  "storeLookup",
  "storeCountry",
  "storeTimeout",
  "verbose",
];

document.addEventListener("DOMContentLoaded", async () => {
  loadForm();
  syncProviderFields();
  bindEvents();
  await loadScans();
  state.timer = window.setInterval(tick, 1000);
});

function bindEvents() {
  form.addEventListener("change", () => {
    syncProviderFields();
    saveForm();
  });
  form.addEventListener("input", saveForm);
  startScanButton.addEventListener("click", startScan);
  stopScanButton.addEventListener("click", stopScan);
  refreshButton.addEventListener("click", loadScans);
  resetDefaultsButton.addEventListener("click", resetDefaults);
  toggleTokenButton.addEventListener("click", toggleToken);
  copyCommandButton.addEventListener("click", copyCommand);
  clearLogsButton.addEventListener("click", () => {
    state.logs = [];
    renderLogs();
  });
  downloadLogsButton.addEventListener("click", downloadLogs);
}

async function startScan() {
  if (!form.reportValidity()) {
    return;
  }
  const payload = formPayload();
  setBusy(true);
  try {
    const response = await fetch("/api/scans", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Scan could not be started.");
    }
    state.activeScan = data.scan;
    state.logs = data.scan.logsTail || [];
    await loadScans(data.scan.id);
    listenToScan(data.scan.id);
    notify("Scan started.");
  } catch (error) {
    notify(error.message || "Scan could not be started.");
  } finally {
    setBusy(false);
  }
}

async function stopScan() {
  if (!state.activeScan) {
    return;
  }
  try {
    const response = await fetch(`/api/scans/${state.activeScan.id}/stop`, {method: "POST"});
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Stop failed.");
    }
    state.activeScan = data.scan;
    renderActiveScan();
    notify("Stop requested.");
  } catch (error) {
    notify(error.message || "Stop failed.");
  }
}

async function loadScans(preferredId = "") {
  try {
    const response = await fetch("/api/scans");
    const data = await response.json();
    state.scans = data.scans || [];
    if (preferredId) {
      const selected = state.scans.find((scan) => scan.id === preferredId);
      if (selected) {
        selectScan(selected, false);
      }
    } else if (!state.activeScan && state.scans.length) {
      selectScan(state.scans[0], false);
    } else if (state.activeScan) {
      const refreshed = state.scans.find((scan) => scan.id === state.activeScan.id);
      if (refreshed) {
        state.activeScan = refreshed;
      }
    }
    renderAll();
  } catch (error) {
    notify(error.message || "Could not refresh scans.");
  }
}

async function selectScan(scan, connect = true) {
  try {
    const response = await fetch(`/api/scans/${scan.id}`);
    const data = await response.json();
    state.activeScan = data.scan || scan;
    state.logs = state.activeScan.logsTail || [];
    renderAll();
    if (connect && state.activeScan.status === "running") {
      listenToScan(state.activeScan.id);
    } else if (connect) {
      closeEventSource();
    }
  } catch (error) {
    notify(error.message || "Could not open scan.");
  }
}

function listenToScan(scanId) {
  closeEventSource();
  const source = new EventSource(`/api/scans/${scanId}/events`);
  state.eventSource = source;
  source.addEventListener("status", (event) => {
    state.activeScan = JSON.parse(event.data);
    mergeScan(state.activeScan);
    renderAll();
  });
  source.addEventListener("log", (event) => {
    const data = JSON.parse(event.data);
    if (data.line) {
      state.logs.push(data.line);
      if (state.logs.length > 1200) {
        state.logs = state.logs.slice(-1200);
      }
      renderLogs();
    }
  });
  source.addEventListener("done", async (event) => {
    state.activeScan = JSON.parse(event.data);
    mergeScan(state.activeScan);
    closeEventSource();
    await loadScans(state.activeScan.id);
    notify(`Scan ${state.activeScan.status}.`);
  });
}

function closeEventSource() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function mergeScan(scan) {
  const index = state.scans.findIndex((candidate) => candidate.id === scan.id);
  if (index >= 0) {
    state.scans.splice(index, 1, scan);
  } else {
    state.scans.unshift(scan);
  }
}

function renderAll() {
  renderActiveScan();
  renderRuns();
  renderReports();
  renderLogs();
}

function renderActiveScan() {
  const scan = state.activeScan;
  if (!scan) {
    activeStatus.textContent = "Idle";
    activeStatus.className = "";
    activeTitle.textContent = "No scan selected";
    detectedCount.textContent = "0";
    progressValue.textContent = "0%";
    etaValue.textContent = "Not started";
    runtimeValue.textContent = "0s";
    commandLine.textContent = "appsec-inventory-service";
    copyCommandButton.disabled = true;
    stopScanButton.disabled = true;
    downloadLogsButton.disabled = true;
    return;
  }
  activeStatus.textContent = capitalize(scan.status);
  activeStatus.className = `status-${scan.status}`;
  activeTitle.textContent = `${scan.org || "Unknown"} · ${scan.target || "all"} · ${providerLabel(scan.provider)}`;
  detectedCount.textContent = String(scan.detectedCount || 0);
  progressValue.textContent = scanProgress(scan);
  etaValue.textContent = scanEta(scan);
  runtimeValue.textContent = scanRuntime(scan);
  commandLine.textContent = scan.command || "appsec-inventory-service";
  copyCommandButton.disabled = !scan.command;
  stopScanButton.disabled = scan.status !== "running";
  downloadLogsButton.disabled = state.logs.length === 0;
}

function renderRuns() {
  scanCount.textContent = String(state.scans.length);
  if (!state.scans.length) {
    runList.innerHTML = '<div class="empty-state">No runs</div>';
    return;
  }
  runList.innerHTML = state.scans.map((scan) => {
    const active = state.activeScan && state.activeScan.id === scan.id ? " active" : "";
    return `
      <button class="run-item${active}" type="button" data-scan-id="${escapeHtml(scan.id)}">
        <span class="run-main">
          <strong>${escapeHtml(scan.org || "Unknown")} · ${escapeHtml(scan.target || "all")}</strong>
          <span class="status-chip status-${escapeHtml(scan.status)}">${escapeHtml(capitalize(scan.status))}</span>
        </span>
        <span class="run-meta">
          <span>${escapeHtml(providerLabel(scan.provider))}</span>
          <span>${escapeHtml(formatDate(scan.startedAt))}</span>
        </span>
      </button>
    `;
  }).join("");
  runList.querySelectorAll("[data-scan-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const selected = state.scans.find((scan) => scan.id === button.dataset.scanId);
      if (selected) {
        selectScan(selected);
      }
    });
  });
}

function renderReports() {
  const reports = state.activeScan ? state.activeScan.reports || [] : [];
  reportCount.textContent = String(reports.length);
  if (!reports.length) {
    reportList.innerHTML = '<div class="empty-state">No reports</div>';
    return;
  }
  reportList.innerHTML = reports.map((report) => `
    <a class="report-item" href="${escapeHtml(report.url)}">
      <strong>${escapeHtml(report.name)}</strong>
      <span>${escapeHtml(formatBytes(report.size))}</span>
    </a>
  `).join("");
}

function renderLogs() {
  logsElement.textContent = state.logs.join("\n");
  logsElement.scrollTop = logsElement.scrollHeight;
  downloadLogsButton.disabled = state.logs.length === 0;
}

function formPayload() {
  const data = new FormData(form);
  const provider = data.get("provider") || "azure-devops";
  return {
    provider,
    org: value(data, "org"),
    project: provider === "azure-devops" ? value(data, "project") : "",
    repo: provider === "github-enterprise" ? value(data, "repo") : "",
    baseUrl: provider === "github-enterprise" ? value(data, "baseUrl") : "",
    token: value(data, "token"),
    outPrefix: value(data, "outPrefix") || defaultValues.outPrefix,
    minConfidence: value(data, "minConfidence") || defaultValues.minConfidence,
    activityMode: value(data, "activityMode") || defaultValues.activityMode,
    branchAgeDays: numberValue(data, "branchAgeDays", Number(defaultValues.branchAgeDays)),
    maxWorkers: numberValue(data, "maxWorkers", Number(defaultValues.maxWorkers)),
    branchWorkers: numberValue(data, "branchWorkers", Number(defaultValues.branchWorkers)),
    contentWorkers: numberValue(data, "contentWorkers", Number(defaultValues.contentWorkers)),
    maxCommitsPerRepo: numberValue(data, "maxCommitsPerRepo", Number(defaultValues.maxCommitsPerRepo)),
    timeout: numberValue(data, "timeout", Number(defaultValues.timeout)),
    storeLookup: data.has("storeLookup"),
    storeCountry: value(data, "storeCountry") || defaultValues.storeCountry,
    storeTimeout: numberValue(data, "storeTimeout", Number(defaultValues.storeTimeout)),
    verbose: data.has("verbose"),
  };
}

function syncProviderFields() {
  const provider = new FormData(form).get("provider") || "azure-devops";
  document.querySelectorAll(".provider-azure").forEach((node) => {
    node.classList.toggle("hidden", provider !== "azure-devops");
  });
  document.querySelectorAll(".provider-github").forEach((node) => {
    node.classList.toggle("hidden", provider !== "github-enterprise");
  });
  form.querySelector('[name="baseUrl"]').required = provider === "github-enterprise";
  form.querySelector('[name="project"]').required = false;
  form.querySelector('[name="repo"]').required = false;
}

function loadForm() {
  const saved = JSON.parse(localStorage.getItem("appsec-inventory-service-ui") || "{}");
  applyDefaultValues();
  for (const name of persistedFields) {
    if (!(name in saved)) {
      continue;
    }
    const element = form.elements[name];
    if (!element) {
      continue;
    }
    if (element instanceof RadioNodeList) {
      element.value = saved[name];
    } else if (element.type === "checkbox") {
      element.checked = Boolean(saved[name]);
    } else {
      element.value = saved[name];
    }
  }
}

function saveForm() {
  const data = new FormData(form);
  const saved = {};
  for (const name of persistedFields) {
    if (name === "provider") {
      saved[name] = data.get(name);
    } else if (name === "storeLookup" || name === "verbose") {
      saved[name] = data.has(name);
    } else {
      saved[name] = value(data, name);
    }
  }
  localStorage.setItem("appsec-inventory-service-ui", JSON.stringify(saved));
}

function resetDefaults() {
  applyDefaultValues();
  syncProviderFields();
  saveForm();
  notify("Scan defaults restored.");
}

function applyDefaultValues() {
  for (const [name, defaultValue] of Object.entries(defaultValues)) {
    const element = form.elements[name];
    if (!element) {
      continue;
    }
    if (element.type === "checkbox") {
      element.checked = Boolean(defaultValue);
    } else {
      element.value = defaultValue;
    }
  }
}

function toggleToken() {
  const token = form.querySelector('[name="token"]');
  const showing = token.type === "text";
  token.type = showing ? "password" : "text";
  toggleTokenButton.textContent = showing ? "Show" : "Hide";
}

async function copyCommand() {
  if (!state.activeScan || !state.activeScan.command) {
    return;
  }
  await navigator.clipboard.writeText(state.activeScan.command);
  notify("Command copied.");
}

function downloadLogs() {
  if (!state.logs.length) {
    return;
  }
  const blob = new Blob([state.logs.join("\n")], {type: "text/plain"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${state.activeScan ? state.activeScan.id : "scan"}-logs.txt`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function setBusy(isBusy) {
  startScanButton.disabled = isBusy;
  formStatus.textContent = isBusy ? "Starting" : "Ready";
  formStatus.className = `status-chip ${isBusy ? "status-running" : "idle"}`;
}

function tick() {
  if (state.activeScan) {
    runtimeValue.textContent = scanRuntime(state.activeScan);
  }
  const running = state.scans.some((scan) => scan.status === "running");
  if (running) {
    loadScans();
  }
}

function scanRuntime(scan) {
  if (!scan.startedAt) {
    return "0s";
  }
  const start = Date.parse(scan.startedAt);
  const end = scan.endedAt ? Date.parse(scan.endedAt) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return "0s";
  }
  const total = Math.max(0, Math.floor((end - start) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function scanProgress(scan) {
  const progress = scan.progress || {};
  if (scan.status === "succeeded") {
    return "100%";
  }
  const percent = Number(progress.percent || 0);
  if (!Number.isFinite(percent) || percent <= 0) {
    return scan.status === "running" ? "Starting" : "0%";
  }
  return `${Math.max(0, Math.min(100, Math.round(percent)))}%`;
}

function scanEta(scan) {
  if (scan.status === "succeeded") {
    return "Done";
  }
  if (scan.status === "failed") {
    return "Stopped";
  }
  if (scan.status === "stopped") {
    return "Stopped";
  }
  if (scan.status !== "running") {
    return "Not started";
  }
  const seconds = Number((scan.progress || {}).etaSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "Estimating";
  }
  return `About ${durationText(seconds)}`;
}

function durationText(totalSeconds) {
  const total = Math.max(1, Math.round(totalSeconds));
  if (total < 60) {
    return `${total}s`;
  }
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes < 60) {
    return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function providerLabel(provider) {
  return provider === "github-enterprise" ? "GitHub Enterprise" : "Azure DevOps";
}

function value(data, name) {
  return String(data.get(name) || "").trim();
}

function numberValue(data, name, fallback) {
  const number = Number(value(data, name));
  return Number.isFinite(number) ? number : fallback;
}

function capitalize(text) {
  const value = String(text || "");
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function notify(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(notify.timeout);
  notify.timeout = window.setTimeout(() => toast.classList.remove("visible"), 2400);
}
