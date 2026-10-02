"""Parse a btsnoop_hci.log to extract Onewheel GemAuth challenge/response pairs.

We don't bother with full ATT decoding — we look for:
  - 20-byte ATT Handle Value Notifications (opcode 0x1B): challenges from board on f3fe
  - 20-byte ATT Write Requests/Commands  (opcode 0x12 / 0x52): responses on f3ff

This works because the unlock packets are a fixed 20-byte size, and on a
typical session there are very few other 20-byte ATT exchanges.

Usage:
    python src/parse_snoop.py captures/btsnoop_hci.log
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

BTSNOOP_MAGIC = b"btsnoop\x00"


def parse_btsnoop(path: Path):
    """Yield (timestamp_us, direction, hci_packet) tuples."""
    with path.open("rb") as f:
        header = f.read(16)
        if not header.startswith(BTSNOOP_MAGIC):
            raise ValueError(f"not a btsnoop file: {path}")
        # version (4) + datalink (4)
        while True:
            rec_hdr = f.read(24)
            if len(rec_hdr) < 24:
                break
            orig_len, incl_len, flags, drops, ts = struct.unpack(">IIIIq", rec_hdr)
            data = f.read(incl_len)
            if len(data) < incl_len:
                break
            direction = "→" if (flags & 0x01) else "←"  # 0=host→ctrl(send), 1=ctrl→host(recv)
            yield ts, direction, data


def find_att_packets(records):
    """From btsnoop records, decode ACL → L2CAP → ATT. Yields (ts, dir, opcode, payload).

    HCI ACL packet on btsnoop datalink type 1024 (HCI UART) starts with packet
    indicator 0x02 = ACL, then ACL header (4 bytes), then L2CAP header (4 bytes
    on first frag), then ATT.
    """
    for ts, direction, data in records:
        if not data:
            continue
        # btsnoop datalink type 1002 = HCI UART (with packet indicator byte)
        # First byte is HCI packet type indicator
        idx = 0
        ptype = data[0]
        if ptype != 0x02:  # 0x02 = ACL
            continue
        idx = 1
        if len(data) < idx + 4:
            continue
        # ACL header: handle+flags (2), length (2)
        handle_flags, acl_len = struct.unpack("<HH", data[idx:idx + 4])
        idx += 4
        pb_flag = (handle_flags >> 12) & 0x03  # packet boundary flag
        # PB=0b10 (2) = first frag of higher-layer; PB=0b01 (1) = continuation
        if pb_flag == 1:  # continuation — skip; we'd need reassembly
            continue
        if len(data) < idx + 4:
            continue
        # L2CAP header: length (2), CID (2)
        l2_len, cid = struct.unpack("<HH", data[idx:idx + 4])
        idx += 4
        if cid != 0x0004:  # CID 0x0004 = ATT
            continue
        if len(data) < idx + 1:
            continue
        opcode = data[idx]
        payload = data[idx + 1:idx + 1 + l2_len - 1]
        yield ts, direction, opcode, payload


def main(path: str) -> None:
    p = Path(path)
    print(f"# parsing {p}  ({p.stat().st_size:,} bytes)")
    records = list(parse_btsnoop(p))
    print(f"# {len(records):,} HCI records")

    handle_writes_to_f3ff = []   # (ts, attr_handle, value)
    handle_notifies = []          # (ts, attr_handle, value)
    write_request_seen = []
    notify_seen = []
    found_handle_for_uuid: dict[str, int] = {}

    # Pass 1: collect all ATT events
    att_count = 0
    for ts, direction, opcode, payload in find_att_packets(records):
        att_count += 1
        # 0x12 = Write Request, 0x52 = Write Command
        if opcode in (0x12, 0x52) and len(payload) >= 2:
            attr_handle = struct.unpack("<H", payload[0:2])[0]
            value = payload[2:]
            if len(value) == 20:
                handle_writes_to_f3ff.append((ts, attr_handle, value))
        # 0x1B = Handle Value Notification, 0x1D = Indication
        elif opcode in (0x1B, 0x1D) and len(payload) >= 2:
            attr_handle = struct.unpack("<H", payload[0:2])[0]
            value = payload[2:]
            if len(value) == 20:
                handle_notifies.append((ts, attr_handle, value))

    print(f"# {att_count:,} ATT packets total")
    print(f"# {len(handle_notifies)} 20-byte notifications (likely f3fe challenges)")
    print(f"# {len(handle_writes_to_f3ff)} 20-byte writes (likely f3ff responses)")

    # Print pairs sorted by time
    if handle_notifies:
        print("\n=== 20-byte NOTIFICATIONS (board → app, candidate challenges) ===")
        for ts, h, v in handle_notifies[:10]:
            print(f"  ts={ts}  handle=0x{h:04x}  bytes={v.hex()}")

    if handle_writes_to_f3ff:
        print("\n=== 20-byte WRITES (app → board, candidate responses) ===")
        for ts, h, v in handle_writes_to_f3ff[:10]:
            print(f"  ts={ts}  handle=0x{h:04x}  bytes={v.hex()}")

    # Pair them up — each challenge should be followed by a write within ~1s
    print("\n=== CHALLENGE / RESPONSE PAIRS (notify followed within 5s by 20-byte write) ===")
    pairs = []
    for nts, nh, nv in handle_notifies:
        for wts, wh, wv in handle_writes_to_f3ff:
            dt_us = wts - nts
            if 0 < dt_us < 5_000_000:  # within 5 s
                pairs.append((nts, nv, wts, wv, dt_us))
                break
    print(f"# found {len(pairs)} pairs")
    for i, (nts, nv, wts, wv, dt) in enumerate(pairs[:5]):
        print(f"\n  pair #{i + 1}  (response sent {dt / 1000:.1f} ms after challenge)")
        print(f"    challenge: {nv.hex()}")
        print(f"    response:  {wv.hex()}")
        # Show first 3 bytes as ASCII (CRX prefix?)
        try:
            print(f"      challenge[0:3] ascii = {nv[0:3].decode('ascii', errors='replace')!r}")
            print(f"      response[0:3]  ascii = {wv[0:3].decode('ascii', errors='replace')!r}")
        except Exception:
            pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "captures/btsnoop_hci.log")
