"""Better Onewheel snoop log parser.

Tracks BLE connection handles → device addresses (from HCI LE Connection Complete
events), so we can filter ATT exchanges to only those involving the Onewheel.

Also dumps Find Information / Read By Type Group responses to map ATT handles
to characteristic UUIDs.

Usage:
    python src/parse_snoop2.py captures/btsnoop_hci.log [TARGET_MAC]
"""

from __future__ import annotations

import struct
import sys
from collections import defaultdict
from pathlib import Path

import os

BTSNOOP_MAGIC = b"btsnoop\x00"
OW_UUID_PREFIX = bytes.fromhex("669a0c200008")[::-1]  # tail of OW base UUID


def _default_mac() -> str:
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
    return val or "XX:XX:XX:XX:XX:XX"


TARGET_MAC = _default_mac()


def parse_btsnoop(path: Path):
    with path.open("rb") as f:
        if not f.read(16).startswith(BTSNOOP_MAGIC):
            raise ValueError(f"not btsnoop: {path}")
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig, incl, flags, drops, ts = struct.unpack(">IIIIq", hdr)
            data = f.read(incl)
            if len(data) < incl:
                break
            yield ts, flags, data


def mac_str(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in b[::-1])


def main(path: str, target: str = TARGET_MAC):
    p = Path(path)
    print(f"# parsing {p}")
    target_le = bytes(int(x, 16) for x in target.split(":"))[::-1]

    conn_to_mac: dict[int, str] = {}
    target_conns: set[int] = set()

    # First pass: build connection-handle → MAC map by scanning HCI Events
    for ts, flags, data in parse_btsnoop(p):
        if not data:
            continue
        ptype = data[0]
        # 0x04 = HCI Event packet
        if ptype != 0x04 or len(data) < 4:
            continue
        evt_code = data[1]
        # 0x3E = LE Meta Event
        if evt_code != 0x3E or len(data) < 4:
            continue
        sub = data[3]
        # 0x01 = LE Connection Complete, 0x0A = LE Enhanced Connection Complete
        if sub == 0x01 and len(data) >= 22:
            status = data[4]
            if status != 0:
                continue
            conn = struct.unpack("<H", data[5:7])[0]
            mac = data[10:16]
            mac_s = mac_str(mac)
            conn_to_mac[conn] = mac_s
            if mac == target_le:
                target_conns.add(conn)
                print(f"# OW connection opened: handle=0x{conn:04x} mac={mac_s} ts={ts}")
        elif sub == 0x0A and len(data) >= 34:
            status = data[4]
            if status != 0:
                continue
            conn = struct.unpack("<H", data[5:7])[0]
            mac = data[10:16]
            mac_s = mac_str(mac)
            conn_to_mac[conn] = mac_s
            if mac == target_le:
                target_conns.add(conn)
                print(f"# OW connection opened (enh): handle=0x{conn:04x} mac={mac_s} ts={ts}")

    print(f"# total connections seen: {len(conn_to_mac)}")
    print(f"# OW connection handles: {sorted(target_conns)}")

    # Second pass: ATT events on OW connections only
    fragments: dict[int, bytes] = {}  # conn handle → in-flight L2CAP buffer
    att_events = []  # (ts, dir, conn, opcode, payload)

    for ts, flags, data in parse_btsnoop(p):
        if not data or data[0] != 0x02:  # not ACL
            continue
        if len(data) < 5:
            continue
        handle_flags, acl_len = struct.unpack("<HH", data[1:5])
        conn = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x03
        payload = data[5:5 + acl_len]
        direction = "←" if (flags & 0x01) else "→"  # board→phone vs phone→board

        if pb == 1:  # continuation
            buf = fragments.get(conn, b"") + payload
        else:
            buf = payload
        # Try to parse L2CAP
        if len(buf) < 4:
            fragments[conn] = buf
            continue
        l2_len, cid = struct.unpack("<HH", buf[0:4])
        if len(buf) - 4 < l2_len:
            fragments[conn] = buf  # need more
            continue
        fragments.pop(conn, None)
        if cid != 0x0004:  # not ATT
            continue
        att = buf[4:4 + l2_len]
        if not att:
            continue
        opcode = att[0]
        att_payload = att[1:]
        if conn in target_conns:
            att_events.append((ts, direction, conn, opcode, att_payload))

    print(f"# ATT events on OW connections: {len(att_events)}")

    # Decode handle → UUID from Find Information Response (opcode 0x05)
    # Format: format-byte (0x01=16bit, 0x02=128bit) + repeating handle/UUID pairs
    handle_to_uuid: dict[int, str] = {}
    for ts, d, conn, opcode, payload in att_events:
        if opcode == 0x05 and len(payload) >= 1:
            fmt = payload[0]
            body = payload[1:]
            if fmt == 0x01:
                # 16-bit UUIDs, 4 bytes each
                for i in range(0, len(body), 4):
                    if i + 4 > len(body):
                        break
                    h, u = struct.unpack("<HH", body[i:i + 4])
                    handle_to_uuid[h] = f"{u:04x}"
            elif fmt == 0x02:
                # 128-bit UUIDs, 18 bytes each
                for i in range(0, len(body), 18):
                    if i + 18 > len(body):
                        break
                    h = struct.unpack("<H", body[i:i + 2])[0]
                    u = body[i + 2:i + 18][::-1].hex()
                    handle_to_uuid[h] = u
        # Read By Type Response (0x09) — characteristic decls, attr+props+value-handle+UUID
        elif opcode == 0x09 and len(payload) >= 1:
            length = payload[0]
            body = payload[1:]
            for i in range(0, len(body), length):
                rec = body[i:i + length]
                if len(rec) < length:
                    break
                attr_h = struct.unpack("<H", rec[0:2])[0]
                if length == 7:  # 16-bit UUID char decl
                    props = rec[2]
                    val_h = struct.unpack("<H", rec[3:5])[0]
                    u = struct.unpack("<H", rec[5:7])[0]
                    handle_to_uuid[val_h] = f"{u:04x}"
                elif length == 21:  # 128-bit UUID char decl
                    props = rec[2]
                    val_h = struct.unpack("<H", rec[3:5])[0]
                    u = rec[5:21][::-1].hex()
                    handle_to_uuid[val_h] = u

    # Filter to OW-service handles (UUID contains the OW base suffix)
    ow_handles = {
        h: u for h, u in handle_to_uuid.items()
        if u.startswith("e659f") or "e659f" in u
    }
    print(f"\n# OW characteristic value handles found: {len(ow_handles)}")
    for h in sorted(ow_handles):
        u = ow_handles[h]
        nick = u[4:8] if len(u) >= 8 else u
        print(f"  handle=0x{h:04x}  uuid={u}  (f{nick})")

    # Now look at writes / notifications on OW handles only
    print("\n=== ATT events on OW characteristics ===")
    interesting = []
    for ts, d, conn, op, pl in att_events:
        if op == 0x12 and len(pl) >= 2:
            h = struct.unpack("<H", pl[0:2])[0]
            v = pl[2:]
            if h in ow_handles:
                interesting.append((ts, "WRITE_REQ", d, h, ow_handles[h], v))
        elif op == 0x52 and len(pl) >= 2:
            h = struct.unpack("<H", pl[0:2])[0]
            v = pl[2:]
            if h in ow_handles:
                interesting.append((ts, "WRITE_CMD", d, h, ow_handles[h], v))
        elif op == 0x1B and len(pl) >= 2:
            h = struct.unpack("<H", pl[0:2])[0]
            v = pl[2:]
            if h in ow_handles:
                interesting.append((ts, "NOTIFY", d, h, ow_handles[h], v))
        elif op == 0x0B and len(pl) >= 0:  # Read Response — preceding read req carried the handle
            pass

    print(f"# {len(interesting)} interesting events")
    for ts, kind, d, h, u, v in interesting[:60]:
        nick = u[4:8] if len(u) >= 8 else u
        print(f"  ts={ts}  {kind:<10s}  f{nick}  ({len(v)}B)  {v.hex()}")


if __name__ == "__main__":
    args = sys.argv[1:]
    path = args[0] if args else "captures/btsnoop_hci.log"
    target = args[1] if len(args) > 1 else TARGET_MAC
    main(path, target)
