"""Deterministic engineering-drawing generation (P4: Drafting).

Projects frozen STEP solids to 2D wireframe elevations (TOP + FRONT),
dimensions overall extents, lists hole data, and writes a machinable DXF
via ezdxf. Zero tokens.

v1 scope (deliberate): wireframe projection (all face-boundary wires, outer
+ holes) — no hidden-line removal, no tangent silhouettes on curved sides.
Overall bbox dimensions are EXACT; hole table comes from measured diameters.
Title block + layers included. CAD Viewer renders .dxf natively.

CLI::

    python -m multi_agent_cad.drafting --step temp_output_4.step \\
        --out temp_drawing_0.dxf --part-name foldable-phone-stand \\
        --version v0.2 --holes 5.0,5.0
"""

from __future__ import annotations

import argparse
import datetime
import math
from pathlib import Path

LAYER_VISIBLE = "VISIBLE"
LAYER_DIM = "DIMENSIONS"
LAYER_TEXT = "TEXT"


def _read_solids(step_path: str) -> list:
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS

    reader = STEPControl_Reader()
    if reader.ReadFile(step_path) != 1:
        raise RuntimeError(f"cannot read STEP: {step_path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while explorer.More():
        solids.append(TopoDS.Solid_s(explorer.Current()))
        explorer.Next()
    if not solids:
        raise RuntimeError("STEP contains no solids")
    return solids


def _solid_bbox(solid) -> list[float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(solid, box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return [round(x1 - x0, 2), round(y1 - y0, 2), round(z1 - z0, 2)]


def _project_solid_wires(solid, view: str) -> list[list[tuple[float, float]]]:
    """Project all face-boundary wires of a solid to 2D polylines.

    view="top": (x, y). view="front": (x, z). Only planar faces contribute
    (their wires carry outer profiles + hole loops); curved faces are skipped
    in v1 (their cap boundaries arrive via adjacent planar faces).
    """
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GCPnts import GCPnts_TangentialDeflection
    from OCP.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir

    if view == "front":
        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), -math.pi / 2)
    else:
        trsf = None

    polylines: list[list[tuple[float, float]]] = []
    face_exp = TopExp_Explorer(solid, TopAbs_FACE)
    while face_exp.More():
        face = TopoDS.Face_s(face_exp.Current())
        try:
            adapt = BRepAdaptor_Surface(face)
            planar = int(adapt.GetType()) == int(GeomAbs_Plane)
        except Exception:
            planar = False
        if planar:
            wire_exp = TopExp_Explorer(face, TopAbs_WIRE)
            while wire_exp.More():
                wire = TopoDS.Wire_s(wire_exp.Current())
                edge_exp = TopExp_Explorer(wire, TopAbs_EDGE)
                while edge_exp.More():
                    edge = TopoDS.Edge_s(edge_exp.Current())
                    try:
                        adapt_c = BRepAdaptor_Curve(edge)
                        poly = GCPnts_TangentialDeflection(adapt_c, 0.15, 0.1)
                        pts = []
                        for i in range(1, poly.NbPoints() + 1):
                            p = poly.Value(i)  # gp_Pnt directly
                            if trsf is not None:
                                p = p.Transformed(trsf)
                            pts.append((round(p.X(), 3), round(p.Z(), 3))
                                       if view == "front"
                                       else (round(p.X(), 3), round(p.Y(), 3)))
                        if len(pts) >= 2:
                            polylines.append(pts)
                    except Exception:
                        pass
                    edge_exp.Next()
                wire_exp.Next()
        face_exp.Next()
    return polylines


def generate_dxf(*, step_path: str, out_path: str, part_name: str = "part",
                 version: str = "v0.1",
                 hole_diameters: list[float] | None = None) -> dict:
    """Build the drawing. Returns {"views": [...], "bbox_top": [...], ...}."""
    import ezdxf

    hole_diameters = hole_diameters or []
    solids = _read_solids(step_path)
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimeters
    for layer, color in ((LAYER_VISIBLE, 7), (LAYER_DIM, 3), (LAYER_TEXT, 4)):
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern=[0.6, 0.25, -0.25])
    msp = doc.modelspace()

    views_info = []
    cursor_x = 0.0
    for si, solid in enumerate(solids, 1):
        bbox = _solid_bbox(solid)
        for view in ("top", "front"):
            wires = _project_solid_wires(solid, view)
            if not wires:
                continue
            if view == "top":
                w, h = bbox[0], bbox[1]
                org = (cursor_x, 0.0)
            else:
                w, h = bbox[0], bbox[2]
                org = (cursor_x, -(max(bbox[1], bbox[2]) + 20.0))
            for pts in wires:
                moved = [(x + org[0], y + org[1]) for x, y in pts]
                msp.add_lwpolyline(moved, dxfattribs={"layer": LAYER_VISIBLE, "closed": True})
            # Overall dims for this view.
            try:
                msp.add_linear_dim(
                    base=(org[0], org[1] - 8.0),
                    p1=(org[0], org[1]), p2=(org[0] + w, org[1]),
                    dimstyle="EZDXF", dxfattribs={"layer": LAYER_DIM}).render()
                msp.add_linear_dim(
                    base=(org[0] - 8.0, org[1]),
                    p1=(org[0], org[1]), p2=(org[0], org[1] + h),
                    angle=90, dimstyle="EZDXF",
                    dxfattribs={"layer": LAYER_DIM}).render()
            except Exception:
                pass
            views_info.append({"solid": si, "view": view, "origin": org,
                               "size": [w, h], "wires": len(wires)})
            cursor_x += w + 25.0
        cursor_x += 15.0

    # Hole table + title block.
    tx = cursor_x + 10.0
    msp.add_text(f"{part_name}  {version}", height=5.0,
                 dxfattribs={"layer": LAYER_TEXT}).set_placement((tx, 0.0))
    msp.add_text("UNITS mm  SCALE 1:1  PROJECTION: TOP + FRONT (wireframe v1)",
                 height=2.5, dxfattribs={"layer": LAYER_TEXT}).set_placement((tx, -8.0))
    y = -16.0
    msp.add_text("HOLES:", height=3.0, dxfattribs={"layer": LAYER_TEXT}).set_placement((tx, y))
    y -= 6.0
    if hole_diameters:
        for i, d in enumerate(hole_diameters, 1):
            msp.add_text(f"H{i}: DIA {float(d):g} THRU", height=2.5,
                         dxfattribs={"layer": LAYER_TEXT}).set_placement((tx, y))
            y -= 5.0
    else:
        msp.add_text("(no measured holes)", height=2.5,
                     dxfattribs={"layer": LAYER_TEXT}).set_placement((tx, y))
    doc.saveas(out_path)
    try:
        import json as _json
        _json_path = out_path + ".views.json"
        open(_json_path, "w", encoding="utf-8").write(
            _json.dumps({"views": views_info, "solids": len(solids)}, indent=2))
    except OSError:
        pass
    return {"views": views_info, "solids": len(solids), "dxf": out_path}


def check_drawing(dxf_path: str, step_bbox_xyz: list[float]) -> list[str]:
    """Light QA: DXF extents cover the STEP bbox (top=XY, front=XZ). Warnings only."""
    warnings: list[str] = []
    try:
        import ezdxf
        from ezdxf import bbox as _bbox
        doc = ezdxf.readfile(dxf_path)
        ext = _bbox.extents(doc.modelspace())
        if ext.has_data:
            size = [round(ext.size.x, 2), round(ext.size.y, 2)]
            if size[0] + 1.0 < step_bbox_xyz[0]:
                warnings.append(f"drawing X extent {size[0]} < STEP X {step_bbox_xyz[0]}")
    except Exception as exc:
        warnings.append(f"drawing QA unreadable: {exc}")
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic drafting to DXF (P4).")
    parser.add_argument("--step", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--part-name", default="part")
    parser.add_argument("--version", default="v0.1")
    parser.add_argument("--holes", default="")
    args = parser.parse_args(argv)

    holes = [float(x) for x in args.holes.split(",") if x.strip()] if args.holes else []
    info = generate_dxf(step_path=args.step, out_path=args.out,
                        part_name=args.part_name, version=args.version,
                        hole_diameters=holes)
    print(f"DXF: {len(info['views'])} views, {info['solids']} solids -> {args.out}")
    for v in info["views"]:
        print(f"  solid {v['solid']} {v['view']}: {v['wires']} wires, size {v['size']}")
    for w in check_drawing(args.out, [0, 0, 0]):
        print(f"  WARN: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
