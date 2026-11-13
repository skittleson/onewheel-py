// Ride log CSV format + file naming. Pure C++ so it runs in host tests.
// One file per BLE session on the SD card: /rides/ride_NNNN.csv
#pragma once

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "ow_protocol.h"

namespace ridelog {

constexpr const char* DIR = "/rides";
constexpr const char* HEADER =
    "t_s,rpm,mph,pitch_deg,roll_deg,batt_pct,batt_c,amps,volts,ctrl_c,motor_c,state,error_flags,esp_mv\n";
constexpr size_t ROW_MAX = 128;

namespace detail {
// snprintf returns the length it *would* have written; clamp p so a truncated
// field can never push the cursor (or the next size_t(end - p), which would
// wrap huge) past the buffer.
inline void advance(char*& p, char* end, int written) {
  if (written > 0) p += written;
  if (p > end) p = end;
}
inline void cellInt(char*& p, char* end, int v, bool known) {
  if (known && p < end) advance(p, end, std::snprintf(p, size_t(end - p), "%d", v));
  if (p < end) *p++ = ',';
}
inline void cellFloat(char*& p, char* end, float v, const char* fmt) {
  if (!std::isnan(v) && p < end) advance(p, end, std::snprintf(p, size_t(end - p), fmt, double(v)));
  if (p < end) *p++ = ',';
}
}  // namespace detail

// Unknown values become empty cells so the CSV loads cleanly in pandas/sheets.
inline void formatRow(char* out, size_t size, unsigned long elapsedMs, const ow::Telemetry& t,
                      int espMilliVolts) {
  using namespace detail;
  if (size < 2) {
    if (size) out[0] = '\0';
    return;
  }
  char* p = out;
  char* end = out + size - 2;  // room for '\n' + NUL
  advance(p, end, std::snprintf(p, size_t(end - p), "%lu.%03lu,", elapsedMs / 1000, elapsedMs % 1000));
  cellInt(p, end, t.rpm, t.rpm >= 0);
  cellFloat(p, end, t.rpm >= 0 ? ow::rpmToMph(t.rpm) : NAN, "%.2f");
  cellFloat(p, end, t.pitch, "%.1f");
  cellFloat(p, end, t.roll, "%.1f");
  cellInt(p, end, t.batteryPct, t.batteryPct >= 0);
  cellInt(p, end, t.batteryTemp, t.batteryTemp >= 0);
  cellFloat(p, end, t.amps, "%.2f");
  cellFloat(p, end, t.voltage, "%.1f");
  cellInt(p, end, t.controllerTemp, t.controllerTemp >= 0);
  cellInt(p, end, t.motorTemp, t.motorTemp >= 0);
  cellInt(p, end, t.state, t.state >= 0);
  cellInt(p, end, t.errorFlags, true);
  if (espMilliVolts >= 0 && p < end) advance(p, end, std::snprintf(p, size_t(end - p), "%d", espMilliVolts));
  *p++ = '\n';
  *p = '\0';
}

// --- Live BLE feed (ESP32 → laptop) ------------------------------------------
// Custom GATT service so a laptop can watch the device in real time.
constexpr const char* LIVE_SERVICE_UUID = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0001";
constexpr const char* LIVE_ROW_UUID     = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0002";  // notify/read
constexpr const char* LIVE_HEADER_UUID  = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0003";  // read
constexpr const char* LIVE_HEADER =
    "link,data_age_ms,notify_per_s,recording,t_s,rpm,mph,pitch_deg,roll_deg,batt_pct,batt_c,"
    "amps,volts,ctrl_c,motor_c,state,error_flags,esp_mv\n";
constexpr size_t LIVE_ROW_MAX = ROW_MAX + 48;

inline const char* linkName(ow::Link l) {
  switch (l) {
    case ow::Link::Scanning:   return "SCANNING";
    case ow::Link::Connecting: return "CONNECTING";
    case ow::Link::Unlocking:  return "UNLOCKING";
    case ow::Link::Live:       return "LIVE";
    case ow::Link::Stale:      return "STALE";
  }
  return "?";
}

inline void formatLive(char* out, size_t size, ow::Link link, unsigned long ageMs,
                       unsigned long notifyPerS, bool recording, unsigned long elapsedMs,
                       const ow::Telemetry& t, int espMilliVolts) {
  int n = std::snprintf(out, size, "%s,%lu,%lu,%d,", linkName(link), ageMs, notifyPerS,
                        recording ? 1 : 0);
  if (n < 0 || size_t(n) >= size) return;
  formatRow(out + n, size - size_t(n), elapsedMs, t, espMilliVolts);
}

inline void fileName(char* out, size_t size, int index) {
  std::snprintf(out, size, "%s/ride_%04d.csv", DIR, index);
}

// "ride_0007.csv" or "/rides/ride_0007.csv" → 7; anything else → -1.
inline int parseIndex(const char* name) {
  const char* base = std::strrchr(name, '/');
  base = base ? base + 1 : name;
  if (std::strncmp(base, "ride_", 5) != 0) return -1;
  char* endDigits = nullptr;
  long n = std::strtol(base + 5, &endDigits, 10);
  if (endDigits == base + 5 || std::strcmp(endDigits, ".csv") != 0 || n < 0) return -1;
  return int(n);
}

}  // namespace ridelog
