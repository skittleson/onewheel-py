"""Unlock + live monitor for a Pint / Pint S.

Thin wrapper kept for muscle memory — equivalent to:

    python src/live_monitor.py --unlock pint [--address ...] [--csv ride.csv]

The unlock token is a static per-board signature extracted from a
btsnoop_hci.log capture of the official app (see README). Token + MAC live in
.env as OW_UNLOCK_MAGIC / OW_ADDRESS.
"""

from __future__ import annotations

import sys

from live_monitor import main as live_main


def main(argv: list[str] | None = None) -> None:
    live_main(["--unlock", "pint", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    main()
