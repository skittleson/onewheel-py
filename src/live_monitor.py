"""Live telemetry stream from a Onewheel — subscribes to every notify-capable
characteristic and prints a rolling dashboard.

Unlock modes (`--unlock`):
  none  V1 / + stream without any handshake.
  xr    Onewheel+ XR: answer the GemAuth challenge on f3fe with the MD5 solver
        so the board doesn't throttle after ~30 s.
  pint  Pint / Pint S: replay the static per-board token (OW_UNLOCK_MAGIC in
        .env), then keep the session alive. See README for how to capture it.

The board address comes from --address or OW_ADDRESS (env or .env).

Usage:
    python src/live_monitor.py --unlock pint
    python src/live_monitor.py --address XX:XX:XX:XX:FD:B0 --unlock xr
    python src/live_monitor.py --unlock pint --csv ride.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import signal
import sys
import time
from pathlib import Path

from bleak import BleakClient

from ow_env import resolve_address, resolve_unlock_magic
from ow_uuids import (
    FIELDS_BY_UUID,
    FIRMWARE_REVISION,
    HARDWARE_REVISION,
    RIDING_MODE,
    SAFETY_HEADROOM,
    RPM,
    SERVICE_UUID,
    UART_SERIAL_READ,
    UART_SERIAL_WRITE,
    rpm_to_mph,
)
from unlock_xr import xr_response

UNLOCK_MODES = ("none", "xr", "pint")
KEEPALIVE_INTERVAL_S = 0.2
RENDER_INTERVAL_S = 0.05  # notifications arrive at ~200 Hz; redraw at ~20 Hz
CSV_HEADER = ["t", "field", "uuid", "value", "raw_hex"]


def decode_notification(uuid: str, raw: bytes) -> tuple[str, object]:
    """Return (field name, decoded value). Unknown chars / decode errors → hex."""
    field = FIELDS_BY_UUID.get(uuid)
    if field is None:
        return uuid[4:8], raw.hex()
    try:
        return field.name, field.decoder(raw)
    except Exception:
        return field.name, raw.hex()


def _num(value: object, spec: str) -> str:
    """Format numbers with `spec`; anything else (fallback hex, None) as text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, spec)
    return format("?" if value is None else str(value), spec.lstrip("+").split(".")[0])


def render(values: dict[str, object], elapsed: float, count: int) -> str:
    rpm = values.get("RPM", "?")
    mph = f"{rpm_to_mph(rpm):4.1f}mph" if isinstance(rpm, int) else "  ?mph"
    temp = values.get("Temperature", "?")
    temp_s = (f"{_num(temp[0], '')}/{temp[1]}°C"
              if isinstance(temp, tuple) and len(temp) == 2 else str(temp))
    status = values.get("StatusError")
    state = status.get("state", "?") if isinstance(status, dict) else "?"
    return (
        f"\rt={elapsed:6.1f}s  "
        f"#={count:5d}  "
        f"batt={_num(values.get('BatteryRemaining', '?'), '>3')}%  "
        f"V={_num(values.get('BatteryVoltage', '?'), '<5')}  "
        f"rpm={_num(rpm, '>4')}({mph})  "
        f"pitch={_num(values.get('Pitch', '?'), '>6')}°  "
        f"roll={_num(values.get('Roll', '?'), '>6')}°  "
        f"A={_num(values.get('CurrentAmps', '?'), '>6')}  "
        f"T={temp_s}  "
        f"battT={values.get('BatteryTemp', '?')}°C  "
        f"state={state}"
    )


class Throttle:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last = float("-inf")

    def ready(self, now: float) -> bool:
        if now - self._last >= self.interval:
            self._last = now
            return True
        return False


def challenge_reply(mode: str, data: bytes) -> bytes | None:
    """Response to write to f3ff for a f3fe notification, or None."""
    if mode == "xr" and len(data) == 20:
        return xr_response(data)
    return None


async def _write_reply(client: BleakClient, reply: bytes) -> None:
    try:
        await client.write_gatt_char(UART_SERIAL_WRITE, reply, response=True)
    except Exception as e:
        print(f"\n[unlock] response write failed: {e}", file=sys.stderr)


async def unlock_pint(client: BleakClient, magic: bytes) -> None:
    """Replay the app's unlock sequence (captured from btsnoop)."""
    print("[unlock] reading priming characteristics…")
    for uuid, name in ((RIDING_MODE, "RidingMode"), (HARDWARE_REVISION, "HardwareRev"),
                       (FIRMWARE_REVISION, "FirmwareRev")):
        raw = await client.read_gatt_char(uuid)
        print(f"  {uuid[4:8]} {name:<13s}= {raw.hex()}")
    print("[unlock] writing token to f3ff")
    await client.write_gatt_char(UART_SERIAL_WRITE, magic, response=True)
    print("[unlock] enabling f317…")
    await client.write_gatt_char(SAFETY_HEADROOM, bytes.fromhex("0001"), response=True)
    print("[unlock] done")


async def keepalive_loop(client: BleakClient, stop: asyncio.Event) -> None:
    """Write 0x00 to f30b every ~200 ms while connected (Pint keeps streaming)."""
    while not stop.is_set():
        try:
            await client.write_gatt_char(RPM, b"\x00", response=True)
        except Exception as e:
            print(f"\n[keepalive] write failed: {e}", file=sys.stderr)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=KEEPALIVE_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def run(address: str, unlock: str, csv_path: Path | None,
              magic: bytes | None = None) -> None:
    values: dict[str, object] = {}
    t0 = time.time()
    count = 0
    throttle = Throttle(RENDER_INTERVAL_S)
    csv_file = csv_path.open("w", newline="") if csv_path else None
    csv_writer = csv.writer(csv_file) if csv_file else None
    if csv_writer:
        csv_writer.writerow(CSV_HEADER)

    try:
        async with BleakClient(address) as client:
            svc = client.services.get_service(SERVICE_UUID)
            if not svc:
                print(f"OW service not found on {address}", file=sys.stderr)
                return
            print(f"Connected to {address}. mtu={client.mtu_size}")

            if unlock == "pint":
                await unlock_pint(client, magic)

            def make_handler(uuid: str):
                def handler(_sender, data: bytearray) -> None:
                    nonlocal count
                    count += 1
                    raw = bytes(data)
                    name, val = decode_notification(uuid, raw)
                    values[name] = val
                    now = time.time()
                    if csv_writer:
                        csv_writer.writerow([f"{now - t0:.3f}", name, uuid, val, raw.hex()])
                    if uuid == UART_SERIAL_READ:
                        reply = challenge_reply(unlock, raw)
                        if reply is not None:
                            print(f"\n[unlock] challenge {raw.hex()} → {reply.hex()}",
                                  file=sys.stderr)
                            asyncio.ensure_future(_write_reply(client, reply))
                    if throttle.ready(now):
                        sys.stdout.write(render(values, now - t0, count))
                        sys.stdout.flush()
                return handler

            notify_chars = [ch for ch in svc.characteristics if "notify" in ch.properties]
            print(f"Subscribing to {len(notify_chars)} notify characteristics…")
            for ch in notify_chars:
                try:
                    await client.start_notify(ch.uuid, make_handler(ch.uuid))
                except Exception as exc:
                    print(f"  start_notify({ch.uuid[4:8]}) failed: {exc}", file=sys.stderr)

            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            ka_task = asyncio.create_task(keepalive_loop(client, stop)) if unlock == "pint" else None
            await stop.wait()
            print("\nStopping…")
            if ka_task:
                ka_task.cancel()
                try:
                    await ka_task
                except asyncio.CancelledError:
                    pass
    finally:
        if csv_file:
            csv_file.close()
            print(f"wrote {csv_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--address", help="board MAC (default: OW_ADDRESS from env/.env)")
    p.add_argument("--unlock", choices=UNLOCK_MODES, default="none")
    p.add_argument("--unlock-xr", dest="unlock", action="store_const", const="xr",
                   help="alias for --unlock xr")
    p.add_argument("--csv", type=Path, help="log every notification to this CSV file")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    address = resolve_address(args.address)
    magic = resolve_unlock_magic() if args.unlock == "pint" else None
    asyncio.run(run(address, args.unlock, args.csv, magic))


if __name__ == "__main__":
    main()
