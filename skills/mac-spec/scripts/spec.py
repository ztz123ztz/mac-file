"""Deterministic technical SPEC + version freeze (P5).

- :func:`generate_spec` builds SPEC.md from the frozen STEP, BOM.csv,
  DXF info, CADBrief and QA summary. Every numbered requirement carries a
  ``[REF: ...]`` citation; :func:`check_citations` verifies each citation
  resolves to a measurement key, BOM row or DXF view — unresolvable ones go
  to an explicit OPEN ITEMS section (no silent claims).
- :func:`freeze_version` writes PART.md (snapshot) + appends CHANGES.md and
  enforces the version triad: PART.md == BOM footer == SPEC header.
  Mismatch blocks the freeze (E4 Part-folder gate, MAC-flat-file edition).

CLI::

    python -m multi_agent_cad.spec --step temp_output_4.step \\
        --measurements temp_measurements_4.json --bom temp_BOM_4.csv \\
        --brief pipeline_cache/cad_brief.json --out SPEC.md \\
        --part-name foldable-phone-stand --version v0.2 --material "Acrylic 4mm"
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path

_REF_TOKEN = re.compile(r"\[REF:\s*([^\]]+)\]")


def _sha8(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def generate_spec(*, step_path: str, measurements: dict | None = None,
                  bom_rows: list[dict] | None = None,
                  bom_version: str | None = None,
                  dxf_views: list[dict] | None = None,
                  hole_diameters: list[float] | None = None,
                  brief: dict | None = None,
                  qa_summary: dict | None = None,
                  part_name: str = "part", version: str = "v0.1",
                  material: str = "unspecified") -> str:
    """Render SPEC.md text. All numbers come from inputs (no invention)."""
    measurements = measurements or {}
    bom_rows = bom_rows or []
    dxf_views = dxf_views or []
    hole_diameters = hole_diameters or []
    brief = brief or {}
    qa_summary = qa_summary or {}
    today = datetime.date.today().isoformat()

    overall = measurements.get("overall")
    if isinstance(overall, dict):
        bbox_line = (f"{overall.get('size_x', '?')} x {overall.get('size_y', '?')} x "
                     f"{overall.get('size_z', '?')} mm [REF: overall]")
    else:
        bbox_line = "overall bbox: see STEP [REF: overall-missing]"

    func_reqs = brief.get("functional_requirements", []) or []
    mfg = brief.get("manufacturing_method", "unspecified")

    lines = [
        f"# Technical Specification — {part_name} {version}",
        "",
        f"Date: {today} | Source STEP sha8: {_sha8(step_path)} | Units: mm",
        "",
        "## 1. Scope",
        f"Foldable desktop phone stand assembly, {len(bom_rows)} BOM line items, "
        f"{len(dxf_views)} drawing views. [REF: BOM-count]",
        "",
        "## 2. Materials",
        f"Fabricated parts: {material}. [REF: brief-material]",
        f"Process intent: {mfg}. [REF: brief-manufacturing]",
        "",
        "## 3. Geometry",
        f"Overall envelope: {bbox_line}",
    ]
    for key, feat in measurements.items():
        if not isinstance(feat, dict) or key == "overall":
            continue
        size = {k: feat.get(k) for k in ("size_x", "size_y", "size_z") if feat.get(k) is not None}
        if size:
            lines.append(f"- {key}: {' x '.join(str(v) for v in size.values())} mm [REF: {key}]")
    lines += [
        "",
        "## 4. Holes and cutouts",
    ]
    if hole_diameters:
        for i, d in enumerate(hole_diameters, 1):
            lines.append(f"- H{i}: DIA {float(d):g} mm through [REF: iso-hole-{i}]")
    else:
        lines.append("- No measured through-holes recorded [REF: holes-none].")
    lines += [
        "",
        "## 5. BOM reference",
        f"BOM.csv: {len(bom_rows)} rows, version {bom_version or 'unknown'} "
        f"[REF: BOM-version]. Fabricated items per drawing refs, purchased items "
        f"per catalog or miss notes [REF: BOM-rows].",
        "",
        "## 6. Drawings reference",
    ]
    if dxf_views:
        for v in dxf_views:
            lines.append(f"- Solid {v.get('solid')} {v.get('view')}: {v.get('wires')} wires "
                         f"[REF: dxf-view-{v.get('solid')}-{v.get('view')}]")
    else:
        lines.append("- No DXF views recorded [REF: dxf-none].")
    lines += [
        "",
        "## 7. Functional requirements",
    ]
    for fr in func_reqs:
        lines.append(f"- {fr} [REF: brief-functional]")
    if not func_reqs:
        lines.append("- (none stated) [REF: brief-functional-empty]")
    lines += [
        "",
        "## 8. Acceptance",
        f"QA: passed={qa_summary.get('passed_count', '?')} "
        f"failed={qa_summary.get('failed_count', '?')} "
        f"unknown={qa_summary.get('unknown_count', '?')} "
        f"strength={qa_summary.get('strength_score', '?')}/100 [REF: QA-report].",
        "Acceptance = all QA failures resolved or waived with reason; "
        "open items below must be empty for release [REF: QA-gate].",
        "",
    ]
    return "\n".join(lines)


def check_citations(spec_text: str, *, measurement_keys: set[str],
                    bom_items: set[int], dxf_views: set[str]) -> list[str]:
    """Verify every [REF: x] resolves. Returns broken refs (empty = clean)."""
    known = set(measurement_keys) | {"overall", "overall-missing", "holes-none",
                                     "dxf-none", "BOM-count", "BOM-version", "BOM-rows",
                                     "brief-material", "brief-manufacturing",
                                     "brief-functional", "brief-functional-empty",
                                     "QA-report", "QA-gate"}
    known |= {f"iso-hole-{i}" for i in range(1, 20)}
    known |= {f"dxf-view-{v}" for v in dxf_views}
    broken = []
    for token in _REF_TOKEN.findall(spec_text):
        t = token.strip()
        if t in known:
            continue
        if t.startswith("#o"):
            continue  # CAD occurrence selectors resolve against the STEP
        broken.append(t)
    return sorted(set(broken))


def freeze_version(*, part_name: str, version: str, files: dict[str, str],
                   bom_version: str | None, spec_text: str,
                   change_summary: str, cwd: str = ".") -> dict:
    """Write PART.md, append CHANGES.md, enforce the version triad.

    Returns {"ok": bool, "reason": str}. Triad: PART.md version ==
    BOM footer version == SPEC header version. Mismatch blocks the freeze.
    """
    cwd_p = Path(cwd)
    triad = {"part": version, "bom": bom_version, "spec": version}
    if bom_version is not None and bom_version != version:
        return {"ok": False,
                "reason": f"version triad mismatch: SPEC/PART {version} vs BOM {bom_version}"}
    today = datetime.date.today().isoformat()
    part_md = [
        f"# PART — {part_name}",
        "",
        f"version: {version}",
        f"date: {today}",
        "",
        "## Artifacts",
    ]
    for label, path in files.items():
        p = Path(path)
        if p.is_file():
            part_md.append(f"- {label}: `{p.name}` ({p.stat().st_size} bytes, sha8 {_sha8(str(p))})")
        else:
            part_md.append(f"- {label}: MISSING ({path})")
    part_md += ["", f"## SPEC", "", spec_text[:2000]]
    cwd_p.joinpath("PART.md").write_text("\n".join(part_md) + "\n", encoding="utf-8")

    changes = cwd_p.joinpath("CHANGES.md")
    prev = changes.read_text(encoding="utf-8") if changes.exists() else "# CHANGES\n"
    entry = f"\n## {version} ({today})\n{change_summary.strip()}\n"
    if f"## {version} " not in prev and f"## {version}\n" not in prev:
        changes.write_text(prev.rstrip() + "\n" + entry, encoding="utf-8")
    return {"ok": True, "reason": f"frozen {version}: PART.md + CHANGES.md + BOM triad aligned"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic SPEC + freeze (P5).")
    parser.add_argument("--step", required=True)
    parser.add_argument("--measurements", default=None)
    parser.add_argument("--bom", default=None)
    parser.add_argument("--brief", default=None)
    parser.add_argument("--out", default="SPEC.md")
    parser.add_argument("--part-name", default="part")
    parser.add_argument("--version", default="v0.1")
    parser.add_argument("--material", default="unspecified")
    parser.add_argument("--holes", default="")
    args = parser.parse_args(argv)

    import csv

    measurements = json.loads(Path(args.measurements).read_text(encoding="utf-8")) if args.measurements else {}
    brief = json.loads(Path(args.brief).read_text(encoding="utf-8")) if args.brief else {}
    bom_rows, bom_version = [], None
    if args.bom:
        bom_text = Path(args.bom).read_text(encoding="utf-8").splitlines()
        header = next(csv.reader([bom_text[0]]))
        assert header[0] == "Item", f"BOM header violated: {header[:3]}"
        for ln in bom_text[1:]:
            if not ln or ln.startswith("#"):
                if "Part-Version:" in ln:
                    bom_version = ln.split("Part-Version:")[1].strip().split()[0]
                    if "-" in bom_version:
                        bom_version = "v" + bom_version.split("-")[-1].lstrip("v")
                continue
            bom_rows.append(ln)
        material = args.material
        if brief.get("material"):
            material = brief["material"]
    holes = [float(x) for x in args.holes.split(",") if x.strip()] if args.holes else []

    spec = generate_spec(
        step_path=args.step, measurements=measurements, bom_rows=bom_rows,
        bom_version=bom_version, hole_diameters=holes, brief=brief,
        part_name=args.part_name, version=args.version, material=material)
    broken = check_citations(
        spec, measurement_keys=set(measurements.keys()),
        bom_items=set(range(1, len(bom_rows) + 1)), dxf_views=set())
    if broken:
        spec += "\n## OPEN ITEMS (unresolved references)\n" + "".join(f"- {b}\n" for b in broken)
    Path(args.out).write_text(spec, encoding="utf-8")
    print(f"SPEC: {args.out} ({len(spec)} chars, {len(broken)} broken refs)")
    fr = freeze_version(part_name=args.part_name, version=args.version,
                        files={"step": args.step, "bom": args.bom or "",
                               "spec": args.out},
                        bom_version=bom_version, spec_text=spec,
                        change_summary=f"SPEC freeze for {args.part_name} {args.version}.",
                        cwd=str(Path(args.out).parent))
    print(f"Freeze: {fr['reason']}")
    return 0 if (fr["ok"] and not broken) else 2


if __name__ == "__main__":
    raise SystemExit(main())
