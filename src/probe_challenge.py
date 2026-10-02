"""Minimal probe: subscribe ONLY to the UART challenge characteristic and wait.
Some firmware revs only emit the challenge once a CCCD-enable lands on f3fe;
others want a read on a protected characteristic to provoke it. We try both.
"""

import asyncio
import os
import sys
from pathlib import Path
from bleak import BleakClient
from ow_uuids import (
    UART_SERIAL_READ, HARDWARE_REVISION, FIRMWARE_REVISION,
    BATTERY_REMAINING, BATTERY_VOLTAGE,
)

import os

def _addr():
    if len(sys.argv) > 1:
        return sys.argv[1]
    val = os.environ.get("OW_ADDRESS", "").strip()
    if not val:
        for p in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
            if p.is_file():
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("OW_ADDRESS="):
                        val = line.split("=", 1)[1].strip().strip("'\"")
                        break
                break
    if not val:
        sys.exit("error: OW_ADDRESS not set (env var or .env file)")
    return val

ADDR = _addr()

async def main():
    seen = []
    def on_data(_s, data):
        ts = asyncio.get_event_loop().time()
        seen.append((ts, bytes(data)))
        print(f"  [{ts:.3f}] f3fe ← {bytes(data).hex()}  ({len(data)} bytes)", flush=True)

    async with BleakClient(ADDR) as c:
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

asyncio.run(main())
