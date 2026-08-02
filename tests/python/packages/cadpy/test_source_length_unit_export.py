"""STEP and GLB must describe the same physical object.

Written against a deliberately simple shape — a 2 x 2 x 0.5 box — so the
expected physical size is obvious and the assertions state intent rather than
echo whatever the writers produced.

The central property is the last class in this file: if only one half of the
source-unit change lands, STEP and GLB disagree by a factor of a thousand and
``StepGlbPhysicalAgreementTest`` fails. Everything above it localises the
failure.
"""

from __future__ import annotations

import json
import math
import re
import struct
import unittest
from pathlib import Path

import build123d
from build123d.build_enums import Unit

from cadpy.glb import export_native_glb_from_scene
from cadpy.step_export import export_build123d_step_scene
from cadpy.step_scene import mesh_step_scene, scene_export_shape

from tests.python.support.tmp_root import temporary_directory

#: Nominal box, in whatever unit the exporter is told the kernel is in.
BOX = (2.0, 2.0, 0.5)
#: A Ø16 reinforcing bar expressed in metres, the case the RC consumer cares
#: about: small next to the model, and the first thing a wrong scale destroys.
BAR_DIAMETER_M = 0.016

LINEAR_DEFLECTION = 0.0005
ANGULAR_DEFLECTION = 0.2

#: STEP always names millimetres in AP214; the coordinates carry the scale.
_STEP_LENGTH_UNIT_RE = re.compile(
    r"LENGTH_UNIT\(\) NAMED_UNIT\(\*\) SI_UNIT\(\.(\w+)\.,\.METRE\.\)"
)
_STEP_POINT_RE = re.compile(
    r"CARTESIAN_POINT\('[^']*',\(([-0-9.E+]+),([-0-9.E+]+),([-0-9.E+]+)\)\)"
)
_SI_PREFIX_METRES = {"MILLI": 0.001, "CENTI": 0.01, "": 1.0}


def _step_declared_metres_per_unit(step_text: str) -> float:
    prefixes = set(_STEP_LENGTH_UNIT_RE.findall(step_text))
    if not prefixes:
        raise AssertionError("STEP declares no length unit")
    if len(prefixes) != 1:
        raise AssertionError(f"STEP declares conflicting length units: {sorted(prefixes)}")
    return _SI_PREFIX_METRES[prefixes.pop()]


def _step_physical_extent(step_path: Path) -> tuple[float, float, float]:
    """Bounding box of the STEP in metres, via an independent OCCT read-back.

    Deliberately not a text scan of ``CARTESIAN_POINT``. Those cover a box's
    corners but not a cylinder's silhouette — a Ø16 bar exposes only nine
    points and a naive scan reports 8 mm — so reading the file back through a
    real STEP reader is the only measurement that generalises. The reader
    resolves the file's own unit declaration into OCCT's internal millimetres,
    which is then converted once to metres here.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
        raise AssertionError(f"STEP could not be read back: {step_path}")
    reader.TransferRoots()
    box = Bnd_Box()
    BRepBndLib.Add_s(reader.OneShape(), box, True)
    if box.IsVoid():
        raise AssertionError(f"STEP read back with no geometry: {step_path}")
    x0, y0, z0, x1, y1, z1 = box.Get()
    return ((x1 - x0) / 1000.0, (y1 - y0) / 1000.0, (z1 - z0) / 1000.0)


def _glb_document(glb_path: Path) -> tuple[dict, bytes]:
    payload = glb_path.read_bytes()
    _magic, _version, length = struct.unpack_from("<III", payload, 0)
    offset, chunks = 12, []
    while offset < length:
        chunk_length, _chunk_type = struct.unpack_from("<II", payload, offset)
        chunks.append(payload[offset + 8 : offset + 8 + chunk_length])
        offset += 8 + chunk_length
    return json.loads(chunks[0].decode("utf-8")), (chunks[1] if len(chunks) > 1 else b"")


def _glb_world_extent(glb_path: Path) -> tuple[float, float, float]:
    """Bounding box in glTF units, which the spec fixes as metres.

    Walks the node hierarchy rather than reading accessor min/max alone, so a
    scale wrongly applied to node translations is caught as well as one applied
    to vertices.
    """
    document, _ = _glb_document(glb_path)
    minimum = [math.inf] * 3
    maximum = [-math.inf] * 3

    def matrix_of(node: dict) -> list[list[float]]:
        if "matrix" in node:
            m = node["matrix"]
            return [[m[0], m[4], m[8], m[12]],
                    [m[1], m[5], m[9], m[13]],
                    [m[2], m[6], m[10], m[14]],
                    [m[3], m[7], m[11], m[15]]]
        return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]

    def multiply(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    def apply(m, p):
        return [sum(m[i][j] * p[j] for j in range(3)) + m[i][3] for i in range(3)]

    def walk(index: int, parent) -> None:
        node = document["nodes"][index]
        world = multiply(parent, matrix_of(node))
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]].get("primitives", []):
                accessor_index = primitive["attributes"].get("POSITION")
                if accessor_index is None:
                    continue
                accessor = document["accessors"][accessor_index]
                lo, hi = accessor["min"], accessor["max"]
                for cx in (lo[0], hi[0]):
                    for cy in (lo[1], hi[1]):
                        for cz in (lo[2], hi[2]):
                            point = apply(world, [cx, cy, cz])
                            for axis in range(3):
                                minimum[axis] = min(minimum[axis], point[axis])
                                maximum[axis] = max(maximum[axis], point[axis])
        for child in node.get("children", []):
            walk(child, world)

    identity = [[1 if i == j else 0 for j in range(4)] for i in range(4)]
    for scene in document.get("scenes", []):
        for root in scene.get("nodes", []):
            walk(root, identity)
    return tuple(maximum[axis] - minimum[axis] for axis in range(3))


def _export(shape, directory: Path, stem: str, *, source_length_unit):
    """Write a STEP+GLB pair the way the RC consumer does."""
    step_path = directory / f"{stem}.step"
    glb_path = directory / f"{stem}.glb"
    kwargs = {} if source_length_unit is None else {"source_length_unit": source_length_unit}
    scene = export_build123d_step_scene(shape, step_path, **kwargs)
    mesh_step_scene(
        scene,
        linear_deflection=LINEAR_DEFLECTION,
        angular_deflection=ANGULAR_DEFLECTION,
        relative=False,
    )
    scene_export_shape(scene)
    written = Path(
        export_native_glb_from_scene(
            step_path,
            scene,
            target_path=glb_path,
            linear_deflection=LINEAR_DEFLECTION,
            angular_deflection=ANGULAR_DEFLECTION,
            **kwargs,
        )
    )
    return step_path, written


class _ExportCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temporary_directory(prefix="cadpy-source-unit-")
        cls.out = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


class MillimetreDefaultTest(_ExportCase):
    """Existing callers author millimetres and must be untouched."""

    def test_default_step_is_physically_millimetres(self) -> None:
        step, _glb = _export(build123d.Box(*BOX), self.out, "default-mm", source_length_unit=None)
        extent = _step_physical_extent(step)
        # A 2-unit box authored in millimetres is physically 2 mm.
        self.assertAlmostEqual(extent[0], 0.002, places=9)
        self.assertAlmostEqual(extent[2], 0.0005, places=9)

    def test_default_glb_is_physically_millimetres(self) -> None:
        _step, glb = _export(build123d.Box(*BOX), self.out, "default-mm-glb", source_length_unit=None)
        extent = _glb_world_extent(glb)
        self.assertAlmostEqual(extent[0], 0.002, places=9)

    def test_omitting_the_unit_matches_passing_unit_mm(self) -> None:
        """The default is Unit.MM as behaviour, not merely as documentation.

        The STEP header carries a wall-clock timestamp, so the STEP is compared
        on its geometry rather than its bytes; the GLB is byte-reproducible and
        is compared exactly.
        """
        implicit_step, implicit_glb = _export(
            build123d.Box(*BOX), self.out, "implicit", source_length_unit=None
        )
        explicit_step, explicit_glb = _export(
            build123d.Box(*BOX), self.out, "explicit", source_length_unit=Unit.MM
        )
        self.assertEqual(
            _step_physical_extent(implicit_step), _step_physical_extent(explicit_step)
        )
        self.assertEqual(
            _step_declared_metres_per_unit(implicit_step.read_text()),
            _step_declared_metres_per_unit(explicit_step.read_text()),
        )
        self.assertEqual(implicit_glb.read_bytes(), explicit_glb.read_bytes())


class MetreKernelStepTest(_ExportCase):
    def test_metre_kernel_writes_a_physically_two_metre_box(self) -> None:
        step, _glb = _export(build123d.Box(*BOX), self.out, "metre-step", source_length_unit=Unit.M)
        extent = _step_physical_extent(step)
        self.assertAlmostEqual(extent[0], 2.0, places=6)
        self.assertAlmostEqual(extent[1], 2.0, places=6)
        self.assertAlmostEqual(extent[2], 0.5, places=6)

    def test_step_declares_an_inspectable_length_unit(self) -> None:
        step, _glb = _export(build123d.Box(*BOX), self.out, "metre-decl", source_length_unit=Unit.M)
        text = step.read_text()
        # OCCT always emits AP214 in millimetres and converts the coordinates,
        # so the declaration is millimetres and the numbers are 1000x. Both
        # halves are asserted: the file is only correct if they agree.
        self.assertEqual(_step_declared_metres_per_unit(text), 0.001)
        points = [float(m[0]) for m in _STEP_POINT_RE.findall(text)]
        self.assertAlmostEqual(max(points) - min(points), 2000.0, places=3)

    def test_a_sixteen_millimetre_bar_stays_sixteen_millimetres(self) -> None:
        """Authored as 0.016 metres; must survive as 16 mm, not 16 µm."""
        bar = build123d.Cylinder(radius=BAR_DIAMETER_M / 2.0, height=1.0)
        step, _glb = _export(bar, self.out, "metre-bar", source_length_unit=Unit.M)
        extent = _step_physical_extent(step)
        self.assertAlmostEqual(extent[0], BAR_DIAMETER_M, places=6)
        self.assertAlmostEqual(extent[1], BAR_DIAMETER_M, places=6)


class MetreKernelGlbTest(_ExportCase):
    def test_metre_kernel_glb_world_extent_is_two_gltf_metres(self) -> None:
        _step, glb = _export(build123d.Box(*BOX), self.out, "metre-glb", source_length_unit=Unit.M)
        # Y-up: kernel (x=2, y=2, z=0.5) -> glTF (x, z, -y) = (2, 0.5, 2).
        x_extent, y_extent, z_extent = _glb_world_extent(glb)
        self.assertAlmostEqual(x_extent, 2.0, places=6)
        self.assertAlmostEqual(y_extent, 0.5, places=6)
        self.assertAlmostEqual(z_extent, 2.0, places=6)

    def test_a_sixteen_millimetre_bar_is_0016_gltf_metres(self) -> None:
        bar = build123d.Cylinder(radius=BAR_DIAMETER_M / 2.0, height=1.0)
        _step, glb = _export(bar, self.out, "metre-bar-glb", source_length_unit=Unit.M)
        extent = _glb_world_extent(glb)
        # A triangulated cylinder sits just inside its true silhouette, so the
        # tolerance is the mesh chord tolerance rather than exactness. It is
        # nowhere near the 1000x this test exists to catch.
        self.assertAlmostEqual(extent[0], BAR_DIAMETER_M, delta=LINEAR_DEFLECTION)
        self.assertGreater(extent[0], BAR_DIAMETER_M * 0.99)

    def test_the_scale_is_applied_exactly_once(self) -> None:
        """A doubly-applied metre scale is invisible; a doubly-applied
        millimetre scale is not. Compare the two units against the ratio their
        declarations imply — 1000x, not 1x and not 1e6x."""
        _s1, mm_glb = _export(build123d.Box(*BOX), self.out, "once-mm", source_length_unit=Unit.MM)
        _s2, m_glb = _export(build123d.Box(*BOX), self.out, "once-m", source_length_unit=Unit.M)
        mm_extent = _glb_world_extent(mm_glb)[0]
        m_extent = _glb_world_extent(m_glb)[0]
        self.assertAlmostEqual(m_extent / mm_extent, 1000.0, places=3)
        self.assertAlmostEqual(mm_extent, 0.002, places=9)
        self.assertAlmostEqual(m_extent, 2.0, places=6)

    def test_y_up_conversion_survives_the_scale(self) -> None:
        """Z-up kernel to Y-up glTF, still right-handed and unmixed.

        A tall thin box makes the mapping unambiguous: its long kernel Z axis
        must land on glTF Y, and no axis may pick up the other's length.
        """
        _step, glb = _export(
            build123d.Box(1.0, 2.0, 4.0), self.out, "yup", source_length_unit=Unit.M
        )
        x_extent, y_extent, z_extent = _glb_world_extent(glb)
        # kernel (x=1, y=2, z=4) -> glTF (x, z, -y) = (1, 4, 2)
        self.assertAlmostEqual(x_extent, 1.0, places=6)
        self.assertAlmostEqual(y_extent, 4.0, places=6)
        self.assertAlmostEqual(z_extent, 2.0, places=6)


class DivergenceRefusalTest(_ExportCase):
    def test_a_glb_unit_that_contradicts_its_scene_is_refused(self) -> None:
        """The structural half of the coupling.

        The scene carries the unit its STEP was written with, so a GLB export
        that is handed a different one is refused rather than quietly writing
        an artifact a thousand times off from its own STEP.
        """
        step_path = self.out / "diverge.step"
        scene = export_build123d_step_scene(
            build123d.Box(*BOX), step_path, source_length_unit=Unit.M
        )
        mesh_step_scene(
            scene,
            linear_deflection=LINEAR_DEFLECTION,
            angular_deflection=ANGULAR_DEFLECTION,
            relative=False,
        )
        scene_export_shape(scene)
        with self.assertRaises(ValueError) as caught:
            export_native_glb_from_scene(
                step_path,
                scene,
                target_path=self.out / "diverge.glb",
                linear_deflection=LINEAR_DEFLECTION,
                angular_deflection=ANGULAR_DEFLECTION,
                source_length_unit=Unit.MM,
            )
        self.assertIn("disagrees", str(caught.exception))


class StepGlbPhysicalAgreementTest(_ExportCase):
    """The regression that fails if only one half of the fix lands.

    Each artifact is parsed independently and reduced to physical metres
    through its own declaration — the STEP through its ``SI_UNIT``, the GLB
    through the glTF metre convention. Then they are required to agree. A STEP
    at 2 m beside a GLB at 2 mm fails here, and so does the reverse.
    """

    #: Half a millimetre, matching the mesh chord tolerance. The GLB is a
    #: triangulation of the STEP's exact surfaces, so they agree to meshing
    #: accuracy and no closer.
    TOLERANCE_M = 0.0005

    def _assert_agreement(self, unit, expected_x: float) -> None:
        step, glb = _export(
            build123d.Box(*BOX), self.out, f"agree-{unit.name.lower()}", source_length_unit=unit
        )
        step_extent = _step_physical_extent(step)
        glb_extent = _glb_world_extent(glb)
        # Y-up: glTF (x, y, z) corresponds to kernel (x, z, y).
        paired = ((step_extent[0], glb_extent[0]), (step_extent[2], glb_extent[1]),
                  (step_extent[1], glb_extent[2]))
        self.assertAlmostEqual(step_extent[0], expected_x, delta=self.TOLERANCE_M)
        for axis, (from_step, from_glb) in enumerate(paired):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(from_step, from_glb, delta=self.TOLERANCE_M)

    def test_metre_kernel_artifacts_agree_at_two_metres(self) -> None:
        self._assert_agreement(Unit.M, 2.0)

    def test_millimetre_kernel_artifacts_agree_at_two_millimetres(self) -> None:
        self._assert_agreement(Unit.MM, 0.002)


if __name__ == "__main__":
    unittest.main()
