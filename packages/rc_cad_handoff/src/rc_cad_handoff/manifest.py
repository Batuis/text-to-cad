"""An independent parser for the ``RcCadHandoffV1`` interchange format.

Written against the documented format rather than generated from, or translated
from, the producer's implementation. It reads the stable field names the format
defines — that is what interoperability requires — and derives no engineering
rule from them. Every classification, requirement and verdict in the document
stays the producer's; this module only checks that the document says what it
claims to and hands the values on unchanged.

Units are the document's own: lengths metres, angles degrees, bar diameters
millimetres. Nothing is rescaled on the way in, because a silent unit
conversion is indistinguishable from a geometry defect downstream.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ContractError, StructureError
from .status import KNOWN_POLICIES

CONTRACT = "RcCadHandoffV1"

#: Every version this consumer reads, and the contract name each one must declare.
#:
#: Two entries rather than one because the producer's subject changed: V1 carries the
#: column-transfer cage and says, in its own conditions, that the footing mats are NOT bar
#: geometry. Once the mats became physical steel that stopped being true, and a V1 document
#: carrying them would have to describe twenty mat bars as column dowels — so the producer
#: declared V2 instead of widening V1. Reading both is what lets an old file on disk keep working.
CONTRACTS: dict[int, str] = {1: "RcCadHandoffV1", 2: "RcCadHandoffV2"}
SUPPORTED_SCHEMA_VERSIONS = frozenset(CONTRACTS)

#: Reinforcement families each version may declare.
#:
#: V1's two are frozen. V2 adds the crossties the certified column layout earns and the two mat
#: directions. A family kind outside its version's set is refused rather than guessed at: the whole
#: point of naming families is that a consumer can act on the name, and a mat bar silently read as
#: a column dowel is the exact defect that made V2 necessary.
FAMILY_KINDS: dict[int, frozenset[str]] = {
    1: frozenset({"columnDowel", "starterTie"}),
    2: frozenset(
        {
            "columnDowel",
            "starterTie",
            "starterCrosstie",
            "footingBottomMatX",
            "footingBottomMatY",
        }
    ),
}

#: The two V2 families that describe bottom-mat steel. Never a column dowel.
MAT_FAMILY_KINDS = frozenset({"footingBottomMatX", "footingBottomMatY"})
TIE_FAMILY_KINDS = frozenset({"starterTie", "starterCrosstie"})

#: Bar-pair classification the producer treats as never acceptable.
PAIR_CLASS_PROHIBITED_OVERLAP = "prohibitedOverlap"


# ─── Primitives ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Point3:
    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class Note:
    text: str
    code: str | None = None
    message_key: str | None = None
    body_ids: tuple[str, ...] = ()
    element_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class BoxShape:
    b: float
    length: float
    height: float
    centre: Point3
    rotation_deg: float


@dataclass(frozen=True)
class ConcreteBody:
    body_id: str
    role: str
    shape: BoxShape
    truncated_faces: tuple[str, ...]
    source_kind: str
    source_id: int
    source_name: str | None
    element_id: int | None
    material_ref: str | None


@dataclass(frozen=True)
class ConcreteInterface:
    interface_id: str
    kind: str
    below_body_id: str
    above_body_id: str
    exposure: str
    elevation: float
    b: float
    length: float
    centre: Point3
    rotation_deg: float
    intentional_bar_ids: tuple[str, ...]
    intentional_reason_key: str | None


@dataclass(frozen=True)
class BarSegment:
    kind: str
    start: Point3
    end: Point3
    length: float
    radius: float | None = None
    sweep_deg: float | None = None
    centre: Point3 | None = None
    arc_approximated: bool = False

    @property
    def is_arc(self) -> bool:
        return self.kind == "arc"


@dataclass(frozen=True)
class Bar:
    bar_id: str
    diameter_mm: float
    role: str
    family_id: str
    segments: tuple[BarSegment, ...]
    cutting_length: float
    owner_element_ids: tuple[int, ...]
    mark: str | None
    layer_id: str | None
    start_treatment_kind: str
    end_treatment_kind: str

    @property
    def radius_m(self) -> float:
        """Bar radius in metres. The diameter is the format's only mm field."""
        return self.diameter_mm / 2000.0

    @property
    def developed_length(self) -> float:
        return math.fsum(s.length for s in self.segments)


@dataclass(frozen=True)
class Mark:
    mark: str
    diameter_mm: float
    cutting_length: float
    quantity: int
    role: str | None
    bar_ids: tuple[str, ...]
    mass_kg: float | None
    shape: str | None


@dataclass(frozen=True)
class MatRegion:
    """One distribution region of one bottom-mat direction."""

    kind: str
    bar_ids: tuple[str, ...]
    spacing_centre: float
    spacing_clear: float
    width: float
    centre_offset: float


@dataclass(frozen=True)
class MatFamilyDetail:
    """Which mat this family is, and where it physically sits.

    Read rather than derived. A consumer that clustered bar elevations to recover the layer order
    would be inferring something the producer already resolved and stated.
    """

    direction: str
    layer: str
    axis_elevation: float
    clear_cover_to_soffit: float
    regions: tuple[MatRegion, ...]


@dataclass(frozen=True)
class TieFamilyDetail:
    """Legs across the width and the stations along the lap."""

    legs_contributed: int
    stations: tuple[float, ...]


@dataclass(frozen=True)
class ReinforcementFamily:
    family_id: str
    kind: str
    purpose_key: str
    bar_ids: tuple[str, ...]
    #: V2 only, and present exactly on the two mat families.
    mat: MatFamilyDetail | None = None
    #: V2 only, and present exactly on the tie and crosstie families.
    tie: TieFamilyDetail | None = None


@dataclass(frozen=True)
class MeasurementScope:
    within_body_id: str
    exclude_interface_ids: tuple[str, ...]
    exclude_truncated_faces: bool


@dataclass(frozen=True)
class CoverRequirement:
    requirement_id: str
    element_id: int
    element_type: str
    applies_to_body_ids: tuple[str, ...]
    applies_to_bar_ids: tuple[str, ...]
    measurement_scope: MeasurementScope
    distance: float
    category: str
    provenance_source: str
    #: ``None`` means the producer models no per-surface distinction. That is a
    #: stated limitation, never a wildcard over every face.
    surface_face: str | None


@dataclass(frozen=True)
class ClearSpacingRequirement:
    requirement_id: str
    distance: float
    category: str
    bar_id_a: str | None
    bar_id_b: str | None
    pair_class: str | None
    reportable: bool | None
    role_pair: tuple[str, str] | None
    member_kind: str | None
    governing_diameter_mm: float | None


@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: str
    bar_id_a: str | None
    bar_id_b: str | None
    pair_class: str | None
    at: Point3 | None
    measured: float | None
    required: float | None
    shortfall: float | None


@dataclass(frozen=True)
class Check:
    check_id: str
    check_kind: str
    authority: str
    evaluation_status: str
    consumer_observation_policy: str
    requirement_ids: tuple[str, ...]
    findings: tuple[Finding, ...]
    not_evaluated_reason: str | None
    not_evaluated_code: str | None
    scope_bar_ids: tuple[str, ...]
    scope_body_ids: tuple[str, ...]
    scope_interface_ids: tuple[str, ...]
    scope_element_ids: tuple[int, ...]

    @property
    def evaluated(self) -> bool:
        return self.evaluation_status == "EVALUATED"

    @property
    def may_cross_check(self) -> bool:
        return self.consumer_observation_policy == "MAY_CROSS_CHECK"

    @property
    def may_observe_only(self) -> bool:
        return self.consumer_observation_policy == "MAY_OBSERVE_NOT_COMPARABLE"

    @property
    def out_of_scope(self) -> bool:
        return self.consumer_observation_policy == "OUT_OF_SCOPE"


@dataclass(frozen=True)
class Statuses:
    """The producer's own statuses, VERBATIM.

    Not re-encoded. The producer distinguishes an anchorage that was VERIFIED from one evaluated
    and FAILED, and any local three-value re-encoding loses that — so these carry the strings the
    document carries and a consumer switches on them. ``bottom_anchorage == "FAILED"`` is a real
    state and is never softened here to NOT_EVALUATED, OK or a warning.
    """

    constructible: bool
    constructibility_blockers: tuple[str, ...]
    bottom_flexure: str
    bottom_mat_geometry: str
    bottom_anchorage: str
    top_reinforcement: str
    punching_moment_transfer: str


@dataclass(frozen=True)
class BottomMatLayerOrder:
    """Which mat direction sits in the LOWER physical layer, stated once."""

    lower_direction: str
    resolution: str


@dataclass(frozen=True)
class Handoff:
    """A parsed, structurally validated handoff document."""

    schema_version: int
    generator_name: str
    generator_version: str
    subject_kind: str
    subject_entity_id: int
    subject_name: str
    assembly_kind: str
    assembly_completeness: str
    families: tuple[ReinforcementFamily, ...]
    revisions: dict[str, int]
    certificate: dict[str, Any]
    project: dict[str, str]
    bodies: tuple[ConcreteBody, ...]
    interfaces: tuple[ConcreteInterface, ...]
    bars: tuple[Bar, ...]
    marks: tuple[Mark, ...]
    cover_requirements: tuple[CoverRequirement, ...]
    clear_spacing_requirements: tuple[ClearSpacingRequirement, ...]
    checks: tuple[Check, ...]
    assumptions: tuple[Note, ...]
    unsupported: tuple[Note, ...]
    source_git_revision: str | None
    #: SHA-256 of the exact bytes parsed, so a review names the document it read.
    manifest_sha256: str
    manifest_bytes: int
    #: V2 only. ``None`` for a V1 document, which states no statuses block at all.
    statuses: Statuses | None = None
    #: V2 only, and ``None`` when the producer modelled no mat.
    bottom_mat_layer_order: BottomMatLayerOrder | None = None

    # -- lookups ---------------------------------------------------------

    def bar(self, bar_id: str) -> Bar:
        for b in self.bars:
            if b.bar_id == bar_id:
                return b
        raise StructureError("UNKNOWN_BAR_ID", f"no bar {bar_id!r}", "reinforcement.bars")

    def body(self, body_id: str) -> ConcreteBody:
        for b in self.bodies:
            if b.body_id == body_id:
                return b
        raise StructureError("UNKNOWN_BODY_ID", f"no body {body_id!r}", "concrete.bodies")

    def interface(self, interface_id: str) -> ConcreteInterface:
        for i in self.interfaces:
            if i.interface_id == interface_id:
                return i
        raise StructureError(
            "UNKNOWN_INTERFACE_ID", f"no interface {interface_id!r}", "concrete.interfaces"
        )

    def check_of_kind(self, kind: str, *, body_id: str | None = None) -> Check | None:
        for c in self.checks:
            if c.check_kind != kind:
                continue
            if body_id is not None and body_id not in c.scope_body_ids:
                continue
            return c
        return None

    def pair_requirement(self, bar_a: str, bar_b: str) -> ClearSpacingRequirement | None:
        for r in self.clear_spacing_requirements:
            if r.bar_id_a == bar_a and r.bar_id_b == bar_b:
                return r
            if r.bar_id_a == bar_b and r.bar_id_b == bar_a:
                return r
        return None

    @property
    def family_of_bar(self) -> dict[str, str]:
        return {b.bar_id: b.family_id for b in self.bars}

    @property
    def intentional_passage_bar_ids(self) -> frozenset[str]:
        """Bars the producer says deliberately cross an internal interface."""
        ids: set[str] = set()
        for i in self.interfaces:
            ids.update(i.intentional_bar_ids)
        return frozenset(ids)

    def arc_segments(self) -> list[tuple[Bar, int, BarSegment]]:
        out: list[tuple[Bar, int, BarSegment]] = []
        for bar in self.bars:
            for index, seg in enumerate(bar.segments):
                if seg.is_arc:
                    out.append((bar, index, seg))
        return out


# ─── Field readers ───────────────────────────────────────────────────────


def _obj(node: Any, path: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise StructureError("NOT_AN_OBJECT", f"expected an object, got {type(node).__name__}", path)
    return node


def _req(node: dict[str, Any], key: str, path: str) -> Any:
    if key not in node:
        raise StructureError("MISSING_FIELD", f"required field {key!r} is absent", f"{path}.{key}")
    return node[key]


def _finite(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StructureError("NOT_A_NUMBER", f"expected a number, got {value!r}", path)
    result = float(value)
    if not math.isfinite(result):
        raise StructureError("NON_FINITE_NUMBER", f"value is not finite: {value!r}", path)
    return result


def _positive(value: Any, path: str) -> float:
    result = _finite(value, path)
    if result <= 0.0:
        raise StructureError("NOT_POSITIVE", f"expected a positive number, got {result!r}", path)
    return result


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise StructureError("NOT_A_NON_EMPTY_STRING", f"expected a non-empty string, got {value!r}", path)
    return value


def _int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StructureError("NOT_AN_INTEGER", f"expected an integer, got {value!r}", path)
    return value


def _seq(node: dict[str, Any], key: str, path: str) -> list[Any]:
    value = node.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise StructureError("NOT_AN_ARRAY", f"expected an array, got {type(value).__name__}", f"{path}.{key}")
    return value


def _point(node: Any, path: str) -> Point3:
    obj = _obj(node, path)
    return Point3(
        x=_finite(_req(obj, "x", path), f"{path}.x"),
        y=_finite(_req(obj, "y", path), f"{path}.y"),
        z=_finite(_req(obj, "z", path), f"{path}.z"),
    )


def _note(node: Any, path: str) -> Note:
    obj = _obj(node, path)
    return Note(
        text=_text(_req(obj, "text", path), f"{path}.text"),
        code=obj.get("code"),
        message_key=obj.get("messageKey"),
        body_ids=tuple(str(v) for v in _seq(obj, "bodyIds", path)),
        element_ids=tuple(_int(v, f"{path}.elementIds[]") for v in _seq(obj, "elementIds", path)),
    )


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# ─── Parsing ─────────────────────────────────────────────────────────────


def parse_handoff_text(text: str, *, sha256: str, size_bytes: int) -> Handoff:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructureError("MALFORMED_JSON", f"document is not valid JSON: {exc}", None) from exc
    return parse_handoff_object(raw, sha256=sha256, size_bytes=size_bytes)


def load_handoff(path: Path) -> Handoff:
    """Read, hash and parse a handoff document from disk."""
    import hashlib

    resolved = Path(path).expanduser()
    payload = resolved.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    return parse_handoff_text(
        payload.decode("utf-8"), sha256=digest, size_bytes=len(payload)
    )


def parse_handoff_object(raw: Any, *, sha256: str, size_bytes: int) -> Handoff:
    root = _obj(raw, "$")

    # Contract identity first. Everything after this assumes the field names
    # mean what this format says they mean, so an unknown contract must not be
    # parsed on the chance that it happens to look similar.
    version = root.get("schemaVersion")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractError(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"schema version {version!r} is not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})",
            "schemaVersion",
        )
    # The name must AGREE with the version, in both directions. A document claiming
    # ``RcCadHandoffV1`` at version 2 is refused rather than resolved by trusting whichever field
    # the reader happened to look at first — the two self-descriptions disagreeing is itself the
    # defect, and picking one would be wrong half the time.
    schema = root.get("schema")
    expected_contract = CONTRACTS[int(version)]
    if schema != expected_contract:
        raise ContractError(
            "UNKNOWN_CONTRACT",
            f"schema version {version!r} must declare contract {expected_contract!r}, "
            f"got {schema!r}",
            "schema",
        )

    generator = _obj(_req(root, "generator", "$"), "generator")
    subject = _obj(_req(root, "subject", "$"), "subject")
    assembly = _obj(_req(root, "assembly", "$"), "assembly")
    units = _obj(_req(root, "units", "$"), "units")
    coords = _obj(_req(root, "coordinateSystem", "$"), "coordinateSystem")
    concrete = _obj(_req(root, "concrete", "$"), "concrete")
    reinforcement = _obj(_req(root, "reinforcement", "$"), "reinforcement")
    requirements = _obj(_req(root, "requirements", "$"), "requirements")

    _check_units_and_frame(units, coords)

    bodies = tuple(
        _parse_body(node, f"concrete.bodies[{i}]")
        for i, node in enumerate(_seq(concrete, "bodies", "concrete"))
    )
    if not bodies:
        raise StructureError("NO_CONCRETE_BODIES", "at least one concrete body is required", "concrete.bodies")

    interfaces = tuple(
        _parse_interface(node, f"concrete.interfaces[{i}]")
        for i, node in enumerate(_seq(concrete, "interfaces", "concrete"))
    )

    bars = tuple(
        _parse_bar(node, f"reinforcement.bars[{i}]")
        for i, node in enumerate(_seq(reinforcement, "bars", "reinforcement"))
    )
    marks = tuple(
        _parse_mark(node, f"reinforcement.marks[{i}]")
        for i, node in enumerate(_seq(reinforcement, "marks", "reinforcement"))
    )
    families = tuple(
        _parse_family(node, f"assembly.families[{i}]", int(version))
        for i, node in enumerate(_seq(assembly, "families", "assembly"))
    )
    cover = tuple(
        _parse_cover(node, f"requirements.cover[{i}]")
        for i, node in enumerate(_seq(requirements, "cover", "requirements"))
    )
    spacing = tuple(
        _parse_clear_spacing(node, f"requirements.clearSpacing[{i}]")
        for i, node in enumerate(_seq(requirements, "clearSpacing", "requirements"))
    )
    checks = tuple(
        _parse_check(node, f"checks[{i}]")
        for i, node in enumerate(_seq(root, "checks", "$"))
    )

    revisions_node = _obj(_req(root, "revisions", "$"), "revisions")
    revisions = {
        key: _int(value, f"revisions.{key}") for key, value in sorted(revisions_node.items())
    }
    for required_revision in ("detailing", "demand"):
        if required_revision not in revisions:
            raise StructureError(
                "MISSING_REVISION",
                f"revision {required_revision!r} is required for staleness detection",
                f"revisions.{required_revision}",
            )

    source = root.get("source") or {}
    project_node = root.get("project") or {}

    handoff = Handoff(
        schema_version=int(version),
        generator_name=_text(_req(generator, "name", "generator"), "generator.name"),
        generator_version=_text(_req(generator, "version", "generator"), "generator.version"),
        subject_kind=_text(_req(subject, "kind", "subject"), "subject.kind"),
        subject_entity_id=_int(_req(subject, "entityId", "subject"), "subject.entityId"),
        subject_name=_text(_req(subject, "name", "subject"), "subject.name"),
        assembly_kind=_text(_req(assembly, "kind", "assembly"), "assembly.kind"),
        assembly_completeness=_text(
            _req(assembly, "completeness", "assembly"), "assembly.completeness"
        ),
        families=families,
        revisions=revisions,
        certificate=_obj(_req(root, "certificate", "$"), "certificate"),
        project={k: str(v) for k, v in sorted(project_node.items())},
        bodies=bodies,
        interfaces=interfaces,
        bars=bars,
        marks=marks,
        cover_requirements=cover,
        clear_spacing_requirements=spacing,
        checks=checks,
        assumptions=tuple(
            _note(n, f"assumptions[{i}]") for i, n in enumerate(_seq(root, "assumptions", "$"))
        ),
        unsupported=tuple(
            _note(n, f"unsupported[{i}]") for i, n in enumerate(_seq(root, "unsupported", "$"))
        ),
        source_git_revision=_str_or_none((source or {}).get("gitRevision")),
        manifest_sha256=sha256,
        manifest_bytes=size_bytes,
        # V2 only. A V1 document states no statuses block, and inventing one would be this
        # consumer asserting verdicts the producer never made.
        statuses=(
            _parse_statuses(_req(root, "statuses", "$"), "statuses")
            if int(version) >= 2
            else None
        ),
        bottom_mat_layer_order=_parse_layer_order(
            assembly.get("bottomMatLayerOrder"), "assembly.bottomMatLayerOrder"
        )
        if int(version) >= 2
        else None,
    )

    validate_semantics(handoff)
    return handoff


def _check_units_and_frame(units: dict[str, Any], coords: dict[str, Any]) -> None:
    """Refuse a document whose units or frame are not the ones this consumer reads.

    A rescaled or Y-up document would produce geometry that looks plausible and
    is wrong, which is worse than a refusal.
    """
    expected_units = {"length": "m", "angle": "deg", "barDiameter": "mm", "mass": "kg"}
    for key, want in expected_units.items():
        got = units.get(key)
        if got != want:
            raise ContractError(
                "UNSUPPORTED_UNIT", f"expected units.{key} == {want!r}, got {got!r}", f"units.{key}"
            )
    if coords.get("up") != "Z":
        raise ContractError(
            "UNSUPPORTED_FRAME", f"expected Z-up, got up={coords.get('up')!r}", "coordinateSystem.up"
        )
    if coords.get("handedness") != "right":
        raise ContractError(
            "UNSUPPORTED_FRAME",
            f"expected a right-handed frame, got {coords.get('handedness')!r}",
            "coordinateSystem.handedness",
        )


def _parse_box(node: Any, path: str) -> BoxShape:
    obj = _obj(node, path)
    kind = _req(obj, "kind", path)
    if kind != "box":
        raise StructureError(
            "UNSUPPORTED_SHAPE",
            f"only explicit 'box' primitives are supported, got {kind!r}; a solid is never inferred",
            f"{path}.kind",
        )
    return BoxShape(
        b=_positive(_req(obj, "B", path), f"{path}.B"),
        length=_positive(_req(obj, "L", path), f"{path}.L"),
        height=_positive(_req(obj, "height", path), f"{path}.height"),
        centre=_point(_req(obj, "centre", path), f"{path}.centre"),
        rotation_deg=_finite(_req(obj, "rotationDeg", path), f"{path}.rotationDeg"),
    )


def _parse_body(node: Any, path: str) -> ConcreteBody:
    obj = _obj(node, path)
    source = _obj(_req(obj, "source", path), f"{path}.source")
    element_id = obj.get("elementId")
    return ConcreteBody(
        body_id=_text(_req(obj, "bodyId", path), f"{path}.bodyId"),
        role=_text(_req(obj, "role", path), f"{path}.role"),
        shape=_parse_box(_req(obj, "shape", path), f"{path}.shape"),
        truncated_faces=tuple(str(v) for v in _seq(obj, "truncatedFaces", path)),
        source_kind=_text(_req(source, "kind", f"{path}.source"), f"{path}.source.kind"),
        source_id=_int(_req(source, "id", f"{path}.source"), f"{path}.source.id"),
        source_name=_str_or_none(source.get("name")),
        element_id=None if element_id is None else _int(element_id, f"{path}.elementId"),
        material_ref=_str_or_none(obj.get("materialRef")),
    )


def _parse_interface(node: Any, path: str) -> ConcreteInterface:
    obj = _obj(node, path)
    participants = _obj(_req(obj, "participants", path), f"{path}.participants")
    geometry = _obj(_req(obj, "geometry", path), f"{path}.geometry")
    geometry_kind = _req(geometry, "kind", f"{path}.geometry")
    if geometry_kind != "planarRectZ":
        raise StructureError(
            "UNSUPPORTED_INTERFACE_GEOMETRY",
            f"only 'planarRectZ' interface geometry is supported, got {geometry_kind!r}",
            f"{path}.geometry.kind",
        )
    passage = obj.get("intentionalBarPassage") or {}
    return ConcreteInterface(
        interface_id=_text(_req(obj, "interfaceId", path), f"{path}.interfaceId"),
        kind=_text(_req(obj, "kind", path), f"{path}.kind"),
        below_body_id=_text(
            _req(participants, "belowBodyId", f"{path}.participants"),
            f"{path}.participants.belowBodyId",
        ),
        above_body_id=_text(
            _req(participants, "aboveBodyId", f"{path}.participants"),
            f"{path}.participants.aboveBodyId",
        ),
        exposure=_text(_req(obj, "exposure", path), f"{path}.exposure"),
        elevation=_finite(_req(geometry, "elevation", f"{path}.geometry"), f"{path}.geometry.elevation"),
        b=_positive(_req(geometry, "B", f"{path}.geometry"), f"{path}.geometry.B"),
        length=_positive(_req(geometry, "L", f"{path}.geometry"), f"{path}.geometry.L"),
        centre=_point(_req(geometry, "centre", f"{path}.geometry"), f"{path}.geometry.centre"),
        rotation_deg=_finite(
            _req(geometry, "rotationDeg", f"{path}.geometry"), f"{path}.geometry.rotationDeg"
        ),
        intentional_bar_ids=tuple(str(v) for v in (passage.get("barIds") or [])),
        intentional_reason_key=_str_or_none(passage.get("reasonKey")),
    )


def _parse_segment(node: Any, path: str) -> BarSegment:
    obj = _obj(node, path)
    kind = _text(_req(obj, "kind", path), f"{path}.kind")
    if kind not in {"straight", "arc"}:
        raise StructureError(
            "UNSUPPORTED_SEGMENT_KIND", f"unknown segment kind {kind!r}", f"{path}.kind"
        )
    centre_node = obj.get("centre")
    radius = obj.get("radius")
    sweep = obj.get("sweepDeg")
    return BarSegment(
        kind=kind,
        start=_point(_req(obj, "start", path), f"{path}.start"),
        end=_point(_req(obj, "end", path), f"{path}.end"),
        length=_positive(_req(obj, "length", path), f"{path}.length"),
        radius=None if radius is None else _positive(radius, f"{path}.radius"),
        sweep_deg=None if sweep is None else _finite(sweep, f"{path}.sweepDeg"),
        centre=None if centre_node is None else _point(centre_node, f"{path}.centre"),
        arc_approximated=bool(obj.get("arcApproximated", False)),
    )


def _parse_bar(node: Any, path: str) -> Bar:
    obj = _obj(node, path)
    segments = tuple(
        _parse_segment(s, f"{path}.segments[{i}]")
        for i, s in enumerate(_seq(obj, "segments", path))
    )
    if not segments:
        raise StructureError("NO_SEGMENTS", "a bar needs at least one segment", f"{path}.segments")
    start_treatment = _obj(_req(obj, "startTreatment", path), f"{path}.startTreatment")
    end_treatment = _obj(_req(obj, "endTreatment", path), f"{path}.endTreatment")
    owners = _seq(obj, "ownerElementIds", path)
    if not owners:
        raise StructureError(
            "NO_OWNER_ELEMENTS",
            "a bar must name at least one owning element for traceability",
            f"{path}.ownerElementIds",
        )
    return Bar(
        bar_id=_text(_req(obj, "id", path), f"{path}.id"),
        diameter_mm=_positive(_req(obj, "diameterMm", path), f"{path}.diameterMm"),
        role=_text(_req(obj, "role", path), f"{path}.role"),
        family_id=_text(_req(obj, "familyId", path), f"{path}.familyId"),
        segments=segments,
        cutting_length=_positive(_req(obj, "cuttingLength", path), f"{path}.cuttingLength"),
        owner_element_ids=tuple(_int(v, f"{path}.ownerElementIds[]") for v in owners),
        mark=_str_or_none(obj.get("mark")),
        layer_id=_str_or_none(obj.get("layerId")),
        start_treatment_kind=_text(
            _req(start_treatment, "kind", f"{path}.startTreatment"), f"{path}.startTreatment.kind"
        ),
        end_treatment_kind=_text(
            _req(end_treatment, "kind", f"{path}.endTreatment"), f"{path}.endTreatment.kind"
        ),
    )


def _parse_mark(node: Any, path: str) -> Mark:
    obj = _obj(node, path)
    mass = obj.get("massKg")
    return Mark(
        mark=_text(_req(obj, "mark", path), f"{path}.mark"),
        diameter_mm=_positive(_req(obj, "diameterMm", path), f"{path}.diameterMm"),
        cutting_length=_positive(_req(obj, "cuttingLength", path), f"{path}.cuttingLength"),
        quantity=_int(_req(obj, "quantity", path), f"{path}.quantity"),
        role=_str_or_none(obj.get("role")),
        bar_ids=tuple(str(v) for v in _seq(obj, "barIds", path)),
        mass_kg=None if mass is None else _finite(mass, f"{path}.massKg"),
        shape=_str_or_none(obj.get("shape")),
    )


def _parse_mat_region(node: Any, path: str) -> MatRegion:
    obj = _obj(node, path)
    return MatRegion(
        kind=_text(_req(obj, "kind", path), f"{path}.kind"),
        bar_ids=tuple(str(v) for v in _seq(obj, "barIds", path)),
        spacing_centre=_finite(_req(obj, "spacingCentre", path), f"{path}.spacingCentre"),
        spacing_clear=_finite(_req(obj, "spacingClear", path), f"{path}.spacingClear"),
        width=_finite(_req(obj, "width", path), f"{path}.width"),
        centre_offset=_finite(_req(obj, "centreOffset", path), f"{path}.centreOffset"),
    )


def _parse_mat_detail(node: Any, path: str) -> MatFamilyDetail:
    obj = _obj(node, path)
    return MatFamilyDetail(
        direction=_text(_req(obj, "direction", path), f"{path}.direction"),
        layer=_text(_req(obj, "layer", path), f"{path}.layer"),
        axis_elevation=_finite(_req(obj, "axisElevation", path), f"{path}.axisElevation"),
        clear_cover_to_soffit=_finite(
            _req(obj, "clearCoverToSoffit", path), f"{path}.clearCoverToSoffit"
        ),
        regions=tuple(
            _parse_mat_region(r, f"{path}.regions[{i}]")
            for i, r in enumerate(_seq(obj, "regions", path))
        ),
    )


def _parse_tie_detail(node: Any, path: str) -> TieFamilyDetail:
    obj = _obj(node, path)
    return TieFamilyDetail(
        legs_contributed=_int(_req(obj, "legsContributed", path), f"{path}.legsContributed"),
        stations=tuple(_finite(v, f"{path}.stations[]") for v in _seq(obj, "stations", path)),
    )


def _parse_family(node: Any, path: str, version: int) -> ReinforcementFamily:
    obj = _obj(node, path)
    kind = _text(_req(obj, "kind", path), f"{path}.kind")
    allowed = FAMILY_KINDS[version]
    if kind not in allowed:
        raise ContractError(
            "UNKNOWN_FAMILY_KIND",
            f"family kind {kind!r} is not declared by schema version {version} "
            f"(declared: {sorted(allowed)})",
            f"{path}.kind",
        )
    mat_node = obj.get("mat")
    tie_node = obj.get("tie")
    # The detail must MATCH the kind, both ways. A mat family with no mat detail cannot state its
    # layer, and a dowel family carrying mat detail is describing itself as two things.
    if kind in MAT_FAMILY_KINDS and mat_node is None:
        raise StructureError(
            "MAT_FAMILY_WITHOUT_DETAIL",
            f"family {kind!r} must carry its mat detail: direction, layer, elevation and regions",
            f"{path}.mat",
        )
    if kind not in MAT_FAMILY_KINDS and mat_node is not None:
        raise StructureError(
            "MAT_DETAIL_ON_NON_MAT_FAMILY",
            f"family {kind!r} carries mat detail but is not a bottom-mat family",
            f"{path}.mat",
        )
    if kind in TIE_FAMILY_KINDS and version >= 2 and tie_node is None:
        raise StructureError(
            "TIE_FAMILY_WITHOUT_DETAIL",
            f"family {kind!r} must carry its tie detail",
            f"{path}.tie",
        )
    mat = _parse_mat_detail(mat_node, f"{path}.mat") if mat_node is not None else None
    if mat is not None:
        expected_direction = "X" if kind == "footingBottomMatX" else "Y"
        if mat.direction != expected_direction:
            raise StructureError(
                "MAT_DIRECTION_MISMATCH",
                f"family {kind!r} states direction {mat.direction!r}; the two cannot disagree",
                f"{path}.mat.direction",
            )
    return ReinforcementFamily(
        family_id=_text(_req(obj, "familyId", path), f"{path}.familyId"),
        kind=kind,
        purpose_key=_text(_req(obj, "purposeKey", path), f"{path}.purposeKey"),
        bar_ids=tuple(str(v) for v in _seq(obj, "barIds", path)),
        mat=mat,
        tie=_parse_tie_detail(tie_node, f"{path}.tie") if tie_node is not None else None,
    )


def _parse_layer_order(node: Any, path: str) -> BottomMatLayerOrder | None:
    """``None`` is a real answer: the producer modelled no mat.

    Distinguished from a MISSING field, which is a malformed V2 document — V2 requires the key and
    permits ``null`` as its value, so absence and "no mat" are not the same statement.
    """
    if node is None:
        return None
    obj = _obj(node, path)
    return BottomMatLayerOrder(
        lower_direction=_text(_req(obj, "lowerDirection", path), f"{path}.lowerDirection"),
        resolution=_text(_req(obj, "resolution", path), f"{path}.resolution"),
    )


def _parse_statuses(node: Any, path: str) -> Statuses:
    obj = _obj(node, path)
    constructible = _req(obj, "constructible", path)
    if not isinstance(constructible, bool):
        raise StructureError(
            "NON_BOOLEAN_CONSTRUCTIBLE",
            f"expected a boolean, got {type(constructible).__name__}",
            f"{path}.constructible",
        )
    return Statuses(
        constructible=constructible,
        constructibility_blockers=tuple(
            str(v) for v in _seq(obj, "constructibilityBlockers", path)
        ),
        bottom_flexure=_text(_req(obj, "bottomFlexure", path), f"{path}.bottomFlexure"),
        bottom_mat_geometry=_text(
            _req(obj, "bottomMatGeometry", path), f"{path}.bottomMatGeometry"
        ),
        bottom_anchorage=_text(_req(obj, "bottomAnchorage", path), f"{path}.bottomAnchorage"),
        top_reinforcement=_text(
            _req(obj, "topReinforcement", path), f"{path}.topReinforcement"
        ),
        punching_moment_transfer=_text(
            _req(obj, "punchingMomentTransfer", path), f"{path}.punchingMomentTransfer"
        ),
    )


def _parse_cover(node: Any, path: str) -> CoverRequirement:
    obj = _obj(node, path)
    scope_node = _obj(_req(obj, "measurementScope", path), f"{path}.measurementScope")
    provenance = _obj(_req(obj, "provenance", path), f"{path}.provenance")
    surface = obj.get("surface") or {}
    return CoverRequirement(
        requirement_id=_text(_req(obj, "requirementId", path), f"{path}.requirementId"),
        element_id=_int(_req(obj, "elementId", path), f"{path}.elementId"),
        element_type=_text(_req(obj, "elementType", path), f"{path}.elementType"),
        applies_to_body_ids=tuple(str(v) for v in _seq(obj, "appliesToBodyIds", path)),
        applies_to_bar_ids=tuple(str(v) for v in _seq(obj, "appliesToBarIds", path)),
        measurement_scope=MeasurementScope(
            within_body_id=_text(
                _req(scope_node, "withinBodyId", f"{path}.measurementScope"),
                f"{path}.measurementScope.withinBodyId",
            ),
            exclude_interface_ids=tuple(
                str(v) for v in _seq(scope_node, "excludeInterfaceIds", f"{path}.measurementScope")
            ),
            exclude_truncated_faces=bool(scope_node.get("excludeTruncatedFaces", False)),
        ),
        distance=_finite(_req(obj, "distance", path), f"{path}.distance"),
        category=_text(_req(obj, "category", path), f"{path}.category"),
        provenance_source=_text(
            _req(provenance, "source", f"{path}.provenance"), f"{path}.provenance.source"
        ),
        surface_face=_str_or_none(surface.get("face")),
    )


def _parse_clear_spacing(node: Any, path: str) -> ClearSpacingRequirement:
    obj = _obj(node, path)
    role_pair_node = obj.get("appliesToRolePair") or {}
    role_pair: tuple[str, str] | None = None
    if role_pair_node:
        role_pair = (
            _text(role_pair_node.get("roleA"), f"{path}.appliesToRolePair.roleA"),
            _text(role_pair_node.get("roleB"), f"{path}.appliesToRolePair.roleB"),
        )
    governing = role_pair_node.get("governingBarDiameterMm")
    reportable = obj.get("reportable")
    return ClearSpacingRequirement(
        requirement_id=_text(_req(obj, "requirementId", path), f"{path}.requirementId"),
        distance=_finite(_req(obj, "distance", path), f"{path}.distance"),
        category=_text(_req(obj, "category", path), f"{path}.category"),
        bar_id_a=_str_or_none(obj.get("barIdA")),
        bar_id_b=_str_or_none(obj.get("barIdB")),
        pair_class=_str_or_none(obj.get("pairClass")),
        reportable=None if reportable is None else bool(reportable),
        role_pair=role_pair,
        member_kind=_str_or_none(role_pair_node.get("memberKind")),
        governing_diameter_mm=(
            None if governing is None else _positive(governing, f"{path}.appliesToRolePair.governingBarDiameterMm")
        ),
    )


def _parse_finding(node: Any, path: str) -> Finding:
    obj = _obj(node, path)
    at = obj.get("at")
    return Finding(
        finding_id=_text(_req(obj, "findingId", path), f"{path}.findingId"),
        severity=_text(_req(obj, "severity", path), f"{path}.severity"),
        bar_id_a=_str_or_none(obj.get("barIdA")),
        bar_id_b=_str_or_none(obj.get("barIdB")),
        pair_class=_str_or_none(obj.get("pairClass")),
        at=None if at is None else _point(at, f"{path}.at"),
        measured=None if obj.get("measured") is None else _finite(obj["measured"], f"{path}.measured"),
        required=None if obj.get("required") is None else _finite(obj["required"], f"{path}.required"),
        shortfall=None if obj.get("shortfall") is None else _finite(obj["shortfall"], f"{path}.shortfall"),
    )


def _parse_check(node: Any, path: str) -> Check:
    obj = _obj(node, path)
    scope = obj.get("scope") or {}
    policy = _text(
        _req(obj, "consumerObservationPolicy", path), f"{path}.consumerObservationPolicy"
    )
    if policy not in KNOWN_POLICIES:
        # An unknown policy is the one case where guessing is most tempting and
        # most dangerous: the safe-looking default (measure anyway) is exactly
        # what OUT_OF_SCOPE forbids.
        raise ContractError(
            "UNKNOWN_OBSERVATION_POLICY",
            f"unknown consumerObservationPolicy {policy!r}; refusing rather than assuming a default",
            f"{path}.consumerObservationPolicy",
        )
    evaluation_status = _text(_req(obj, "evaluationStatus", path), f"{path}.evaluationStatus")
    if evaluation_status not in {"EVALUATED", "NOT_EVALUATED"}:
        raise StructureError(
            "UNKNOWN_EVALUATION_STATUS",
            f"unknown evaluationStatus {evaluation_status!r}",
            f"{path}.evaluationStatus",
        )
    return Check(
        check_id=_text(_req(obj, "checkId", path), f"{path}.checkId"),
        check_kind=_text(_req(obj, "checkKind", path), f"{path}.checkKind"),
        authority=_text(_req(obj, "authority", path), f"{path}.authority"),
        evaluation_status=evaluation_status,
        consumer_observation_policy=policy,
        requirement_ids=tuple(str(v) for v in _seq(obj, "requirementIds", path)),
        findings=tuple(
            _parse_finding(f, f"{path}.findings[{i}]")
            for i, f in enumerate(_seq(obj, "findings", path))
        ),
        not_evaluated_reason=_str_or_none(obj.get("notEvaluatedReason")),
        not_evaluated_code=_str_or_none(obj.get("notEvaluatedCode")),
        scope_bar_ids=tuple(str(v) for v in _seq(scope, "barIds", f"{path}.scope")),
        scope_body_ids=tuple(str(v) for v in _seq(scope, "bodyIds", f"{path}.scope")),
        scope_interface_ids=tuple(str(v) for v in _seq(scope, "interfaceIds", f"{path}.scope")),
        scope_element_ids=tuple(
            _int(v, f"{path}.scope.elementIds[]") for v in _seq(scope, "elementIds", f"{path}.scope")
        ),
    )


# ─── Semantic validation ─────────────────────────────────────────────────


def validate_semantics(h: Handoff) -> None:
    """Cross-reference checks the per-field readers cannot make.

    Structural validity is not enough: a document whose ids do not resolve
    describes a cage this consumer cannot key a review to.
    """
    body_ids = {b.body_id for b in h.bodies}
    bar_ids = {b.bar_id for b in h.bars}
    interface_ids = {i.interface_id for i in h.interfaces}
    family_ids = {f.family_id for f in h.families}

    if len(body_ids) != len(h.bodies):
        raise StructureError("DUPLICATE_BODY_ID", "concrete body ids must be unique", "concrete.bodies")
    if len(bar_ids) != len(h.bars):
        raise StructureError("DUPLICATE_BAR_ID", "bar ids must be unique", "reinforcement.bars")
    if len(interface_ids) != len(h.interfaces):
        raise StructureError(
            "DUPLICATE_INTERFACE_ID", "interface ids must be unique", "concrete.interfaces"
        )

    for bar in h.bars:
        if bar.family_id not in family_ids:
            raise StructureError(
                "UNKNOWN_FAMILY_REFERENCE",
                f"bar {bar.bar_id!r} names family {bar.family_id!r}, which is not declared",
                "reinforcement.bars[].familyId",
            )

    for fam in h.families:
        for bid in fam.bar_ids:
            if bid not in bar_ids:
                raise StructureError(
                    "UNKNOWN_BAR_REFERENCE",
                    f"family {fam.family_id!r} names unknown bar {bid!r}",
                    "assembly.families[].barIds",
                )

    for mark in h.marks:
        for bid in mark.bar_ids:
            if bid not in bar_ids:
                raise StructureError(
                    "UNKNOWN_BAR_REFERENCE",
                    f"mark {mark.mark!r} names unknown bar {bid!r}",
                    "reinforcement.marks[].barIds",
                )

    for iface in h.interfaces:
        for role, ref in (("belowBodyId", iface.below_body_id), ("aboveBodyId", iface.above_body_id)):
            if ref not in body_ids:
                raise StructureError(
                    "UNKNOWN_BODY_REFERENCE",
                    f"interface {iface.interface_id!r} names unknown body {ref!r}",
                    f"concrete.interfaces[].participants.{role}",
                )
        if iface.kind == "concreteToConcrete" and iface.exposure != "internal":
            # Stated rather than implied by the producer, and enforced here:
            # treating this contact as exposed is what turns a correct dowel
            # into a false cover breach.
            raise StructureError(
                "INTERNAL_INTERFACE_MISDECLARED",
                f"a concrete-to-concrete contact must declare exposure 'internal', "
                f"got {iface.exposure!r}",
                "concrete.interfaces[].exposure",
            )
        for bid in iface.intentional_bar_ids:
            if bid not in bar_ids:
                raise StructureError(
                    "UNKNOWN_BAR_REFERENCE",
                    f"interface {iface.interface_id!r} lists unknown passage bar {bid!r}",
                    "concrete.interfaces[].intentionalBarPassage.barIds",
                )

    requirement_ids = {r.requirement_id for r in h.cover_requirements}
    requirement_ids |= {r.requirement_id for r in h.clear_spacing_requirements}

    for req in h.cover_requirements:
        for bid in req.applies_to_body_ids:
            if bid not in body_ids:
                raise StructureError(
                    "UNKNOWN_BODY_REFERENCE",
                    f"cover requirement {req.requirement_id!r} names unknown body {bid!r}",
                    "requirements.cover[].appliesToBodyIds",
                )
        for bid in req.applies_to_bar_ids:
            if bid not in bar_ids:
                raise StructureError(
                    "UNKNOWN_BAR_REFERENCE",
                    f"cover requirement {req.requirement_id!r} names unknown bar {bid!r}",
                    "requirements.cover[].appliesToBarIds",
                )
        if req.measurement_scope.within_body_id not in body_ids:
            raise StructureError(
                "UNKNOWN_BODY_REFERENCE",
                f"cover requirement {req.requirement_id!r} scopes measurement to unknown body "
                f"{req.measurement_scope.within_body_id!r}",
                "requirements.cover[].measurementScope.withinBodyId",
            )
        for iid in req.measurement_scope.exclude_interface_ids:
            if iid not in interface_ids:
                raise StructureError(
                    "UNKNOWN_INTERFACE_REFERENCE",
                    f"cover requirement {req.requirement_id!r} excludes unknown interface {iid!r}",
                    "requirements.cover[].measurementScope.excludeInterfaceIds",
                )

    for check in h.checks:
        for bid in check.scope_bar_ids:
            if bid not in bar_ids:
                raise StructureError(
                    "UNKNOWN_BAR_REFERENCE",
                    f"check {check.check_id!r} scopes unknown bar {bid!r}",
                    "checks[].scope.barIds",
                )
        for bid in check.scope_body_ids:
            if bid not in body_ids:
                raise StructureError(
                    "UNKNOWN_BODY_REFERENCE",
                    f"check {check.check_id!r} scopes unknown body {bid!r}",
                    "checks[].scope.bodyIds",
                )
        for iid in check.scope_interface_ids:
            if iid not in interface_ids:
                raise StructureError(
                    "UNKNOWN_INTERFACE_REFERENCE",
                    f"check {check.check_id!r} scopes unknown interface {iid!r}",
                    "checks[].scope.interfaceIds",
                )
        for rid in check.requirement_ids:
            if rid not in requirement_ids:
                raise StructureError(
                    "UNKNOWN_REQUIREMENT_REFERENCE",
                    f"check {check.check_id!r} names unknown requirement {rid!r}",
                    "checks[].requirementIds",
                )
        if not check.evaluated and not check.not_evaluated_reason:
            raise StructureError(
                "MISSING_NOT_EVALUATED_REASON",
                f"check {check.check_id!r} is NOT_EVALUATED without naming a reason",
                "checks[].notEvaluatedReason",
            )
        if not check.evaluated and check.findings:
            raise StructureError(
                "FINDINGS_WITHOUT_EVALUATION",
                f"check {check.check_id!r} is NOT_EVALUATED but carries findings",
                "checks[].findings",
            )
        if check.may_cross_check and not check.evaluated:
            # Inviting a cross-check where no verdict exists would make any CAD
            # measurement look like a confirmation of one.
            raise StructureError(
                "CROSS_CHECK_WITHOUT_VERDICT",
                f"check {check.check_id!r} invites MAY_CROSS_CHECK but was not evaluated",
                "checks[].consumerObservationPolicy",
            )
        for finding in check.findings:
            for bid in (finding.bar_id_a, finding.bar_id_b):
                if bid is not None and bid not in bar_ids:
                    raise StructureError(
                        "UNKNOWN_BAR_REFERENCE",
                        f"finding {finding.finding_id!r} names unknown bar {bid!r}",
                        "checks[].findings[].barIdA/barIdB",
                    )

    _validate_arc_claims(h)


def _validate_arc_claims(h: Handoff) -> None:
    """An arc must carry what an exact arc needs, or declare that it does not.

    Start, end, radius and sweep do not determine an arc in three dimensions:
    two centres satisfy them in any plane, and the plane itself is free. So a
    missing centre leaves the chord as the only reconstructable curve, and the
    deviation is the full sagitta. The producer is required to say so.
    """
    for bar in h.bars:
        for index, seg in enumerate(bar.segments):
            path = f"reinforcement.bars[{bar.bar_id}].segments[{index}]"
            if not seg.is_arc:
                if seg.centre is not None or seg.radius is not None:
                    raise StructureError(
                        "STRAIGHT_SEGMENT_WITH_ARC_DATA",
                        "a straight segment must not carry arc geometry",
                        path,
                    )
                continue
            if seg.centre is None and not seg.arc_approximated:
                raise StructureError(
                    "ARC_WITHOUT_CENTRE_OR_DECLARATION",
                    "an arc without a centre must declare arcApproximated; "
                    "exactness may never be claimed silently",
                    path,
                )
            if seg.centre is not None and seg.arc_approximated:
                raise StructureError(
                    "ARC_APPROXIMATION_CONTRADICTION",
                    "an arc that supplies a centre must not also declare itself approximated",
                    path,
                )
