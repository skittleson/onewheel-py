// SD card ride logger (Arduino side). Row format lives in ride_log.h.
//
// Waveshare ESP32-S3-ePaper-1.54 TF slot, 1-bit SDMMC: CLK 39, CMD 41, D0 40.
// The card is formatted FAT32 automatically if it won't mount.
//
// Serial commands (also see diag.h):
//   l          list ride files
//   g<N>\n     print /rides/ride_NNNN.csv between BEGIN/END markers
#pragma once

#include <Arduino.h>
#include <FS.h>
#include <SD_MMC.h>

#include "ow_protocol.h"
#include "ride_log.h"

namespace sdlog {

constexpr int PIN_CLK = 39;
constexpr int PIN_CMD = 41;
constexpr int PIN_D0 = 40;
constexpr uint32_t ROW_MS = 200;     // 5 Hz
constexpr uint32_t FLUSH_MS = 2000;  // bound data loss on power cut to ~2 s

struct Sample {
  bool live;
  ow::Telemetry t;
  int espMilliVolts;
};
using SampleFn = Sample (*)();

inline volatile bool cardOk = false;
inline volatile int currentIndex = -1;
inline volatile uint32_t rowsWritten = 0;
inline volatile uint32_t writeErrors = 0;
inline SampleFn sampleFn = nullptr;
inline volatile bool splitRequested = false;  // BOOT long-press: start a new ride file
inline volatile bool stopped = false;         // powering off: never reopen
inline SemaphoreHandle_t fsMutex = nullptr;

inline int nextIndex() {
  int maxIdx = 0;
  File dir = SD_MMC.open(ridelog::DIR);
  if (!dir) return 1;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    maxIdx = max(maxIdx, ridelog::parseIndex(f.name()));
  }
  return maxIdx + 1;
}

inline void task(void*) {
  File file;
  uint32_t sessionStart = 0, lastFlush = 0;
  char row[ridelog::ROW_MAX];
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(ROW_MS));
    if (!cardOk || !sampleFn) continue;
    Sample s = sampleFn();
    xSemaphoreTake(fsMutex, portMAX_DELAY);
    if (stopped) s.live = false;
    if (splitRequested && file) {
      file.close();
      Serial.printf("[sd] split: closed ride_%04d\n", currentIndex);
      currentIndex = -1;
    }
    splitRequested = false;
    if (s.live && !file) {
      char name[32];
      int idx = nextIndex();
      ridelog::fileName(name, sizeof name, idx);
      file = SD_MMC.open(name, FILE_WRITE);
      if (file) {
        file.print(ridelog::HEADER);
        currentIndex = idx;
        sessionStart = lastFlush = millis();
        Serial.printf("[sd] recording %s\n", name);
      } else {
        writeErrors++;
      }
    } else if (!s.live && file) {
      file.close();
      Serial.printf("[sd] closed ride_%04d (%lu rows)\n", currentIndex, (unsigned long)rowsWritten);
      currentIndex = -1;
    }
    if (file) {
      ridelog::formatRow(row, sizeof row, millis() - sessionStart, s.t, s.espMilliVolts);
      if (file.print(row) == 0) writeErrors++;
      else rowsWritten++;
      if (millis() - lastFlush >= FLUSH_MS) {
        file.flush();
        lastFlush = millis();
      }
    }
    xSemaphoreGive(fsMutex);
  }
}

inline void split() { splitRequested = true; }

// Stop logging and wait until the open file (if any) is closed.
inline void stop() {
  stopped = true;
  for (int i = 0; i < 20 && currentIndex >= 0; i++) vTaskDelay(pdMS_TO_TICKS(ROW_MS));
}

inline void begin(SampleFn fn) {
  sampleFn = fn;
  fsMutex = xSemaphoreCreateMutex();
  SD_MMC.setPins(PIN_CLK, PIN_CMD, PIN_D0);
  cardOk = SD_MMC.begin("/sdcard", true /*1-bit*/, true /*format if mount fails*/);
  if (cardOk) {
    SD_MMC.mkdir(ridelog::DIR);
    Serial.printf("[sd] card %lluMB, type %d, next ride_%04d\n",
                  SD_MMC.cardSize() / (1024 * 1024), int(SD_MMC.cardType()), nextIndex());
  } else {
    Serial.println("[sd] no card / mount failed");
  }
  xTaskCreatePinnedToCore(task, "sdlog", 6144, nullptr, 1, nullptr, 1);
}

inline void list() {
  if (!cardOk) {
    Serial.println("[sd] no card");
    return;
  }
  xSemaphoreTake(fsMutex, portMAX_DELAY);
  File dir = SD_MMC.open(ridelog::DIR);
  Serial.println("----- rides -----");
  for (File f = dir ? dir.openNextFile() : File(); f; f = dir.openNextFile()) {
    if (ridelog::parseIndex(f.name()) >= 0) Serial.printf("%s %u\n", f.name(), unsigned(f.size()));
  }
  Serial.println("----- end -----");
  xSemaphoreGive(fsMutex);
}

inline void get(int index) {
  char name[32];
  ridelog::fileName(name, sizeof name, index);
  xSemaphoreTake(fsMutex, portMAX_DELAY);
  File f = SD_MMC.open(name, FILE_READ);
  if (!f) {
    Serial.printf("----- MISSING %s -----\n", name);
  } else {
    Serial.printf("----- BEGIN %s %u -----\n", name, unsigned(f.size()));
    uint8_t buf[512];
    for (int n = f.read(buf, sizeof buf); n > 0; n = f.read(buf, sizeof buf)) Serial.write(buf, n);
    f.close();
    Serial.printf("----- END %s -----\n", name);
  }
  xSemaphoreGive(fsMutex);
}

}  // namespace sdlog
