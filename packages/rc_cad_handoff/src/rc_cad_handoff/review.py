"""``cad-review.json`` — the structured review artifact.

The document keeps three things permanently separate:

* what the producer decided (``AUTHORITATIVE_STABILEO``);
* what this consumer measured (``CAD_OBSERVATION``);
* the relationship between them (``AGREEMENT`` / ``DISAGREEMENT`` /
  ``NOT_COMPARABLE`` / ``NOT_EVALUATED`` / ``OUT_OF_SCOPE``).

A producer finding is echoed, never edited, and no CAD number is written into
the place a producer verdict would go.

Serialisation is deterministic — keys sorted at every level, fixed indent,
trailing newline — because the review's own hash is what tells a reviewer
whether two runs described the same cage. No timestamp is written: a timestamp
makes two identical reviews look different and a stale one look fresh.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactSet
from .crosscheck import AGREEMENT_BAND_M, CONTACT_TOL_M, CrossCheckResult
from .geometry import RealisedModel
from .manifest import CONTRACT, Handoff
from .status import Comparison, IssueKind, Provenance, Realisation

REVIEW_FORMAT = "RcCadReviewV1"
REVIEW_FORMAT_VERSION = 1

#: Length tolerance above which a realised arc is reported as deviating from the
#: producer's nominal bend parameters. Matches the agreement band.
ARC_LENGTH_REPORT_TOL_M = AGREEMENT_BAND_M


def serialise(document: Any) -> str:
    """Deterministic JSON: sorted keys, two-space indent, trailing newline."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _round(value: float | None, places: int = 9) -> float | None:
    """Quantise to a nanometre. Enough for geometry, short of pretending to more."""
    if value is None:
        return None
    return round(value, places)


def _tool_versions() -> dict[str, str]:
    """Backend versions, so a review states which kernel produced its numbers."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    versions: dict[str, str] = {"python": platform.python_version()}
    for key, distribution in (
        ("build123d", "build123d"),
        # The OCCT binding distribution; its version tracks the OCCT release.
        ("occt", "cadquery-ocp"),
        ("cadpy", "cadpy"),
        ("rcCadHandoff", "rc-cad-handoff"),
    ):
        try:
            versions[key] = version(distribution)
        except PackageNotFoundError:
            versions[key] = "unavailable"
    versions["occtBinding"] = "cadquery-ocp"
    return versions


def build_review(
    handoff: Handoff,
    model: RealisedModel,
    result: CrossCheckResult,
    artifacts: ArtifactSet | None,
    *,
    relative_to: Path | None = None,
) -> dict[str, Any]:
    review: dict[str, Any] = {
        "reviewFormat": REVIEW_FORMAT,
        "reviewFormatVersion": REVIEW_FORMAT_VERSION,
        "source": {
            "contract": CONTRACT,
            "schemaVersion": handoff.schema_version,
            "manifestSha256": handoff.manifest_sha256,
            "manifestSizeBytes": handoff.manifest_bytes,
            "generator": {
                "name": handoff.generator_name,
                "version": handoff.generator_version,
            },
            "gitRevision": handoff.source_git_revision,
            "revisions": dict(sorted(handoff.revisions.items())),
            "certificate": {k: handoff.certificate[k] for k in sorted(handoff.certificate)},
            "project": handoff.project,
            "subject": {
                "kind": handoff.subject_kind,
                "entityId": handoff.subject_entity_id,
                "name": handoff.subject_name,
            },
            "assembly": {
                "kind": handoff.assembly_kind,
                "completeness": handoff.assembly_completeness,
            },
        },
        "units": {
            "length": "m",
            "angle": "deg",
            "barDiameter": "mm",
            "volume": "m3",
        },
        "tolerances": {
            "agreementBandM": AGREEMENT_BAND_M,
            "contactToleranceM": CONTACT_TOL_M,
            "arcLengthReportToleranceM": ARC_LENGTH_REPORT_TOL_M,
            "agreementBandRationale": (
                "the producer's own collision sampling chord tolerance; a tighter band would "
                "report its sampling noise as disagreement"
            ),
        },
        "tooling": _tool_versions(),
        "artifacts": (
            {
                "step": artifacts.step.as_json(relative_to=relative_to),
                "glb": artifacts.glb.as_json(relative_to=relative_to),
                "assemblyLabel": artifacts.assembly_label,
                "bodyNames": sorted(artifacts.body_names),
                "glbOrientation": {
                    "note": (
                        "GLB is Y-up by glTF convention; the Z-up to Y-up conversion is applied "
                        "on the CAD side by cadpy's native writer and declared here. The manifest "
                        "and every measurement in this review remain Z-up."
                    )
                },
            }
            if artifacts is not None
            else None
        ),
        "concreteReconciliation": _concrete_reconciliation(handoff, model),
        "reinforcementReconciliation": _reinforcement_reconciliation(handoff, model),
        "approximationInventory": _approximation_inventory(handoff, model),
        "authoritativeFindings": _authoritative_findings(handoff),
        "cadObservations": {
            "barPairs": [_pair_json(p) for p in result.reported_pairs],
            "unreportedBarPairs": [_pair_json(p) for p in result.unreported_pairs],
            "cover": [_cover_json(c) for c in result.cover],
        },
        "observationPolicy": _policy_status(handoff),
        "issues": _issues(result),
        "summary": _summary(handoff, model, result),
    }
    return review


def _concrete_reconciliation(handoff: Handoff, model: RealisedModel) -> dict[str, Any]:
    components = []
    for body in handoff.bodies:
        geometry = model.concrete[body.body_id]
        components.append(
            {
                "bodyId": body.body_id,
                "role": body.role,
                "sourceKind": body.source_kind,
                "sourceId": body.source_id,
                "sourceName": body.source_name,
                "elementId": body.element_id,
                "declaredB": body.shape.b,
                "declaredL": body.shape.length,
                "declaredHeight": body.shape.height,
                "declaredRotationDeg": body.shape.rotation_deg,
                "declaredVolume": _round(geometry.declared_volume),
                "cadSolidVolume": _round(geometry.solid_volume),
                "volumeDelta": _round(geometry.solid_volume - geometry.declared_volume),
                "verticalExtent": {
                    "zMin": _round(geometry.z_min),
                    "zMax": _round(geometry.z_max),
                },
                "truncatedFaces": list(body.truncated_faces),
                "truncatedFacesAreNotCoverSurfaces": bool(body.truncated_faces),
                "realisation": str(Realisation.EXACT),
            }
        )
    interfaces = [
        {
            "interfaceId": i.interface_id,
            "kind": i.kind,
            "exposure": i.exposure,
            "isInternalContact": i.exposure == "internal",
            "belowBodyId": i.below_body_id,
            "aboveBodyId": i.above_body_id,
            "elevation": _round(i.elevation),
            "intentionalBarPassageIds": sorted(i.intentional_bar_ids),
            "intentionalPassageIsNotAFailure": True,
        }
        for i in handoff.interfaces
    ]
    return {
        "declaredComponentCount": len(handoff.bodies),
        "realisedSolidCount": len(model.concrete),
        "componentsMatch": len(handoff.bodies) == len(model.concrete),
        "components": components,
        "interfaces": interfaces,
    }


def _reinforcement_reconciliation(handoff: Handoff, model: RealisedModel) -> dict[str, Any]:
    families = []
    for family in handoff.families:
        realised = model.bars_of_family(family.family_id)
        families.append(
            {
                "familyId": family.family_id,
                "kind": family.kind,
                "purposeKey": family.purpose_key,
                "declaredBarCount": len(family.bar_ids),
                "realisedSolidCount": len(realised),
                "matches": len(family.bar_ids) == len(realised),
            }
        )

    bars = []
    for bar in handoff.bars:
        geometry = model.bars[bar.bar_id]
        bars.append(
            {
                "barId": bar.bar_id,
                "mark": bar.mark,
                "familyId": bar.family_id,
                "role": bar.role,
                "diameterMm": bar.diameter_mm,
                "segmentCount": len(bar.segments),
                "arcCount": sum(1 for s in bar.segments if s.is_arc),
                "declaredCuttingLength": bar.cutting_length,
                "declaredDevelopedLength": _round(bar.developed_length),
                "cuttingLengthMatchesDevelopedLength": bool(
                    abs(bar.cutting_length - bar.developed_length) <= ARC_LENGTH_REPORT_TOL_M
                ),
                "cadSolidVolume": _round(geometry.solid_volume),
                "nominalSweptVolume": _round(geometry.nominal_volume),
                "volumeRatio": _round(geometry.solid_volume / geometry.nominal_volume, 6),
                "ownerElementIds": list(bar.owner_element_ids),
                "startTreatment": bar.start_treatment_kind,
                "endTreatment": bar.end_treatment_kind,
                "layerId": bar.layer_id,
                "realisation": str(Realisation.EXACT),
            }
        )

    marks = [
        {
            "mark": m.mark,
            "diameterMm": m.diameter_mm,
            "quantity": m.quantity,
            "declaredBarCount": len(m.bar_ids),
            "cuttingLength": m.cutting_length,
            "massKg": m.mass_kg,
            "quantityMatchesBarCount": m.quantity == len(m.bar_ids),
        }
        for m in handoff.marks
    ]

    return {
        "declaredBarCount": len(handoff.bars),
        "realisedSolidCount": len(model.bars),
        "barsMatch": len(handoff.bars) == len(model.bars),
        "families": families,
        "bars": bars,
        "marks": marks,
        "totalSolidCount": model.solid_count,
    }


def _approximation_inventory(handoff: Handoff, model: RealisedModel) -> dict[str, Any]:
    """Every arc, and whether anything about it was approximated.

    The geometry of each arc is exact — built from the supplied centre. Separate
    from that, the producer's nominal ``radius``/``sweepDeg`` may not equal the
    realised arc; that is recorded as a declared-parameter deviation, not as a
    geometric approximation, because the curve itself was not approximated.
    """
    declared_approximated = [
        {"barId": bar.bar_id, "segmentIndex": index}
        for bar in handoff.bars
        for index, seg in enumerate(bar.segments)
        if seg.is_arc and seg.arc_approximated
    ]

    deviations = []
    for arc in model.all_arcs:
        if not arc.deviates(length_tol=ARC_LENGTH_REPORT_TOL_M) and (
            arc.radius_deviation is None or abs(arc.radius_deviation) <= 1e-12
        ):
            continue
        deviations.append(
            {
                "barId": arc.bar_id,
                "segmentIndex": arc.segment_index,
                "declaredRadius": arc.declared_radius,
                "realisedRadius": _round(arc.realised_radius),
                "radiusDeviation": _round(arc.radius_deviation),
                "declaredSweepDeg": arc.declared_sweep_deg,
                "realisedSweepDeg": _round(arc.realised_sweep_deg, 6),
                "sweepDeviationDeg": _round(arc.sweep_deviation, 6),
                "declaredLength": arc.declared_length,
                "realisedLength": _round(arc.realised_length),
                "lengthDeviation": _round(arc.length_deviation),
            }
        )

    return {
        "totalArcCount": len(model.all_arcs),
        "exactArcCount": len(model.all_arcs) - len(declared_approximated),
        "approximatedArcCount": len(declared_approximated),
        "approximatedArcs": declared_approximated,
        "chordSubstitutionsMade": 0,
        "arcRealisationBasis": (
            "each arc is built from its supplied centre with start and end; radius and sweepDeg "
            "are never used to re-derive the curve, because start, end, radius and sweep do not "
            "determine an arc in three dimensions"
        ),
        "worstEndpointRadiusAsymmetry": _round(
            max((a.radius_asymmetry for a in model.all_arcs), default=0.0), 15
        ),
        "nominalBendParameterDeviations": deviations,
        "nominalBendParameterNote": (
            "a deviation here means the producer's nominal bend parameters do not equal the "
            "realised three-dimensional arc; the arc geometry itself is exact and no chord was "
            "substituted"
        ),
    }


def _authoritative_findings(handoff: Handoff) -> list[dict[str, Any]]:
    """The producer's own results, echoed verbatim and marked as authoritative."""
    out: list[dict[str, Any]] = []
    for check in handoff.checks:
        for finding in check.findings:
            out.append(
                {
                    "provenance": str(Provenance.AUTHORITATIVE_STABILEO),
                    "checkId": check.check_id,
                    "checkKind": check.check_kind,
                    "authority": check.authority,
                    "findingId": finding.finding_id,
                    "severity": finding.severity,
                    "pairClass": finding.pair_class,
                    "barIdA": finding.bar_id_a,
                    "barIdB": finding.bar_id_b,
                    "measured": finding.measured,
                    "required": finding.required,
                    "shortfall": finding.shortfall,
                    "at": None if finding.at is None else list(finding.at.as_tuple()),
                }
            )
    out.sort(key=lambda f: (str(f["checkId"]), str(f["findingId"])))
    return out


def _pair_json(observation) -> dict[str, Any]:
    common = observation.common_volume
    return {
        "provenance": str(Provenance.CAD_OBSERVATION),
        "barIdA": observation.bar_id_a,
        "barIdB": observation.bar_id_b,
        "cadCentrelineDistance": _round(observation.centreline_distance),
        "cadClearance": _round(observation.cad_clearance),
        "cadSolidDistance": _round(observation.solid_distance),
        "cadIntersectionVolume": _round(common.volume) if common.ok else None,
        "cadIntersectionVolumeReliable": common.ok,
        "cadCollisionClassification": str(observation.classification),
        "stabileoMeasured": observation.stabileo_measured,
        "stabileoPairClass": observation.stabileo_pair_class,
        "stabileoFindingId": observation.stabileo_finding_id,
        "reportable": observation.reportable,
        "comparison": str(observation.comparison),
        "deltaM": _round(observation.delta),
        "withinAgreementBand": (
            None if observation.delta is None else observation.delta <= AGREEMENT_BAND_M
        ),
        "classificationAgrees": observation.classification_agrees,
        "notes": list(observation.notes),
    }


def _cover_json(observation) -> dict[str, Any]:
    return {
        "provenance": str(Provenance.CAD_OBSERVATION),
        "requirementId": observation.requirement_id,
        "checkId": observation.check_id,
        "bodyId": observation.body_id,
        "requiredDistance": observation.required_distance,
        "comparison": str(observation.comparison),
        "minimumObservedCover": _round(observation.minimum_observed),
        "observedBelowRequirement": observation.below_requirement,
        "isRegulatoryVerdict": False,
        "surfaceScope": {
            "measuredFaces": list(observation.surface_scope.measured_faces),
            "excludedFaces": list(observation.surface_scope.excluded_faces),
            "reasons": list(observation.surface_scope.reasons),
        },
        "bars": [
            {
                "barId": b.bar_id,
                "volumeInsideBody": _round(b.volume_inside),
                "volumeOutsideBody": _round(b.volume_outside),
                "observedCover": _round(b.observed_cover),
                "crossesExcludedInternalInterface": b.crosses_excluded_interface,
                "notes": list(b.notes),
            }
            for b in observation.bars
        ],
        "notes": list(observation.notes),
    }


def _policy_status(handoff: Handoff) -> list[dict[str, Any]]:
    """Per-check record of what the producer permitted and what was done."""
    out = []
    for check in handoff.checks:
        if check.out_of_scope:
            action = "NOT_MEASURED"
            comparison = Comparison.OUT_OF_SCOPE
        elif not check.evaluated:
            action = "OBSERVED_NOT_COMPARABLE" if check.may_observe_only else "NOT_MEASURED"
            comparison = Comparison.NOT_EVALUATED
        else:
            action = "CROSS_CHECKED" if check.may_cross_check else "OBSERVED_NOT_COMPARABLE"
            comparison = Comparison.AGREEMENT if check.may_cross_check else Comparison.NOT_COMPARABLE
        out.append(
            {
                "checkId": check.check_id,
                "checkKind": check.check_kind,
                "authority": check.authority,
                "evaluationStatus": check.evaluation_status,
                "consumerObservationPolicy": check.consumer_observation_policy,
                "consumerAction": action,
                "producerVerdictStatus": str(comparison)
                if not check.evaluated
                else "EVALUATED",
                "notEvaluatedReason": check.not_evaluated_reason,
                "notEvaluatedCode": check.not_evaluated_code,
                "declaredFindingCount": len(check.findings),
                "neverConvertedToPass": not check.evaluated,
            }
        )
    out.sort(key=lambda c: str(c["checkId"]))
    return out


def _issues(result: CrossCheckResult) -> list[dict[str, Any]]:
    def key(issue: dict[str, Any]) -> tuple[str, ...]:
        return (
            str(issue.get("kind", "")),
            str(issue.get("barIdA", "")),
            str(issue.get("barIdB", "")),
            str(issue.get("code", "")),
            str(issue.get("requirementId", "")),
            str(issue.get("detail", ""))[:120],
        )

    return sorted((dict(i) for i in result.issues), key=key)


def _summary(handoff: Handoff, model: RealisedModel, result: CrossCheckResult) -> dict[str, Any]:
    reported = result.reported_pairs
    agreements = [p for p in reported if p.comparison is Comparison.AGREEMENT]
    disagreements = [p for p in reported if p.comparison is Comparison.DISAGREEMENT]
    worst = max((p.delta for p in reported if p.delta is not None), default=None)
    return {
        "concreteComponents": len(model.concrete),
        "barSolids": len(model.bars),
        "totalSolids": model.solid_count,
        "totalArcs": len(model.all_arcs),
        "approximatedArcs": sum(
            1 for bar in handoff.bars for s in bar.segments if s.is_arc and s.arc_approximated
        ),
        "authoritativeFindingCount": sum(len(c.findings) for c in handoff.checks),
        "crossCheckedPairCount": len(reported),
        "agreementCount": len(agreements),
        "disagreementCount": len(disagreements),
        "worstDeltaM": _round(worst),
        "unreportedClosePairCount": len(result.unreported_pairs),
        "outOfScopeCheckCount": len(result.out_of_scope),
        "notEvaluatedCheckCount": len(result.not_evaluated),
        "unsupportedConditionCount": len(handoff.unsupported),
        "blockedByUnsupportedConditions": bool(handoff.unsupported),
        "cleanPass": False,
        "cleanPassRationale": (
            "this review is never a clean pass: the producer declares unsupported conditions and "
            "reports containment as NOT_EVALUATED, and a consumer may not convert either into a pass"
        ),
    }


def write_review(document: dict[str, Any], path: Path) -> tuple[Path, int, str]:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialise(document).encode("utf-8")
    path.write_bytes(payload)
    return path, len(payload), hashlib.sha256(payload).hexdigest()
