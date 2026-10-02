"""One-shot read of every readable Onewheel characteristic. No notifications,
no unlock required — works on every gen for the static fields.

Usage:
    python src/read_static.py --address XX:XX:XX:XX:FD:B0
"""

from __future__ import annotations

import argparse
import asyncio

from bleak import BleakClient

from ow_uuids import FIELDS, SERVICE_UUID


async def run(address: str) -> None:
    async with BleakClient(address) as client:
        svc = client.services.get_service(SERVICE_UUID)
        if not svc:
            print(f"Onewheel service {SERVICE_UUID} not found on {address}")
            return
        print(f"Connected to {address}.\n")
        char_uuids = {ch.uuid for ch in svc.characteristics}
        for f in FIELDS:
            if f.uuid not in char_uuids:
                continue
            try:
                raw = await client.read_gatt_char(f.uuid)
                val = f.decoder(raw)
                print(f"{f.name:<22s} = {val} {f.unit}".rstrip()
                      + f"   raw={raw.hex()}")
            except Exception as exc:
                print(f"{f.name:<22s} = <{exc}>")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--address", required=True)
    args = p.parse_args()
    asyncio.run(run(args.address))


if __name__ == "__main__":
    main()
