---
name: mac-bom
description: Generate and validate a deterministic bill of materials (BOM.csv) from a STEP file and white-box measurements. Use when a parts list is needed with quantities taken only from geometry, ISO-anchored purchased lines, and an 8-column locked schema. Part of the MAC (Multi-Agent CAD) skill family.
---

# MAC BOM generation and validation

Provenance: extracted from [Pan-Chera/Multi-Agent-CAD](https://github.com/Pan-Chera/Multi-Agent-CAD)
(`multi_agent_cad/bom.py`). Self-contained: needs only Python 3.11 + build123d
(for OCP STEP reading). No MAC checkout required.

## What it does

- One **fabricated** line per top-level solid (volume via OpenCASCADE,
  weight via `--density`, refs `#o<i>` occurrence-style).
- **Purchased** lines for measurement features matching fastener keywords
  (`screw/bolt/nut/washer/pin/bearing/standoff/rivet/insert`). `Spec-PartNo`
  comes from the ISO tables on a metric designation, else `miss` with a
  flagged substitute note — never a silent swap.
- **Qty comes only from geometry counts.**
- 8 LOCKED columns (`Item/Qty/Unit/Part/Type/Material/Spec-PartNo/Refs`,
  order immutable) + version/weight/notes footer.
- `cross_check_purchased` (opt-in via `--holes`): purchased ISO lines need at
  least Qty matching holes (sufficient, not exact). Lines without a
  designation are reported skipped, never failed.

## Usage

```bash
python scripts/bom.py --step part.step --measurements meas.json --out BOM.csv \
  --part-name my-part --material "Acrylic 4mm" --density 1.18 --version v0.2 \
  --holes 5.0,5.0
```

Exit 0 = schema clean; exit 2 = schema violations listed. See
`references/usage.md` for the column contract and the 4 lock rules.
