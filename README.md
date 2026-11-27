---
license: agpl-3.0
license_link: LICENSE

tags:
  - compression
  - data-compression
  - lossless-compression
  - 3d-geometry
  - wheel8-geometry
  - combinatorics
  - sha512
  - rfc-specification
  - pure-python
---

# EULNO (`.elp8`) — 3D-Tetrahedron, Wheel-8 & Combinatorial Compression Suite (`v0.9.7-beta`)

**Quick Links:** [🌐 Project Website (eulno.org)](https://eulno.org) · [📖 User Guide & Functional Reference (DE)](docs/manual/EULNO_Funktionsreferenz_DE.md) · [📄 IETF RFC Draft (`draft-heilos-eulno-elp8-00`)](docs/rfc/draft-heilos-eulno-elp8-00.xml) · [🏛️ Zenodo DOI (`10.5281/zenodo.24521151`)](https://doi.org)

**Public Beta Notice (`v0.9.7-beta`):** This release implements the RFC-compliant `.elp8` streaming container specification (`draft-heilos-eulno-elp8-00`), full-lifecycle TOCTOU/symlink/quarantine hardening, a 100% self-contained native `rANS-256` entropy backend (`zlib`-free by default), and 8-gear MDL routing (`G0..G7`). Always maintain independent, verified backups of important files before testing new archive formats.

**EULNO** is a pure-Python, **100% self-contained** integer-based lossless compression engine and `O(1)`-RAM streaming CLI archiving suite. In its default operating mode, EULNO runs completely without external compression libraries (`zlib`-free), pairing **discrete 3D-tetrahedron geometry**, **counter-rotating modulo-8 wheel geometry (`S₀`)**, **multi-stride difference operators (`Δₛ,ₖ`)**, **CTZ-accelerated Pascal `C(64, k)` coding**, and **32-KiB Burrows-Wheeler + Move-To-Front (`BWT+MTF`)** transforms with its own native, purely integer **256-symbol rANS (`M = 4096`) entropy coder**.

Every `.elp8` container enforces **bit-exact data integrity** via an RFC-compliant tiered master header (**56 bytes** with SHA-256, **88 bytes** with SHA-512, or **152 bytes** with Bit-3 Origin Authentication Tag) where the cryptographic Anchor Hash simultaneously seeds deterministic wheel alignment (`S₀ = uint64(H₀[0..7]) mod 8`) and validates reconstruction in constant time.

---

## Key Features (`v0.9.7-beta`)

* **100% Self-Contained Native Entropy Backend (`zlib`-Free Default):**
  * By default (`-m max` and `-m turbo`), EULNO relies on **zero third-party compression libraries** (`zlib`-free), driving all transform gears (`G1..G7`) through its own deterministic, purely integer **256-symbol rANS & 2-state context-rANS coder (`M = 4096`)**.
  * External `zlib` compression (`G10..G13`, RFC Flag Bit 5 `0x20`) is strictly decoupled as an opt-in speed/legacy backend via `--use-zlib` and is rejected by default during verification and extraction unless explicitly enabled.
* **RFC-Compliant Streaming Container (`draft-heilos-eulno-elp8-00`):**
  * **Authenticated Vault Encryption (`-p / --password`, Flag Bit 4):** Optional post-quantum-safe payload encryption using **Argon2id** key derivation and **`O(1)`-RAM `XChaCha20-Poly1305` secretstream** (`Compress-then-Encrypt`) with AEAD header binding.
  * Supports arbitrarily large files (multi-GB / TB up to `2^64 - 1` bytes) with **constant `O(1)` RAM usage**.
  * Configurable **Anchor Hash** (`--hash sha512` default or `--hash sha256`) and optional **64-byte Origin Authentication Tag** (`--key-file`, Flag Bit 3).
  * Full-lifecycle security hardening: `O_NOFOLLOW` descriptor locking, two-pass TOCTOU modification guard, bounded decompression (`64 MiB` chunk ceiling), path-traversal & symlink-escape protection, overwrite protection (requires `-f`), and atomic quarantine promotion (`os.replace`).
* **Dual Operating Profiles (`TURBO` vs. `MAX`):**
  * `TURBO` (`-m turbo`): Restricts routing to Gear 0 (Noise Bypass), Gear 1 (`Δₛ,ₖ` ZigZag + rANS-256), and Gear 6 (Wheel-8 Mirror-Spiral + rANS-256) for ultra-fast throughput.
  * `MAX` (`-m max`, default): Activates the full **Minimum Description Length (MDL) Auto-Router** across all 8 Gears (`G0..G7`), automatically selecting the smallest verified representation per chunk.
* **8 Specialized Integer Transform Gears (`G0..G7`, Zero Floating-Point Drift):**
  * **Gear 0 (`G0_RAW`):** Dual-entropy pre-filter (`H(X)` and `H(ΔX)`) detects incompressible random noise in `< 0.25 ms` with zero payload expansion.
  * **Gear 1 (`G1_TURBO_RANS`):** Unified spatial/curve difference equation `(Xᵢ − k·Xᵢ₋ₛ + (k−1)·Xᵢ₋₂ₛ) mod 256` with vectorized ZigZag sign folding and native rANS-256 coding.
  * **Gear 2 (`G2_NIBBLE_RANS`):** 4-bit nibble decomposition and 64-column matrix transposition with native rANS-256 coding.
  * **Gear 3 (`G3_TETRA3D_SPARSE`):** Causal 3D-tetrahedron gradient predictor with hierarchical macro-zero runs (`32`, `512`, `4096`, `32768`) and 20-symbol sparse rANS coding.
  * **Gear 4 (`G4_BITPLANE_RANS`):** 8-layer bit-plane slicing + bitwise XOR edge detection + CTZ-accelerated (`w & -w`) Pascal `C(64, k)` enumerative coding.
  * **Gear 5 (`G5_MTF_TETRA3D`):** Move-To-Front recency ranking coupled with 3D-tetrahedron gradient coding.
  * **Gear 6 (`G6_WHEEL8_RANS`):** Counter-rotating modulo-8 wheel phase transform with **Inward-Mirror-Split** (left half clockwise `0 -> mid-1`, right half counter-clockwise inward `n-1 -> mid`) anchored at sector `S₀`.
  * **Gear 7 (`G7_BWT_CONTEXT`):** Static `O(1)`-arena 32-KiB Prefix-Doubling Burrows-Wheeler Transform (`BWT`) + Move-To-Front (`MTF`) coupled with EULNO's native 2-state context-rANS coder (`Mₜ₋₁ == 0` vs. `Mₜ₋₁ > 0`).
* **Built-in End-to-End Self-Test (`--self-test`):** 5-stage verification suite covering pure-EULNO `G0..G7`, SHA-512 & SHA-256, Bit-3 origin authentication, the `--use-zlib` rejection guard, directory tree bundling, and Argon2id + XChaCha20-Poly1305 encryption.


---

## Installation & Quick Start

```bash
pip install -r requirements.txt
python eulno_pack.py --self-test
```

### CLI Usage Examples

```bash
# Compress and encrypt an archive with Argon2id + XChaCha20-Poly1305
python eulno_pack.py -c secret_data.bin -p

# Compress a single file with 100% self-contained EULNO MAX (G0..G7, native rANS-256, SHA-512)
python eulno_pack.py -c heartbeat.wav

# Compress with SHA-256 compact 56-byte header and Bit-3 Origin Authentication Tag
python eulno_pack.py -c data.bin --hash sha256 --key-file secret.key

# Compress using Ultra-Fast Mode (TURBO: Gears G0, G1, G6) or optional --use-zlib speed backend
python eulno_pack.py -c sensor_stream.dat -m turbo
python eulno_pack.py -c large_backup.bin -m turbo --use-zlib

# Compress an entire directory tree including subdirectories (O(1) RAM)
python eulno_pack.py -c ./my_project_folder -o project_backup.elp8

# Extract an .elp8 archive bit-exact (live chunk progress + constant-time Anchor Hash verification)
python eulno_pack.py -x archive.elp8

# Verify full stream integrity (-t) or inspect RFC header & Start Spoke S0 (-i)
python eulno_pack.py -t archive.elp8
python eulno_pack.py -i archive.elp8
```

---

## Benchmark & Real-World Streaming Evaluation (`v0.9.7-beta`)

* **100 MB General Corpus Benchmark (`enwik8` — 100,000,000 B, Pure Native rANS-256, Zero `zlib`):**
  * **Compression (`-m max --hash sha512`):** `100,000,000 B` → `38,904,580 B` (**38.90% | +61.10% lossless space saved**, `88 B` RFC header, `S0 = 1`, Gears: `96x G7_BWT_CONTEXT`).
  * **Extraction & Verification (`-x` / `-t`):** `100,000,000 B` restored & verified in **`51.694 s`** — `SHA512 PASS (100% Bit-Exact)`.
* **Real-World Uncompressed PCM Audio (`heartbeat.wav` — 67,752,136 B):**
  * **Compression (`-m max --hash sha512`):** `67,752,136 B` → `60,829,784 B` (**+10.22% lossless space saved**, `88 B` RFC header, `S0 = 3`, Gears: `51x G2`, `14x G1`).
  * **Extraction & Verification (`-x`):** `67,752,136 B` restored in **`842.3 ms` (`76.71 MB/s`)** — `SHA512 PASS (100% Bit-Exact)`.

| Data Domain (50,000 B) | EULNO `TURBO` | EULNO `MAX` (MDL Router) | `zlib` (L9, no SHA) | `LZMA` (7-Zip, no SHA) | EULNO Domain Verdict |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1. Structured 3D Voxel Frames** | 1,032 B (**90.4 MB/s**) | **437 B** | 511 B | 456 B | **Best-in-Class (Outperforms LZMA & zlib)** |
| **2. Sensor Ramps & Step Telemetry** | **629 B** (**70.3 MB/s**) | **182 B** | 697 B | 388 B | **Exceptional (>2x smaller than LZMA)** |
| **3. Smooth Sine / Wave Signals** | 17,602 B (52.0 MB/s) | **4,767 B** | 4,414 B | **2,236 B** | **Strong Real-Time Alternative** |
| **4. Balanced Alphabet (Base64)** | 50,105 B (55.6 MB/s) | **43,874 B** | **38,487 B** | 38,544 B | **Competitive Pure-Python Model** |
| **5. Repetitive JSON / Source Text** | 779 B (65.8 MB/s) | **396 B** | 224 B | **196 B** | **Fast & Compact (Dictionary-free)** |
| **6. Cryptographic Random Noise** | 50,105 B (**192.7 MB/s**) | **50,105 B** *(Raw Bypass)* | 50,026 B | 50,060 B | **Zero Payload Expansion (Instant Bypass)** |

---

## Repository Structure

* `eulno_beta_engine.py` — RFC-compliant `ELP8` streaming engine, native rANS-256 coder (`M = 4096`), MDL router, and Gears `G0..G7` (plus opt-in `G10..G13`).
* `eulno_pack.py` — Hardened CLI packer/extractor, `O(1)`-RAM directory bundler, and 5-stage `--self-test` runner.
* `eulno_tetra3d_v21.py` — Discrete 3D-tetrahedron gradient predictor and 20-symbol sparse rANS backend (`G3` & `G5`).
* `docs/rfc/draft-heilos-eulno-elp8-00.xml` — Official IETF Internet-Draft specification (RFCXML v3) for the `.elp8` container format.
* `docs/manual/` — German Functional Reference & User Guide (`EULNO_Funktionsreferenz_DE.md` & `.pdf` + visual diagrams).
* `LICENSE` — GNU Affero General Public License v3.0 (GNU AGPLv3).
* `NOTICE.md` — Dual-licensing terms, commercial enterprise option, and user safety notices.
* `CONTRIBUTING.md` — Contributor License Agreement (CLA) for pull requests.
* `requirements.txt` — Minimal Python dependency list (`numpy`, `pynacl`).
* `DEDICATION.md` — Dedication to Leonhard Euler & Emmy Noether.

---

## Citation & Official Identifiers

* **Zenodo Archive DOI:** [`10.5281/zenodo.24521151`](https://doi.org/10.5281/zenodo.24521151)
* **IETF Specification Draft:** `draft-heilos-eulno-elp8-00` (*The ELP8 Container Format for Geometric Wheel-8 Compression, Hash-Anchored Self-Alignment, and Multi-Gear Streaming*)
* **Project Website:** [https://eulno.org](https://eulno.org)

---

## Author, Support & Licensing

* **Author:** Robin Heilos (`McCalm`) — [Hugging Face](https://huggingface.co/mccalm/eulno-compression-suite) | [GitHub](https://github.com/mccalm/eulno-compression-suite) | [eulno.org](https://eulno.org)
* **Technical Support & Bug Reports:** Please use the **Community Tab (Discussions)** on [Hugging Face](https://huggingface.co/mccalm/eulno-compression-suite/discussions) or **Issues** on [GitHub](https://github.com/mccalm/eulno-compression-suite/issues). *No individual email support is provided for community releases.*
* **Open-Source License:** **GNU Affero General Public License v3.0 (GNU AGPLv3)** — Free to use, study, modify, and distribute under strict copyleft terms, including network/cloud/SaaS source disclosure (Section 13). See `LICENSE` and `NOTICE.md`.
* **Commercial Enterprise Licensing (Proprietary / Closed-Source Use):** `mccalm@outlook.de`

