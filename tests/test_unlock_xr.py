import pytest

from unlock_xr import CRX_PREFIX, xr_response


def test_response_layout_prefix_digest_checksum():
    challenge = CRX_PREFIX + bytes(range(16)) + b"\x00"
    resp = xr_response(challenge)
    assert len(resp) == 20
    assert resp[:3] == CRX_PREFIX
    checksum = 0
    for byte in resp[:19]:
        checksum ^= byte
    assert resp[19] == checksum


def test_known_vector():
    # md5(b"CRX" + bytes(range(16))) computed independently with hashlib.
    import hashlib
    body = bytes(range(16))
    expected_digest = hashlib.md5(CRX_PREFIX + body).digest()
    resp = xr_response(CRX_PREFIX + body + b"\xaa")
    assert resp[3:19] == expected_digest


def test_rejects_wrong_length():
    with pytest.raises(ValueError):
        xr_response(b"\x00" * 19)
