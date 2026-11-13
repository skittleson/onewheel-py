import struct

import parse_snoop
import unlock_pint


def _btsnoop(records: list[tuple[int, bytes]]) -> bytes:
    out = bytearray(b"btsnoop\x00" + struct.pack(">II", 1, 1002))
    for flags, data in records:
        out += struct.pack(">IIIIq", len(data), len(data), flags, 0, 123) + data
    return bytes(out)


def _acl(conn: int, att: bytes, pb: int = 2) -> bytes:
    l2 = struct.pack("<HH", len(att), 0x0004) + att
    return b"\x02" + struct.pack("<HH", conn | (pb << 12), len(l2)) + l2


def test_parse_btsnoop_and_iter_att(tmp_path):
    write_req = bytes([0x12]) + struct.pack("<H", 0x0016) + bytes(20)
    notify = bytes([0x1B]) + struct.pack("<H", 0x0010) + b"\x07\x09"
    path = tmp_path / "snoop.log"
    path.write_bytes(_btsnoop([(0, _acl(0x40, write_req)), (1, _acl(0x40, notify))]))

    events = list(parse_snoop.iter_att(parse_snoop.parse_btsnoop(path)))

    assert [(d, conn, op) for _ts, d, conn, op, _pl in events] == [
        ("tx", 0x40, 0x12), ("rx", 0x40, 0x1B)]
    assert events[1][4] == struct.pack("<H", 0x0010) + b"\x07\x09"


def test_iter_att_reassembles_fragments():
    att = bytes([0x1B]) + struct.pack("<H", 0x0010) + bytes(range(20))
    full = _acl(0x40, att)
    hdr_len = 5
    first = full[:hdr_len + 10]
    rest = full[hdr_len + 10:]
    first = first[:3] + struct.pack("<H", len(first) - hdr_len) + first[5:]
    cont = b"\x02" + struct.pack("<HH", 0x40 | (1 << 12), len(rest)) + rest

    events = list(parse_snoop.iter_att([(0, 1, first), (1, 1, cont)]))

    assert len(events) == 1
    assert events[0][4] == att[1:]


def test_unlock_pint_wrapper_forces_pint_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(unlock_pint, "live_main", calls.append)
    unlock_pint.main(["--csv", "ride.csv"])
    assert calls == [["--unlock", "pint", "--csv", "ride.csv"]]
