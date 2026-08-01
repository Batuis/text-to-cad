"""Independent geometric observation of a realised handoff.

Everything measured here is computed from OCCT geometry this consumer built, by
a different implementation from the producer's. That independence is the point:
agreement is evidence, and disagreement is a defect in one of the two.

Nothing in this module recomputes a classification rule, a required distance or
a regulatory verdict. It measures, and it labels what the measurement may be
compared against.

**On penetration depth.** ``BRepExtrema_DistShapeShape`` between two solids
returns ``0`` for every pair that touches or overlaps, so it cannot supply a
signed depth. Rather than invent one, the signed surface clearance is taken from
the exact *centreline* distance minus the sum of the radii — still OCCT, still
exact curves including arcs, and independent of how the producer sampled. Solid
distance and solid intersection are then used to corroborate the sign, and where
a boolean is numerically untrustworthy that is recorded instead of hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from .geometry import (
    BooleanResult,
    RealisedModel,
    common_volume,
    vertical_overlap,
    volume_of,
)
from .manifest import (
    PAIR_CLASS_PROHIBITED_OVERLAP,
    Check,
    CoverRequirement,
    Finding,
    Handoff,
)
from .status import Collision, Comparison, IssueKind

#: The approved agreement band. It is the producer's own collision sampling
#: chord tolerance, so anything tighter would report its sampling noise as
#: disagreement.
AGREEMENT_BAND_M = 0.0005

#: Distance below which two solids are treated as touching rather than apart.
CONTACT_TOL_M = 1e-9

#: A common volume this small is indistinguishable from a tangential contact.
COMMON_VOLUME_TOL_M3 = 1e-12


def _distance(a: TopoDS_Shape, b: TopoDS_Shape) -> float | None:
    op = BRepExtrema_DistShapeShape(a, b)
    op.Perform()
    if not op.IsDone():
        return None
    return op.Value()


# ─── Bar-pair observations ───────────────────────────────────────────────


@dataclass(frozen=True)
class PairObservation:
    """One independently measured bar pair."""

    bar_id_a: str
    bar_id_b: str
    #: Exact centreline-to-centreline distance, m.
    centreline_distance: float
    #: Signed surface clearance, m. Negative means the surfaces interpenetrate.
    cad_clearance: float
    #: Solid-to-solid minimum distance, m. Zero for contact and for overlap alike.
    solid_distance: float | None
    common_volume: BooleanResult
    classification: Collision
    #: Present only where the producer supplied a verdict for this pair.
    stabileo_measured: float | None = None
    stabileo_pair_class: str | None = None
    stabileo_finding_id: str | None = None
    reportable: bool | None = None
    comparison: Comparison = Comparison.NOT_COMPARABLE
    delta: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def classification_agrees(self) -> bool | None:
        """Does the independent classification match the producer's pair class?"""
        if self.stabileo_pair_class is None:
            return None
        if self.stabileo_pair_class == PAIR_CLASS_PROHIBITED_OVERLAP:
            return self.classification in (Collision.INTERSECTING, Collision.CONTACT)
        return self.classification is Collision.SEPARATED


def classify(cad_clearance: float) -> Collision:
    if cad_clearance < -AGREEMENT_BAND_M:
        return Collision.INTERSECTING
    if cad_clearance <= AGREEMENT_BAND_M:
        return Collision.CONTACT
    return Collision.SEPARATED


def observe_pair(
    model: RealisedModel,
    bar_a: str,
    bar_b: str,
    *,
    finding: Finding | None = None,
    reportable: bool | None = None,
) -> PairObservation:
    ga, gb = model.bars[bar_a], model.bars[bar_b]

    centreline_distance = _distance(ga.centreline, gb.centreline)
    if centreline_distance is None:
        raise RuntimeError(f"OCCT could not measure centreline distance for {bar_a!r}/{bar_b!r}")
    cad_clearance = centreline_distance - (ga.radius_m + gb.radius_m)
    solid_distance = _distance(ga.solid, gb.solid)
    common = common_volume(ga.solid, gb.solid)
    classification = classify(cad_clearance)

    notes: list[str] = []
    if solid_distance is not None and solid_distance <= CONTACT_TOL_M:
        notes.append(
            "solid-to-solid distance is zero, which confirms contact or overlap but cannot "
            "supply a signed penetration depth; the signed value comes from exact centreline "
            "distance minus the sum of the radii"
        )
    if (
        classification is Collision.INTERSECTING
        and common.ok
        and common.volume is not None
        and common.volume <= COMMON_VOLUME_TOL_M3
    ):
        # Coincident or tangent surfaces make BRepAlgoAPI_Common degenerate.
        # Reporting CONTACT on that basis would contradict an exact measurement.
        notes.append(
            "intersection volume returned zero for surfaces the exact distance shows "
            "interpenetrating; the boolean is degenerate for coincident centrelines and its "
            "volume is not used to classify this pair"
        )
    if not common.ok and common.note:
        notes.append(common.note)

    comparison = Comparison.NOT_COMPARABLE
    delta: float | None = None
    if finding is not None and finding.measured is not None:
        delta = abs(cad_clearance - finding.measured)
        comparison = Comparison.AGREEMENT if delta <= AGREEMENT_BAND_M else Comparison.DISAGREEMENT
    elif finding is None:
        notes.append(
            "the producer emitted no per-pair verdict for this pair, so the observation is not "
            "comparable to one; it is recorded as geometry only"
        )

    return PairObservation(
        bar_id_a=bar_a,
        bar_id_b=bar_b,
        centreline_distance=centreline_distance,
        cad_clearance=cad_clearance,
        solid_distance=solid_distance,
        common_volume=common,
        classification=classification,
        stabileo_measured=None if finding is None else finding.measured,
        stabileo_pair_class=None if finding is None else finding.pair_class,
        stabileo_finding_id=None if finding is None else finding.finding_id,
        reportable=reportable,
        comparison=comparison,
        delta=delta,
        notes=tuple(notes),
    )


def cross_check_pairs(handoff: Handoff, model: RealisedModel) -> list[PairObservation]:
    """Independently measure every pair the producer reported.

    Only checks that invite a cross-check are cross-checked. A check the producer
    did not evaluate has no verdict to agree with, and measuring against a
    verdict that does not exist is how ``NOT_EVALUATED`` becomes a false pass.
    """
    observations: list[PairObservation] = []
    for check in handoff.checks:
        if check.check_kind not in {"barCollision", "barClearSpacing"}:
            continue
        if not check.may_cross_check:
            continue
        for finding in check.findings:
            if finding.bar_id_a is None or finding.bar_id_b is None:
                continue
            requirement = handoff.pair_requirement(finding.bar_id_a, finding.bar_id_b)
            reportable = None if requirement is None else requirement.reportable
            if reportable is False:
                # The producer deliberately suppresses this pair class. Honour
                # that, or the review raises conflicts it chose not to report.
                continue
            observations.append(
                observe_pair(
                    model,
                    finding.bar_id_a,
                    finding.bar_id_b,
                    finding=finding,
                    reportable=reportable,
                )
            )
    observations.sort(key=lambda o: (o.bar_id_a, o.bar_id_b))
    return observations


def scan_unreported_pairs(
    handoff: Handoff, model: RealisedModel, *, reported: list[PairObservation]
) -> list[PairObservation]:
    """Observe close pairs the producer did not report.

    These are recorded strictly as geometry. Without a per-pair verdict there is
    nothing to agree or disagree with, and inferring one would be re-deriving a
    classification this consumer is not entitled to make.
    """
    already = {tuple(sorted((o.bar_id_a, o.bar_id_b))) for o in reported}
    bar_ids = sorted(model.bars)
    out: list[PairObservation] = []
    for i, a in enumerate(bar_ids):
        for b in bar_ids[i + 1 :]:
            if tuple(sorted((a, b))) in already:
                continue
            observation = observe_pair(model, a, b, finding=None)
            if observation.classification is Collision.SEPARATED:
                continue
            out.append(observation)
    out.sort(key=lambda o: (o.bar_id_a, o.bar_id_b))
    return out


# ─── Cover observation ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SurfaceScopeDecision:
    """Which of a body's faces a cover observation was allowed to measure."""

    measured_faces: tuple[str, ...]
    excluded_faces: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BarCoverObservation:
    bar_id: str
    #: Volume of this bar lying inside the scoping body, m³.
    volume_inside: float | None
    volume_outside: float | None
    #: Minimum distance from the in-body portion of the bar surface to the
    #: permitted exposed faces, m. ``None`` when it could not be measured.
    observed_cover: float | None
    crosses_excluded_interface: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverObservation:
    requirement_id: str
    check_id: str
    body_id: str
    required_distance: float
    #: Always NOT_COMPARABLE in this release: the producer does not evaluate
    #: containment, so there is no verdict a measurement could match.
    comparison: Comparison
    surface_scope: SurfaceScopeDecision
    bars: tuple[BarCoverObservation, ...]
    minimum_observed: float | None
    #: True when the measured geometry sits closer to an exposed face than the
    #: producer's requirement. Informational: containment is NOT_EVALUATED, so
    #: this contradicts no verdict and is not itself one.
    below_requirement: bool = False
    notes: tuple[str, ...] = ()


def _face_centroid(face: TopoDS_Shape) -> tuple[float, float, float]:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    centre = props.CentreOfMass()
    return (centre.X(), centre.Y(), centre.Z())


def _classify_faces(solid: TopoDS_Shape) -> dict[str, list[TopoDS_Shape]]:
    """Split a box's faces into top, bottom and sides by centroid elevation."""
    faces: list[TopoDS_Shape] = []
    explorer = TopExp_Explorer(solid, TopAbs_ShapeEnum.TopAbs_FACE)
    while explorer.More():
        faces.append(TopoDS.Face_s(explorer.Current()))
        explorer.Next()
    if not faces:
        return {}
    elevations = [_face_centroid(f)[2] for f in faces]
    z_min, z_max = min(elevations), max(elevations)
    grouped: dict[str, list[TopoDS_Shape]] = {"top": [], "bottom": [], "side": []}
    for face, z in zip(faces, elevations):
        if math.isclose(z, z_max, abs_tol=1e-9):
            grouped["top"].append(face)
        elif math.isclose(z, z_min, abs_tol=1e-9):
            grouped["bottom"].append(face)
        else:
            grouped["side"].append(face)
    return grouped


def observe_footing_cover(
    handoff: Handoff,
    model: RealisedModel,
    requirement: CoverRequirement,
    check: Check,
) -> CoverObservation:
    """Measure achieved cover as geometry, inside the scope the producer set.

    Scope is taken entirely from the manifest:

    * only the portion of a bar inside ``measurementScope.withinBodyId`` counts;
    * interfaces in ``excludeInterfaceIds`` are internal contacts, so the face
      that carries them is not a cover surface;
    * ``excludeTruncatedFaces`` removes cut planes, which are not concrete
      surfaces at all.

    The top face is not measured. It carries the excluded internal interface,
    and the requirement declares no ``surface`` scope — which the format defines
    as "the producer models no per-surface distinction", explicitly not a
    wildcard over every face. Measuring it would therefore be an assumption, so
    the limitation is recorded instead.
    """
    body_id = requirement.measurement_scope.within_body_id
    body_geometry = model.concrete[body_id]
    body = handoff.body(body_id)

    grouped = _classify_faces(body_geometry.solid)
    excluded: list[str] = []
    reasons: list[str] = []

    excluded_interfaces = [
        handoff.interface(i) for i in requirement.measurement_scope.exclude_interface_ids
    ]
    interface_elevations = [i.elevation for i in excluded_interfaces]

    if interface_elevations:
        reasons.append(
            "the internal concrete-to-concrete interface at z="
            + ", ".join(f"{z:.4f}" for z in interface_elevations)
            + " m is excluded by the requirement's measurementScope, so the face carrying it "
            "is not an exposed cover surface"
        )

    truncated = set(body.truncated_faces)
    if requirement.measurement_scope.exclude_truncated_faces and truncated:
        reasons.append(
            "truncated faces "
            + ", ".join(sorted(truncated))
            + " are cut planes rather than concrete surfaces and admit no cover measurement"
        )

    measured_faces: list[TopoDS_Shape] = []
    measured_names: list[str] = []
    for name in ("bottom", "side"):
        if name in truncated and requirement.measurement_scope.exclude_truncated_faces:
            excluded.append(name)
            continue
        if grouped.get(name):
            measured_faces.extend(grouped[name])
            measured_names.append(name)

    # The top face is where the excluded interface sits.
    if grouped.get("top"):
        excluded.append("top")
        reasons.append(
            "the top face is not measured: it carries the excluded internal interface, and the "
            "requirement declares no per-surface scope, which this format defines as the producer "
            "modelling no per-face distinction rather than the requirement applying to every face"
        )

    scope = SurfaceScopeDecision(
        measured_faces=tuple(measured_names),
        excluded_faces=tuple(sorted(set(excluded))),
        reasons=tuple(reasons),
    )

    passage = handoff.intentional_passage_bar_ids
    observations: list[BarCoverObservation] = []
    for bar_id in requirement.applies_to_bar_ids:
        geometry = model.bars[bar_id]
        notes: list[str] = []
        inside = common_volume(geometry.solid, body_geometry.solid)
        outside: BooleanResult | None = None
        observed: float | None = None

        if bar_id in passage:
            notes.append(
                "this bar deliberately crosses the internal interface; the crossing is the "
                "detail working and is never a containment or cover failure"
            )

        if not inside.ok or inside.volume is None:
            notes.append(
                "the intersection with the concrete body could not be computed, so no cover is "
                "reported for this bar rather than a value derived from partial geometry"
            )
        elif inside.volume <= COMMON_VOLUME_TOL_M3:
            # A zero boolean has two very different meanings. Separate them with
            # a bounding-box test, which no coplanar tangency can confuse.
            overlap = vertical_overlap(geometry.solid, body_geometry.solid)
            if overlap > CONTACT_TOL_M:
                notes.append(
                    f"the bar shares a {overlap:.6f} m vertical band with the concrete body, so "
                    "material inside it is expected, but the intersection boolean returned an "
                    "empty result — the bar's legs are coplanar with the body's top face, where "
                    "the boolean is degenerate. No cover is reported for this bar rather than a "
                    "value measured over the wrong region"
                )
            else:
                notes.append("this bar has no material inside the scoping body")
        else:
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

            op = BRepAlgoAPI_Common(geometry.solid, body_geometry.solid)
            op.Build()
            clipped = op.Shape()
            outside = BooleanResult(
                (geometry.solid_volume - inside.volume) if inside.volume is not None else None, True
            )
            distances = [d for d in (_distance(clipped, f) for f in measured_faces) if d is not None]
            if distances:
                observed = min(distances)
            else:
                notes.append("no permitted exposed face remained to measure against")

        observations.append(
            BarCoverObservation(
                bar_id=bar_id,
                volume_inside=inside.volume,
                volume_outside=None if outside is None else outside.volume,
                observed_cover=observed,
                crosses_excluded_interface=bar_id in passage,
                notes=tuple(notes),
            )
        )

    values = [o.observed_cover for o in observations if o.observed_cover is not None]
    minimum = min(values) if values else None
    return CoverObservation(
        requirement_id=requirement.requirement_id,
        check_id=check.check_id,
        body_id=body_id,
        required_distance=requirement.distance,
        comparison=Comparison.NOT_COMPARABLE,
        surface_scope=scope,
        bars=tuple(observations),
        minimum_observed=minimum,
        below_requirement=(minimum is not None and minimum < requirement.distance),
        notes=(
            "this is a CAD geometric observation, not a regulatory verdict and not a "
            "certification; the producer reports containment as NOT_EVALUATED and a measurement "
            "may never be presented as a pass",
        ),
    )


# ─── Whole-model observation ─────────────────────────────────────────────


@dataclass
class CrossCheckResult:
    reported_pairs: list[PairObservation]
    unreported_pairs: list[PairObservation]
    cover: list[CoverObservation]
    #: Checks whose policy forbids measurement, recorded so the refusal is visible.
    out_of_scope: list[tuple[str, str]] = field(default_factory=list)
    not_evaluated: list[tuple[str, str]] = field(default_factory=list)
    issues: list[dict[str, object]] = field(default_factory=list)

    @property
    def disagreements(self) -> list[PairObservation]:
        return [p for p in self.reported_pairs if p.comparison is Comparison.DISAGREEMENT]


def cross_check(handoff: Handoff, model: RealisedModel) -> CrossCheckResult:
    reported = cross_check_pairs(handoff, model)
    unreported = scan_unreported_pairs(handoff, model, reported=reported)

    cover: list[CoverObservation] = []
    out_of_scope: list[tuple[str, str]] = []
    not_evaluated: list[tuple[str, str]] = []
    issues: list[dict[str, object]] = []

    for check in handoff.checks:
        if check.out_of_scope:
            # Recorded, never measured. The distinction between "may observe but
            # not compare" and "must not measure" is what keeps an unevaluated
            # property from being reported as satisfied.
            out_of_scope.append((check.check_id, check.not_evaluated_reason or ""))
            continue
        if not check.evaluated:
            not_evaluated.append((check.check_id, check.not_evaluated_reason or ""))
        if check.check_kind not in {"concreteCover", "reinforcementContainment"}:
            continue
        if not check.may_observe_only:
            continue
        for requirement_id in check.requirement_ids:
            requirement = next(
                (r for r in handoff.cover_requirements if r.requirement_id == requirement_id), None
            )
            if requirement is None:
                continue
            if any(c.requirement_id == requirement_id and c.check_id == check.check_id for c in cover):
                continue
            cover.append(observe_footing_cover(handoff, model, requirement, check))

    for observation in reported:
        if observation.comparison is Comparison.DISAGREEMENT:
            issues.append(
                {
                    "kind": str(IssueKind.CROSS_CHECK_DISAGREEMENT),
                    "barIdA": observation.bar_id_a,
                    "barIdB": observation.bar_id_b,
                    "stabileoFindingId": observation.stabileo_finding_id,
                    "stabileoMeasured": observation.stabileo_measured,
                    "cadClearance": observation.cad_clearance,
                    "deltaM": observation.delta,
                    "agreementBandM": AGREEMENT_BAND_M,
                }
            )
        for note in observation.notes:
            if "boolean is degenerate" in note:
                issues.append(
                    {
                        "kind": str(IssueKind.NUMERICAL_LIMITATION),
                        "barIdA": observation.bar_id_a,
                        "barIdB": observation.bar_id_b,
                        "detail": note,
                    }
                )

    for note in handoff.unsupported:
        issues.append(
            {
                "kind": str(IssueKind.UNSUPPORTED_CONDITION),
                "code": note.code,
                "detail": note.text,
                "bodyIds": list(note.body_ids),
            }
        )

    for observation in cover:
        for reason in observation.surface_scope.reasons:
            issues.append(
                {
                    "kind": str(IssueKind.OBSERVATION_SCOPE_LIMITATION),
                    "requirementId": observation.requirement_id,
                    "checkId": observation.check_id,
                    "detail": reason,
                }
            )
        for bar_observation in observation.bars:
            for note in bar_observation.notes:
                if "boolean is degenerate" in note or "boolean returned an empty result" in note:
                    issues.append(
                        {
                            "kind": str(IssueKind.NUMERICAL_LIMITATION),
                            "requirementId": observation.requirement_id,
                            "checkId": observation.check_id,
                            "barIdA": bar_observation.bar_id,
                            "detail": note,
                        }
                    )
        if observation.below_requirement:
            issues.append(
                {
                    "kind": str(IssueKind.COVER_OBSERVATION_BELOW_REQUIREMENT),
                    "requirementId": observation.requirement_id,
                    "checkId": observation.check_id,
                    "bodyId": observation.body_id,
                    "requiredDistance": observation.required_distance,
                    "minimumObservedCover": observation.minimum_observed,
                    "comparison": str(Comparison.NOT_COMPARABLE),
                    "detail": (
                        "CAD measures the realised geometry closer to a permitted exposed face "
                        "than the producer's requirement. This is a geometric observation and not "
                        "a breach verdict: the producer reports containment as NOT_EVALUATED, so "
                        "there is no verdict to contradict, and the declared unmodelled footing "
                        "mat means this document carries only part of the reinforcement."
                    ),
                }
            )

    return CrossCheckResult(
        reported_pairs=reported,
        unreported_pairs=unreported,
        cover=cover,
        out_of_scope=out_of_scope,
        not_evaluated=not_evaluated,
        issues=issues,
    )
