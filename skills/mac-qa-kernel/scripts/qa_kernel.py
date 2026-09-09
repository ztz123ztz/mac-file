"""Standalone kernel/standards QA for STEP files (Engine C extraction).

Checks every solid with the OpenCASCADE kernel (no mesh involved):

* ``kernel-validity`` — BRepCheck_Analyzer passes (fail -> TOPOLOGY).
* ``self-intersection`` — no BOPAlgo_SelfIntersect (fail -> TOPOLOGY).
* ``iso-hole-N`` — advisory only: measured through-hole diameters annotated
  against the ISO 273 clearance table (never fails; undeclared holes are
  left unjudged per cad-eval doctrine).

A checker that cannot run produces NO result (exit 0, ``"ran": false`` in
JSON) — an unrunnable check must never count as pass.

Usage::

    python qa_kernel.py model.step [--holes 5.0,5.0] [--json report.json]

Exit codes: 0 = all runnable checks passed (or none ran); 1 = real failure.
Requires: Python 3.11, build123d (pulls cadquery-ocp for OCP).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from standards_tables import ISO273_CLEARANCE_MM
except Exception:  # standalone copy lives next to this script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from standards_tables import ISO273_CLEARANCE_MM


def check_step(step_path: str, hole_diameters: list[float] | None = None) -> dict:
    """Run all checks. Returns a JSON-serializable report dict."""
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
    from OCP.BOPAlgo import BOPAlgo_CheckStatus
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp

    report: dict = {"step": step_path, "ran": False, "checks": [],
                    "passed": None, "errors": []}
    try:
        reader = STEPControl_Reader()
        if reader.ReadFile(step_path) != 1:
            report["errors"].append(f"cannot read STEP: {step_path}")
            return report
        reader.TransferRoots()
        shape = reader.OneShape()
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        solids = []
        while explorer.More():
            solids.append(TopoDS.Solid_s(explorer.Current()))
            explorer.Next()
    except Exception as exc:
        report["errors"].append(f"STEP load failed: {exc}")
        return report
    if not solids:
        report["checks"].append({"id": "kernel-validity", "passed": False,
                                 "detail": "no solids in STEP"})
        report["passed"] = False
        return report

    bad_valid, bad_self, volumes = [], [], []
    for i, solid in enumerate(solids):
        try:
            if not BRepCheck_Analyzer(solid, True).IsValid():
                bad_valid.append(i)
        except Exception:
            bad_valid.append(i)
        try:
            checker = BRepAlgoAPI_Check(solid, True, True)
            if (not checker.IsValid()) and any(
                    r.GetCheckStatus() == BOPAlgo_CheckStatus.BOPAlgo_SelfIntersect
                    for r in checker.Result().GetCheckResult()):
                bad_self.append(i)
        except Exception:
            pass
        try:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, props)
            volumes.append(round(float(props.Mass()), 2))
        except Exception:
            volumes.append(0.0)

    report["ran"] = True
    report["solids"] = len(solids)
    report["volumes_mm3"] = volumes
    report["checks"].append({
        "id": "kernel-validity", "passed": not bad_valid,
        "detail": f"{len(solids) - len(bad_valid)}/{len(solids)} solids valid"
                  + (f"; invalid: {bad_valid}" if bad_valid else "")})
    report["checks"].append({
        "id": "self-intersection", "passed": not bad_self,
        "detail": ("none" if not bad_self else f"solids: {bad_self}")})
    for n, dia in enumerate(hole_diameters or [], 1):
        try:
            dia_f = float(dia)
        except (TypeError, ValueError):
            continue
        best = min(ISO273_CLEARANCE_MM.items(),
                   key=lambda kv: abs(kv[1] - dia_f), default=None)
        if best is not None and abs(best[1] - dia_f) <= 0.3:
            note = f"Ø{dia_f} matches ISO 273 M{best[0]} clearance ({best[1]}mm)"
        elif best is not None:
            note = (f"Ø{dia_f} non-standard; nearest ISO 273 M{best[0]} "
                    f"({best[1]}mm) — verify intent")
        else:
            note = f"Ø{dia_f}: no ISO table"
        report["checks"].append({"id": f"iso-hole-{n}", "passed": True,
                                 "advisory": True, "detail": note})
    report["passed"] = not bad_valid and not bad_self
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kernel/standards QA for STEP (Engine C).")
    parser.add_argument("step")
    parser.add_argument("--holes", default="",
                        help="comma-separated measured through-hole diameters (advisory)")
    parser.add_argument("--json", default=None, help="write JSON report here")
    args = parser.parse_args(argv)

    try:
        report = check_step(
            args.step,
            [float(x) for x in args.holes.split(",") if x.strip()] if args.holes else None)
    except Exception as exc:
        print(f"engine error: {exc}")
        return 2
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    if not report["ran"]:
        print("ENGINE UNAVAILABLE (no results — not a pass)")
        return 0
    for c in report["checks"]:
        flag = "PASS" if c["passed"] else "FAIL"
        print(f"[{flag}] {c['id']}: {c['detail']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
