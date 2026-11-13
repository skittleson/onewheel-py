import importlib.util
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "firmware" / "ow-epaper" / "tools" / "gen_secrets.py"


def load():
    spec = importlib.util.spec_from_file_location("gen_secrets", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_renders_address_and_token_bytes():
    out = load().render("AA:BB:CC:DD:EE:FF", bytes(range(20)))
    assert '#define OW_ADDRESS "aa:bb:cc:dd:ee:ff"' in out
    assert "0x00, 0x01, 0x02" in out and "0x13 }" in out
    assert "OW_UNLOCK_MAGIC_LEN 20" in out


@pytest.mark.parametrize("addr", ["", "AA:BB", "zz:bb:cc:dd:ee:ff"])
def test_rejects_bad_address(addr):
    with pytest.raises(ValueError):
        load().render(addr, bytes(20))
