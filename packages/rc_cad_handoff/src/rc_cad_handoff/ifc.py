"""IFC4 export for a parsed handoff.

── What this is for, and what it is not ─────────────────────────────

A DISPOSABLE artifact for review and hand-off inspection, not a BIM deliverable. It exists so a
reviewer can open the assembly in an IFC tool, select an individual bar, and read back the family
and the stable id the producer assigned. Nothing here derives an engineering result: every number
is the document's, and the semantics come from the producer's families rather than from anything
this module infers.

── Why the bars are IfcReinforcingBar ───────────────────────────────

Because that is what they are, and because the alternative loses the only thing worth exporting.
An `IfcBuildingElementProxy` would carry the geometry and drop the meaning: a consumer could not
tell a bottom-mat bar from a column starter, which is precisely the distinction V2 was created to
make. `PredefinedType` carries it further — MAIN for the longitudinal steel that resists the
action, LIGATURE for the ties and crossties that confine it.

── Geometry is a swept axis, not a mesh ─────────────────────────────

Each bar is written as a polyline sweep of its own centreline at its own diameter. That keeps the
bar a BAR in the IFC model — selectable, measurable, with a radius a tool can report — instead of
a triangle soup that happens to look like one. Arcs are emitted as polyline segments here, which
is a documented approximation of THIS artifact and not of the STEP: the STEP carries the exact
arcs, and `cad-review.json` records that it does. An IFC viewer showing a faceted bend is showing
this file's approximation, and the assumption below says so in the file itself.

Lengths in the handoff are metres. IFC4 needs a declared unit and this writes SI metre, so no
value is rescaled on the way out — a silent millimetre conversion is indistinguishable from a
geometry defect downstream, which is the same reason the parser refuses a rescaled document.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import ifcopenshell

from .manifest import Handoff, MAT_FAMILY_KINDS, TIE_FAMILY_KINDS

#: How many straight segments approximate one arc of a bar centreline in THIS artifact.
#:
#: The STEP is exact; this is the IFC's own faceting and is declared as an assumption in the file.
#: Sixteen keeps a 90° starter bend visually round without inflating the polyline.
ARC_SEGMENTS = 16

#: IFC predefined types, from the producer's family — never guessed from a diameter or a name.
_PREDEFINED_TYPE = {
    "columnDowel": "MAIN",
    "footingBottomMatX": "MAIN",
    "footingBottomMatY": "MAIN",
    "starterTie": "LIGATURE",
    "starterCrosstie": "LIGATURE",
}


def _arc_points(seg: Any) -> list[tuple[float, float, float]]:
    """Sample one arc segment's centreline, inclusive of its end.

    Uses the stored centre, which is what makes the arc reconstructable at all: start, end, radius
    and sweep are satisfied by two centres in any plane, and the plane itself is free.
    """
    cx, cy, cz = seg.centre.x, seg.centre.y, seg.centre.z
    sx, sy, sz = seg.start.x, seg.start.y, seg.start.z
    ex, ey, ez = seg.end.x, seg.end.y, seg.end.z
    u = (sx - cx, sy - cy, sz - cz)
    w = (ex - cx, ey - cy, ez - cz)
    # The rotation axis is the arc's own plane normal, from the two radii.
    n = (
        u[1] * w[2] - u[2] * w[1],
        u[2] * w[0] - u[0] * w[2],
        u[0] * w[1] - u[1] * w[0],
    )
    nl = math.sqrt(sum(c * c for c in n))
    if nl <= 1e-15:
        # Degenerate: the two radii are collinear, so there is no plane to rotate in. A chord is
        # the honest fallback, and it is exactly what a zero-sweep arc means.
        return [(ex, ey, ez)]
    n = (n[0] / nl, n[1] / nl, n[2] / nl)
    sweep = math.radians(abs(seg.sweep_deg))
    out: list[tuple[float, float, float]] = []
    for i in range(1, ARC_SEGMENTS + 1):
        a = sweep * i / ARC_SEGMENTS
        ca, sa = math.cos(a), math.sin(a)
        # Rodrigues rotation of the start radius about the plane normal.
        cross = (
            n[1] * u[2] - n[2] * u[1],
            n[2] * u[0] - n[0] * u[2],
            n[0] * u[1] - n[1] * u[0],
        )
        dot = sum(a1 * b1 for a1, b1 in zip(n, u))
        r = tuple(u[k] * ca + cross[k] * sa + n[k] * dot * (1.0 - ca) for k in range(3))
        out.append((cx + r[0], cy + r[1], cz + r[2]))
    return out


def _centreline(bar: Any) -> list[tuple[float, float, float]]:
    """The bar's centreline as a polyline, following arcs."""
    first = bar.segments[0]
    pts: list[tuple[float, float, float]] = [(first.start.x, first.start.y, first.start.z)]
    for seg in bar.segments:
        if seg.kind == "arc" and seg.centre is not None and seg.sweep_deg is not None:
            pts.extend(_arc_points(seg))
        else:
            pts.append((seg.end.x, seg.end.y, seg.end.z))
    # Collapse coincident points: a zero-length polyline segment is not geometry an IFC tool can
    # sweep, and it arises legitimately where two segments meet.
    cleaned = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, cleaned[-1]) > 1e-12:
            cleaned.append(p)
    return cleaned


def write_ifc(handoff: Handoff, path: Path) -> dict[str, object]:
    """Write an IFC4 file and return what it contains, measured from the written model."""
    f = ifcopenshell.file(schema="IFC4")

    # ── Units: SI metre, declared, nothing rescaled ──
    metre = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    f.create_entity("IfcUnitAssignment", Units=[metre])

    origin = f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    axis_placement = f.create_entity("IfcAxis2Placement3D", Location=origin)
    context = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-08,
        WorldCoordinateSystem=axis_placement,
    )
    placement = f.create_entity("IfcLocalPlacement", RelativePlacement=axis_placement)

    project = f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name=f"{handoff.subject_name} — {handoff.assembly_kind}",
        Description=(
            "Disposable review artifact. Stabileo owns the engineering meaning of every value; "
            "this file carries geometry and family semantics for inspection only."
        ),
        RepresentationContexts=[context],
        UnitsInContext=f.by_type("IfcUnitAssignment")[0],
    )
    site = f.create_entity(
        "IfcSite", GlobalId=ifcopenshell.guid.new(), Name="Site", ObjectPlacement=placement
    )
    f.create_entity(
        "IfcRelAggregates",
        GlobalId=ifcopenshell.guid.new(),
        RelatingObject=project,
        RelatedObjects=[site],
    )

    family_of_bar = handoff.family_of_bar
    kind_of_family = {fam.family_id: fam.kind for fam in handoff.families}

    written: list[Any] = []
    for bar in handoff.bars:
        pts = _centreline(bar)
        if len(pts) < 2:
            continue
        poly = f.create_entity(
            "IfcPolyline",
            Points=[f.create_entity("IfcCartesianPoint", Coordinates=p) for p in pts],
        )
        # Swept along the centreline, so the bar stays a BAR with a radius a tool can read back
        # rather than becoming a mesh that merely looks like one.
        # `IfcSweptDiskSolid` is the shape an IFC4 tool reads as a bar of a given radius, and it
        # needs no per-station cross-section table.
        solid = f.create_entity(
            "IfcSweptDiskSolid",
            Directrix=poly,
            Radius=bar.diameter_mm / 2000.0,
        )
        shape = f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=context,
            RepresentationIdentifier="Body",
            RepresentationType="AdvancedSweptSolid",
            Items=[solid],
        )
        kind = kind_of_family.get(family_of_bar.get(bar.bar_id, ""), "")
        rebar = f.create_entity(
            "IfcReinforcingBar",
            GlobalId=ifcopenshell.guid.new(),
            # The producer's STABLE ID, verbatim, so a selection in an IFC tool names the same bar
            # the manifest, the STEP and cad-review.json name.
            Name=bar.bar_id,
            Tag=bar.bar_id,
            Description=kind or None,
            ObjectType=kind or None,
            ObjectPlacement=placement,
            Representation=f.create_entity("IfcProductDefinitionShape", Representations=[shape]),
            NominalDiameter=bar.diameter_mm / 1000.0,
            BarLength=bar.cutting_length,
            PredefinedType=_PREDEFINED_TYPE.get(kind, "NOTDEFINED"),
        )
        written.append(rebar)

    if written:
        f.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=ifcopenshell.guid.new(),
            RelatingStructure=site,
            RelatedElements=written,
        )

    # ── Groups: one per family, so the semantics survive the file ──
    groups = 0
    for fam in handoff.families:
        members = [r for r in written if r.Name in set(fam.bar_ids)]
        if not members:
            continue
        group = f.create_entity(
            "IfcGroup",
            GlobalId=ifcopenshell.guid.new(),
            Name=fam.kind,
            Description=fam.family_id,
        )
        f.create_entity(
            "IfcRelAssignsToGroup",
            GlobalId=ifcopenshell.guid.new(),
            RelatingGroup=group,
            RelatedObjects=members,
        )
        groups += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    f.write(str(path))

    return {
        "path": str(path),
        "schema": "IFC4",
        "reinforcing_bars": len(written),
        "family_groups": groups,
        "length_unit": "METRE",
        "arc_segments_per_arc": ARC_SEGMENTS,
        "mat_bars": sum(
            1
            for r in written
            if kind_of_family.get(family_of_bar.get(r.Name, ""), "") in MAT_FAMILY_KINDS
        ),
        "tie_bars": sum(
            1
            for r in written
            if kind_of_family.get(family_of_bar.get(r.Name, ""), "") in TIE_FAMILY_KINDS
        ),
    }
