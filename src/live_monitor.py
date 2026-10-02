"""Live telemetry stream from a Onewheel — subscribes to every notify-capable
characteristic and prints a rolling dashboard.

On Onewheel V1 / + this works fully out of the box.
On Onewheel+ XR this works — pass `--unlock-xr` to send the GemAuth response
so the board doesn't throttle after ~30 s.
On Pint / Pint X / GT / GT S you'll get partial data until you wire in the
matching unlock (see docs/references.md).

Usage:
    python src/live_monitor.py --address XX:XX:XX:XX:FD:B0
    python src/live_monitor.py --address XX:XX:XX:XX:FD:B0 --unlock-xr
    python src/live_monitor.py --address XX:XX:XX:XX:FD:B0 --csv ride.csv
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

from ow_uuids import (
    FIELDS,
    FIELDS_BY_UUID,
    SERVICE_UUID,
    UART_SERIAL_READ,
    UART_SERIAL_WRITE,
    rpm_to_mph,
)
from unlock_xr import xr_response


class LiveState:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.last_update: dict[str, float] = {}
        self.t0 = time.time()

    def update(self, name: str, value: object) -> None:
        self.values[name] = value
        self.last_update[name] = time.time()

    def render(self) -> str:
        v = self.values
        rpm = v.get("RPM", 0)
        mph = rpm_to_mph(rpm) if isinstance(rpm, (int, float)) else 0
        return (
            f"\rt={time.time() - self.t0:6.1f}s  "
            f"batt={v.get('BatteryRemaining', '?'):>3}%  "
            f"V={v.get('BatteryVoltage', '?'):<5}  "
            f"rpm={rpm:>4}({mph:4.1f}mph)  "
            f"pitch={v.get('Pitch', '?'):>6}°  "
            f"roll={v.get('Roll', '?'):>6}°  "
            f"A={v.get('CurrentAmps', '?'):>6}  "
            f"T={v.get('Temperature', '?')}  "
            f"foot={v.get('StanceFootpads', '?')}  "
            f"state={v.get('StatusError', {}).get('state', '?') if isinstance(v.get('StatusError'), dict) else '?'}"
        )


async def run(address: str, unlock_xr: bool, csv_path: Path | None) -> None:
    state = LiveState()
    csv_writer = None
    csv_file = None
    if csv_path:
        csv_file = csv_path.open("w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["t", "field", "value", "raw_hex"])

    async with BleakClient(address) as client:
        svc = client.services.get_service(SERVICE_UUID)
        if not svc:
            print(f"OW service not found on {address}", file=sys.stderr)
            return

        print(f"Connected to {address}. mtu={client.mtu_size}")
        print("Subscribing to notifications…")

        char_uuids = {ch.uuid: ch for ch in svc.characteristics}

        def make_handler(uuid: str):
            field = FIELDS_BY_UUID.get(uuid)

            def handler(_sender, data: bytearray) -> None:
                raw = bytes(data)
                if field:
                    try:
                        val = field.decoder(raw)
                    except Exception:
                        val = raw.hex()
                    state.update(field.name, val)
                    if csv_writer:
                        csv_writer.writerow(
                            [f"{time.time() - state.t0:.3f}", field.name, val, raw.hex()]
                        )
                sys.stdout.write(state.render())
                sys.stdout.flush()

            return handler

        for f in FIELDS:
            ch = char_uuids.get(f.uuid)
            if ch is None or "notify" not in ch.properties:
                continue
            try:
                await client.start_notify(f.uuid, make_handler(f.uuid))
            except Exception as exc:
                print(f"  start_notify({f.name}) failed: {exc}", file=sys.stderr)

        # Subscribe to UART challenge so we can react.
        if UART_SERIAL_READ in char_uuids:
            async def on_challenge(_sender, data: bytearray) -> None:
                challenge = bytes(data)
                print(f"\n[unlock] challenge received: {challenge.hex()}", file=sys.stderr)
                if unlock_xr:
                    resp = xr_response(challenge)
                    print(f"[unlock] sending XR response:  {resp.hex()}", file=sys.stderr)
                    try:
                        await client.write_gatt_char(UART_SERIAL_WRITE, resp, response=True)
                        print("[unlock] response written.", file=sys.stderr)
                    except Exception as exc:
                        print(f"[unlock] write failed: {exc}", file=sys.stderr)
                else:
                    print("[unlock] --unlock-xr not set; ignoring challenge "
                          "(board may stop streaming on XR/Pint/GT).", file=sys.stderr)

            await client.start_notify(UART_SERIAL_READ, on_challenge)

        # Run until Ctrl-C.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        print("\nStopping…")

    if csv_file:
        csv_file.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--address", required=True)
    p.add_argument("--unlock-xr", action="store_true",
                   help="answer the GemAuth challenge using the XR algorithm")
    p.add_argument("--csv", type=Path, help="log every notification to this CSV file")
    args = p.parse_args()
    asyncio.run(run(args.address, args.unlock_xr, args.csv))


if __name__ == "__main__":
    main()
