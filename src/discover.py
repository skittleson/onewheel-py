"""Scan for Onewheel boards and dump their full GATT table.

Usage:
    python src/discover.py            # scan 8s, list candidates
    python src/discover.py --connect XX:XX:XX:XX:FD:B0   # connect + enumerate

The OW advertises as 'ow######' (lowercase) on Pint/Pint X/GT/GT-S, and as
'OW######' or 'Onewheel ...' on older models. We match liberally.
"""

from __future__ import annotations

import argparse
import asyncio

from bleak import BleakClient, BleakScanner

from ow_uuids import SERVICE_UUID, FIELDS_BY_UUID


async def scan(timeout: float = 8.0) -> None:
    print(f"Scanning {timeout}s for Onewheel boards…")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = []
    for addr, (dev, adv) in devices.items():
        name = (adv.local_name or dev.name or "").strip()
        if not name:
            continue
        if name.lower().startswith("ow") or "onewheel" in name.lower():
            found.append((addr, name, adv.rssi))
    if not found:
        print("  no Onewheel-shaped advertisements seen.")
        print("  -- make sure the board is on, app is closed, and no other")
        print("     central is currently connected to it.")
        return
    print("\nCandidates:")
    for addr, name, rssi in sorted(found, key=lambda x: -x[2]):
        print(f"  {addr}  {name:20s}  rssi={rssi} dBm")
    print(f"\nNext: python src/discover.py --connect {found[0][0]}")


async def enumerate_gatt(address: str) -> None:
    print(f"Connecting to {address}…")
    async with BleakClient(address) as client:
        print(f"  connected. mtu={client.mtu_size}")
        svcs = client.services
        ow_svc = svcs.get_service(SERVICE_UUID)
        if ow_svc is None:
            print(f"  WARNING: Onewheel service {SERVICE_UUID} not found.")
            print(f"  Services advertised:")
            for s in svcs:
                print(f"    {s.uuid}")
            return

        print(f"\nOnewheel service: {ow_svc.uuid}")
        print(f"{'UUID':<40s} {'Properties':<30s} {'Name'}")
        print("-" * 100)
        for ch in ow_svc.characteristics:
            props = ",".join(ch.properties)
            field = FIELDS_BY_UUID.get(ch.uuid)
            name = field.name if field else "-"
            print(f"{ch.uuid:<40s} {props:<30s} {name}")

        # Read every readable, decoded characteristic
        print("\nInitial reads:")
        for ch in ow_svc.characteristics:
            if "read" not in ch.properties:
                continue
            try:
                raw = await client.read_gatt_char(ch.uuid)
            except Exception as exc:
                print(f"  {ch.uuid}  <read failed: {exc}>")
                continue
            field = FIELDS_BY_UUID.get(ch.uuid)
            if field:
                try:
                    val = field.decoder(raw)
                    print(f"  {field.name:<20s} = {val} {field.unit}  raw={raw.hex()}")
                except Exception as exc:
                    print(f"  {field.name:<20s} = <decode err: {exc}>  raw={raw.hex()}")
            else:
                print(f"  {ch.uuid}  raw={raw.hex()}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--connect", metavar="ADDR", help="connect to MAC and dump GATT")
    p.add_argument("--timeout", type=float, default=8.0)
    args = p.parse_args()

    if args.connect:
        asyncio.run(enumerate_gatt(args.connect))
    else:
        asyncio.run(scan(args.timeout))


if __name__ == "__main__":
    main()
