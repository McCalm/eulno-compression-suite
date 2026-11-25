# ==============================================================================
# FILE:         eulno_beta_engine.py
# AUTHOR:       Robin Heilos (McCalm) <mccalm@outlook.de>
# PROFILE:      https://huggingface.co
# LICENSE:      EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)
# MODULE:       ELP8 Master Container, MDL Hybrid Router, Gears 0..5, SHA-512
# PROJECT:      EULNO (.elp8) - 3D-Tetrahedron & Combinatorial Compression Engine
# VERSION:      v0.9.0-beta (PUBLIC_BETA)
# DATE:         2026-11-25
#
# DESCRIPTION:
# Core compression engine of the EULNO Suite (.elp8) in Public Beta status
# (v0.9.0-beta) based on discrete 3D-tetrahedron geometry, unified difference
# equation Delta_{s,k} + ZigZag (Gear 1), 16x64 nibble column transposition
# (Gear 2), 3D sparse rANS (Gear 3), bit-plane slicing + CTZ-accelerated
# Pascal C(64,k) coding (Gear 4), and Move-To-Front (Gear 5).
# ==============================================================================

import sys
import os
import time
import struct
import hashlib
import zlib
import lzma
import numpy as np

try:
    from eulno_tetra3d_v21 import (
        forward_3d_gradient_transform,
        inverse_3d_gradient_transform,
        pack_zero_runs_v21,
        unpack_zero_runs_v21,
        rans20_encode,
        rans20_decode,
    )
    HAS_TETRA_V21 = True
except ImportError:
    HAS_TETRA_V21 = False

ELP8_MAGIC = b"ELP8"
ELP8_HDR_FMT = "<4sBBQQI64s6s"
ELP8_HDR_SIZE = struct.calcsize(ELP8_HDR_FMT)
DEFAULT_CHUNK_SIZE = 1048576  # 1 MiB chunks


def sha512_bytes(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def is_incompressible_noise(arr: np.ndarray) -> bool:
    """
    Checks 0-order entropy H(X) AND 1st-order difference entropy H(dX):
    Blocks true random noise in < 0.1 ms, while letting 0..255 ramps (H(dX)=0.81!)
    pass through unimpeded!
    """
    if len(arr) < 256:
        return False
    sample = arr[: min(len(arr), 16384)]
    c0 = np.bincount(sample, minlength=256)
    p0 = c0[c0 > 0] / len(sample)
    if float(-np.sum(p0 * np.log2(p0))) < 7.95:
        return False

    diff = (sample[1:].astype(np.int16) - sample[:-1].astype(np.int16)) & 0xFF
    c1 = np.bincount(diff, minlength=256)
    p1 = c1[c1 > 0] / len(diff)
    return float(-np.sum(p1 * np.log2(p1))) >= 7.95
TURBO_CANDIDATES = ((1, 1), (1, 2), (32, 1), (512, 1), (1024, 1))


def _turbo_fwd(arr: np.ndarray, stride: int, order: int) -> np.ndarray:
    n = len(arr)
    if n <= stride * order:
        return arr.copy()
    x = arr.astype(np.int32)
    res = x.copy()
    res[stride:] = (x[stride:] - x[:-stride]) & 0xFF
    if order == 2:
        d1 = res.copy()
        res[stride:] = (d1[stride:] - d1[:-stride]) & 0xFF
        s = ((res.astype(np.int16) + 128) & 0xFF) - 128
        return ((s << 1) ^ (s >> 15)).astype(np.uint8)
    return res.astype(np.uint8)


def _turbo_inv(res: np.ndarray, stride: int, order: int) -> np.ndarray:
    n = len(res)
    if n <= stride * order:
        return res.copy()
    if order == 2:
        zz_i16 = res.astype(np.int16)
        s_rec = (zz_i16 >> 1) ^ -(zz_i16 & 1)
        res = (s_rec & 0xFF).astype(np.uint8)

    pad_len = (stride - (n % stride)) % stride
    padded = np.zeros(n + pad_len, dtype=np.int32)
    padded[:n] = res.astype(np.int32)
    grid = padded.reshape(-1, stride)
    if order == 1:
        rec_grid = np.cumsum(grid, axis=0) & 0xFF
    else:
        d1 = np.cumsum(grid, axis=0) & 0xFF
        rec_grid = np.cumsum(d1, axis=0) & 0xFF
    return rec_grid.ravel()[:n].astype(np.uint8)


def encode_gang1_turbo(chunk: bytes, z_level: int = 1) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    best_cfg = (1, 1)
    best_small = -1
    sample = arr[: min(len(arr), 16384)]
    for s, k in TURBO_CANDIDATES:
        cand = _turbo_fwd(sample, s, k)
        small = int(np.count_nonzero(cand <= 4))
        if small > best_small:
            best_small = small
            best_cfg = (s, k)

    s_best, k_best = best_cfg
    trans = _turbo_fwd(arr, s_best, k_best)
    comp = zlib.compress(trans.tobytes(), level=z_level)
    return struct.pack("<HB", s_best, k_best) + comp


def decode_gang1_turbo(payload: bytes) -> bytes:
    if len(payload) < 3:
        raise ValueError("Gear 1 payload corrupted.")
    stride, order = struct.unpack("<HB", payload[:3])
    res_arr = np.frombuffer(zlib.decompress(payload[3:]), dtype=np.uint8)
    return _turbo_inv(res_arr, stride, order).tobytes()


# ------------------------------------------------------------------------------
# GEAR 2: 16x64 HEX-NIBBLE COLUMN TRANSPOSITION
# ------------------------------------------------------------------------------
def encode_gang2_nibble_transpose(chunk: bytes) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    n = len(arr)
    nibbles = np.column_stack(((arr >> 4) & 0x0F, arr & 0x0F)).ravel()
    pad = (64 - (len(nibbles) % 64)) % 64
    if pad > 0:
        nibbles = np.concatenate((nibbles, np.zeros(pad, dtype=np.uint8)))
    trans = nibbles.reshape(-1, 64).T.ravel()
    pairs = trans.reshape(-1, 2)
    packed_bytes = ((pairs[:, 0] << 4) | pairs[:, 1]).tobytes()
    return struct.pack("<I", n) + zlib.compress(packed_bytes, level=6)


def decode_gang2_nibble_transpose(payload: bytes) -> bytes:
    if len(payload) < 4:
        raise ValueError("Gear 2 payload corrupted.")
    (orig_n,) = struct.unpack("<I", payload[:4])
    raw_b = np.frombuffer(zlib.decompress(payload[4:]), dtype=np.uint8)
    trans = np.column_stack(((raw_b >> 4) & 0x0F, raw_b & 0x0F)).ravel()
    rows = len(trans) // 64
    untrans = trans.reshape(64, rows).T.ravel()[: orig_n * 2]
    pairs = untrans.reshape(-1, 2)
    return ((pairs[:, 0] << 4) | pairs[:, 1]).tobytes()
# ------------------------------------------------------------------------------
# GEAR 3 & GEAR 5: 3D-TETRAHEDRON GRADIENT & MOVE-TO-FRONT (MTF)
# ------------------------------------------------------------------------------
def encode_gang3_tetra3d(chunk: bytes) -> bytes:
    res, _ = forward_3d_gradient_transform(chunk, dim_x=16, dim_y=64)
    return rans20_encode(pack_zero_runs_v21(res))


def decode_gang3_tetra3d(payload: bytes, orig_byte_len: int) -> bytes:
    res = unpack_zero_runs_v21(rans20_decode(payload), orig_byte_len * 2)
    return inverse_3d_gradient_transform(res, orig_byte_len, dim_x=16, dim_y=64)


def encode_gang5_mtf_tetra(chunk: bytes) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    nibbles = np.column_stack(((arr >> 4) & 0x0F, arr & 0x0F)).ravel().tolist()
    table = list(range(16))
    mtf_out = list((0,) * len(nibbles))
    for i, sym in enumerate(nibbles):
        idx = table.index(sym)
        mtf_out[i] = idx
        if idx > 0:
            table.pop(idx)
            table.insert(0, sym)
    mtf_arr = np.array(mtf_out, dtype=np.uint8).reshape(-1, 2)
    return encode_gang3_tetra3d(((mtf_arr[:, 0] << 4) | mtf_arr[:, 1]).tobytes())


def decode_gang5_mtf_tetra(payload: bytes, orig_byte_len: int) -> bytes:
    mtf_bytes = decode_gang3_tetra3d(payload, orig_byte_len)
    m_arr = np.frombuffer(mtf_bytes, dtype=np.uint8)
    idxs = np.column_stack(((m_arr >> 4) & 0x0F, m_arr & 0x0F)).ravel().tolist()
    table = list(range(16))
    rec_nib = list((0,) * len(idxs))
    for i, idx in enumerate(idxs):
        sym = table[idx]
        rec_nib[i] = sym
        if idx > 0:
            table.pop(idx)
            table.insert(0, sym)
    rec_arr = np.array(rec_nib, dtype=np.uint8).reshape(-1, 2)
    return ((rec_arr[:, 0] << 4) | rec_arr[:, 1]).tobytes()


# ------------------------------------------------------------------------------
# GEAR 4: BIT-PLANE SLICING + BIT-XOR + CTZ-ACCELERATED PASCAL C(64, k)
# ------------------------------------------------------------------------------
def _build_pascal64() -> list:
    c = [list((0,) * 65) for _ in range(65)]
    for n in range(65):
        c[n].__setitem__(0, 1)
        for k in range(1, n + 1):
            c[n][k] = c[n - 1][k - 1] + c[n - 1][k]
    return c


PASCAL64 = _build_pascal64()
BITS_FOR_K64 = tuple(0 if PASCAL64.__getitem__(64)[k] <= 1 else (PASCAL64.__getitem__(64)[k] - 1).bit_length() for k in range(65))
FULL_U64 = 0xFFFFFFFFFFFFFFFF


def _enc_u64_ctz(w: int) -> tuple[int, int, int]:
    if w == 0:
        return 0, 0, 0
    if w == FULL_U64:
        return 64, 0, 0
    k = w.bit_count()
    positions = []
    tmp = w
    while tmp:
        lsb = tmp & -tmp
        positions.append(lsb.bit_length() - 1)
        tmp ^= lsb
    index = 0
    rem_k = k
    for pos in reversed(positions):
        index += PASCAL64[pos][rem_k]
        rem_k -= 1
    return k, index, BITS_FOR_K64[k]


def _dec_u64_pascal(k: int, index: int) -> int:
    if k == 0:
        return 0
    if k == 64:
        return FULL_U64
    val64 = 0
    rem_k = k
    rem_idx = index
    for pos in range(63, -1, -1):
        comb = PASCAL64[pos][rem_k]
        if rem_idx >= comb:
            val64 |= (1 << pos)
            rem_idx -= comb
            rem_k -= 1
            if rem_k == 0:
                break
    return val64
def encode_gang4_bitplane_pascal(chunk: bytes) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    orig_len = len(arr)
    planes = np.unpackbits(arr).reshape(-1, 8).T
    bp_bytes = np.packbits(planes.ravel())
    bp_xor = bp_bytes.copy()
    bp_xor[1:] ^= bp_bytes[:-1]
    pad_len = (8 - (len(bp_xor) % 8)) % 8
    words = np.frombuffer(bp_xor.tobytes() + b"\x00" * pad_len, dtype="<u8").tolist()

    k_list = bytearray()
    bit_buf = 0
    bit_cnt = 0
    packed_idx = bytearray()
    for w in words:
        k, idx, n_bits = _enc_u64_ctz(int(w))
        k_list.append(k)
        if n_bits > 0:
            bit_buf |= (idx << bit_cnt)
            bit_cnt += n_bits
            while bit_cnt >= 8:
                packed_idx.append(bit_buf & 0xFF)
                bit_buf >>= 8
                bit_cnt -= 8
    if bit_cnt > 0:
        packed_idx.append(bit_buf & 0xFF)

    k_comp = zlib.compress(bytes(k_list), level=6)
    return struct.pack("<II", orig_len, len(k_comp)) + k_comp + bytes(packed_idx)


def decode_gang4_bitplane_pascal(payload: bytes) -> bytes:
    if len(payload) < 8:
        raise ValueError("Gear 4 payload too short.")
    orig_len, k_len = struct.unpack("<II", payload[:8])
    k_list = zlib.decompress(payload[8:8 + k_len])
    idx_stream = payload[8 + k_len:]
    words = np.empty(len(k_list), dtype="<u8")
    byte_pos = 0
    bit_buf = 0
    bit_cnt = 0
    for i, k in enumerate(k_list):
        n_bits = BITS_FOR_K64[k]
        while bit_cnt < n_bits and byte_pos < len(idx_stream):
            bit_buf |= idx_stream[byte_pos] << bit_cnt
            byte_pos += 1
            bit_cnt += 8
        idx = bit_buf & ((1 << n_bits) - 1) if n_bits > 0 else 0
        bit_buf >>= n_bits
        bit_cnt -= n_bits
        words.put(i, _dec_u64_pascal(k, idx))

    bp_xor = np.frombuffer(words.tobytes()[:orig_len], dtype=np.uint8)
    bp_bytes = np.bitwise_xor.accumulate(bp_xor)
    rec_bits = np.unpackbits(bp_bytes)
    return np.packbits(rec_bits.reshape(8, -1).T.ravel()).tobytes()


GANG_NAMES = {
    0: "G0_RAW",
    1: "G1_TURBO_ZIGZAG",
    2: "G2_NIBBLE_16x64",
    3: "G3_TETRA3D_SPARSE",
    4: "G4_BITPLANE_PASCAL",
    5: "G5_MTF_TETRA3D",
}

PROBE_HALF_SIZE = 16384  # 16 KiB + 16 KiB = 32 KiB (divisible by 1024, 64, and 8)


def _extract_probe_window(chunk: bytes) -> bytes:
    """Extracts a 32 KiB representative window (start + middle) for fast MDL screening."""
    n = len(chunk)
    if n <= PROBE_HALF_SIZE * 2:
        return chunk
    mid = (n // 2) & ~1023  # Keep 1024-byte alignment for 3D planes
    return chunk[:PROBE_HALF_SIZE] + chunk[mid : mid + PROBE_HALF_SIZE]


def compress_elp8(
    data: bytes,
    profile: int = 1,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_cb=None,
) -> tuple[bytes, dict]:
    """profile: 0 = TURBO_MODE (Gears 0..1), 1 = MDL_AUTO_MAX (Gears 0..5 with 32 KiB Fast-Probe)"""
    orig_len = len(data)
    digest = sha512_bytes(data)
    if orig_len == 0:
        hdr = struct.pack(ELP8_HDR_FMT, ELP8_MAGIC, profile, 0, 0, 0, 0, digest, b"\x00" * 6)
        return hdr, {"modes": {}}

    num_chunks = (orig_len + chunk_size - 1) // chunk_size
    body = bytearray()
    used_modes = {}

    for idx in range(num_chunks):
        chunk = data[idx * chunk_size : (idx + 1) * chunk_size]
        arr = np.frombuffer(chunk, dtype=np.uint8)
        best_g = 0
        best_p = chunk

        if not is_incompressible_noise(arr):
            z_lvl = 1 if profile == 0 else 6
            p1 = encode_gang1_turbo(chunk, z_level=z_lvl)
            if len(p1) < len(best_p):
                best_g, best_p = 1, p1

            if profile == 1:
                cands = [(2, encode_gang2_nibble_transpose), (4, encode_gang4_bitplane_pascal)]
                if HAS_TETRA_V21:
                    cands.extend(((3, encode_gang3_tetra3d), (5, encode_gang5_mtf_tetra)))

                probe = _extract_probe_window(chunk)
                if len(probe) < len(chunk):
                    # Two-stage MDL screening on 32 KiB window
                    p1_probe_len = len(encode_gang1_turbo(probe, z_level=z_lvl))
                    ref_probe_len = min(len(probe), p1_probe_len)

                    winner_g = None
                    winner_fn = None
                    winner_probe_len = ref_probe_len

                    for g_id, fn in cands:
                        cand_probe_len = len(fn(probe))
                        if cand_probe_len < winner_probe_len:
                            winner_probe_len = cand_probe_len
                            winner_g = g_id
                            winner_fn = fn

                    if winner_fn is not None:
                        cand_full = winner_fn(chunk)
                        if len(cand_full) < len(best_p):
                            best_g, best_p = winner_g, cand_full
                else:
                    # Small file (<= 32 KiB): evaluate candidates directly
                    for g_id, fn in cands:
                        cand = fn(chunk)
                        if len(cand) < len(best_p):
                            best_g, best_p = g_id, cand

        g_name = GANG_NAMES[best_g]
        used_modes[g_name] = used_modes.get(g_name, 0) + 1
        body.extend(struct.pack("<BII", best_g, len(chunk), len(best_p)))
        body.extend(best_p)

        if progress_cb is not None and num_chunks > 1:
            progress_cb(idx + 1, num_chunks, g_name)

    hdr = struct.pack(ELP8_HDR_FMT, ELP8_MAGIC, profile, 0, orig_len, num_chunks, 0, digest, b"\x00" * 6)
    return hdr + bytes(body), {"modes": used_modes}


def decompress_elp8(container: bytes) -> bytes:
    if len(container) < ELP8_HDR_SIZE:
        raise ValueError("ELP8 container too short.")
    magic, _, _, orig_len, num_chunks, _, expected_sha, _ = struct.unpack(ELP8_HDR_FMT, container[:ELP8_HDR_SIZE])
    if magic != ELP8_MAGIC:
        raise ValueError("Invalid ELP8 master header.")

    out = bytearray()
    pos = ELP8_HDR_SIZE
    for _ in range(num_chunks):
        if pos + 9 > len(container):
            raise ValueError("ELP8 chunk header truncated.")
        g_id, c_orig, c_comp = struct.unpack("<BII", container[pos:pos + 9])
        if pos + 9 + c_comp > len(container):
            raise ValueError("ELP8 chunk payload truncated.")
        pos += 9
        payload = container[pos:pos + c_comp]
        pos += c_comp

        if g_id == 0:
            rec = payload
        elif g_id == 1:
            rec = decode_gang1_turbo(payload)
        elif g_id == 2:
            rec = decode_gang2_nibble_transpose(payload)
        elif g_id == 3:
            rec = decode_gang3_tetra3d(payload, c_orig)
        elif g_id == 4:
            rec = decode_gang4_bitplane_pascal(payload)
        elif g_id == 5:
            rec = decode_gang5_mtf_tetra(payload, c_orig)
        else:
            raise ValueError(f"Unknown gear ID: {g_id}")
        if len(rec) != c_orig:
            raise ValueError("ELP8 chunk length mismatch.")
        out.extend(rec)

    if pos != len(container):
        raise ValueError("Trailing bytes found in ELP8 container.")

    res = bytes(out)
    if len(res) != orig_len or sha512_bytes(res) != expected_sha:
        raise ValueError("CRITICAL: SHA-512 integrity check failed!")
    return res
def run_beta_benchmark(name: str, data: bytes):
    t0 = time.perf_counter()
    b_t, _ = compress_elp8(data, profile=0)
    ms_t = max(0.01, (time.perf_counter() - t0) * 1000.0)
    ok_t = sha512_bytes(decompress_elp8(b_t)) == sha512_bytes(data)

    t1 = time.perf_counter()
    b_m, st_m = compress_elp8(data, profile=1)
    ms_m = max(0.01, (time.perf_counter() - t1) * 1000.0)
    ok_m = sha512_bytes(decompress_elp8(b_m)) == sha512_bytes(data)

    mb_s = (len(data) / 1_048_576.0) / (ms_t / 1000.0)
    z_sz = len(zlib.compress(data, level=9))
    l_sz = len(lzma.compress(data))
    print(f"  {name:<30} | TURBO: {len(b_t):6,} B ({mb_s:5.1f} MB/s) | MAX: {len(b_m):6,} B {st_m['modes']} | zlib: {z_sz:6,} | lzma: {l_sz:6,} | SHA512: {'PASS' if (ok_t and ok_m) else 'FAIL'}")


if __name__ == "__main__":
    print("=" * 124)
    print("EULNO BETA ENGINE v0.9.0-beta (ELP8 MASTER CONTAINER INCL. 96B SHA-512 HEADER) - 6-DISCIPLINE BENCHMARK")
    print("=" * 124)
    rng = np.random.default_rng(42)
    pattern = rng.choice((0x00, 0x01, 0x04, 0x10, 0x20, 0x40, 0x80), size=512).astype(np.uint8)
    d_3d = np.tile(pattern, 100)[:50_000].tobytes()
    t = np.arange(50_000, dtype=np.float64)
    d_wave = np.round(128 + 80 * np.sin(t * 0.02) + 30 * np.cos(t * 0.05)).astype(np.uint8).tobytes()
    d_ramp = ((np.arange(50_000, dtype=np.int32) // 4) % 256).astype(np.uint8).tobytes()
    b64 = np.frombuffer(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", dtype=np.uint8)
    d_b64 = rng.choice(b64, size=50_000).tobytes()
    d_json = (b'{"id":1024,"sensor":"tetra_node","active":true,"val":42}\n' * 900)[:50_000]
    d_rand = rng.integers(0, 256, size=50_000, dtype=np.uint8).tobytes()

    run_beta_benchmark("1. Structured 3D Frames", d_3d)
    run_beta_benchmark("2. Smooth Sine Wave (f'')", d_wave)
    run_beta_benchmark("3. Sensor Ramp / Steps", d_ramp)
    run_beta_benchmark("4. Mid-Entropy (Base64)", d_b64)
    run_beta_benchmark("5. JSON / Text Structure", d_json)
    run_beta_benchmark("6. Random Noise (Bypass)", d_rand)
