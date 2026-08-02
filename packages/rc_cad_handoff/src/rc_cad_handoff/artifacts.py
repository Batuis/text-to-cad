"""STEP and GLB generation, using cadpy's existing writers unchanged.

Body naming is the reviewer's only handle on which solid is which, so it is
derived from the producer's stable ids and never from array position:
``concrete:<bodyId>`` and ``bar:<barId>``.

**Determinism, stated rather than claimed.** The GLB writer is byte-reproducible.
The STEP writer is not: OCCT's ``APIHeaderSection_MakeHeader`` stamps the current
time into the ISO-10303-21 ``FILE_NAME`` header. Nothing here rewrites that
header to manufacture a stable hash. Instead both hashes are recorded — the file
hash, and a *content* hash taken with the header timestamp normalised — so a
reviewer can see that the geometry, topology and names are identical across runs
while the header is not.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from build123d import Compound, Solid, Unit

from cadpy.file_metadata import text_to_cad_identity_metadata
from cadpy.glb import export_native_glb_from_scene
from cadpy.length_unit import metres_per_source_unit, source_length_unit_name
from cadpy.step_export import export_build123d_step_scene
from cadpy.step_scene import mesh_step_scene, scene_export_shape

from .geometry import RealisedModel, as_solid
from .manifest import Handoff

#: The unit this consumer authors OCCT coordinates in.
#:
#: ``RcCadHandoffV1`` states lengths in metres and this package deliberately
#: does not rescale them (see ``manifest`` module docstring), so the kernel is
#: metre-valued: the 2 m footing is 2.0 units and a Ø16 bar is 0.016. cadpy's
#: writers assumed millimetres unconditionally until this declaration existed,
#: which is why both artifacts used to come out a thousand times small.
#:
#: One value, passed to both writers. It is deliberately not two settings: the
#: STEP and the GLB must never be able to disagree about physical size.
SOURCE_LENGTH_UNIT = Unit.M

#: Mesh tolerances for the GLB. The linear value matches the producer's own
#: collision chord tolerance so the visualisation is no coarser than the
#: geometry the review reasons about. Both are kernel-space, hence metres here,
#: and neither changes with the source-unit declaration — the kernel did not
#: move.
GLB_LINEAR_DEFLECTION = 0.0005
GLB_ANGULAR_DEFLECTION = 0.2

CONCRETE_PREFIX = "concrete:"
BAR_PREFIX = "bar:"

_STEP_TIMESTAMP_RE = re.compile(rb"'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def step_content_hash(step_path: Path) -> str:
    """SHA-256 of a STEP file with the header timestamp normalised."""
    payload = step_path.read_bytes()
    return _sha256(_STEP_TIMESTAMP_RE.sub(b"'<NORMALISED>'", payload))


@dataclass(frozen=True)
class ArtifactRecord:
    path: Path
    size_bytes: int
    sha256: str
    #: Present only where a layer of the file is known to be nondeterministic.
    content_sha256: str | None = None
    nondeterministic_layer: str | None = None

    def as_json(self, *, relative_to: Path | None = None) -> dict[str, object]:
        path = self.path
        if relative_to is not None:
            try:
                path = self.path.relative_to(relative_to)
            except ValueError:
                path = self.path
        out: dict[str, object] = {
            "path": path.as_posix(),
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
        }
        if self.content_sha256 is not None:
            out["contentSha256"] = self.content_sha256
        if self.nondeterministic_layer is not None:
            out["nondeterministicLayer"] = self.nondeterministic_layer
        return out


def unit_contract() -> dict[str, object]:
    """What each boundary of the pipeline measures in, derived not restated.

    Every value here is computed from ``SOURCE_LENGTH_UNIT`` or read from the
    contract, so the review cannot claim a unit the exporter did not use. A
    reader should be able to establish the physical size of the STEP and the
    GLB from this block alone, without opening any source file — the absence
    of exactly that is what let a thousand-fold scale error ship unnoticed.
    """
    metres_per_unit = metres_per_source_unit(SOURCE_LENGTH_UNIT)
    return {
        # What the producer's document states.
        "manifestLength": "m",
        "manifestBarDiameter": "mm",
        # What this consumer hands to OCCT/build123d. Unchanged by the unit
        # correction: the kernel was always metre-valued, only the writers
        # were misinformed.
        "kernelLength": source_length_unit_name(SOURCE_LENGTH_UNIT),
        # What the exporter declared to cadpy, and the two factors derived
        # from that one declaration.
        "sourceLengthUnitDeclaredToCadpy": source_length_unit_name(SOURCE_LENGTH_UNIT),
        "metresPerKernelUnit": metres_per_unit,
        "stepLengthUnitScale": metres_per_unit,
        "glbScale": metres_per_unit,
        # What the files physically contain. The STEP header names millimetres
        # because OCCT always writes AP214 in millimetres; it converts the
        # coordinates by stepLengthUnitScale on the way out, so the geometry is
        # metric-correct and a 2 m footing reads back as 2 m.
        "stepDeclaredLength": "mm",
        "stepPhysicalLength": "m",
        "glbPhysicalLength": "m",
        # What the numbers in this review mean.
        "reviewLength": "m",
        "reviewVolume": "m3",
        "angle": "deg",
        "orientationConversion": "Z-up kernel to Y-up glTF, applied to the GLB only",
    }


@dataclass
class ArtifactSet:
    step: ArtifactRecord
    glb: ArtifactRecord
    assembly_label: str
    body_names: tuple[str, ...]


def build_assembly(handoff: Handoff, model: RealisedModel) -> Compound:
    """One compound whose children are the individually named solids.

    The concrete components stay distinct, named solids even though a compound
    is exported: fusing them would destroy exactly the distinction a reviewer
    needs between footing concrete, column concrete and each bar.
    """
    children: list[Solid] = []

    for body in handoff.bodies:
        geometry = model.concrete[body.body_id]
        children.append(
            Solid(as_solid(geometry.solid), label=f"{CONCRETE_PREFIX}{body.body_id}")
        )

    for bar in handoff.bars:
        geometry = model.bars[bar.bar_id]
        children.append(Solid(as_solid(geometry.solid), label=f"{BAR_PREFIX}{bar.bar_id}"))

    label = f"{handoff.assembly_kind}:{handoff.subject_name}"
    assembly = Compound(children=children)
    assembly.label = label
    return assembly


def write_artifacts(
    handoff: Handoff,
    model: RealisedModel,
    *,
    output_dir: Path,
    stem: str | None = None,
) -> ArtifactSet:
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = stem or _default_stem(handoff)
    step_path = output_dir / f"{slug}.step"
    glb_path = output_dir / f"{slug}.glb"

    assembly = build_assembly(handoff, model)

    # The manifest hash travels into the STEP as the source identity, so an
    # artifact can be tied back to the exact document that produced it.
    identity = text_to_cad_identity_metadata(
        source_path=f"rc-cad-handoff:{handoff.generator_name}@{handoff.generator_version}",
        source_hash=handoff.manifest_sha256,
    )

    # The unit is declared once, here. It reaches the STEP through the XCAF
    # document and travels on the returned scene into the GLB writer, so the
    # two artifacts are structurally incapable of disagreeing about scale.
    scene = export_build123d_step_scene(
        assembly,
        step_path,
        text_to_cad_entry_kind="assembly",
        source_path=identity["sourcePath"],
        source_hash=identity["sourceHash"],
        source_length_unit=SOURCE_LENGTH_UNIT,
    )

    mesh_step_scene(
        scene,
        linear_deflection=GLB_LINEAR_DEFLECTION,
        angular_deflection=GLB_ANGULAR_DEFLECTION,
        relative=False,
    )
    scene_export_shape(scene)
    # Passing the unit again is deliberate rather than redundant: the writer
    # cross-checks it against the scene's own declaration and raises if they
    # differ, so changing one of the two in future fails loudly instead of
    # shipping a STEP and a GLB that are a thousand times apart.
    written_glb = Path(
        export_native_glb_from_scene(
            step_path,
            scene,
            target_path=glb_path,
            linear_deflection=GLB_LINEAR_DEFLECTION,
            angular_deflection=GLB_ANGULAR_DEFLECTION,
            source_length_unit=SOURCE_LENGTH_UNIT,
        )
    )

    step_bytes = step_path.read_bytes()
    glb_bytes = written_glb.read_bytes()

    step_record = ArtifactRecord(
        path=step_path,
        size_bytes=len(step_bytes),
        sha256=_sha256(step_bytes),
        content_sha256=step_content_hash(step_path),
        nondeterministic_layer=(
            "Two layers, both in the STEP writer and neither in the geometry. (1) The "
            "ISO-10303-21 FILE_NAME header carries a wall-clock timestamp from OCCT's "
            "APIHeaderSection_MakeHeader, so it differs on every write; contentSha256 is the "
            "same file with that timestamp normalised. (2) Repeating an export inside one "
            "interpreter shifts NEXT_ASSEMBLY_USAGE_OCCURRENCE numbering, because the writer "
            "keeps process-global state. One export per process — which is what the CLI does — "
            "reproduces contentSha256 exactly. Geometry, topology and body names are stable "
            "across processes; the GLB is byte-identical with no exemption."
        ),
    )
    glb_record = ArtifactRecord(
        path=written_glb,
        size_bytes=len(glb_bytes),
        sha256=_sha256(glb_bytes),
    )

    return ArtifactSet(
        step=step_record,
        glb=glb_record,
        assembly_label=assembly.label,
        body_names=tuple(child.label for child in assembly.children),
    )


def _default_stem(handoff: Handoff) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", handoff.subject_name).strip("-") or "subject"
    detailing = handoff.revisions.get("detailing", 0)
    demand = handoff.revisions.get("demand", 0)
    return f"rc-cad-{slug}-det{detailing}-dem{demand}"
