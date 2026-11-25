# ==============================================================================
# FILE:         eulno_tetra3d_v21.py
# AUTHOR:       Robin Heilos (McCalm) <mccalm@outlook.de>
# PROFILE:      https://huggingface.co
# LICENSE:      EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)
# MODULE:       ELP3D v2.1 & v3.0 Engines, Sparse rANS-20, PocketAIMixer, SHA-512
# PROJECT:      EULNO (.elp3d) - 3D-Tetrahedron & Adaptive Neural Compression Engine
# VERSION:      v0.9.0-beta (PUBLIC_BETA)
# DATE:         2026-11-25
#
# DESCRIPTION:
# Specialized 3D volumetric nibble compression module for the EULNO Suite:
#   - v2.1 (Audit #20): Causal 3D-gradient prediction, hierarchical zero-run
#     encoding (32 / 512 / 4K / 32K), and 20-symbol 24-bit masked sparse rANS.
#   - v3.0 (Pocket AI): Adaptive 3D dimension selection, 7-neighbor volumetric
#     prediction, and causal deterministic probability mixing (PocketAIMixer)
#     with header-free 16-symbol rANS coding and SHA-512 integrity verification.
# ==============================================================================

import sys
import os
import time
import struct
import hashlib
import zlib
import lzma
import numpy as np

# ==============================================================================
# EULNO TETRA-3D ENGINE v2.1 (Audit #20 Refinement -> Target < 450 Bytes)
# Causal 3D-Gradient + Hierarchical Zero-Runs (32/512/4K/32K) + Sparse-rANS
# ==============================================================================

MAGIC = b"ELP3D\x03"
HEADER_FMT = "<6sBBQHH64s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
RESIDUAL_CLASS = np.array((0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4), dtype=np.uint8)

# Alphabet (20 symbols): 0 = 1x0, 1..15 = Residual, 16=32x0, 17=512x0, 18=4096x0, 19=32768x0
NUM_SYMS = 20
RANS_M_BITS = 12
RANS_M = 1 << RANS_M_BITS
RANS_L = 1 << 16


def sha512_bytes(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def forward_3d_gradient_transform(data: bytes, dim_x: int = 32, dim_y: int = 32) -> tuple[np.ndarray, dict]:
    arr = np.frombuffer(data, dtype=np.uint8)
    hi = (arr >> 4) & 0x0F
    lo = arr & 0x0F
    nibbles = np.column_stack((hi, lo)).ravel()

    n_nibbles = len(nibbles)
    plane = dim_x * dim_y
    dim_z = (n_nibbles + plane - 1) // plane
    vol = np.zeros(dim_z * plane, dtype=np.int32)
    vol[:n_nibbles] = nibbles
    vol = vol.reshape(dim_z, dim_y, dim_x)

    a = np.zeros_like(vol)
    a[:, :, 1:] = vol[:, :, :-1]
    b = np.zeros_like(vol)
    b[:, 1:, :] = vol[:, :-1, :]
    c = np.zeros_like(vol)
    c[1:, :, :] = vol[:-1, :, :]

    ab = np.zeros_like(vol)
    ab[:, 1:, 1:] = vol[:, :-1, :-1]
    ac = np.zeros_like(vol)
    ac[1:, :, 1:] = vol[:-1, :, :-1]
    bc = np.zeros_like(vol)
    bc[1:, 1:, :] = vol[:-1, :-1, :]

    err_x = np.abs(b - ab) + np.abs(c - ac)
    err_y = np.abs(a - ab) + np.abs(c - bc)
    err_z = np.abs(a - ac) + np.abs(b - bc)

    use_z = (err_z <= err_x) & (err_z <= err_y)
    use_y = (~use_z) & (err_y <= err_x)
    pred = np.where(use_z, c, np.where(use_y, b, a))
    residual = (vol ^ pred).astype(np.uint8).ravel()[:n_nibbles]

    classes = RESIDUAL_CLASS[residual]
    stats = {
        "exact_pct": float(np.mean(classes == 0) * 100.0),
        "tetra_14_pct": float(np.mean((classes >= 1) & (classes <= 3)) * 100.0),
        "flip_pct": float(np.mean(classes == 4) * 100.0),
    }
    return residual, stats
def inverse_3d_gradient_transform(residual: np.ndarray, orig_byte_len: int, dim_x: int = 32, dim_y: int = 32) -> bytes:
    n_nibbles = orig_byte_len * 2
    plane = dim_x * dim_y
    dim_z = (n_nibbles + plane - 1) // plane
    padded_len = dim_z * plane

    res_pad = np.zeros(padded_len, dtype=np.int32)
    res_pad[:n_nibbles] = residual[:n_nibbles]
    res_list = res_pad.tolist()
    rec_list = [0] * padded_len

    for z in range(dim_z):
        z_off = z * plane
        zm_off = (z - 1) * plane
        for y in range(dim_y):
            zy_off = z_off + y * dim_x
            zym_off = z_off + (y - 1) * dim_x
            zmy_off = zm_off + y * dim_x
            zmym_off = zm_off + (y - 1) * dim_x
            for x in range(dim_x):
                idx = zy_off + x
                a = rec_list[idx - 1] if x > 0 else 0
                b = rec_list[zym_off + x] if y > 0 else 0
                c = rec_list[zmy_off + x] if z > 0 else 0
                ab = rec_list[zym_off + x - 1] if (x > 0 and y > 0) else 0
                ac = rec_list[zmy_off + x - 1] if (x > 0 and z > 0) else 0
                bc = rec_list[zmym_off + x] if (y > 0 and z > 0) else 0

                err_x = abs(b - ab) + abs(c - ac)
                err_y = abs(a - ab) + abs(c - bc)
                err_z = abs(a - ac) + abs(b - bc)

                if err_z <= err_x and err_z <= err_y:
                    pred = c
                elif err_y <= err_x:
                    pred = b
                else:
                    pred = a
                rec_list[idx] = res_list[idx] ^ pred

    rec_arr = np.array(rec_list[:n_nibbles], dtype=np.uint8).reshape(-1, 2)
    hi_rec, lo_rec = rec_arr.T
    return ((hi_rec << 4) | lo_rec).tobytes()


def _flush_zeros(out: list, zeros: int):
    for sym, step in ((19, 32768), (18, 4096), (17, 512), (16, 32)):
        while zeros >= step:
            out.append(sym)
            zeros -= step
    if zeros > 0:
        out.extend((0,) * zeros)


def pack_zero_runs_v21(residual: np.ndarray) -> np.ndarray:
    out = []
    zeros = 0
    for val in residual.tolist():
        if val == 0:
            zeros += 1
        else:
            if zeros > 0:
                _flush_zeros(out, zeros)
                zeros = 0
            out.append(val)
    if zeros > 0:
        _flush_zeros(out, zeros)
    return np.array(out, dtype=np.uint8)


def unpack_zero_runs_v21(tokens: np.ndarray, expected_len: int) -> np.ndarray:
    run_map = {16: 32, 17: 512, 18: 4096, 19: 32768}
    out = []
    for t in tokens.tolist():
        if t in run_map:
            out.extend((0,) * run_map[t])
        else:
            out.append(t)
    return np.array(out[:expected_len], dtype=np.uint8)


def normalize_freqs_20(symbols: np.ndarray) -> np.ndarray:
    counts = np.bincount(symbols, minlength=NUM_SYMS).astype(np.int64)
    total = int(np.sum(counts))
    freqs = np.zeros(NUM_SYMS, dtype=np.uint16)
    if total == 0:
        freqs.put(0, RANS_M)
        return freqs

    (present,) = np.where(counts > 0)
    scaled = np.maximum(1, (counts[present] * RANS_M) // total)
    diff = RANS_M - int(np.sum(scaled))

    order = np.argsort(-counts[present])
    idx = 0
    while diff != 0:
        target = int(order.item(idx % len(order)))
        if diff > 0:
            scaled.put(target, int(scaled.item(target)) + 1)
            diff -= 1
        elif int(scaled.item(target)) > 1:
            scaled.put(target, int(scaled.item(target)) - 1)
            diff += 1
        idx += 1

    freqs[present] = scaled.astype(np.uint16)
    return freqs
def encode_sparse_table(freqs: np.ndarray) -> bytes:
    """24-bit mask (3 bytes) + active uint16 frequencies only instead of 40 bytes."""
    mask = 0
    active_vals = []
    for s in range(NUM_SYMS):
        f = int(freqs.item(s))
        if f > 0:
            mask |= (1 << s)
            active_vals.append(f)
    mask_bytes = struct.pack("<I", mask)[:3]
    vals_bytes = np.array(active_vals, dtype=np.uint16).tobytes()
    return mask_bytes + vals_bytes


def decode_sparse_table(buf: bytes) -> tuple[np.ndarray, int]:
    """Reads the 3-byte bitmask + active uint16 frequencies."""
    (mask,) = struct.unpack("<I", buf[:3] + b"\x00")
    freqs = np.zeros(NUM_SYMS, dtype=np.uint16)
    offset = 3
    for s in range(NUM_SYMS):
        if (mask >> s) & 1:
            (val,) = struct.unpack("<H", buf[offset:offset + 2])
            freqs.put(s, val)
            offset += 2
    return freqs, offset


def rans20_encode(symbols: np.ndarray) -> bytes:
    freqs = normalize_freqs_20(symbols)
    cum = np.zeros(NUM_SYMS + 1, dtype=np.uint32)
    cum[1:] = np.cumsum(freqs, dtype=np.uint32)

    state = RANS_L
    out_words = bytearray()
    freq_list = freqs.tolist()
    cum_list = cum.tolist()

    for s_int in symbols[::-1].tolist():
        f = freq_list[s_int]
        c = cum_list[s_int]
        x_max = ((RANS_L >> RANS_M_BITS) << 16) * f
        while state >= x_max:
            out_words.extend(struct.pack("<H", state & 0xFFFF))
            state >>= 16
        state = ((state // f) << RANS_M_BITS) + (state % f) + c

    sparse_tbl = encode_sparse_table(freqs)
    header_rans = sparse_tbl + struct.pack("<II", len(symbols), state)
    return header_rans + bytes(out_words)


def rans20_decode(payload: bytes) -> np.ndarray:
    freqs, tbl_len = decode_sparse_table(payload)
    num_tokens, state = struct.unpack("<II", payload[tbl_len:tbl_len + 8])
    word_stream = payload[tbl_len + 8:]

    cum = np.zeros(NUM_SYMS + 1, dtype=np.uint32)
    cum[1:] = np.cumsum(freqs, dtype=np.uint32)

    slot_to_sym = np.empty(RANS_M, dtype=np.uint8)
    for s in range(NUM_SYMS):
        start = int(cum.item(s))
        end = int(cum.item(s + 1))
        if end > start:
            slot_to_sym[start:end] = s

    slot_list = slot_to_sym.tolist()
    freq_list = freqs.tolist()
    cum_list = cum.tolist()

    out = [0] * num_tokens
    pos = len(word_stream)
    mask = RANS_M - 1

    for i in range(num_tokens):
        slot = state & mask
        s = slot_list[slot]
        out[i] = s
        f = freq_list[s]
        c = cum_list[s]
        state = f * (state >> RANS_M_BITS) + slot - c
        while state < RANS_L and pos >= 2:
            pos -= 2
            (w,) = struct.unpack("<H", word_stream[pos:pos + 2])
            state = (state << 16) | w

    return np.array(out, dtype=np.uint8)


# --- Automatic Gear Adapter (for the EULNO Master Engine) ---
def encode_tetra3d_chunk(chunk: bytes, dim_x: int = 32, dim_y: int = 32) -> bytes:
    """Compresses a raw chunk without container header (directly for the MDL router)."""
    residual, _ = forward_3d_gradient_transform(chunk, dim_x=dim_x, dim_y=dim_y)
    tokens = pack_zero_runs_v21(residual)
    return rans20_encode(tokens)


def decode_tetra3d_chunk(payload: bytes, orig_byte_len: int, dim_x: int = 32, dim_y: int = 32) -> bytes:
    """Decodes a compressed chunk bit-accurately (directly for the MDL router)."""
    tokens = rans20_decode(payload)
    residual = unpack_zero_runs_v21(tokens, orig_byte_len * 2)
    return inverse_3d_gradient_transform(residual, orig_byte_len, dim_x=dim_x, dim_y=dim_y)


# --- Standalone .elp v2.1 Container & Benchmark ---
def compress_elp_3d_v21(data: bytes, dim_x: int = 32, dim_y: int = 32) -> tuple[bytes, dict]:
    orig_len = len(data)
    digest = sha512_bytes(data)
    if orig_len == 0:
        return struct.pack(HEADER_FMT, MAGIC, 0, 0, 0, dim_x, dim_y, digest), {"mode": "RAW_EMPTY"}

    residual, stats = forward_3d_gradient_transform(data, dim_x=dim_x, dim_y=dim_y)
    tokens = pack_zero_runs_v21(residual)
    rans_payload = rans20_encode(tokens)

    if len(rans_payload) < orig_len:
        mode = 3
        body = rans_payload
        stats["mode"] = "TETRA3D_V21_SPARSE"
    else:
        mode = 0
        body = data
        stats["mode"] = "RAW_BYPASS"

    return struct.pack(HEADER_FMT, MAGIC, mode, 0, orig_len, dim_x, dim_y, digest) + body, stats


def decompress_elp_3d_v21(container: bytes) -> bytes:
    magic, mode, _, orig_len, dim_x, dim_y, expected_sha = struct.unpack(HEADER_FMT, container[:HEADER_SIZE])
    if magic != MAGIC:
        raise ValueError("Invalid ELP3D v2.1 header.")
    body = container[HEADER_SIZE:]
    rec = body[:orig_len] if mode == 0 else decode_tetra3d_chunk(body, orig_len, dim_x=dim_x, dim_y=dim_y)
    if sha512_bytes(rec) != expected_sha:
        raise ValueError("SHA-512 checksum failed!")
    return rec


def run_benchmark_v21(name: str, data: bytes):
    t0 = time.perf_counter()
    elp_blob, stats = compress_elp_3d_v21(data)
    t_enc = (time.perf_counter() - t0) * 1000.0
    t1 = time.perf_counter()
    rec = decompress_elp_3d_v21(elp_blob)
    t_dec = (time.perf_counter() - t1) * 1000.0

    sha_ok = (sha512_bytes(rec) == sha512_bytes(data))
    zlib_sz = len(zlib.compress(data, level=9))
    lzma_sz = len(lzma.compress(data))
    elp_sz = len(elp_blob)

    print("=" * 82)
    print(f"AUDIT #20 v2.1 (4K/32K-RUNS + 24-BIT SPARSE rANS): {name} ({len(data):,} B)")
    print("=" * 82)
    print(f"  SHA-512 Integrity   : {'PASS' if sha_ok else 'FAIL'} | Mode: {stats.get('mode')} | Time: {t_enc:.1f}/{t_dec:.1f} ms")
    print(f"  Original            : {len(data):8,} Bytes")
    print(f"  EULNO v2.1 (.elp)   : {elp_sz:8,} Bytes (incl. 84B SHA-512 Hdr | Payload: {elp_sz - HEADER_SIZE:,} B)")
    print(f"  Standard zlib L9    : {zlib_sz:8,} Bytes (without SHA-512)")
    print(f"  Standard lzma       : {lzma_sz:8,} Bytes (without SHA-512)")
    print()


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    pattern = rng.choice((0x00, 0x01, 0x04, 0x10, 0x20, 0x40, 0x80), size=512).astype(np.uint8)
    struc_data = np.tile(pattern, 100)[:50_000].tobytes()
    b64_chars = np.frombuffer(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", dtype=np.uint8)
    bal_data = rng.choice(b64_chars, size=50_000).tobytes()
    rand_data = rng.integers(0, 256, size=50_000, dtype=np.uint8).tobytes()

    run_benchmark_v21("1. Structured 3D Frames (50 KB)", struc_data)
    run_benchmark_v21("2. Balanced Mid-Entropy / Base64 (50 KB)", bal_data)
    run_benchmark_v21("3. Pure Random Noise (50 KB)", rand_data)
# ------------------------------------------------------------------------------
# 3. ADAPTIVE 16-SYMBOL rANS ENCODER & DECODER (with AI Probabilities)
# ------------------------------------------------------------------------------
RANS_L = 1 << 16

CANDIDATE_DIMS = ((16, 16), (32, 32), (64, 32), (16, 64), (128, 16))
RANS_M_BITS = 12
RANS_M = 1 << RANS_M_BITS


def compute_3d_prediction_array(nibbles: np.ndarray, dim_x: int, dim_y: int) -> np.ndarray:
    n = len(nibbles)
    plane = dim_x * dim_y
    dim_z = (n + plane - 1) // plane
    vol = np.zeros(dim_z * plane, dtype=np.int32)
    vol[:n] = nibbles
    vol = vol.reshape(dim_z, dim_y, dim_x)

    a = np.zeros_like(vol)
    a[:, :, 1:] = vol[:, :, :-1]
    b = np.zeros_like(vol)
    b[:, 1:, :] = vol[:, :-1, :]
    c = np.zeros_like(vol)
    c[1:, :, :] = vol[:-1, :, :]

    ab = np.zeros_like(vol)
    ab[:, 1:, 1:] = vol[:, :-1, :-1]
    ac = np.zeros_like(vol)
    ac[1:, :, 1:] = vol[:-1, :, :-1]
    bc = np.zeros_like(vol)
    bc[1:, 1:, :] = vol[:-1, :-1, :]
    abc = np.zeros_like(vol)
    abc[1:, 1:, 1:] = vol[:-1, :-1, :-1]

    err_x = np.abs(b - ab) + np.abs(c - ac)
    err_y = np.abs(a - ab) + np.abs(c - bc)
    err_z = np.abs(a - ac) + np.abs(b - bc)

    use_z = (err_z <= err_x) & (err_z <= err_y)
    use_y = (~use_z) & (err_y <= err_x)
    pred_axis = np.where(use_z, c, np.where(use_y, b, a))

    pred_plane = np.clip((a + b + c) - (ab + ac + bc) + abc, 0, 15)
    smooth_3d = (err_x + err_y + err_z) <= 2
    return np.where(smooth_3d, pred_plane, pred_axis).astype(np.uint8).ravel()[:n]


def select_best_3d_dims(nibbles: np.ndarray) -> tuple[int, int]:
    if len(nibbles) < 512:
        return (16, 16)
    best_dims = (32, 32)
    best_hits = -1
    sample = nibbles[: min(len(nibbles), 32768)]
    for dx, dy in CANDIDATE_DIMS:
        pred = compute_3d_prediction_array(sample, dx, dy)
        hits = int(np.sum(pred == sample))
        if hits > best_hits:
            best_hits = hits
            best_dims = (dx, dy)
    return best_dims


class PocketAIMixer:
    def __init__(self):
        self.res_counts = np.ones(16, dtype=np.int32) * 4
        self.res_counts.put(0, 32)
        self.res_total = int(np.sum(self.res_counts))
        self.ctx_counts = np.ones((512, 16), dtype=np.int32)
        self.ctx_totals = np.ones(512, dtype=np.int32) * 16
        self.prev1 = 0
        self.prev2 = 0
        self.w_3d = 128

    def predict_freqs(self, pred_3d: int, step_idx: int):
        phase = step_idx & 1
        ctx_id = (phase << 8) | (self.prev1 << 4) | self.prev2
        idx_map = np.arange(16, dtype=np.int32) ^ pred_3d
        p1 = (self.res_counts[idx_map] * RANS_M) // self.res_total
        c_row = self.ctx_counts[ctx_id]
        c_tot = int(self.ctx_totals.item(ctx_id))
        p2 = (c_row * RANS_M) // c_tot

        w1 = self.w_3d
        w2 = 256 - w1
        mixed = np.maximum(1, (w1 * p1 + w2 * p2) >> 8)
        diff = RANS_M - int(np.sum(mixed))
        if diff != 0:
            best_s = int(np.argmax(mixed))
            new_val = int(mixed.item(best_s)) + diff
            if new_val >= 1:
                mixed.put(best_s, new_val)
            else:
                for s in range(16):
                    if diff == 0:
                        break
                    cur = int(mixed.item(s))
                    if diff > 0:
                        mixed.put(s, cur + 1)
                        diff -= 1
                    elif cur > 1:
                        mixed.put(s, cur - 1)
                        diff += 1
        return mixed.astype(np.uint16), p1, p2, ctx_id

    def update(self, actual_sym: int, pred_3d: int, p1: np.ndarray, p2: np.ndarray, ctx_id: int):
        prob1 = int(p1.item(actual_sym))
        prob2 = int(p2.item(actual_sym))
        if prob1 > prob2 and self.w_3d < 252:
            self.w_3d += 4
        elif prob2 > prob1 and self.w_3d > 4:
            self.w_3d -= 4

        r = actual_sym ^ pred_3d
        self.res_counts.put(r, int(self.res_counts.item(r)) + 8)
        self.res_total += 8
        if self.res_total > 16384:
            self.res_counts = np.maximum(1, self.res_counts >> 1)
            self.res_total = int(np.sum(self.res_counts))

        old_c = int(self.ctx_counts[ctx_id, actual_sym])
        self.ctx_counts[ctx_id, actual_sym] = old_c + 4
        new_tot = int(self.ctx_totals.item(ctx_id)) + 4
        if new_tot > 2048:
            self.ctx_counts[ctx_id] = np.maximum(1, self.ctx_counts[ctx_id] >> 1)
            new_tot = int(np.sum(self.ctx_counts[ctx_id]))
        self.ctx_totals.put(ctx_id, new_tot)
        self.prev2 = self.prev1
        self.prev1 = actual_sym
def encode_adaptive_pocket_ai(nibbles: np.ndarray, dim_x: int, dim_y: int) -> tuple[bytes, dict]:
    """
    Traverses the tetrahedron voxels forward to learn the AI probabilities
    and then encodes the entire stream backward with those exact learned
    distributions into an rANS bitstream (0 bytes frequency table in header!).
    """
    n = len(nibbles)
    preds_3d = compute_3d_prediction_array(nibbles, dim_x, dim_y)
    exact_hits = float(np.mean(preds_3d == nibbles) * 100.0)

    ai = PocketAIMixer()
    # Forward pass: Pocket AI learns causally from voxel 0 to n-1
    freqs_history = list((None,) * n)
    nib_list = nibbles.tolist()
    pred_list = preds_3d.tolist()

    for i in range(n):
        sym = nib_list[i]
        p3d = pred_list[i]
        freqs, p1, p2, ctx_id = ai.predict_freqs(p3d, i)
        freqs_history[i] = freqs
        ai.update(sym, p3d, p1, p2, ctx_id)

    # Backward pass: rANS encodes with the exact recorded AI probabilities
    state = RANS_L
    out_words = bytearray()
    for i in range(n - 1, -1, -1):
        sym = nib_list[i]
        freqs = freqs_history[i]
        f = int(freqs.item(sym))
        c = int(np.sum(freqs[:sym]))
        x_max = ((RANS_L >> RANS_M_BITS) << 16) * f
        while state >= x_max:
            out_words.extend(struct.pack("<H", state & 0xFFFF))
            state >>= 16
        state = ((state // f) << RANS_M_BITS) + (state % f) + c

    payload = struct.pack("<I", state) + bytes(out_words)
    stats = {
        "exact_3d_pct": exact_hits,
        "final_w_3d_pct": round((ai.w_3d / 256.0) * 100.0, 1),
    }
    return payload, stats


def decode_adaptive_pocket_ai(payload: bytes, orig_byte_len: int, dim_x: int, dim_y: int) -> bytes:
    """
    Decodes voxel by voxel causally in 3D space while the Pocket AI synchronously
    learns the exact same probabilities as during packing!
    """
    n_nibbles = orig_byte_len * 2
    plane = dim_x * dim_y
    dim_z = (n_nibbles + plane - 1) // plane
    padded_len = dim_z * plane

    (state,) = struct.unpack("<I", payload[:4])
    word_stream = payload[4:]
    pos = len(word_stream)
    mask = RANS_M - 1

    ai = PocketAIMixer()
    rec_vol = list((0,) * padded_len)

    for i in range(n_nibbles):
        z = i // plane
        rem = i % plane
        y = rem // dim_x
        x = rem % dim_x

        a = rec_vol[i - 1] if x > 0 else 0
        b = rec_vol[i - dim_x] if y > 0 else 0
        c = rec_vol[i - plane] if z > 0 else 0

        ab = rec_vol[i - dim_x - 1] if (x > 0 and y > 0) else 0
        ac = rec_vol[i - plane - 1] if (x > 0 and z > 0) else 0
        bc = rec_vol[i - plane - dim_x] if (y > 0 and z > 0) else 0
        abc = rec_vol[i - plane - dim_x - 1] if (x > 0 and y > 0 and z > 0) else 0

        err_x = abs(b - ab) + abs(c - ac)
        err_y = abs(a - ab) + abs(c - bc)
        err_z = abs(a - ac) + abs(b - bc)

        if err_z <= err_x and err_z <= err_y:
            pred_axis = c
        elif err_y <= err_x:
            pred_axis = b
        else:
            pred_axis = a

        if (err_x + err_y + err_z) <= 2:
            val_p = (a + b + c) - (ab + ac + bc) + abc
            p3d = 0 if val_p < 0 else (15 if val_p > 15 else val_p)
        else:
            p3d = pred_axis

        freqs, p1, p2, ctx_id = ai.predict_freqs(p3d, i)
        cum = np.zeros(17, dtype=np.int32)
        cum[1:] = np.cumsum(freqs, dtype=np.int32)

        slot = state & mask
        sym = int(np.searchsorted(cum, slot, side="right")) - 1
        rec_vol[i] = sym

        f = int(freqs.item(sym))
        c_off = int(cum.item(sym))
        state = f * (state >> RANS_M_BITS) + slot - c_off
        while state < RANS_L and pos >= 2:
            pos -= 2
            (w,) = struct.unpack("<H", word_stream[pos:pos + 2])
            state = (state << 16) | w

        ai.update(sym, p3d, p1, p2, ctx_id)

    rec_arr = np.array(rec_vol[:n_nibbles], dtype=np.uint8).reshape(-1, 2)
    hi_rec, lo_rec = rec_arr.T
    return ((hi_rec << 4) | lo_rec).tobytes()


# ------------------------------------------------------------------------------
# 4. CONTAINER & BENCHMARK v3.0 (3D-Tetrahedron + Pocket AI)
# ------------------------------------------------------------------------------
def compress_elp_v3(data: bytes) -> tuple[bytes, dict]:
    orig_len = len(data)
    digest = sha512_bytes(data)
    if orig_len == 0:
        return struct.pack(HEADER_FMT, MAGIC, 0, 0, 0, 16, 16, digest), {"mode": "RAW_EMPTY"}

    arr = np.frombuffer(data, dtype=np.uint8)
    nibbles = np.column_stack(((arr >> 4) & 0x0F, arr & 0x0F)).ravel()
    dim_x, dim_y = select_best_3d_dims(nibbles)

    payload, stats = encode_adaptive_pocket_ai(nibbles, dim_x, dim_y)
    if len(payload) < orig_len:
        mode = 4
        body = payload
        stats["mode"] = f"TETRA3D_POCKET_AI ({dim_x}x{dim_y})"
    else:
        mode = 0
        body = data
        stats["mode"] = "RAW_BYPASS"

    return struct.pack(HEADER_FMT, MAGIC, mode, 0, orig_len, dim_x, dim_y, digest) + body, stats


def decompress_elp_v3(container: bytes) -> bytes:
    magic, mode, _, orig_len, dim_x, dim_y, expected_sha = struct.unpack(HEADER_FMT, container[:HEADER_SIZE])
    if magic != MAGIC:
        raise ValueError("Invalid ELP3D v3 header.")
    body = container[HEADER_SIZE:]
    rec = body[:orig_len] if mode == 0 else decode_adaptive_pocket_ai(body, orig_len, dim_x, dim_y)
    if sha512_bytes(rec) != expected_sha:
        raise ValueError("SHA-512 checksum failed!")
    return rec


def run_benchmark_v3(name: str, data: bytes):
    t0 = time.perf_counter()
    blob, stats = compress_elp_v3(data)
    t_enc = (time.perf_counter() - t0) * 1000.0
    t1 = time.perf_counter()
    rec = decompress_elp_v3(blob)
    t_dec = (time.perf_counter() - t1) * 1000.0

    ok = (sha512_bytes(rec) == sha512_bytes(data))
    zlib_sz = len(zlib.compress(data, level=9))
    lzma_sz = len(lzma.compress(data))
    elp_sz = len(blob)

    print("=" * 84)
    print(f"EULNO v3.0 (3D-7-NEIGHBORS + DETERMINISTIC POCKET AI): {name}")
    print("=" * 84)
    print(f"  SHA-512 : {'PASS' if ok else 'FAIL'} | Mode: {stats.get('mode')} | Time: {t_enc:.0f}/{t_dec:.0f} ms")
    print(f"  3D Hits : {stats.get('exact_3d_pct', 0):.2f} % | Final AI Confidence on 3D Expert: {stats.get('final_w_3d_pct', 0):.1f} %")
    print(f"  Original: {len(data):8,} B | EULNO v3.0: {elp_sz:8,} B (Payload: {elp_sz - HEADER_SIZE:,} B) | zlib: {zlib_sz:8,} B | lzma: {lzma_sz:8,} B")
    print()


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    pattern = rng.choice((0x00, 0x01, 0x04, 0x10, 0x20, 0x40, 0x80), size=512).astype(np.uint8)
    struc_data = np.tile(pattern, 100)[:50_000].tobytes()
    b64_chars = np.frombuffer(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", dtype=np.uint8)
    bal_data = rng.choice(b64_chars, size=50_000).tobytes()
    rand_data = rng.integers(0, 256, size=50_000, dtype=np.uint8).tobytes()

    run_benchmark_v3("1. Structured 3D Frames (50 KB)", struc_data)
    run_benchmark_v3("2. Balanced Mid-Entropy / Base64 (50 KB)", bal_data)
    run_benchmark_v3("3. Pure Random Noise (50 KB)", rand_data)
