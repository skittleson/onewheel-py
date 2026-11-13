"""Shared config loading: env vars first, then a `.env` file.

`.env` is looked up in the CWD, then the repo root. Only the first `.env`
found is read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_SEARCH = (Path.cwd(), Path(__file__).resolve().parent.parent)


def load_env(key: str, search: Iterable[Path] | None = None) -> str:
    """Return `key` from the environment or the first `.env` found; '' if unset."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    for directory in DEFAULT_SEARCH if search is None else search:
        candidate = Path(directory) / ".env"
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
        break
    return ""


def parse_unlock_magic(value: str) -> bytes:
    """Validate a 40-hex-char unlock token. Raises ValueError on bad input."""
    if not value:
        raise ValueError("OW_UNLOCK_MAGIC not set")
    try:
        magic = bytes.fromhex(value)
    except ValueError as e:
        raise ValueError(f"OW_UNLOCK_MAGIC is not valid hex: {e}") from None
    if len(magic) != 20:
        raise ValueError(f"OW_UNLOCK_MAGIC must be 20 bytes, got {len(magic)}")
    return magic


def resolve_unlock_magic() -> bytes:
    """Load OW_UNLOCK_MAGIC or exit with a helpful message."""
    try:
        return parse_unlock_magic(load_env("OW_UNLOCK_MAGIC"))
    except ValueError as e:
        sys.exit(
            f"error: {e}\n"
            "  export OW_UNLOCK_MAGIC=<40-hex-chars>\n"
            "  or add OW_UNLOCK_MAGIC=<40-hex-chars> to a .env file in the repo root."
        )


def resolve_address(cli_value: str | None) -> str:
    """CLI value, else OW_ADDRESS from env/.env, else exit."""
    addr = cli_value or load_env("OW_ADDRESS")
    if not addr:
        sys.exit(
            "error: no board address. Pass --address, or set OW_ADDRESS\n"
            "  (export OW_ADDRESS=XX:XX:XX:XX:XX:XX or add it to .env)."
        )
    return addr
