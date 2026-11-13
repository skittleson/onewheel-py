// Buttons, pages and trip stats. Pure C++ so it runs under host tests.
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "ow_protocol.h"

namespace ui {

// --- Buttons -------------------------------------------------------------------
enum class Press { None, Click, Long };

// Debounced click / long-press detector. Feed it the raw "is pressed" level
// every loop. A button already held at boot is ignored until released (PWR is
// held to power the board on — that must not count as "power off").
class Button {
 public:
  explicit Button(uint32_t longMs, uint32_t debounceMs = 30)
      : longMs_(longMs), debounceMs_(debounceMs) {}

  Press update(uint32_t nowMs, bool pressed) {
    if (first_) {
      first_ = false;
      armed_ = !pressed;
      down_ = pressed;
      return Press::None;
    }
    if (!armed_) {
      if (!pressed) armed_ = true;
      down_ = pressed;
      return Press::None;
    }
    if (pressed && !down_) {
      down_ = true;
      downAt_ = nowMs;
      longFired_ = false;
      return Press::None;
    }
    if (pressed && down_) {
      if (!longFired_ && nowMs - downAt_ >= longMs_) {
        longFired_ = true;
        return Press::Long;
      }
      return Press::None;
    }
    if (!pressed && down_) {
      down_ = false;
      if (!longFired_ && nowMs - downAt_ >= debounceMs_) return Press::Click;
    }
    return Press::None;
  }

 private:
  uint32_t longMs_, debounceMs_;
  uint32_t downAt_ = 0;
  bool first_ = true, armed_ = false, down_ = false, longFired_ = false;
};

// --- Pages ---------------------------------------------------------------------
enum class Page { Ride, Trip, Status };

inline Page next(Page p) {
  switch (p) {
    case Page::Ride: return Page::Trip;
    case Page::Trip: return Page::Status;
    default:         return Page::Ride;
  }
}

// --- Trip stats ----------------------------------------------------------------
constexpr float WHEEL_CIRCUMFERENCE_IN = 35.4f;  // same as rpmToMph()

struct Trip {
  uint32_t startMs = 0;
  float topMph = 0;
  int startBatt = -1, lastBatt = -1;
  int peakCtrl = -1, peakMotor = -1;
  int lastRevs = -1;
  uint32_t revs = 0;

  void reset(uint32_t nowMs) { *this = Trip{}; startMs = nowMs; }

  void update(const ow::Telemetry& t, uint32_t /*nowMs*/) {
    if (t.rpm >= 0 && ow::rpmToMph(t.rpm) > topMph) topMph = ow::rpmToMph(t.rpm);
    if (t.batteryPct >= 0) {
      if (startBatt < 0) startBatt = t.batteryPct;
      lastBatt = t.batteryPct;
    }
    if (t.controllerTemp > peakCtrl) peakCtrl = t.controllerTemp;
    if (t.motorTemp > peakMotor) peakMotor = t.motorTemp;
    if (t.tripRevs >= 0) {
      if (lastRevs >= 0) {
        uint16_t d = uint16_t(t.tripRevs - lastRevs);
        // The Pint resets the odometer when the board power-cycles; a delta
        // beyond what one keepalive interval can add (>32k revs never fits
        // 200 ms) is a reset, not motion.
        if (d < 32768) revs += d;
      }
      lastRevs = t.tripRevs;
    }
  }

  uint32_t elapsedS(uint32_t nowMs) const { return (nowMs - startMs) / 1000; }
  float miles() const { return revs * WHEEL_CIRCUMFERENCE_IN / 63360.0f; }
};

// --- Text pages ----------------------------------------------------------------
struct Lines {
  char line[6][24];
  bool operator==(const Lines& o) const { return std::memcmp(this, &o, sizeof *this) == 0; }
};

inline Lines tripLines(const Trip& trip, uint32_t nowMs) {
  Lines l;
  std::memset(&l, 0, sizeof l);
  uint32_t s = trip.elapsedS(nowMs);
  std::snprintf(l.line[0], sizeof l.line[0], "TIME   %lu:%02lu", (unsigned long)(s / 60),
                (unsigned long)(s % 60));
  std::snprintf(l.line[1], sizeof l.line[1], "TOP    %.1f mph", double(trip.topMph));
  std::snprintf(l.line[2], sizeof l.line[2], "DIST   %.2f mi", double(trip.miles()));
  if (trip.startBatt >= 0 && trip.startBatt < 1000 && trip.lastBatt < 1000)
    std::snprintf(l.line[3], sizeof l.line[3], "BATT   %d->%d%%", trip.startBatt, trip.lastBatt);
  else std::snprintf(l.line[3], sizeof l.line[3], "BATT   --");
  if (trip.peakCtrl >= 0 && trip.peakCtrl < 1000 && trip.peakMotor < 1000)
    std::snprintf(l.line[4], sizeof l.line[4], "PEAK   %d/%dC", trip.peakCtrl, trip.peakMotor);
  else std::snprintf(l.line[4], sizeof l.line[4], "PEAK   --");
  return l;
}

// Everything one e-paper frame shows; redraw only when this changes.
struct Frame {
  Page page;
  ow::Screen screen;
  Lines lines;
  bool operator==(const Frame& o) const { return std::memcmp(this, &o, sizeof *this) == 0; }
};

struct StatusInfo {
  ow::Link link;
  unsigned long notifyPerS;
  unsigned long dataAgeMs;
  int rideIndex;  // -1 = not recording
  unsigned long rows;
  int espMilliVolts;
};

inline const char* linkText(ow::Link l) {
  switch (l) {
    case ow::Link::Scanning:   return "SCANNING";
    case ow::Link::Connecting: return "CONNECTING";
    case ow::Link::Unlocking:  return "UNLOCKING";
    case ow::Link::Live:       return "LIVE";
    case ow::Link::Stale:      return "STALE";
  }
  return "?";
}

inline Lines statusLines(const StatusInfo& s) {
  Lines l;
  std::memset(&l, 0, sizeof l);
  std::snprintf(l.line[0], sizeof l.line[0], "LINK   %s", linkText(s.link));
  std::snprintf(l.line[1], sizeof l.line[1], "RATE   %lu/s", s.notifyPerS % 100000);
  std::snprintf(l.line[2], sizeof l.line[2], "AGE    %.1fs", double(s.dataAgeMs % 10000000) / 1000.0);
  if (s.rideIndex >= 0 && s.rideIndex < 10000)
    std::snprintf(l.line[3], sizeof l.line[3], "SD     ride_%04d", s.rideIndex);
  else std::snprintf(l.line[3], sizeof l.line[3], "SD     off");
  std::snprintf(l.line[4], sizeof l.line[4], "ROWS   %lu", s.rows % 10000000);
  if (s.espMilliVolts >= 0 && s.espMilliVolts < 10000)
    std::snprintf(l.line[5], sizeof l.line[5], "ESP    %.2fV", s.espMilliVolts / 1000.0);
  else std::snprintf(l.line[5], sizeof l.line[5], "ESP    --");
  return l;
}

}  // namespace ui
