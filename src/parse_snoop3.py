"""Better Onewheel snoop parser v3 — identifies the OW conn handle by content.

Strategy:
  1. Reassemble L2CAP per ACL connection handle.
  2. Mark a conn handle as "the OW connection" if any ATT payload contains the
     OW base UUID bytes (e659f3XX-ea98-11e3-ac10-0800200c9a66).
  3. Decode all ATT events on that handle, build value-handle → UUID map from
     Read-By-Type / Find-Information responses, then dump every read/write/
     notification on OW characteristics.

Usage:
    python src/parse_snoop3.py captures/btsnoop_hci.log
"""

from __future__ import annotations

import struct
import sys
from collections import defaultdict
from pathlib import Path

BTSNOOP_MAGIC = b"btsnoop\x00"
OW_UUID_TAIL_LE = bytes.fromhex("669a0c200008")  # last 6 bytes of OW UUID, little-endian


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


def iter_att(records):
    """Reassemble ACL fragments per connection handle, yield ATT packets.

    Yields: (ts, direction_str, conn_handle, opcode, att_payload)
    """
    fragments: dict[int, bytes] = {}
    fragment_dir: dict[int, str] = {}
    for ts, flags, data in records:
        if not data or data[0] != 0x02 or len(data) < 5:
            continue
        handle_flags, acl_len = struct.unpack("<HH", data[1:5])
        conn = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x03
        payload = data[5:5 + acl_len]
        # Direction: btsnoop flag bit0 == 1 means controller→host (board→phone)
        direction = "rx" if (flags & 0x01) else "tx"

        if pb == 1:  # continuation
            buf = fragments.pop(conn, b"") + payload
            d = fragment_dir.pop(conn, direction)
        else:
            buf = payload
            d = direction

        if len(buf) < 4:
            fragments[conn] = buf
            fragment_dir[conn] = d
            continue
        l2_len, cid = struct.unpack("<HH", buf[0:4])
        if len(buf) - 4 < l2_len:
            fragments[conn] = buf
            fragment_dir[conn] = d
            continue
        if cid == 0x0004 and l2_len >= 1:
            att = buf[4:4 + l2_len]
            yield ts, d, conn, att[0], att[1:]


ATT_OPS = {
    0x02: "MTU_REQ", 0x03: "MTU_RSP",
    0x04: "FIND_INFO_REQ", 0x05: "FIND_INFO_RSP",
    0x08: "READ_BY_TYPE_REQ", 0x09: "READ_BY_TYPE_RSP",
    0x0A: "READ_REQ", 0x0B: "READ_RSP",
    0x10: "READ_BY_GRP_REQ", 0x11: "READ_BY_GRP_RSP",
    0x12: "WRITE_REQ", 0x13: "WRITE_RSP",
    0x16: "PREP_WRITE_REQ", 0x17: "PREP_WRITE_RSP",
    0x18: "EXEC_WRITE_REQ", 0x19: "EXEC_WRITE_RSP",
    0x1B: "NOTIFY", 0x1D: "INDICATE",
    0x52: "WRITE_CMD",
    0x01: "ERROR_RSP",
}


def main(path: str) -> None:
    p = Path(path)
    print(f"# {p}  ({p.stat().st_size:,} bytes)")
    records = list(parse_btsnoop(p))
    events = list(iter_att(records))
    print(f"# {len(events):,} ATT events across all connections")

    # Identify OW conn(s) by content
    ow_conns: set[int] = set()
    for ts, d, conn, op, pl in events:
        if OW_UUID_TAIL_LE in pl:
            ow_conns.add(conn)
    print(f"# OW connection handle(s): {[hex(c) for c in sorted(ow_conns)]}")

    if not ow_conns:
        print("# no OW packets found")
        return

    # Track the in-flight read request so we can attach handles to read responses
    last_read_req: dict[int, int] = {}  # conn -> attr_handle

    # Build handle → uuid map
    h2u: dict[int, str] = {}
    for ts, d, conn, op, pl in events:
        if conn not in ow_conns:
            continue
        if op == 0x09 and len(pl) >= 1:  # Read By Type Response (char declarations)
            length = pl[0]
            body = pl[1:]
            for i in range(0, len(body), length):
                rec = body[i:i + length]
                if len(rec) < length:
                    break
                if length == 7:
                    val_h = struct.unpack("<H", rec[3:5])[0]
                    u = struct.unpack("<H", rec[5:7])[0]
                    h2u[val_h] = f"{u:04x}"
                elif length == 21:
                    val_h = struct.unpack("<H", rec[3:5])[0]
                    u = rec[5:21][::-1].hex()
                    h2u[val_h] = u
        elif op == 0x05 and len(pl) >= 1:  # Find Info Response
            fmt = pl[0]
            body = pl[1:]
            step = 4 if fmt == 0x01 else 18
            for i in range(0, len(body), step):
                rec = body[i:i + step]
                if len(rec) < step:
                    break
                h = struct.unpack("<H", rec[0:2])[0]
                if step == 4:
                    h2u[h] = f"{struct.unpack('<H', rec[2:4])[0]:04x}"
                else:
                    h2u[h] = rec[2:18][::-1].hex()

    # Filter to OW chars
    ow_handles = {h: u for h, u in h2u.items() if u.startswith("e659f")}
    print(f"\n# OW characteristic value handles: {len(ow_handles)}")
    for h in sorted(ow_handles):
        u = ow_handles[h]
        nick = u[4:8]
        print(f"  handle=0x{h:04x}  uuid={u}  (f{nick})")

    # Now scan all events on OW conns, dump reads/writes/notifications on OW handles
    print("\n=== OW characteristic traffic ===")
    n = 0
    for ts, d, conn, op, pl in events:
        if conn not in ow_conns:
            continue

        if op == 0x0A and len(pl) >= 2:  # Read Req
            h = struct.unpack("<H", pl[0:2])[0]
            last_read_req[conn] = h
        elif op == 0x0B and conn in last_read_req:  # Read Rsp
            h = last_read_req.pop(conn)
            if h in ow_handles:
                u = ow_handles[h]
                print(f"  ts={ts}  {d}  READ_RSP   f{u[4:8]}  ({len(pl)}B)  {pl.hex()}")
                n += 1
        elif op in (0x12, 0x52) and len(pl) >= 2:  # Write Req/Cmd
            h = struct.unpack("<H", pl[0:2])[0]
            v = pl[2:]
            if h in ow_handles:
                u = ow_handles[h]
                kind = "WRITE_REQ " if op == 0x12 else "WRITE_CMD "
                print(f"  ts={ts}  {d}  {kind} f{u[4:8]}  ({len(v)}B)  {v.hex()}")
                n += 1
        elif op == 0x1B and len(pl) >= 2:  # Notification
            h = struct.unpack("<H", pl[0:2])[0]
            v = pl[2:]
            if h in ow_handles:
                u = ow_handles[h]
                print(f"  ts={ts}  {d}  NOTIFY     f{u[4:8]}  ({len(v)}B)  {v.hex()}")
                n += 1
    print(f"\n# total OW char events: {n}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "captures/btsnoop_hci.log")
