"""Unlock + live monitor for a Pint S using the captured magic bytes.

The unlock token is extracted from a btsnoop_hci.log capture of the official
Onewheel app talking to the board. The same bytes appeared in two separate
sessions, so they are a static signature for this board — not a per-session
challenge. Token + MAC live in .env (OW_UNLOCK_MAGIC / OW_ADDRESS).

Sequence (matches what the app does):
  1. Read f302 (RidingMode)
  2. Read f318 (BatteryCells)
  3. Read f311 (BatteryTemp)
  4. Write 20-byte magic to f3ff
  5. Write 0x0001 to f317 (some enable bit)
  6. Periodically write 0x00 to f30b as keepalive
  7. Subscribe to all OW characteristics for streaming
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import signal
import sys
import time
from pathlib import Path

from bleak import BleakClient

from ow_uuids import (
    FIELDS, FIELDS_BY_UUID, SERVICE_UUID,
    BATTERY_TEMP, BATTERY_CELLS, RIDING_MODE,
    UART_SERIAL_WRITE, RPM, LIFETIME_AMP_HOURS,
    rpm_to_mph, c,
)


def _load_env(key: str) -> str:
    """Read a value from env or .env (repo root or CWD). Returns '' if unset."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip("'\"")
            break
    return ""


def _load_unlock_magic() -> bytes:
    val = _load_env("OW_UNLOCK_MAGIC")
    if not val:
        sys.exit(
            "error: OW_UNLOCK_MAGIC not set.\n"
            "  export OW_UNLOCK_MAGIC=<40-hex-chars>\n"
            "  or add OW_UNLOCK_MAGIC=<40-hex-chars> to a .env file in the repo root."
        )
    try:
        magic = bytes.fromhex(val)
    except ValueError as e:
        sys.exit(f"error: OW_UNLOCK_MAGIC is not valid hex: {e}")
    if len(magic) != 20:
        sys.exit(f"error: OW_UNLOCK_MAGIC must be 20 bytes, got {len(magic)}")
    return magic


def _load_address() -> str:
    addr = _load_env("OW_ADDRESS")
    if not addr:
        sys.exit(
            "error: OW_ADDRESS not set.\n"
            "  export OW_ADDRESS=XX:XX:XX:XX:XX:XX\n"
            "  or add OW_ADDRESS=XX:XX:XX:XX:XX:XX to a .env file in the repo root."
        )
    return addr


UNLOCK_MAGIC = _load_unlock_magic()
OW_ADDRESS = _load_address()
KEEPALIVE_INTERVAL_S = 0.2


async def unlock(client: BleakClient) -> None:
    print("[unlock] reading priming characteristics…")
    rm = await client.read_gatt_char(RIDING_MODE)
    bc = await client.read_gatt_char(BATTERY_CELLS)
    bt = await client.read_gatt_char(BATTERY_TEMP)
    print(f"  f302 RidingMode    = {rm.hex()}")
    print(f"  f318 BatteryCells  = {bc.hex()}")
    print(f"  f311 BatteryTemp   = {bt.hex()}")
    print(f"[unlock] writing magic to f3ff: {UNLOCK_MAGIC.hex()}")
    await client.write_gatt_char(UART_SERIAL_WRITE, UNLOCK_MAGIC, response=True)
    print("[unlock] enabling f317…")
    await client.write_gatt_char(LIFETIME_AMP_HOURS, bytes.fromhex("0001"), response=True)
    print("[unlock] done")


async def keepalive_loop(client: BleakClient, stop: asyncio.Event):
    """Write 0x00 to f30b every ~200 ms while connected."""
    while not stop.is_set():
        try:
            await client.write_gatt_char(RPM, b"\x00", response=True)
        except Exception as e:
            print(f"[keepalive] write failed: {e}", file=sys.stderr)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=KEEPALIVE_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


class State:
    def __init__(self):
        self.values: dict[str, object] = {}
        self.t0 = time.time()
        self.notify_count = 0

    def render(self):
        v = self.values
        rpm_v = v.get("RPM", 0)
        mph = rpm_to_mph(rpm_v) if isinstance(rpm_v, (int, float)) else 0
        return (
            f"\rt={time.time() - self.t0:6.1f}s  "
            f"#={self.notify_count:5d}  "
            f"batt={v.get('BatteryRemaining', '?')}%  "
            f"V={v.get('BatteryVoltage', '?')}  "
            f"rpm={rpm_v}({mph:4.1f}mph)  "
            f"pitch={v.get('Pitch', '?')}  "
            f"roll={v.get('Roll', '?')}  "
            f"A={v.get('CurrentAmps', '?')}  "
            f"foot={v.get('StanceFootpads', '?')}  "
            f"state={(v.get('StatusError') or {}).get('state', '?') if isinstance(v.get('StatusError'), dict) else '?'}"
        )


async def run(address: str, csv_path: Path | None) -> None:
    state = State()
    csv_writer = None
    csv_file = None
    if csv_path:
        csv_file = csv_path.open("w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["t", "field", "uuid", "value", "raw_hex"])

    async with BleakClient(address) as client:
        print(f"[+] connected. mtu={client.mtu_size}")
        svc = client.services.get_service(SERVICE_UUID)
        if not svc:
            print("OW service not found", file=sys.stderr)
            return

        await unlock(client)

        # Subscribe to every notify-capable OW characteristic
        chars = {ch.uuid: ch for ch in svc.characteristics}
        print(f"[+] subscribing to notifications on {len(chars)} chars…")

        def make_handler(uuid: str):
            field = FIELDS_BY_UUID.get(uuid)
            def handler(_s, data: bytearray):
                state.notify_count += 1
                raw = bytes(data)
                if field:
                    try:
                        val = field.decoder(raw)
                    except Exception:
                        val = raw.hex()
                    state.values[field.name] = val
                    name = field.name
                else:
                    val = raw.hex()
                    name = uuid[4:8]
                if csv_writer:
                    csv_writer.writerow([
                        f"{time.time() - state.t0:.3f}", name, uuid, val, raw.hex()
                    ])
                sys.stdout.write(state.render())
                sys.stdout.flush()
            return handler

        for uuid, ch in chars.items():
            if "notify" in ch.properties:
                try:
                    await client.start_notify(uuid, make_handler(uuid))
                except Exception as e:
                    print(f"  start_notify({uuid}): {e}", file=sys.stderr)

        # Keepalive task
        stop = asyncio.Event()
        ka_task = asyncio.create_task(keepalive_loop(client, stop))

        # Wait for Ctrl-C
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        print("\n[+] stopping…")
        ka_task.cancel()
        try:
            await ka_task
        except asyncio.CancelledError:
            pass

    if csv_file:
        csv_file.close()
        print(f"[+] wrote {csv_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", default=OW_ADDRESS)
    p.add_argument("--csv", type=Path)
    args = p.parse_args()
    asyncio.run(run(args.address, args.csv))


if __name__ == "__main__":
    main()
