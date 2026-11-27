# EULNO (.elp8) — Legal Notices, Commercial Licensing & Operational Safety

## 1. Open-Source License (GNU AGPLv3) & Commercial Dual-Licensing
EULNO (`eulno_beta_engine.py`, `eulno_pack.py`, `eulno_tetra3d_v21.py`, `verify_elp8_hardened.py`, and related source files) is licensed under the **GNU Affero General Public License v3.0 (GNU AGPLv3)** — see the `LICENSE` file for the full binding text.

Under the GNU AGPLv3, you are free to use, study, modify, and distribute this software, provided that any distributed derivative works and any network/cloud/SaaS services offering functionality based on this software release their complete corresponding source code under the GNU AGPLv3 (Section 13).

**Commercial Enterprise License (Proprietary / Closed-Source Integration):**
Organizations, cloud providers, or commercial entities wishing to integrate EULNO into proprietary products, internal commercial platforms, or network services **without** releasing their own source code under the GNU AGPLv3 may obtain a separate written **Commercial Enterprise License** from the copyright holder:
* **Contact:** Robin Heilos (`McCalm`) — `mccalm@outlook.de`

## 2. Disclaimer of Warranty & Limitation of Liability
In accordance with Sections 15 and 16 of the GNU AGPLv3, this software is provided **"AS IS"** without warranty of any kind, express or implied. To the maximum extent permitted by applicable statutory law, the author and copyright holder shall not be liable for any direct, indirect, incidental, consequential, or special damages, including loss of data or file corruption.

## 3. Mandatory User Safety Obligations
* **Independent Backups:** You are solely responsible for maintaining independent, verified backups of all data before compressing, transforming, or archiving files with EULNO.
* **Zero-Knowledge Encryption (`Argon2id` + `XChaCha20-Poly1305`):** When using password-based vault encryption (`-p / --password`), there is no master password, backdoor, or recovery key. Lost passwords cannot be recovered under any circumstances.
* **High-Risk Systems:** This software is not designed or certified for use in life-support, medical, nuclear, aviation, or defense systems.

---
Copyright (c) 2026 Robin Heilos (`McCalm`) — https://eulno.org | https://huggingface.co | https://github.com
