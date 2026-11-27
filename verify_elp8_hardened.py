# ==============================================================================
# FILE:         verify_elp8_hardened.py
# AUTHOR:       Robin Heilos (McCalm) <mccalm@outlook.de>
# LICENSE:      GNU AGPLv3 (or Commercial Enterprise License — see LICENSE & NOTICE.md)
# ==============================================================================

#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import os
import stat
import struct
import tempfile
import zlib
import numpy as np

MAGIC = b"ELP8"
VERSION = 0x01

FLAG_COUNTER_ROTATING = 0x01
FLAG_INWARD_MIRROR_SPLIT = 0x02
FLAG_MULTI_CHUNK_GEARS = 0x04
FLAG_AUTHENTICATED_ORIGIN = 0x08

DEFAULT_MODULUS = 8
HASH_SHA256 = 0x01
HASH_SHA512 = 0x02
HASH_SPECS = {
    HASH_SHA256: ("sha256", 32),
    HASH_SHA512: ("sha512", 64),
}

GEAR_G0_RAW = 0x00
GEAR_G1_ZLIB = 0x01
GEAR_G6_WHEEL8_SPIRAL = 0x06
MAX_SAFE_CHUNK_SIZE = 64 * 1024 * 1024


def compute_s0_sector(anchor_hash: bytes, modulus: int = DEFAULT_MODULUS) -> int:
    prefix_u64 = struct.unpack(">Q", anchor_hash[:8])[0]
    return prefix_u64 % modulus


def _read_exact(fd: int, num_bytes: int) -> bytes:
    buf = bytearray()
    while len(buf) < num_bytes:
        part = os.read(fd, num_bytes - len(buf))
        if not part:
            break
        buf.extend(part)
    return bytes(buf)


def _decompress_bounded(data: bytes, expected_len: int) -> bytes:
    if expected_len > MAX_SAFE_CHUNK_SIZE:
        raise MemoryError("Chunk exceeds safety ceiling")
    d = zlib.decompressobj()
    out = d.decompress(data, expected_len + 1)
    if d.unconsumed_tail or len(out) != expected_len or d.flush() != b"":
        raise ValueError("Decompression bomb or length mismatch")
    return out


def spiral_transform_fast(
    data: bytes, s0: int, modulus: int = DEFAULT_MODULUS, mirror_split: bool = True
) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    arr = np.frombuffer(data, dtype=np.uint8)
    mid = n // 2 if mirror_split else n

    if mid > 0:
        idx_l = np.arange(mid, dtype=np.int64)
        bias_l = (((s0 + idx_l) % modulus) * 31) & 0xFF
        val_l = arr[:mid] ^ bias_l.astype(np.uint8)
        prev_l = np.empty_like(val_l)
        prev_l[0] = s0 & 0xFF
        if mid > 1:
            prev_l[1:] = val_l[:-1]
        out_l = ((val_l.astype(np.int16) - prev_l.astype(np.int16)) & 0xFF).astype(np.uint8)
    else:
        out_l = np.empty(0, dtype=np.uint8)

    if not mirror_split or mid == n:
        return out_l.tobytes()

    right_rev = arr[mid:][::-1]
    idx_r = np.arange(len(right_rev), dtype=np.int64)
    bias_r = (((s0 - idx_r) % modulus) * 31) & 0xFF
    val_r = right_rev ^ bias_r.astype(np.uint8)
    prev_r = np.empty_like(val_r)
    prev_r[0] = s0 & 0xFF
    if len(val_r) > 1:
        prev_r[1:] = val_r[:-1]
    out_r_rev = ((val_r.astype(np.int16) - prev_r.astype(np.int16)) & 0xFF).astype(np.uint8)
    return np.concatenate((out_l, out_r_rev[::-1])).tobytes()


def inverse_spiral_transform_fast(
    transformed: bytes, s0: int, modulus: int = DEFAULT_MODULUS, mirror_split: bool = True
) -> bytes:
    n = len(transformed)
    if n == 0:
        return b""
    arr = np.frombuffer(transformed, dtype=np.uint8)
    mid = n // 2 if mirror_split else n

    if mid > 0:
        idx_l = np.arange(mid, dtype=np.int64)
        bias_l = (((s0 + idx_l) % modulus) * 31) & 0xFF
        val_l = (np.cumsum(arr[:mid].astype(np.int64)) + (s0 & 0xFF)) & 0xFF
        rec_l = val_l.astype(np.uint8) ^ bias_l.astype(np.uint8)
    else:
        rec_l = np.empty(0, dtype=np.uint8)

    if not mirror_split or mid == n:
        return rec_l.tobytes()

    right_rev = arr[mid:][::-1]
    idx_r = np.arange(len(right_rev), dtype=np.int64)
    bias_r = (((s0 - idx_r) % modulus) * 31) & 0xFF
    val_r = (np.cumsum(right_rev.astype(np.int64)) + (s0 & 0xFF)) & 0xFF
    rec_r = (val_r.astype(np.uint8) ^ bias_r.astype(np.uint8))[::-1]
    return np.concatenate((rec_l, rec_r)).tobytes()
def _open_regular_file_secure(path: str) -> int:
    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, open_flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Security violation: Input is not a regular file")
        return fd
    except Exception:
        os.close(fd)
        raise


def encode_file_hardened(
    input_path: str,
    output_path: str,
    hash_id: int = HASH_SHA512,
    modulus: int = DEFAULT_MODULUS,
    mirror_split: bool = True,
    chunk_size: int = 1024 * 1024,
    auth_key: bytes | None = None,
) -> dict:
    if (
        hash_id not in HASH_SPECS
        or not (1 <= modulus <= 255)
        or not (1 <= chunk_size <= MAX_SAFE_CHUNK_SIZE)
    ):
        raise ValueError("Invalid hash_id, modulus, or chunk_size")
    algo_name, digest_len = HASH_SPECS[hash_id]
    use_auth = auth_key is not None
    header_total = 24 + digest_len + (64 if use_auth else 0)

    fd = _open_regular_file_secure(input_path)
    out_dir = os.path.dirname(os.path.abspath(output_path))
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".elp8_enc_", suffix=".tmp", dir=out_dir)

    try:
        expected_size = os.fstat(fd).st_size
        hasher_p1 = hashlib.new(algo_name)
        read_p1 = 0
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            buf = _read_exact(fd, chunk_size)
            if not buf:
                break
            read_p1 += len(buf)
            hasher_p1.update(buf)

        if read_p1 != expected_size:
            raise IOError("Source file size changed during Pass 1")

        anchor_hash = hasher_p1.digest()
        s0 = compute_s0_sector(anchor_hash, modulus)
        flags = FLAG_COUNTER_ROTATING | FLAG_MULTI_CHUNK_GEARS
        if mirror_split:
            flags |= FLAG_INWARD_MIRROR_SPLIT
        if use_auth:
            flags |= FLAG_AUTHENTICATED_ORIGIN

        num_chunks = (expected_size + chunk_size - 1) // chunk_size if expected_size > 0 else 0
        gear_stats = {GEAR_G0_RAW: 0, GEAR_G1_ZLIB: 0, GEAR_G6_WHEEL8_SPIRAL: 0}
        hasher_p2 = hashlib.new(algo_name)
        read_p2 = 0
        os.lseek(fd, 0, os.SEEK_SET)

        with os.fdopen(tmp_fd, "wb") as f_out:
            tmp_fd = -1
            f_out.write(b"\x00" * header_total)
            f_out.write(struct.pack(">I", num_chunks))
            payload_len = 4

            for c_idx in range(num_chunks):
                chunk = _read_exact(fd, chunk_size)
                if not chunk:
                    raise IOError("Unexpected EOF during Pass 2")
                read_p2 += len(chunk)
                hasher_p2.update(chunk)
                chunk_s0 = (s0 + c_idx) % modulus

                cand_g0 = chunk
                cand_g1 = zlib.compress(chunk, level=6)
                cand_g6 = zlib.compress(
                    spiral_transform_fast(chunk, chunk_s0, modulus, mirror_split), level=6
                )
                best_gear, best_bytes = min(
                    [(GEAR_G0_RAW, cand_g0), (GEAR_G1_ZLIB, cand_g1), (GEAR_G6_WHEEL8_SPIRAL, cand_g6)],
                    key=lambda item: len(item[1]),
                )
                gear_stats[best_gear] += 1
                f_out.write(struct.pack(">BII", best_gear, len(chunk), len(best_bytes)))
                f_out.write(best_bytes)
                payload_len += 9 + len(best_bytes)

            if os.read(fd, 1) != b"" or read_p2 != expected_size:
                raise IOError("TOCTOU ATTACK: Source modified during encoding")
            if not hmac.compare_digest(hasher_p2.digest(), anchor_hash):
                raise IOError("TOCTOU FAILURE: Pass-2 hash differs from H0")

            base_header = struct.pack(
                ">4sBBBBQQ", MAGIC, VERSION, flags, modulus, hash_id, expected_size, payload_len
            )
            auth_tag = hmac.new(auth_key, base_header + anchor_hash, hashlib.sha512).digest() if use_auth else b""
            f_out.seek(0)
            f_out.write(base_header + anchor_hash + auth_tag)
            f_out.flush()
            os.fsync(f_out.fileno())

        os.replace(tmp_path, output_path)
        tmp_path = None
    finally:
        os.close(fd)
        if tmp_fd != -1:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"orig_size": expected_size, "compressed_size": header_total + payload_len, "s0": s0, "gear_stats": gear_stats}
def decode_file_hardened(
    container_path: str, output_path: str, auth_key: bytes | None = None
) -> dict:
    fd = _open_regular_file_secure(container_path)
    out_dir = os.path.dirname(os.path.abspath(output_path))
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".elp8_dec_", suffix=".tmp", dir=out_dir)

    try:
        container_size = os.fstat(fd).st_size
        if container_size < 24:
            raise ValueError("Truncated ELP8 base header")
        base_header = _read_exact(fd, 24)
        magic, version, flags, modulus, hash_id, orig_size, payload_len = struct.unpack(
            ">4sBBBBQQ", base_header
        )
        if magic != MAGIC or version != VERSION or modulus == 0 or hash_id not in HASH_SPECS:
            raise ValueError("Invalid ELP8 header parameters")
        if not (flags & FLAG_MULTI_CHUNK_GEARS):
            raise ValueError("Unsupported container mode (expected FLAG_MULTI_CHUNK_GEARS)")

        algo_name, digest_len = HASH_SPECS[hash_id]
        has_auth = bool(flags & FLAG_AUTHENTICATED_ORIGIN)
        header_total = 24 + digest_len + (64 if has_auth else 0)
        if container_size != header_total + payload_len or payload_len < 4:
            raise ValueError("Container length mismatch")

        anchor_hash = _read_exact(fd, digest_len)
        if has_auth:
            stored_tag = _read_exact(fd, 64)
            if auth_key is None:
                raise ValueError("Missing authentication key (Bit 3 set)")
            expected_tag = hmac.new(auth_key, base_header + anchor_hash, hashlib.sha512).digest()
            if not hmac.compare_digest(stored_tag, expected_tag):
                raise ValueError("AUTHENTICATION FAILURE: Invalid origin tag")

        s0 = compute_s0_sector(anchor_hash, modulus)
        mirror_split = bool(flags & FLAG_INWARD_MIRROR_SPLIT)
        (num_chunks,) = struct.unpack(">I", _read_exact(fd, 4))
        bytes_consumed, written_bytes = 4, 0
        hasher = hashlib.new(algo_name)

        with os.fdopen(tmp_fd, "wb") as f_out:
            tmp_fd = -1
            for c_idx in range(num_chunks):
                frame_hdr = _read_exact(fd, 9)
                if len(frame_hdr) < 9:
                    raise ValueError("Truncated chunk header")
                bytes_consumed += 9
                gear_id, c_orig_len, c_comp_len = struct.unpack(">BII", frame_hdr)
                if c_orig_len > MAX_SAFE_CHUNK_SIZE or c_comp_len > MAX_SAFE_CHUNK_SIZE:
                    raise MemoryError("Chunk exceeds safety ceiling")

                c_data = _read_exact(fd, c_comp_len)
                if len(c_data) < c_comp_len:
                    raise ValueError("Truncated chunk payload")
                bytes_consumed += c_comp_len

                chunk_s0 = (s0 + c_idx) % modulus
                if gear_id == GEAR_G0_RAW:
                    dec_chunk = c_data
                elif gear_id == GEAR_G1_ZLIB:
                    dec_chunk = _decompress_bounded(c_data, c_orig_len)
                elif gear_id == GEAR_G6_WHEEL8_SPIRAL:
                    dec_chunk = inverse_spiral_transform_fast(
                        _decompress_bounded(c_data, c_orig_len), chunk_s0, modulus, mirror_split
                    )
                else:
                    raise ValueError(f"Unknown Gear ID: {gear_id}")

                if len(dec_chunk) != c_orig_len:
                    raise ValueError("Chunk length mismatch")
                f_out.write(dec_chunk)
                hasher.update(dec_chunk)
                written_bytes += len(dec_chunk)

            if bytes_consumed != payload_len or written_bytes != orig_size:
                raise ValueError("Total byte accounting mismatch")
            if not hmac.compare_digest(hasher.digest(), anchor_hash):
                raise ValueError("INTEGRITY FAILURE: Anchor Hash mismatch")
            f_out.flush()
            os.fsync(f_out.fileno())

        os.replace(tmp_path, output_path)
        tmp_path = None
    finally:
        os.close(fd)
        if tmp_fd != -1:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"verified_bytes": written_bytes, "status": "VERIFIED_OK"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EULNO ELP8 Hardened RFC Reference Tool")
    parser.add_argument("--encode", nargs=2, metavar=("IN", "OUT"), help="Encode file to .elp8")
    parser.add_argument("--decode", nargs=2, metavar=("IN", "OUT"), help="Decode & verify .elp8")
    parser.add_argument("--key-file", help="Optional secret key file for Bit-3 origin auth")
    args = parser.parse_args()

    key_bytes = None
    if args.key_file:
        with open(args.key_file, "rb") as kf:
            key_bytes = kf.read()
        if not key_bytes:
            raise ValueError("Key file must not be empty")

    if args.encode:
        print("ENCODED OK:", encode_file_hardened(args.encode[0], args.encode[1], auth_key=key_bytes))
    elif args.decode:
        print("DECODED OK:", decode_file_hardened(args.decode[0], args.decode[1], auth_key=key_bytes))
    else:
        secret_key = b"EULNO-ORIGIN-SECRET-KEY-2026"
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Edge-Cases: 0-Byte, 1-Byte & ungerade Chunk-Größe (1337 B)
            for label, payload in [("0-Byte", b""), ("1-Byte", b"\x42"), ("Odd-1337B", os.urandom(1337))]:
                p_in = os.path.join(tmpdir, f"{label}.bin")
                p_elp = os.path.join(tmpdir, f"{label}.elp8")
                p_out = os.path.join(tmpdir, f"{label}_restored.bin")
                with open(p_in, "wb") as f:
                    f.write(payload)
                encode_file_hardened(p_in, p_elp, chunk_size=500, auth_key=secret_key)
                decode_file_hardened(p_elp, p_out, auth_key=secret_key)
                assert open(p_out, "rb").read() == payload
            print("EDGE-CASES (0-Byte, 1-Byte, Odd-Chunk): ALL PASSED")

            # 2. Mirror-Symmetry + Gear-Selection + Header Hex für RFC Appendix A
            src = os.path.join(tmpdir, "mirror.bin")
            elp8 = os.path.join(tmpdir, "mirror.elp8")
            out = os.path.join(tmpdir, "mirror_out.bin")
            half = np.sin(np.linspace(0, 8 * np.pi, 32768, dtype=np.float32)).tobytes()
            with open(src, "wb") as f:
                f.write((half + half[::-1]) * 4)

            enc = encode_file_hardened(src, elp8, hash_id=HASH_SHA512, auth_key=secret_key)
            dec = decode_file_hardened(elp8, out, auth_key=secret_key)
            with open(elp8, "rb") as f:
                hdr_hex = f.read(152).hex()
            print(f"AUDIT PASSED: {dec['verified_bytes']} B, S0={enc['s0']}, Gears={enc['gear_stats']}")
            print(f"RFC-HEADER-HEX (152B): {hdr_hex}")
