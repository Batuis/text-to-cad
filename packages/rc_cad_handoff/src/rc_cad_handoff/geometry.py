"""Exact CAD geometry from a parsed handoff.

Two rules govern everything here.

**Arcs are realised from their supplied centres.** The producer stores a centre
precisely because start, end, radius and sweep do not determine an arc in three
dimensions. This module therefore builds each arc from ``(start, end, centre)``
— the only self-determining triple — and never from ``radius``/``sweepDeg``.
Those two are the producer's *nominal* bend parameters; where they disagree with
the realised arc the deviation is measured and reported rather than enforced,
because enforcing them would reject exact geometry, and re-deriving from them
would reintroduce the sagitta error the stored centre exists to prevent.

**Nothing is silently substituted.** An arc that cannot be realised exactly
raises, rather than degrading to its chord.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GC import GC_MakeArcOfCircle
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopoDS import TopoDS, TopoDS_Shape, TopoDS_Wire

from .errors import GeometryError
from .manifest import Bar, BarSegment, BoxShape, ConcreteBody, Handoff, Point3

#: Tolerance on ``|start-centre| == |end-centre|``. An arc whose endpoints are
#: not equidistant from its centre is not a circular arc at all, so this is a
#: correctness gate, not a fit tolerance.
ARC_RADIUS_CONSISTENCY_TOL = 1e-9

#: Below this the arc plane is indeterminate: start, centre and end are
#: collinear, so no unique circle passes through them.
ARC_PLANE_DEGENERACY_TOL = 1e-12


def _pnt(p: Point3) -> gp_Pnt:
    return gp_Pnt(p.x, p.y, p.z)


def _vec(a: Point3, b: Point3) -> gp_Vec:
    return gp_Vec(a.x - b.x, a.y - b.y, a.z - b.z)


def volume_of(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


# ─── Arc realisation ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArcRealisation:
    """What an arc turned out to be, next to what the producer nominally declared."""

    bar_id: str
    segment_index: int
    realised_radius: float
    realised_sweep_deg: float
    realised_length: float
    declared_radius: float | None
    declared_sweep_deg: float | None
    declared_length: float
    #: Endpoint asymmetry about the supplied centre. Near zero by construction.
    radius_asymmetry: float

    @property
    def radius_deviation(self) -> float | None:
        if self.declared_radius is None:
            return None
        return self.realised_radius - self.declared_radius

    @property
    def sweep_deviation(self) -> float | None:
        if self.declared_sweep_deg is None:
            return None
        return self.realised_sweep_deg - self.declared_sweep_deg

    @property
    def length_deviation(self) -> float:
        return self.realised_length - self.declared_length

    def deviates(self, *, length_tol: float) -> bool:
        return abs(self.length_deviation) > length_tol


def _arc_edge(
    bar_id: str, index: int, seg: BarSegment
) -> tuple[BRepBuilderAPI_MakeEdge, ArcRealisation]:
    if seg.centre is None:
        raise GeometryError(
            "ARC_WITHOUT_CENTRE",
            f"bar {bar_id!r} segment {index} is an exact arc with no centre; "
            "it will not be replaced by its chord",
            "segments[].centre",
        )
    v_start = _vec(seg.start, seg.centre)
    v_end = _vec(seg.end, seg.centre)
    r_start, r_end = v_start.Magnitude(), v_end.Magnitude()
    asymmetry = abs(r_start - r_end)
    if asymmetry > ARC_RADIUS_CONSISTENCY_TOL:
        raise GeometryError(
            "ARC_ENDPOINTS_NOT_EQUIDISTANT",
            f"bar {bar_id!r} segment {index}: endpoints are {asymmetry:.3e} m apart in radius "
            f"({r_start:.9f} vs {r_end:.9f}); the supplied centre does not define a circular arc",
            "segments[].centre",
        )
    normal = v_start.Crossed(v_end)
    if normal.Magnitude() <= ARC_PLANE_DEGENERACY_TOL:
        raise GeometryError(
            "ARC_PLANE_DEGENERATE",
            f"bar {bar_id!r} segment {index}: start, centre and end are collinear, "
            "so the arc plane is indeterminate",
            "segments[].centre",
        )

    realised_radius = (r_start + r_end) / 2.0
    circle = gp_Circ(gp_Ax2(_pnt(seg.centre), gp_Dir(normal), gp_Dir(v_start)), realised_radius)
    maker = GC_MakeArcOfCircle(circle, _pnt(seg.start), _pnt(seg.end), True)
    if not maker.IsDone():
        raise GeometryError(
            "ARC_CONSTRUCTION_FAILED",
            f"bar {bar_id!r} segment {index}: OCCT could not build the arc from its supplied centre",
            "segments[].centre",
        )

    sweep_deg = math.degrees(v_start.Angle(v_end))
    realisation = ArcRealisation(
        bar_id=bar_id,
        segment_index=index,
        realised_radius=realised_radius,
        realised_sweep_deg=sweep_deg,
        realised_length=realised_radius * math.radians(sweep_deg),
        declared_radius=seg.radius,
        declared_sweep_deg=seg.sweep_deg,
        declared_length=seg.length,
        radius_asymmetry=asymmetry,
    )
    return BRepBuilderAPI_MakeEdge(maker.Value()), realisation


def _straight_edge(bar_id: str, index: int, seg: BarSegment) -> BRepBuilderAPI_MakeEdge:
    if _vec(seg.end, seg.start).Magnitude() <= 0.0:
        raise GeometryError(
            "ZERO_LENGTH_SEGMENT",
            f"bar {bar_id!r} segment {index} has coincident endpoints",
            "segments[]",
        )
    return BRepBuilderAPI_MakeEdge(_pnt(seg.start), _pnt(seg.end))


def _start_tangent(bar_id: str, seg: BarSegment) -> gp_Vec:
    """Unit tangent at the start of a bar, used to orient the sweep profile."""
    if not seg.is_arc:
        tangent = _vec(seg.end, seg.start)
    else:
        assert seg.centre is not None  # guarded by _arc_edge
        v_start = _vec(seg.start, seg.centre)
        v_end = _vec(seg.end, seg.centre)
        tangent = v_start.Crossed(v_end).Crossed(v_start)
    magnitude = tangent.Magnitude()
    if magnitude <= ARC_PLANE_DEGENERACY_TOL:
        raise GeometryError(
            "START_TANGENT_DEGENERATE",
            f"bar {bar_id!r}: the tangent at the start of the path is indeterminate",
            "segments[0]",
        )
    return gp_Vec(tangent.X() / magnitude, tangent.Y() / magnitude, tangent.Z() / magnitude)


# ─── Bars ────────────────────────────────────────────────────────────────


@dataclass
class BarGeometry:
    """One bar, as both its exact centreline and its physical solid."""

    bar_id: str
    family_id: str
    role: str
    mark: str | None
    diameter_mm: float
    radius_m: float
    centreline: TopoDS_Wire
    solid: TopoDS_Shape
    arcs: tuple[ArcRealisation, ...]
    #: Developed length summed from the producer's own segment lengths.
    declared_developed_length: float
    cutting_length: float
    solid_volume: float

    @property
    def nominal_volume(self) -> float:
        return math.pi * self.radius_m**2 * self.declared_developed_length


def build_centreline(bar: Bar) -> tuple[TopoDS_Wire, tuple[ArcRealisation, ...]]:
    maker = BRepBuilderAPI_MakeWire()
    arcs: list[ArcRealisation] = []
    for index, seg in enumerate(bar.segments):
        if seg.is_arc:
            edge_maker, realisation = _arc_edge(bar.bar_id, index, seg)
            arcs.append(realisation)
        else:
            edge_maker = _straight_edge(bar.bar_id, index, seg)
        if not edge_maker.IsDone():
            raise GeometryError(
                "EDGE_CONSTRUCTION_FAILED",
                f"bar {bar.bar_id!r} segment {index}: edge construction failed",
                "segments[]",
            )
        maker.Add(edge_maker.Edge())
    if not maker.IsDone():
        raise GeometryError(
            "WIRE_NOT_CONNECTED",
            f"bar {bar.bar_id!r}: segments do not form a single connected centreline",
            "segments",
        )
    return maker.Wire(), tuple(arcs)


def build_bar(bar: Bar) -> BarGeometry:
    """Sweep a circular profile of the supplied diameter along the exact centreline."""
    centreline, arcs = build_centreline(bar)

    first = bar.segments[0]
    tangent = _start_tangent(bar.bar_id, first)
    profile_circle = gp_Circ(gp_Ax2(_pnt(first.start), gp_Dir(tangent)), bar.radius_m)
    profile = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(profile_circle).Edge())
    if not profile.IsDone():
        raise GeometryError(
            "PROFILE_CONSTRUCTION_FAILED", f"bar {bar.bar_id!r}: profile wire failed", None
        )

    pipe = BRepOffsetAPI_MakePipeShell(centreline)
    pipe.SetMode(True)  # Frenet: the profile follows the path frame.
    pipe.Add(profile.Wire(), False, False)
    pipe.Build()
    if not pipe.IsDone():
        raise GeometryError(
            "SWEEP_FAILED",
            f"bar {bar.bar_id!r}: sweeping the profile along the centreline failed",
            None,
        )
    if not pipe.MakeSolid():
        # An uncapped sweep is a shell, and a shell has no trustworthy volume,
        # so accepting one would silently break every volume reconciliation.
        raise GeometryError(
            "SWEEP_NOT_CLOSED", f"bar {bar.bar_id!r}: the swept tube could not be closed into a solid", None
        )
    solid = pipe.Shape()

    return BarGeometry(
        bar_id=bar.bar_id,
        family_id=bar.family_id,
        role=bar.role,
        mark=bar.mark,
        diameter_mm=bar.diameter_mm,
        radius_m=bar.radius_m,
        centreline=centreline,
        solid=solid,
        arcs=arcs,
        declared_developed_length=bar.developed_length,
        cutting_length=bar.cutting_length,
        solid_volume=volume_of(solid),
    )


# ─── Concrete ────────────────────────────────────────────────────────────


@dataclass
class ConcreteGeometry:
    body_id: str
    role: str
    solid: TopoDS_Shape
    declared_volume: float
    solid_volume: float
    truncated_faces: tuple[str, ...]

    @property
    def z_min(self) -> float:
        return self._extent[0]

    @property
    def z_max(self) -> float:
        return self._extent[1]

    _extent: tuple[float, float] = (0.0, 0.0)


def _rotated(shape: TopoDS_Shape, centre: Point3, rotation_deg: float) -> TopoDS_Shape:
    if rotation_deg == 0.0:
        return shape
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(_pnt(centre), gp_Dir(0.0, 0.0, 1.0)), math.radians(rotation_deg))
    transform = BRepBuilderAPI_Transform(shape, trsf, True)
    transform.Build()
    if not transform.IsDone():
        raise GeometryError("ROTATION_FAILED", "applying the body rotation failed", "shape.rotationDeg")
    return transform.Shape()


def _box_solid(shape: BoxShape) -> TopoDS_Shape:
    corner = gp_Pnt(
        shape.centre.x - shape.b / 2.0,
        shape.centre.y - shape.length / 2.0,
        shape.centre.z - shape.height / 2.0,
    )
    maker = BRepPrimAPI_MakeBox(corner, shape.b, shape.length, shape.height)
    maker.Build()
    if not maker.IsDone():
        raise GeometryError("BOX_CONSTRUCTION_FAILED", "building the concrete box failed", "shape")
    return _rotated(maker.Shape(), shape.centre, shape.rotation_deg)


def build_concrete(body: ConcreteBody) -> ConcreteGeometry:
    solid = _box_solid(body.shape)
    half_height = body.shape.height / 2.0
    geometry = ConcreteGeometry(
        body_id=body.body_id,
        role=body.role,
        solid=solid,
        declared_volume=body.shape.b * body.shape.length * body.shape.height,
        solid_volume=volume_of(solid),
        truncated_faces=body.truncated_faces,
    )
    geometry._extent = (body.shape.centre.z - half_height, body.shape.centre.z + half_height)
    return geometry


# ─── The realised model ──────────────────────────────────────────────────


@dataclass
class RealisedModel:
    bars: dict[str, BarGeometry]
    concrete: dict[str, ConcreteGeometry]

    @property
    def all_arcs(self) -> list[ArcRealisation]:
        return [arc for bar in self.bars.values() for arc in bar.arcs]

    @property
    def solid_count(self) -> int:
        return len(self.bars) + len(self.concrete)

    def bars_of_family(self, family_id: str) -> list[BarGeometry]:
        return [b for b in self.bars.values() if b.family_id == family_id]


def realise(handoff: Handoff) -> RealisedModel:
    """Build every concrete component and every bar solid, in document order."""
    concrete = {body.body_id: build_concrete(body) for body in handoff.bodies}
    bars = {bar.bar_id: build_bar(bar) for bar in handoff.bars}

    realised = RealisedModel(bars=bars, concrete=concrete)

    # Reconcile against the producer's own counts rather than trusting the loop.
    if len(bars) != len(handoff.bars):
        raise GeometryError(
            "BAR_SOLID_COUNT_MISMATCH",
            f"realised {len(bars)} bar solids for {len(handoff.bars)} declared bars",
            "reinforcement.bars",
        )
    if len(concrete) != len(handoff.bodies):
        raise GeometryError(
            "CONCRETE_COUNT_MISMATCH",
            f"realised {len(concrete)} concrete solids for {len(handoff.bodies)} declared bodies",
            "concrete.bodies",
        )
    return realised


# ─── Boolean helpers ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class BooleanResult:
    """A boolean volume, plus whether OCCT is to be trusted about it here."""

    volume: float | None
    ok: bool
    note: str | None = None


def common_volume(a: TopoDS_Shape, b: TopoDS_Shape) -> BooleanResult:
    op = BRepAlgoAPI_Common(a, b)
    op.Build()
    if not op.IsDone():
        return BooleanResult(None, False, "BRepAlgoAPI_Common did not complete")
    return BooleanResult(volume_of(op.Shape()), True)


def cut_volume(a: TopoDS_Shape, b: TopoDS_Shape) -> BooleanResult:
    """Volume of ``a`` outside ``b``."""
    op = BRepAlgoAPI_Cut(a, b)
    op.Build()
    if not op.IsDone():
        return BooleanResult(None, False, "BRepAlgoAPI_Cut did not complete")
    return BooleanResult(volume_of(op.Shape()), True)


def as_solid(shape: TopoDS_Shape):
    """Cast to ``TopoDS_Solid`` for build123d, which requires the concrete type."""
    return TopoDS.Solid_s(shape)


def z_extent(shape: TopoDS_Shape) -> tuple[float, float]:
    """Vertical extent of a shape's bounding box, m.

    Used as a cheap, boolean-free way to tell "these two solids cannot overlap"
    from "a boolean said they do not overlap". The two are very different claims
    and only one of them is trustworthy near coplanar faces.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, True)
    if box.IsVoid():
        raise GeometryError("EMPTY_BOUNDING_BOX", "shape has no bounding box", None)
    _, _, z_min, _, _, z_max = box.Get()
    return (z_min, z_max)


def vertical_overlap(a: TopoDS_Shape, b: TopoDS_Shape) -> float:
    """Height of the shared vertical band of two shapes, m. Zero when disjoint."""
    a_min, a_max = z_extent(a)
    b_min, b_max = z_extent(b)
    return max(0.0, min(a_max, b_max) - max(a_min, b_min))
