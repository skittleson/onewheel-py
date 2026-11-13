// Onewheel BLE protocol: decoders, screen text, and e-paper refresh policy.
// Pure C++ (no Arduino deps) so it runs under host tests in test/.
// Mirrors src/ow_uuids.py — keep both in sync.
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace ow {

// 16-bit short ids; full UUID = e659fXXX-ea98-11e3-ac10-0800200c9a66
// f310 onward follow OWCE (OWBoard.cs), confirmed against the official app's
// reads in captures/snoop_decoded.txt. Keep in sync with src/ow_uuids.py.
constexpr const char* SERVICE_UUID = "e659f300-ea98-11e3-ac10-0800200c9a66";
constexpr uint16_t RIDING_MODE       = 0xf302;
constexpr uint16_t BATTERY_REMAINING = 0xf303;
constexpr uint16_t PITCH             = 0xf307;
constexpr uint16_t ROLL              = 0xf308;
constexpr uint16_t TRIP_ODOMETER     = 0xf30a;
constexpr uint16_t RPM               = 0xf30b;
constexpr uint16_t STATUS_ERROR      = 0xf30f;
constexpr uint16_t TEMPERATURE       = 0xf310;  // [0] motor °C, [1] controller °C
constexpr uint16_t FIRMWARE_REVISION = 0xf311;
constexpr uint16_t CURRENT_AMPS      = 0xf312;  // int16 × 0.002 A (Pint)
constexpr uint16_t BATTERY_TEMP      = 0xf315;  // [0] °C
constexpr uint16_t BATTERY_VOLTAGE   = 0xf316;  // ×0.1 V; reads 0 (locked) on Pint S
constexpr uint16_t SAFETY_HEADROOM   = 0xf317;  // unlock writes 0x0001 here
constexpr uint16_t HARDWARE_REVISION = 0xf318;
constexpr uint16_t UART_SERIAL_READ  = 0xf3fe;
constexpr uint16_t UART_SERIAL_WRITE = 0xf3ff;

inline void uuidFor(uint16_t id, char out[37]) {
  std::snprintf(out, 37, "e659%04x-ea98-11e3-ac10-0800200c9a66", id);
}

struct Telemetry {
  float pitch = NAN;
  float roll = NAN;
  float voltage = NAN;
  int batteryPct = -1;
  int rpm = -1;
  int controllerTemp = -1;
  int motorTemp = -1;
  int batteryTemp = -1;
  float amps = NAN;
  int state = -1;
  int errorFlags = 0;
  int tripRevs = -1;
};

inline uint16_t u16(const uint8_t* b) { return uint16_t(b[0] << 8 | b[1]); }

// Decode one notification/read into `t`. Returns false for unknown ids or
// payloads too short to decode (leaves `t` untouched).
inline bool apply(Telemetry& t, uint16_t id, const uint8_t* b, size_t n) {
  if (n == 0) return false;
  switch (id) {
    case BATTERY_REMAINING: t.batteryPct = n == 1 ? b[0] : u16(b); return true;
    case BATTERY_TEMP:      t.batteryTemp = b[0]; return true;
    default: break;
  }
  if (n < 2) return false;
  switch (id) {
    case PITCH:           t.pitch = (int(u16(b)) - 1800) / 10.0f; return true;
    case ROLL:            t.roll = (int(u16(b)) - 1800) / 10.0f; return true;
    case RPM:             t.rpm = u16(b); return true;
    case BATTERY_VOLTAGE: t.voltage = u16(b) ? u16(b) / 10.0f : NAN; return true;
    case CURRENT_AMPS:    t.amps = int16_t(u16(b)) * 0.002f; return true;
    case TEMPERATURE:     t.motorTemp = b[0]; t.controllerTemp = b[1]; return true;
    case STATUS_ERROR:    t.state = u16(b) & 0xff; t.errorFlags = u16(b) >> 8; return true;
    case TRIP_ODOMETER:   t.tripRevs = u16(b); return true;
    default: return false;
  }
}

// Wheel circumference ≈ 35.4" → mph ≈ rpm × 0.0124 (same as ow_uuids.rpm_to_mph)
inline float rpmToMph(int rpm) { return rpm * 0.0124f; }

inline const char* stateName(int s) {
  switch (s) {
    case 0: return "INIT";
    case 1: return "OFF";
    case 2: return "READY";
    case 3: return "RIDING";
    case 4: return "CHARGING";
    case 5: return "CHARGED";
    case 6: return "SHUTDOWN";
    default: return "?";
  }
}

// Single-cell LiPo resting voltage → %, linear between points. -1 = unknown.
inline int lipoPercent(int mV) {
  if (mV < 0) return -1;
  static const int curve[][2] = {
      {3300, 0}, {3500, 10}, {3700, 35}, {3800, 50}, {3900, 62}, {4000, 75}, {4100, 88}, {4200, 100}};
  constexpr int N = sizeof curve / sizeof curve[0];
  if (mV <= curve[0][0]) return 0;
  if (mV >= curve[N - 1][0]) return 100;
  for (int i = 1; i < N; i++) {
    if (mV <= curve[i][0]) {
      int v0 = curve[i - 1][0], p0 = curve[i - 1][1], v1 = curve[i][0], p1 = curve[i][1];
      return p0 + (mV - v0) * (p1 - p0) / (v1 - v0);
    }
  }
  return 100;
}

enum class Link { Scanning, Connecting, Unlocking, Live, Stale };

// Liveness info shown next to the speed so a frozen screen is obvious.
struct Indicator {
  uint32_t heartbeat = 0;  // advances whenever new data arrived since the last frame
  uint32_t ageS = 0;       // seconds since the last decoded value
  bool recording = false;  // SD ride log open
};

// Everything the display draws, as text. Redraw only when this changes.
struct Screen {
  char speed[8];
  char battery[8];
  char battTemp[8];
  char pitch[8];
  char temps[12];
  char status[12];
  char device[16];  // this ESP32's own LiPo, e.g. "ESP 87%"
  char indicator[24];
  int batteryPct;  // for the gauge; -1 = unknown

  bool operator==(const Screen& o) const { return std::memcmp(this, &o, sizeof *this) == 0; }
};

inline Screen format(const Telemetry& t, Link link, int deviceMilliVolts = -1,
                     const Indicator& ind = Indicator{}) {
  Screen s;
  std::memset(&s, 0, sizeof s);
  if (t.rpm >= 0) std::snprintf(s.speed, sizeof s.speed, "%.1f", rpmToMph(t.rpm));
  else std::snprintf(s.speed, sizeof s.speed, "--");
  if (t.batteryPct >= 0) std::snprintf(s.battery, sizeof s.battery, "%d%%", t.batteryPct);
  else std::snprintf(s.battery, sizeof s.battery, "--%%");
  if (t.batteryTemp >= 0 && t.batteryTemp < 1000)
    std::snprintf(s.battTemp, sizeof s.battTemp, "%dC", t.batteryTemp);
  else std::snprintf(s.battTemp, sizeof s.battTemp, "--C");
  if (!std::isnan(t.pitch)) std::snprintf(s.pitch, sizeof s.pitch, "%.1f", t.pitch);
  else std::snprintf(s.pitch, sizeof s.pitch, "--");
  if (t.controllerTemp >= 0)
    std::snprintf(s.temps, sizeof s.temps, "%d/%dC", t.controllerTemp, t.motorTemp);
  else std::snprintf(s.temps, sizeof s.temps, "--/--C");
  s.batteryPct = t.batteryPct;
  int dev = lipoPercent(deviceMilliVolts);
  if (dev >= 0) std::snprintf(s.device, sizeof s.device, "ESP %d%%", dev);
  else std::snprintf(s.device, sizeof s.device, "ESP --%%");

  if (link == Link::Live || link == Link::Stale) {
    const char* rec = ind.recording ? "REC " : "";
    if (link == Link::Stale)
      std::snprintf(s.indicator, sizeof s.indicator, "%s%lus old", rec, (unsigned long)ind.ageS);
    else
      std::snprintf(s.indicator, sizeof s.indicator, "%s%c", rec, "|/-\\"[ind.heartbeat % 4]);
  }

  switch (link) {
    case Link::Scanning:   std::snprintf(s.status, sizeof s.status, "SCANNING"); break;
    case Link::Connecting: std::snprintf(s.status, sizeof s.status, "CONNECTING"); break;
    case Link::Unlocking:  std::snprintf(s.status, sizeof s.status, "UNLOCKING"); break;
    case Link::Stale:      std::snprintf(s.status, sizeof s.status, "NO DATA"); break;
    case Link::Live:
      if (t.errorFlags) std::snprintf(s.status, sizeof s.status, "FAULT %02X", t.errorFlags);
      else std::snprintf(s.status, sizeof s.status, "%s", t.state >= 0 ? stateName(t.state) : "LIVE");
      break;
  }
  return s;
}

enum class Refresh { None, Partial, Full };

// E-paper: partial refreshes are fast but ghost; do a full one every N.
class RefreshPolicy {
 public:
  RefreshPolicy(uint32_t minIntervalMs, int partialsPerFull)
      : minInterval_(minIntervalMs), partialsPerFull_(partialsPerFull) {}

  void requestFull() { forceFull_ = true; }

  Refresh decide(uint32_t nowMs, bool changed) {
    if (forceFull_ || first_) return commit(nowMs, Refresh::Full);
    if (!changed || nowMs - last_ < minInterval_) return Refresh::None;
    if (partials_ >= partialsPerFull_) return commit(nowMs, Refresh::Full);
    return commit(nowMs, Refresh::Partial);
  }

 private:
  Refresh commit(uint32_t nowMs, Refresh r) {
    last_ = nowMs;
    first_ = forceFull_ = false;
    partials_ = r == Refresh::Full ? 0 : partials_ + 1;
    return r;
  }

  uint32_t minInterval_;
  int partialsPerFull_;
  uint32_t last_ = 0;
  int partials_ = 0;
  bool first_ = true;
  bool forceFull_ = false;
};

}  // namespace ow
