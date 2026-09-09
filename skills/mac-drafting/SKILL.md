---
name: mac-drafting
description: Generate deterministic 2D engineering DXF drawings (TOP+FRONT wireframe, overall dimensions, hole table) from a STEP file. Use for machinable drawing output, view generation, and drawing QA. Part of the MAC (Multi-Agent CAD) skill family.
---

# MAC deterministic drafting to DXF

Provenance: extracted from [Pan-Chera/Multi-Agent-CAD](https://github.com/Pan-Chera/Multi-Agent-CAD)
(`multi_agent_cad/drafting.py`). Self-contained: needs only Python 3.11,
build123d (OCP) and ezdxf. No MAC checkout required. Zero tokens.

## What it does

- Projects planar face-boundary wires (outer profiles + hole loops) to TOP
  (XY) and FRONT (XZ) elevations, one view set per solid.
- Overall bbox dimensions per view (ezdxf linear dims), hole table from
  measured diameters, title block, VISIBLE/DIMENSIONS/TEXT layers, mm units.
- v1 limits (honest): wireframe only — no hidden-line removal, no tangent
  silhouettes on curved sides. Overall extents are exact.

## Usage

```bash
python scripts/drafting.py --step part.step --out drawing.dxf \
  --part-name my-part --version v0.2 --holes 5.0,5.0
```

`check_drawing` (same module) verifies DXF extents cover the STEP bbox;
warnings only, never blocks. See `references/usage.md`.
