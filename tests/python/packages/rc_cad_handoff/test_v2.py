"""RcCadHandoffV2: dispatch, families, mats, statuses and the IFC readback.

Every assertion starts from the COMMITTED V2 fixture — the producer's own golden, copied here so a
divergence between the two sides surfaces as a fixture diff rather than as a mystery. Nothing is
constructed by hand: a literal would prove this module can read a literal.

The V1 tests are untouched and still run against the frozen V1 fixture. That pair is the point:
one consumer, two declared versions, and an old file on disk keeps working.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rc_cad_handoff.errors import ContractError, StructureError
from rc_cad_handoff.ifc import write_ifc
from rc_cad_handoff.manifest import (
    CONTRACTS,
    FAMILY_KINDS,
    MAT_FAMILY_KINDS,
    parse_handoff_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
V2 = FIXTURES / "rc-footing-cad-poc.handoff.v2.json"
V1 = FIXTURES / "rc-footing-cad-poc.handoff.json"

#: What the producer's canonical V2 export contains. Exact, so a silent change is a failure.
EXPECTED_FAMILIES = {
    "columnDowel": 8,
    "starterTie": 6,
    "starterCrosstie": 12,
    "footingBottomMatX": 10,
    "footingBottomMatY": 10,
}


def _parse(path: Path):
    text = path.read_text()
    return parse_handoff_text(
        text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        size_bytes=len(text.encode()),
    )


class VersionDispatchTest(unittest.TestCase):
    def test_reads_both_declared_versions(self) -> None:
        self.assertEqual(CONTRACTS, {1: "RcCadHandoffV1", 2: "RcCadHandoffV2"})
        self.assertEqual(_parse(V1).schema_version, 1)
        self.assertEqual(_parse(V2).schema_version, 2)

    def test_v1_carries_no_statuses_block(self) -> None:
        # ``None``, not a fabricated default. Inventing statuses for a V1 document would be this
        # consumer asserting verdicts the producer never made.
        self.assertIsNone(_parse(V1).statuses)
        self.assertIsNone(_parse(V1).bottom_mat_layer_order)

    def test_refuses_a_v2_family_in_a_v1_document(self) -> None:
        doc = json.loads(V1.read_text())
        doc["assembly"]["families"][0]["kind"] = "footingBottomMatX"
        text = json.dumps(doc)
        with self.assertRaises(ContractError) as ctx:
            parse_handoff_text(text, sha256="x", size_bytes=len(text))
        self.assertEqual(ctx.exception.code, "UNKNOWN_FAMILY_KIND")

    def test_family_kinds_are_declared_per_version(self) -> None:
        self.assertEqual(FAMILY_KINDS[1], frozenset({"columnDowel", "starterTie"}))
        self.assertEqual(FAMILY_KINDS[2], frozenset(EXPECTED_FAMILIES))


class FamiliesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _parse(V2)

    def test_carries_all_five_families_with_the_producer_counts(self) -> None:
        got = {f.kind: len(f.bar_ids) for f in self.h.families}
        self.assertEqual(got, EXPECTED_FAMILIES)
        self.assertEqual(len(self.h.bars), 46)
        self.assertEqual(sum(got.values()), 46)

    def test_a_mat_bar_is_never_a_column_dowel(self) -> None:
        """The defect V2 exists to remove, asserted on the consumer side too.

        A footing's bars are attributed to the column element, so ownership cannot separate a mat
        bar from a starter. The family is what does, and it must not collapse.
        """
        kind_of = {f.family_id: f.kind for f in self.h.families}
        mat_ids = {
            b
            for f in self.h.families
            if f.kind in MAT_FAMILY_KINDS
            for b in f.bar_ids
        }
        self.assertEqual(len(mat_ids), 20)
        for bar_id in mat_ids:
            self.assertNotEqual(kind_of[self.h.family_of_bar[bar_id]], "columnDowel")
        # And every mat bar is owned by the same element as the starters, which is why the family
        # is the only thing that separates them.
        owners = {tuple(b.owner_element_ids) for b in self.h.bars}
        self.assertEqual(len(owners), 1)

    def test_mat_families_carry_their_layer_direction_and_cover(self) -> None:
        by_kind = {f.kind: f for f in self.h.families}
        x = by_kind["footingBottomMatX"].mat
        y = by_kind["footingBottomMatY"].mat
        assert x is not None and y is not None
        self.assertEqual((x.direction, x.layer), ("X", "LOWER"))
        self.assertEqual((y.direction, y.layer), ("Y", "UPPER"))
        # 16 mm apart — one lower-layer diameter — and the upper cover is larger by the same.
        self.assertAlmostEqual(y.axis_elevation - x.axis_elevation, 0.016, places=9)
        self.assertAlmostEqual(x.clear_cover_to_soffit, 0.050, places=9)
        self.assertAlmostEqual(y.clear_cover_to_soffit, 0.066, places=9)
        for mat in (x, y):
            self.assertEqual(len(mat.regions), 1)
            self.assertEqual(mat.regions[0].kind, "UNIFORM_FULL_WIDTH")
            self.assertEqual(len(mat.regions[0].bar_ids), 10)

    def test_the_layer_order_agrees_with_the_families(self) -> None:
        order = self.h.bottom_mat_layer_order
        assert order is not None
        self.assertEqual((order.lower_direction, order.resolution), ("X", "X_BELOW_Y"))
        for fam in self.h.families:
            if fam.mat is None:
                continue
            want = "LOWER" if fam.mat.direction == order.lower_direction else "UPPER"
            self.assertEqual(fam.mat.layer, want, fam.kind)

    def test_ties_and_crossties_are_separated_by_legs(self) -> None:
        by_kind = {f.kind: f for f in self.h.families}
        tie = by_kind["starterTie"].tie
        cross = by_kind["starterCrosstie"].tie
        assert tie is not None and cross is not None
        self.assertEqual(tie.legs_contributed, 2)
        self.assertEqual(cross.legs_contributed, 1)

    def test_refuses_a_mat_family_with_no_mat_detail(self) -> None:
        doc = json.loads(V2.read_text())
        for fam in doc["assembly"]["families"]:
            if fam["kind"] == "footingBottomMatX":
                del fam["mat"]
        text = json.dumps(doc)
        with self.assertRaises(StructureError) as ctx:
            parse_handoff_text(text, sha256="x", size_bytes=len(text))
        self.assertEqual(ctx.exception.code, "MAT_FAMILY_WITHOUT_DETAIL")

    def test_refuses_a_mat_detail_whose_direction_disagrees(self) -> None:
        doc = json.loads(V2.read_text())
        for fam in doc["assembly"]["families"]:
            if fam["kind"] == "footingBottomMatX":
                fam["mat"]["direction"] = "Y"
        text = json.dumps(doc)
        with self.assertRaises(StructureError) as ctx:
            parse_handoff_text(text, sha256="x", size_bytes=len(text))
        self.assertEqual(ctx.exception.code, "MAT_DIRECTION_MISMATCH")


class StatusesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = _parse(V2).statuses

    def test_the_verdict_and_its_cause(self) -> None:
        assert self.s is not None
        self.assertFalse(self.s.constructible)
        self.assertEqual(
            self.s.constructibility_blockers, ("MAT_STARTER_CLEAR_SPACING_FAILURE",)
        )

    def test_each_status_stays_separate_and_verbatim(self) -> None:
        assert self.s is not None
        self.assertEqual(self.s.bottom_flexure, "OK")
        self.assertEqual(self.s.bottom_mat_geometry, "MODELED")
        # FAILED, explicitly. Never softened to NOT_EVALUATED, OK or a warning: the producer's
        # anchorage outcome for this footing is a failure and the consumer reads it as one.
        self.assertEqual(self.s.bottom_anchorage, "FAILED")
        self.assertEqual(self.s.top_reinforcement, "NOT_EVALUATED")
        self.assertEqual(self.s.punching_moment_transfer, "UNSUPPORTED")


class FindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _parse(V2)

    def test_zero_interpenetrations_and_four_spacing_failures(self) -> None:
        collision = [c for c in self.h.checks if c.check_kind == "barCollision"][0]
        spacing = [c for c in self.h.checks if c.check_kind == "barClearSpacing"][0]
        self.assertEqual(collision.evaluation_status, "EVALUATED")
        self.assertEqual(len(collision.findings), 0)
        self.assertEqual(spacing.evaluation_status, "EVALUATED")
        self.assertEqual(len(spacing.findings), 4)

    def test_every_finding_names_a_mat_bar_and_a_starter_it_carries(self) -> None:
        """The pairs V1 could not carry at all.

        V1's own rule forbids a finding naming a bar the document does not contain, and one bar of
        each of these pairs was a mat bar — outside its scope. In V2 both are here.
        """
        spacing = [c for c in self.h.checks if c.check_kind == "barClearSpacing"][0]
        ids = {b.bar_id for b in self.h.bars}
        mat_ids = {
            b for f in self.h.families if f.kind in MAT_FAMILY_KINDS for b in f.bar_ids
        }
        for f in spacing.findings:
            self.assertEqual(f.pair_class, "sameLayerSpacing")
            self.assertEqual(f.severity, "clearance")
            self.assertIn(f.bar_id_a, ids)
            self.assertIn(f.bar_id_b, ids)
            pair = {f.bar_id_a, f.bar_id_b}
            self.assertEqual(len(pair & mat_ids), 1, f"exactly one mat bar in {pair}")
            # 27,97 mm measured against the 40,00 mm the clause requires: a shortfall with clear
            # concrete between the surfaces, not an interpenetration.
            self.assertAlmostEqual(f.measured * 1000, 27.97, places=2)
            self.assertAlmostEqual(f.required * 1000, 40.00, places=2)
            self.assertGreater(f.measured, 0.0)
            self.assertLess(f.measured, f.required)


class IfcTest(unittest.TestCase):
    def test_ifc4_carries_every_bar_with_its_family_and_stable_id(self) -> None:
        import ifcopenshell

        h = _parse(V2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.ifc"
            info = write_ifc(h, path)
            self.assertEqual(info["reinforcing_bars"], 46)
            self.assertEqual(info["family_groups"], 5)
            self.assertEqual(info["length_unit"], "METRE")

            model = ifcopenshell.open(str(path))
            self.assertEqual(model.schema, "IFC4")
            bars = model.by_type("IfcReinforcingBar")
            self.assertEqual(len(bars), 46)

            # Stable ids survive the round trip, exactly once each.
            names = [b.Name for b in bars]
            self.assertEqual(len(set(names)), 46)
            self.assertEqual(set(names), {b.bar_id for b in h.bars})

            # Declared metres, and the diameters written as metres rather than millimetres.
            unit = model.by_type("IfcSIUnit")[0]
            self.assertEqual((unit.UnitType, unit.Name), ("LENGTHUNIT", "METRE"))
            by_name = {b.Name: b for b in bars}
            for bar in h.bars:
                self.assertAlmostEqual(
                    by_name[bar.bar_id].NominalDiameter, bar.diameter_mm / 1000.0, places=9
                )

            # The family survives as a group, and no mat bar is grouped as a column dowel.
            kind_by_name: dict[str, str] = {}
            for rel in model.by_type("IfcRelAssignsToGroup"):
                for obj in rel.RelatedObjects:
                    kind_by_name[obj.Name] = rel.RelatingGroup.Name
            counted: dict[str, int] = {}
            for kind in kind_by_name.values():
                counted[kind] = counted.get(kind, 0) + 1
            self.assertEqual(counted, EXPECTED_FAMILIES)
            mat_ids = {
                b for f in h.families if f.kind in MAT_FAMILY_KINDS for b in f.bar_ids
            }
            for bar_id in mat_ids:
                self.assertNotEqual(kind_by_name[bar_id], "columnDowel")
                self.assertEqual(by_name[bar_id].PredefinedType, "MAIN")


if __name__ == "__main__":
    unittest.main()
