#!/usr/bin/env python3
"""
EULNO (.elp8) - Minimal RFC Reference Example (draft-heilos-eulno-elp8-00)
Author: Robin Heilos <mccalm@outlook.de> | https://eulno.org
Zero external dependencies (Python Standard Library only).
Demonstrates:
  1. 88-byte RFC header construction (Magic "ELP8", Flags, Wheel Modulus M=8, SHA-512)
  2. Deterministic start spoke derivation: S0 = uint64(AnchorHash[0..7]) mod 8
  3. Counter-rotating Wheel-8 Inward-Mirror-Split transform (Gear G6)
  4. Constant-time cryptographic self-validation & tamper rejection
"""

import hashlib
import hmac
import struct
import zlib

MAGIC = b"ELP8"
VERSION = 0x01
FLAGS = 0x07  # Bit 0: Counter-Rotating | Bit 1: Inward-Mirror-Split | Bit 2: Multi-Chunk
MODULUS = 8
HASH_SHA512 = 0x02
GEAR_G6_WHEEL8 = 0x06


def compute_s0(anchor_hash: bytes, modulus: int = MODULUS) -> int:
    return struct.unpack(">Q", anchor_hash[:8])[0] % modulus


def wheel8_mirror_transform(data: bytes, s0: int, modulus: int = MODULUS) -> bytes:
    """Clockwise [0..mid-1] + Counter-Clockwise Inward [n-1..mid] anchored at S0."""
    n = len(data)
    mid = n // 2
    out = bytearray(n)

    prev = s0
    for i in range(mid):
        bias = (((s0 + i) % modulus) * 31) & 0xFF
        val = data[i] ^ bias
        out[i] = (val - prev) & 0xFF
        prev = val

    prev = s0
    for step, j in enumerate(range(n - 1, mid - 1, -1)):
        bias = (((s0 - step) % modulus) * 31) & 0xFF
        val = data[j] ^ bias
        out[j] = (val - prev) & 0xFF
        prev = val

    return bytes(out)


def wheel8_mirror_inverse(transformed: bytes, s0: int, modulus: int = MODULUS) -> bytes:
    """Exact bit-reversible inverse of wheel8_mirror_transform."""
    n = len(transformed)
    mid = n // 2
    out = bytearray(n)

    prev = s0
    for i in range(mid):
        bias = (((s0 + i) % modulus) * 31) & 0xFF
        val = (transformed[i] + prev) & 0xFF
        out[i] = val ^ bias
        prev = val

    prev = s0
    for step, j in enumerate(range(n - 1, mid - 1, -1)):
        bias = (((s0 - step) % modulus) * 31) & 0xFF
        val = (transformed[j] + prev) & 0xFF
        out[j] = val ^ bias
        prev = val

    return bytes(out)


def pack_rfc_elp8(raw_data: bytes) -> bytes:
    anchor = hashlib.sha512(raw_data).digest()
    s0 = compute_s0(anchor)
    comp_chunk = zlib.compress(wheel8_mirror_transform(raw_data, s0), level=9)
    # Multi-chunk framing: [num_chunks=1 (4B)] + [Gear=G6 (1B), orig_len (4B), comp_len (4B)] + data
    payload = struct.pack(">IBII", 1, GEAR_G6_WHEEL8, len(raw_data), len(comp_chunk)) + comp_chunk
    header = struct.pack(">4sBBBBQQ", MAGIC, VERSION, FLAGS, MODULUS, HASH_SHA512, len(raw_data), len(payload))
    return header + anchor + payload


def unpack_rfc_elp8(container: bytes) -> bytes:
    magic, ver, flags, mod, hash_id, orig_size, payload_len = struct.unpack(">4sBBBBQQ", container[:24])
    assert magic == MAGIC and ver == VERSION and hash_id == HASH_SHA512
    if len(container) != 88 + payload_len:
        raise ValueError("Container length mismatch")

    anchor = container[24:88]
    s0 = compute_s0(anchor, mod)

    num_chunks, gear_id, c_orig, c_comp = struct.unpack(">IBII", container[88:101])
    assert num_chunks == 1 and gear_id == GEAR_G6_WHEEL8
    restored = wheel8_mirror_inverse(zlib.decompress(container[101 : 101 + c_comp]), s0, mod)

    if len(restored) != orig_size or not hmac.compare_digest(hashlib.sha512(restored).digest(), anchor):
        raise ValueError("INTEGRITY FAILURE: Anchor Hash mismatch!")
    return restored


if __name__ == "__main__":
    sample = b"EULNO-RFC-WHEEL8-MIRROR-DEMO-" * 100
    elp8_bytes = pack_rfc_elp8(sample)
    recovered = unpack_rfc_elp8(elp8_bytes)

    print(f"Original Size  : {len(sample)} B")
    print(f"ELP8 Container : {len(elp8_bytes)} B (incl. 88 B RFC Header)")
    print(f"Start Spoke S0 : {compute_s0(elp8_bytes[24:88])} (mod 8)")
    print(f"Self-Validation: {'PASS (Bit-Exact)' if recovered == sample else 'FAIL'}")

    # Tamper-Test (flip 1 bit in payload)
    tampered = bytearray(elp8_bytes)
    tampered[-1] ^= 0x01
    try:
        unpack_rfc_elp8(bytes(tampered))
    except (ValueError, zlib.error):
        print("Tamper Reject  : PASS (1-bit corruption detected)")
