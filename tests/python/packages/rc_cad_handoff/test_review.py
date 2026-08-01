"""The review artifact: content, vocabulary and determinism."""

from __future__ import annotations

import json
import unittest

from rc_cad_handoff.crosscheck import AGREEMENT_BAND_M, cross_check
from rc_cad_handoff.manifest import CONTRACT
from rc_cad_handoff.review import REVIEW_FORMAT, build_review, serialise
from rc_cad_handoff.status import Comparison, IssueKind, Provenance, Realisation

from tests.python.packages.rc_cad_handoff import _support as fx


def _review():
    h, model = fx.handoff(), fx.model()
    return build_review(h, model, fx.cross_check_result(), None)


class ReviewIdentityTest(unittest.TestCase):
    def test_names_its_own_format_and_the_source_contract(self) -> None:
        review = _review()
        self.assertEqual(review["reviewFormat"], REVIEW_FORMAT)
        self.assertEqual(review["reviewFormatVersion"], 1)
        self.assertEqual(review["source"]["contract"], CONTRACT)
        self.assertEqual(review["source"]["schemaVersion"], 1)

    def test_records_the_manifest_hash_and_size(self) -> None:
        review = _review()
        self.assertEqual(review["source"]["manifestSha256"], fx.MANIFEST_SHA256)
        self.assertEqual(review["source"]["manifestSizeBytes"], fx.MANIFEST_SIZE_BYTES)

    def test_echoes_every_revision_for_staleness_detection(self) -> None:
        review = _review()
        self.assertEqual(review["source"]["revisions"], dict(fx.handoff().revisions))
        self.assertEqual(review["source"]["revisions"]["detailing"], 3)
        self.assertEqual(review["source"]["revisions"]["demand"], 2)

    def test_propagates_the_certificate_and_subject(self) -> None:
        review = _review()
        self.assertEqual(review["source"]["certificate"]["maturity"], "IMPLEMENTED_PROVISIONAL")
        self.assertEqual(review["source"]["subject"]["name"], "Z1")
        self.assertEqual(review["source"]["assembly"]["completeness"], "partialConnectionOnly")

    def test_records_units_tolerances_and_tooling(self) -> None:
        review = _review()
        self.assertEqual(review["units"]["length"], "m")
        self.assertEqual(review["units"]["barDiameter"], "mm")
        self.assertEqual(review["tolerances"]["agreementBandM"], AGREEMENT_BAND_M)
        self.assertTrue(review["tolerances"]["agreementBandRationale"])
        self.assertIn("occt", review["tooling"])
        self.assertIn("build123d", review["tooling"])
        self.assertNotIn(review["tooling"]["occt"], ("unknown", "unavailable"))


class ReconciliationSectionTest(unittest.TestCase):
    def test_concrete_reconciliation_covers_both_components(self) -> None:
        section = _review()["concreteReconciliation"]
        self.assertEqual(section["declaredComponentCount"], fx.EXPECTED_BODY_COUNT)
        self.assertEqual(section["realisedSolidCount"], fx.EXPECTED_BODY_COUNT)
        self.assertTrue(section["componentsMatch"])
        ids = {c["bodyId"] for c in section["components"]}
        self.assertEqual(ids, {fx.FOOTING_BODY_ID, fx.COLUMN_BODY_ID})

    def test_marks_the_truncated_face_as_not_a_cover_surface(self) -> None:
        section = _review()["concreteReconciliation"]
        column = next(c for c in section["components"] if c["bodyId"] == fx.COLUMN_BODY_ID)
        self.assertEqual(column["truncatedFaces"], ["top"])
        self.assertTrue(column["truncatedFacesAreNotCoverSurfaces"])
        footing = next(c for c in section["components"] if c["bodyId"] == fx.FOOTING_BODY_ID)
        self.assertEqual(footing["truncatedFaces"], [])

    def test_records_the_interface_as_internal_with_intentional_passage(self) -> None:
        section = _review()["concreteReconciliation"]
        self.assertEqual(len(section["interfaces"]), fx.EXPECTED_INTERFACE_COUNT)
        interface = section["interfaces"][0]
        self.assertEqual(interface["exposure"], "internal")
        self.assertTrue(interface["isInternalContact"])
        self.assertTrue(interface["intentionalPassageIsNotAFailure"])
        self.assertTrue(interface["intentionalBarPassageIds"])

    def test_reinforcement_reconciliation_matches_the_declared_counts(self) -> None:
        section = _review()["reinforcementReconciliation"]
        self.assertEqual(section["declaredBarCount"], fx.EXPECTED_BAR_COUNT)
        self.assertEqual(section["realisedSolidCount"], fx.EXPECTED_BAR_COUNT)
        self.assertTrue(section["barsMatch"])
        self.assertEqual(section["totalSolidCount"], fx.EXPECTED_BAR_COUNT + fx.EXPECTED_BODY_COUNT)

        by_kind = {f["kind"]: f for f in section["families"]}
        self.assertEqual(by_kind["columnDowel"]["realisedSolidCount"], fx.EXPECTED_DOWEL_COUNT)
        self.assertEqual(by_kind["starterTie"]["realisedSolidCount"], fx.EXPECTED_TIE_COUNT)
        self.assertTrue(all(f["matches"] for f in section["families"]))

    def test_every_bar_row_keeps_its_stable_identity_and_metadata(self) -> None:
        section = _review()["reinforcementReconciliation"]
        for row in section["bars"]:
            with self.subTest(bar=row["barId"]):
                bar = fx.handoff().bar(row["barId"])
                self.assertEqual(row["mark"], bar.mark)
                self.assertEqual(row["familyId"], bar.family_id)
                self.assertEqual(row["diameterMm"], bar.diameter_mm)
                self.assertEqual(row["declaredCuttingLength"], bar.cutting_length)
                self.assertEqual(row["ownerElementIds"], list(bar.owner_element_ids))
                self.assertTrue(row["cuttingLengthMatchesDevelopedLength"])
                self.assertEqual(row["realisation"], str(Realisation.EXACT))

    def test_marks_reconcile_quantity_against_bar_count(self) -> None:
        section = _review()["reinforcementReconciliation"]
        self.assertEqual(len(section["marks"]), fx.EXPECTED_MARK_COUNT)
        for mark in section["marks"]:
            with self.subTest(mark=mark["mark"]):
                self.assertTrue(mark["quantityMatchesBarCount"])


class ApproximationInventoryTest(unittest.TestCase):
    def test_reports_thirty_eight_exact_arcs_and_no_approximations(self) -> None:
        inventory = _review()["approximationInventory"]
        self.assertEqual(inventory["totalArcCount"], fx.EXPECTED_ARC_COUNT)
        self.assertEqual(inventory["exactArcCount"], fx.EXPECTED_ARC_COUNT)
        self.assertEqual(inventory["approximatedArcCount"], fx.EXPECTED_APPROXIMATED_ARCS)
        self.assertEqual(inventory["approximatedArcs"], [])
        self.assertEqual(inventory["chordSubstitutionsMade"], 0)

    def test_states_that_arcs_come_from_their_centres(self) -> None:
        inventory = _review()["approximationInventory"]
        self.assertIn("supplied centre", inventory["arcRealisationBasis"])
        self.assertIn("do not determine an arc", inventory["arcRealisationBasis"])

    def test_reports_nominal_bend_deviations_separately_from_approximations(self) -> None:
        """A nominal-parameter mismatch is not a geometric approximation."""
        inventory = _review()["approximationInventory"]
        deviations = inventory["nominalBendParameterDeviations"]
        self.assertEqual(len(deviations), 12)
        self.assertEqual(inventory["approximatedArcCount"], 0)
        for row in deviations:
            with self.subTest(bar=row["barId"], segment=row["segmentIndex"]):
                self.assertNotEqual(row["declaredRadius"], row["realisedRadius"])
                self.assertIsNotNone(row["radiusDeviation"])
        self.assertIn("no chord was", inventory["nominalBendParameterNote"])


class ProvenanceSeparationTest(unittest.TestCase):
    def test_authoritative_findings_are_echoed_and_labelled(self) -> None:
        review = _review()
        findings = review["authoritativeFindings"]
        self.assertEqual(len(findings), fx.EXPECTED_PROHIBITED_OVERLAPS)
        declared = {f.finding_id for f in fx.collision_check().findings}
        self.assertEqual({f["findingId"] for f in findings}, declared)
        for finding in findings:
            with self.subTest(finding=finding["findingId"]):
                self.assertEqual(finding["provenance"], str(Provenance.AUTHORITATIVE_STABILEO))
                self.assertEqual(finding["authority"], "stabileo")

    def test_cad_observations_are_labelled_as_observations(self) -> None:
        review = _review()
        for pair in review["cadObservations"]["barPairs"]:
            with self.subTest(pair=(pair["barIdA"], pair["barIdB"])):
                self.assertEqual(pair["provenance"], str(Provenance.CAD_OBSERVATION))
        for cover in review["cadObservations"]["cover"]:
            self.assertEqual(cover["provenance"], str(Provenance.CAD_OBSERVATION))
            self.assertFalse(cover["isRegulatoryVerdict"])

    def test_producer_numbers_and_cad_numbers_live_in_separate_fields(self) -> None:
        review = _review()
        for pair in review["cadObservations"]["barPairs"]:
            with self.subTest(pair=(pair["barIdA"], pair["barIdB"])):
                self.assertIn("stabileoMeasured", pair)
                self.assertIn("cadClearance", pair)
                self.assertNotEqual(pair["stabileoMeasured"], None)
                self.assertEqual(pair["comparison"], str(Comparison.AGREEMENT))
                self.assertTrue(pair["withinAgreementBand"])

    def test_the_full_status_vocabulary_is_used(self) -> None:
        review = _review()
        blob = json.dumps(review)
        for token in (
            str(Provenance.AUTHORITATIVE_STABILEO),
            str(Provenance.CAD_OBSERVATION),
            str(Comparison.AGREEMENT),
            str(Comparison.NOT_COMPARABLE),
            str(Comparison.NOT_EVALUATED),
            str(Comparison.OUT_OF_SCOPE),
            str(Realisation.EXACT),
        ):
            with self.subTest(token=token):
                self.assertIn(token, blob)


class ObservationPolicySectionTest(unittest.TestCase):
    def test_records_every_check_with_its_policy_and_the_action_taken(self) -> None:
        rows = {r["checkId"]: r for r in _review()["observationPolicy"]}
        self.assertEqual(len(rows), len(fx.handoff().checks))

        column = rows["check:concreteCover:column:1"]
        self.assertEqual(column["consumerObservationPolicy"], "OUT_OF_SCOPE")
        self.assertEqual(column["consumerAction"], "NOT_MEASURED")

        footing = rows["check:concreteCover:footing:1"]
        self.assertEqual(footing["consumerObservationPolicy"], "MAY_OBSERVE_NOT_COMPARABLE")
        self.assertEqual(footing["consumerAction"], "OBSERVED_NOT_COMPARABLE")
        self.assertEqual(footing["evaluationStatus"], "NOT_EVALUATED")

        collision = rows["check:barCollision:footing:1"]
        self.assertEqual(collision["consumerAction"], "CROSS_CHECKED")

    def test_never_converts_not_evaluated_into_a_pass(self) -> None:
        for row in _review()["observationPolicy"]:
            with self.subTest(check=row["checkId"]):
                if row["evaluationStatus"] == "NOT_EVALUATED":
                    self.assertTrue(row["neverConvertedToPass"])
                    self.assertNotEqual(row["producerVerdictStatus"], "EVALUATED")
                    self.assertTrue(row["notEvaluatedReason"])


class SummaryTest(unittest.TestCase):
    def test_summary_reports_the_headline_counts(self) -> None:
        summary = _review()["summary"]
        self.assertEqual(summary["concreteComponents"], fx.EXPECTED_BODY_COUNT)
        self.assertEqual(summary["barSolids"], fx.EXPECTED_BAR_COUNT)
        self.assertEqual(summary["totalArcs"], fx.EXPECTED_ARC_COUNT)
        self.assertEqual(summary["approximatedArcs"], 0)
        self.assertEqual(summary["crossCheckedPairCount"], fx.EXPECTED_PROHIBITED_OVERLAPS)
        self.assertEqual(summary["agreementCount"], fx.EXPECTED_PROHIBITED_OVERLAPS)
        self.assertEqual(summary["disagreementCount"], 0)

    def test_is_never_a_clean_pass_while_conditions_are_declared(self) -> None:
        summary = _review()["summary"]
        self.assertFalse(summary["cleanPass"])
        self.assertTrue(summary["blockedByUnsupportedConditions"])
        self.assertGreater(summary["unsupportedConditionCount"], 0)
        self.assertIn("NOT_EVALUATED", summary["cleanPassRationale"])


class IssueTest(unittest.TestCase):
    def test_declared_blockers_appear_as_issues(self) -> None:
        kinds = {i["kind"] for i in _review()["issues"]}
        self.assertIn(str(IssueKind.UNSUPPORTED_CONDITION), kinds)

    def test_no_disagreement_issue_on_this_document(self) -> None:
        kinds = [i["kind"] for i in _review()["issues"]]
        self.assertNotIn(str(IssueKind.CROSS_CHECK_DISAGREEMENT), kinds)


class DeterminismTest(unittest.TestCase):
    def test_serialisation_is_byte_stable_for_the_same_input(self) -> None:
        h, model = fx.handoff(), fx.model()
        first = serialise(build_review(h, model, cross_check(h, model), None))
        second = serialise(build_review(h, model, cross_check(h, model), None))
        self.assertEqual(first, second)

    def test_keys_are_sorted_at_every_level(self) -> None:
        text = serialise(_review())
        reparsed = json.loads(text)
        self.assertEqual(text, json.dumps(reparsed, sort_keys=True, indent=2, ensure_ascii=False) + "\n")

    def test_carries_no_timestamp(self) -> None:
        """A timestamp makes two identical reviews differ and a stale one look fresh."""
        import re

        text = serialise(_review())
        self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        for suspicious in ("generatedAt", "timestamp", "reviewedAt"):
            self.assertNotIn(f'"{suspicious}"', text)

    def test_ends_with_a_single_trailing_newline(self) -> None:
        text = serialise(_review())
        self.assertTrue(text.endswith("}\n"))
        self.assertFalse(text.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
