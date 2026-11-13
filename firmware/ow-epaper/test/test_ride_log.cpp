// Host-side tests for ride_log.h. Run via: make -C firmware/ow-epaper test
#include <cmath>
#include <cstdio>
#include <cstring>
#include <initializer_list>

#include "../ride_log.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)
#define CHECK_STR(a, b) do { if (std::strcmp((a), (b)) != 0) { std::printf("FAIL %s:%d  \"%s\" != \"%s\"\n", __FILE__, __LINE__, (a), (b)); failures++; } } while (0)

static void test_header_matches_row_columns() {
  ow::Telemetry t;
  char row[ridelog::ROW_MAX];
  ridelog::formatRow(row, sizeof row, 0, t, -1);
  int headerCommas = 0, rowCommas = 0;
  for (const char* p = ridelog::HEADER; *p; p++) headerCommas += *p == ',';
  for (const char* p = row; *p; p++) rowCommas += *p == ',';
  CHECK(headerCommas == rowCommas);
  CHECK(ridelog::HEADER[std::strlen(ridelog::HEADER) - 1] == '\n');
  CHECK(row[std::strlen(row) - 1] == '\n');
}

static void test_unknown_values_are_empty_cells() {
  ow::Telemetry t;
  char row[ridelog::ROW_MAX];
  ridelog::formatRow(row, sizeof row, 1500, t, -1);
  CHECK_STR(row, "1.500,,,,,,,,,,,,0,\n");
}

static void test_live_row() {
  ow::Telemetry t;
  t.rpm = 800;
  t.pitch = -2.2f;
  t.roll = 0.5f;
  t.batteryPct = 75;
  t.batteryTemp = 25;
  t.amps = -1.25f;
  t.controllerTemp = 28;
  t.motorTemp = 30;
  t.state = 3;
  t.errorFlags = 4;
  char row[ridelog::ROW_MAX];
  ridelog::formatRow(row, sizeof row, 62345, t, 4166);
  CHECK_STR(row, "62.345,800,9.92,-2.2,0.5,75,25,-1.25,,28,30,3,4,4166\n");
}

static void test_file_names_round_trip() {
  char name[32];
  ridelog::fileName(name, sizeof name, 7);
  CHECK_STR(name, "/rides/ride_0007.csv");
  CHECK(ridelog::parseIndex("ride_0007.csv") == 7);
  CHECK(ridelog::parseIndex("/rides/ride_0123.csv") == 123);
  CHECK(ridelog::parseIndex("notes.txt") == -1);
  CHECK(ridelog::parseIndex("ride_abcd.csv") == -1);
  CHECK(ridelog::parseIndex("ride_0001.csv.tmp") == -1);
}

static void test_live_row_prefixes_link_state() {
  ow::Telemetry t;
  t.rpm = 800;
  char row[ridelog::LIVE_ROW_MAX];
  ridelog::formatLive(row, sizeof row, ow::Link::Live, 120, 455, true, 62345, t, 4166);
  CHECK_STR(row, "LIVE,120,455,1,62.345,800,9.92,,,,,,,,,,0,4166\n");
  int headerCommas = 0, rowCommas = 0;
  for (const char* p = ridelog::LIVE_HEADER; *p; p++) headerCommas += *p == ',';
  for (const char* p = row; *p; p++) rowCommas += *p == ',';
  CHECK(headerCommas == rowCommas);
}

static void test_link_names() {
  CHECK_STR(ridelog::linkName(ow::Link::Scanning), "SCANNING");
  CHECK_STR(ridelog::linkName(ow::Link::Stale), "STALE");
}

static void test_truncation_stays_in_bounds() {
  // Widest possible row into a deliberately small buffer: nothing may be
  // written past `size`, and the result must still be NUL-terminated.
  ow::Telemetry t;
  t.rpm = 65535; t.pitch = -180.0f; t.roll = -180.0f;
  t.batteryPct = 100; t.batteryTemp = 255;
  t.amps = -32.768f; t.voltage = 6553.5f;
  t.controllerTemp = 255; t.motorTemp = 255; t.state = 255; t.errorFlags = 255;
  char buf[128];
  for (size_t size : {2u, 8u, 16u, 48u, 80u}) {
    std::memset(buf, '#', sizeof buf);
    ridelog::formatRow(buf, size, 1234567UL, t, 4200);
    for (size_t i = size; i < sizeof buf; i++) CHECK(buf[i] == '#');
    CHECK(std::strlen(buf) < size);
  }
  // formatLive's huge age prefix must not push formatRow past the row either.
  std::memset(buf, '#', sizeof buf);
  ridelog::formatLive(buf, 48, ow::Link::Live, 999999999UL, 99999UL, true, 62345, t, 4200);
  for (size_t i = 48; i < sizeof buf; i++) CHECK(buf[i] == '#');
}

int main() {
  test_live_row_prefixes_link_state();
  test_link_names();
  test_header_matches_row_columns();
  test_unknown_values_are_empty_cells();
  test_live_row();
  test_file_names_round_trip();
  test_truncation_stays_in_bounds();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all ride_log tests passed\n");
  return 0;
}
