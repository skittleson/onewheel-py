"""Watch the ow-epaper ESP32 in real time over BLE from the laptop.

The ESP32 firmware (firmware/ow-epaper) advertises as "ow-epaper" and
notifies a CSV row twice a second: its link state to the Onewheel, how fresh
the data is, the notification rate, whether it's recording to SD, and the
latest telemetry. Use this to confirm the device is working while you ride.

Usage:
    python src/epaper_monitor.py              # scan for "ow-epaper"
    python src/epaper_monitor.py --address 70:04:1D:D7:AE:86
    python src/epaper_monitor.py --csv feed.csv
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

DEVICE_NAME = "ow-epaper"
LIVE_SERVICE_UUID = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0001"
LIVE_ROW_UUID = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0002"
LIVE_HEADER_UUID = "6f77e000-5a1e-4c0e-9b6e-0b3e7a1d0003"
TEXT_FIELDS = {"link"}


def parse_header(text: str) -> list[str]:
    return text.strip().split(",")


def _convert(name: str, raw: str) -> object:
    if name in TEXT_FIELDS:
        return raw
    if raw == "":
        return None
    if name == "recording":
        return raw == "1"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def parse_row(header: list[str], text: str) -> dict[str, object]:
    """Map a notified CSV row onto the header. Missing trailing cells → None."""
    cells = text.strip().split(",")
    return {name: _convert(name, cells[i]) if i < len(cells) else None
            for i, name in enumerate(header)}


def _fmt(value: object, spec: str = "") -> str:
    return "?" if value is None else format(value, spec)


def render(row: dict[str, object]) -> str:
    age = row.get("data_age_ms")
    esp = row.get("esp_mv")
    return (
        f"\r{_fmt(row.get('link')):<10} "
        f"age={'?' if age is None else f'{age / 1000:.1f}s':<6} "
        f"n/s={_fmt(row.get('notify_per_s')):<4} "
        f"{'REC' if row.get('recording') else '   '}  "
        f"mph={_fmt(row.get('mph'))}  "
        f"pitch={_fmt(row.get('pitch_deg'))}  "
        f"batt={_fmt(row.get('batt_pct'))}%  "
        f"battT={_fmt(row.get('batt_c'))}C  "
        f"amps={_fmt(row.get('amps'))}  "
        f"temps={_fmt(row.get('ctrl_c'))}/{_fmt(row.get('motor_c'))}C  "
        f"state={_fmt(row.get('state'))}  "
        f"esp={'?' if esp is None else f'{esp / 1000:.2f}V'}   "
    )


async def find_address(timeout: float) -> str:
    print(f"Scanning {timeout:.0f}s for {DEVICE_NAME}…")
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)
    if dev is None:
        sys.exit(f"error: no '{DEVICE_NAME}' advertisement seen — is the ESP32 powered "
                 "and running the ow-epaper firmware?")
    return dev.address


async def run(address: str | None, csv_path: Path | None, scan_timeout: float) -> None:
    address = address or await find_address(scan_timeout)
    csv_file = csv_path.open("w") if csv_path else None
    try:
        async with BleakClient(address) as client:
            header_text = bytes(await client.read_gatt_char(LIVE_HEADER_UUID)).decode()
            header = parse_header(header_text)
            print(f"Connected to {address}. mtu={client.mtu_size}")
            if csv_file:
                csv_file.write("pc_time," + header_text)
            last = [time.monotonic()]

            def on_row(_sender, data: bytearray) -> None:
                text = bytes(data).decode(errors="replace")
                last[0] = time.monotonic()
                if csv_file:
                    csv_file.write(f"{time.time():.3f},{text.strip()}\n")
                sys.stdout.write(render(parse_row(header, text)))
                sys.stdout.flush()

            await client.start_notify(LIVE_ROW_UUID, on_row)
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            while not stop.is_set() and client.is_connected:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.monotonic() - last[0] > 3:
                        sys.stdout.write(f"\r[no feed for {time.monotonic() - last[0]:.0f}s]   ")
                        sys.stdout.flush()
            print("\nStopping…" if stop.is_set() else "\nDisconnected.")
    finally:
        if csv_file:
            csv_file.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--address", help="ESP32 BLE address (default: scan for 'ow-epaper')")
    p.add_argument("--csv", type=Path, help="also log every feed row to this CSV")
    p.add_argument("--scan-timeout", type=float, default=10.0)
    args = p.parse_args(argv)
    asyncio.run(run(args.address, args.csv, args.scan_timeout))


if __name__ == "__main__":
    main()
