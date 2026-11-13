// ow-epaper PWA: watch the ESP32 stats display's BLE feed.
// No secrets: this only reads the device's own public service, never the
// Onewheel unlock characteristics.
"use strict";

const LIVE_SERVICE = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0001";
const LIVE_ROW = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0002";
const LIVE_HEADER = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0003";
const LIVE_CMD = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0004";
const LIVE_FILE = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0005";

const $ = (id) => document.getElementById(id);
const connectBtn = $("connect");
const recordBtn = $("record");
const statusEl = $("status");

let device = null;
let header = [];
let recording = false;
let writer = null;   // FileSystemWritableFileStream
let chunkQueue = "";
let rowsCount = 0;
let rowChar = null, cmdChar = null, fileChar = null;

// --- Ride file export over BLE -------------------------------------------------
// Protocol (firmware ow-epaper.ino): write "LIST" or "GET <n>" to LIVE_CMD;
// chunks arrive on LIVE_FILE. Listing is text lines "ride_NNNN.csv:<bytes>"
// ending "END\n". A file is raw CSV bytes ending with a single 0x00 (0x01 =
// missing). Only one transfer at a time; a queue serializes requests.
let fileBuf = [];       // collected bytes of the in-flight transfer
let fileMode = null;    // "list" | "get"
let transferDone = null; // resolve() of the in-flight promise
const requestQueue = [];
let transferring = false;

function onFileChunk(e) {
  const bytes = new Uint8Array(e.target.value.buffer);
  if (!transferring) return;
  // GET terminator: one-byte chunk 0x00 (done) or 0x01 (missing).
  if (fileMode === "get" && bytes.length === 1 && bytes[0] <= 0x01) {
    const data = bytes[0] === 0x00 ? concatBytes(fileBuf) : null;
    fileBuf = [];
    transferring = false;
    const done = transferDone; transferDone = null;
    done(data);
    pumpQueue();
    return;
  }
  fileBuf.push(bytes);
  if (fileMode === "list") {
    const text = new TextDecoder().decode(concatBytes(fileBuf));
    if (text.includes("END")) {
      const lines = text.split("\n").filter((l) => l.includes(".csv"));
      fileBuf = [];
      transferring = false;
      const done = transferDone; transferDone = null;
      done(lines);
      pumpQueue();
    }
  }
}

function concatBytes(parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}

// One BLE transfer at a time.
function requestFile(mode, arg) {
  return new Promise((resolve) => {
    requestQueue.push({ mode, arg, resolve });
    pumpQueue();
  });
}

async function pumpQueue() {
  if (transferring || !cmdChar || !device?.gatt.connected) return;
  const req = requestQueue.shift();
  if (!req) return;
  transferring = true;
  fileMode = req.mode;
  fileBuf = [];
  const timer = setTimeout(() => {   // device without file support: don't hang
    if (transferring) {
      transferring = false; transferDone = null;
      req.resolve(req.mode === "list" ? [] : null);
      pumpQueue();
    }
  }, req.mode === "list" ? 4000 : 30000);
  transferDone = (v) => { clearTimeout(timer); req.resolve(v); };
  try {
    await cmdChar.writeValue(new TextEncoder().encode(
      req.mode === "list" ? "LIST" : `GET ${req.arg}`));
  } catch {
    clearTimeout(timer);
    transferring = false; transferDone = null;
    req.resolve(req.mode === "list" ? [] : null);
    pumpQueue();
  }
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

if (!("bluetooth" in navigator)) {
  setStatus("This browser has no Web Bluetooth. Use Chrome or Edge.", true);
  connectBtn.disabled = true;
}

connectBtn.addEventListener("click", async () => {
  if (device) {
    await device.gatt.disconnect();
    return;
  }
  try {
    device = await navigator.bluetooth.requestDevice({
      filters: [{ name: "ow-epaper" }, { services: [LIVE_SERVICE] }],
      optionalServices: [LIVE_SERVICE],
    });
  } catch (e) {
    setStatus(`No device selected (${e.name}).`, true);
    return;
  }
  device.addEventListener("gattserverdisconnected", onDisconnected);
  await connectGatt();
});

async function connectGatt() {
  connectBtn.textContent = "Connecting…";
  try {
    const server = await device.gatt.connect();
    const svc = await server.getPrimaryService(LIVE_SERVICE);
    const headerChar = await svc.getCharacteristic(LIVE_HEADER);
    rowChar = await svc.getCharacteristic(LIVE_ROW);
    cmdChar = await svc.getCharacteristic(LIVE_CMD);
    fileChar = await svc.getCharacteristic(LIVE_FILE);
    const headBytes = await headerChar.readValue();
    header = new TextDecoder().decode(headBytes).trim().split(",");
    await rowChar.startNotifications();
    rowChar.addEventListener("characteristicvaluechanged", (e) =>
      onRow(new TextDecoder().decode(e.target.value)));
    await fileChar.startNotifications();
    fileChar.addEventListener("characteristicvaluechanged", onFileChunk);
    connectBtn.textContent = "Disconnect";
    recordBtn.disabled = false;
    $("tiles").hidden = false;
    $("meta").hidden = false;
    $("rides").hidden = false;
    setStatus(`Connected to ${device.name}.`);
    refreshRides();
  } catch (e) {
    setStatus(`Connect failed: ${e.message}`, true);
    connectBtn.textContent = "Connect";
  }
}

function onDisconnected() {
  connectBtn.textContent = "Connect";
  recordBtn.disabled = true;
  $("rides").hidden = true;
  requestQueue.length = 0;
  transferring = false;
  stopRecording();
  setStatus("Disconnected. Press Connect to retry.", true);
}

// --- Rides list / CSV download ---------------------------------------------------
let lastListing = [];

async function refreshRides() {
  const listBtn = $("refresh-rides");
  listBtn.disabled = true;
  listBtn.textContent = "…";
  const lines = await requestFile("list");
  lastListing = lines;
  renderRides(lines);
  listBtn.disabled = false;
  listBtn.textContent = "Refresh";
  $("download-all").disabled = lines.length === 0;
}

function renderRides(lines) {
  const ul = $("ride-list");
  ul.innerHTML = "";
  if (!lines.length) {
    const li = document.createElement("li");
    li.textContent = "No rides on the card yet.";
    ul.appendChild(li);
    return;
  }
  for (const line of lines) {
    const [name, bytes] = line.split(":");
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${name}  (${(bytes / 1024).toFixed(1)} KB)`;
    const btn = document.createElement("button");
    btn.className = "small";
    btn.textContent = "CSV";
    btn.addEventListener("click", () => downloadRide(name, btn));
    li.append(label, btn);
    ul.appendChild(li);
  }
}

function saveBlob(filename, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

async function downloadRide(name, btn) {
  const idx = parseInt(name.replace(/\D/g, ""), 10);
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  const data = await requestFile("get", idx);
  if (btn) {
    btn.disabled = false;
    btn.textContent = data ? "CSV" : "✗";
  }
  if (data) saveBlob(name, new Blob([data], { type: "text/csv" }));
  else setStatus(`Could not read ${name}.`, true);
}

$("download-all").addEventListener("click", async () => {
  const btn = $("download-all");
  btn.disabled = true;
  for (const line of lastListing) {
    btn.textContent = "…";
    const name = line.split(":")[0];
    const idx = parseInt(name.replace(/\D/g, ""), 10);
    const data = await requestFile("get", idx);
    if (data) saveBlob(name, new Blob([data], { type: "text/csv" }));
    await new Promise((r) => setTimeout(r, 400));  // let downloads register
  }
  btn.textContent = "Download all";
  btn.disabled = false;
});

$("refresh-rides").addEventListener("click", refreshRides);

// --- Install as app ----------------------------------------------------------------
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("install").hidden = false;
});
$("install").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("install").hidden = true;
});

function parseRow(text) {
  const cells = text.trim().split(",");
  const row = {};
  header.forEach((name, i) => (row[name] = cells[i] ?? ""));
  return row;
}

function show(name, value, suffix = "") {
  const el = $(name);
  if (el) el.textContent = value === "" || value == null ? "--" : value + suffix;
}

function onRow(text) {
  const r = parseRow(text);
  show("mph", r.mph);
  show("batt_pct", r.batt_pct, "%");
  show("pitch_deg", r.pitch_deg);
  show("ctrl_c", r.ctrl_c);
  show("motor_c", r.motor_c);
  show("batt_c", r.batt_c);
  show("amps", r.amps);
  show("link", r.link);
  show("notify_per_s", r.notify_per_s, "/s");
  show("recording", r.recording === "1" ? "yes (SD)" : "no");
  show("esp_mv", r.esp_mv && (r.esp_mv / 1000).toFixed(2) + " V");
  show("data_age_ms", r.data_age_ms && (r.data_age_ms / 1000).toFixed(1) + " s");
  if (recording) {
    chunkQueue += `${Date.now() / 1000},${text}`;
    rowsCount++;
    show("rows", rowsCount);
  }
}

recordBtn.addEventListener("click", async () => {
  if (recording) { stopRecording(); return; }
  const name = `ow-epaper-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`;
  try {
    const handle = await showSaveFilePicker({
      suggestedName: name,
      types: [{ description: "CSV", accept: { "text/csv": [".csv"] } }],
    });
    writer = await handle.createWritable();
    await writer.write("pc_time_s," + header.join(",") + "\n");
    recording = true;
    recordBtn.textContent = "Stop";
    pump();
  } catch (e) {
    if (e.name !== "AbortError") setStatus(`Record failed: ${e.message}`, true);
  }
});

async function pump() {
  while (recording) {
    if (chunkQueue) {
      try { await writer.write(chunkQueue); } catch { stopRecording(); break; }
      chunkQueue = "";
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

async function stopRecording() {
  recording = false;
  recordBtn.textContent = "Record";
  if (writer) {
    try {
      if (chunkQueue) await writer.write(chunkQueue);
      await writer.close();
    } catch { /* file already closed */ }
    writer = null;
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
