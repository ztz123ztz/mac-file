---
name: mac-qa-kernel
description: Kernel-level QA for STEP files via OpenCASCADE (BRepCheck validity, self-intersection) plus advisory ISO 273 hole notes. Use to catch deep B-rep defects mesh analysis cannot see, or to annotate measured holes against standards. Part of the MAC (Multi-Agent CAD) skill family.
---

# MAC kernel/standards QA (Engine C, standalone)

Provenance: extracted from [Pan-Chera/Multi-Agent-CAD](https://github.com/Pan-Chera/Multi-Agent-CAD)
(`_run_engine_c_standards` + `standards_tables.py`: ISO 273/4762 vendored
from text-to-cad, ISO 4032 nuts added). Self-contained: Python 3.11 +
build123d (OCP). No MAC checkout required. Zero tokens.

## What it does

- `kernel-validity`: every solid passes `BRepCheck_Analyzer` (fail → rebuild).
- `self-intersection`: no `BOPAlgo_SelfIntersect` (fail → rebuild).
- `iso-hole-N`: advisory only — measured through-hole diameters annotated
  against ISO 273. Undeclared holes are left unjudged, never failed.
- A checker that cannot run yields NO result (`"ran": false`), never a pass
  (cad-eval ❓ semantics).

## Usage

```bash
python scripts/qa_kernel.py part.step --holes 5.0,5.0 --json report.json
```

Exit 0 = all runnable checks passed (or engine unavailable, clearly stated);
exit 1 = real failure; exit 2 = engine crash. See `references/usage.md`.
