// Host-side tests for ui.h (buttons, trip stats, extra pages).
#include <cmath>
#include <cstdio>
#include <cstring>

#include "../ui.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)
#define CHECK_STR(a, b) do { if (std::strcmp((a), (b)) != 0) { std::printf("FAIL %s:%d  \"%s\" != \"%s\"\n", __FILE__, __LINE__, (a), (b)); failures++; } } while (0)

using ui::Button;
using ui::Press;

static void test_click_on_release() {
  Button b(1000);
  CHECK(b.update(0, false) == Press::None);
  CHECK(b.update(100, true) == Press::None);
  CHECK(b.update(130, true) == Press::None);   // still held
  CHECK(b.update(250, false) == Press::Click);
  CHECK(b.update(300, false) == Press::None);
}

static void test_bounce_is_ignored() {
  Button b(1000);
  b.update(0, false);
  b.update(100, true);
  CHECK(b.update(110, false) == Press::None);  // 10 ms blip = bounce
}

static void test_long_press_fires_once_while_held() {
  Button b(1000);
  b.update(0, false);
  b.update(100, true);
  CHECK(b.update(900, true) == Press::None);
  CHECK(b.update(1100, true) == Press::Long);
  CHECK(b.update(1500, true) == Press::None);
  CHECK(b.update(1600, false) == Press::None); // release after long → no click
}

static void test_held_at_boot_must_release_first() {
  // PWR is held to power the board on; that hold must not count as "turn off".
  Button b(2000);
  CHECK(b.update(0, true) == Press::None);
  CHECK(b.update(3000, true) == Press::None);
  CHECK(b.update(3100, false) == Press::None);
  b.update(4000, true);
  CHECK(b.update(6100, true) == Press::Long);
}

static void test_page_cycle() {
  CHECK(ui::next(ui::Page::Ride) == ui::Page::Trip);
  CHECK(ui::next(ui::Page::Trip) == ui::Page::Status);
  CHECK(ui::next(ui::Page::Status) == ui::Page::Ride);
}

static void feed(ui::Trip& trip, uint32_t now, int rpm, int batt, int revs, int ctrl, int motor) {
  ow::Telemetry t;
  t.rpm = rpm;
  t.batteryPct = batt;
  t.tripRevs = revs;
  t.controllerTemp = ctrl;
  t.motorTemp = motor;
  trip.update(t, now);
}

static void test_trip_stats() {
  ui::Trip trip;
  trip.reset(10000);
  feed(trip, 10000, 0, 80, 1000, 25, 24);
  feed(trip, 20000, 800, 79, 1300, 30, 28);
  feed(trip, 70000, 400, 77, 2000, 35, 40);
  CHECK(trip.elapsedS(70000) == 60);
  CHECK(std::fabs(trip.topMph - 9.92f) < 0.01f);
  CHECK(trip.startBatt == 80 && trip.lastBatt == 77);
  CHECK(trip.peakCtrl == 35 && trip.peakMotor == 40);
  // 1000 revs × 35.4" / 63360 = 0.559 mi
  CHECK(std::fabs(trip.miles() - 0.559f) < 0.005f);
}

static void test_trip_ignores_unknown_values() {
  ui::Trip trip;
  trip.reset(0);
  feed(trip, 1000, -1, -1, -1, -1, -1);
  CHECK(trip.topMph == 0.0f);
  CHECK(trip.startBatt == -1);
  CHECK(trip.miles() == 0.0f);
}

static void test_trip_odometer_wrap() {
  ui::Trip trip;
  trip.reset(0);
  feed(trip, 0, 0, 50, 65500, 20, 20);
  feed(trip, 1000, 0, 50, 100, 20, 20);   // uint16 rolled over: 136 revs
  CHECK(std::fabs(trip.miles() - 136 * 35.4f / 63360.0f) < 1e-4f);
}

static void test_trip_odometer_reset_ignored() {
  // Board power-cycles mid-log: the Pint resets its odometer to 0. A backward
  // jump is not motion and must not count as ~65k revs.
  ui::Trip trip;
  trip.reset(0);
  feed(trip, 0, 0, 50, 5000, 20, 20);
  feed(trip, 1000, 0, 50, 200, 20, 20);    // reset, not reverse
  feed(trip, 2000, 0, 50, 260, 20, 20);    // 60 revs after the reset
  CHECK(std::fabs(trip.miles() - 60 * 35.4f / 63360.0f) < 1e-4f);
}

static void test_trip_lines() {
  ui::Trip trip;
  trip.reset(0);
  feed(trip, 0, 0, 80, 1000, 25, 24);
  feed(trip, 125000, 800, 77, 2000, 35, 40);
  ui::Lines l = ui::tripLines(trip, 125000);
  CHECK_STR(l.line[0], "TIME   2:05");
  CHECK_STR(l.line[1], "TOP    9.9 mph");
  CHECK_STR(l.line[2], "DIST   0.56 mi");
  CHECK_STR(l.line[3], "BATT   80->77%");
  CHECK_STR(l.line[4], "PEAK   35/40C");
}

static void test_status_lines() {
  ui::StatusInfo s{ow::Link::Live, 412, 120, 7, 1234, 4166};
  ui::Lines l = ui::statusLines(s);
  CHECK_STR(l.line[0], "LINK   LIVE");
  CHECK_STR(l.line[1], "RATE   412/s");
  CHECK_STR(l.line[2], "AGE    0.1s");
  CHECK_STR(l.line[3], "SD     ride_0007");
  CHECK_STR(l.line[4], "ROWS   1234");
  CHECK_STR(l.line[5], "ESP    4.17V");
  ui::StatusInfo off{ow::Link::Scanning, 0, 0, -1, 0, -1};
  CHECK_STR(ui::statusLines(off).line[3], "SD     off");
  CHECK_STR(ui::statusLines(off).line[5], "ESP    --");
}

static void test_lines_equality() {
  ui::StatusInfo s{ow::Link::Live, 412, 120, 7, 1234, 4166};
  CHECK(ui::statusLines(s) == ui::statusLines(s));
  s.rows++;
  CHECK(!(ui::statusLines(s) == ui::statusLines(ui::StatusInfo{ow::Link::Live, 412, 120, 7, 1234, 4166})));
}

int main() {
  test_click_on_release();
  test_bounce_is_ignored();
  test_long_press_fires_once_while_held();
  test_held_at_boot_must_release_first();
  test_page_cycle();
  test_trip_stats();
  test_trip_ignores_unknown_values();
  test_trip_odometer_wrap();
  test_trip_odometer_reset_ignored();
  test_trip_lines();
  test_status_lines();
  test_lines_equality();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all ui tests passed\n");
  return 0;
}
