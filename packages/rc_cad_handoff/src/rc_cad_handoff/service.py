"""The import-and-generate step, as a callable the UI and the tests share.

── Why this layer exists ────────────────────────────────────────────

A user who exports an RC CAD handoff from Stabileo gets a JSON file, and a JSON file is not a
drawing. It is a semantic handoff: bar families, layer order, covers, findings and verdicts. Turning
it into something a CAD tool can open is `consume` plus `write_ifc`, both of which already exist —
and both of which, until now, only a Python command could reach.

So this module is orchestration and nothing else. It parses no geometry, writes no solid, and
re-derives no engineering value. It picks a safe output directory, calls the existing pipeline, and
reports what happened in terms a person can act on.

── Why the surface lives with the schema ────────────────────────────

The obvious alternative was to teach the generic CAD Viewer to open handoff JSON. That viewer knows
about `.step`, `.glb`, `.stl`, `.3mf`, `.dxf`, `.gcode` and robot descriptions — file formats, not
producers. Teaching it one producer's semantic schema would make a general tool depend on Stabileo's
contract, and the repository's own rule is that a skill stays self-contained and does not reach into
another's code. The schema knowledge belongs here, where the parser and the pipeline already live;
the viewer keeps doing what it does, which is render whatever CAD lands in a directory.

── Safety ───────────────────────────────────────────────────────────

Nothing from the uploaded filename reaches the filesystem. The output directory is derived from the
handoff's own subject and the SHA-256 of the exact bytes received, so the same document always lands
in the same place and a hostile name like `../../etc/x` cannot escape: the name is used only to
report back to the user.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .errors import HandoffError
from .ifc import write_ifc
from .manifest import CONTRACTS
from .pipeline import consume

#: Where generated artifacts go when the caller names no root.
#:
#: Under the user's home rather than the repository, because a person running this is not
#: necessarily standing in a checkout and generated output does not belong in one. The path is
#: computed, never hard-coded to one developer's machine.
DEFAULT_OUTPUT_ROOT = Path.home() / ".stabileo" / "rc-cad-handoff"

#: The generic viewer's default port. Only used to BUILD a URL, never to talk to the viewer.
DEFAULT_VIEWER_ORIGIN = "http://127.0.0.1:4178"

#: What each generated file is for, in the user's terms.
#:
#: Kept beside the generator so the UI cannot drift from what is actually produced, and honest
#: about the one output the companion viewer cannot show.
ARTIFACT_ROLES: dict[str, dict[str, str]] = {
    "step": {
        "label": "STEP",
        "purpose": "The assembly. This is what the CAD viewer displays, and what a CAD tool opens.",
        "viewable": "yes",
    },
    "glb": {
        "label": "GLB",
        "purpose": "A lightweight 3-D visualisation of the same assembly.",
        "viewable": "yes",
    },
    "ifc": {
        "label": "IFC4",
        "purpose": (
            "For inspection in an external BIM tool: each bar is an IfcReinforcingBar carrying its "
            "family and its stable id."
        ),
        # Stated rather than implied: the companion's catalogue reads .step/.glb/.stl/.3mf/.dxf and
        # robot descriptions. It has no IFC support, and pretending otherwise would send a reviewer
        # looking for a file the viewer will never list.
        "viewable": "no — the companion CAD viewer does not read IFC",
    },
    "review": {
        "label": "cad-review.json",
        "purpose": (
            "Review metadata: the independent cross-check, the findings and the constructibility "
            "verdict. Not a drawing."
        ),
        "viewable": "no — it is a report, not geometry",
    },
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(value: str, *, fallback: str = "handoff") -> str:
    """A filesystem-safe stem from arbitrary text.

    Collapses everything outside `[A-Za-z0-9._-]`, strips leading dots so nothing becomes hidden or
    relative, and refuses to produce an empty string. `../../etc/passwd` becomes `etc-passwd`; it
    cannot traverse, because the result is a single path COMPONENT that the caller joins.
    """
    cleaned = _SAFE.sub("-", str(value or "")).strip("-.")
    return cleaned or fallback


@dataclass
class ImportOutcome:
    """What the UI needs to render, success or failure."""

    ok: bool
    #: The name the user chose, echoed back for display only. Never used as a path.
    source_name: str
    #: Stable identity of the exact bytes received.
    manifest_sha256: str = ""
    schema: str = ""
    schema_version: int = 0
    subject: str = ""
    #: Where the artifacts landed.
    output_dir: str = ""
    artifacts: list[dict[str, object]] = field(default_factory=list)
    #: The URL that opens the generated assembly in the generic viewer.
    viewer_url: str = ""
    #: Headline counts and verdicts, so the success view says something useful.
    summary: dict[str, object] = field(default_factory=dict)
    #: One of `MALFORMED_JSON`, `UNSUPPORTED_SCHEMA`, `REFUSED`, `GENERATION_FAILED`.
    error_code: str = ""
    #: A sentence a non-developer can act on.
    error_message: str = ""
    #: Developer detail, shown behind a disclosure.
    error_detail: str = ""

    def as_json(self) -> dict[str, object]:
        out: dict[str, object] = {"ok": self.ok, "sourceName": self.source_name}
        if self.ok:
            out.update(
                manifestSha256=self.manifest_sha256,
                schema=self.schema,
                schemaVersion=self.schema_version,
                subject=self.subject,
                outputDir=self.output_dir,
                artifacts=self.artifacts,
                viewerUrl=self.viewer_url,
                summary=self.summary,
            )
        else:
            out.update(
                errorCode=self.error_code,
                errorMessage=self.error_message,
                errorDetail=self.error_detail,
            )
            if self.manifest_sha256:
                out["manifestSha256"] = self.manifest_sha256
        return out


def viewer_url_for(step_path: Path, *, viewer_origin: str = DEFAULT_VIEWER_ORIGIN) -> str:
    """The URL that opens one generated STEP in the generic viewer.

    Built here so the user never assembles a query string. `dir` is the artifact directory and
    `file` is relative to it, which is the contract the viewer documents.
    """
    directory = step_path.parent
    return (
        f"{viewer_origin.rstrip('/')}/"
        f"?dir={quote(str(directory), safe='')}"
        f"&file={quote(step_path.name, safe='')}"
    )


def _summarise(result: object) -> dict[str, object]:
    """Headline facts for the success view, read off the parsed handoff."""
    handoff = getattr(result, "handoff")
    families = {f.kind: len(f.bar_ids) for f in handoff.families}
    statuses = handoff.statuses
    findings = [
        f
        for c in handoff.checks
        for f in c.findings
    ]
    out: dict[str, object] = {
        "bars": len(handoff.bars),
        "families": families,
        "findings": len(findings),
    }
    if statuses is not None:
        out.update(
            constructible=statuses.constructible,
            constructibilityBlockers=list(statuses.constructibility_blockers),
            bottomAnchorage=statuses.bottom_anchorage,
            topReinforcement=statuses.top_reinforcement,
            punchingMomentTransfer=statuses.punching_moment_transfer,
        )
    return out


def import_handoff_bytes(
    payload: bytes,
    *,
    source_name: str = "handoff.json",
    output_root: Path | None = None,
    viewer_origin: str = DEFAULT_VIEWER_ORIGIN,
) -> ImportOutcome:
    """Validate a handoff, generate every artifact, and say what happened.

    The one entry point the UI calls and the tests exercise, so there is no path a browser can take
    that a test cannot.
    """
    outcome = ImportOutcome(ok=False, source_name=str(source_name or "handoff.json"))
    sha = hashlib.sha256(payload).hexdigest()
    outcome.manifest_sha256 = sha

    # ── 1. Is it JSON at all? ────────────────────────────────
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        outcome.error_code = "MALFORMED_JSON"
        outcome.error_message = (
            "That file is not valid JSON, so it cannot be an RC CAD handoff. Export the handoff "
            "again from Stabileo and select the downloaded .json file."
        )
        outcome.error_detail = str(exc)
        return outcome

    # ── 2. Is it a handoff this tool reads? ──────────────────
    #
    # Checked BEFORE the pipeline so the message names the real problem. Letting the parser refuse
    # would also be correct, but its message is written for a developer.
    version = document.get("schemaVersion") if isinstance(document, dict) else None
    declared = document.get("schema") if isinstance(document, dict) else None
    if version not in CONTRACTS:
        outcome.error_code = "UNSUPPORTED_SCHEMA"
        known = ", ".join(f"{k} ({v})" for k, v in sorted(CONTRACTS.items()))
        outcome.error_message = (
            f"This file declares schema version {version!r}, which this tool does not read. "
            f"It reads: {known}. A newer Stabileo may have produced a handoff this build predates."
        )
        outcome.error_detail = f"schema={declared!r} schemaVersion={version!r}"
        return outcome
    if declared != CONTRACTS[version]:
        outcome.error_code = "UNSUPPORTED_SCHEMA"
        outcome.error_message = (
            f"This file says it is {declared!r} but declares version {version}, and those disagree. "
            "The file may be damaged or hand-edited; export it again from Stabileo."
        )
        outcome.error_detail = f"expected schema {CONTRACTS[version]!r} for version {version}"
        return outcome
    outcome.schema = str(declared)
    outcome.schema_version = int(version)

    subject = ""
    if isinstance(document.get("subject"), dict):
        subject = str(document["subject"].get("name") or "")
    outcome.subject = subject

    # ── 3. A deterministic, safe home for the output ─────────
    #
    # Subject plus the first eight of the content hash: the same document always lands in the same
    # directory, two different documents never share one, and nothing here comes from the uploaded
    # filename.
    # ABSOLUTE, always. The viewer's `?dir=` must be an absolute path — it documents that — and a
    # relative root produced a URL the viewer silently could not resolve. `resolve()` also collapses
    # any `..` a caller passed in the root itself.
    root = (Path(output_root).expanduser() if output_root else DEFAULT_OUTPUT_ROOT).resolve()
    stem = safe_stem(subject or "handoff", fallback="handoff").lower()
    out_dir = root / f"{stem}-{sha[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 4. The existing pipeline, invoked and not reimplemented ──
    #
    # `consume` writes STEP, GLB and cad-review.json; `write_ifc` adds the IFC. The bytes are staged
    # in a temporary file because `consume` reads a path, and the handoff's own hash is passed as
    # `expect_sha256` so the document generated from is provably the document received.
    try:
        with tempfile.TemporaryDirectory() as staging:
            staged = Path(staging) / "handoff.json"
            staged.write_bytes(payload)
            result = consume(
                staged,
                output_dir=out_dir,
                stem=stem,
                review_name="cad-review.json",
                expect_sha256=sha,
            )
            ifc_info = write_ifc(result.handoff, out_dir / f"{stem}.ifc")
    except HandoffError as exc:
        # The document parsed as JSON and declared a version this tool reads, and the pipeline still
        # refused it. That is a content problem, and its own message is the useful one.
        outcome.error_code = "REFUSED"
        outcome.error_message = (
            "The handoff was read but refused: " + getattr(exc, "message", str(exc))
        )
        outcome.error_detail = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
        return outcome
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        outcome.error_code = "GENERATION_FAILED"
        outcome.error_message = (
            "The handoff was valid, but generating the CAD artifacts failed. Nothing was published. "
            "The detail below names the stage that failed."
        )
        outcome.error_detail = f"{type(exc).__name__}: {exc}"
        return outcome

    artifacts: list[dict[str, object]] = []

    def record(kind: str, path: Path, sha256: str | None = None) -> None:
        role = ARTIFACT_ROLES[kind]
        artifacts.append(
            {
                "kind": kind,
                "label": role["label"],
                "purpose": role["purpose"],
                "viewable": role["viewable"],
                "name": path.name,
                "path": str(path),
                "sizeBytes": path.stat().st_size if path.exists() else 0,
                "sha256": sha256
                or hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists()
                else "",
            }
        )

    assert result.artifacts is not None
    record("step", result.artifacts.step.path, result.artifacts.step.sha256)
    record("glb", result.artifacts.glb.path, result.artifacts.glb.sha256)
    record("ifc", Path(str(ifc_info["path"])))
    if result.review_path is not None:
        record("review", result.review_path, result.review_sha256)

    outcome.ok = True
    outcome.output_dir = str(out_dir)
    outcome.artifacts = artifacts
    outcome.viewer_url = viewer_url_for(
        result.artifacts.step.path, viewer_origin=viewer_origin
    )
    outcome.summary = _summarise(result)
    return outcome
