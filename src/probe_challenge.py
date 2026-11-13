"""Minimal probe: subscribe ONLY to the UART challenge characteristic and wait.
Some firmware revs only emit the challenge once a CCCD-enable lands on f3fe;
others want a read on a protected characteristic to provoke it. We try both.

Usage:
    python src/probe_challenge.py [ADDRESS]   # default: OW_ADDRESS from env/.env
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient

from ow_env import resolve_address
from ow_uuids import (
    BATTERY_REMAINING,
    BATTERY_VOLTAGE,
    FIRMWARE_REVISION,
    HARDWARE_REVISION,
    UART_SERIAL_READ,
)


async def probe(address: str) -> None:
    loop = asyncio.get_running_loop()
    seen = []

    def on_data(_s, data):
        ts = loop.time()
        seen.append((ts, bytes(data)))
        print(f"  [{ts:.3f}] f3fe ← {bytes(data).hex()}  ({len(data)} bytes)", flush=True)

    async with BleakClient(address) as c:
        print(f"connected, mtu={c.mtu_size}")
        print("subscribing to f3fe (UART challenge)…")
        await c.start_notify(UART_SERIAL_READ, on_data)
        print("waiting 5s for unsolicited challenge…")
        await asyncio.sleep(5)

        print("\npoking protected characteristics to provoke challenge:")
        for uuid, name in [
            (HARDWARE_REVISION, "HardwareRevision"),
            (FIRMWARE_REVISION, "FirmwareRevision"),
            (BATTERY_REMAINING, "BatteryRemaining"),
            (BATTERY_VOLTAGE,   "BatteryVoltage"),
        ]:
            try:
                v = await c.read_gatt_char(uuid)
                print(f"  read {name}: {v.hex()}")
            except Exception as e:
                print(f"  read {name}: ERR {e}")
            await asyncio.sleep(2)

        print("\nwaiting another 15s for late challenge…")
        await asyncio.sleep(15)

        print(f"\ntotal challenges received: {len(seen)}")


def main() -> None:
    asyncio.run(probe(resolve_address(sys.argv[1] if len(sys.argv) > 1 else None)))


if __name__ == "__main__":
    main()
