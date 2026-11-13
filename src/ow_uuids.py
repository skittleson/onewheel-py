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
# f301–f30f are long-standing community names. f310 onward follow OWCE
# (github.com/OnewheelCommunityEdition/OWCE_App, OWBoard.cs), confirmed against
# the official app's reads in captures/snoop_decoded.txt.
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
TEMPERATURE         = c("310")  # [0] motor °C, [1] controller °C
FIRMWARE_REVISION   = c("311")
CURRENT_AMPS        = c("312")
TRIP_AMP_HOURS      = c("313")
TRIP_REGEN_AMP_HOURS = c("314")
BATTERY_TEMP        = c("315")  # [0] °C on Pint / XR / GT
BATTERY_VOLTAGE     = c("316")  # reads 0 on Pint S firmware (locked)
SAFETY_HEADROOM     = c("317")  # the unlock sequence writes 0x0001 here
HARDWARE_REVISION   = c("318")
LIFETIME_ODOMETER   = c("319")
LIFETIME_AMP_HOURS  = c("31a")
BATTERY_CELLS       = c("31b")
LAST_ERROR_CODE     = c("31c")
UART_SERIAL_READ    = c("3fe")  # board → app challenge (notify)
UART_SERIAL_WRITE   = c("3ff")  # app → board response  (write)


# --- Hardware-revision ranges → model (OWCE) --------------------------------
HW_MODEL_RANGES = [
    (1, 2999, "V1"),
    (3000, 3999, "Plus"),
    (4000, 4999, "XR"),
    (5000, 5999, "Pint"),
    (6000, 6999, "GT"),
    (7000, 7999, "Pint X / Pint S"),
]


# --- Decoders ---------------------------------------------------------------
def _u16(b: bytes) -> int:
    return struct.unpack(">H", b[:2])[0]


def _i16(b: bytes) -> int:
    return struct.unpack(">h", b[:2])[0]


def decode_pitch_roll_yaw(b: bytes) -> float:
    """Pint/Pint X/GT firmware uses 1800 as the zero offset.
    raw = degrees * 10 + 1800, so degrees = (raw - 1800) / 10.
    Empirically verified on a Pint S: pitch raw 0x0709 (=1801) during
    a near-level engagement decodes to +0.1°. (OWCE uses the opposite
    sign, (1800 - raw) / 10.)
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


def decode_voltage(b: bytes) -> Optional[float]:
    """Tenths of a volt. 0 means the firmware has locked this value → None."""
    raw = _u16(b)
    return raw / 10.0 if raw else None


def decode_amps(b: bytes) -> float:
    """Signed motor current, 0.002 A per count on Pint / Pint X / GT (OWCE).
    Negative = regen."""
    return _i16(b) * 0.002


def decode_temperature(b: bytes) -> tuple[Optional[int], int]:
    """Returns (controller_C, motor_C). Wire order is [motor, controller]."""
    return (b[1] if len(b) > 1 else None), b[0]


def decode_battery_temp(b: bytes) -> int:
    return b[0]


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


def decode_hw_rev(b: bytes) -> str:
    rev = _u16(b)
    model = next((m for lo, hi, m in HW_MODEL_RANGES if lo <= rev <= hi), "Unknown")
    return f"{rev} ({model})"


def decode_fw_rev(b: bytes) -> int:
    return _u16(b)


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
    Field(BATTERY_TEMP,      "BatteryTemp",      decode_battery_temp, "°C", True),
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
    Field(LIFETIME_AMP_HOURS,"LifetimeAmpHours", _u16, "", False),
    Field(LAST_ERROR_CODE,   "LastErrorCode",    _u16, "", True),
    Field(STATUS_ERROR,      "StatusError",      decode_status_error, "", True),
    Field(SAFETY_HEADROOM,   "SafetyHeadroom",   _u16, "", True),
    Field(RIDING_MODE,       "RidingMode",       lambda b: b[0], "", False),
]

# Quick reverse lookup: uuid → Field
FIELDS_BY_UUID: dict[str, Field] = {f.uuid: f for f in FIELDS}
