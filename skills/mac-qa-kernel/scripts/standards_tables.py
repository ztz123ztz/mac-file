"""Standards-anchored size tables for fastening furniture.

VENDORED (verbatim) from earthtojake/text-to-cad
``packages/cadgen/src/cadgen/standards.py`` @ cce04de6 (MIT, original
copyright retained by that project). Do NOT edit the tables here — fixes go
upstream; this copy is refreshed wholesale. MAC-local additions (e.g. ISO
4032 nuts) live in clearly-marked sections at the end of this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# ---------------------------------------------------------------------------
# ISO 273: clearance holes for bolts and screws. "normal" (medium) series,
# through-hole nominal diameters in mm.
# ---------------------------------------------------------------------------
ISO273_CLEARANCE_MM: dict[int, float] = {
    1.6: 1.8,
    2: 2.4,
    2.5: 2.9,
    3: 3.4,
    4: 4.5,
    5: 5.5,
    6: 6.6,
    8: 9.0,
    10: 11.0,
    12: 13.5,
    16: 17.5,
    20: 22.0,
    24: 26.0,
}


@dataclass(frozen=True)
class Counterbore:
    """Counterbore furniture for a socket-head cap screw (ISO 4762)."""

    thread_mm: float
    counterbore_dia_mm: float
    counterbore_depth_mm: float
    through_dia_mm: float
    standard: str = "ISO 4762"


# ISO 4762 (hexagon socket head cap screws): counterbore dia + head-height
# depth, plus the ISO 273 normal clearance hole they seat in.
ISO4762_COUNTERBORE: dict[int, Counterbore] = {
    3: Counterbore(3, 6.0, 3.1, 3.4),
    4: Counterbore(4, 8.0, 4.1, 4.5),
    5: Counterbore(5, 10.0, 5.1, 5.5),
    6: Counterbore(6, 12.0, 6.1, 6.6),
    8: Counterbore(8, 16.0, 8.2, 9.0),
    10: Counterbore(10, 20.0, 10.2, 11.0),
    12: Counterbore(12, 24.0, 12.2, 13.5),
    16: Counterbore(16, 33.0, 16.2, 17.5),
    20: Counterbore(20, 40.0, 20.3, 22.0),
}


@dataclass(frozen=True)
class Countersink:
    """Countersink furniture for a flat-head screw (ISO 10642 family)."""

    thread_mm: float
    head_dia_mm: float
    included_angle_deg: float
    standard: str = "ISO 10642"


# ISO 10642 (hex socket countersunk head screws): head (countersink) diameter,
# 90 degree included angle.
ISO10642_COUNTERSINK: dict[int, Countersink] = {
    3: Countersink(3, 6.72, 90.0),
    4: Countersink(4, 8.96, 90.0),
    5: Countersink(5, 11.2, 90.0),
    6: Countersink(6, 13.44, 90.0),
    8: Countersink(8, 17.92, 90.0),
    10: Countersink(10, 22.4, 90.0),
    12: Countersink(12, 26.88, 90.0),
}


def _lookup(table: Mapping[float, object], nominal: float) -> object | None:
    """Return a copy (never a live reference) or None for a nominal in mm."""
    row = table.get(nominal)
    return row.__class__(**row.__dict__) if row is not None else None


def clearance_hole_mm(thread_mm: float) -> float | None:
    """Nominal ISO 273 clearance-through-hole diameter for a metric thread."""
    return ISO273_CLEARANCE_MM.get(thread_mm)


def counterbore_for(thread_mm: float) -> Counterbore | None:
    """ISO 4762 socket-head counterbore furniture, or None if unknown."""
    return _lookup(ISO4762_COUNTERBORE, thread_mm)


def countersink_for(thread_mm: float) -> Countersink | None:
    """ISO 10642 countersink furniture, or None if unknown."""
    return _lookup(ISO10642_COUNTERSINK, thread_mm)


# ---------------------------------------------------------------------------
# Measurement: pull cylindrical fastening features out of build123d shapes.
# build123d is imported lazily here so `cadgen.standards` stays importable in
# pure-data contexts; only the measurement functions need a CAD shape.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CylindricalFace:
    """A cylindrical face as seen by measurement: diameter + position."""

    radius_mm: float
    diameter_mm: float
    direction: tuple[float, float, float]
    position: tuple[float, float, float]
    axial_length_mm: float | None
    is_internal: bool


@dataclass(frozen=True)
class HoleFeature:
    """A single through-bore (straight cylindrical hole)."""

    diameter_mm: float
    direction: tuple[float, float, float]
    position: tuple[float, float, float]


@dataclass(frozen=True)
class CounterboreFeature:
    """A coaxial bore+recess pair interpreted as an ISO 4762 counterbore."""

    through_diameter_mm: float
    counterbore_diameter_mm: float
    counterbore_depth_mm: float | None
    direction: tuple[float, float, float]
    position: tuple[float, float, float]


def _unit_vector(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _same_axis(
    d0: tuple[float, float, float],
    p0: tuple[float, float, float],
    d1: tuple[float, float, float],
    p1: tuple[float, float, float],
    pos_tol: float = 0.05,
    dir_tol: float = 1e-3,
) -> bool:
    """Are two cylinders coaxial (same axis line, within tolerance)?"""
    cross = (
        d0[1] * d1[2] - d0[2] * d1[1],
        d0[2] * d1[0] - d0[0] * d1[2],
        d0[0] * d1[1] - d0[1] * d1[0],
    )
    if (cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2) ** 0.5 > dir_tol:
        return False
    # Distance from p1 to the line through p0 along d0 must be ~0.
    delta = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    proj = _dot(delta, d0)
    perp = (
        delta[0] - proj * d0[0],
        delta[1] - proj * d0[1],
        delta[2] - proj * d0[2],
    )
    return (_dot(perp, perp)) ** 0.5 <= pos_tol


def measure_fastening_cylinders(shapes) -> dict[str, object]:
    """Extract straight holes and counterbores from solid shapes.

    ``shapes`` is an iterable of part shapes (raw OCCT ``TopoDS_Shape`` as
    produced by STEP scene loading, or build123d ``Shape`` objects here
    unwrapped). Returns:

      {
        "holes": [HoleFeature, ...],
        "counterbores": [CounterboreFeature, ...],
      }

    Coaxial cylindrical faces with matching radii are collapsed so a single
    through-bore is one feature. A pair of coaxial cylinders with two distinct
    radii on the same axis line is interpreted as a counterbore (smaller =
    through bore, larger = recess). Returns only what is measurable; faces that
    cannot be read are skipped rather than crashing the whole inspection.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    def _unwrap(shape: object) -> object:
        wrapped = getattr(shape, "wrapped", None)
        return wrapped if wrapped is not None else shape

    def _face_cylinder(f: object) -> CylindricalFace | None:
        try:
            adaptor = BRepAdaptor_Surface(f)
            if adaptor.GetType() != GeomAbs_Cylinder:
                return None
            geom = adaptor.Cylinder()
            direction = _unit_vector(tuple(geom.Axis().Direction().Coord()))
            position = tuple(geom.Axis().Location().Coord())
            radius = float(geom.Radius())
            # For a cylinder the V parameter is the axial coordinate, so the
            # face's axial length is its V-parameter span.
            axis_len = round(abs(float(adaptor.LastVParameter()) - float(adaptor.FirstVParameter())), 6)
            return CylindricalFace(
                radius_mm=radius,
                diameter_mm=round(2.0 * radius, 6),
                direction=tuple(round(v, 6) for v in direction),
                position=tuple(round(v, 6) for v in position),
                axial_length_mm=axis_len,
                is_internal=f.Orientation() == TopAbs_Orientation.TopAbs_REVERSED,
            )
        except Exception:  # noqa: BLE001 - unreadable face is skipped
            return None

    faces: list[CylindricalFace] = []
    for idx, shape in enumerate(shapes):
        raw = _unwrap(shape)
        try:
            explorer = TopExp_Explorer(raw, TopAbs_ShapeEnum.TopAbs_FACE)
        except Exception:  # noqa: BLE001
            continue
        while explorer.More():
            face = TopoDS.Face_s(explorer.Current())
            found = _face_cylinder(face)
            if found is not None and found.is_internal:
                faces.append(found)
            explorer.Next()

    # Group cylinders by axis line.
    groups: list[list[CylindricalFace]] = []
    for face in faces:
        placed = False
        for group in groups:
            ref = group[0]
            if _same_axis(ref.direction, ref.position, face.direction, face.position):
                group.append(face)
                placed = True
                break
        if not placed:
            groups.append([face])

    holes: list[HoleFeature] = []
    counterbores: list[CounterboreFeature] = []
    for group in groups:
        radii = sorted({round(f.radius_mm, 4) for f in group})
        if len(radii) == 1:
            ref = group[0]
            holes.append(
                HoleFeature(
                    diameter_mm=round(ref.diameter_mm, 4),
                    direction=ref.direction,
                    position=ref.position,
                )
            )
        else:
            # Counterbore: smallest radius is the through bore; the largest
            # radius is the recess. Depth = axial length of the recess face.
            through = min(group, key=lambda f: f.radius_mm)
            recess = max(group, key=lambda f: f.radius_mm)
            counterbores.append(
                CounterboreFeature(
                    through_diameter_mm=round(through.diameter_mm, 4),
                    counterbore_diameter_mm=round(recess.diameter_mm, 4),
                    counterbore_depth_mm=recess.axial_length_mm,
                    direction=through.direction,
                    position=through.position,
                )
            )

    return {"holes": holes, "counterbores": counterbores}


# ---------------------------------------------------------------------------
# MAC-LOCAL ADDITIONS (not in upstream cadgen standards.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HexNut:
    """Hex nut, style 1 (ISO 4032), product grades A/B.

    s = width across flats (nominal = max), m = nut height (max).
    Values: ISO 4032:2023 Table 1 + BS EN ISO 4032 preferred sizes.
    """

    thread_mm: float
    pitch_mm: float
    across_flats_mm: float
    height_mm: float
    standard: str = "ISO 4032"


ISO4032_HEX_NUT: dict[float, HexNut] = {
    2.0: HexNut(2.0, 0.4, 4.0, 1.6),
    2.5: HexNut(2.5, 0.45, 5.0, 2.0),
    3.0: HexNut(3.0, 0.5, 5.5, 2.4),
    4.0: HexNut(4.0, 0.7, 7.0, 3.2),
    5.0: HexNut(5.0, 0.8, 8.0, 4.7),
    6.0: HexNut(6.0, 1.0, 10.0, 5.2),
    8.0: HexNut(8.0, 1.25, 13.0, 6.8),
    10.0: HexNut(10.0, 1.5, 16.0, 8.4),
    12.0: HexNut(12.0, 1.75, 18.0, 10.8),
}


def hex_nut_for(thread_mm: float) -> HexNut | None:
    """Return a copy of the ISO 4032 nut for a thread nominal, else None."""
    import copy

    nut = _lookup(ISO4032_HEX_NUT, thread_mm)
    return copy.deepcopy(nut) if nut is not None else None
