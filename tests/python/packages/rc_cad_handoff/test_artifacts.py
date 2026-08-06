"""STEP and GLB generation, naming and reproducibility."""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path

from rc_cad_handoff.artifacts import (
    BAR_PREFIX,
    CONCRETE_PREFIX,
    build_assembly,
    step_content_hash,
    write_artifacts,
)

from tests.python.support.tmp_root import temporary_directory

from tests.python.packages.rc_cad_handoff import _support as fx


class AssemblyNamingTest(unittest.TestCase):
    def test_every_solid_is_individually_named_from_a_stable_id(self) -> None:
        h, model = fx.handoff(), fx.model()
        assembly = build_assembly(h, model)
        labels = [child.label for child in assembly.children]

        self.assertEqual(len(labels), fx.EXPECTED_BAR_COUNT + fx.EXPECTED_BODY_COUNT)
        self.assertEqual(len(set(labels)), len(labels), "names must be unique")

        for body in h.bodies:
            self.assertIn(f"{CONCRETE_PREFIX}{body.body_id}", labels)
        for bar in h.bars:
            self.assertIn(f"{BAR_PREFIX}{bar.bar_id}", labels)

    def test_a_reviewer_can_tell_the_four_groups_apart(self) -> None:
        h, model = fx.handoff(), fx.model()
        labels = [child.label for child in build_assembly(h, model).children]

        footing = [n for n in labels if n == f"{CONCRETE_PREFIX}{fx.FOOTING_BODY_ID}"]
        column = [n for n in labels if n == f"{CONCRETE_PREFIX}{fx.COLUMN_BODY_ID}"]
        dowels = [
            n
            for n in labels
            if n.startswith(BAR_PREFIX)
            and h.bar(n[len(BAR_PREFIX) :]).family_id == fx.DOWEL_FAMILY_ID
        ]
        ties = [
            n
            for n in labels
            if n.startswith(BAR_PREFIX)
            and h.bar(n[len(BAR_PREFIX) :]).family_id == fx.TIE_FAMILY_ID
        ]
        self.assertEqual(len(footing), 1)
        self.assertEqual(len(column), 1)
        self.assertEqual(len(dowels), fx.EXPECTED_DOWEL_COUNT)
        self.assertEqual(len(ties), fx.EXPECTED_TIE_COUNT)

    def test_concrete_components_remain_separate_solids(self) -> None:
        """A fused compound would destroy the distinction a reviewer needs."""
        h, model = fx.handoff(), fx.model()
        assembly = build_assembly(h, model)
        concrete = [c for c in assembly.children if c.label.startswith(CONCRETE_PREFIX)]
        self.assertEqual(len(concrete), fx.EXPECTED_BODY_COUNT)


class ArtifactGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temporary_directory(prefix="rc-cad-artifacts-")
        cls.out = Path(cls._tmp.name)
        cls.artifacts = write_artifacts(fx.handoff(), fx.model(), output_dir=cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_writes_a_step_file(self) -> None:
        step = self.artifacts.step
        self.assertTrue(step.path.exists())
        self.assertGreater(step.size_bytes, 0)
        head = step.path.read_bytes()[:200]
        self.assertTrue(head.startswith(b"ISO-10303-21;"))

    def test_writes_a_glb_file(self) -> None:
        glb = self.artifacts.glb
        self.assertTrue(glb.path.exists())
        payload = glb.path.read_bytes()
        magic, version, _ = struct.unpack("<III", payload[:12])
        self.assertEqual(magic, 0x46546C67)  # 'glTF'
        self.assertEqual(version, 2)

    def test_the_step_carries_the_manifest_hash_as_its_source_identity(self) -> None:
        from cadpy.step_metadata import read_text_to_cad_step_metadata

        metadata = read_text_to_cad_step_metadata(self.artifacts.step.path)
        self.assertEqual(metadata.get("sourceHash"), fx.handoff().manifest_sha256)
        self.assertEqual(metadata.get("entryKind"), "assembly")

    def test_body_names_survive_into_the_step(self) -> None:
        text = self.artifacts.step.path.read_text(encoding="utf-8", errors="replace")
        self.assertIn(f"{CONCRETE_PREFIX}{fx.FOOTING_BODY_ID}", text)
        self.assertIn(f"{CONCRETE_PREFIX}{fx.COLUMN_BODY_ID}", text)
        for bar in fx.handoff().bars:
            with self.subTest(bar=bar.bar_id):
                self.assertIn(f"{BAR_PREFIX}{bar.bar_id}", text)

    def test_records_paths_sizes_and_hashes(self) -> None:
        for record in (self.artifacts.step, self.artifacts.glb):
            with self.subTest(path=record.path.name):
                self.assertEqual(record.size_bytes, record.path.stat().st_size)
                self.assertEqual(len(record.sha256), 64)

    def test_the_glb_orientation_conversion_is_declared_not_silent(self) -> None:
        """glTF is Y-up; the conversion belongs here and must be stated."""
        review = self._review()
        self.assertIn("Y-up", review["artifacts"]["glbOrientation"]["note"])
        self.assertEqual(review["units"]["length"], "m")

    def _review(self):
        from rc_cad_handoff.crosscheck import cross_check
        from rc_cad_handoff.review import build_review

        h, model = fx.handoff(), fx.model()
        return build_review(h, model, cross_check(h, model), self.artifacts, relative_to=self.out)


_EXPORT_SCRIPT = """
import sys
from pathlib import Path
from rc_cad_handoff.artifacts import write_artifacts
from rc_cad_handoff.geometry import realise
from rc_cad_handoff.manifest import load_handoff

handoff = load_handoff(Path(sys.argv[1]))
artifacts = write_artifacts(handoff, realise(handoff), output_dir=Path(sys.argv[2]), stem="det")
print(artifacts.step.sha256)
print(artifacts.step.content_sha256)
print(artifacts.glb.sha256)
print(artifacts.step.path)
"""


def _export_in_subprocess(out_dir: Path) -> dict[str, str]:
    """Export once in a fresh interpreter.

    Determinism has to be measured across processes. The STEP writer carries
    process-global state, so comparing two exports inside one interpreter would
    measure that state rather than the reproducibility of the artifact.
    """
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)

    completed = subprocess.run(
        [sys.executable, "-c", _EXPORT_SCRIPT, str(fx.MANIFEST_PATH), str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root,
        check=True,
    )
    step_sha, step_content, glb_sha, step_path = completed.stdout.strip().splitlines()[-4:]
    return {
        "step_sha": step_sha,
        "step_content": step_content,
        "glb_sha": glb_sha,
        "step_path": step_path,
    }


class DeterminismTest(unittest.TestCase):
    """Reproducibility is reported honestly, never manufactured."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._a = temporary_directory(prefix="rc-cad-det-a-")
        cls._b = temporary_directory(prefix="rc-cad-det-b-")
        cls.first = _export_in_subprocess(Path(cls._a.name))
        cls.second = _export_in_subprocess(Path(cls._b.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._a.cleanup()
        cls._b.cleanup()

    def test_the_glb_is_byte_identical_across_runs(self) -> None:
        self.assertEqual(self.first["glb_sha"], self.second["glb_sha"])

    def test_the_step_geometry_and_names_are_reproducible_across_runs(self) -> None:
        self.assertEqual(self.first["step_content"], self.second["step_content"])

    def test_the_step_nondeterminism_is_confined_to_the_header_timestamp(self) -> None:
        first = Path(self.first["step_path"]).read_bytes().split(b"\n")
        second = Path(self.second["step_path"]).read_bytes().split(b"\n")
        self.assertEqual(len(first), len(second))
        differing = [i for i, (a, b) in enumerate(zip(first, second)) if a != b]
        for index in differing:
            self.assertIn(b"FILE_NAME", first[index], "only the header may differ between runs")

    def test_the_nondeterministic_layer_is_declared_on_the_record(self) -> None:
        h, model = fx.handoff(), fx.model()
        with temporary_directory(prefix="rc-cad-det-decl-") as tmp:
            artifacts = write_artifacts(h, model, output_dir=Path(tmp), stem="decl")
        self.assertIsNotNone(artifacts.step.nondeterministic_layer)
        self.assertIn("FILE_NAME", artifacts.step.nondeterministic_layer)
        # The GLB is reproducible, so it claims no exemption.
        self.assertIsNone(artifacts.glb.nondeterministic_layer)

    def test_determinism_is_not_faked_by_rewriting_the_file(self) -> None:
        """The recorded file hash is of the real bytes on disk, not a normalised copy."""
        import hashlib

        path = Path(self.first["step_path"])
        payload = path.read_bytes()
        self.assertEqual(self.first["step_sha"], hashlib.sha256(payload).hexdigest())
        self.assertNotEqual(self.first["step_sha"], self.first["step_content"])
        self.assertEqual(step_content_hash(path), self.first["step_content"])


if __name__ == "__main__":
    unittest.main()
