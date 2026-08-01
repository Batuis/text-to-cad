"""End-to-end: manifest in, solids plus artifacts plus review out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactSet, write_artifacts
from .crosscheck import CrossCheckResult, cross_check
from .geometry import RealisedModel, realise
from .manifest import Handoff, load_handoff
from .review import build_review, write_review


@dataclass
class ConsumeResult:
    handoff: Handoff
    model: RealisedModel
    cross_check: CrossCheckResult
    artifacts: ArtifactSet | None
    review: dict[str, Any]
    review_path: Path | None = None
    review_bytes: int | None = None
    review_sha256: str | None = None


def consume(
    manifest_path: Path,
    *,
    output_dir: Path,
    stem: str | None = None,
    write_geometry: bool = True,
    review_name: str = "cad-review.json",
    expect_sha256: str | None = None,
) -> ConsumeResult:
    """Parse, realise, cross-check and review a handoff document.

    ``expect_sha256`` lets a caller pin the exact document it meant to consume;
    a mismatch refuses rather than reviewing a different cage under the expected
    name.
    """
    handoff = load_handoff(manifest_path)

    if expect_sha256 is not None and handoff.manifest_sha256 != expect_sha256:
        from .errors import ContractError

        raise ContractError(
            "MANIFEST_HASH_MISMATCH",
            f"expected manifest SHA-256 {expect_sha256}, read {handoff.manifest_sha256}",
            None,
        )

    model = realise(handoff)
    result = cross_check(handoff, model)

    output_dir = Path(output_dir).expanduser()
    artifacts = (
        write_artifacts(handoff, model, output_dir=output_dir, stem=stem)
        if write_geometry
        else None
    )

    review = build_review(handoff, model, result, artifacts, relative_to=output_dir)
    review_path, review_bytes, review_sha = write_review(review, output_dir / review_name)

    return ConsumeResult(
        handoff=handoff,
        model=model,
        cross_check=result,
        artifacts=artifacts,
        review=review,
        review_path=review_path,
        review_bytes=review_bytes,
        review_sha256=review_sha,
    )
