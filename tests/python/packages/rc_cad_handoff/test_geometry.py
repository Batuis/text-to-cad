"""Realised geometry.

Every assertion here is made against solids and curves OCCT actually built. Where
a numeric expectation appears it is derived from the manifest's own values inside
the test, not transcribed from a previous run.
"""

from __future__ import annotations

import math
import unittest

from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Pnt

from rc_cad_handoff.errors import GeometryError
from rc_cad_handoff.geometry import build_bar, realise, volume_of

from tests.python.packages.rc_cad_handoff import _support as fx


def _distance_to_point(shape, point: gp_Pnt) -> float:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex

    vertex = BRepBuilderAPI_MakeVertex(point).Vertex()
    op = BRepExtrema_DistShapeShape(shape, vertex)
    op.Perform()
    return op.Value()


class ConcreteGeometryTest(unittest.TestCase):
    def test_realises_both_concrete_components_as_distinct_solids(self) -> None:
        model = fx.model()
        self.assertEqual(len(model.concrete), fx.EXPECTED_BODY_COUNT)
        self.assertIn(fx.FOOTING_BODY_ID, model.concrete)
        self.assertIn(fx.COLUMN_BODY_ID, model.concrete)
        self.assertIsNot(
            model.concrete[fx.FOOTING_BODY_ID].solid, model.concrete[fx.COLUMN_BODY_ID].solid
        )

    def test_component_volumes_match_their_declared_dimensions(self) -> None:
        h, model = fx.handoff(), fx.model()
        for body in h.bodies:
            geometry = model.concrete[body.body_id]
            expected = body.shape.b * body.shape.length * body.shape.height
            self.assertAlmostEqual(geometry.solid_volume, expected, places=9)

    def test_preserves_the_footing_dimensions_and_extent(self) -> None:
        h, model = fx.handoff(), fx.model()
        footing = h.body(fx.FOOTING_BODY_ID)
        geometry = model.concrete[fx.FOOTING_BODY_ID]
        self.assertAlmostEqual(footing.shape.b, 2.0, places=9)
        self.assertAlmostEqual(footing.shape.length, 2.0, places=9)
        self.assertAlmostEqual(footing.shape.height, 0.5, places=9)
        z_min, z_max = fx.footing_extent()
        self.assertAlmostEqual(geometry.z_min, z_min, places=9)
        self.assertAlmostEqual(geometry.z_max, z_max, places=9)

    def test_column_stub_top_is_a_truncation_not_a_surface(self) -> None:
        h = fx.handoff()
        column = h.body(fx.COLUMN_BODY_ID)
        self.assertEqual(column.truncated_faces, ("top",))
        self.assertEqual(h.body(fx.FOOTING_BODY_ID).truncated_faces, ())

    def test_column_stub_sits_on_the_interface_elevation(self) -> None:
        h, model = fx.handoff(), fx.model()
        interface = h.interface(fx.INTERFACE_ID)
        stub = model.concrete[fx.COLUMN_BODY_ID]
        footing = model.concrete[fx.FOOTING_BODY_ID]
        # The contact plane is simultaneously the footing top and the stub base.
        self.assertAlmostEqual(stub.z_min, interface.elevation, places=9)
        self.assertAlmostEqual(footing.z_max, interface.elevation, places=9)


class BarSolidTest(unittest.TestCase):
    def test_realises_one_solid_per_declared_bar(self) -> None:
        model = fx.model()
        self.assertEqual(len(model.bars), fx.EXPECTED_BAR_COUNT)
        self.assertEqual(model.solid_count, fx.EXPECTED_BAR_COUNT + fx.EXPECTED_BODY_COUNT)

    def test_family_membership_matches_the_declared_split(self) -> None:
        model = fx.model()
        self.assertEqual(len(model.bars_of_family(fx.DOWEL_FAMILY_ID)), fx.EXPECTED_DOWEL_COUNT)
        self.assertEqual(len(model.bars_of_family(fx.TIE_FAMILY_ID)), fx.EXPECTED_TIE_COUNT)

    def test_bar_solids_have_positive_volume_close_to_the_swept_nominal(self) -> None:
        """A capped sweep of a circular profile has the volume of a bent cylinder."""
        for bar in fx.model().bars.values():
            with self.subTest(bar=bar.bar_id):
                self.assertGreater(bar.solid_volume, 0.0)
                ratio = bar.solid_volume / bar.nominal_volume
                # Torus segments at bends account for the small departure.
                self.assertAlmostEqual(ratio, 1.0, delta=0.005)

    def test_uses_the_supplied_diameter(self) -> None:
        h, model = fx.handoff(), fx.model()
        for bar in h.bars:
            with self.subTest(bar=bar.bar_id):
                self.assertAlmostEqual(
                    model.bars[bar.bar_id].radius_m, bar.diameter_mm / 2000.0, places=12
                )
        diameters = {b.diameter_mm for b in h.bars}
        self.assertEqual(diameters, {6.0, 16.0})

    def test_preserves_cutting_length_metadata(self) -> None:
        h, model = fx.handoff(), fx.model()
        for bar in h.bars:
            geometry = model.bars[bar.bar_id]
            with self.subTest(bar=bar.bar_id):
                self.assertEqual(geometry.cutting_length, bar.cutting_length)
                # The producer's developed length is the sum of its own segments.
                self.assertAlmostEqual(
                    geometry.declared_developed_length, bar.cutting_length, places=6
                )

    def test_preserves_stable_bar_family_and_mark_identity(self) -> None:
        h, model = fx.handoff(), fx.model()
        for bar in h.bars:
            geometry = model.bars[bar.bar_id]
            with self.subTest(bar=bar.bar_id):
                self.assertEqual(geometry.bar_id, bar.bar_id)
                self.assertEqual(geometry.family_id, bar.family_id)
                self.assertEqual(geometry.mark, bar.mark)

    def test_centreline_endpoints_are_the_supplied_endpoints(self) -> None:
        """The realised curve starts and ends where the producer said it does."""
        h, model = fx.handoff(), fx.model()
        for bar in h.bars:
            geometry = model.bars[bar.bar_id]
            first, last = bar.segments[0].start, bar.segments[-1].end
            with self.subTest(bar=bar.bar_id):
                self.assertLess(
                    _distance_to_point(geometry.centreline, gp_Pnt(*first.as_tuple())), 1e-9
                )
                self.assertLess(
                    _distance_to_point(geometry.centreline, gp_Pnt(*last.as_tuple())), 1e-9
                )


class ArcRealisationTest(unittest.TestCase):
    def test_realises_every_declared_arc(self) -> None:
        arcs = fx.model().all_arcs
        self.assertEqual(len(arcs), fx.EXPECTED_ARC_COUNT)

    def test_no_arc_was_replaced_by_a_chord(self) -> None:
        """Each realised arc is longer than its chord by its own sagitta."""
        h, model = fx.handoff(), fx.model()
        realised = {(a.bar_id, a.segment_index): a for a in model.all_arcs}
        for bar, index, seg in h.arc_segments():
            arc = realised[(bar.bar_id, index)]
            chord = math.dist(seg.start.as_tuple(), seg.end.as_tuple())
            with self.subTest(bar=bar.bar_id, segment=index):
                self.assertGreater(arc.realised_length, chord)

    def test_arc_endpoints_are_equidistant_from_the_supplied_centre(self) -> None:
        """This is what makes (start, end, centre) a genuine circular arc."""
        for arc in fx.model().all_arcs:
            with self.subTest(bar=arc.bar_id, segment=arc.segment_index):
                self.assertLess(arc.radius_asymmetry, 1e-12)

    def test_arc_geometry_comes_from_the_centre_not_the_nominal_radius(self) -> None:
        """Twelve hooks realise a radius that differs from the declared nominal.

        The realised value is the one implied by the supplied centre. Enforcing
        the nominal radius instead would reject exact geometry.
        """
        deviating = [
            a
            for a in fx.model().all_arcs
            if a.radius_deviation is not None and abs(a.radius_deviation) > 1e-9
        ]
        self.assertEqual(len(deviating), 12)
        for arc in deviating:
            with self.subTest(bar=arc.bar_id, segment=arc.segment_index):
                # The realised radius is what the centre implies, to machine precision.
                self.assertAlmostEqual(
                    arc.realised_radius,
                    arc.realised_length / math.radians(arc.realised_sweep_deg),
                    places=12,
                )
                self.assertGreater(abs(arc.radius_deviation), 1e-9)

    def test_the_other_arcs_match_their_nominal_parameters_exactly(self) -> None:
        matching = [
            a
            for a in fx.model().all_arcs
            if a.radius_deviation is not None and abs(a.radius_deviation) <= 1e-9
        ]
        self.assertEqual(len(matching), fx.EXPECTED_ARC_COUNT - 12)
        for arc in matching:
            with self.subTest(bar=arc.bar_id, segment=arc.segment_index):
                self.assertAlmostEqual(arc.length_deviation, 0.0, places=9)
                self.assertAlmostEqual(arc.sweep_deviation or 0.0, 0.0, places=9)


class VisibleFailureTest(unittest.TestCase):
    """An arc that cannot be realised exactly must raise, never degrade quietly."""

    def _first_arc_bar(self):
        h = fx.handoff()
        for bar in h.bars:
            if any(s.is_arc for s in bar.segments):
                return bar
        raise AssertionError("the fixture has no arcs")

    def test_fails_visibly_when_an_exact_arc_has_no_centre(self) -> None:
        import dataclasses

        bar = self._first_arc_bar()
        segments = []
        for seg in bar.segments:
            if seg.is_arc and seg.centre is not None:
                seg = dataclasses.replace(seg, centre=None)
            segments.append(seg)
        broken = dataclasses.replace(bar, segments=tuple(segments))

        with self.assertRaises(GeometryError) as ctx:
            build_bar(broken)
        self.assertEqual(ctx.exception.code, "ARC_WITHOUT_CENTRE")

    def test_fails_visibly_when_the_centre_is_not_equidistant(self) -> None:
        import dataclasses

        from rc_cad_handoff.manifest import Point3

        bar = self._first_arc_bar()
        segments = []
        for seg in bar.segments:
            if seg.is_arc and seg.centre is not None:
                moved = Point3(seg.centre.x + 0.01, seg.centre.y, seg.centre.z)
                seg = dataclasses.replace(seg, centre=moved)
            segments.append(seg)
        broken = dataclasses.replace(bar, segments=tuple(segments))

        with self.assertRaises(GeometryError) as ctx:
            build_bar(broken)
        self.assertEqual(ctx.exception.code, "ARC_ENDPOINTS_NOT_EQUIDISTANT")

    def test_fails_visibly_when_segments_do_not_connect(self) -> None:
        import dataclasses

        from rc_cad_handoff.manifest import Point3

        h = fx.handoff()
        bar = next(b for b in h.bars if len(b.segments) >= 2)
        first = bar.segments[0]
        detached = dataclasses.replace(
            first, end=Point3(first.end.x + 0.5, first.end.y + 0.5, first.end.z + 0.5)
        )
        broken = dataclasses.replace(bar, segments=(detached,) + bar.segments[1:])

        with self.assertRaises(GeometryError) as ctx:
            build_bar(broken)
        self.assertEqual(ctx.exception.code, "WIRE_NOT_CONNECTED")


class ReconciliationTest(unittest.TestCase):
    def test_realise_reconciles_counts_against_the_document(self) -> None:
        h = fx.handoff()
        model = realise(h)
        self.assertEqual(len(model.bars), len(h.bars))
        self.assertEqual(len(model.concrete), len(h.bodies))

    def test_volume_helper_agrees_with_a_known_box(self) -> None:
        model = fx.model()
        footing = model.concrete[fx.FOOTING_BODY_ID]
        self.assertAlmostEqual(volume_of(footing.solid), 2.0 * 2.0 * 0.5, places=9)


if __name__ == "__main__":
    unittest.main()
