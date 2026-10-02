"""GemAuth challenge solver for Onewheel+ / XR.

Algorithm (open-sourced via the `gemurf` and `pOnewheel` projects):

    challenge: 20 bytes received on UART_SERIAL_READ (e659f3fe…)
        challenge[0:3] is always b"\\x43\\x52\\x58" (ASCII "CRX")
        challenge[3:19] is the random body
        challenge[19] is a parity/checksum the firmware computes

    response = b"\\x43\\x52\\x58"               # 3 bytes "CRX"
             + md5(b"\\x43\\x52\\x58" + body)[:16]   # 16 bytes
             + checksum                       # 1 byte = XOR of bytes 0..18

    write 20-byte response to UART_SERIAL_WRITE (e659f3ff…) within ~30 s.

Notes
-----
- This response is correct on V1, +, and XR.
- On Pint / Pint X / GT / GT-S the firmware uses a *different* algorithm
  (FM rotated keys / changed the prefix derivation). The byte layout is
  the same (CRX prefix + 16-byte digest + checksum) but the digest input
  and/or hash function differ. See docs/references.md for the current
  community implementations.
"""

from __future__ import annotations

import hashlib

CRX_PREFIX = b"\x43\x52\x58"  # "CRX"


def xr_response(challenge: bytes) -> bytes:
    if len(challenge) != 20:
        raise ValueError(f"challenge must be 20 bytes, got {len(challenge)}")
    body = challenge[3:19]                       # 16 random bytes
    digest = hashlib.md5(CRX_PREFIX + body).digest()  # 16 bytes
    out = bytearray(CRX_PREFIX + digest)         # 19 bytes
    checksum = 0
    for b in out:
        checksum ^= b
    out.append(checksum)
    return bytes(out)


if __name__ == "__main__":
    # Self-test against a known good vector (synthetic).
    # 20-byte synthetic challenge: 3 "CRX" + 16 random body + 1 checksum byte
    sample = bytes.fromhex("435258") + bytes(range(16)) + bytes([0])
    resp = xr_response(sample)
    print(f"challenge: {sample.hex()}")
    print(f"response:  {resp.hex()}")
    assert len(resp) == 20
