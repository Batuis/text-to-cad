"""Independent cross-check behaviour.

The measurements asserted here are computed by OCCT from the realised solids and
curves. Where a value is compared against a number, that number comes either
from the producer's own document or from arithmetic on the manifest performed
inside the test — never from a recorded CAD result.
"""

from __future__ import annotations

import unittest

from rc_cad_handoff.crosscheck import (
    AGREEMENT_BAND_M,
    classify,
    cross_check,
    observe_pair,
)
from rc_cad_handoff.status import Collision, Comparison, IssueKind

from tests.python.packages.rc_cad_handoff import _support as fx


class ReportedPairResolutionTest(unittest.TestCase):
    def test_resolves_all_twelve_authoritative_pairs(self) -> None:
        check = fx.collision_check()
        self.assertEqual(len(check.findings), fx.EXPECTED_PROHIBITED_OVERLAPS)
        observations = fx.cross_check_result().reported_pairs
        self.assertEqual(len(observations), fx.EXPECTED_PROHIBITED_OVERLAPS)

        declared = {
            tuple(sorted((f.bar_id_a, f.bar_id_b)))
            for f in check.findings
        }
        measured = {tuple(sorted((o.bar_id_a, o.bar_id_b))) for o in observations}
        self.assertEqual(declared, measured)

    def test_every_pair_resolves_to_two_real_bar_solids(self) -> None:
        model = fx.model()
        for observation in fx.cross_check_result().reported_pairs:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertIn(observation.bar_id_a, model.bars)
                self.assertIn(observation.bar_id_b, model.bars)

    def test_all_twelve_are_the_producers_prohibited_overlaps(self) -> None:
        for observation in fx.cross_check_result().reported_pairs:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertEqual(observation.stabileo_pair_class, "prohibitedOverlap")


class IndependentClassificationTest(unittest.TestCase):
    def test_occt_independently_classifies_every_reported_pair_as_colliding(self) -> None:
        for observation in fx.cross_check_result().reported_pairs:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertIn(
                    observation.classification, (Collision.INTERSECTING, Collision.CONTACT)
                )
                self.assertTrue(observation.classification_agrees)

    def test_solid_distance_is_zero_for_every_colliding_pair(self) -> None:
        """Solid distance confirms contact-or-overlap but yields no signed depth."""
        for observation in fx.cross_check_result().reported_pairs:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertIsNotNone(observation.solid_distance)
                self.assertLessEqual(observation.solid_distance, 1e-9)
                self.assertTrue(
                    any("signed penetration depth" in n for n in observation.notes),
                    "the review must state why no signed depth comes from solid distance",
                )

    def test_signed_clearance_is_derived_from_exact_geometry(self) -> None:
        """clearance == centreline distance - sum of radii, measured on exact curves."""
        model = fx.model()
        for observation in fx.cross_check_result().reported_pairs:
            a = model.bars[observation.bar_id_a]
            b = model.bars[observation.bar_id_b]
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertAlmostEqual(
                    observation.cad_clearance,
                    observation.centreline_distance - (a.radius_m + b.radius_m),
                    places=12,
                )
                self.assertLess(observation.cad_clearance, 0.0)

    def test_records_the_degenerate_boolean_rather_than_misclassifying(self) -> None:
        """Near-tangential surfaces make the intersection volume unreliable.

        Two pairs of dowel legs are coplanar arcs that leave the same elevation
        tangent to horizontal, curve in opposite directions and cross at a
        shallow angle. OCCT's boolean returns an empty result for that
        near-tangential meeting. The classification must come from the exact
        centreline distance, and the numerical limitation must be recorded.

        Not to be confused with collinear bars: the pairs that overlap along
        exactly coincident centrelines boolean cleanly, and are covered by
        ``test_most_pairs_do_produce_a_positive_intersection_volume``.
        """
        degenerate = [
            o
            for o in fx.cross_check_result().reported_pairs
            if o.common_volume.ok
            and o.common_volume.volume is not None
            and o.common_volume.volume <= 1e-12
        ]
        self.assertTrue(degenerate, "expected at least one degenerate boolean in this cage")
        for observation in degenerate:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                # Still classified as interpenetrating, from the exact measurement.
                self.assertIs(observation.classification, Collision.INTERSECTING)
                notes = " ".join(observation.notes)
                self.assertIn("numerically degenerate", notes)
                # The cause must be named accurately: these arcs cross, they
                # are not collinear, and calling them coincident sent a reader
                # looking at the wrong geometry.
                self.assertIn("near-tangential coplanar arc crossing", notes)
                self.assertNotIn("degenerate for coincident centrelines", notes)

    def test_most_pairs_do_produce_a_positive_intersection_volume(self) -> None:
        positive = [
            o
            for o in fx.cross_check_result().reported_pairs
            if o.common_volume.ok and (o.common_volume.volume or 0.0) > 1e-12
        ]
        self.assertGreaterEqual(len(positive), 10)


class AgreementTest(unittest.TestCase):
    def test_cad_agrees_with_every_authoritative_measurement(self) -> None:
        result = fx.cross_check_result()
        for observation in result.reported_pairs:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertIs(observation.comparison, Comparison.AGREEMENT)
                self.assertIsNotNone(observation.delta)
                self.assertLessEqual(observation.delta, AGREEMENT_BAND_M)
        self.assertEqual(result.disagreements, [])

    def test_worst_delta_is_far_inside_the_band(self) -> None:
        deltas = [o.delta for o in fx.cross_check_result().reported_pairs if o.delta is not None]
        self.assertEqual(len(deltas), fx.EXPECTED_PROHIBITED_OVERLAPS)
        self.assertLess(max(deltas), AGREEMENT_BAND_M)

    def test_the_authoritative_measurement_is_never_overwritten(self) -> None:
        check = fx.collision_check()
        by_pair = {
            tuple(sorted((f.bar_id_a, f.bar_id_b))): f.measured for f in check.findings
        }
        for observation in fx.cross_check_result().reported_pairs:
            key = tuple(sorted((observation.bar_id_a, observation.bar_id_b)))
            with self.subTest(pair=key):
                self.assertEqual(observation.stabileo_measured, by_pair[key])


class AgreementBandBoundaryTest(unittest.TestCase):
    """The band is a boundary, not a suggestion."""

    def test_classify_at_and_across_the_band(self) -> None:
        self.assertIs(classify(-AGREEMENT_BAND_M * 1.001), Collision.INTERSECTING)
        self.assertIs(classify(-AGREEMENT_BAND_M), Collision.CONTACT)
        self.assertIs(classify(0.0), Collision.CONTACT)
        self.assertIs(classify(AGREEMENT_BAND_M), Collision.CONTACT)
        self.assertIs(classify(AGREEMENT_BAND_M * 1.001), Collision.SEPARATED)

    def _perturbed_comparison(self, offset: float) -> Comparison:
        import dataclasses

        model = fx.model()
        finding = fx.collision_check().findings[0]
        shifted = dataclasses.replace(finding, measured=finding.measured + offset)
        observation = observe_pair(
            model, finding.bar_id_a, finding.bar_id_b, finding=shifted, reportable=True
        )
        return observation.comparison

    def test_just_inside_the_band_agrees(self) -> None:
        self.assertIs(self._perturbed_comparison(AGREEMENT_BAND_M * 0.9), Comparison.AGREEMENT)

    def test_just_outside_the_band_disagrees(self) -> None:
        self.assertIs(self._perturbed_comparison(AGREEMENT_BAND_M * 1.5), Comparison.DISAGREEMENT)

    def test_a_disagreement_becomes_its_own_issue_kind(self) -> None:
        import dataclasses

        h = fx.handoff()
        model = fx.model()
        collision = fx.collision_check()
        broken_findings = tuple(
            dataclasses.replace(f, measured=(f.measured or 0.0) + 0.01) for f in collision.findings
        )
        broken_check = dataclasses.replace(collision, findings=broken_findings)
        checks = tuple(
            broken_check if c.check_id == collision.check_id else c for c in h.checks
        )
        result = cross_check(dataclasses.replace(h, checks=checks), model)

        self.assertEqual(len(result.disagreements), fx.EXPECTED_PROHIBITED_OVERLAPS)
        kinds = {i["kind"] for i in result.issues}
        self.assertIn(str(IssueKind.CROSS_CHECK_DISAGREEMENT), kinds)
        # And the producer's number is still carried, not replaced.
        for observation in result.disagreements:
            self.assertIsNotNone(observation.stabileo_measured)
            self.assertIsNotNone(observation.cad_clearance)


class ReportableTest(unittest.TestCase):
    def test_a_non_reportable_pair_is_not_raised(self) -> None:
        """The producer suppresses some pair classes deliberately."""
        import dataclasses

        h = fx.handoff()
        requirements = tuple(
            dataclasses.replace(r, reportable=False) if r.bar_id_a is not None else r
            for r in h.clear_spacing_requirements
        )
        result = cross_check(
            dataclasses.replace(h, clear_spacing_requirements=requirements), fx.model()
        )
        self.assertEqual(result.reported_pairs, [])


class UnreportedPairTest(unittest.TestCase):
    def test_close_pairs_without_a_verdict_are_not_comparable(self) -> None:
        """No per-pair verdict means nothing to agree or disagree with."""
        unreported = fx.cross_check_result().unreported_pairs
        self.assertTrue(unreported)
        for observation in unreported:
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertIs(observation.comparison, Comparison.NOT_COMPARABLE)
                self.assertIsNone(observation.stabileo_measured)
                self.assertIsNone(observation.stabileo_finding_id)
                self.assertTrue(any("no per-pair verdict" in n for n in observation.notes))

    def test_unreported_pairs_are_never_counted_as_disagreements(self) -> None:
        result = fx.cross_check_result()
        self.assertEqual(result.disagreements, [])

    def test_the_close_pairs_are_tie_to_dowel_contacts(self) -> None:
        """Ties bear against the dowels they restrain; that is the detail working."""
        h = fx.handoff()
        family_of = h.family_of_bar
        for observation in fx.cross_check_result().unreported_pairs:
            families = {family_of[observation.bar_id_a], family_of[observation.bar_id_b]}
            with self.subTest(pair=(observation.bar_id_a, observation.bar_id_b)):
                self.assertEqual(families, {fx.DOWEL_FAMILY_ID, fx.TIE_FAMILY_ID})


class PolicyEnforcementTest(unittest.TestCase):
    def test_column_cover_is_out_of_scope_and_never_measured(self) -> None:
        result = fx.cross_check_result()
        out_of_scope_ids = {check_id for check_id, _ in result.out_of_scope}
        self.assertIn("check:concreteCover:column:1", out_of_scope_ids)
        # No cover observation may exist for the column stub.
        for observation in result.cover:
            self.assertNotEqual(observation.body_id, fx.COLUMN_BODY_ID)

    def test_containment_is_not_evaluated_and_never_becomes_a_pass(self) -> None:
        h = fx.handoff()
        containment = h.check_of_kind("reinforcementContainment")
        self.assertIsNotNone(containment)
        self.assertEqual(containment.evaluation_status, "NOT_EVALUATED")
        self.assertTrue(containment.may_observe_only)
        for observation in fx.cross_check_result().cover:
            with self.subTest(check=observation.check_id):
                self.assertIs(observation.comparison, Comparison.NOT_COMPARABLE)

    def test_out_of_scope_checks_are_recorded_with_their_reason(self) -> None:
        for _, reason in fx.cross_check_result().out_of_scope:
            self.assertTrue(reason)


class CoverObservationTest(unittest.TestCase):
    def _footing_cover(self):
        observations = [
            o for o in fx.cross_check_result().cover if o.check_id == "check:concreteCover:footing:1"
        ]
        self.assertEqual(len(observations), 1)
        return observations[0]

    def test_measures_only_inside_the_scoped_body(self) -> None:
        observation = self._footing_cover()
        self.assertEqual(observation.body_id, fx.FOOTING_BODY_ID)
        measured = [b for b in observation.bars if b.observed_cover is not None]
        self.assertTrue(measured)
        for bar in measured:
            with self.subTest(bar=bar.bar_id):
                self.assertIsNotNone(bar.volume_inside)
                self.assertGreater(bar.volume_inside, 0.0)

    def test_an_unmeasurable_clip_is_reported_not_read_as_absence(self) -> None:
        """A degenerate boolean must not be reported as "no material inside".

        One starter tie's legs are coplanar with the footing's top face, where
        OCCT's intersection returns an empty result even though the bar
        demonstrably shares a vertical band with the concrete.
        """
        observation = self._footing_cover()
        unmeasured = [b for b in observation.bars if b.observed_cover is None]
        self.assertTrue(unmeasured, "expected the coplanar tie to be unmeasurable here")
        for bar in unmeasured:
            with self.subTest(bar=bar.bar_id):
                self.assertTrue(
                    any("boolean returned an empty result" in n for n in bar.notes),
                    f"expected a stated numerical limitation, got {bar.notes}",
                )
                self.assertFalse(
                    any("no material inside" in n for n in bar.notes),
                    "a degenerate boolean must never be reported as an absence of material",
                )

    def test_the_numerical_limitation_reaches_the_issue_list(self) -> None:
        limitations = [
            i
            for i in fx.cross_check_result().issues
            if i["kind"] == str(IssueKind.NUMERICAL_LIMITATION)
        ]
        self.assertTrue(limitations)

    def test_excludes_the_internal_interface_face(self) -> None:
        observation = self._footing_cover()
        self.assertIn("top", observation.surface_scope.excluded_faces)
        self.assertNotIn("top", observation.surface_scope.measured_faces)
        self.assertTrue(
            any("internal concrete-to-concrete interface" in r for r in observation.surface_scope.reasons)
        )

    def test_measures_against_the_bottom_and_sides(self) -> None:
        observation = self._footing_cover()
        self.assertEqual(set(observation.surface_scope.measured_faces), {"bottom", "side"})

    def test_intentional_interface_passage_is_not_a_failure(self) -> None:
        observation = self._footing_cover()
        crossing = [b for b in observation.bars if b.crosses_excluded_interface]
        self.assertTrue(crossing)
        for bar in crossing:
            with self.subTest(bar=bar.bar_id):
                self.assertTrue(any("detail working" in n for n in bar.notes))

    def test_observed_cover_equals_the_geometric_distance_to_the_footing_bottom(self) -> None:
        """Derive the expectation from the manifest, then check CAD measured it."""
        h = fx.handoff()
        footing_z_min, _ = fx.footing_extent()
        observation = self._footing_cover()

        for bar_observation in observation.bars:
            if bar_observation.observed_cover is None:
                continue
            bar = h.bar(bar_observation.bar_id)
            lowest_centreline_z = min(
                min(seg.start.z, seg.end.z) for seg in bar.segments
            )
            expected_to_bottom = (lowest_centreline_z - bar.radius_m) - footing_z_min
            with self.subTest(bar=bar.bar_id):
                # The nearest exposed face for these bars is the footing soffit.
                self.assertAlmostEqual(
                    bar_observation.observed_cover, expected_to_bottom, places=6
                )

    def test_the_observation_is_labelled_as_geometry_not_certification(self) -> None:
        observation = self._footing_cover()
        self.assertTrue(any("not a regulatory verdict" in n for n in observation.notes))

    def test_a_shortfall_against_the_requirement_is_an_observation_not_a_verdict(self) -> None:
        observation = self._footing_cover()
        self.assertIsNotNone(observation.minimum_observed)
        if observation.minimum_observed < observation.required_distance:
            self.assertTrue(observation.below_requirement)
            issues = [
                i
                for i in fx.cross_check_result().issues
                if i["kind"] == str(IssueKind.COVER_OBSERVATION_BELOW_REQUIREMENT)
            ]
            self.assertTrue(issues)
            for issue in issues:
                self.assertEqual(issue["comparison"], str(Comparison.NOT_COMPARABLE))
                self.assertIn("not a breach verdict", str(issue["detail"]))


class BlockerTest(unittest.TestCase):
    def test_declared_unsupported_conditions_become_issues(self) -> None:
        result = fx.cross_check_result()
        codes = {
            i.get("code")
            for i in result.issues
            if i["kind"] == str(IssueKind.UNSUPPORTED_CONDITION)
        }
        self.assertIn("FOOTING_MAT_GEOMETRY_NOT_MODELED", codes)
        self.assertIn("COLUMN_COVER_OUT_OF_SCOPE", codes)


if __name__ == "__main__":
    unittest.main()
