"""The single boundary where cadpy decides what a kernel coordinate means.

These tests exist because the same number feeds two unrelated writers. If
``metres_per_source_unit`` ever returned one thing for STEP and another for
GLB, the two artifacts would disagree about physical size — which is exactly
the defect the source unit was introduced to fix.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from build123d.build_common import UNITS_PER_METER
from build123d.build_enums import Unit

from cadpy.glb_mesh_payload import CAD_TO_GLB_SCALE
from cadpy.length_unit import (
    metres_per_source_unit,
    resolve_source_length_unit,
    source_length_unit_name,
)

from tests.python.support.paths import REPO_ROOT


class DefaultIsMillimetresTest(unittest.TestCase):
    """Omitting the argument must be indistinguishable from passing Unit.MM.

    The parameter is spelled ``Unit | None`` rather than ``Unit = Unit.MM``
    because a ``Unit`` default would be evaluated at module import and drag
    build123d into ``cadpy.glb``. That makes "the default is Unit.MM" a
    property to verify rather than something the signature states, so it is
    pinned here.
    """

    def test_omitted_argument_equals_unit_mm(self) -> None:
        self.assertEqual(metres_per_source_unit(), metres_per_source_unit(Unit.MM))
        self.assertEqual(metres_per_source_unit(None), metres_per_source_unit(Unit.MM))

    def test_default_matches_the_historical_glb_constant(self) -> None:
        """The millimetre fast path must not drift from the value it replaced."""
        self.assertEqual(metres_per_source_unit(None), CAD_TO_GLB_SCALE)

    def test_resolve_reports_unit_mm(self) -> None:
        self.assertIs(resolve_source_length_unit(None), Unit.MM)
        self.assertIs(resolve_source_length_unit(), Unit.MM)
        self.assertEqual(source_length_unit_name(None), "mm")


class DerivationTest(unittest.TestCase):
    def test_scales_are_metres_per_kernel_unit(self) -> None:
        self.assertEqual(metres_per_source_unit(Unit.MM), 0.001)
        self.assertEqual(metres_per_source_unit(Unit.CM), 0.01)
        self.assertEqual(metres_per_source_unit(Unit.M), 1.0)

    def test_every_declared_unit_is_the_reciprocal_of_build123d(self) -> None:
        """One derivation, not a lookup table that could fall out of step."""
        for unit in Unit:
            with self.subTest(unit=unit):
                self.assertAlmostEqual(
                    metres_per_source_unit(unit), 1.0 / UNITS_PER_METER[unit], places=15
                )

    def test_imperial_units_are_exact_rather_than_refused(self) -> None:
        self.assertAlmostEqual(metres_per_source_unit(Unit.IN), 0.0254, places=12)


class RefusalTest(unittest.TestCase):
    """Guessing a scale yields a plausible model at the wrong size."""

    def test_non_unit_argument_is_refused(self) -> None:
        for bad in ("mm", 0.001, object()):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    metres_per_source_unit(bad)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resolve_source_length_unit("m")  # type: ignore[arg-type]


class ImportCostTest(unittest.TestCase):
    def test_importing_cadpy_glb_does_not_load_build123d(self) -> None:
        """Threading the unit through must not cost GLB consumers ~1.5 s.

        ``cadpy.glb`` deliberately keeps build123d out of its import path, and
        the source-unit parameter is typed under ``TYPE_CHECKING`` to preserve
        that. A stray module-level import would silently regress every GLB
        caller, so this asserts the property directly in a clean interpreter.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, cadpy.glb; "
                "print('build123d' in sys.modules)",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env={
                "PYTHONPATH": str(REPO_ROOT / "packages" / "cadpy" / "src"),
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(result.stdout.strip(), "False", result.stderr)


if __name__ == "__main__":
    unittest.main()
