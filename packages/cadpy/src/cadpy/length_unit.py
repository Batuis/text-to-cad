"""The single place cadpy decides what a kernel coordinate means.

Callers declare the unit their build123d/OCCT coordinates are authored in, and
every downstream scale factor is derived from that one declaration. Before this
existed, cadpy assumed millimetres unconditionally in two unrelated writers:
``step_export`` stamped a millimetre length unit onto the XCAF document, and
``glb_mesh_payload`` multiplied every vertex by a hardcoded ``0.001``. A
consumer authoring in any other unit got two artifacts that were both wrong, and
nothing in either file said so.

**One number serves both writers.** ``metres_per_source_unit`` is metres per
kernel unit, and that is exactly what each writer needs:

* STEP wants it for ``XCAFDoc_DocumentTool::SetLengthUnit``, which OCCT applies
  as a coordinate scale when it writes the file;
* GLB wants it as the vertex scale, because glTF fixes linear distance at
  metres.

Deriving both from one function is what makes the two artifacts unable to
disagree about physical size. Do not add a second public scale setting.

**Why the parameter is spelled ``Unit | None`` rather than ``Unit = Unit.MM``.**
``Unit`` lives in ``build123d``, and importing any part of build123d executes
``build123d/__init__.py``, which costs ~1.5 s and pulls OCP. ``cadpy.glb`` and
``cadpy.step_export`` deliberately import build123d only inside functions so
that GLB consumers never pay for it — measurably, ``import cadpy.glb`` does not
load build123d today. A ``Unit.MM`` default in a signature would be evaluated at
module import and destroy that. ``None`` therefore *means* ``Unit.MM``, the
millimetre fast path never imports build123d at all, and
``test_length_unit.py`` pins the equivalence so the default cannot drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from build123d.build_enums import Unit

#: Metres per millimetre. The value cadpy assumed unconditionally before the
#: source unit existed, kept as the default so every existing caller is
#: byte-identical. Also the fast path: resolving the default must not import
#: build123d.
_METRES_PER_MM = 0.001


def metres_per_source_unit(source_length_unit: Unit | None = None) -> float:
    """Metres represented by one kernel coordinate unit.

    ``None`` selects the historical default, ``Unit.MM``. Both the STEP length
    unit and the GLB vertex scale are this number, which is what keeps the two
    artifacts in agreement.

    Raises ``TypeError`` for a non-``Unit`` argument and ``ValueError`` for a
    ``Unit`` build123d has no conversion for. Guessing a scale would produce a
    plausible model at the wrong size, which is the defect this module exists
    to prevent.
    """
    if source_length_unit is None:
        return _METRES_PER_MM

    from build123d.build_common import UNITS_PER_METER
    from build123d.build_enums import Unit

    if not isinstance(source_length_unit, Unit):
        raise TypeError(
            "source_length_unit must be a build123d Unit or None (meaning Unit.MM), "
            f"got {type(source_length_unit).__name__}"
        )
    try:
        per_metre = UNITS_PER_METER[source_length_unit]
    except KeyError:
        raise ValueError(
            f"build123d declares no metre conversion for {source_length_unit!r}; "
            "refusing to guess a scale"
        ) from None
    if not isinstance(per_metre, (int, float)) or per_metre <= 0.0:
        raise ValueError(
            f"build123d reports a non-positive conversion {per_metre!r} for "
            f"{source_length_unit!r}; refusing to guess a scale"
        )
    return 1.0 / float(per_metre)


def resolve_source_length_unit(source_length_unit: Unit | None = None) -> Unit:
    """The concrete ``Unit``, resolving ``None`` to ``Unit.MM``.

    For callers that need the enum itself rather than its scale. This one does
    import build123d even for the default, so prefer
    ``metres_per_source_unit`` on paths that must stay build123d-free.
    """
    from build123d.build_enums import Unit

    if source_length_unit is None:
        return Unit.MM
    if not isinstance(source_length_unit, Unit):
        raise TypeError(
            "source_length_unit must be a build123d Unit or None (meaning Unit.MM), "
            f"got {type(source_length_unit).__name__}"
        )
    return source_length_unit


def source_length_unit_name(source_length_unit: Unit | None = None) -> str:
    """Short name for metadata, e.g. ``"mm"`` or ``"m"``.

    Lets a producer record the unit it declared without re-deriving it, and
    without a second source of truth about what the default is.
    """
    return resolve_source_length_unit(source_length_unit).name.lower()
