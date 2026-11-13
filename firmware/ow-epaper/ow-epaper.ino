// Onewheel live stats on a Waveshare ESP32-S3-ePaper-1.54 (V2).
//
// The ESP32 connects to the board over BLE itself (no phone/laptop), replays
// the Pint unlock sequence from src/live_monitor.py, and shows speed, battery,
// pitch, temps and state on the 200x200 e-paper.
//
// Build/flash: make -C firmware/ow-epaper flash   (see README.md there)

#include <Arduino.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSansBold24pt7b.h>
#include <NimBLEDevice.h>
#include <LittleFS.h>
#include <SPI.h>

#include "ow_protocol.h"
#include "secrets.h"
#include "diag.h"
#include "sd_logger.h"
#include "ui.h"

// --- Board pins (Waveshare ESP32-S3-ePaper-1.54, V1 and V2 identical) -------
constexpr int PIN_EPD_BUSY = 8;
constexpr int PIN_EPD_RST  = 9;
constexpr int PIN_EPD_DC   = 10;
constexpr int PIN_EPD_CS   = 11;
constexpr int PIN_EPD_SCK  = 12;
constexpr int PIN_EPD_MOSI = 13;
constexpr int PIN_EPD_PWR  = 6;   // LOW = panel powered
constexpr int PIN_VBAT_PWR = 17;  // HIGH = stay on when running from LiPo
constexpr int PIN_BOOT_BTN = 0;   // click = next page, hold 1 s = new ride
constexpr int PIN_PWR_BTN  = 18;  // hold 2 s = power off (deep sleep on USB)
constexpr int PIN_VBAT_ADC = 4;   // ADC1_CH3, LiPo through a 2:1 divider

// --- Timing ------------------------------------------------------------------
constexpr uint32_t KEEPALIVE_MS     = 200;   // f30b write, same as live_monitor.py
constexpr uint32_t POLL_MS          = 5000;  // battery/voltage/temps aren't notified on the Pint S
constexpr uint32_t STALE_MS         = 5000;  // no notifications for this long → "NO DATA"
constexpr uint32_t SCAN_SECONDS     = 5;
constexpr uint32_t MIN_REFRESH_MS   = 1000;
constexpr int      PARTIALS_PER_FULL = 30;
constexpr uint32_t DEVICE_BATT_MS   = 30000;
constexpr uint32_t LIVE_FEED_MS     = 500;   // laptop BLE feed (tools: src/epaper_monitor.py)

GxEPD2_BW<GxEPD2_154_D67, GxEPD2_154_D67::HEIGHT> display(
    GxEPD2_154_D67(PIN_EPD_CS, PIN_EPD_DC, PIN_EPD_RST, PIN_EPD_BUSY));

// --- Shared state (BLE task writes, display loop reads) ----------------------
static portMUX_TYPE stateLock = portMUX_INITIALIZER_UNLOCKED;
static ow::Telemetry telemetry;
static volatile ow::Link linkState = ow::Link::Scanning;
static volatile uint32_t lastDataMs = 0;
static volatile uint32_t notifyCount = 0;
static volatile int deviceMilliVolts = -1;

static void applyLocked(uint16_t id, const uint8_t* data, size_t len) {
  taskENTER_CRITICAL(&stateLock);
  bool used = ow::apply(telemetry, id, data, len);
  taskEXIT_CRITICAL(&stateLock);
  if (used) lastDataMs = millis();
}

static ow::Telemetry snapshot() {
  taskENTER_CRITICAL(&stateLock);
  ow::Telemetry t = telemetry;
  taskEXIT_CRITICAL(&stateLock);
  return t;
}

// --- BLE ---------------------------------------------------------------------
static NimBLEUUID uuid(uint16_t id) {
  char s[37];
  ow::uuidFor(id, s);
  return NimBLEUUID(s);
}

static void onNotify(NimBLERemoteCharacteristic* ch, uint8_t* data, size_t len, bool) {
  uint16_t id = 0;
  std::string s = ch->getUUID().toString();  // "e659f307-…"
  if (s.size() >= 8) id = uint16_t(strtoul(s.substr(4, 4).c_str(), nullptr, 16));
  notifyCount++;
  lastDataMs = millis();  // any notification (incl. the raw f3fe stream) proves the link is alive
  diag::countNotify(id);
  applyLocked(id, data, len);
}

static const NimBLEAdvertisedDevice* findBoard() {
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setActiveScan(true);
  NimBLEScanResults results = scan->getResults(SCAN_SECONDS * 1000, false);
  NimBLEAddress want(std::string(OW_ADDRESS), BLE_ADDR_PUBLIC);
  for (int i = 0; i < results.getCount(); i++) {
    const NimBLEAdvertisedDevice* d = results.getDevice(i);
    if (d->getAddress().toString() == want.toString()) return d;
  }
  Serial.printf("[ble] scan: %d devices, board not seen\n", results.getCount());
  return nullptr;
}

static bool readInto(NimBLERemoteService* svc, uint16_t id) {
  NimBLERemoteCharacteristic* ch = svc->getCharacteristic(uuid(id));
  if (!ch || !ch->canRead()) return false;
  DIAG_BLE_OP("read", id);
  NimBLEAttValue v = ch->readValue();
  DIAG_BLE_DONE();
  applyLocked(id, v.data(), v.size());
  return true;
}

static bool write(NimBLERemoteService* svc, uint16_t id, const uint8_t* data, size_t len) {
  NimBLERemoteCharacteristic* ch = svc->getCharacteristic(uuid(id));
  if (!ch) return false;
  DIAG_BLE_OP("write", id);
  bool ok = ch->writeValue(data, len, true);
  DIAG_BLE_DONE();
  return ok;
}

// Same sequence as live_monitor.unlock_pint().
static bool unlockPint(NimBLERemoteService* svc) {
  for (uint16_t id : {ow::RIDING_MODE, ow::HARDWARE_REVISION, ow::FIRMWARE_REVISION}) {
    NimBLERemoteCharacteristic* ch = svc->getCharacteristic(uuid(id));
    if (ch) Serial.printf("[ble]   prime %04x -> %u bytes\n", id, unsigned(ch->readValue().size()));
  }
  Serial.println("[ble]   writing token");
  static const uint8_t enable[] = {0x00, 0x01};
  if (!write(svc, ow::UART_SERIAL_WRITE, OW_UNLOCK_MAGIC, OW_UNLOCK_MAGIC_LEN)) return false;
  // On a cold boot the ESP32 can reach the Pint before its BLE service fully
  // accepts writes; re-sending the token usually makes the headroom write
  // stick instead of dropping the whole session.
  for (int attempt = 0; attempt < 5; attempt++) {
    if (write(svc, ow::SAFETY_HEADROOM, enable, sizeof enable)) return true;
    Serial.printf("[ble]   headroom write failed, retrying token (%d)\n", attempt + 1);
    delay(250);
    if (!write(svc, ow::UART_SERIAL_WRITE, OW_UNLOCK_MAGIC, OW_UNLOCK_MAGIC_LEN)) return false;
    delay(250);
  }
  return false;
}

static void runSession(NimBLEClient* client) {
  Serial.printf("[ble] connected, mtu=%u; discovering\n", client->getMTU());
  NimBLERemoteService* svc = client->getService(NimBLEUUID(ow::SERVICE_UUID));
  if (!svc) {
    Serial.println("[ble] OW service not found");
    return;
  }
  linkState = ow::Link::Unlocking;
  if (!unlockPint(svc)) {
    Serial.println("[ble] unlock writes failed");
    return;
  }
  for (NimBLERemoteCharacteristic* ch : svc->getCharacteristics(true)) {
    if (ch->canNotify()) ch->subscribe(true, onNotify);
  }
  Serial.println("[ble] unlocked + subscribed");
  lastDataMs = millis();

  // Keepalive goes out every 200 ms; resolve the characteristic once instead
  // of rebuilding the UUID string on every write.
  NimBLERemoteCharacteristic* keepalive = svc->getCharacteristic(uuid(ow::RPM));
  if (!keepalive) {
    Serial.println("[ble] keepalive characteristic not found");
    return;
  }
  static const uint8_t zero[] = {0x00};
  uint32_t lastPoll = 0;
  while (client->isConnected()) {
    DIAG_BLE_OP("write", ow::RPM);
    bool ok = keepalive->writeValue(zero, sizeof zero, true);
    DIAG_BLE_DONE();
    if (!ok) break;
    uint32_t now = millis();
    if (now - lastPoll >= POLL_MS) {
      lastPoll = now;
      ow::Telemetry t = snapshot();
      Serial.printf("[ble] notifies=%lu rpm=%d batt=%d%% V=%.1f pitch=%.1f temps=%d/%d state=%d\n",
                    (unsigned long)notifyCount, t.rpm, t.batteryPct, t.voltage, t.pitch,
                    t.controllerTemp, t.motorTemp, t.state);
      for (uint16_t id : {ow::BATTERY_REMAINING, ow::BATTERY_TEMP, ow::BATTERY_VOLTAGE,
                          ow::TEMPERATURE, ow::STATUS_ERROR}) {
        readInto(svc, id);
      }
    }
    linkState = (millis() - lastDataMs > STALE_MS) ? ow::Link::Stale : ow::Link::Live;
    diag::bleBeat = millis();
    vTaskDelay(pdMS_TO_TICKS(KEEPALIVE_MS));
  }
}

// --- Live feed: ESP32 as a BLE peripheral so a laptop can watch it ------------
static NimBLECharacteristic* liveRowChar = nullptr;
static SemaphoreHandle_t liveFeedLock = nullptr;

static void startLiveFeed() {
  liveFeedLock = xSemaphoreCreateMutex();
  NimBLEServer* server = NimBLEDevice::createServer();
  server->advertiseOnDisconnect(true);
  NimBLEService* svc = server->createService(ridelog::LIVE_SERVICE_UUID);
  NimBLECharacteristic* header =
      svc->createCharacteristic(ridelog::LIVE_HEADER_UUID, NIMBLE_PROPERTY::READ);
  header->setValue(ridelog::LIVE_HEADER);
  liveRowChar = svc->createCharacteristic(ridelog::LIVE_ROW_UUID,
                                          NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  svc->start();
  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->setName("ow-epaper");
  adv->addServiceUUID(ridelog::LIVE_SERVICE_UUID);
  adv->start();
  Serial.println("[feed] advertising as ow-epaper");
}

static volatile uint32_t notifyRate = 0;

static void publishLiveFeed() {
  static uint32_t lastMs = 0, lastCount = 0, rate = 0;
  uint32_t now = millis();
  if (now - lastMs < LIVE_FEED_MS) return;
  uint32_t count = notifyCount;
  rate = (count - lastCount) * 1000 / max<uint32_t>(1, now - lastMs);
  notifyRate = rate;
  lastCount = count;
  lastMs = now;
  char row[ridelog::LIVE_ROW_MAX];
  ridelog::formatLive(row, sizeof row, linkState, now - lastDataMs, rate,
                      sdlog::currentIndex >= 0, now, snapshot(), deviceMilliVolts);
  // The NimBLE host task may still be sending the previous notify.
  if (liveFeedLock && xSemaphoreTake(liveFeedLock, pdMS_TO_TICKS(100))) {
    liveRowChar->setValue(reinterpret_cast<const uint8_t*>(row), strlen(row));
    liveRowChar->notify();
    xSemaphoreGive(liveFeedLock);
  }
}

static void bleTask(void*) {
  NimBLEClient* client = NimBLEDevice::createClient();
  client->setConnectTimeout(10 * 1000);
  // Back off the scan while the board is absent (it streams for a while after
  // power-off, so the board usually shows up on the first scan when riding).
  // Full-rate scan for a few minutes, then slow to one scan per minute.
  uint32_t idleSince = 0;
  for (;;) {
    linkState = ow::Link::Scanning;
    const NimBLEAdvertisedDevice* dev = findBoard();
    if (dev) {
      idleSince = 0;
      linkState = ow::Link::Connecting;
      Serial.printf("[ble] connecting to %s\n", dev->getAddress().toString().c_str());
      if (!client->connect(dev)) {
        Serial.printf("[ble] connect failed (err %d)\n", client->getLastError());
      } else {
        runSession(client);
        client->disconnect();
        Serial.println("[ble] disconnected");
      }
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    uint32_t now = millis();
    if (!idleSince) idleSince = now;
    uint32_t idle = now - idleSince;
    uint32_t waitMs = idle < 60000    ? 1000
                     : idle < 300000  ? 10000
                                      : 60000;
    vTaskDelay(pdMS_TO_TICKS(waitMs));
  }
}

// --- Display -------------------------------------------------------------------
static void centered(const char* text, const GFXfont* font, int16_t cx, int16_t baseline) {
  int16_t x1, y1;
  uint16_t w, h;
  display.setFont(font);
  display.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(cx - int16_t(w) / 2 - x1, baseline);
  display.print(text);
}

static void batteryGauge(int16_t x, int16_t y, int pct) {
  constexpr int16_t W = 44, H = 20;
  display.drawRect(x, y, W, H, GxEPD_BLACK);
  display.fillRect(x + W, y + 6, 3, H - 12, GxEPD_BLACK);
  if (pct > 0) display.fillRect(x + 2, y + 2, (W - 4) * min(pct, 100) / 100, H - 4, GxEPD_BLACK);
}

static void cell(const char* label, const char* value, int16_t cx, int16_t y) {
  display.setFont(nullptr);  // built-in 6x8 font for labels
  int16_t w = int16_t(strlen(label)) * 6;
  display.setCursor(cx - w / 2, y);
  display.print(label);
  centered(value, &FreeSansBold12pt7b, cx, y + 30);
}

static void drawRight(const char* text, int16_t right, int16_t y) {
  display.setFont(nullptr);
  display.setCursor(right - int16_t(strlen(text)) * 6, y);
  display.print(text);
}

static void draw(const ow::Screen& s) {
  display.fillScreen(GxEPD_WHITE);
  display.setTextColor(GxEPD_BLACK);

  // Header: status + battery
  display.setFont(&FreeSansBold9pt7b);
  display.setCursor(4, 20);
  display.print(s.status);
  batteryGauge(150, 4, s.batteryPct);
  display.drawFastHLine(0, 30, 200, GxEPD_BLACK);

  // Speed
  centered(s.speed, &FreeSansBold24pt7b, 100, 84);
  centered("MPH", &FreeSansBold9pt7b, 100, 106);
  display.drawFastHLine(0, 114, 200, GxEPD_BLACK);
  display.drawFastVLine(100, 114, 86, GxEPD_BLACK);
  display.drawFastHLine(0, 157, 200, GxEPD_BLACK);

  cell("BATTERY", s.battery, 50, 120);
  cell("BATT TEMP", s.battTemp, 150, 120);
  cell("PITCH", s.pitch, 50, 163);
  cell("CTRL/MOTOR", s.temps, 150, 163);

  display.setFont(nullptr);
  display.setCursor(4, 104);
  display.print(s.device);
  drawRight(s.indicator, 196, 104);
}

static int readDeviceMilliVolts() {
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) sum += analogReadMilliVolts(PIN_VBAT_ADC);
  return int(sum / 16 * 2);
}

// Label/value rows for the TRIP and STATUS pages: "TOP    9.9 mph" → label left, value right.
static void drawLines(const char* title, const ow::Screen& s, const ui::Lines& lines) {
  display.fillScreen(GxEPD_WHITE);
  display.setTextColor(GxEPD_BLACK);
  display.setFont(&FreeSansBold9pt7b);
  display.setCursor(4, 20);
  display.print(title);
  drawRight(s.indicator, 196, 14);
  display.drawFastHLine(0, 30, 200, GxEPD_BLACK);
  for (int i = 0; i < 6; i++) {
    const char* line = lines.line[i];
    if (!*line) continue;
    char label[12] = {};
    const char* sp = strchr(line, ' ');
    size_t n = sp ? min<size_t>(size_t(sp - line), sizeof label - 1) : 0;
    memcpy(label, line, n);
    const char* value = sp ? sp : line;
    while (*value == ' ') value++;
    int16_t y = 56 + i * 28;
    display.setFont(nullptr);
    display.setCursor(4, y - 8);
    display.print(label);
    int16_t x1, y1;
    uint16_t w, h;
    display.setFont(&FreeSansBold12pt7b);
    display.getTextBounds(value, 0, 0, &x1, &y1, &w, &h);
    display.setCursor(196 - int16_t(w) - x1, y);
    display.print(value);
  }
}

static void drawFrame(const ui::Frame& f) {
  switch (f.page) {
    case ui::Page::Ride:   draw(f.screen); break;
    case ui::Page::Trip:   drawLines("TRIP", f.screen, f.lines); break;
    case ui::Page::Status: drawLines("STATUS", f.screen, f.lines); break;
  }
}

static void render(const ui::Frame& f, ow::Refresh mode) {
  diag::dispOpStart = millis();
  diag::dispOp = mode == ow::Refresh::Full ? "full" : "partial";
  if (mode == ow::Refresh::Full) display.setFullWindow();
  else display.setPartialWindow(0, 0, display.width(), display.height());
  display.firstPage();
  do {
    drawFrame(f);
  } while (display.nextPage());
  diag::lastRenderMs = millis() - diag::dispOpStart;
  (mode == ow::Refresh::Full ? diag::fullRenders : diag::partialRenders)++;
  diag::dispOp = "idle";
}

// Serial commands: d = dump diag log, c = clear it, l = list rides, g<N> = print ride N.
static void pollSerial() {
  static char cmd[16];
  static size_t len = 0;
  while (Serial.available()) {
    char c = char(Serial.read());
    if (len == 0 && c == 'd') { diag::dump(); continue; }
    if (len == 0 && c == 'c') { diag::clear(); continue; }
    if (len == 0 && c == 'l') { sdlog::list(); continue; }
    if (c == '\n' || c == '\r') {
      if (len > 1 && cmd[0] == 'g') sdlog::get(atoi(cmd + 1));
      len = 0;
      continue;
    }
    if (len < sizeof cmd - 1) { cmd[len++] = c; cmd[len] = '\0'; }
  }
}

static ow::RefreshPolicy policy(MIN_REFRESH_MS, PARTIALS_PER_FULL);
static ui::Frame shown;
static ui::Page page = ui::Page::Ride;
static ui::Trip trip;
static ui::Button bootBtn(1000);
static ui::Button pwrBtn(2000);

// Close the ride file, leave a summary on the e-paper (it keeps the image with
// no power), then cut the battery latch. On USB the latch can't cut power, so
// fall back to deep sleep; press PWR to wake (= reboot).
static void powerOff() {
  Serial.println("[pwr] powering off");
  sdlog::stop();
  ui::Lines l = ui::tripLines(trip, millis());
  std::snprintf(l.line[5], sizeof l.line[5], "PWR    hold to start");
  ui::Frame f{ui::Page::Trip, ow::format(snapshot(), linkState, deviceMilliVolts), l};
  f.screen.indicator[0] = '\0';
  display.setFullWindow();
  display.firstPage();
  do {
    drawLines("OFF", f.screen, f.lines);
  } while (display.nextPage());
  display.hibernate();
  delay(200);
  digitalWrite(PIN_VBAT_PWR, LOW);
  delay(1000);
  // Still running → powered from USB.
  while (digitalRead(PIN_PWR_BTN) == LOW) delay(10);
  esp_sleep_enable_ext0_wakeup(gpio_num_t(PIN_PWR_BTN), 0);
  esp_deep_sleep_start();
}

static void handleButtons() {
  uint32_t now = millis();
  switch (bootBtn.update(now, digitalRead(PIN_BOOT_BTN) == LOW)) {
    case ui::Press::Click:
      page = ui::next(page);
      policy.requestFull();
      break;
    case ui::Press::Long:
      trip.reset(now);
      sdlog::split();
      page = ui::Page::Trip;
      policy.requestFull();
      Serial.println("[btn] new ride");
      break;
    default: break;
  }
  if (pwrBtn.update(now, digitalRead(PIN_PWR_BTN) == LOW) == ui::Press::Long) powerOff();
}

void setup() {
  pinMode(PIN_VBAT_PWR, OUTPUT);
  digitalWrite(PIN_VBAT_PWR, HIGH);
  pinMode(PIN_EPD_PWR, OUTPUT);
  digitalWrite(PIN_EPD_PWR, LOW);
  pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
  pinMode(PIN_PWR_BTN, INPUT_PULLUP);
  trip.reset(millis());
  Serial.begin(115200);
  delay(50);

  SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, PIN_EPD_CS);
  display.epd2.selectSPI(SPI, SPISettings(4000000, MSBFIRST, SPI_MODE0));
  display.init(0, true, 2, false);
  display.setRotation(0);

  deviceMilliVolts = readDeviceMilliVolts();
  Serial.printf("[ow-epaper] target %s\n", OW_ADDRESS);
  diag::begin([] {
    ow::Telemetry t = snapshot();
    return diag::Snapshot{int(linkState), t.rpm, t.pitch, t.batteryPct, deviceMilliVolts};
  });
  NimBLEDevice::init("ow-epaper");
  NimBLEDevice::setMTU(247);
  startLiveFeed();
  sdlog::begin([] {
    ow::Link l = linkState;
    return sdlog::Sample{l == ow::Link::Live || l == ow::Link::Stale, snapshot(), deviceMilliVolts};
  });
  xTaskCreatePinnedToCore(bleTask, "ble", 8192, nullptr, 2, nullptr, 0);
}

void loop() {
  diag::loopBeat = millis();
  pollSerial();
  handleButtons();
  static uint32_t lastDeviceBatt = millis();
  if (millis() - lastDeviceBatt >= DEVICE_BATT_MS) {
    lastDeviceBatt = millis();
    deviceMilliVolts = readDeviceMilliVolts();
  }
  // Spinner advances at most once per frame, and only when new data arrived.
  static uint32_t heartbeat = 0, seenCount = 0, lastBeat = 0;
  if (notifyCount != seenCount && millis() - lastBeat >= MIN_REFRESH_MS) {
    seenCount = notifyCount;
    lastBeat = millis();
    heartbeat++;
  }
  ow::Indicator ind{heartbeat, (millis() - lastDataMs) / 1000, sdlog::currentIndex >= 0};
  ow::Telemetry t = snapshot();
  if (linkState == ow::Link::Live) trip.update(t, millis());
  ui::Frame next;
  memset(&next, 0, sizeof next);
  next.page = page;
  next.screen = ow::format(t, linkState, deviceMilliVolts, ind);
  if (page == ui::Page::Trip) next.lines = ui::tripLines(trip, millis());
  if (page == ui::Page::Status) {
    next.lines = ui::statusLines({linkState, notifyRate, millis() - lastDataMs,
                                  sdlog::currentIndex, sdlog::rowsWritten, deviceMilliVolts});
  }
  publishLiveFeed();
  ow::Refresh mode = policy.decide(millis(), !(next == shown));
  if (mode != ow::Refresh::None) {
    render(next, mode);
    shown = next;
  }
  delay(50);
}
