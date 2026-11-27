# ==============================================================================
# FILE:         eulno_beta_engine.py
# AUTHOR:       Robin Heilos (McCalm) <mccalm@outlook.de>
# PROFILE:      https://huggingface.co | https://eulno.org
# LICENSE:      EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)
# MODULE:       ELP8 RFC Master Container, MDL Router G0..G6, O(1) Streaming
# PROJECT:      EULNO (.elp8) - 3D-Tetrahedron & Wheel-8 Compression Engine
# VERSION:      v0.9.6-beta (RFC draft-heilos-eulno-elp8-00 Compliant)
# DATE:         2026-11-25
# ==============================================================================

import os
import stat
import struct
import hashlib
import hmac
import tempfile
import zlib
import numpy as np

try:
    from nacl.bindings import (
        crypto_pwhash_alg,
        crypto_pwhash_ALG_ARGON2ID13,
        crypto_pwhash_argon2id_OPSLIMIT_INTERACTIVE,
        crypto_pwhash_argon2id_MEMLIMIT_INTERACTIVE,
        crypto_secretstream_xchacha20poly1305_KEYBYTES,
        crypto_secretstream_xchacha20poly1305_HEADERBYTES,
        crypto_secretstream_xchacha20poly1305_ABYTES,
        crypto_secretstream_xchacha20poly1305_TAG_MESSAGE,
        crypto_secretstream_xchacha20poly1305_TAG_FINAL,
        crypto_secretstream_xchacha20poly1305_state,
        crypto_secretstream_xchacha20poly1305_init_push,
        crypto_secretstream_xchacha20poly1305_push,
        crypto_secretstream_xchacha20poly1305_init_pull,
        crypto_secretstream_xchacha20poly1305_pull,
    )
    HAS_PYNACL = True
except ImportError:
    HAS_PYNACL = False


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
ELP8_RFC_VERSION = 0x01
ELP8_BASE_FMT = ">4sBBBBQQ"
ELP8_BASE_SIZE = 24

FLAG_COUNTER_ROTATING = 0x01
FLAG_INWARD_MIRROR_SPLIT = 0x02
FLAG_MULTI_CHUNK_GEARS = 0x04
FLAG_AUTHENTICATED_ORIGIN = 0x08
FLAG_ENCRYPTED_PAYLOAD = 0x10

DEFAULT_MODULUS = 8
HASH_SHA256 = 0x01
HASH_SHA512 = 0x02
HASH_SPECS = {
    HASH_SHA256: ("sha256", 32),
    HASH_SHA512: ("sha512", 64),
}

DEFAULT_CHUNK_SIZE = 1048576
MAX_SAFE_CHUNK_SIZE = 64 * 1024 * 1024
PROBE_HALF_SIZE = 16384

GANG_NAMES = {
    0: "G0_RAW",
    1: "G1_TURBO_ZIGZAG",
    2: "G2_NIBBLE_16x64",
    3: "G3_TETRA3D_SPARSE",
    4: "G4_BITPLANE_PASCAL",
    5: "G5_MTF_TETRA3D",
    6: "G6_WHEEL8_SPIRAL",
}


def derive_argon2id_key(password: str, salt16: bytes) -> bytes:
    if not HAS_PYNACL:
        raise RuntimeError("Encryption requires 'pynacl' (install via: python -m pip install pynacl).")
    if not password:
        raise ValueError("Password must not be empty.")
    return crypto_pwhash_alg(
        outlen=crypto_secretstream_xchacha20poly1305_KEYBYTES,
        passwd=password.encode("utf-8"),
        salt=salt16,
        opslimit=crypto_pwhash_argon2id_OPSLIMIT_INTERACTIVE,
        memlimit=crypto_pwhash_argon2id_MEMLIMIT_INTERACTIVE,
        alg=crypto_pwhash_ALG_ARGON2ID13,
    )


def sha512_bytes(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


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
        raise MemoryError("Chunk exceeds 64 MiB safety ceiling")
    d = zlib.decompressobj()
    out = d.decompress(data, expected_len + 1)
    if d.unconsumed_tail or len(out) != expected_len or d.flush() != b"":
        raise ValueError("Decompression bomb or length mismatch")
    return out
def is_incompressible_noise(arr: np.ndarray) -> bool:
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


def spiral_transform_fast(data: bytes, s0: int, modulus: int = DEFAULT_MODULUS) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    arr = np.frombuffer(data, dtype=np.uint8)
    mid = n // 2

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


def inverse_spiral_transform_fast(transformed: bytes, s0: int, modulus: int = DEFAULT_MODULUS) -> bytes:
    n = len(transformed)
    if n == 0:
        return b""
    arr = np.frombuffer(transformed, dtype=np.uint8)
    mid = n // 2

    if mid > 0:
        idx_l = np.arange(mid, dtype=np.int64)
        bias_l = (((s0 + idx_l) % modulus) * 31) & 0xFF
        val_l = (np.cumsum(arr[:mid].astype(np.int64)) + (s0 & 0xFF)) & 0xFF
        rec_l = val_l.astype(np.uint8) ^ bias_l.astype(np.uint8)
    else:
        rec_l = np.empty(0, dtype=np.uint8)

    right_rev = arr[mid:][::-1]
    idx_r = np.arange(len(right_rev), dtype=np.int64)
    bias_r = (((s0 - idx_r) % modulus) * 31) & 0xFF
    val_r = (np.cumsum(right_rev.astype(np.int64)) + (s0 & 0xFF)) & 0xFF
    rec_r = (val_r.astype(np.uint8) ^ bias_r.astype(np.uint8))[::-1]
    return np.concatenate((rec_l, rec_r)).tobytes()


def encode_gang6_wheel8(chunk: bytes, chunk_s0: int, z_level: int = 6) -> bytes:
    return zlib.compress(spiral_transform_fast(chunk, chunk_s0, DEFAULT_MODULUS), level=z_level)


def decode_gang6_wheel8(payload: bytes, c_orig: int, chunk_s0: int, modulus: int = DEFAULT_MODULUS) -> bytes:
    return inverse_spiral_transform_fast(_decompress_bounded(payload, c_orig), chunk_s0, modulus)

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
    best_cfg, best_small = (1, 1), -1
    sample = arr[: min(len(arr), 16384)]
    for s, k in TURBO_CANDIDATES:
        small = int(np.count_nonzero(_turbo_fwd(sample, s, k) <= 4))
        if small > best_small:
            best_small, best_cfg = small, (s, k)
    s_best, k_best = best_cfg
    return struct.pack("<HB", s_best, k_best) + zlib.compress(_turbo_fwd(arr, s_best, k_best).tobytes(), level=z_level)


def decode_gang1_turbo(payload: bytes, c_orig: int) -> bytes:
    if len(payload) < 3:
        raise ValueError("Gear 1 payload corrupted.")
    stride, order = struct.unpack("<HB", payload[:3])
    return _turbo_inv(np.frombuffer(_decompress_bounded(payload[3:], c_orig), dtype=np.uint8), stride, order).tobytes()


def encode_gang2_nibble_transpose(chunk: bytes) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    nibbles = np.column_stack(((arr >> 4) & 0x0F, arr & 0x0F)).ravel()
    pad = (64 - (len(nibbles) % 64)) % 64
    if pad > 0:
        nibbles = np.concatenate((nibbles, np.zeros(pad, dtype=np.uint8)))
    pairs = nibbles.reshape(-1, 64).T.ravel().reshape(-1, 2)
    return struct.pack("<I", len(arr)) + zlib.compress(((pairs[:, 0] << 4) | pairs[:, 1]).tobytes(), level=6)


def decode_gang2_nibble_transpose(payload: bytes) -> bytes:
    if len(payload) < 4:
        raise ValueError("Gear 2 payload corrupted.")
    (orig_n,) = struct.unpack("<I", payload[:4])
    padded_bytes = ((orig_n * 2 + 63) // 64) * 32
    raw_b = np.frombuffer(_decompress_bounded(payload[4:], padded_bytes), dtype=np.uint8)
    trans = np.column_stack(((raw_b >> 4) & 0x0F, raw_b & 0x0F)).ravel()
    pairs = trans.reshape(64, len(trans) // 64).T.ravel()[: orig_n * 2].reshape(-1, 2)
    return ((pairs[:, 0] << 4) | pairs[:, 1]).tobytes()


def encode_gang3_tetra3d(chunk: bytes) -> bytes:
    if not HAS_TETRA_V21:
        raise RuntimeError("Missing dependency 'eulno_tetra3d_v21' required to encode G3/G5 chunks.")
    res, _ = forward_3d_gradient_transform(chunk, dim_x=16, dim_y=64)
    return rans20_encode(pack_zero_runs_v21(res))


def decode_gang3_tetra3d(payload: bytes, orig_byte_len: int) -> bytes:
    if not HAS_TETRA_V21:
        raise RuntimeError(
            "Missing dependency 'eulno_tetra3d_v21' required to decode G3/G5 chunks."
        )
    return inverse_3d_gradient_transform(
        unpack_zero_runs_v21(rans20_decode(payload), orig_byte_len * 2),
        orig_byte_len,
        dim_x=16,
        dim_y=64,
    )

def _build_pascal64() -> list:
    c = [[0] * 65 for _ in range(65)]
    for n in range(65):
        c[n][0] = 1
        for k in range(1, n + 1):
            c[n][k] = c[n - 1][k - 1] + c[n - 1][k]
    return c

PASCAL64 = _build_pascal64()
BITS_FOR_K64 = tuple(0 if PASCAL64[64][k] <= 1 else (PASCAL64[64][k] - 1).bit_length() for k in range(65))
FULL_U64 = 0xFFFFFFFFFFFFFFFF


def _enc_u64_ctz(w: int) -> tuple[int, int, int]:
    if w == 0:
        return 0, 0, 0
    if w == FULL_U64:
        return 64, 0, 0
    k = w.bit_count()
    positions, tmp = [], w
    while tmp:
        lsb = tmp & -tmp
        positions.append(lsb.bit_length() - 1)
        tmp ^= lsb
    index, rem_k = 0, k
    for pos in reversed(positions):
        index += PASCAL64[pos][rem_k]
        rem_k -= 1
    return k, index, BITS_FOR_K64[k]


def _dec_u64_pascal(k: int, index: int) -> int:
    if k == 0:
        return 0
    if k == 64:
        return FULL_U64
    val64, rem_k, rem_idx = 0, k, index
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
    bp_bytes = np.packbits(np.unpackbits(arr).reshape(-1, 8).T.ravel())
    bp_xor = bp_bytes.copy()
    bp_xor[1:] ^= bp_bytes[:-1]
    pad_len = (8 - (len(bp_xor) % 8)) % 8
    words = np.frombuffer(bp_xor.tobytes() + b"\x00" * pad_len, dtype="<u8").tolist()
    k_list, packed_idx = bytearray(), bytearray()
    bit_buf = bit_cnt = 0
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
    k_list = _decompress_bounded(payload[8 : 8 + k_len], (orig_len + 7) // 8)
    idx_stream = payload[8 + k_len :]
    words = np.empty(len(k_list), dtype="<u8")
    byte_pos = bit_buf = bit_cnt = 0
    for i, k in enumerate(k_list):
        if k > 64:
            raise ValueError("Corrupted Gear 4 weight k > 64.")
        n_bits = BITS_FOR_K64[k]
        while bit_cnt < n_bits and byte_pos < len(idx_stream):
            bit_buf |= idx_stream[byte_pos] << bit_cnt
            byte_pos += 1
            bit_cnt += 8
        idx = bit_buf & ((1 << n_bits) - 1) if n_bits > 0 else 0
        bit_buf >>= n_bits
        bit_cnt -= n_bits
        words[i] = _dec_u64_pascal(k, idx)
    bp_xor = np.frombuffer(words.tobytes()[:orig_len], dtype=np.uint8)
    bp_bytes = np.bitwise_xor.accumulate(bp_xor)
    return np.packbits(np.unpackbits(bp_bytes).reshape(8, -1).T.ravel()).tobytes()


def encode_gang5_mtf_tetra(chunk: bytes) -> bytes:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    nibbles = np.column_stack(((arr >> 4) & 0x0F, arr & 0x0F)).ravel().tolist()
    table, mtf_out = list(range(16)), [0] * len(nibbles)
    for i, sym in enumerate(nibbles):
        idx = table.index(sym)
        mtf_out[i] = idx
        if idx > 0:
            table.pop(idx)
            table.insert(0, sym)
    mtf_arr = np.array(mtf_out, dtype=np.uint8).reshape(-1, 2)
    return encode_gang3_tetra3d(((mtf_arr[:, 0] << 4) | mtf_arr[:, 1]).tobytes())


def decode_gang5_mtf_tetra(payload: bytes, orig_byte_len: int) -> bytes:
    m_arr = np.frombuffer(decode_gang3_tetra3d(payload, orig_byte_len), dtype=np.uint8)
    idxs = np.column_stack(((m_arr >> 4) & 0x0F, m_arr & 0x0F)).ravel().tolist()
    table, rec_nib = list(range(16)), [0] * len(idxs)
    for i, idx in enumerate(idxs):
        sym = table[idx]
        rec_nib[i] = sym
        if idx > 0:
            table.pop(idx)
            table.insert(0, sym)
    rec_arr = np.array(rec_nib, dtype=np.uint8).reshape(-1, 2)
    return ((rec_arr[:, 0] << 4) | rec_arr[:, 1]).tobytes()
def _extract_probe_window(chunk: bytes) -> bytes:
    n = len(chunk)
    if n <= PROBE_HALF_SIZE * 2:
        return chunk
    mid = (n // 2) & ~1023
    return chunk[:PROBE_HALF_SIZE] + chunk[mid : mid + PROBE_HALF_SIZE]


def compress_single_chunk_mdl(chunk: bytes, chunk_s0: int, profile: int = 1) -> tuple[int, bytes]:
    arr = np.frombuffer(chunk, dtype=np.uint8)
    best_g, best_p = 0, chunk
    if is_incompressible_noise(arr):
        return best_g, best_p

    z_lvl = 1 if profile == 0 else 6
    for g_id, cand in ((1, encode_gang1_turbo(chunk, z_lvl)), (6, encode_gang6_wheel8(chunk, chunk_s0, z_lvl))):
        if len(cand) < len(best_p):
            best_g, best_p = g_id, cand

    if profile == 1:
        cands = [(2, encode_gang2_nibble_transpose), (4, encode_gang4_bitplane_pascal)]
        if HAS_TETRA_V21:
            cands.extend(((3, encode_gang3_tetra3d), (5, encode_gang5_mtf_tetra)))
        probe = _extract_probe_window(chunk)
        if len(probe) < len(chunk):
            ref_len = min(len(probe), len(encode_gang1_turbo(probe, z_lvl)), len(encode_gang6_wheel8(probe, chunk_s0, z_lvl)))
            win_g, win_fn = None, None
            for g_id, fn in cands:
                c_len = len(fn(probe))
                if c_len < ref_len:
                    ref_len, win_g, win_fn = c_len, g_id, fn
            if win_fn is not None:
                cand_full = win_fn(chunk)
                if len(cand_full) < len(best_p):
                    best_g, best_p = win_g, cand_full
        else:
            for g_id, fn in cands:
                cand = fn(chunk)
                if len(cand) < len(best_p):
                    best_g, best_p = g_id, cand
    return best_g, best_p


def decode_single_chunk_mdl(
    g_id: int, payload: bytes, c_orig: int, chunk_s0: int, modulus: int = DEFAULT_MODULUS
) -> bytes:
    if g_id == 0:
        rec = payload
    elif g_id == 1:
        rec = decode_gang1_turbo(payload, c_orig)
    elif g_id == 2:
        rec = decode_gang2_nibble_transpose(payload)
    elif g_id == 3:
        rec = decode_gang3_tetra3d(payload, c_orig)
    elif g_id == 4:
        rec = decode_gang4_bitplane_pascal(payload)
    elif g_id == 5:
        rec = decode_gang5_mtf_tetra(payload, c_orig)
    elif g_id == 6:
        rec = decode_gang6_wheel8(payload, c_orig, chunk_s0, modulus)
    else:
        raise ValueError(f"Unknown gear ID: {g_id}")
    if len(rec) != c_orig:
        raise ValueError("ELP8 chunk byte length mismatch.")
    return rec


def _open_regular_file_secure(path: str) -> int:
    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, open_flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Security alert: Input path is not a regular file.")
        return fd
    except Exception:
        os.close(fd)
        raise


def compress_file_stream(
    input_path: str,
    output_path: str,
    profile: int = 1,
    hash_id: int = HASH_SHA512,
    auth_key: bytes | None = None,
    password: str | None = None,
    overwrite: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_cb=None,
) -> dict:
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ValueError("Security alert: Input and output paths MUST differ.")
    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(f"Target already exists (use -f / --force): {output_path}")
    if hash_id not in HASH_SPECS or not (1 <= chunk_size <= MAX_SAFE_CHUNK_SIZE):
        raise ValueError("Invalid hash_id or chunk_size.")

    use_enc = password is not None
    if use_enc and not HAS_PYNACL:
        raise RuntimeError("Encryption requires 'pynacl' (install via: python -m pip install pynacl).")

    algo_name, digest_len = HASH_SPECS[hash_id]
    use_auth = auth_key is not None
    header_total = ELP8_BASE_SIZE + digest_len + (64 if use_auth else 0)
    fd = _open_regular_file_secure(input_path)
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".elp8_enc_", suffix=".tmp", dir=out_dir)

    try:
        orig_size = os.fstat(fd).st_size
        hasher_p1, read_p1 = hashlib.new(algo_name), 0
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            buf = _read_exact(fd, chunk_size)
            if not buf:
                break
            read_p1 += len(buf)
            hasher_p1.update(buf)
        if read_p1 != orig_size:
            raise IOError("Source file size changed during Pass 1.")

        anchor_hash = hasher_p1.digest()
        s0 = compute_s0_sector(anchor_hash, DEFAULT_MODULUS)
        flags = FLAG_COUNTER_ROTATING | FLAG_INWARD_MIRROR_SPLIT | FLAG_MULTI_CHUNK_GEARS
        if use_auth:
            flags |= FLAG_AUTHENTICATED_ORIGIN
        if use_enc:
            flags |= FLAG_ENCRYPTED_PAYLOAD
            header_field_bytes = os.urandom(digest_len)
            enc_key = derive_argon2id_key(password, header_field_bytes[:16])
            enc_state = crypto_secretstream_xchacha20poly1305_state()
            stream_hdr = crypto_secretstream_xchacha20poly1305_init_push(enc_state, enc_key)
            aad_hdr = struct.pack(">4sBBBBQ", ELP8_MAGIC, ELP8_RFC_VERSION, flags, DEFAULT_MODULUS, hash_id, orig_size) + header_field_bytes
        else:
            header_field_bytes = anchor_hash
            enc_state = None
            stream_hdr = b""
            aad_hdr = None

        num_chunks = (orig_size + chunk_size - 1) // chunk_size if orig_size > 0 else 0
        used_modes, hasher_p2, read_p2 = {}, hashlib.new(algo_name), 0
        os.lseek(fd, 0, os.SEEK_SET)

        with os.fdopen(tmp_fd, "wb") as f_out:
            tmp_fd = -1
            f_out.write(b"\x00" * header_total)
            payload_len = 0

            if use_enc:
                f_out.write(stream_hdr)
                payload_len += len(stream_hdr)
                first_plain = anchor_hash + struct.pack(">I", num_chunks)
                first_tag = (
                    crypto_secretstream_xchacha20poly1305_TAG_FINAL
                    if num_chunks == 0
                    else crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
                )
                enc_first = crypto_secretstream_xchacha20poly1305_push(enc_state, first_plain, aad_hdr, first_tag)
                f_out.write(struct.pack(">I", len(enc_first)) + enc_first)
                payload_len += 4 + len(enc_first)
            else:
                f_out.write(struct.pack(">I", num_chunks))
                payload_len += 4

            for idx in range(num_chunks):
                chunk = _read_exact(fd, chunk_size)
                if not chunk:
                    raise IOError("Unexpected EOF during Pass 2.")
                read_p2 += len(chunk)
                hasher_p2.update(chunk)
                best_g, best_p = compress_single_chunk_mdl(chunk, (s0 + idx) % DEFAULT_MODULUS, profile)
                g_name = GANG_NAMES[best_g]
                used_modes[g_name] = used_modes.get(g_name, 0) + 1
                raw_frame = struct.pack(">BII", best_g, len(chunk), len(best_p)) + best_p
                if use_enc:
                    msg_tag = (
                        crypto_secretstream_xchacha20poly1305_TAG_FINAL
                        if idx == num_chunks - 1
                        else crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
                    )
                    enc_frame = crypto_secretstream_xchacha20poly1305_push(enc_state, raw_frame, None, msg_tag)
                    out_frame = struct.pack(">I", len(enc_frame)) + enc_frame
                else:
                    out_frame = raw_frame
                f_out.write(out_frame)
                payload_len += len(out_frame)
                if progress_cb is not None and num_chunks > 1:
                    progress_cb(idx + 1, num_chunks, g_name)

            if os.read(fd, 1) != b"" or read_p2 != orig_size or not hmac.compare_digest(hasher_p2.digest(), anchor_hash):
                raise IOError("TOCTOU ALERT: Source modified during encoding!")

            base_hdr = struct.pack(ELP8_BASE_FMT, ELP8_MAGIC, ELP8_RFC_VERSION, flags, DEFAULT_MODULUS, hash_id, orig_size, payload_len)
            auth_tag = hmac.new(auth_key, base_hdr + header_field_bytes, hashlib.sha512).digest() if use_auth else b""
            f_out.seek(0)
            f_out.write(base_hdr + header_field_bytes + auth_tag)
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

    return {"orig_size": orig_size, "comp_size": header_total + payload_len, "header_size": header_total, "s0": s0, "anchor_hex": anchor_hash.hex(), "modes": used_modes, "encrypted": use_enc}


def decompress_file_stream(
    container_path: str,
    output_path: str | None,
    auth_key: bytes | None = None,
    password: str | None = None,
    overwrite: bool = False,
    verify_only: bool = False,
) -> dict:
    if not verify_only and output_path is not None:
        if os.path.abspath(container_path) == os.path.abspath(output_path):
            raise ValueError("Security alert: Input and output paths MUST differ.")
        if os.path.exists(output_path) and not overwrite:
            raise FileExistsError(f"Target already exists (use -f / --force): {output_path}")

    fd = _open_regular_file_secure(container_path)
    tmp_fd, tmp_path = -1, None
    if not verify_only and output_path is not None:
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        os.makedirs(out_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".elp8_dec_", suffix=".tmp", dir=out_dir)

    try:
        container_size = os.fstat(fd).st_size
        if container_size < ELP8_BASE_SIZE:
            raise ValueError("Truncated ELP8 base header.")
        base_hdr = _read_exact(fd, ELP8_BASE_SIZE)
        magic, version, flags, modulus, hash_id, orig_size, payload_len = struct.unpack(ELP8_BASE_FMT, base_hdr)
        if magic != ELP8_MAGIC or version != ELP8_RFC_VERSION or modulus == 0 or hash_id not in HASH_SPECS or not (flags & FLAG_MULTI_CHUNK_GEARS):
            raise ValueError("Invalid RFC ELP8 header.")

        algo_name, digest_len = HASH_SPECS[hash_id]
        has_auth = bool(flags & FLAG_AUTHENTICATED_ORIGIN)
        is_enc = bool(flags & FLAG_ENCRYPTED_PAYLOAD)
        header_total = ELP8_BASE_SIZE + digest_len + (64 if has_auth else 0)
        if container_size != header_total + payload_len or payload_len < 4:
            raise ValueError("Container length mismatch.")

        header_field_bytes = _read_exact(fd, digest_len)
        if has_auth:
            stored_tag = _read_exact(fd, 64)
            if auth_key is None or not hmac.compare_digest(stored_tag, hmac.new(auth_key, base_hdr + header_field_bytes, hashlib.sha512).digest()):
                raise ValueError("AUTHENTICATION FAILURE: Origin tag missing or invalid!")

        bytes_consumed = 0
        if is_enc:
            if not HAS_PYNACL:
                raise RuntimeError("Encrypted archive requires 'pynacl' (install via: python -m pip install pynacl).")
            if not password:
                raise ValueError("PASSWORD REQUIRED: Archive is encrypted (use -p / --password).")
            if payload_len < crypto_secretstream_xchacha20poly1305_HEADERBYTES + 4:
                raise ValueError("Truncated encrypted payload.")
            stream_hdr = _read_exact(fd, crypto_secretstream_xchacha20poly1305_HEADERBYTES)
            bytes_consumed += len(stream_hdr)
            dec_key = derive_argon2id_key(password, header_field_bytes[:16])
            dec_state = crypto_secretstream_xchacha20poly1305_state()
            crypto_secretstream_xchacha20poly1305_init_pull(dec_state, stream_hdr, dec_key)

            len_buf = _read_exact(fd, 4)
            if len(len_buf) < 4:
                raise ValueError("Truncated encrypted anchor frame length.")
            bytes_consumed += 4
            (enc_first_len,) = struct.unpack(">I", len_buf)
            expected_first_enc = digest_len + 4 + crypto_secretstream_xchacha20poly1305_ABYTES
            if enc_first_len != expected_first_enc:
                raise ValueError("Invalid encrypted anchor frame length.")
            enc_first = _read_exact(fd, enc_first_len)
            if len(enc_first) < enc_first_len:
                raise ValueError("Truncated encrypted anchor frame.")
            bytes_consumed += enc_first_len

            aad_hdr = struct.pack(">4sBBBBQ", magic, version, flags, modulus, hash_id, orig_size) + header_field_bytes
            try:
                first_plain, first_tag = crypto_secretstream_xchacha20poly1305_pull(dec_state, enc_first, aad_hdr)
            except Exception as exc:
                raise ValueError("DECRYPTION FAILURE: Wrong password, tampered header, or corrupted ciphertext!") from exc
            if len(first_plain) != digest_len + 4:
                raise ValueError("Corrupted encrypted anchor frame.")
            anchor_hash = first_plain[:digest_len]
            (num_chunks,) = struct.unpack(">I", first_plain[digest_len : digest_len + 4])
            expected_anchor_tag = (
                crypto_secretstream_xchacha20poly1305_TAG_FINAL
                if num_chunks == 0
                else crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
            )
            if first_tag != expected_anchor_tag:
                raise ValueError("DECRYPTION FAILURE: Unexpected stream tag on anchor frame!")
        else:
            if password:
                raise ValueError("Archive is NOT encrypted, but a password was provided.")
            dec_state = None
            anchor_hash = header_field_bytes
            (num_chunks,) = struct.unpack(">I", _read_exact(fd, 4))
            bytes_consumed += 4

        s0 = compute_s0_sector(anchor_hash, modulus)
        written_bytes, hasher = 0, hashlib.new(algo_name)

        f_out = os.fdopen(tmp_fd, "wb") if tmp_fd != -1 else None
        tmp_fd = -1
        try:
            for idx in range(num_chunks):
                if is_enc:
                    len_buf = _read_exact(fd, 4)
                    if len(len_buf) < 4:
                        raise ValueError("Truncated encrypted frame length.")
                    bytes_consumed += 4
                    (enc_len,) = struct.unpack(">I", len_buf)
                    if enc_len > MAX_SAFE_CHUNK_SIZE + 64:
                        raise MemoryError("Encrypted chunk exceeds 64 MiB safety ceiling.")
                    enc_blob = _read_exact(fd, enc_len)
                    if len(enc_blob) < enc_len:
                        raise ValueError("Truncated encrypted chunk payload.")
                    bytes_consumed += enc_len
                    try:
                        raw_frame, frame_tag = crypto_secretstream_xchacha20poly1305_pull(dec_state, enc_blob, None)
                    except Exception as exc:
                        raise ValueError("DECRYPTION FAILURE: Ciphertext tampered or truncated!") from exc
                    expected_tag = (
                        crypto_secretstream_xchacha20poly1305_TAG_FINAL
                        if idx == num_chunks - 1
                        else crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
                    )
                    if frame_tag != expected_tag:
                        raise ValueError("DECRYPTION FAILURE: Stream truncation or invalid frame tag!")
                    if len(raw_frame) < 9:
                        raise ValueError("Corrupted decrypted frame header.")
                    g_id, c_orig, c_comp = struct.unpack(">BII", raw_frame[:9])
                    if c_orig > MAX_SAFE_CHUNK_SIZE or c_comp > MAX_SAFE_CHUNK_SIZE:
                        raise MemoryError("Decrypted chunk exceeds 64 MiB safety ceiling.")
                    c_data = raw_frame[9:]
                    if len(c_data) != c_comp:
                        raise ValueError("Decrypted chunk payload length mismatch.")
                else:
                    frame_hdr = _read_exact(fd, 9)
                    if len(frame_hdr) < 9:
                        raise ValueError("Truncated chunk header.")
                    bytes_consumed += 9
                    g_id, c_orig, c_comp = struct.unpack(">BII", frame_hdr)
                    if c_orig > MAX_SAFE_CHUNK_SIZE or c_comp > MAX_SAFE_CHUNK_SIZE:
                        raise MemoryError("Chunk exceeds 64 MiB safety ceiling.")
                    c_data = _read_exact(fd, c_comp)
                    if len(c_data) < c_comp:
                        raise ValueError("Truncated chunk payload.")
                    bytes_consumed += c_comp

                rec = decode_single_chunk_mdl(g_id, c_data, c_orig, (s0 + idx) % modulus, modulus)
                if f_out is not None:
                    f_out.write(rec)
                hasher.update(rec)
                written_bytes += len(rec)

            if bytes_consumed != payload_len or written_bytes != orig_size or not hmac.compare_digest(hasher.digest(), anchor_hash):
                raise ValueError("INTEGRITY FAILURE: Byte count or Anchor Hash mismatch!")
            if f_out is not None:
                f_out.flush()
                os.fsync(f_out.fileno())
        finally:
            if f_out is not None:
                f_out.close()

        if tmp_path is not None and output_path is not None:
            os.replace(tmp_path, output_path)
            tmp_path = None
    finally:
        os.close(fd)
        if tmp_fd != -1:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"orig_size": orig_size, "container_size": container_size, "num_chunks": num_chunks, "s0": s0, "hash_algo": algo_name.upper(), "anchor_hex": anchor_hash.hex(), "authenticated": has_auth, "encrypted": is_enc}

