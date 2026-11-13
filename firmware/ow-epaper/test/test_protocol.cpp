// Host-side tests for ow_protocol.h. Build + run: make -C firmware/ow-epaper test
// Vectors mirror tests/test_ow_uuids.py so firmware and Python agree.
#include <cmath>
#include <cstdio>
#include <cstring>

#include "../ow_protocol.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)
#define CHECK_STR(a, b) do { if (std::strcmp((a), (b)) != 0) { std::printf("FAIL %s:%d  \"%s\" != \"%s\"\n", __FILE__, __LINE__, (a), (b)); failures++; } } while (0)
#define CHECK_NEAR(a, b) CHECK(std::fabs((a) - (b)) < 1e-4)

static bool apply(ow::Telemetry& t, uint16_t id, const char* hex) {
  uint8_t buf[32];
  size_t n = std::strlen(hex) / 2;
  for (size_t i = 0; i < n; i++) std::sscanf(hex + 2 * i, "%2hhx", &buf[i]);
  return ow::apply(t, id, buf, n);
}

static void test_decoders() {
  ow::Telemetry t;
  CHECK(apply(t, ow::PITCH, "0709"));
  CHECK_NEAR(t.pitch, 0.1f);
  apply(t, ow::PITCH, "06e8");
  CHECK_NEAR(t.pitch, -3.2f);
  apply(t, ow::ROLL, "0708");
  CHECK_NEAR(t.roll, 0.0f);
  apply(t, ow::BATTERY_VOLTAGE, "0258");
  CHECK_NEAR(t.voltage, 60.0f);
  apply(t, ow::BATTERY_VOLTAGE, "0000");    // locked on Pint S firmware → unknown
  CHECK(std::isnan(t.voltage));
  apply(t, ow::BATTERY_TEMP, "191d");
  CHECK(t.batteryTemp == 25);
  apply(t, ow::CURRENT_AMPS, "fe0c");       // -500 × 0.002 A
  CHECK_NEAR(t.amps, -1.0f);
  apply(t, ow::BATTERY_REMAINING, "55");
  CHECK(t.batteryPct == 85);
  apply(t, ow::BATTERY_REMAINING, "0055");
  CHECK(t.batteryPct == 85);
  apply(t, ow::RPM, "0010");
  CHECK(t.rpm == 16);
  apply(t, ow::TEMPERATURE, "1e1c");        // wire order: motor, controller
  CHECK(t.controllerTemp == 28 && t.motorTemp == 30);
  apply(t, ow::STATUS_ERROR, "0103");
  CHECK(t.state == 3 && t.errorFlags == 1);
}

static void test_uuid_map_matches_owce() {
  CHECK(ow::TEMPERATURE == 0xf310);
  CHECK(ow::FIRMWARE_REVISION == 0xf311);
  CHECK(ow::CURRENT_AMPS == 0xf312);
  CHECK(ow::BATTERY_TEMP == 0xf315);
  CHECK(ow::BATTERY_VOLTAGE == 0xf316);
  CHECK(ow::SAFETY_HEADROOM == 0xf317);
  CHECK(ow::HARDWARE_REVISION == 0xf318);
}

static void test_apply_ignores_unknown_and_short() {
  ow::Telemetry t;
  CHECK(!apply(t, 0xf31e, "0301"));
  CHECK(!apply(t, ow::PITCH, "07"));
  CHECK(std::isnan(t.pitch));
}

static void test_mph() {
  CHECK_NEAR(ow::rpmToMph(100), 1.24f);
}

static void test_state_names() {
  CHECK_STR(ow::stateName(3), "RIDING");
  CHECK_STR(ow::stateName(0x42), "?");
}

static void test_screen_placeholders_before_data() {
  ow::Telemetry t;
  ow::Screen s = ow::format(t, ow::Link::Scanning);
  CHECK_STR(s.speed, "--");
  CHECK_STR(s.battery, "--%");
  CHECK_STR(s.battTemp, "--C");
  CHECK_STR(s.pitch, "--");
  CHECK_STR(s.temps, "--/--C");
  CHECK_STR(s.status, "SCANNING");
}

static void test_screen_live() {
  ow::Telemetry t;
  apply(t, ow::RPM, "0320");               // 800 rpm → 9.9 mph
  apply(t, ow::BATTERY_REMAINING, "4b");   // 75 %
  apply(t, ow::BATTERY_TEMP, "191d");      // 25 °C
  apply(t, ow::PITCH, "06f2");             // -2.2°
  apply(t, ow::TEMPERATURE, "1e1c");       // motor 30, controller 28
  apply(t, ow::STATUS_ERROR, "0003");
  ow::Screen s = ow::format(t, ow::Link::Live);
  CHECK_STR(s.speed, "9.9");
  CHECK_STR(s.battery, "75%");
  CHECK_STR(s.battTemp, "25C");
  CHECK_STR(s.pitch, "-2.2");
  CHECK_STR(s.temps, "28/30C");
  CHECK_STR(s.status, "RIDING");
}

static void test_screen_link_overrides_board_state() {
  ow::Telemetry t;
  apply(t, ow::STATUS_ERROR, "0003");
  CHECK_STR(ow::format(t, ow::Link::Stale).status, "NO DATA");
  CHECK_STR(ow::format(t, ow::Link::Unlocking).status, "UNLOCKING");
}

static void test_screen_fault_flag_shown() {
  ow::Telemetry t;
  apply(t, ow::STATUS_ERROR, "0403");
  CHECK_STR(ow::format(t, ow::Link::Live).status, "FAULT 04");
}

static void test_screen_equality_drives_redraw() {
  ow::Telemetry t;
  apply(t, ow::RPM, "0320");
  ow::Screen a = ow::format(t, ow::Link::Live);
  apply(t, ow::RPM, "0321");   // 801 rpm still rounds to 9.9
  CHECK(a == ow::format(t, ow::Link::Live));
  apply(t, ow::RPM, "0340");
  CHECK(!(a == ow::format(t, ow::Link::Live)));
}

static void test_refresh_policy() {
  ow::RefreshPolicy p(1000, 3);
  CHECK(p.decide(0, true) == ow::Refresh::Full);        // first frame is always full
  CHECK(p.decide(500, true) == ow::Refresh::None);      // rate-limited
  CHECK(p.decide(1000, false) == ow::Refresh::None);    // nothing changed
  CHECK(p.decide(1000, true) == ow::Refresh::Partial);
  CHECK(p.decide(2000, true) == ow::Refresh::Partial);
  CHECK(p.decide(3000, true) == ow::Refresh::Partial);
  CHECK(p.decide(4000, true) == ow::Refresh::Full);     // every 3 partials → full
  p.requestFull();
  CHECK(p.decide(5000, false) == ow::Refresh::Full);    // forced even if unchanged
}

static void test_lipo_percent_curve() {
  CHECK(ow::lipoPercent(4200) == 100);
  CHECK(ow::lipoPercent(4300) == 100);   // clamps above full (USB charging)
  CHECK(ow::lipoPercent(3300) == 0);
  CHECK(ow::lipoPercent(3000) == 0);
  CHECK(ow::lipoPercent(3800) == 50);
  int mid = ow::lipoPercent(3950);       // between table points → interpolated
  CHECK(mid > 50 && mid < 80);
  CHECK(ow::lipoPercent(-1) == -1);      // unknown stays unknown
}

static void test_screen_device_battery() {
  ow::Telemetry t;
  CHECK_STR(ow::format(t, ow::Link::Live, 3800).device, "ESP 50%");
  CHECK_STR(ow::format(t, ow::Link::Live, -1).device, "ESP --%");
  CHECK_STR(ow::format(t, ow::Link::Live).device, "ESP --%");
}

static void test_indicator_spins_while_fresh() {
  ow::Telemetry t;
  ow::Screen a = ow::format(t, ow::Link::Live, -1, {0, 0, false});
  ow::Screen b = ow::format(t, ow::Link::Live, -1, {1, 0, false});
  CHECK_STR(a.indicator, "|");
  CHECK_STR(b.indicator, "/");
  CHECK(!(a == b));  // new data → visible change → redraw
}

static void test_indicator_shows_age_when_stale() {
  ow::Telemetry t;
  CHECK_STR(ow::format(t, ow::Link::Stale, -1, {5, 12, false}).indicator, "12s old");
  CHECK_STR(ow::format(t, ow::Link::Live, -1, {5, 1, false}).indicator, "/");
}

static void test_indicator_rec_marker() {
  ow::Telemetry t;
  CHECK_STR(ow::format(t, ow::Link::Live, -1, {2, 0, true}).indicator, "REC -");
  CHECK_STR(ow::format(t, ow::Link::Stale, -1, {2, 9, true}).indicator, "REC 9s old");
}

static void test_indicator_hidden_when_not_connected() {
  ow::Telemetry t;
  CHECK_STR(ow::format(t, ow::Link::Scanning, -1, {3, 40, false}).indicator, "");
}

int main() {
  test_uuid_map_matches_owce();
  test_indicator_spins_while_fresh();
  test_indicator_shows_age_when_stale();
  test_indicator_rec_marker();
  test_indicator_hidden_when_not_connected();
  test_lipo_percent_curve();
  test_screen_device_battery();
  test_decoders();
  test_apply_ignores_unknown_and_short();
  test_mph();
  test_state_names();
  test_screen_placeholders_before_data();
  test_screen_live();
  test_screen_link_overrides_board_state();
  test_screen_fault_flag_shown();
  test_screen_equality_drives_redraw();
  test_refresh_policy();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all ow_protocol tests passed\n");
  return 0;
}
