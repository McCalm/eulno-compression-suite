# ==============================================================================
# FILE:         eulno_pack.py
# AUTHOR:       Robin Heilos (McCalm)
# PROFILE:      https://huggingface.co | https://eulno.org
# SUPPORT:      Community Tab at https://huggingface.co (No email support)
# COMMERCIAL:   mccalm@outlook.de (Enterprise licensing >= $10M annual revenue)
# LICENSE:      EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)
# MODULE:       CLI Packer, Directory Tree Archiver, RFC Integrity Tester, Self-Test
# PROJECT:      EULNO (.elp8) - 3D-Tetrahedron & Wheel-8 Compression Suite
# VERSION:      v0.9.5-beta (RFC draft-heilos-eulno-elp8-00 Compliant)
# DATE:         2026-11-25
# ==============================================================================

import sys
import os
import time
import struct
import argparse
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eulno_beta_engine import (
    ELP8_MAGIC,
    ELP8_BASE_FMT,
    ELP8_BASE_SIZE,
    HASH_SHA256,
    HASH_SHA512,
    HASH_SPECS,
    FLAG_AUTHENTICATED_ORIGIN,
    sha512_bytes,
    compute_s0_sector,
    _open_regular_file_secure,
    _read_exact,
    compress_file_stream,
    decompress_file_stream,
)

ARCHIVE_MAGIC = b"ELP8DIR\x01"
AUTHOR_INFO = "Robin Heilos (McCalm) | https://huggingface.co | https://eulno.org"
SUPPORT_INFO = "Community Tab at https://huggingface.co (No email support)"
COMMERCIAL_INFO = "mccalm@outlook.de (Enterprise licensing >= $10M annual revenue)"
LICENSE_INFO = "EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)"


class ActivityWatchdog:
    """Terminates the process safely if a single step stalls without progress."""
    def __init__(self, timeout_sec: float = 45.0):
        self.timeout_sec = timeout_sec
        self._last_beat = time.monotonic()
        self._stage = "Initialization"
        self._active = False
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        if timeout_sec > 0:
            self._thread.start()

    def beat(self, stage: str | None = None):
        self._last_beat = time.monotonic()
        if stage:
            self._stage = stage

    def start(self, stage: str):
        self.beat(stage)
        self._active = True

    def stop(self):
        self._active = False

    def _monitor(self):
        while not self._stop_evt.wait(0.5):
            if self._active and (time.monotonic() - self._last_beat) > self.timeout_sec:
                sys.stderr.write(
                    f"\n\n[WATCHDOG TIMEOUT] Engine stalled for >{self.timeout_sec:.0f}s "
                    f"during '{self._stage}'. Aborting safely (exit code 3).\n"
                )
                sys.stderr.flush()
                os._exit(3)


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
            if os.path.islink(full_p):
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
    """Extracts a bundled directory stream to disk with symlink-escape, path-traversal & overwrite guards."""
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
            out_path = os.path.abspath(os.path.join(target_abs, rel_p))

            if os.path.commonpath([target_abs, out_path]) != target_abs:
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

    if num_files > 0:
        print()
    return num_files


def cmd_compress(
    input_path: str,
    output_path: str | None,
    mode: str,
    hash_id: int,
    auth_key: bytes | None,
    force: bool,
    delete_source: bool,
    verbose: bool,
):
    if not os.path.exists(input_path):
        print(f"[ERROR] Source not found: {input_path}")
        sys.exit(1)

    is_dir = os.path.isdir(input_path)
    if output_path is None:
        clean_in = input_path.rstrip("/\\")
        output_path = f"{clean_in}.elp8"

    if os.path.abspath(input_path) == os.path.abspath(output_path):
        print("[ABORT] Security alert: Input and output paths MUST differ.")
        sys.exit(1)
    if os.path.exists(output_path) and not force:
        print(f"[ABORT] Target already exists: {output_path} (use -f / --force to overwrite)")
        sys.exit(1)

    algo_label = "SHA-256" if hash_id == HASH_SHA256 else "SHA-512"
    print("=" * 84)
    print(f"EULNO COMPRESSOR v0.9.5-beta | Mode: {mode.upper()} | Hash: {algo_label} | Target: {os.path.basename(output_path)}")
    print("=" * 84)

    watchdog = ActivityWatchdog(timeout_sec=45.0)
    watchdog.start("Initializing stream")
    tmp_bundle = None
    t_start = time.perf_counter()

    try:
        if is_dir:
            print(f"  [1/3] Bundling directory tree on disk: {input_path}")
            tmp_bundle, file_count, orig_size = pack_directory_to_temp_stream(input_path, tempfile.gettempdir())
            stream_source = tmp_bundle
            print(f"        -> {file_count} files bundled ({orig_size:,} bytes)")
        else:
            stream_source = input_path
            orig_size = os.path.getsize(input_path)
            print(f"  [1/3] Streaming source file (O(1) RAM): {os.path.basename(input_path)} ({orig_size:,} B)")

        profile_id = 0 if mode == "turbo" else 1
        gear_desc = "TURBO (G0, G1, G6)" if profile_id == 0 else "MDL AUTO-ROUTER (G0..G6)"
        print(f"  [2/3] Encoding RFC container ({gear_desc}) + TOCTOU Guard...")
        watchdog.beat("Streaming encode")

        def _cli_progress(curr: int, total: int, gear_name: str):
            watchdog.beat(f"Chunk {curr}/{total} ({gear_name})")
            pct = (curr / total) * 100.0
            sys.stdout.write(f"\r        -> Chunk {curr}/{total} ({pct:5.1f}%) | Winner: {gear_name:<20}")
            sys.stdout.flush()
            if curr == total:
                sys.stdout.write("\n")
                sys.stdout.flush()

        t_comp0 = time.perf_counter()
        res = compress_file_stream(
            stream_source,
            output_path,
            profile=profile_id,
            hash_id=hash_id,
            auth_key=auth_key,
            overwrite=force,
            progress_cb=_cli_progress,
        )
        comp_ms = max(0.01, (time.perf_counter() - t_comp0) * 1000.0)
    finally:
        watchdog.stop()
        if tmp_bundle and os.path.exists(tmp_bundle):
            os.remove(tmp_bundle)

    comp_size = res["comp_size"]
    ratio_pct = (comp_size * 100.0 / orig_size) if orig_size > 0 else 100.0
    saved_pct = 100.0 - ratio_pct
    speed_mbs = (orig_size / 1_048_576.0) / (comp_ms / 1000.0)
    total_ms = (time.perf_counter() - t_start) * 1000.0

    print(f"  [3/3] Container verified & committed atomically: {output_path}")
    print("-" * 84)
    print(f"  RESULT        : {orig_size:,} B  --->  {comp_size:,} B (incl. {res['header_size']} B RFC header)")
    print(f"  RATIO         : {ratio_pct:.2f} % of original (Space saved: {saved_pct:+.2f} %)")
    print(f"  WHEEL ANCHOR  : Start Spoke S0 = {res['s0']} (mod 8) | Auth Tag: {'YES (Bit 3)' if auth_key else 'NO'}")
    print(f"  SELECTED GEARS: {res['modes']}")
    print(f"  THROUGHPUT    : {speed_mbs:.2f} MB/s (Core: {comp_ms:.1f} ms | Total: {total_ms:.1f} ms)")
    if verbose:
        print(f"  FULL ANCHOR   : {res['anchor_hex']}")
    print("=" * 84)

    if delete_source:
        if is_dir:
            print("  [INFO] --delete-source is ignored for directories for safety.")
        else:
            os.remove(input_path)
            print(f"  [INFO] Source file removed (--delete-source): {input_path}")


def cmd_extract(archive_path: str, output_path: str | None, auth_key: bytes | None, force: bool, verbose: bool):
    if not os.path.isfile(archive_path):
        print(f"[ERROR] Archive not found: {archive_path}")
        sys.exit(1)

    if output_path is None:
        output_path = archive_path[:-5] if archive_path.lower().endswith(".elp8") else f"{archive_path}.unpacked"

    if os.path.abspath(archive_path) == os.path.abspath(output_path):
        print("[ABORT] Security alert: Input and output paths MUST differ.")
        sys.exit(1)
    if os.path.exists(output_path) and not force:
        print(f"[ABORT] Target already exists: {output_path} (use -f / --force to overwrite)")
        sys.exit(1)

    print("=" * 84)
    print(f"EULNO EXTRACTOR v0.9.5-beta | Source: {os.path.basename(archive_path)}")
    print("=" * 84)
    t0 = time.perf_counter()

    watchdog = ActivityWatchdog(timeout_sec=45.0)
    watchdog.start("Streaming decode & RFC verification")
    tmp_raw = None
    try:
        parent_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        os.makedirs(parent_dir, exist_ok=True)
        fd_tmp, tmp_raw = tempfile.mkstemp(prefix=".elp8_ext_", suffix=".raw", dir=parent_dir)
        os.close(fd_tmp)

        print("  [1/2] Decoding chunks in O(1) RAM & verifying Anchor Hash in constant time...")
        t_dec0 = time.perf_counter()
        res = decompress_file_stream(archive_path, tmp_raw, auth_key=auth_key, overwrite=True)
        dec_ms = max(0.01, (time.perf_counter() - t_dec0) * 1000.0)

        with open(tmp_raw, "rb") as check_f:
            is_dir_archive = check_f.read(8) == ARCHIVE_MAGIC

        print(f"  [2/2] Promoting verified data to: {output_path}")
        if is_dir_archive:
            n_files = unpack_file_stream_to_directory(tmp_raw, output_path, overwrite=force)
            print(f"        -> Directory with {n_files} files completely restored.")
        else:
            os.chmod(tmp_raw, 0o644)
            os.replace(tmp_raw, output_path)
            tmp_raw = None
    finally:
        watchdog.stop()
        if tmp_raw and os.path.exists(tmp_raw):
            os.remove(tmp_raw)

    orig_size = res["orig_size"]
    speed_mbs = (orig_size / 1_048_576.0) / (dec_ms / 1000.0)
    total_ms = (time.perf_counter() - t0) * 1000.0

    print("-" * 84)
    print(f"  INTEGRITY     : {res['hash_algo']} PASS (100% Bit-Exact | S0={res['s0']} | Auth={res['authenticated']})")
    print(f"  RESTORED      : {orig_size:,} bytes in {dec_ms:.1f} ms ({speed_mbs:.2f} MB/s | Total: {total_ms:.1f} ms)")
    if verbose:
        print(f"  FULL ANCHOR   : {res['anchor_hex']}")
    print("=" * 84)

def cmd_test_and_info(archive_path: str, auth_key: bytes | None, verify_full: bool):
    if not os.path.isfile(archive_path):
        print(f"[ERROR] File not found: {archive_path}")
        sys.exit(1)

    fd = _open_regular_file_secure(archive_path)
    try:
        c_size = os.fstat(fd).st_size
        if c_size < ELP8_BASE_SIZE:
            raise ValueError("Not a valid ELP8 archive.")
        base_hdr = _read_exact(fd, ELP8_BASE_SIZE)
        magic, ver, flags, mod, hash_id, orig_len, payload_len = struct.unpack(ELP8_BASE_FMT, base_hdr)
        if magic != ELP8_MAGIC or hash_id not in HASH_SPECS:
            raise ValueError("Invalid RFC ELP8 header.")
        algo_name, digest_len = HASH_SPECS[hash_id]
        anchor_hex = _read_exact(fd, digest_len).hex()
        s0 = compute_s0_sector(bytes.fromhex(anchor_hex), mod)
        has_auth = bool(flags & FLAG_AUTHENTICATED_ORIGIN)
    finally:
        os.close(fd)

    ratio = (c_size * 100.0 / orig_len) if orig_len > 0 else 100.0
    print("=" * 84)

    print("EULNO CONTAINER INSPECTION & RFC INTEGRITY TEST (.elp8 | v0.9.5-beta)")
    print("=" * 84)
    print(f"  Author        : {AUTHOR_INFO}")
    print(f"  Commercial    : {COMMERCIAL_INFO}")
    print(f"  License       : {LICENSE_INFO}")
    print("-" * 84)
    print(f"  File          : {archive_path}")
    print(f"  RFC Protocol  : Version 0x{ver:02x} | Flags: 0x{flags:02x} | Modulus: M={mod} | Start Spoke S0={s0}")
    print(f"  Container Size: {c_size:,} bytes | Original Size: {orig_len:,} bytes (Ratio: {ratio:.2f} %)")
    print(f"  Origin Auth   : {'ACTIVE (64-Byte Tag)' if has_auth else 'NONE'}")
    print(f"  {algo_name.upper()} Anchor : {anchor_hex}")

    if verify_full:
        t0 = time.perf_counter()
        decompress_file_stream(archive_path, None, auth_key=auth_key, verify_only=True)
        ms = (time.perf_counter() - t0) * 1000.0
        print(f"  INTEGRITY     : PASS (Full O(1)-RAM stream verification in {ms:.1f} ms OK!)")
    print("=" * 84)


def _pause_step(seconds: int = 2):
    for rem in range(seconds, 0, -1):
        sys.stdout.write(f"\r  [PAUSE] Next step in {rem}s (press Ctrl+C to abort)... ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r" + " " * 64 + "\r")
    sys.stdout.flush()


def cmd_self_test(pause_sec: int = 2):
    print("=" * 84)
    print("EULNO CLI PACKER v0.9.5-beta — END-TO-END RFC SELF-TEST")
    print("=" * 84)
    tmp_dir = tempfile.mkdtemp(prefix="eulno_selftest_")
    test_key = b"EULNO-RFC-SELFTEST-ORIGIN-KEY-2026"

    try:
        sample_file = os.path.join(tmp_dir, "sample_payload.py")
        engine_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eulno_beta_engine.py")
        if os.path.isfile(engine_path):
            shutil.copy2(engine_path, sample_file)
        else:
            with open(sample_file, "wb") as f:
                f.write(b'{"sensor":"wheel8","status":"OK","val":42}\n' * 1500)

        arc_max = os.path.join(tmp_dir, "test_max.elp8")
        arc_auth = os.path.join(tmp_dir, "test_auth.elp8")
        restored_file = os.path.join(tmp_dir, "restored_payload.py")

        print("\n>>> STEP 1/5: Compressing File in MDL_AUTO_MAX Mode (G0..G6, SHA-512)")
        cmd_compress(sample_file, arc_max, mode="max", hash_id=HASH_SHA512, auth_key=None, force=True, delete_source=False, verbose=True)
        _pause_step(pause_sec)

        print("\n>>> STEP 2/5: Compressing with Authenticated Origin Tag (Bit 3, SHA-256)")
        cmd_compress(sample_file, arc_auth, mode="turbo", hash_id=HASH_SHA256, auth_key=test_key, force=True, delete_source=False, verbose=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 3/5: Inspecting RFC Header & Start Spoke S0 (-i)")
        cmd_test_and_info(arc_auth, auth_key=test_key, verify_full=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 4/5: Full Stream Verification (-t) & Extraction (-x)")
        cmd_test_and_info(arc_auth, auth_key=test_key, verify_full=True)
        cmd_extract(arc_auth, restored_file, auth_key=test_key, force=True, verbose=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 5/5: Directory Tree Bundling & Final Bit-Exact Verification")
        sub_dir = os.path.join(tmp_dir, "mini_folder")
        os.makedirs(os.path.join(sub_dir, "nested"), exist_ok=True)
        shutil.copy2(sample_file, os.path.join(sub_dir, "a.py"))
        shutil.copy2(sample_file, os.path.join(sub_dir, "nested", "b.py"))
        dir_arc = os.path.join(tmp_dir, "folder.elp8")
        dir_out = os.path.join(tmp_dir, "folder_restored")
        cmd_compress(sub_dir, dir_arc, mode="max", hash_id=HASH_SHA512, auth_key=None, force=True, delete_source=False, verbose=False)
        cmd_extract(dir_arc, dir_out, auth_key=None, force=True, verbose=False)

        with open(sample_file, "rb") as f1, open(restored_file, "rb") as f2:
            if sha512_bytes(f1.read()) != sha512_bytes(f2.read()):
                raise RuntimeError("Self-test SHA-512 mismatch!")

        print("\n" + "=" * 84)
        print("  SELF-TEST RESULT: ALL 5 STEPS PASSED (100% RFC & BIT-EXACT VERIFIED!)")
        print("=" * 84)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eulno_pack.py",
        description="EULNO (.elp8) v0.9.5-beta - RFC-Compliant Compressor & Extractor (G0..G6 + Wheel-8)",
    )
    action_grp = parser.add_mutually_exclusive_group(required=True)
    action_grp.add_argument("-c", "--compress", metavar="SOURCE", help="Compress a file or directory into an .elp8 archive")
    action_grp.add_argument("-x", "--extract", metavar="ARCHIVE", help="Extract an .elp8 archive bit-exact")
    action_grp.add_argument("-t", "--test", metavar="ARCHIVE", help="Verify full stream integrity without writing to disk")
    action_grp.add_argument("-i", "--info", metavar="ARCHIVE", help="Display RFC container header, S0 anchor, and ratio")
    action_grp.add_argument("--version", action="store_true", help="Display version, author, and license information")
    action_grp.add_argument("--self-test", action="store_true", help="Run end-to-end RFC & CLI self-test")

    parser.add_argument("-o", "--output", metavar="TARGET", default=None, help="Custom output path")
    parser.add_argument("-m", "--mode", choices=("max", "turbo"), default="max", help="'max' = G0..G6 (default), 'turbo' = G0,G1,G6")
    parser.add_argument("--hash", choices=("sha256", "sha512"), default="sha512", help="RFC Anchor Hash algorithm (default: sha512)")
    parser.add_argument("--key-file", metavar="KEYFILE", default=None, help="Secret key file for Bit-3 origin authentication")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing output files without aborting")
    parser.add_argument("--delete-source", action="store_true", help="Delete source file after verified compression")
    parser.add_argument("-v", "--verbose", action="store_true", help="Display full anchor hash hex")
    return parser


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    key_bytes = None
    if args.key_file:
        with open(args.key_file, "rb") as kf:
            key_bytes = kf.read()
        if not key_bytes:
            print("[ERROR] Key file must not be empty.")
            sys.exit(1)

    hash_id = HASH_SHA256 if args.hash == "sha256" else HASH_SHA512

    try:
        if args.version:
            print(f"EULNO CLI PACKER v0.9.5-beta (RFC draft-heilos-eulno-elp8-00)\nAuthor : {AUTHOR_INFO}\nLicense: {LICENSE_INFO}")
        elif args.self_test:
            cmd_self_test(pause_sec=2)
        elif args.compress:
            cmd_compress(args.compress, args.output, args.mode, hash_id, key_bytes, args.force, args.delete_source, args.verbose)
        elif args.extract:
            cmd_extract(args.extract, args.output, key_bytes, args.force, args.verbose)
        elif args.test:
            cmd_test_and_info(args.test, key_bytes, verify_full=True)
        elif args.info:
            cmd_test_and_info(args.info, key_bytes, verify_full=False)
    except Exception as exc:
        print(f"\n[CRITICAL ERROR] {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
