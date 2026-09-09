---
name: mac-spec
description: Generate a deterministic technical SPEC.md with citation checking, and freeze versions across PART.md/CHANGES.md/BOM (triad gate). Use for binding technical agreements and release stamping of CAD parts. Part of the MAC (Multi-Agent CAD) skill family.
---

# MAC technical SPEC + version freeze

Provenance: extracted from [Pan-Chera/Multi-Agent-CAD](https://github.com/Pan-Chera/Multi-Agent-CAD)
(`multi_agent_cad/spec.py`). Self-contained: Python 3.11 stdlib only.
No MAC checkout required. Zero tokens.

## What it does

- `SPEC.md`: scope/materials/geometry/holes/BOM/drawings/functional/
  acceptance in 8 sections. Every requirement carries a `[REF:]` citation;
  `check_citations` verifies each one resolves (measurement key, BOM row,
  DXF view, `#o` selector) — broken refs land in an explicit OPEN ITEMS
  section, never silently dropped.
- `freeze_version`: writes `PART.md` (artifact list + sha8), appends
  `CHANGES.md`, enforces the version triad PART == BOM == SPEC. Mismatch
  BLOCKS the freeze (exit 2).

## Usage

```bash
python scripts/spec.py --step part.step --measurements meas.json --bom BOM.csv \
  --brief brief.json --out SPEC.md --part-name my-part --version v0.2 \
  --material "Acrylic 4mm" --holes 5.0,5.0
```

Exit 0 = clean + frozen; exit 2 = broken refs or triad mismatch (details
printed). See `references/usage.md`.
