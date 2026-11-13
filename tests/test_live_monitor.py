import pytest

import live_monitor as lm
import ow_uuids as ow


def test_decode_known_field():
    assert lm.decode_notification(ow.RPM, bytes.fromhex("0010")) == ("RPM", 16)


def test_decode_unknown_uuid_uses_short_name_and_hex():
    assert lm.decode_notification(ow.c("31e"), bytes.fromhex("0301")) == ("f31e", "0301")


def test_decode_failure_falls_back_to_hex():
    # Pitch decoder needs 2 bytes; a 1-byte payload must not raise.
    assert lm.decode_notification(ow.PITCH, b"\x07") == ("Pitch", "07")


def test_render_before_any_data_shows_placeholders():
    line = lm.render({}, elapsed=1.0, count=0)
    assert "batt=  ?%" in line
    assert "rpm=   ?(  ?mph)" in line
    assert "state=?" in line


def test_render_full_state():
    values = {
        "BatteryRemaining": 85,
        "BatteryVoltage": 60.1,
        "RPM": 100,
        "Pitch": 1.5,
        "Roll": -0.3,
        "CurrentAmps": 2.5,
        "Temperature": (28, 30),
        "BatteryTemp": 25,
        "StatusError": {"raw": 3, "state": "Riding", "error_flags": 0},
    }
    line = lm.render(values, elapsed=12.3, count=42)
    assert "batt= 85%" in line
    assert "rpm= 100( 1.2mph)" in line
    assert "T=28/30°C" in line
    assert "battT=25°C" in line
    assert "foot=" not in line
    assert "state=Riding" in line
    assert "#=   42" in line


@pytest.mark.parametrize("field,value", [
    ("RPM", "beef"), ("Pitch", "07"), ("Temperature", "1c"), ("StatusError", "0003"),
    ("BatteryRemaining", None), ("CurrentAmps", (1, 2)),
])
def test_render_tolerates_fallback_values(field, value):
    lm.render({field: value}, elapsed=0.0, count=1)


def test_throttle_limits_redraw_rate():
    t = lm.Throttle(interval=0.05)
    assert t.ready(now=0.00)
    assert not t.ready(now=0.01)
    assert t.ready(now=0.06)


def test_xr_mode_answers_only_20_byte_challenges():
    challenge = b"CRX" + bytes(16) + b"\x00"
    assert len(lm.challenge_reply("xr", challenge)) == 20
    assert lm.challenge_reply("xr", b"\x4f\x6e") is None


@pytest.mark.parametrize("mode", ["none", "pint"])
def test_other_modes_never_answer_challenges(mode):
    assert lm.challenge_reply(mode, b"CRX" + bytes(17)) is None


def test_cli_unlock_xr_alias():
    args = lm.parse_args(["--address", "AA", "--unlock-xr"])
    assert args.unlock == "xr"


def test_cli_defaults():
    args = lm.parse_args([])
    assert args.address is None
    assert args.unlock == "none"
    assert args.csv is None
