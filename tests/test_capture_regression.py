"""Regression check: decode real notifications captured from a Pint S ride.

Fixture: tests/fixtures/ride_10s_sample.csv — raw bytes only (no f3fe UART
stream), extracted from captures/ride_10s.csv.
"""

import csv
from collections import defaultdict
from pathlib import Path

import live_monitor as lm

FIXTURE = Path(__file__).parent / "fixtures" / "ride_10s_sample.csv"


def decoded():
    out = defaultdict(list)
    with FIXTURE.open() as f:
        for row in csv.DictReader(f):
            name, val = lm.decode_notification(row["uuid"], bytes.fromhex(row["raw_hex"]))
            out[name].append(val)
    return out


def test_fixture_covers_live_fields():
    assert {"Pitch", "Roll", "RPM"} <= decoded().keys()


def test_pitch_and_roll_are_plausible_degrees():
    d = decoded()
    for name in ("Pitch", "Roll"):
        assert all(isinstance(v, float) and -45 <= v <= 45 for v in d[name]), name


def test_rpm_is_plausible_for_a_short_ride():
    assert all(isinstance(v, int) and 0 <= v <= 1000 for v in decoded()["RPM"])


def test_temperatures_match_board():
    d = decoded()
    assert d["BatteryTemp"] == [25]          # f315 191c: was misread as firmware "25.28"
    assert d["Temperature"] == [(28, 28)]


def test_full_ride_renders_without_error():
    values = {}
    with FIXTURE.open() as f:
        for i, row in enumerate(csv.DictReader(f)):
            name, val = lm.decode_notification(row["uuid"], bytes.fromhex(row["raw_hex"]))
            values[name] = val
            lm.render(values, elapsed=float(row["t"]), count=i)
