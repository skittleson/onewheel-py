import pytest

import ow_env


def test_env_var_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OW_ADDRESS=AA:AA:AA:AA:AA:AA\n")
    monkeypatch.setenv("OW_ADDRESS", "BB:BB:BB:BB:BB:BB")
    assert ow_env.load_env("OW_ADDRESS", search=[tmp_path]) == "BB:BB:BB:BB:BB:BB"


def test_reads_dotenv_and_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("OW_ADDRESS", raising=False)
    (tmp_path / ".env").write_text("# comment\nOW_ADDRESS='AA:BB:CC:DD:EE:FF'\n")
    assert ow_env.load_env("OW_ADDRESS", search=[tmp_path]) == "AA:BB:CC:DD:EE:FF"


def test_missing_key_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("OW_ADDRESS", raising=False)
    assert ow_env.load_env("OW_ADDRESS", search=[tmp_path]) == ""


def test_first_existing_dotenv_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("OW_ADDRESS", raising=False)
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / ".env").write_text("OTHER=1\n")
    (second / ".env").write_text("OW_ADDRESS=AA:AA:AA:AA:AA:AA\n")
    assert ow_env.load_env("OW_ADDRESS", search=[first, second]) == ""


def test_parse_unlock_magic_accepts_20_bytes():
    assert ow_env.parse_unlock_magic("00" * 20) == bytes(20)


@pytest.mark.parametrize("value", ["", "zz" * 20, "00" * 19])
def test_parse_unlock_magic_rejects_bad_values(value):
    with pytest.raises(ValueError):
        ow_env.parse_unlock_magic(value)


def test_resolve_address_prefers_cli(monkeypatch):
    monkeypatch.setenv("OW_ADDRESS", "BB:BB:BB:BB:BB:BB")
    assert ow_env.resolve_address("AA:AA:AA:AA:AA:AA") == "AA:AA:AA:AA:AA:AA"


def test_resolve_address_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OW_ADDRESS", "BB:BB:BB:BB:BB:BB")
    assert ow_env.resolve_address(None) == "BB:BB:BB:BB:BB:BB"


def test_resolve_address_exits_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("OW_ADDRESS", raising=False)
    monkeypatch.setattr(ow_env, "DEFAULT_SEARCH", (tmp_path,))
    with pytest.raises(SystemExit):
        ow_env.resolve_address(None)
