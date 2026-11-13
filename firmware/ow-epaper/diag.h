// 1 Hz diagnostics: heartbeats, in-flight ops, notify rates, render timings.
// Lines go to Serial and to LittleFS /diag.log so a ride can be inspected
// afterwards. Serial commands: 'd' = dump log, 'c' = clear log.
#pragma once

#include <Arduino.h>
#include <LittleFS.h>
#include <esp_system.h>

namespace diag {

struct Snapshot {
  int link;
  int rpm;
  float pitch;
  int batteryPct;
  int deviceMilliVolts;
};
using SnapshotFn = Snapshot (*)();

inline volatile uint32_t bleBeat = 0;
inline volatile uint32_t loopBeat = 0;
inline const char* volatile bleOp = "idle";
inline volatile uint16_t bleOpId = 0;
inline volatile uint32_t bleOpStart = 0;
inline const char* volatile dispOp = "idle";
inline volatile uint32_t dispOpStart = 0;
inline volatile uint32_t lastRenderMs = 0;
inline volatile uint32_t partialRenders = 0;
inline volatile uint32_t fullRenders = 0;
inline volatile uint32_t notifyById[256] = {};

inline void countNotify(uint16_t id) { notifyById[id & 0xff]++; }

#define DIAG_BLE_OP(name, id) (diag::bleOp = (name), diag::bleOpId = (id), diag::bleOpStart = millis())
#define DIAG_BLE_DONE() (diag::bleOp = "idle")

constexpr const char* LOG = "/diag.log";
constexpr const char* LOG_OLD = "/diag.old";
constexpr size_t LOG_MAX = 256 * 1024;
inline bool fsOk = false;
inline SnapshotFn snapshotFn = nullptr;

inline void writeLine(const char* line) {
  Serial.println(line);
  if (!fsOk) return;
  File f = LittleFS.open(LOG, FILE_APPEND);
  if (!f) return;
  f.println(line);
  size_t size = f.size();
  f.close();
  if (size > LOG_MAX) {
    LittleFS.remove(LOG_OLD);
    LittleFS.rename(LOG, LOG_OLD);
  }
}

inline void task(void*) {
  static uint32_t prev[256];
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t now = millis();
    uint32_t total = 0, top[3] = {}, topId[3] = {};
    for (int i = 0; i < 256; i++) {
      uint32_t d = notifyById[i] - prev[i];
      prev[i] = notifyById[i];
      total += d;
      for (int k = 0; k < 3; k++) {
        if (d > top[k]) {
          for (int j = 2; j > k; j--) { top[j] = top[j - 1]; topId[j] = topId[j - 1]; }
          top[k] = d;
          topId[k] = i;
          break;
        }
      }
    }
    Snapshot s = snapshotFn ? snapshotFn() : Snapshot{-1, -1, NAN, -1, -1};
    char line[256];
    snprintf(line, sizeof line,
             "[diag] t=%lu link=%d n/s=%lu (f3%02lx:%lu f3%02lx:%lu f3%02lx:%lu) "
             "ble=%s/%04x %lums beat=%lums | disp=%s %lums loop=%lums last=%lums p/f=%lu/%lu "
             "| rpm=%d pitch=%.1f batt=%d vbat=%dmV heap=%lu min=%lu",
             (unsigned long)(now / 1000), s.link, (unsigned long)total,
             (unsigned long)topId[0], (unsigned long)top[0],
             (unsigned long)topId[1], (unsigned long)top[1],
             (unsigned long)topId[2], (unsigned long)top[2],
             bleOp, bleOpId, (unsigned long)(now - bleOpStart), (unsigned long)(now - bleBeat),
             dispOp, (unsigned long)(now - dispOpStart), (unsigned long)(now - loopBeat),
             (unsigned long)lastRenderMs, (unsigned long)partialRenders, (unsigned long)fullRenders,
             s.rpm, s.pitch, s.batteryPct, s.deviceMilliVolts,
             (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getMinFreeHeap());
    writeLine(line);
  }
}

inline void begin(SnapshotFn fn) {
  snapshotFn = fn;
  fsOk = LittleFS.begin(true, "/littlefs", 10, "spiffs");
  char line[96];
  snprintf(line, sizeof line, "[diag] ===== boot reset_reason=%d fs=%d =====",
           int(esp_reset_reason()), int(fsOk));
  writeLine(line);
  xTaskCreatePinnedToCore(task, "diag", 4096, nullptr, 1, nullptr, 1);
}

inline void dump() {
  for (const char* path : {LOG_OLD, LOG}) {
    File f = LittleFS.open(path, FILE_READ);
    if (!f) continue;
    Serial.printf("----- %s (%u bytes) -----\n", path, unsigned(f.size()));
    while (f.available()) Serial.write(f.read());
    f.close();
  }
  Serial.println("----- end -----");
}

inline void clear() {
  if (!fsOk) return;
  LittleFS.remove(LOG);
  LittleFS.remove(LOG_OLD);
  Serial.println("[diag] log cleared");
}

}  // namespace diag
