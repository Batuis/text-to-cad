"""End-to-end consumption and the CLI entry point."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rc_cad_handoff.cli import main
from rc_cad_handoff.errors import ContractError
from rc_cad_handoff.pipeline import consume

from tests.python.support.tmp_root import temporary_directory

from tests.python.packages.rc_cad_handoff import _support as fx


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temporary_directory(prefix="rc-cad-pipeline-")
        cls.out = Path(cls._tmp.name)
        cls.result = consume(fx.MANIFEST_PATH, output_dir=cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_produces_every_deliverable(self) -> None:
        self.assertIsNotNone(self.result.artifacts)
        self.assertTrue(self.result.artifacts.step.path.exists())
        self.assertTrue(self.result.artifacts.glb.path.exists())
        self.assertTrue(self.result.review_path.exists())
        self.assertEqual(self.result.review_path.name, "cad-review.json")

    def test_the_written_review_is_valid_json_and_hashes_as_recorded(self) -> None:
        import hashlib

        payload = self.result.review_path.read_bytes()
        self.assertEqual(len(payload), self.result.review_bytes)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), self.result.review_sha256)
        document = json.loads(payload)
        self.assertEqual(document["source"]["manifestSha256"], fx.MANIFEST_SHA256)

    def test_artifact_paths_in_the_review_are_relative_to_the_output_dir(self) -> None:
        document = json.loads(self.result.review_path.read_text())
        for key in ("step", "glb"):
            path = document["artifacts"][key]["path"]
            with self.subTest(artifact=key):
                self.assertFalse(Path(path).is_absolute())
                self.assertTrue((self.out / path).exists())

    def test_artifact_hashes_in_the_review_match_the_files_on_disk(self) -> None:
        import hashlib

        document = json.loads(self.result.review_path.read_text())
        for key in ("step", "glb"):
            entry = document["artifacts"][key]
            payload = (self.out / entry["path"]).read_bytes()
            with self.subTest(artifact=key):
                self.assertEqual(entry["sizeBytes"], len(payload))
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())

    def test_the_default_stem_encodes_the_subject_and_revisions(self) -> None:
        self.assertEqual(self.result.artifacts.step.path.stem, "rc-cad-Z1-det3-dem2")

    def test_pinning_the_manifest_hash_accepts_the_right_document(self) -> None:
        with temporary_directory(prefix="rc-cad-pin-ok-") as tmp:
            result = consume(
                fx.MANIFEST_PATH,
                output_dir=Path(tmp),
                write_geometry=False,
                expect_sha256=fx.MANIFEST_SHA256,
            )
            self.assertEqual(result.handoff.manifest_sha256, fx.MANIFEST_SHA256)

    def test_pinning_the_manifest_hash_refuses_a_different_document(self) -> None:
        with temporary_directory(prefix="rc-cad-pin-bad-") as tmp:
            with self.assertRaises(ContractError) as ctx:
                consume(
                    fx.MANIFEST_PATH,
                    output_dir=Path(tmp),
                    write_geometry=False,
                    expect_sha256="0" * 64,
                )
            self.assertEqual(ctx.exception.code, "MANIFEST_HASH_MISMATCH")

    def test_review_only_mode_writes_no_geometry(self) -> None:
        with temporary_directory(prefix="rc-cad-nogeom-") as tmp:
            out = Path(tmp)
            result = consume(fx.MANIFEST_PATH, output_dir=out, write_geometry=False)
            self.assertIsNone(result.artifacts)
            self.assertEqual(list(out.glob("*.step")), [])
            self.assertEqual(list(out.glob("*.glb")), [])
            document = json.loads(result.review_path.read_text())
            self.assertIsNone(document["artifacts"])
            # The cross-check still ran on real geometry.
            self.assertEqual(
                document["summary"]["crossCheckedPairCount"], fx.EXPECTED_PROHIBITED_OVERLAPS
            )


_CONSUME_SCRIPT = """
import sys
from pathlib import Path
from rc_cad_handoff.pipeline import consume

result = consume(Path(sys.argv[1]), output_dir=Path(sys.argv[2]), stem="det")
print(result.review_path)
"""


def _consume_in_subprocess(out_dir: Path) -> dict:
    """One full run in a fresh interpreter, so writer state cannot leak between runs."""
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    completed = subprocess.run(
        [sys.executable, "-c", _CONSUME_SCRIPT, str(fx.MANIFEST_PATH), str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root,
        check=True,
    )
    review_path = Path(completed.stdout.strip().splitlines()[-1])
    return json.loads(review_path.read_text())


class WrittenReviewDeterminismTest(unittest.TestCase):
    """The written review varies in exactly one field, and for a stated reason.

    The review records the STEP file's real hash. That hash changes on every
    write because OCCT stamps a timestamp into the STEP header, so the review
    inherits that single varying value rather than misreporting the bytes on
    disk. Everything else, including the normalised ``contentSha256``, is stable.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._a = temporary_directory(prefix="rc-cad-review-det-a-")
        cls._b = temporary_directory(prefix="rc-cad-review-det-b-")
        cls.first = _consume_in_subprocess(Path(cls._a.name))
        cls.second = _consume_in_subprocess(Path(cls._b.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._a.cleanup()
        cls._b.cleanup()

    def test_only_the_step_file_hash_differs(self) -> None:
        first, second = dict(self.first), dict(self.second)
        first_artifacts = dict(first.pop("artifacts"))
        second_artifacts = dict(second.pop("artifacts"))

        first_step = dict(first_artifacts.pop("step"))
        second_step = dict(second_artifacts.pop("step"))
        self.assertNotEqual(first_step.pop("sha256"), second_step.pop("sha256"))
        self.assertEqual(first_step, second_step, "only the file hash may differ")

        self.assertEqual(first_artifacts, second_artifacts, "the GLB record must be stable")
        self.assertEqual(first, second, "the rest of the review must be byte-stable")

    def test_the_normalised_step_content_hash_is_stable(self) -> None:
        self.assertEqual(
            self.first["artifacts"]["step"]["contentSha256"],
            self.second["artifacts"]["step"]["contentSha256"],
        )

    def test_the_glb_hash_is_stable(self) -> None:
        self.assertEqual(
            self.first["artifacts"]["glb"]["sha256"], self.second["artifacts"]["glb"]["sha256"]
        )

    def test_the_varying_field_is_accompanied_by_its_explanation(self) -> None:
        layer = self.first["artifacts"]["step"]["nondeterministicLayer"]
        self.assertIn("FILE_NAME", layer)
        self.assertIn("contentSha256", layer)


class CliTest(unittest.TestCase):
    def test_exits_zero_and_writes_a_review(self) -> None:
        with temporary_directory(prefix="rc-cad-cli-") as tmp:
            code = main([str(fx.MANIFEST_PATH), "-o", tmp, "--no-geometry", "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "cad-review.json").exists())

    def test_honours_a_custom_review_name(self) -> None:
        with temporary_directory(prefix="rc-cad-cli-name-") as tmp:
            code = main(
                [
                    str(fx.MANIFEST_PATH),
                    "-o",
                    tmp,
                    "--no-geometry",
                    "--quiet",
                    "--review-name",
                    "review.json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "review.json").exists())

    def test_refuses_an_unknown_contract_with_a_non_zero_exit(self) -> None:
        with temporary_directory(prefix="rc-cad-cli-bad-") as tmp:
            bad = Path(tmp) / "bad.json"
            document = fx.raw_manifest()
            document["schema"] = "NotOurContract"
            bad.write_text(json.dumps(document))
            with self.assertRaises(SystemExit) as ctx:
                main([str(bad), "-o", tmp, "--no-geometry"])
            self.assertEqual(ctx.exception.code, 2)

    def test_refuses_a_pinned_hash_mismatch(self) -> None:
        with temporary_directory(prefix="rc-cad-cli-pin-") as tmp:
            with self.assertRaises(SystemExit) as ctx:
                main(
                    [
                        str(fx.MANIFEST_PATH),
                        "-o",
                        tmp,
                        "--no-geometry",
                        "--expect-sha256",
                        "0" * 64,
                    ]
                )
            self.assertEqual(ctx.exception.code, 2)

    def test_reports_a_non_zero_exit_when_the_two_implementations_disagree(self) -> None:
        """A disagreement is a result the caller must not be able to ignore."""
        with temporary_directory(prefix="rc-cad-cli-dis-") as tmp:
            skewed = Path(tmp) / "skewed.json"
            document = fx.raw_manifest()
            for check in document["checks"]:
                for finding in check.get("findings", []):
                    if finding.get("measured") is not None:
                        finding["measured"] = finding["measured"] + 0.01
            skewed.write_text(json.dumps(document))
            code = main([str(skewed), "-o", tmp, "--no-geometry"])
            self.assertEqual(code, 1)

            document = json.loads((Path(tmp) / "cad-review.json").read_text())
            self.assertEqual(
                document["summary"]["disagreementCount"], fx.EXPECTED_PROHIBITED_OVERLAPS
            )


if __name__ == "__main__":
    unittest.main()
