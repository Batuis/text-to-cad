from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .errors import HandoffError
from .manifest import CONTRACT
from .status import Comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rc-cad-handoff",
        description=(
            f"Realise a {CONTRACT} reinforced-concrete detailing handoff as exact CAD solids, "
            "export STEP and GLB, and write an independent cad-review.json cross-check."
        ),
    )
    parser.add_argument("manifest", help=f"Path to a {CONTRACT} JSON document.")
    parser.add_argument(
        "-o",
        "--output-dir",
        required=True,
        metavar="DIR",
        help="Directory for the STEP, GLB and cad-review.json outputs.",
    )
    parser.add_argument(
        "--stem",
        metavar="NAME",
        help="Base name for the generated artifacts. Defaults to a name derived from the subject and its revisions.",
    )
    parser.add_argument(
        "--review-name",
        default="cad-review.json",
        metavar="NAME",
        help="Filename for the review artifact (default: cad-review.json).",
    )
    parser.add_argument(
        "--expect-sha256",
        metavar="HEX",
        help="Refuse unless the manifest has this SHA-256.",
    )
    parser.add_argument(
        "--no-geometry",
        action="store_true",
        help="Validate, cross-check and review without writing STEP or GLB.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the review path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    from .pipeline import consume

    try:
        result = consume(
            Path(args.manifest),
            output_dir=Path(args.output_dir),
            stem=args.stem,
            write_geometry=not args.no_geometry,
            review_name=args.review_name,
            expect_sha256=args.expect_sha256,
        )
    except HandoffError as exc:
        parser.exit(2, f"refused: {exc}\n")
        return 2

    if args.quiet:
        print(result.review_path)
        return 0

    handoff = result.handoff
    summary = result.review["summary"]
    print(f"contract           {CONTRACT} v{handoff.schema_version}")
    print(f"subject            {handoff.subject_name} (entity {handoff.subject_entity_id})")
    print(f"manifest sha256    {handoff.manifest_sha256}")
    print(
        "revisions          "
        + ", ".join(f"{k}={v}" for k, v in sorted(handoff.revisions.items()))
    )
    print(
        f"solids             {summary['concreteComponents']} concrete + "
        f"{summary['barSolids']} bars = {summary['totalSolids']}"
    )
    print(
        f"arcs               {summary['totalArcs']} total, "
        f"{summary['approximatedArcs']} approximated"
    )
    print(
        f"cross-check        {summary['crossCheckedPairCount']} pairs, "
        f"{summary['agreementCount']} agree, {summary['disagreementCount']} disagree"
    )
    if summary["worstDeltaM"] is not None:
        print(f"worst delta        {summary['worstDeltaM'] * 1000:.4f} mm")
    print(
        f"not evaluated      {summary['notEvaluatedCheckCount']} checks; "
        f"out of scope {summary['outOfScopeCheckCount']}"
    )
    print(f"unsupported        {summary['unsupportedConditionCount']} declared conditions")

    if result.artifacts is not None:
        for label, record in (("STEP", result.artifacts.step), ("GLB", result.artifacts.glb)):
            print(f"{label:18s} {record.path}  {record.size_bytes} bytes  {record.sha256[:16]}…")
    print(f"review             {result.review_path}  {result.review_bytes} bytes")

    disagreements = [
        p for p in result.cross_check.reported_pairs if p.comparison is Comparison.DISAGREEMENT
    ]
    if disagreements:
        print()
        print(f"{len(disagreements)} cross-check disagreement(s):")
        for p in disagreements:
            print(
                f"  {p.bar_id_a} / {p.bar_id_b}: producer {p.stabileo_measured}, "
                f"CAD {p.cad_clearance:.6f}, delta {p.delta:.6f} m"
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
