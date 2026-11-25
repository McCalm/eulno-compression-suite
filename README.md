---
license: other
license_name: eulno-community-2.0
license_link: LICENSE
tags:
  - compression
  - data-compression
  - lossless-compression
  - 3d-geometry
  - combinatorics
  - sha512
  - pure-python
---

# EULNO (`.elp8`) — 3D-Tetrahedron & Combinatorial Compression Suite (`v0.9.0-beta`)

> **Public Beta Notice (`v0.9.0-beta`):** This release is an experimental research and engineering preview. Always maintain independent, verified backups of important files before testing new archive formats.

**EULNO** is a pure-Python, integer-based lossless compression engine and CLI archiving suite built around **discrete 3D-tetrahedron geometry**, **unified multi-stride difference operators (`Δₛ,ₖ`)**, **bit-plane slicing with CTZ-accelerated Pascal `C(64, k)` combinatorial coding**, and **Move-To-Front (MTF)** transforms.

Every `.elp8` container enforces **bit-exact data integrity** via an integrated **96-byte master header** containing a mandatory **64-byte SHA-512 cryptographic anchor** and strict chunk boundary validation.

---

## Key Features

* **Dual Operating Profiles (`TURBO` vs. `MAX`):**
  * `TURBO` (`-m turbo`): Restricts routing to Gear 0 (Noise Bypass) and Gear 1 (Unified Difference Operator `Δₛ,ₖ` + Vectorized ZigZag) for real-time throughput (**70–192 MB/s** in pure Python).
  * `MAX` (`-m max`, default): Activates the full **Minimum Description Length (MDL) Auto-Router** across Gears `0..5`, automatically selecting the smallest verified representation per chunk.
* **5 Specialized Integer Transform Gears (Zero Floating-Point Drift):**
  * **Gear 0 (`G0_RAW`):** Dual-entropy pre-filter (`H(X)` and `H(ΔX)`) detects incompressible random noise in `< 0.25 ms` with zero payload expansion.
  * **Gear 1 (`G1_TURBO_ZIGZAG`):** Unified spatial/curve difference equation `(Xᵢ − k·Xᵢ₋ₛ + (k−1)·Xᵢ₋₂ₛ) mod 256` with vectorized ZigZag sign folding.
  * **Gear 2 (`G2_NIBBLE_16x64`):** 4-bit nibble decomposition and 64-column matrix transposition.
  * **Gear 3 (`G3_TETRA3D_SPARSE`):** Causal 3D-tetrahedron gradient predictor with hierarchical macro-zero runs (`32`, `512`, `4096`, `32768`) and 20-symbol sparse rANS coding.
  * **Gear 4 (`G4_BITPLANE_PASCAL`):** 8-layer bit-plane slicing + bitwise XOR edge detection + CTZ-accelerated (`w & -w`) Pascal `C(64, k)` enumerative coding.
  * **Gear 5 (`G5_MTF_TETRA3D`):** Move-To-Front recency ranking coupled with 3D-tetrahedron gradient coding.
* **Autarkic SHA-512 Container (`ELP8`):** 96-byte, 16-byte-aligned master header with full file verification (`-t`), header inspection (`-i`), and path-traversal-safe directory tree bundling.
* **Built-in Interactive Self-Test (`--self-test`):** End-to-end 6-step verification suite with automatic temporary file cleanup.

---
## How EULNO Works Under the Hood (Detailed Visual & Step-by-Step Guide)

Most files are not uniform. A single project folder or dataset can contain smooth sensor curves, 3D spatial grids, structured text, and encrypted or random data all at once. Traditional compressors rely on a single rigid method (such as dictionary matching). **EULNO works like an intelligent automatic multi-gear transmission**: it slices your data into chunks (default: `1 MiB`), inspects each chunk's mathematical structure in milliseconds, and automatically engages the exact combination of engines that yields the smallest, 100% bit-exact result.

### Visual Pipeline Overview (`ELP8` Master Container)

```text
  [Input File / Folder]
           │
           ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 1. SHA-512 Cryptographic Anchor (64-Byte Digest computed upfront)   │
  └─────────────────────────────────────────────────────────────────────┘
           │
           ▼  (Split into 1 MiB Chunks)
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 2. Gear 0: Dual-Entropy Gatekeeper  H(X) & H(dX) < 0.25 ms          │
  │    └── Pure random noise / encrypted? ──► Instant Raw Bypass (G0)   │
  └─────────────────────────────────────────────────────────────────────┘
           │ (Structured data passes through)
           ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 3. MDL Auto-Router (Measures exact compressed byte size per gear)   │
  │    ├── [TURBO Profile] ──► Gear 1 only (Real-time: 70–192 MB/s)     │
  │    └── [MAX Profile]   ──► Competes Gears 1, 2, 3, 4 & 5            │
  │         ├── Gear 1: Multi-Stride Difference + ZigZag                │
  │         ├── Gear 2: 16x64 Hex-Nibble Column Transposition           │
  │         ├── Gear 3: 3D-Tetrahedron Gradient + Sparse rANS-20        │
  │         ├── Gear 4: 8-Layer Bit-Plane Slicing + Pascal C(64, k)     │
  │         └── Gear 5: Move-To-Front (MTF) + 3D-Tetrahedron            │
  └─────────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 4. Self-Verifying .elp8 Container (96-Byte Header + Winner Chunks)  │
  └─────────────────────────────────────────────────────────────────────┘
```

### Deep-Dive: Every Gear Explained with Concrete Examples

#### 1. The MDL Auto-Router (The Decision Brain)
* **What happens:** Instead of guessing which algorithm might work best, EULNO applies the **Minimum Description Length (MDL)** principle. For every chunk, the router runs the candidate transforms and compares the exact resulting byte sizes—including all metadata needed to reverse the step.
* **Why it matters to you:** Even if a specialized transform does not help on a weird file, EULNO never makes your data worse. It always picks the smallest verified representation and records the chosen Gear ID (`0..5`) in the chunk's 9-byte header.

#### 2. Gear 0 — Instant Noise Bypass (`G0_RAW`)
* **What happens:** Encrypted archives (`.zip`, `.7z`, `.gpg`), already-compressed videos (`.mp4`), or cryptographic random numbers contain no predictable patterns. Gear 0 samples the chunk and checks two entropies in under `0.25 ms`: raw byte randomness `H(X)` and step-to-step difference randomness `H(ΔX)`.
* **Concrete Example:**
  * *Encrypted File:* Both `H(X) ≥ 7.95` and `H(ΔX) ≥ 7.95`. Gear 0 immediately shuts down all heavy math and stores the chunk raw with `0` bytes of expansion.
  * *Sensor Ramp (`0, 1, 2, 3 ... 255`):* Every byte value appears equally often (`H(X) = 8.0`, looking like noise at first glance!), **but** the difference between neighbors is always `+1` (`H(ΔX) ≈ 0.81`). Gear 0 recognizes the hidden order and lets the chunk pass straight into Gears 1–5!

#### 3. Gear 1 — Multi-Stride Difference & ZigZag (`G1_TURBO_ZIGZAG`)
* **What happens:** Smooth signals (audio, temperature curves, motor telemetry) rarely jump wildly from one reading to the next. Gear 1 tests multiple step distances (*strides* `s = 1, 32, 512, 1024`) and difference orders (`k = 1` for velocity, `k = 2` for acceleration), then folds negative and positive numbers into small unsigned integers using **ZigZag encoding** (`0 -> 0, -1 -> 1, +1 -> 2, -2 -> 3, +2 -> 4`).
* **Concrete Example:**
  * *Raw values:* `[100, 102, 104, 106, 108]` (5 distinct large numbers)
  * *1st Difference (`s=1, k=1`):* `[100, +2, +2, +2, +2]`
  * *2nd Difference (`s=1, k=2`):* `[100, +2, 0, 0, 0]` — A rising curve turns almost entirely into zeros!

#### 4. Gear 2 — Hex-Nibble Column Transposition (`G2_NIBBLE_16x64`)
* **What happens:** Many binary files (such as struct arrays, database records, or tensor tables) repeat specific bit patterns at fixed intervals. Gear 2 splits every byte into two 4-bit halves (*nibbles*: high `0..15` and low `0..15`) and rotates the stream across a 64-column matrix.
* **Concrete Example:**
  * *Before:* `[A1, B2, C3, D4 ...]` (high and low nibbles mixed together)
  * *After Transposition:* All high nibbles (`A, B, C, D...`) are grouped together in dedicated rows, and all low nibbles (`1, 2, 3, 4...`) in others—creating long, uniform runs that compress dramatically better.

#### 5. Gear 3 — 3D-Tetrahedron Gradient & Sparse rANS-20 (`G3_TETRA3D_SPARSE`)
* **What happens:** Gear 3 reshapes linear byte streams into a **3D volume of 4-bit voxels** (`X × Y × Z`). To predict each voxel, it inspects its already-known neighbors in 3D space (left `a`, above `b`, behind `c`, and diagonal corners `ab, ac, bc`) to detect whether a surface or edge is running along the X-, Y-, or Z-axis. It then XORs the actual value with the prediction, collapses long streaks of exact hits (`0`) into macro-zero tokens (`32`, `512`, `4,096`, `32,768`), and encodes the stream using a 20-symbol **rANS** (range Asymmetric Numeral Systems) entropy coder with a compact 24-bit sparse frequency mask.
* **Concrete Example:**
  * Imagine a 3D temperature grid where layer `Z=1` is almost identical to layer `Z=0`. Gear 3 detects that `err_z` is minimal, predicts every voxel from the layer behind it, and turns `50,000` bytes of 3D frames into just **437 bytes** (including the 96-byte SHA-512 container header).

#### 6. Gear 4 — Bit-Plane Slicing & Pascal Combinatorics (`G4_BITPLANE_PASCAL`)
* **What happens:** Even when whole bytes look varied (such as Base64 strings or flag fields), individual bit positions often behave very predictably (for example, the highest bit of ASCII characters is always `0`). Gear 4 slices every 8 bytes into **8 separate bit-planes** (all bit-7s together, all bit-6s together, down to bit-0), applies a bitwise XOR edge filter, and splits the stream into 64-bit words.
* **How Pascal's Triangle saves bits:** For each 64-bit word, Gear 4 counts how many `1`-bits exist (`k ∈ {0..64}`) using fast bit-isolation (`w & -w`, Count Trailing Zeros). Instead of storing all 64 bits, it stores $k$ and the exact combinatorial rank of that bit pattern among all `C(64, k)` possible arrangements:
  * If `k = 0` (all zeros) or `k = 64` (all ones): `C(64, 0) = 1` → **0 index bits needed!**
  * If `k = 2` (only two `1`-bits out of 64): Only `C(64, 2) = 2,016` combinations exist → **11 bits needed instead of 64 bits!**

#### 7. Gear 5 — Move-To-Front (MTF) + 3D Tetrahedron (`G5_MTF_TETRA3D`)
* **What happens:** Some files use a small active vocabulary locally (e.g., a few recurring symbols in a specific section) that changes over time. Gear 5 maintains a dynamic list of the 16 nibble values `[0..15]`. Whenever a symbol appears, Gear 5 outputs its current list position and moves that symbol to index `0` (the very front).
* **Concrete Example:**
  * *Input nibbles:* `[9, 9, 9, 4, 4, 9]`
  * *MTF Output indices:* `[9, 0, 0, 5, 0, 1]` — Repeated local symbols immediately become `0`s and `1`s, which are then handed over to Gear 3's 3D-Tetrahedron engine for final packing.

#### 8. Causal Online Pocket AI (`PocketAIMixer` — Core Roadmap for `v1.0.0`)
* **What happens:** Included in `eulno_tetra3d_v21.py` (v3.0 module) is an adaptive, deterministic probability mixer that combines **two competing experts** at every single voxel:
  1. **Expert 1 (3D Spatial Predictor):** Predicts from 7 surrounding 3D neighbors (including the diagonal `abc` corner).
  2. **Expert 2 (Sequential Context Memory):** Tracks short-term nibble history (`prev1`, `prev2`, and byte phase).
* **Zero-Overhead Learning:** After every voxel, `PocketAIMixer` checks which expert assigned a higher probability to the true value and shifts its internal trust weight (`w_3d`) toward the winner. Because the decoder runs the exact same deterministic math step-by-step during extraction, **0 bytes** of AI weights or probability tables ever need to be stored in the archive.

---

## Installation & Quick Start

### 1. Requirements
EULNO runs on **Python 3.10+** and requires only **NumPy** (`>= 1.24.0`):

```bash
pip install -r requirements.txt
```

### 2. Run the Interactive 6-Step Self-Test
Verify all compression modes, directory archiving, and SHA-512 integrity checks in one command:

```bash
python eulno_pack.py --self-test
```

### 3. CLI Usage Examples

```bash
# Compress a single file with maximum compression (MDL Auto-Router G0..G5)
python eulno_pack.py -c data.bin
python eulno_pack.py --compress model_weights.safetensors -o archive.elp8

# Compress using Ultra-Fast Real-Time Mode (TURBO)
python eulno_pack.py -c sensor_stream.dat -m turbo

# Compress an entire directory tree including subdirectories
python eulno_pack.py -c ./my_project_folder -o project_backup.elp8

# Extract an .elp8 archive bit-exact (automatically verifies SHA-512)
python eulno_pack.py -x archive.elp8
python eulno_pack.py --extract project_backup.elp8 -o ./restored_folder

# Verify full archive integrity (-t) or inspect container metadata (-i)
python eulno_pack.py -t archive.elp8
python eulno_pack.py -i archive.elp8
```

---

## Global Benchmark & Performance Evaluation (`v0.9.0-beta`)

Benchmarked against C-compiled industry standards (`zlib` Level 9 and `LZMA` / 7-Zip) across 6 distinct data domains (**50,000 bytes** per test stream).

> **Container Note:** Every `.elp8` output includes the mandatory **96-byte header with a 64-byte SHA-512 hash**, whereas raw `zlib` and `lzma` streams carry no cryptographic verification.

| Data Domain (50,000 B) | EULNO `TURBO` | EULNO `MAX` (MDL Router) | `zlib` (L9, no SHA) | `LZMA` (7-Zip, no SHA) | EULNO Domain Verdict |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1. Structured 3D Voxel Frames** | 1,032 B (**90.4 MB/s**) | **437 B** *(341 B payload)* | 511 B | 456 B | **Best-in-Class (Outperforms LZMA & zlib)** |
| **2. Sensor Ramps & Step Telemetry** | **629 B** (**70.3 MB/s**) | **182 B** *(86 B payload)* | 697 B | 388 B | **Exceptional (>2x smaller than LZMA)** |
| **3. Smooth Sine / Wave Signals** | 17,602 B (52.0 MB/s) | **4,767 B** *(f'' ZigZag)* | 4,414 B | **2,236 B** | **Strong Real-Time Alternative** |
| **4. Balanced Alphabet (Base64)** | 50,105 B (55.6 MB/s) | **43,874 B** *(BitPlane+Pascal)* | **38,487 B** | 38,544 B | **Competitive Pure-Python Model** |
| **5. Repetitive JSON / Source Text** | 779 B (65.8 MB/s) | **396 B** *(300 B payload)* | 224 B | **196 B** | **Fast & Compact (Dictionary-free)** |
| **6. Cryptographic Random Noise** | 50,105 B (**192.7 MB/s**) | **50,105 B** *(Raw Bypass)* | 50,026 B | 50,060 B | **Zero Payload Expansion (Instant Bypass)** |

### Engineering Scorecard (Pure `.py` vs. C-Compiled Engines)

* **Spatial & Structured Numeric Data (3D Frames, Telemetry, Ramps): `A+ (Outstanding)`** — Outperforms C-compiled `LZMA` and `zlib` on multi-dimensional frames and monotonic sensor ramps even with the 64-byte SHA-512 anchor included.
* **Built-in Cryptographic Data Integrity (`SHA-512` + Bounds Checks): `A+ (Outstanding)`** — Enforces bit-exact SHA-512 verification on extraction.
* **Pure-Python Throughput (`TURBO` Mode): `A (Excellent)`** — Reaches **70–192 MB/s** in pure Python without compiler dependencies.
* **1D Natural Language & Repetitive Text: `B (Good)`** — Specialized LZ77/BWT dictionary engines (`Zstd`, `LZMA`) achieve tighter ratios on long repetitive text strings.

---

## Repository Structure

* `eulno_beta_engine.py` — Core `ELP8` compression engine, MDL hybrid router, and Gears `0..5`.
* `eulno_pack.py` — Full-featured CLI packer/extractor, directory bundler, and `--self-test` runner.
* `eulno_tetra3d_v21.py` — Discrete 3D-tetrahedron gradient predictor and 20-symbol sparse rANS backend.
* `LICENSE` — EULNO Studio Community License v2.0.
* `requirements.txt` — Minimal Python dependency list (`numpy`).
* `DEDICATION.md` — Dedication to Leonhard Euler & Emmy Noether (and sample text payload for testing).

---

## Author, Support & Licensing

* **Author:** Robin Heilos (`McCalm`) — [https://huggingface.co](https://huggingface.co)
* **Technical Support & Bug Reports:** Please use the **Community Tab (Discussions)** on Hugging Face ([https://huggingface.co](https://huggingface.co)). *No individual email support is provided for community releases.*
* **License:** **EULNO Studio Community License v2.0** (Dedicated to Euler & Noether) — Free of charge for individuals, academic/research institutions, public authorities, and organizations with annual gross revenue strictly below **USD $10,000,000**.
* **Commercial Enterprise Licensing (≥ USD $10,000,000):** `mccalm@outlook.de`
