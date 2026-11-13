import pytest

import ow_uuids as ow


def b(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


@pytest.mark.parametrize("raw,deg", [
    ("0708", 0.0),     # 1800 = level
    ("0709", 0.1),     # verified on Pint S: near-level engagement
    ("06e8", -3.2),
    ("07c8", 19.2),
])
def test_pitch_roll_yaw_uses_1800_offset(raw, deg):
    assert ow.decode_pitch_roll_yaw(b(raw)) == pytest.approx(deg)


def test_battery_pct_single_byte_and_u16():
    assert ow.decode_battery_pct(b("55")) == 85
    assert ow.decode_battery_pct(b("0055")) == 85


def test_voltage_is_tenths_of_volts():
    assert ow.decode_voltage(b("0258")) == pytest.approx(60.0)


def test_voltage_zero_means_locked():
    # f316 reads 0x0000 on Pint S firmware even for the official app.
    assert ow.decode_voltage(b("0000")) is None


def test_amps_signed_pint_scale():
    # OWCE: Pint/Pint X/GT current scale is 0.002 A per count, two's complement.
    assert ow.decode_amps(b("01f4")) == pytest.approx(1.0)
    assert ow.decode_amps(b("fe0c")) == pytest.approx(-1.0)


def test_temperature_is_controller_then_motor_from_motor_first_bytes():
    # OWCE: data[0] = motor, data[1] = controller. We return (controller, motor).
    assert ow.decode_temperature(b("1e1c")) == (28, 30)
    assert ow.decode_temperature(b("1c")) == (None, 28)


def test_battery_temp_is_first_byte():
    assert ow.decode_battery_temp(b("191d")) == 25


def test_status_error_splits_state_and_flags():
    assert ow.decode_status_error(b("0103")) == {"raw": 0x0103, "state": "Riding", "error_flags": 1}
    assert ow.decode_status_error(b("0042"))["state"] == "State_0x42"


def test_firmware_revision_is_plain_number():
    assert ow.decode_fw_rev(b("1450")) == 5200


@pytest.mark.parametrize("raw,model", [
    ("1c9b", "Pint X / Pint S"),   # 7323: this board
    ("1450", "Pint"),
    ("1194", "XR"),
    ("0bb8", "Plus"),
    ("0001", "V1"),
    ("1770", "GT"),
    ("0000", "Unknown"),
])
def test_hardware_revision_ranges(raw, model):
    assert ow.decode_hw_rev(b(raw)) == f"{int(raw, 16)} ({model})"


def test_uuid_map_matches_owce_from_f310():
    assert ow.TEMPERATURE == ow.c("310")
    assert ow.FIRMWARE_REVISION == ow.c("311")
    assert ow.CURRENT_AMPS == ow.c("312")
    assert ow.BATTERY_TEMP == ow.c("315")
    assert ow.BATTERY_VOLTAGE == ow.c("316")
    assert ow.SAFETY_HEADROOM == ow.c("317")
    assert ow.HARDWARE_REVISION == ow.c("318")
    assert ow.BATTERY_CELLS == ow.c("31b")
    assert ow.LAST_ERROR_CODE == ow.c("31c")
    assert not hasattr(ow, "STANCE_FOOTPADS")


def test_uuid_builder_and_lookup_table():
    assert ow.c("30B") == "e659f30b-ea98-11e3-ac10-0800200c9a66"
    assert ow.FIELDS_BY_UUID[ow.RPM].name == "RPM"
    assert len(ow.FIELDS_BY_UUID) == len(ow.FIELDS)
