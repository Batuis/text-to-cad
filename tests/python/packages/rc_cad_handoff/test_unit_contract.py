"""The physical size of the artifacts, and the engineering values around them.

Two things are pinned here, and they are different in kind.

The first is that the STEP and the GLB describe a footing that is **2 metres**
across. Both were a thousand times smaller until the source unit reached
cadpy's writers, and nothing in either file said so — the STEP declared
millimetres over metre-valued coordinates and the GLB multiplied a metre kernel
by 0.001 a second time.

The second is that fixing that changed **no engineering number**. The kernel
was always metre-valued and the review always read it correctly; only the two
writers were misinformed. Every value in ``EngineeringResultsPreservedTest``
therefore has to survive the correction untouched, and a change to any of them
means the fix reached further than it should have.
"""

from __future__ import annotations

import json
import math
import struct
import unittest
from pathlib import Path

from build123d.build_enums import Unit

from rc_cad_handoff.artifacts import SOURCE_LENGTH_UNIT, unit_contract, write_artifacts
from rc_cad_handoff.review import REVIEW_FORMAT_VERSION, build_review

from tests.python.support.tmp_root import temporary_directory

from tests.python.packages.rc_cad_handoff import _support as fx

#: The footing the canonical manifest describes: 2 m x 2 m x 0.5 m.
FOOTING_PLAN_M = 2.0
#: Whole-model height: footing soffit at z=-1.2 up to the column top at 0.4473.
MODEL_HEIGHT_M = 1.647263
#: Largest of the reinforcement diameters, the feature a wrong scale ruins first.
BAR_DIAMETER_M = 0.016

#: Mesh chord tolerance. The GLB triangulates the STEP's exact surfaces, so the
#: two agree to meshing accuracy — nowhere near the 1000x under test.
MESH_TOLERANCE_M = 0.0005


def _step_physical_extent(step_path: Path) -> tuple[float, float, float]:
    """Read the STEP back through a real reader and report metres.

    Independent read-back rather than a text scan: curved surfaces expose few
    explicit points, so scanning ``CARTESIAN_POINT`` understates a bar. The
    reader resolves the file's declared unit into OCCT millimetres.
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
    x0, y0, z0, x1, y1, z1 = box.Get()
    return ((x1 - x0) / 1000.0, (y1 - y0) / 1000.0, (z1 - z0) / 1000.0)


def _glb_world_extent(glb_path: Path) -> tuple[float, float, float]:
    """Bounding box in glTF units, which the spec fixes as metres."""
    payload = glb_path.read_bytes()
    _magic, _version, length = struct.unpack_from("<III", payload, 0)
    offset, chunks = 12, []
    while offset < length:
        chunk_length, _type = struct.unpack_from("<II", payload, offset)
        chunks.append(payload[offset + 8 : offset + 8 + chunk_length])
        offset += 8 + chunk_length
    document = json.loads(chunks[0].decode("utf-8"))

    minimum = [math.inf] * 3
    maximum = [-math.inf] * 3

    def matrix_of(node):
        if "matrix" in node:
            m = node["matrix"]
            return [[m[0], m[4], m[8], m[12]], [m[1], m[5], m[9], m[13]],
                    [m[2], m[6], m[10], m[14]], [m[3], m[7], m[11], m[15]]]
        return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]

    def multiply(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    def apply(m, p):
        return [sum(m[i][j] * p[j] for j in range(3)) + m[i][3] for i in range(3)]

    def walk(index, parent):
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


class _ArtifactCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temporary_directory(prefix="rc-cad-units-")
        cls.out = Path(cls._tmp.name)
        cls.artifacts = write_artifacts(fx.handoff(), fx.model(), output_dir=cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


class SourceUnitDeclarationTest(unittest.TestCase):
    def test_the_consumer_declares_metres(self) -> None:
        """The kernel is metre-valued because the manifest is, and this package
        deliberately does not rescale on the way in."""
        self.assertIs(SOURCE_LENGTH_UNIT, Unit.M)

    def test_one_declaration_drives_both_writers(self) -> None:
        contract = unit_contract()
        self.assertEqual(contract["metresPerKernelUnit"], 1.0)
        # The STEP length-unit scale and the GLB vertex scale are the same
        # number by construction. If these ever differ, the two artifacts
        # describe different-sized objects.
        self.assertEqual(contract["stepLengthUnitScale"], contract["glbScale"])
        self.assertEqual(contract["stepLengthUnitScale"], contract["metresPerKernelUnit"])

    def test_the_contract_states_physical_artifact_units(self) -> None:
        """A reader must be able to size the artifacts without reading code."""
        contract = unit_contract()
        self.assertEqual(contract["manifestLength"], "m")
        self.assertEqual(contract["manifestBarDiameter"], "mm")
        self.assertEqual(contract["kernelLength"], "m")
        self.assertEqual(contract["sourceLengthUnitDeclaredToCadpy"], "m")
        self.assertEqual(contract["stepPhysicalLength"], "m")
        self.assertEqual(contract["glbPhysicalLength"], "m")
        self.assertEqual(contract["reviewLength"], "m")
        self.assertEqual(contract["reviewVolume"], "m3")
        # STEP files are always written in millimetres by OCCT; the coordinates
        # carry the conversion. Declaring only one of the two would be a lie.
        self.assertEqual(contract["stepDeclaredLength"], "mm")


class KernelIsMetreValuedTest(unittest.TestCase):
    def test_the_footing_is_two_kernel_units(self) -> None:
        """The kernel did not move. Correcting the writers must not move it."""
        footing = fx.handoff().body(fx.FOOTING_BODY_ID)
        self.assertAlmostEqual(footing.shape.b, FOOTING_PLAN_M, places=9)
        self.assertAlmostEqual(footing.shape.length, FOOTING_PLAN_M, places=9)

    def test_a_sixteen_millimetre_bar_is_0016_kernel_units(self) -> None:
        bars = [b for b in fx.handoff().bars if b.diameter_mm == 16.0]
        self.assertTrue(bars)
        self.assertAlmostEqual(bars[0].radius_m * 2.0, BAR_DIAMETER_M, places=12)


class StepIsPhysicallyCorrectTest(_ArtifactCase):
    def test_step_reads_back_as_a_two_metre_footing(self) -> None:
        x_extent, y_extent, z_extent = _step_physical_extent(self.artifacts.step.path)
        self.assertAlmostEqual(x_extent, FOOTING_PLAN_M, places=6)
        self.assertAlmostEqual(y_extent, FOOTING_PLAN_M, places=6)
        self.assertAlmostEqual(z_extent, MODEL_HEIGHT_M, places=5)

    def test_step_declares_millimetres_over_converted_coordinates(self) -> None:
        """Both halves, because the file is only right if they agree."""
        text = self.artifacts.step.path.read_text()
        self.assertIn("SI_UNIT(.MILLI.,.METRE.)", text)
        self.assertNotIn("SI_UNIT($,.METRE.)", text)
        import re

        points = [
            float(m[0])
            for m in re.findall(
                r"CARTESIAN_POINT\('[^']*',\(([-0-9.E+]+),([-0-9.E+]+),([-0-9.E+]+)\)\)", text
            )
        ]
        # Metre-valued 2.0 written as 2000 mm, not left at 2.0.
        self.assertAlmostEqual(max(points) - min(points), 2000.0, places=3)


class GlbIsPhysicallyCorrectTest(_ArtifactCase):
    def test_glb_world_extent_is_two_gltf_metres(self) -> None:
        x_extent, y_extent, z_extent = _glb_world_extent(self.artifacts.glb.path)
        # Y-up: kernel (x, y, z) -> glTF (x, z, -y). Plan stays on X and Z,
        # model height lands on Y.
        self.assertAlmostEqual(x_extent, FOOTING_PLAN_M, delta=MESH_TOLERANCE_M)
        self.assertAlmostEqual(z_extent, FOOTING_PLAN_M, delta=MESH_TOLERANCE_M)
        self.assertAlmostEqual(y_extent, MODEL_HEIGHT_M, delta=MESH_TOLERANCE_M)

    def test_the_model_is_not_a_thousand_times_undersized(self) -> None:
        """The defect, stated directly."""
        x_extent, _y, _z = _glb_world_extent(self.artifacts.glb.path)
        self.assertGreater(x_extent, 1.0, "GLB is undersized; the source unit did not reach it")
        self.assertLess(x_extent, 10.0, "GLB is oversized; the scale may be applied twice")


class StepGlbAgreementTest(_ArtifactCase):
    """Fails if only one half of the source-unit change is present."""

    def test_both_artifacts_describe_the_same_physical_object(self) -> None:
        step_x, step_y, step_z = _step_physical_extent(self.artifacts.step.path)
        glb_x, glb_y, glb_z = _glb_world_extent(self.artifacts.glb.path)
        # STEP is Z-up, GLB is Y-up: step (x, y, z) <-> glb (x, z, y).
        for axis, (from_step, from_glb) in enumerate(
            ((step_x, glb_x), (step_y, glb_z), (step_z, glb_y))
        ):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(from_step, from_glb, delta=MESH_TOLERANCE_M)

    def test_agreement_is_at_metre_scale_not_merely_consistent(self) -> None:
        """Two artifacts can agree and both be wrong. Pin the physical size."""
        self.assertAlmostEqual(
            _step_physical_extent(self.artifacts.step.path)[0], FOOTING_PLAN_M, places=6
        )
        self.assertAlmostEqual(
            _glb_world_extent(self.artifacts.glb.path)[0], FOOTING_PLAN_M, delta=MESH_TOLERANCE_M
        )


class ReviewVersionTwoTest(_ArtifactCase):
    def setUp(self) -> None:
        self.review = build_review(fx.handoff(), fx.model(), fx.cross_check_result(), self.artifacts)

    def test_declares_format_version_two(self) -> None:
        self.assertEqual(self.review["reviewFormatVersion"], REVIEW_FORMAT_VERSION)
        self.assertEqual(self.review["reviewFormatVersion"], 2)

    def test_the_producer_contract_is_untouched(self) -> None:
        """Only the review format moved. RcCadHandoffV1 stays version 1."""
        self.assertEqual(self.review["source"]["schemaVersion"], 1)
        self.assertEqual(fx.raw_manifest()["schemaVersion"], 1)

    def test_unit_metadata_is_complete(self) -> None:
        boundaries = self.review["units"]["boundaries"]
        for field in (
            "manifestLength",
            "manifestBarDiameter",
            "kernelLength",
            "stepDeclaredLength",
            "stepPhysicalLength",
            "glbPhysicalLength",
            "reviewLength",
            "reviewVolume",
            "sourceLengthUnitDeclaredToCadpy",
            "stepLengthUnitScale",
            "glbScale",
            "orientationConversion",
        ):
            with self.subTest(field=field):
                self.assertIn(field, boundaries)
                self.assertTrue(str(boundaries[field]))

    def test_unit_metadata_is_internally_consistent(self) -> None:
        boundaries = self.review["units"]["boundaries"]
        # The review's own units must not contradict the top-level block.
        self.assertEqual(boundaries["reviewLength"], self.review["units"]["length"])
        self.assertEqual(boundaries["reviewVolume"], self.review["units"]["volume"])
        self.assertEqual(boundaries["manifestBarDiameter"], self.review["units"]["barDiameter"])
        # And the two derived scales must remain one number.
        self.assertEqual(boundaries["stepLengthUnitScale"], boundaries["glbScale"])

    def test_metadata_matches_the_artifacts_actually_written(self) -> None:
        """Metadata that can drift from the files is worse than none."""
        boundaries = self.review["units"]["boundaries"]
        self.assertEqual(boundaries["stepPhysicalLength"], "m")
        self.assertAlmostEqual(
            _step_physical_extent(self.artifacts.step.path)[0], FOOTING_PLAN_M, places=6
        )
        self.assertEqual(boundaries["glbPhysicalLength"], "m")
        self.assertAlmostEqual(
            _glb_world_extent(self.artifacts.glb.path)[0], FOOTING_PLAN_M, delta=MESH_TOLERANCE_M
        )


class DegenerateCauseIdentityTest(unittest.TestCase):
    """Two check contexts, one geometric computation, one numerical limitation."""

    def setUp(self) -> None:
        self.review = build_review(fx.handoff(), fx.model(), fx.cross_check_result(), None)
        self.numerical = [
            i for i in self.review["issues"] if i["kind"] == "numericalLimitation"
        ]

    def test_every_numerical_limitation_carries_a_stable_cause_id(self) -> None:
        self.assertTrue(self.numerical)
        for issue in self.numerical:
            with self.subTest(issue=issue.get("detail", "")[:40]):
                cause = issue.get("causeId")
                self.assertTrue(cause, "numerical limitations must be attributable to a cause")
                # An identity, not prose: derived from stable ids only.
                self.assertTrue(str(cause).startswith("cause:numericalLimitation:"))
                self.assertNotIn(" ", str(cause))

    def test_the_interface_tie_reports_two_records_under_one_cause(self) -> None:
        tie = [i for i in self.numerical if i.get("barIdA") == "F1-C1:starter:stirrup:0.0000"]
        self.assertEqual(len(tie), 2, "cover and containment must both keep their record")
        self.assertEqual(
            {i["checkId"] for i in tie},
            {"check:concreteCover:footing:1", "check:reinforcementContainment:footing:1"},
        )
        self.assertEqual(
            len({i["causeId"] for i in tie}),
            1,
            "one clip of one bar against one body is one numerical limitation",
        )

    def test_the_distinct_cause_count_is_not_inflated(self) -> None:
        summary = self.review["summary"]
        self.assertEqual(summary["numericalLimitationRecordCount"], len(self.numerical))
        self.assertEqual(
            summary["numericalLimitationDistinctCauseCount"],
            len({i["causeId"] for i in self.numerical}),
        )
        # Four records; three geometries. Reporting only the record count
        # invites a reader to count the interface tie twice.
        self.assertEqual(summary["numericalLimitationRecordCount"], 4)
        self.assertEqual(summary["numericalLimitationDistinctCauseCount"], 3)

    def test_the_corrected_near_tangential_description_is_emitted(self) -> None:
        pairs = [i for i in self.numerical if i.get("barIdB")]
        self.assertTrue(pairs)
        for issue in pairs:
            with self.subTest(pair=(issue["barIdA"], issue["barIdB"])):
                self.assertIn("near-tangential coplanar arc crossing", issue["detail"])
                self.assertNotIn("degenerate for coincident centrelines", issue["detail"])


class EngineeringResultsPreservedTest(unittest.TestCase):
    """The unit correction touched two writers. It must have touched nothing else."""

    def setUp(self) -> None:
        self.review = build_review(fx.handoff(), fx.model(), fx.cross_check_result(), None)
        self.summary = self.review["summary"]

    def test_twelve_prohibited_overlaps_all_agreeing(self) -> None:
        self.assertEqual(self.summary["crossCheckedPairCount"], fx.EXPECTED_PROHIBITED_OVERLAPS)
        self.assertEqual(self.summary["agreementCount"], fx.EXPECTED_PROHIBITED_OVERLAPS)
        self.assertEqual(self.summary["disagreementCount"], 0)

    def test_worst_delta_is_still_about_0016_millimetres(self) -> None:
        worst = self.summary["worstDeltaM"]
        self.assertLess(worst, 0.0005, "must stay inside the approved agreement band")
        self.assertAlmostEqual(worst * 1000.0, 0.0016, places=3)

    def test_forty_eight_intentional_contacts(self) -> None:
        self.assertEqual(self.summary["unreportedClosePairCount"], 48)

    def test_cover_observation_and_placement_intent_in_metres(self) -> None:
        cover = self.review["cadObservations"]["cover"]
        self.assertTrue(cover)
        for observation in cover:
            with self.subTest(check=observation["checkId"]):
                self.assertAlmostEqual(observation["minimumObservedCover"], 0.036, places=9)
                self.assertAlmostEqual(observation["requiredDistance"], 0.050, places=9)
                self.assertEqual(observation["comparison"], "NOT_COMPARABLE")
                self.assertFalse(observation["isRegulatoryVerdict"])

    def test_reinforcement_inventory_unchanged(self) -> None:
        self.assertEqual(self.summary["barSolids"], fx.EXPECTED_BAR_COUNT)
        self.assertEqual(self.summary["totalArcs"], fx.EXPECTED_ARC_COUNT)
        self.assertEqual(self.summary["approximatedArcs"], fx.EXPECTED_APPROXIMATED_ARCS)

    def test_volume_reconciliation_unchanged_and_in_cubic_metres(self) -> None:
        components = self.review["concreteReconciliation"]["components"]
        footing = next(c for c in components if c["bodyId"] == fx.FOOTING_BODY_ID)
        # 2 m x 2 m x 0.5 m. In a millimetre kernel this would read 2e9.
        self.assertAlmostEqual(footing["cadSolidVolume"], 2.0, places=9)
        self.assertAlmostEqual(footing["declaredVolume"], 2.0, places=9)
        self.assertAlmostEqual(footing["volumeDelta"], 0.0, places=9)
        for bar in self.review["reinforcementReconciliation"]["bars"]:
            with self.subTest(bar=bar["barId"]):
                self.assertAlmostEqual(bar["volumeRatio"], 1.0, delta=0.005)

    def test_producer_authority_unchanged(self) -> None:
        self.assertTrue(self.summary["blockedByUnsupportedConditions"])
        self.assertFalse(self.summary["cleanPass"])
        self.assertEqual(self.summary["outOfScopeCheckCount"], 1)
        self.assertEqual(self.summary["notEvaluatedCheckCount"], 2)
        self.assertEqual(self.summary["unsupportedConditionCount"], 3)


class ManifestStabilityTest(unittest.TestCase):
    """The producer's document is an input and must not have been touched."""

    def test_canonical_handoff_is_byte_identical(self) -> None:
        import hashlib

        payload = fx.MANIFEST_PATH.read_bytes()
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "795e9de26f2eb8ce8d51f2ac7130336702fc534588f390071e3bd40bc03aa0e7",
        )
        self.assertEqual(len(payload), fx.MANIFEST_SIZE_BYTES)

    def test_the_handoff_still_declares_its_own_units(self) -> None:
        units = fx.raw_manifest()["units"]
        self.assertEqual(units["length"], "m")
        self.assertEqual(units["barDiameter"], "mm")


if __name__ == "__main__":
    unittest.main()
