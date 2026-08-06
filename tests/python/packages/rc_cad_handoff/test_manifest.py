"""Ingestion and refusal behaviour of the independent parser."""

from __future__ import annotations

import unittest

from rc_cad_handoff.errors import ContractError, StructureError
from rc_cad_handoff.manifest import CONTRACT

from tests.python.packages.rc_cad_handoff import _support as fx


class ValidIngestionTest(unittest.TestCase):
    def test_reads_the_document_identity_and_hash(self) -> None:
        h = fx.handoff()
        self.assertEqual(h.schema_version, 1)
        self.assertEqual(h.manifest_sha256, fx.MANIFEST_SHA256)
        self.assertEqual(h.manifest_bytes, fx.MANIFEST_SIZE_BYTES)
        self.assertEqual(h.generator_name, "stabileo-rc-cad-handoff")

    def test_preserves_stable_identifiers(self) -> None:
        h = fx.handoff()
        self.assertEqual({b.body_id for b in h.bodies}, {fx.FOOTING_BODY_ID, fx.COLUMN_BODY_ID})
        self.assertEqual({i.interface_id for i in h.interfaces}, {fx.INTERFACE_ID})
        self.assertEqual(
            {f.family_id for f in h.families}, {fx.DOWEL_FAMILY_ID, fx.TIE_FAMILY_ID}
        )
        self.assertEqual(len(h.bars), fx.EXPECTED_BAR_COUNT)
        self.assertEqual(len(h.marks), fx.EXPECTED_MARK_COUNT)
        # Every bar keeps its producer id, mark and family.
        for bar in h.bars:
            self.assertTrue(bar.bar_id)
            self.assertIn(bar.family_id, {fx.DOWEL_FAMILY_ID, fx.TIE_FAMILY_ID})
            self.assertIsNotNone(bar.mark)

    def test_echoes_revisions_and_certificate(self) -> None:
        h = fx.handoff()
        self.assertEqual(h.revisions["detailing"], 3)
        self.assertEqual(h.revisions["demand"], 2)
        self.assertEqual(h.certificate["maturity"], "IMPLEMENTED_PROVISIONAL")

    def test_counts_families_as_declared(self) -> None:
        h = fx.handoff()
        by_kind = {f.kind: f for f in h.families}
        self.assertEqual(len(by_kind["columnDowel"].bar_ids), fx.EXPECTED_DOWEL_COUNT)
        self.assertEqual(len(by_kind["starterTie"].bar_ids), fx.EXPECTED_TIE_COUNT)

    def test_declares_no_footing_mat_geometry(self) -> None:
        h = fx.handoff()
        self.assertEqual(h.assembly_completeness, "partialConnectionOnly")
        codes = {n.code for n in h.unsupported}
        self.assertIn("FOOTING_MAT_GEOMETRY_NOT_MODELED", codes)


class ContractRefusalTest(unittest.TestCase):
    def test_refuses_an_unknown_contract_name(self) -> None:
        document = fx.mutated(lambda d: d.__setitem__("schema", "SomeOtherHandoffV1"))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNKNOWN_CONTRACT")

    def test_refuses_an_unsupported_schema_version(self) -> None:
        # Version 3, not 2: version 2 is now READ, so it is no longer an example of an
        # unsupported version. A future version is the dangerous case — it has V2's fields plus
        # more, so a lenient reader would parse it and be silently wrong about the rest.
        document = fx.mutated(lambda d: d.__setitem__("schemaVersion", 3))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_SCHEMA_VERSION")

    def test_refuses_a_version_the_contract_name_disagrees_with(self) -> None:
        # A V1 document claiming version 2. The two self-descriptions conflict, and resolving it
        # by trusting either field would be wrong half the time.
        document = fx.mutated(lambda d: d.__setitem__("schemaVersion", 2))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNKNOWN_CONTRACT")

    def test_refuses_a_v2_name_at_version_one(self) -> None:
        # And the mirror: the check is symmetric, so neither field is privileged.
        document = fx.mutated(lambda d: d.__setitem__("schema", "RcCadHandoffV2"))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNKNOWN_CONTRACT")

    def test_refuses_rescaled_units(self) -> None:
        document = fx.mutated(lambda d: d["units"].__setitem__("length", "mm"))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_UNIT")

    def test_refuses_a_y_up_frame(self) -> None:
        document = fx.mutated(lambda d: d["coordinateSystem"].__setitem__("up", "Y"))
        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(document)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_FRAME")

    def test_refuses_an_unknown_observation_policy(self) -> None:
        """An unknown policy must not fall back to measuring."""

        def mutate(d):
            d["checks"][0]["consumerObservationPolicy"] = "MAY_DO_WHATEVER"

        with self.assertRaises(ContractError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_OBSERVATION_POLICY")

    def test_reports_the_contract_name_it_expects(self) -> None:
        self.assertEqual(CONTRACT, "RcCadHandoffV1")


class ReferenceValidationTest(unittest.TestCase):
    def test_rejects_a_bar_naming_an_undeclared_family(self) -> None:
        def mutate(d):
            d["reinforcement"]["bars"][0]["familyId"] = "family:does-not-exist"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_FAMILY_REFERENCE")

    def test_rejects_a_family_naming_an_unknown_bar(self) -> None:
        def mutate(d):
            d["assembly"]["families"][0]["barIds"].append("bar:ghost")

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_BAR_REFERENCE")

    def test_rejects_an_interface_naming_an_unknown_body(self) -> None:
        def mutate(d):
            d["concrete"]["interfaces"][0]["participants"]["aboveBodyId"] = "body:ghost"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_BODY_REFERENCE")

    def test_rejects_a_cover_requirement_scoped_to_an_unknown_body(self) -> None:
        def mutate(d):
            d["requirements"]["cover"][0]["measurementScope"]["withinBodyId"] = "body:ghost"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_BODY_REFERENCE")

    def test_rejects_a_check_naming_an_unknown_requirement(self) -> None:
        def mutate(d):
            d["checks"][0]["requirementIds"] = ["req:ghost"]

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_REQUIREMENT_REFERENCE")

    def test_rejects_a_finding_naming_an_unknown_bar(self) -> None:
        def mutate(d):
            check = next(c for c in d["checks"] if c["checkKind"] == "barCollision")
            check["findings"][0]["barIdA"] = "bar:ghost"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNKNOWN_BAR_REFERENCE")

    def test_rejects_duplicate_bar_ids(self) -> None:
        def mutate(d):
            bars = d["reinforcement"]["bars"]
            bars[1]["id"] = bars[0]["id"]

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "DUPLICATE_BAR_ID")


class MalformedGeometryTest(unittest.TestCase):
    def test_rejects_a_non_finite_coordinate(self) -> None:
        def mutate(d):
            d["reinforcement"]["bars"][0]["segments"][0]["start"]["x"] = float("inf")

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "NON_FINITE_NUMBER")

    def test_rejects_nan(self) -> None:
        def mutate(d):
            d["concrete"]["bodies"][0]["shape"]["centre"]["z"] = float("nan")

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "NON_FINITE_NUMBER")

    def test_rejects_a_non_positive_dimension(self) -> None:
        def mutate(d):
            d["concrete"]["bodies"][0]["shape"]["height"] = 0.0

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "NOT_POSITIVE")

    def test_rejects_a_non_positive_bar_diameter(self) -> None:
        def mutate(d):
            d["reinforcement"]["bars"][0]["diameterMm"] = -16

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "NOT_POSITIVE")

    def test_rejects_an_inferred_solid(self) -> None:
        """Only explicit primitives are accepted; a solid is never inferred."""

        def mutate(d):
            d["concrete"]["bodies"][0]["shape"]["kind"] = "prism"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_SHAPE")

    def test_rejects_malformed_json(self) -> None:
        from rc_cad_handoff.manifest import parse_handoff_text

        with self.assertRaises(StructureError) as ctx:
            parse_handoff_text("{not json", sha256="0" * 64, size_bytes=9)
        self.assertEqual(ctx.exception.code, "MALFORMED_JSON")

    def test_rejects_a_missing_required_field(self) -> None:
        def mutate(d):
            del d["reinforcement"]["bars"][0]["cuttingLength"]

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "MISSING_FIELD")


class ArcClaimTest(unittest.TestCase):
    def test_rejects_an_arc_that_claims_exactness_without_a_centre(self) -> None:
        def mutate(d):
            for bar in d["reinforcement"]["bars"]:
                for seg in bar["segments"]:
                    if seg["kind"] == "arc":
                        del seg["centre"]
                        return

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "ARC_WITHOUT_CENTRE_OR_DECLARATION")

    def test_accepts_a_declared_approximation(self) -> None:
        """A missing centre is allowed only when the approximation is declared."""

        def mutate(d):
            for bar in d["reinforcement"]["bars"]:
                for seg in bar["segments"]:
                    if seg["kind"] == "arc":
                        del seg["centre"]
                        seg["arcApproximated"] = True
                        return

        parsed = fx.parse_object(fx.mutated(mutate))
        approximated = [s for _, _, s in parsed.arc_segments() if s.arc_approximated]
        self.assertEqual(len(approximated), 1)

    def test_rejects_declaring_an_approximation_while_supplying_a_centre(self) -> None:
        def mutate(d):
            for bar in d["reinforcement"]["bars"]:
                for seg in bar["segments"]:
                    if seg["kind"] == "arc":
                        seg["arcApproximated"] = True
                        return

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "ARC_APPROXIMATION_CONTRADICTION")

    def test_the_real_document_declares_no_approximations(self) -> None:
        arcs = fx.handoff().arc_segments()
        self.assertEqual(len(arcs), fx.EXPECTED_ARC_COUNT)
        self.assertEqual(
            sum(1 for _, _, s in arcs if s.arc_approximated), fx.EXPECTED_APPROXIMATED_ARCS
        )
        self.assertTrue(all(s.centre is not None for _, _, s in arcs))


class CheckPolicyValidationTest(unittest.TestCase):
    def test_rejects_a_cross_check_invitation_without_a_verdict(self) -> None:
        def mutate(d):
            check = next(c for c in d["checks"] if c["checkKind"] == "reinforcementContainment")
            check["consumerObservationPolicy"] = "MAY_CROSS_CHECK"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "CROSS_CHECK_WITHOUT_VERDICT")

    def test_rejects_findings_on_an_unevaluated_check(self) -> None:
        def mutate(d):
            check = next(c for c in d["checks"] if c["checkKind"] == "reinforcementContainment")
            check["findings"] = [{"findingId": "f:1", "severity": "warning"}]

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "FINDINGS_WITHOUT_EVALUATION")

    def test_rejects_a_missing_not_evaluated_reason(self) -> None:
        def mutate(d):
            check = next(c for c in d["checks"] if c["evaluationStatus"] == "NOT_EVALUATED")
            check.pop("notEvaluatedReason", None)

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "MISSING_NOT_EVALUATED_REASON")

    def test_rejects_an_internal_interface_declared_exposed(self) -> None:
        def mutate(d):
            d["concrete"]["interfaces"][0]["exposure"] = "exposed"

        with self.assertRaises(StructureError) as ctx:
            fx.parse_object(fx.mutated(mutate))
        self.assertEqual(ctx.exception.code, "INTERNAL_INTERFACE_MISDECLARED")


if __name__ == "__main__":
    unittest.main()
