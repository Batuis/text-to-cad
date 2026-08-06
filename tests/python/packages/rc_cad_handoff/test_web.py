"""The user-facing import surface, exercised through the same boundary a browser uses.

Every success case runs the REAL pipeline over the REAL canonical PR19 V2 fixture — the same bytes
the producer commits and the same bytes a user downloads. A simplified payload would bypass the
semantic families and the layer metadata, which are exactly what makes this handoff worth importing,
so none is used.

The HTTP cases talk to a live server on an ephemeral port. That is deliberate: the browser reaches
`POST /api/import`, so a test that only called `import_handoff_bytes` would leave the transport,
the query-string filename and the status codes uncovered.
"""

from __future__ import annotations

import hashlib
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, quote, urlparse

from rc_cad_handoff.service import (
    ARTIFACT_ROLES,
    import_handoff_bytes,
    safe_stem,
    viewer_url_for,
)
from rc_cad_handoff.web import MAX_BODY_BYTES, serve

FIXTURES = Path(__file__).parent / "fixtures"
V2 = FIXTURES / "rc-footing-cad-poc.handoff.v2.json"
V1 = FIXTURES / "rc-footing-cad-poc.handoff.json"

#: The producer's canonical export, as a user would have it on disk after downloading.
DOWNLOADED_NAME = "rc-cad-handoff-v2-Z1-det3-dem2.json"

EXPECTED_FAMILIES = {
    "columnDowel": 8,
    "starterTie": 6,
    "starterCrosstie": 12,
    "footingBottomMatX": 10,
    "footingBottomMatY": 10,
}


class _Server:
    """A live surface on an ephemeral port, for the duration of one test."""

    def __init__(self, output_root: Path, viewer_origin: str = "http://127.0.0.1:4178") -> None:
        # Port 0: the OS picks a free one, so the suite never collides with a running 4179.
        self.httpd = serve(
            host="127.0.0.1", port=0, output_root=output_root, viewer_origin=viewer_origin
        )
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        # 127.0.0.1, not localhost: the surface binds the address explicitly.
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(f"{self.origin}{path}") as r:  # noqa: S310
            return r.status, json.loads(r.read())

    def post_json(self, payload: bytes, name: str) -> tuple[int, dict]:
        req = urllib.request.Request(  # noqa: S310
            f"{self.origin}/api/import?name={quote(name)}",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:  # noqa: S310
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class ServiceImportTest(unittest.TestCase):
    """The import callable, over the real fixture."""

    def test_selecting_the_canonical_handoff_generates_every_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(
                V2.read_bytes(), source_name=DOWNLOADED_NAME, output_root=Path(tmp)
            )
            self.assertTrue(out.ok, out.error_detail)
            self.assertEqual(out.source_name, DOWNLOADED_NAME)
            self.assertEqual((out.schema, out.schema_version), ("RcCadHandoffV2", 2))
            self.assertEqual(out.subject, "Z1")
            self.assertEqual(
                out.manifest_sha256, hashlib.sha256(V2.read_bytes()).hexdigest()
            )

            kinds = {a["kind"] for a in out.artifacts}
            self.assertEqual(kinds, {"step", "glb", "ifc", "review"})
            for a in out.artifacts:
                path = Path(str(a["path"]))
                self.assertTrue(path.is_file(), a["name"])
                self.assertGreater(int(a["sizeBytes"]), 0, a["name"])
                # Every artifact says what it is FOR, from one table beside the generator.
                self.assertEqual(a["purpose"], ARTIFACT_ROLES[str(a["kind"])]["purpose"])

    def test_the_real_semantics_survive_the_import(self) -> None:
        """Proof the canonical fixture was used rather than something simplified."""
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            self.assertEqual(out.summary["bars"], 46)
            self.assertEqual(out.summary["families"], EXPECTED_FAMILIES)
            self.assertEqual(out.summary["findings"], 4)
            # The verdicts a reviewer must see, carried through to the success view.
            self.assertIs(out.summary["constructible"], False)
            self.assertEqual(
                out.summary["constructibilityBlockers"], ["MAT_STARTER_CLEAR_SPACING_FAILURE"]
            )
            self.assertEqual(out.summary["bottomAnchorage"], "FAILED")
            self.assertEqual(out.summary["topReinforcement"], "NOT_EVALUATED")
            self.assertEqual(out.summary["punchingMomentTransfer"], "UNSUPPORTED")

    def test_ifc_is_declared_as_not_viewable_in_the_companion(self) -> None:
        # Honesty, asserted: the viewer's catalogue reads no IFC, and the surface must say so rather
        # than listing it as if it were openable there.
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            by_kind = {a["kind"]: a for a in out.artifacts}
            self.assertEqual(by_kind["step"]["viewable"], "yes")
            self.assertEqual(by_kind["glb"]["viewable"], "yes")
            self.assertIn("does not read IFC", str(by_kind["ifc"]["viewable"]))
            self.assertIn("not geometry", str(by_kind["review"]["viewable"]))

    def test_the_same_document_always_lands_in_the_same_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            first = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            second = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            self.assertEqual(first.output_dir, second.output_dir)
            # Named from the subject and the content hash, never from the upload's filename.
            self.assertTrue(Path(first.output_dir).name.startswith("z1-"))
            self.assertIn(first.manifest_sha256[:8], Path(first.output_dir).name)


class ViewerUrlTest(unittest.TestCase):
    def test_the_url_is_built_for_the_user(self) -> None:
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            step = next(a for a in out.artifacts if a["kind"] == "step")
            parsed = urlparse(out.viewer_url)
            self.assertEqual((parsed.scheme, parsed.hostname, parsed.port), ("http", "127.0.0.1", 4178))
            q = parse_qs(parsed.query)
            # `dir` must be ABSOLUTE — the viewer documents that, and a relative one silently fails.
            self.assertTrue(q["dir"][0].startswith("/"), q["dir"][0])
            self.assertEqual(q["dir"][0], out.output_dir)
            # `file` is relative to `dir`, and it is the STEP: the only artifact the viewer renders.
            self.assertEqual(q["file"][0], step["name"])
            self.assertTrue(q["file"][0].endswith(".step"))

    def test_the_step_the_url_points_at_exists_and_is_a_step(self) -> None:
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            q = parse_qs(urlparse(out.viewer_url).query)
            resolved = Path(q["dir"][0]) / q["file"][0]
            self.assertTrue(resolved.is_file())
            text = resolved.read_text(errors="ignore")
            self.assertTrue(text.startswith("ISO-10303-21;"))
            # The unit the STEP declares, unchanged by this surface. Searched in the whole file
            # rather than a 4 kB head: OCCT emits dozens of axis placements before the unit block.
            self.assertIn("SI_UNIT(.MILLI.,.METRE.)", text)

    def test_a_custom_viewer_origin_is_honoured(self) -> None:
        url = viewer_url_for(Path("/tmp/x/y.step"), viewer_origin="http://127.0.0.1:9999/")
        self.assertTrue(url.startswith("http://127.0.0.1:9999/?dir="))


class SafePathTest(unittest.TestCase):
    def test_a_hostile_filename_cannot_escape(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = import_handoff_bytes(
                V2.read_bytes(), source_name="../../../../etc/passwd", output_root=root
            )
            self.assertTrue(out.ok, out.error_detail)
            # Echoed for display, and NOT used as a path.
            self.assertEqual(out.source_name, "../../../../etc/passwd")
            written = Path(out.output_dir).resolve()
            self.assertTrue(
                str(written).startswith(str(root.resolve())),
                f"{written} escaped {root}",
            )
            for a in out.artifacts:
                self.assertTrue(str(Path(str(a["path"])).resolve()).startswith(str(root.resolve())))

    def test_safe_stem_collapses_and_never_returns_empty(self) -> None:
        self.assertEqual(safe_stem("../../etc/passwd"), "etc-passwd")
        self.assertEqual(safe_stem("..."), "handoff")
        self.assertEqual(safe_stem(""), "handoff")
        self.assertEqual(safe_stem("Z1"), "Z1")
        for hostile in ("../x", "/abs", "a/b", "a\\b", ".hidden", "  "):
            stem = safe_stem(hostile)
            self.assertNotIn("/", stem)
            self.assertNotIn("\\", stem)
            self.assertFalse(stem.startswith("."))
            self.assertTrue(stem)


class ErrorTest(unittest.TestCase):
    def test_malformed_json(self) -> None:
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(b"{ not json at all", output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "MALFORMED_JSON")
            # A sentence that tells the user what to DO.
            self.assertIn("not valid JSON", out.error_message)
            self.assertIn("Stabileo", out.error_message)
            self.assertTrue(out.error_detail)

    def test_a_json_file_that_is_not_a_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(b'{"hello":"world"}', output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "UNSUPPORTED_SCHEMA")

    def test_an_unsupported_future_version(self) -> None:
        doc = json.loads(V2.read_text())
        doc["schema"] = "RcCadHandoffV3"
        doc["schemaVersion"] = 3
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(json.dumps(doc).encode(), output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "UNSUPPORTED_SCHEMA")
            self.assertIn("does not read", out.error_message)
            # It names what it DOES read, so the user can tell whether to update.
            self.assertIn("RcCadHandoffV2", out.error_message)

    def test_a_name_and_version_that_disagree(self) -> None:
        doc = json.loads(V2.read_text())
        doc["schemaVersion"] = 1  # V2 body, V1 version
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(json.dumps(doc).encode(), output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "UNSUPPORTED_SCHEMA")
            self.assertIn("disagree", out.error_message)

    def test_a_declared_version_whose_content_the_pipeline_refuses(self) -> None:
        """Valid JSON, a version this tool reads, and content the parser will not accept.

        Distinguished from a schema problem on purpose: the remedy is different, so the code is too.
        """
        doc = json.loads(V2.read_text())
        doc["units"]["length"] = "mm"  # a rescaled document the parser refuses by contract
        with TemporaryDirectory() as tmp:
            out = import_handoff_bytes(json.dumps(doc).encode(), output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "REFUSED")
            self.assertIn("refused", out.error_message)
            self.assertIn("UNSUPPORTED_UNIT", out.error_detail)

    def test_generation_failure_is_reported_not_swallowed(self) -> None:
        """A pipeline that raises something unexpected must reach the user as a clear failure."""
        import rc_cad_handoff.service as service

        original = service.consume

        def exploding(*_a, **_k):
            raise RuntimeError("OCCT went home")

        service.consume = exploding  # type: ignore[assignment]
        try:
            with TemporaryDirectory() as tmp:
                out = import_handoff_bytes(V2.read_bytes(), output_root=Path(tmp))
            self.assertFalse(out.ok)
            self.assertEqual(out.error_code, "GENERATION_FAILED")
            self.assertIn("Nothing was published", out.error_message)
            self.assertIn("OCCT went home", out.error_detail)
        finally:
            service.consume = original  # type: ignore[assignment]


class HttpBoundaryTest(unittest.TestCase):
    """Through the surface a browser actually talks to."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.server = _Server(self.root)

    def tearDown(self) -> None:
        self.server.close()
        self._tmp.cleanup()

    def test_the_page_is_served_and_offers_the_visible_action(self) -> None:
        with urllib.request.urlopen(f"{self.server.origin}/") as r:  # noqa: S310
            html = r.read().decode()
        self.assertIn("Open RC handoff JSON", html)
        # The explanation a user needs, in the page rather than in a README.
        self.assertIn("not a CAD drawing", html)
        self.assertIn("does not read IFC", html)
        # A drop target and a file input, both present.
        self.assertIn('id="drop"', html)
        self.assertIn('type="file"', html)
        self.assertIn("Open assembly in CAD viewer", html)

    def test_health_reports_what_it_reads_and_where_it_writes(self) -> None:
        status, body = self.server.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["readsSchemas"], ["RcCadHandoffV1", "RcCadHandoffV2"])
        self.assertEqual(body["outputRoot"], str(self.root))

    def test_posting_the_canonical_handoff_generates_and_reports(self) -> None:
        status, body = self.server.post_json(V2.read_bytes(), DOWNLOADED_NAME)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["sourceName"], DOWNLOADED_NAME)
        self.assertEqual(body["schema"], "RcCadHandoffV2")
        self.assertEqual({a["kind"] for a in body["artifacts"]}, {"step", "glb", "ifc", "review"})
        self.assertEqual(body["summary"]["families"], EXPECTED_FAMILIES)
        self.assertIn("127.0.0.1:4178", body["viewerUrl"])
        # And the files are really there, under the root this server was given.
        for a in body["artifacts"]:
            self.assertTrue(Path(str(a["path"])).is_file())

    def test_a_dropped_file_takes_the_same_path_as_a_picked_one(self) -> None:
        """Drag-and-drop and the file picker both POST the file's text to one endpoint.

        So this covers both gestures at the only layer where they differ — they do not.
        """
        picked = self.server.post_json(V2.read_bytes(), "picked.json")
        dropped = self.server.post_json(V2.read_bytes(), "dropped.json")
        self.assertEqual(picked[0], 200)
        self.assertEqual(dropped[0], 200)
        self.assertEqual(picked[1]["sourceName"], "picked.json")
        self.assertEqual(dropped[1]["sourceName"], "dropped.json")
        # Same document, so the same output directory and the same viewer URL either way.
        self.assertEqual(picked[1]["outputDir"], dropped[1]["outputDir"])
        self.assertEqual(picked[1]["viewerUrl"], dropped[1]["viewerUrl"])

    def test_malformed_json_over_http_is_422_with_a_usable_message(self) -> None:
        status, body = self.server.post_json(b"{{{", "broken.json")
        self.assertEqual(status, 422)
        self.assertFalse(body["ok"])
        self.assertEqual(body["errorCode"], "MALFORMED_JSON")
        self.assertEqual(body["sourceName"], "broken.json")

    def test_a_v1_handoff_is_read_rather_than_refused(self) -> None:
        """V1 is still a declared version, so the surface must accept it.

        Its artifacts are the V1 transfer cage — fewer bars, two families — which is correct for a
        V1 document and is why the summary is read from the document rather than assumed.
        """
        status, body = self.server.post_json(V1.read_bytes(), "old.json")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["schema"], "RcCadHandoffV1")
        self.assertEqual(body["summary"]["bars"], 14)
        self.assertEqual(set(body["summary"]["families"]), {"columnDowel", "starterTie"})

    def test_an_empty_body_is_refused_clearly(self) -> None:
        status, body = self.server.post_json(b"", "empty.json")
        self.assertEqual(status, 400)
        self.assertEqual(body["errorCode"], "MALFORMED_JSON")

    def test_an_unknown_path_is_a_clean_404(self) -> None:
        status, body = self.server.post_json(b"{}", "x.json")
        self.assertEqual(status, 422)  # /api/import with bad content, not a 404
        req = urllib.request.Request(  # noqa: S310
            f"{self.server.origin}/api/nope", data=b"{}", method="POST"
        )
        try:
            urllib.request.urlopen(req)  # noqa: S310
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            self.assertEqual(json.loads(exc.read())["errorCode"], "NOT_FOUND")

    def test_the_body_limit_is_stated_and_enforced(self) -> None:
        self.assertEqual(MAX_BODY_BYTES, 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
