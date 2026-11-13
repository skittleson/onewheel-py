// ow-epaper PWA: watch the ESP32 stats display's BLE feed.
// No secrets: this only reads the device's own public service, never the
// Onewheel unlock characteristics.
"use strict";

const LIVE_SERVICE = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0001";
const LIVE_ROW = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0002";
const LIVE_HEADER = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0003";

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
    const rowChar = await svc.getCharacteristic(LIVE_ROW);
    const headBytes = await headerChar.readValue();
    header = new TextDecoder().decode(headBytes).trim().split(",");
    await rowChar.startNotifications();
    rowChar.addEventListener("characteristicvaluechanged", (e) =>
      onRow(new TextDecoder().decode(e.target.value)));
    connectBtn.textContent = "Disconnect";
    recordBtn.disabled = false;
    $("tiles").hidden = false;
    $("meta").hidden = false;
    setStatus(`Connected to ${device.name}.`);
  } catch (e) {
    setStatus(`Connect failed: ${e.message}`, true);
    connectBtn.textContent = "Connect";
  }
}

function onDisconnected() {
  connectBtn.textContent = "Connect";
  recordBtn.disabled = true;
  stopRecording();
  setStatus("Disconnected. Press Connect to retry.", true);
}

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
