"""Deterministic BOM generation + validation (P3).

- Fabricated lines: one per top-level solid in the final STEP (volume via
  OCP, weight via --density, refs ``#o<i>`` occurrence-style).
- Purchased lines: measurement features whose label matches fastener keywords
  (screw|bolt|nut|washer|pin|bearing|standoff|rivet). Spec-PartNo comes from
  the ISO tables when a metric designation is present, else ``miss`` with a
  substitute note — never a silent swap.
- 8 LOCKED columns (order immutable) + version/weight/notes footer.
- Qty comes ONLY from geometry counts (measured features / solids).
- :func:`cross_check_purchased` enforces the cad-eval rule: a purchased ISO
  line needs at least Qty matching holes (sufficient, not exact).

CLI::

    python -m multi_agent_cad.bom --step temp_output_4.step \\
        --measurements temp_measurements_4.json --out BOM.csv \\
        --part-name foldable-phone-stand --material "Acrylic 4mm" \\
        --density 1.18 --version v0.2
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

COLUMNS = ["Item", "Qty", "Unit", "Part", "Type", "Material", "Spec-PartNo", "Refs"]

FASTENER_KEYWORDS = ("screw", "bolt", "nut", "washer", "pin", "bearing",
                     "standoff", "rivet", "insert")

_ISO_DESIGNATION = re.compile(r"\bM(\d+(?:\.\d+)?)\b")


def _read_solids(step_path: str) -> list[dict]:
    """Read top-level solids from a STEP file: volume + bbox (OCP)."""
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    reader = STEPControl_Reader()
    if reader.ReadFile(step_path) != 1:
        raise RuntimeError(f"cannot read STEP: {step_path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while explorer.More():
        solid = TopoDS.Solid_s(explorer.Current())
        props = GProp_GProps()
        volume = 0.0
        try:
            BRepGProp.VolumeProperties_s(solid, props)
            volume = float(props.Mass())
        except Exception:
            pass
        box = Bnd_Box()
        try:
            BRepBndLib.Add_s(solid, box)
            x0, y0, z0, x1, y1, z1 = box.Get()
            bbox = [round(x1 - x0, 2), round(y1 - y0, 2), round(z1 - z0, 2)]
        except Exception:
            bbox = [0.0, 0.0, 0.0]
        solids.append({"volume_mm3": round(volume, 2), "bbox_mm": bbox})
        explorer.Next()
    return solids


def _pretty_part_name(key: str) -> str:
    name = re.sub(r"^step-\d+-", "", key)
    name = re.sub(r"^(extrude|cut|hole|fillet|chamfer)-", "", name)
    return name.replace("-", " ").replace("_", " ").strip() or key


def generate_bom(*, step_path: str, measurements: dict | None = None,
                 part_name: str = "part", material: str = "unspecified",
                 density_g_cm3: float | None = None,
                 version: str = "v0.1") -> tuple[list[dict], list[str]]:
    """Build BOM rows + footer notes. Returns (rows, footer_lines)."""
    solids = _read_solids(step_path)
    if not solids:
        raise RuntimeError("STEP contains no solids")
    measurements = measurements or {}

    rows: list[dict] = []
    notes: list[str] = []
    item = 0

    # -- Fabricated: one line per solid -------------------------------------
    for i, solid in enumerate(solids, 1):
        item += 1
        # Best-effort label: largest extrude feature is usually the body.
        label = f"body-{i}"
        refs = f"#o{i}"
        rows.append({
            "Item": item, "Qty": 1, "Unit": "ea",
            "Part": label, "Type": "fabricated",
            "Material": material, "Spec-PartNo": f"{part_name}-{i:02d}",
            "Refs": refs,
        })

    # -- Purchased: fastener-keyword features --------------------------------
    for key in measurements:
        low = key.lower()
        if not any(k in low for k in FASTENER_KEYWORDS):
            continue
        item += 1
        match = _ISO_DESIGNATION.search(key.upper())
        if match:
            nominal = float(match.group(1))
            spec_no = f"ISO 4762 M{match.group(1)} (verify head/length)"
        else:
            nominal = None
            spec_no = "miss"
            notes.append(
                f"Item {item}: no catalog match for '{_pretty_part_name(key)}' — "
                f"substitute: custom turned part per Refs geometry (flagged, not swapped).")
        rows.append({
            "Item": item, "Qty": 1, "Unit": "ea",
            "Part": _pretty_part_name(key), "Type": "purchased",
            "Material": "unspecified",
            "Spec-PartNo": spec_no if nominal is not None else "miss",
            "Refs": key,
        })

    # -- Footer ---------------------------------------------------------------
    total_volume = sum(s["volume_mm3"] for s in solids)
    footer = [f"# Part-Version: {part_name}-{version}"]
    if density_g_cm3:
        weight_g = total_volume / 1000.0 * density_g_cm3
        footer.append(f"# Weight: total {weight_g:.1f} g "
                      f"(volume {total_volume:.0f} mm3 @ {density_g_cm3} g/cm3)")
    footer.extend(f"# Notes: {n}" for n in notes)
    return rows, footer


def write_bom_csv(rows: list[dict], footer: list[str], out_path: str) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in COLUMNS})
    text = buf.getvalue()
    if footer:
        text += "".join(line if line.endswith("\n") else line + "\n" for line in footer)
    Path(out_path).write_text(text, encoding="utf-8")


def validate_bom_csv(path: str) -> list[str]:
    """Enforce the locked schema. Returns a list of violations (empty = clean).

    Rules: (1) header order immutable; (2) Spec-PartNo non-empty;
    (3) Qty numeric; (4) version footer present.
    """
    errors: list[str] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"cannot read BOM: {exc}"]
    if not lines:
        return ["BOM file is empty"]
    header = next(csv.reader([lines[0]]))
    if header != COLUMNS:
        return [f"header must be exactly {COLUMNS}, got {header}"]
    body = [ln for ln in lines[1:] if ln and not ln.startswith("#")]
    reader = csv.DictReader(body, fieldnames=COLUMNS)
    for n, row in enumerate(reader, 2):
        try:
            qty = float(row["Qty"])
        except (TypeError, ValueError):
            errors.append(f"line {n}: Qty not numeric: {row.get('Qty')!r}")
            continue
        if qty < 0:
            errors.append(f"line {n}: Qty negative")
        if not (row.get("Spec-PartNo") or "").strip():
            errors.append(f"line {n}: Spec-PartNo empty (drawing no. or standard required)")
    footers = [ln for ln in lines if ln.startswith("#")]
    if not any("Part-Version:" in ln for ln in footers):
        errors.append("missing version footer (# Part-Version: ...)")
    return errors


def cross_check_purchased(rows: list[dict], hole_diameters: list[float],
                          tolerance_mm: float = 0.3) -> list[dict]:
    """BOM↔geometry cross-check (cad-eval sufficient-not-exact rule).

    For purchased rows carrying an ISO 273/4762/4032 ``M<n>`` designation,
    require at least Qty matching holes. Rows without a designation are
    reported as skipped (unjudged), never failed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from standards_tables import (ISO273_CLEARANCE_MM, ISO4032_HEX_NUT,
                                      ISO4762_COUNTERBORE)
    except Exception:
        ISO273_CLEARANCE_MM, ISO4032_HEX_NUT, ISO4762_COUNTERBORE = {}, {}, {}
    checks: list[dict] = []
    for row in rows:
        if row.get("Type") != "purchased":
            continue
        match = _ISO_DESIGNATION.search(str(row.get("Spec-PartNo", "")))
        if not match:
            checks.append({"item": row.get("Item"), "check": "bom_iso",
                           "passed": None,
                           "detail": "no ISO designation — skipped (unjudged)"})
            continue
        nominal = float(match.group(1))
        targets = [v for v in (ISO273_CLEARANCE_MM.get(nominal),
                               getattr(ISO4762_COUNTERBORE.get(nominal), "through_dia_mm", None))
                   if v is not None]
        hits = sum(1 for d in hole_diameters
                   for t in targets if abs(float(d) - t) <= tolerance_mm)
        need = int(float(row.get("Qty", 0)))
        checks.append({"item": row.get("Item"), "check": f"bom_iso_M{match.group(1)}",
                       "passed": hits >= need,
                       "detail": f"{hits} matching hole(s), need {need}"})
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic BOM generation (P3).")
    parser.add_argument("--step", required=True)
    parser.add_argument("--measurements", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--part-name", default="part")
    parser.add_argument("--material", default="unspecified")
    parser.add_argument("--density", type=float, default=None)
    parser.add_argument("--version", default="v0.1")
    parser.add_argument("--holes", default="",
                        help="comma-separated measured through-hole diameters for cross-check")
    args = parser.parse_args(argv)

    measurements = {}
    if args.measurements:
        measurements = json.loads(Path(args.measurements).read_text(encoding="utf-8"))
    rows, footer = generate_bom(
        step_path=args.step, measurements=measurements,
        part_name=args.part_name, material=args.material,
        density_g_cm3=args.density, version=args.version)
    write_bom_csv(rows, footer, args.out)
    violations = validate_bom_csv(args.out)
    holes = [float(x) for x in args.holes.split(",") if x.strip()] if args.holes else []
    checks = cross_check_purchased(rows, holes)
    print(f"BOM: {len(rows)} rows -> {args.out}")
    for c in checks:
        print(f"  item {c['item']}: {c['check']} passed={c['passed']} ({c['detail']})")
    if violations:
        print("VIOLATIONS:")
        for v in violations:
            print(f"  - {v}")
        return 2
    print("schema: clean (8 columns locked, version footer present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
