"""All Onewheel BLE GATT UUIDs and per-characteristic decoders.

UUID base: e659fXXX-ea98-11e3-ac10-0800200c9a66
Every characteristic on the board shares this base; only the 4-digit prefix
(`XXX`) changes. Helper `c(prefix)` builds the full 128-bit UUID.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Optional


def c(prefix: str) -> str:
    """Build a full Onewheel characteristic UUID from its 4-hex-digit prefix."""
    return f"e659f{prefix.lower()}-ea98-11e3-ac10-0800200c9a66"


SERVICE_UUID = c("300")  # primary OW service

# --- Characteristic UUIDs ---------------------------------------------------
SERIAL_NUMBER       = c("301")
RIDING_MODE         = c("302")
BATTERY_REMAINING   = c("303")
BATTERY_LOW_5       = c("304")
BATTERY_LOW_20      = c("305")
BATTERY_SERIAL      = c("306")
PITCH               = c("307")
ROLL                = c("308")
YAW                 = c("309")
TRIP_ODOMETER       = c("30a")
RPM                 = c("30b")
LIGHTING_MODE       = c("30c")
LIGHTS_FRONT        = c("30d")
LIGHTS_BACK         = c("30e")
STATUS_ERROR        = c("30f")
TEMPERATURE         = c("310")
BATTERY_TEMP        = c("311")
BATTERY_VOLTAGE     = c("312")
SAFETY_HEADROOM     = c("313")
HARDWARE_REVISION   = c("314")
FIRMWARE_REVISION   = c("315")
LIFETIME_ODOMETER   = c("316")
LIFETIME_AMP_HOURS  = c("317")
BATTERY_CELLS       = c("318")
LAST_ERROR_CODE     = c("319")
STANCE_FOOTPADS     = c("31b")
CURRENT_AMPS        = c("320")
UART_SERIAL_READ    = c("3fe")  # board → app challenge (notify)
UART_SERIAL_WRITE   = c("3ff")  # app → board response  (write)


# --- Hardware-revision → model lookup --------------------------------------
# From community reverse-engineering. Not exhaustive; unknown values fall
# through as "Unknown".
HW_MODELS = {
    0x0001: "V1 (original)",
    0x0002: "V1 (original)",
    0x0003: "Onewheel+",
    0x0004: "Onewheel+",
    0x0005: "Onewheel+ XR",
    0x0006: "Onewheel Pint",
    0x0007: "Onewheel Pint X",
    0x0008: "Onewheel GT",
    0x0009: "Onewheel GT S",
}


# --- Decoders ---------------------------------------------------------------
def _u16(b: bytes) -> int:
    return struct.unpack(">H", b[:2])[0]


def _i16(b: bytes) -> int:
    return struct.unpack(">h", b[:2])[0]


def decode_pitch_roll_yaw(b: bytes) -> float:
    """Pint/Pint X/GT firmware uses 1800 as the zero offset.
    raw = degrees * 10 + 1800, so degrees = (raw - 1800) / 10.
    Empirically verified on a Pint S: pitch raw 0x0709 (=1801) during
    a near-level engagement decodes to +0.1°.
    """
    raw = _u16(b)
    return (raw - 1800) / 10.0


def decode_battery_pct(b: bytes) -> int:
    return b[0] if len(b) == 1 else _u16(b)


def decode_rpm(b: bytes) -> int:
    return _u16(b)


def rpm_to_mph(rpm: int) -> float:
    """Wheel circumference ≈ 35.4" → mph ≈ rpm × 0.0124. Close enough for HUD."""
    return rpm * 0.0124


def decode_voltage(b: bytes) -> float:
    """Centivolts → volts."""
    return _u16(b) / 10.0


def decode_amps(b: bytes) -> float:
    """Signed motor amps. Scaling varies by gen; raw / 1000 lines up on XR/Pint
    in observed traces."""
    return _i16(b) / 1000.0


def decode_temperature(b: bytes) -> tuple[int, int]:
    """Returns (controller_C, motor_C). Two uint8s packed in one uint16."""
    return b[0], b[1] if len(b) > 1 else 0


def decode_status_error(b: bytes) -> dict:
    """Bitfield. Low byte = state machine, high byte = error/fault flags."""
    raw = _u16(b)
    state = raw & 0xFF
    err = (raw >> 8) & 0xFF
    states = {
        0x00: "Init",
        0x01: "BoardOff",
        0x02: "BoardOn",
        0x03: "Riding",
        0x04: "Charging",
        0x05: "ChargedFully",
        0x06: "ShuttingDown",
    }
    return {
        "raw": raw,
        "state": states.get(state, f"State_{state:#04x}"),
        "error_flags": err,
    }


def decode_footpads(b: bytes) -> str:
    return {0: "off", 1: "front-only", 2: "both", 3: "back-only"}.get(
        b[0] if b else -1, "unknown"
    )


def decode_hw_rev(b: bytes) -> str:
    rev = _u16(b)
    return f"{rev:#06x} ({HW_MODELS.get(rev, 'Unknown')})"


def decode_fw_rev(b: bytes) -> str:
    rev = _u16(b)
    return f"{rev} ({rev >> 8}.{rev & 0xFF})"


@dataclass
class Field:
    uuid: str
    name: str
    decoder: Callable[[bytes], object]
    unit: str = ""
    notify: bool = False


# Convenience table for read_static.py / live_monitor.py to iterate.
FIELDS: list[Field] = [
    Field(SERIAL_NUMBER,     "SerialNumber",     _u16, "", False),
    Field(HARDWARE_REVISION, "HardwareRevision", decode_hw_rev, "", False),
    Field(FIRMWARE_REVISION, "FirmwareRevision", decode_fw_rev, "", False),
    Field(BATTERY_REMAINING, "BatteryRemaining", decode_battery_pct, "%", True),
    Field(BATTERY_VOLTAGE,   "BatteryVoltage",   decode_voltage, "V", True),
    Field(BATTERY_TEMP,      "BatteryTemp",      lambda b: b[0], "°C", False),
    Field(BATTERY_LOW_5,     "BatteryLow5",      lambda b: bool(b[0]), "", True),
    Field(BATTERY_LOW_20,    "BatteryLow20",     lambda b: bool(b[0]), "", True),
    Field(TEMPERATURE,       "Temperature",      decode_temperature, "°C", True),
    Field(PITCH,             "Pitch",            decode_pitch_roll_yaw, "°", True),
    Field(ROLL,              "Roll",             decode_pitch_roll_yaw, "°", True),
    Field(YAW,               "Yaw",              decode_pitch_roll_yaw, "°", True),
    Field(RPM,               "RPM",              decode_rpm, "rpm", True),
    Field(CURRENT_AMPS,      "CurrentAmps",      decode_amps, "A", True),
    Field(TRIP_ODOMETER,     "TripOdometer",     _u16, "rev", True),
    Field(LIFETIME_ODOMETER, "LifetimeOdometer", _u16, "", False),
    Field(LIFETIME_AMP_HOURS,"LifetimeAmpHours", _u16, "Ah", False),
    Field(LAST_ERROR_CODE,   "LastErrorCode",    _u16, "", False),
    Field(STATUS_ERROR,      "StatusError",      decode_status_error, "", True),
    Field(STANCE_FOOTPADS,   "StanceFootpads",   decode_footpads, "", True),
    Field(SAFETY_HEADROOM,   "SafetyHeadroom",   lambda b: b[0], "%", True),
    Field(RIDING_MODE,       "RidingMode",       lambda b: b[0], "", False),
]

# Quick reverse lookup: uuid → Field
FIELDS_BY_UUID: dict[str, Field] = {f.uuid: f for f in FIELDS}
