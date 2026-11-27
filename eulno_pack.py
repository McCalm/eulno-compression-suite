# ==============================================================================
# FILE:         eulno_pack.py
# AUTHOR:       Robin Heilos (McCalm)
# LICENSE:      GNU AGPLv3 (or Commercial Enterprise License — see LICENSE & NOTICE.md)
# MODULE:       CLI Packer, Directory Tree Archiver, RFC Integrity Tester, Self-Test
# PROJECT:      EULNO (.elp8) - Deterministic General-Purpose Compression Suite
# VERSION:      v0.9.7-beta (Audit #24 Compliant - Pure EULNO Standard + Opt Zlib)
# DATE:         2026-11-27
# ==============================================================================

import sys
import os
import stat
import time
import struct
import argparse
import tempfile
import shutil
import getpass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eulno_beta_engine import (
    ELP8_MAGIC,
    ELP8_BASE_FMT,
    ELP8_BASE_SIZE,
    HASH_SHA256,
    HASH_SHA512,
    HASH_SPECS,
    DEFAULT_CHUNK_SIZE,
    FLAG_AUTHENTICATED_ORIGIN,
    FLAG_ENCRYPTED_PAYLOAD,
    FLAG_ZLIB_OPT,
    compute_s0_sector,
    _open_regular_file_secure,
    _read_exact,
    compress_file_stream,
    decompress_file_stream,
)

ARCHIVE_MAGIC = b"ELP8DIR\x01"
AUTHOR_INFO = "Robin Heilos (McCalm) | https://huggingface.co | https://eulno.org"
LICENSE_INFO = "GNU AGPLv3 (Commercial Enterprise License available — see LICENSE & NOTICE.md)"


def _resolve_password(pw_arg: str | None, confirm: bool = False) -> str | None:
    if pw_arg is None:
        return None
    if pw_arg != "__PROMPT__":
        if not pw_arg:
            raise ValueError("Password must not be empty.")
        return pw_arg
    pw1 = getpass.getpass("Enter archive password: ")
    if not pw1:
        raise ValueError("Password must not be empty.")
    if confirm:
        pw2 = getpass.getpass("Confirm archive password: ")
        if pw1 != pw2:
            raise ValueError("Passwords do not match.")
    return pw1


def print_live_bar(label: str, current: int, total: int, extra: str = ""):
    total = max(1, total)
    pct = min(100.0, (current * 100.0) / total)
    filled = int(pct // 4)
    bar = "#" * filled + "-" * (25 - filled)
    sys.stdout.write(f"\r  [{bar}] {pct:5.1f}% | {label} {extra}   ")
    sys.stdout.flush()


def pack_directory_to_temp_stream(folder_path: str, tmp_dir: str) -> tuple[str, int, int]:
    """Bundles an entire directory tree to a temporary file on disk (O(1) RAM)."""
    file_entries = []
    for root, _, files in os.walk(folder_path):
        for fname in sorted(files):
            full_p = os.path.join(root, fname)
            st = os.lstat(full_p)
            if not stat.S_ISREG(st.st_mode):
                continue
            rel_p = os.path.relpath(full_p, folder_path).replace("\\", "/")
            file_entries.append((rel_p, full_p))

    fd, bundle_path = tempfile.mkstemp(prefix=".elp8_dir_", suffix=".bundle", dir=tmp_dir)
    total_files = len(file_entries)
    total_bytes = 0

    with os.fdopen(fd, "wb") as out_f:
        out_f.write(ARCHIVE_MAGIC)
        out_f.write(struct.pack("<I", total_files))
        total_bytes += 12
        for idx, (rel_p, full_p) in enumerate(file_entries, 1):
            print_live_bar(f"Collecting files ({idx}/{total_files})", idx, total_files, f"-> {rel_p[:32]}")
            f_size = os.path.getsize(full_p)
            rel_bytes = rel_p.encode("utf-8")
            out_f.write(struct.pack("<HQ", len(rel_bytes), f_size))
            out_f.write(rel_bytes)
            total_bytes += 10 + len(rel_bytes)
            with open(full_p, "rb") as src_f:
                while True:
                    chunk = src_f.read(1048576)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    total_bytes += len(chunk)

    if total_files > 0:
        print()
    return bundle_path, total_files, total_bytes
def unpack_file_stream_to_directory(bundle_path: str, target_dir: str, overwrite: bool = False) -> int:
    """Extracts a bundled directory stream to disk with symlink-escape, path-traversal & trailing-byte guards."""
    target_abs = os.path.abspath(target_dir)
    os.makedirs(target_abs, exist_ok=True)
    real_target = os.path.realpath(target_abs)

    with open(bundle_path, "rb") as in_f:
        magic = in_f.read(8)
        if magic != ARCHIVE_MAGIC:
            raise ValueError("Invalid EULNO directory archive header.")
        hdr_cnt = in_f.read(4)
        if len(hdr_cnt) < 4:
            raise IOError("Truncated directory bundle header.")
        (num_files,) = struct.unpack("<I", hdr_cnt)

        for idx in range(1, num_files + 1):
            entry_hdr = in_f.read(10)
            if len(entry_hdr) < 10:
                raise IOError("Truncated directory entry header.")
            name_len, data_len = struct.unpack("<HQ", entry_hdr)
            raw_name = in_f.read(name_len)
            if len(raw_name) < name_len:
                raise IOError("Truncated directory entry path.")
            rel_p = raw_name.decode("utf-8")
            if not rel_p or rel_p.startswith(("/", "\\")):
                raise ValueError(f"Security alert: Invalid archive entry path ({rel_p!r})")
            out_path = os.path.abspath(os.path.join(target_abs, rel_p))

            if out_path == target_abs or os.path.commonpath([target_abs, out_path]) != target_abs:
                raise ValueError(f"Security alert: Path traversal blocked ({rel_p})")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if os.path.commonpath([real_target, os.path.realpath(out_path)]) != real_target:
                raise ValueError(f"Security alert: Symlink escape blocked ({rel_p})")
            if os.path.exists(out_path) and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing file: {out_path} (use -f / --force)")

            rem = data_len
            with open(out_path, "wb") as out_f:
                while rem > 0:
                    part = in_f.read(min(1048576, rem))
                    if not part:
                        raise IOError("Truncated directory bundle stream.")
                    out_f.write(part)
                    rem -= len(part)

            print_live_bar(f"Writing files ({idx}/{num_files})", idx, num_files, f"-> {rel_p[:32]}")

        if in_f.read(1) != b"":
            raise ValueError("Corrupted directory bundle: trailing bytes detected.")

    if num_files > 0:
        print()
    return num_files


def cmd_compress(
    source_str: str,
    output_str: str | None,
    mode: str,
    hash_id: int,
    auth_key: bytes | None,
    password: str | None,
    force: bool,
    delete_source: bool,
    verbose: bool,
    use_zlib: bool = False,
):
    src_abs = os.path.abspath(source_str)
    if not os.path.exists(src_abs):
        raise FileNotFoundError(f"Source path not found: {src_abs}")
    is_dir = os.path.isdir(src_abs)
    if not is_dir and not os.path.isfile(src_abs):
        raise ValueError(f"Unsupported path type: {src_abs}")

    out_abs = os.path.abspath(output_str) if output_str else (src_abs.rstrip("/\\") + ".elp8")
    if src_abs == out_abs:
        raise ValueError("Input and output paths MUST differ.")
    if os.path.exists(out_abs) and not force:
        raise FileExistsError(f"Target already exists (use -f / --force): {out_abs}")

    profile = 0 if mode == "turbo" else 1
    backend_tag = "OPT-ZLIB" if use_zlib else "PURE-EULNO-rANS"
    t0 = time.perf_counter()

    def _progress(done: int, total: int, gear: str):
        pct = (done / total) * 100.0
        print(f"\r  [COMPRESS] Chunk {done}/{total} ({pct:5.1f}%) -> {gear:<18}", end="", flush=True)

    temp_bundle = None
    try:
        if is_dir:
            print(f"[EULNO] Bundling directory tree ({backend_tag}): {src_abs}")
            out_dir = os.path.dirname(out_abs) or "."
            os.makedirs(out_dir, exist_ok=True)
            temp_bundle, file_count, raw_total = pack_directory_to_temp_stream(src_abs, out_dir)
            print(f"[EULNO] {file_count} files bundled ({raw_total:,} bytes). Compressing...")
            stats = compress_file_stream(temp_bundle, out_abs, profile, hash_id, auth_key, password, force, use_zlib=use_zlib, progress_cb=_progress)
        else:
            print(f"[EULNO] Compressing file ({mode.upper()} | {backend_tag}): {src_abs}")
            stats = compress_file_stream(src_abs, out_abs, profile, hash_id, auth_key, password, force, use_zlib=use_zlib, progress_cb=_progress)
    finally:
        if temp_bundle and os.path.exists(temp_bundle):
            os.remove(temp_bundle)

    dt = time.perf_counter() - t0
    if stats["orig_size"] > DEFAULT_CHUNK_SIZE:
        print()
    ratio = (stats["comp_size"] / stats["orig_size"] * 100.0) if stats["orig_size"] > 0 else 100.0
    saved = 100.0 - ratio
    print(f"[OK] Archive created : {out_abs}")
    print(f"     Original Size   : {stats['orig_size']:,} bytes")
    print(f"     ELP8 Size       : {stats['comp_size']:,} bytes ({ratio:.2f}% | Saved: {saved:+.2f}%)")
    print(f"     Wheel-8 Anchor  : S0 = Sector {stats['s0']} (Backend: {backend_tag} | Encrypted: {stats['encrypted']})")
    if verbose:
        print(f"     Anchor Hash     : {stats['anchor_hex']}")
    if stats["modes"]:
        print(f"     MDL Gears Used  : {stats['modes']}")
    print(f"     Elapsed Time    : {dt:.3f} s")

    if delete_source:
        verify = decompress_file_stream(out_abs, None, auth_key=auth_key, password=password, verify_only=True, allow_zlib=use_zlib)
        if verify["anchor_hex"] != stats["anchor_hex"]:
            raise RuntimeError("Safety abort: Verification failed; source preserved.")
        if is_dir:
            shutil.rmtree(src_abs)
        else:
            os.remove(src_abs)
        print(f"[CLEANUP] Verified source securely removed: {src_abs}")


def cmd_extract(
    archive_str: str,
    output_str: str | None,
    auth_key: bytes | None,
    password: str | None,
    force: bool,
    verbose: bool,
    allow_zlib: bool = False,
):
    arc_abs = os.path.abspath(archive_str)
    if not os.path.isfile(arc_abs):
        raise FileNotFoundError(f"Archive not found: {arc_abs}")

    if output_str:
        target_abs = os.path.abspath(output_str)
    elif arc_abs.lower().endswith(".elp8"):
        target_abs = arc_abs[:-5]
    else:
        target_abs = arc_abs + ".extracted"

    if arc_abs == target_abs:
        raise ValueError("Archive and extraction target MUST differ.")
    if os.path.exists(target_abs) and not force:
        raise FileExistsError(f"Extraction target already exists (use -f / --force): {target_abs}")

    t0 = time.perf_counter()
    out_dir = os.path.dirname(target_abs) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_fd, tmp_raw = tempfile.mkstemp(prefix=".elp8_unpack_", suffix=".raw", dir=out_dir)
    os.close(tmp_fd)

    def _extract_progress(done: int, total: int, gear: str):
        pct = (done / total) * 100.0
        print(f"\r  [EXTRACT]  Chunk {done}/{total} ({pct:5.1f}%) -> {gear:<18}", end="", flush=True)

    try:
        stats = decompress_file_stream(
            arc_abs, tmp_raw, auth_key=auth_key, password=password, overwrite=True, verify_only=False, allow_zlib=allow_zlib, progress_cb=_extract_progress
        )
        if stats["num_chunks"] > 1:
            print()
        with open(tmp_raw, "rb") as check_f:
            is_dir_bundle = check_f.read(8) == ARCHIVE_MAGIC

        if is_dir_bundle:
            if os.path.exists(target_abs) and os.path.isfile(target_abs):
                if not force:
                    raise FileExistsError(f"Target exists as file (use -f / --force): {target_abs}")
                os.remove(target_abs)
            os.makedirs(target_abs, exist_ok=True)
            extracted_files = unpack_file_stream_to_directory(tmp_raw, target_abs, overwrite=force)
            os.remove(tmp_raw)
            tmp_raw = None
            dt = time.perf_counter() - t0
            print(f"[OK] Directory restored : {target_abs} ({extracted_files} files)")
        else:
            os.replace(tmp_raw, target_abs)
            tmp_raw = None
            dt = time.perf_counter() - t0
            print(f"[OK] File restored      : {target_abs} ({stats['orig_size']:,} bytes)")

        print(f"     Integrity Check    : {stats['hash_algo']} PASS (100% Bit-Exact | Encrypted: {stats['encrypted']})")
        if verbose:
            print(f"     Anchor Hash        : {stats['anchor_hex']}")
        print(f"     Elapsed Time       : {dt:.3f} s")
    finally:
        if tmp_raw and os.path.exists(tmp_raw):
            os.remove(tmp_raw)


def cmd_test_and_info(
    archive_str: str, auth_key: bytes | None, password: str | None, verify_full: bool, allow_zlib: bool = False
):
    arc_abs = os.path.abspath(archive_str)
    if not os.path.isfile(arc_abs):
        raise FileNotFoundError(f"Archive not found: {arc_abs}")

    with open(arc_abs, "rb") as f:
        base = f.read(ELP8_BASE_SIZE)
        if len(base) < ELP8_BASE_SIZE:
            raise ValueError("File too small to be an ELP8 container.")
        magic, ver, flags, mod, hash_id, orig_size, payload_len = struct.unpack(ELP8_BASE_FMT, base)
        if magic != ELP8_MAGIC or hash_id not in HASH_SPECS:
            raise ValueError("Not a valid RFC ELP8 container.")
        algo_name, d_len = HASH_SPECS[hash_id]
        anchor_field = f.read(d_len)

    has_auth = bool(flags & FLAG_AUTHENTICATED_ORIGIN)
    is_enc = bool(flags & FLAG_ENCRYPTED_PAYLOAD)
    has_zlib_opt = bool(flags & FLAG_ZLIB_OPT)
    total_size = os.path.getsize(arc_abs)
    ratio = (total_size / orig_size * 100.0) if orig_size > 0 else 100.0
    print(f"[ELP8 RFC HEADER] {arc_abs}")
    print(f"  Format / Version : ELP8 v0x{ver:02x} (Flags: 0x{flags:02x} | Modulus: {mod})")
    print(f"  Entropy Backend  : {'OPT-ZLIB (requires --use-zlib)' if has_zlib_opt else 'PURE-EULNO Native rANS-256'}")
    print(f"  Hash Algorithm   : {algo_name.upper()} ({d_len * 8}-bit)")
    print(f"  Encrypted (Bit 4): {'YES (Argon2id + XChaCha20-Poly1305)' if is_enc else 'NO'}")
    print(f"  Origin Auth Tag  : {'ENABLED (64-byte HMAC-SHA512)' if has_auth else 'DISABLED'}")
    if is_enc:
        print(f"  Argon2id Salt    : {anchor_field[:16].hex()} (Anchor Hash encrypted inside stream)")
    else:
        print(f"  Wheel-8 Sector   : S0 = {compute_s0_sector(anchor_field, mod)}")
        print(f"  Anchor Hash      : {anchor_field.hex()}")
    print(f"  Original Size    : {orig_size:,} bytes")
    print(f"  Container Size   : {total_size:,} bytes (Ratio: {ratio:.2f}%)")

    if verify_full:
        t0 = time.perf_counter()
        stats = decompress_file_stream(
            arc_abs, None, auth_key=auth_key, password=password, verify_only=True, allow_zlib=allow_zlib
        )
        dt = time.perf_counter() - t0
        print(f"[PASS] Full Stream Verification OK ({stats['num_chunks']} chunks, S0={stats['s0']}, 100% Bit-Exact in {dt:.3f} s)")
def cmd_self_test(pause_sec: int = 1):
    print("=" * 72)
    print("EULNO (.elp8) v0.9.7-beta - AUTOMATED END-TO-END RFC SELF-TEST")
    print("=" * 72)
    with tempfile.TemporaryDirectory(prefix="eulno_selftest_") as tmp:
        src_file = os.path.join(tmp, "sensor_matrix.bin")
        arc_max = os.path.join(tmp, "sensor_max.elp8")
        arc_auth = os.path.join(tmp, "sensor_auth.elp8")
        rec_file = os.path.join(tmp, "sensor_restored.bin")
        key_file = os.path.join(tmp, "secret.key")

        wave = bytes(((i * 3) % 256) for i in range(16384))
        text_blk = b"Euler and Noether unified algebra and physics! <page><title>EULNO</title></page>\n" * 200
        raw_sample = wave + text_blk
        with open(src_file, "wb") as f:
            f.write(raw_sample)
        with open(key_file, "wb") as kf:
            kf.write(b"EULNO-SECRET-ORIGIN-KEY-2026")

        print("\n[STEP 1/5] Compressing mixed wave + text (PURE-EULNO MAX G0..G7, SHA-512)...")
        cmd_compress(src_file, arc_max, "max", HASH_SHA512, None, None, True, False, False, use_zlib=False)
        cmd_test_and_info(arc_max, None, None, verify_full=True, allow_zlib=False)
        time.sleep(pause_sec)

        print("\n[STEP 2/5] Compressing with Origin Authentication Tag (--key-file, SHA-256)...")
        with open(key_file, "rb") as kf:
            secret = kf.read()
        cmd_compress(src_file, arc_auth, "turbo", HASH_SHA256, secret, None, True, False, False, use_zlib=False)
        cmd_extract(arc_auth, rec_file, secret, None, True, False, allow_zlib=False)
        with open(rec_file, "rb") as rf:
            assert rf.read() == raw_sample, "Self-test bit comparison failed!"
        time.sleep(pause_sec)

        print("\n[STEP 3/5] Testing Optional --use-zlib Guard (Audit #24 5.1)...")
        arc_z = os.path.join(tmp, "sensor_zlib.elp8")
        cmd_compress(src_file, arc_z, "turbo", HASH_SHA512, None, None, True, False, False, use_zlib=True)
        try:
            cmd_test_and_info(arc_z, None, None, verify_full=True, allow_zlib=False)
            raise AssertionError("Guard failure: Zlib archive was accepted without --use-zlib!")
        except ValueError:
            print("     Pure-EULNO Zlib-Rejection Guard : PASS")
        cmd_test_and_info(arc_z, None, None, verify_full=True, allow_zlib=True)
        time.sleep(pause_sec)

        print("\n[STEP 4/5] Testing Directory Tree Bundling & Extraction...")
        sub_dir = os.path.join(tmp, "project_tree")
        os.makedirs(os.path.join(sub_dir, "nested"), exist_ok=True)
        with open(os.path.join(sub_dir, "a.txt"), "wb") as f1:
            f1.write(b"Hello Euler & Noether!\n" * 500)
        with open(os.path.join(sub_dir, "nested", "b.py"), "wb") as f2:
            f2.write(b"def wheel8(): return 8\n" * 1500)
        dir_arc = os.path.join(tmp, "tree.elp8")
        dir_out = os.path.join(tmp, "tree_restored")
        cmd_compress(sub_dir, dir_arc, "max", HASH_SHA512, None, None, True, False, False, use_zlib=False)
        cmd_extract(dir_arc, dir_out, None, None, True, False, allow_zlib=False)
        time.sleep(pause_sec)

        print("\n[STEP 5/5] Testing Argon2id + XChaCha20-Poly1305 Encryption (-p)...")
        arc_enc = os.path.join(tmp, "sensor_vault.elp8")
        rec_enc = os.path.join(tmp, "sensor_vault_out.bin")
        cmd_compress(src_file, arc_enc, "max", HASH_SHA512, secret, "EulerNoether2026!", True, False, False, use_zlib=False)
        cmd_extract(arc_enc, rec_enc, secret, "EulerNoether2026!", True, False, allow_zlib=False)
        with open(rec_enc, "rb") as ef:
            assert ef.read() == raw_sample, "Encrypted self-test bit comparison failed!"

    print("\n" + "=" * 72)
    print("[SUCCESS] ALL 5 SELF-TEST STAGES PASSED 100% BIT-EXACT!")
    print("=" * 72)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eulno_pack.py",
        description="EULNO (.elp8) v0.9.7-beta - Pure Native rANS-256 Compressor & Extractor (G0..G7)",
    )
    action_grp = parser.add_mutually_exclusive_group(required=True)
    action_grp.add_argument("-c", "--compress", metavar="SOURCE", help="Compress a file or directory into an .elp8 archive")
    action_grp.add_argument("-x", "--extract", metavar="ARCHIVE", help="Extract an .elp8 archive bit-exact")
    action_grp.add_argument("-t", "--test", metavar="ARCHIVE", help="Verify full stream integrity without writing to disk")
    action_grp.add_argument("-i", "--info", metavar="ARCHIVE", help="Display RFC container header, S0 anchor, and ratio")
    action_grp.add_argument("--version", action="store_true", help="Display version, author, and license information")
    action_grp.add_argument("--self-test", action="store_true", help="Run end-to-end RFC & CLI self-test")

    parser.add_argument("-o", "--output", metavar="TARGET", default=None, help="Custom output path")
    parser.add_argument("-m", "--mode", choices=("max", "turbo"), default="max", help="'max' = G0..G7 (default), 'turbo' = G0,G1,G6")
    parser.add_argument("--use-zlib", action="store_true", help="Optional external zlib backend (G10..G13) for speed/legacy archives")
    parser.add_argument("--hash", choices=("sha256", "sha512"), default="sha512", help="RFC Anchor Hash algorithm (default: sha512)")
    parser.add_argument("--key-file", metavar="KEYFILE", default=None, help="Secret key file for Bit-3 origin authentication")
    parser.add_argument("-p", "--password", nargs="?", const="__PROMPT__", default=None, help="Encrypt/decrypt archive with Argon2id + XChaCha20-Poly1305")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing output files without aborting")
    parser.add_argument("--delete-source", action="store_true", help="Delete source file after verified compression")
    parser.add_argument("-v", "--verbose", action="store_true", help="Display full anchor hash hex")
    return parser


def main():
    for arg in sys.argv[1:]:
        if arg == "--":
            break
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            print(f"[ERROR] Invalid short flag '{arg}'. Did you mean '--{arg[1:]}' (with two dashes)?")
            sys.exit(2)

    parser = build_cli_parser()
    args = parser.parse_args()

    try:
        key_bytes = None
        if args.key_file:
            k_fd = _open_regular_file_secure(args.key_file)
            try:
                key_bytes = _read_exact(k_fd, 65536)
            finally:
                os.close(k_fd)
            if not key_bytes:
                raise ValueError("Key file must not be empty.")

        hash_id = HASH_SHA256 if args.hash == "sha256" else HASH_SHA512
        password = _resolve_password(args.password, confirm=bool(args.compress))
        if args.version:
            print(f"EULNO CLI PACKER v0.9.7-beta (Pure Native rANS-256 Standard)\nAuthor : {AUTHOR_INFO}\nLicense: {LICENSE_INFO}")
        elif args.self_test:
            cmd_self_test(pause_sec=1)
        elif args.compress:
            cmd_compress(args.compress, args.output, args.mode, hash_id, key_bytes, password, args.force, args.delete_source, args.verbose, use_zlib=args.use_zlib)
        elif args.extract:
            cmd_extract(args.extract, args.output, key_bytes, password, args.force, args.verbose, allow_zlib=args.use_zlib)
        elif args.test:
            cmd_test_and_info(args.test, key_bytes, password, verify_full=True, allow_zlib=args.use_zlib)
        elif args.info:
            cmd_test_and_info(args.info, key_bytes, password, verify_full=False, allow_zlib=args.use_zlib)
    except Exception as exc:
        print(f"\n[CRITICAL ERROR] {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
