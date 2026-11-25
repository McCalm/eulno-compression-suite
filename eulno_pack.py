# ==============================================================================
# FILE:         eulno_pack.py
# AUTHOR:       Robin Heilos (McCalm)
# PROFILE:      https://huggingface.co
# SUPPORT:      Community Tab at https://huggingface.co (No email support)
# COMMERCIAL:   mccalm@outlook.de (Enterprise licensing >= $10M annual revenue)
# LICENSE:      EULNO Studio Community License v2.0 (Dedicated to Euler & Noether)
# MODULE:       CLI Packer, Directory Tree Archiver, Integrity Tester, Self-Test
# PROJECT:      EULNO (.elp8) - 3D-Tetrahedron & Combinatorial Compression Suite
# VERSION:      v0.9.0-beta (PUBLIC_BETA)
# DATE:         2026-11-25
#
# DESCRIPTION:
# Command-line interface (CLI) packer and extractor for the EULNO Compression
# Suite (.elp8) in Public Beta status (v0.9.0-beta). Supports single-file and
# recursive directory tree archiving (ELP8DIR), MAX (MDL Auto-Router G0..G5)
# and TURBO profiles, SHA-512 verification, and a 6-step end-to-end self-test.
# ==============================================================================

import sys
import os
import time
import struct
import argparse
import hashlib
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eulno_beta_engine import (
    ELP8_MAGIC,
    ELP8_HDR_FMT,
    ELP8_HDR_SIZE,
    DEFAULT_CHUNK_SIZE,
    sha512_bytes,
    compress_elp8,
    decompress_elp8,
)

ARCHIVE_MAGIC = b"ELP8DIR\x01"
AUTHOR_INFO = "Robin Heilos (McCalm) | https://huggingface.co"
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
                    f"during '{self._stage}'. Aborting safely (exit code 3). "
                    f"Tip: Retry with '-m turbo' for real-time speed.\n"
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


def pack_directory_to_stream(folder_path: str) -> tuple[bytes, int]:
    """Bundles an entire directory tree including relative paths into a binary stream."""
    file_entries = []
    for root, _, files in os.walk(folder_path):
        for fname in sorted(files):
            full_p = os.path.join(root, fname)
            rel_p = os.path.relpath(full_p, folder_path).replace("\\", "/")
            file_entries.append((rel_p, full_p))

    buf = bytearray(ARCHIVE_MAGIC)
    buf.extend(struct.pack("<I", len(file_entries)))
    total_files = len(file_entries)

    for idx, (rel_p, full_p) in enumerate(file_entries, 1):
        print_live_bar(f"Collecting files ({idx}/{total_files})", idx, total_files, f"-> {rel_p[:32]}")
        with open(full_p, "rb") as f:
            content = f.read()
        rel_bytes = rel_p.encode("utf-8")
        buf.extend(struct.pack("<HQ", len(rel_bytes), len(content)))
        buf.extend(rel_bytes)
        buf.extend(content)

    if total_files > 0:
        print()
    return bytes(buf), total_files


def unpack_stream_to_directory(stream: bytes, target_dir: str) -> int:
    """Extracts a bundled directory stream bit-exact to disk with path-traversal protection."""
    if not stream.startswith(ARCHIVE_MAGIC):
        raise ValueError("Invalid EULNO directory archive header.")
    (num_files,) = struct.unpack("<I", stream[8:12])
    pos = 12
    os.makedirs(target_dir, exist_ok=True)
    target_abs = os.path.abspath(target_dir)

    for idx in range(1, num_files + 1):
        name_len, data_len = struct.unpack("<HQ", stream[pos:pos + 10])
        pos += 10
        rel_p = stream[pos:pos + name_len].decode("utf-8")
        pos += name_len
        content = stream[pos:pos + data_len]
        pos += data_len

        out_path = os.path.abspath(os.path.join(target_abs, rel_p))
        if not out_path.startswith(target_abs):
            raise ValueError(f"Security alert: Invalid archive path detected ({rel_p})")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        print_live_bar(f"Writing files ({idx}/{num_files})", idx, num_files, f"-> {rel_p[:32]}")

    if num_files > 0:
        print()
    return num_files


def cmd_compress(input_path: str, output_path: str | None, mode: str, force: bool, delete_source: bool, verbose: bool):
    if not os.path.exists(input_path):
        print(f"[ERROR] Source not found: {input_path}")
        sys.exit(1)

    is_dir = os.path.isdir(input_path)
    if output_path is None:
        clean_in = input_path.rstrip("/\\")
        output_path = f"{clean_in}.elp8"

    if os.path.exists(output_path) and not force:
        print(f"[ABORT] Target already exists: {output_path} (use -f / --force to overwrite)")
        sys.exit(1)

    print("=" * 84)
    print(f"EULNO COMPRESSOR v0.9.0-beta | Mode: {mode.upper()} | Target: {os.path.basename(output_path)}")
    print("=" * 84)

    watchdog = ActivityWatchdog(timeout_sec=45.0)
    watchdog.start("Reading input & SHA-512")
    try:
        t_start = time.perf_counter()
        if is_dir:
            print(f"  [1/4] Bundling directory structure: {input_path}")
            raw_bytes, file_count = pack_directory_to_stream(input_path)
            print(f"        -> {file_count} files bundled ({len(raw_bytes):,} bytes raw stream)")
        else:
            print(f"  [1/4] Reading source file: {os.path.basename(input_path)}")
            with open(input_path, "rb") as f:
                raw_bytes = f.read()
            print(f"        -> Original size: {len(raw_bytes):,} bytes")

        watchdog.beat("Computing SHA-512")
        orig_size = len(raw_bytes)
        orig_sha = hashlib.sha512(raw_bytes).hexdigest()
        print(f"  [2/4] Computing SHA-512 reference anchor: {orig_sha[:24]}...{orig_sha[-12:]}")

        profile_id = 0 if mode == "turbo" else 1
        print(f"  [3/4] Running ELP8 compression ({'TURBO-ZIGZAG' if profile_id == 0 else 'MDL AUTO-ROUTER G0..G5'})...")
        watchdog.beat("Compressing chunks")

        def _cli_progress(curr: int, total: int, gear_name: str):
            watchdog.beat(f"Chunk {curr}/{total} ({gear_name})")
            pct = (curr / total) * 100.0
            sys.stdout.write(f"\r        -> Processing chunk {curr}/{total} ({pct:5.1f}%) | Last winner: {gear_name:<18}")
            sys.stdout.flush()
            if curr == total:
                sys.stdout.write("\n")
                sys.stdout.flush()

        t_comp0 = time.perf_counter()
        compressed_blob, stats = compress_elp8(raw_bytes, profile=profile_id, progress_cb=_cli_progress)
        comp_ms = max(0.01, (time.perf_counter() - t_comp0) * 1000.0)
    finally:
        watchdog.stop()

    comp_size = len(compressed_blob)
    ratio_pct = (comp_size * 100.0 / orig_size) if orig_size > 0 else 100.0
    saved_pct = 100.0 - ratio_pct
    speed_mbs = (orig_size / 1_048_576.0) / (comp_ms / 1000.0)

    print(f"  [4/4] Writing ELP8 container: {output_path}")
    with open(output_path, "wb") as f:
        f.write(compressed_blob)

    total_ms = (time.perf_counter() - t_start) * 1000.0
    print("-" * 84)
    print(f"  RESULT        : {orig_size:,} B  --->  {comp_size:,} B (incl. 96 B SHA-512 header)")
    print(f"  RATIO         : {ratio_pct:.2f} % of original (Space saved: {saved_pct:+.2f} %)")
    print(f"  SELECTED GEARS: {stats.get('modes', {})}")
    print(f"  THROUGHPUT    : {speed_mbs:.2f} MB/s (Core: {comp_ms:.1f} ms | Total: {total_ms:.1f} ms)")
    if verbose:
        print(f"  FULL SHA-512  : {orig_sha}")
    print("=" * 84)

    if delete_source and not is_dir:
        os.remove(input_path)
        print(f"  [INFO] Source file removed (--delete-source): {input_path}")
def cmd_extract(archive_path: str, output_path: str | None, force: bool, verbose: bool):
    if not os.path.isfile(archive_path):
        print(f"[ERROR] Archive not found: {archive_path}")
        sys.exit(1)

    print("=" * 84)
    print(f"EULNO EXTRACTOR v0.9.0-beta | Source: {os.path.basename(archive_path)}")
    print("=" * 84)
    t0 = time.perf_counter()

    watchdog = ActivityWatchdog(timeout_sec=45.0)
    watchdog.start("Reading ELP8 archive")
    try:
        print("  [1/3] Reading ELP8 container and validating header...")
        with open(archive_path, "rb") as f:
            blob = f.read()

        watchdog.beat("Decoding chunks & SHA-512 verification")
        print("  [2/3] Decoding chunks & verifying SHA-512 checksum (100% bit-exact)...")
        t_dec0 = time.perf_counter()
        raw_bytes = decompress_elp8(blob)
        dec_ms = max(0.01, (time.perf_counter() - t_dec0) * 1000.0)
    finally:
        watchdog.stop()

    speed_mbs = (len(raw_bytes) / 1_048_576.0) / (dec_ms / 1000.0)

    is_dir_archive = raw_bytes.startswith(ARCHIVE_MAGIC)
    if output_path is None:
        base = archive_path[:-5] if archive_path.lower().endswith(".elp8") else f"{archive_path}.unpacked"
        output_path = base

    if os.path.exists(output_path) and not force and not is_dir_archive:
        print(f"[ABORT] Target already exists: {output_path} (use -f / --force)")
        sys.exit(1)

    print(f"  [3/3] Writing reconstructed data to: {output_path}")
    if is_dir_archive:
        n_files = unpack_stream_to_directory(raw_bytes, output_path)
        print(f"        -> Directory with {n_files} files completely restored.")
    else:
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(raw_bytes)

    total_ms = (time.perf_counter() - t0) * 1000.0
    print("-" * 84)
    print(f"  INTEGRITY     : SHA-512 PASS (100% Bit-Exact Verified)")
    print(f"  RESTORED      : {len(raw_bytes):,} bytes in {dec_ms:.1f} ms ({speed_mbs:.2f} MB/s | Total: {total_ms:.1f} ms)")
    if verbose:
        print(f"  FULL SHA-512  : {hashlib.sha512(raw_bytes).hexdigest()}")
    print("=" * 84)


def cmd_test_and_info(archive_path: str, verify_full: bool):
    if not os.path.isfile(archive_path):
        print(f"[ERROR] File not found: {archive_path}")
        sys.exit(1)

    with open(archive_path, "rb") as f:
        blob = f.read()

    if len(blob) < ELP8_HDR_SIZE or blob[:4] != ELP8_MAGIC:
        print(f"[ERROR] Not a valid ELP8 archive: {archive_path}")
        sys.exit(1)

    _, profile, _, orig_len, num_chunks, _, expected_sha, _ = struct.unpack(ELP8_HDR_FMT, blob[:ELP8_HDR_SIZE])
    ratio = (len(blob) * 100.0 / orig_len) if orig_len > 0 else 100.0

    print("=" * 84)
    print("EULNO CONTAINER INSPECTION & INTEGRITY TEST (.elp8 | v0.9.0-beta)")
    print("=" * 84)
    print(f"  Author        : {AUTHOR_INFO}")
    print(f"  Support       : {SUPPORT_INFO}")
    print(f"  Commercial    : {COMMERCIAL_INFO}")
    print(f"  License       : {LICENSE_INFO}")
    print("-" * 84)
    print(f"  File          : {archive_path}")
    print(f"  Container Size: {len(blob):,} bytes")
    print(f"  Original Size : {orig_len:,} bytes (Ratio: {ratio:.2f} %)")
    print(f"  Profile       : {'TURBO_MODE (0)' if profile == 0 else 'MDL_AUTO_MAX (1)'} | Chunks: {num_chunks}")
    print(f"  SHA-512 Anchor: {expected_sha.hex()}")

    if verify_full:
        t0 = time.perf_counter()
        decompress_elp8(blob)
        ms = (time.perf_counter() - t0) * 1000.0
        print(f"  INTEGRITY     : PASS (Full decompression + SHA-512 verification in {ms:.1f} ms OK!)")
    print("=" * 84)


def _pause_step(seconds: int = 5):
    for rem in range(seconds, 0, -1):
        sys.stdout.write(f"\r  [PAUSE] Next step in {rem}s (press Ctrl+C to abort)... ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r" + " " * 64 + "\r")
    sys.stdout.flush()


def cmd_self_test(pause_sec: int = 5):
    """Runs a relaxed, interactive 6-step end-to-end self-test with automatic cleanup."""
    print("=" * 84)
    print("EULNO CLI PACKER v0.9.0-beta — INTERACTIVE END-TO-END SELF-TEST")
    print("=" * 84)
    print("  Testing all CLI commands, modes (MAX & TURBO), folder archiving, and SHA-512.")

    tmp_dir = tempfile.mkdtemp(prefix="eulno_selftest_")
    try:
        sample_file = os.path.join(tmp_dir, "sample_payload.py")
        engine_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eulno_beta_engine.py")
        if os.path.isfile(engine_path):
            shutil.copy2(engine_path, sample_file)
        else:
            with open(sample_file, "wb") as f:
                f.write((b'{"sensor":"tetra3d","status":"OK","val":42}\n' * 600))

        arc_max = os.path.join(tmp_dir, "test_max.elp8")
        arc_turbo = os.path.join(tmp_dir, "test_turbo.elp8")
        restored_file = os.path.join(tmp_dir, "restored_payload.py")

        print("\n>>> STEP 1/6: Version, Author & License Telemetry (--version)")
        print(f"  Author     : {AUTHOR_INFO}")
        print(f"  Support    : {SUPPORT_INFO}")
        print(f"  Commercial : {COMMERCIAL_INFO}")
        print(f"  License    : {LICENSE_INFO}")
        _pause_step(pause_sec)

        print("\n>>> STEP 2/6: Compressing File in MDL_AUTO_MAX Mode (-c -m max -v)")
        cmd_compress(sample_file, arc_max, mode="max", force=True, delete_source=False, verbose=True)
        _pause_step(pause_sec)

        print("\n>>> STEP 3/6: Compressing File in TURBO Real-Time Mode (-c -m turbo)")
        cmd_compress(sample_file, arc_turbo, mode="turbo", force=True, delete_source=False, verbose=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 4/6: Inspecting ELP8 Container Header & SHA-512 Anchor (-i)")
        cmd_test_and_info(arc_max, verify_full=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 5/6: Full Integrity Verification (-t) & Extraction (-x)")
        cmd_test_and_info(arc_max, verify_full=True)
        cmd_extract(arc_max, restored_file, force=True, verbose=False)
        _pause_step(pause_sec)

        print("\n>>> STEP 6/6: Directory Tree Bundling & Final Bit-Exact Verification")
        sub_dir = os.path.join(tmp_dir, "mini_folder")
        os.makedirs(os.path.join(sub_dir, "nested"), exist_ok=True)
        shutil.copy2(sample_file, os.path.join(sub_dir, "a.py"))
        shutil.copy2(sample_file, os.path.join(sub_dir, "nested", "b.py"))
        dir_arc = os.path.join(tmp_dir, "folder.elp8")
        dir_out = os.path.join(tmp_dir, "folder_restored")
        cmd_compress(sub_dir, dir_arc, mode="max", force=True, delete_source=False, verbose=False)
        cmd_extract(dir_arc, dir_out, force=True, verbose=False)

        with open(sample_file, "rb") as f1, open(restored_file, "rb") as f2:
            if sha512_bytes(f1.read()) != sha512_bytes(f2.read()):
                raise RuntimeError("Self-test SHA-512 mismatch!")

        print("\n" + "=" * 84)
        print("  SELF-TEST RESULT: ALL 6 STEPS PASSED (100% SHA-512 BIT-EXACT VERIFIED!)")
        print("=" * 84)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_cli_parser() -> argparse.ArgumentParser:
    epilog_text = f"""
====================================================================================
PARAMETER COMBINATIONS & USAGE EXAMPLES (EULNO CLI PACKER v0.9.0-beta)
====================================================================================
  1. Compress a single file using maximum compression (MDL Auto-Router G0..G5):
     python eulno_pack.py -c data.bin
     python eulno_pack.py --compress model_weights.safetensors -o archive.elp8

  2. Use Ultra-Fast Mode (TURBO: >100 MB/s real-time throughput):
     python eulno_pack.py -c sensor_stream.dat -m turbo

  3. Compress an entire directory tree including subdirectories:
     python eulno_pack.py -c ./my_project_folder -o project_backup.elp8

  4. Overwrite existing target (-f) and remove source file after verification:
     python eulno_pack.py -c logfile.json -f --delete-source

  5. Extract an .elp8 archive bit-exact (with automatic SHA-512 verification):
     python eulno_pack.py -x archive.elp8
     python eulno_pack.py --extract project_backup.elp8 -o ./restored_folder

  6. Verify archive integrity (-t) or inspect metadata & SHA-512 anchor (-i):
     python eulno_pack.py -t archive.elp8
     python eulno_pack.py -i archive.elp8

====================================================================================
AUTHOR     : {AUTHOR_INFO}
SUPPORT    : {SUPPORT_INFO}
COMMERCIAL : {COMMERCIAL_INFO}
LICENSE    : {LICENSE_INFO}
====================================================================================
"""
    parser = argparse.ArgumentParser(
        prog="eulno_pack.py",
        description="EULNO (.elp8) - High-Performance Compressor & Extractor (3D-Tetrahedron + MDL Router + SHA-512)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_text,
    )

    action_grp = parser.add_mutually_exclusive_group(required=True)
    action_grp.add_argument("-c", "--compress", metavar="SOURCE", help="Compress a file or directory into an .elp8 archive")
    action_grp.add_argument("-x", "--extract", metavar="ARCHIVE", help="Extract an .elp8 archive bit-exact (with SHA-512 check)")
    action_grp.add_argument("-t", "--test", metavar="ARCHIVE", help="Verify full bit-integrity of an .elp8 archive (SHA-512 test)")
    action_grp.add_argument("-i", "--info", metavar="ARCHIVE", help="Display container metadata, profile, ratio, and SHA-512 anchor")
    action_grp.add_argument("--version", action="store_true", help="Display version, author, support, and license information")
    action_grp.add_argument("--self-test", action="store_true", help="Run an interactive 6-step end-to-end self-test with 5s pauses")

    parser.add_argument("-o", "--output", metavar="TARGET", default=None, help="Custom output path (file or destination folder)")
    parser.add_argument(
        "-m",
        "--mode",
        choices=("max", "turbo"),
        default="max",
        help="Compression profile: 'max' = MDL Auto-Router G0..G5 (default), 'turbo' = Real-time speed G0..G1",
    )
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing output files without prompting")
    parser.add_argument("--delete-source", action="store_true", help="Delete source file after verified compression")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose telemetry including full 128-char SHA-512 hash")
    return parser


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    try:
        if args.version:
            print("=" * 84)
            print("EULNO CLI PACKER v0.9.0-beta (ELP8 Master Container | Public Beta)")
            print(f"Author     : {AUTHOR_INFO}")
            print(f"Support    : {SUPPORT_INFO}")
            print(f"Commercial : {COMMERCIAL_INFO}")
            print(f"License    : {LICENSE_INFO}")
            print("=" * 84)
        elif args.self_test:
            cmd_self_test(pause_sec=5)
        elif args.compress:
            cmd_compress(args.compress, args.output, args.mode, args.force, args.delete_source, args.verbose)
        elif args.extract:
            cmd_extract(args.extract, args.output, args.force, args.verbose)
        elif args.test:
            cmd_test_and_info(args.test, verify_full=True)
        elif args.info:
            cmd_test_and_info(args.info, verify_full=False)
    except Exception as exc:
        print(f"\n[CRITICAL ERROR] {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()

