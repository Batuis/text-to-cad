# rc_cad_handoff

Renders a reinforced-concrete detailing handoff as exact CAD solids, exports STEP
and GLB, and writes an independent geometric cross-check.

This is the **consumer half of a POC companion to Stabileo PR19**. It is not an
upstream proposal — see [Why this lives in `packages/`](#why-this-lives-in-packages).

## What it does

Given one `RcCadHandoffV1` JSON document it will:

1. **Parse and validate it independently** — refusing an unknown contract, an
   unsupported schema version, rescaled units, a non-Z-up frame, unresolved ids,
   non-finite geometry, and an arc that claims exactness without the data to
   support it.
2. **Realise exact geometry** — one solid per concrete component and one swept
   solid per bar, with every arc built from its supplied centre.
3. **Export STEP and GLB** using `cadpy`'s existing writers, with every solid
   individually named `concrete:<bodyId>` / `bar:<barId>`.
4. **Cross-check the producer's verdicts** with OCCT, from geometry it built
   itself, and record agreement or disagreement.
5. **Write `cad-review.json`** — a deterministic artifact that keeps the
   producer's verdicts, this consumer's observations, and the relationship
   between them in three separate places.

## Usage

```bash
rc-cad-handoff path/to/handoff.json -o /path/to/output-dir
```

Useful flags:

| Flag | Effect |
|---|---|
| `--expect-sha256 HEX` | Refuse unless the manifest has this hash. |
| `--no-geometry` | Validate, cross-check and review without writing STEP or GLB. |
| `--stem NAME` | Override the artifact base name. |
| `--review-name NAME` | Override `cad-review.json`. |
| `--quiet` | Print only the review path. |

Exit codes: `0` success, `1` the two implementations disagree, `2` the document
was refused.

As a library:

```python
from pathlib import Path
from rc_cad_handoff import consume

result = consume(Path("handoff.json"), output_dir=Path("out"))
print(result.review["summary"]["disagreementCount"])
```

Write generated artifacts under `models/` per repository policy, or to a scratch
directory for one-off runs. `cad-review.json` is review data, not a model
artifact, so it does not belong in `models/`.

## What it is not

It is **not** the authority on any engineering question. The producer owns
reinforcement roles, required cover and clear spacing, regulatory classification
and every verdict. This package measures geometry and reports; it derives no rule
and overrides no verdict.

Concretely, it will not:

- convert a `NOT_EVALUATED` check into a pass;
- measure a check marked `OUT_OF_SCOPE`;
- treat an internal concrete-to-concrete interface, or a truncated face, as a
  cover surface;
- treat a bar's declared intentional interface crossing as a failure;
- raise a shortfall for a pair the producer marks non-reportable;
- re-derive bend geometry from nominal radius and sweep;
- replace an arc with its chord.

## Two things worth knowing

**Arcs come from centres, not from radius and sweep.** Start, end, radius and
sweep do not determine an arc in three dimensions — two centres satisfy them in
any plane, and the plane itself is free. So each arc is built from
`(start, end, centre)`, the only self-determining triple. `radius` and `sweepDeg`
are treated as the producer's *nominal bend parameters*: where they differ from
the realised arc, the deviation is reported under
`approximationInventory.nominalBendParameterDeviations` rather than enforced.
Enforcing them would reject exact geometry; re-deriving from them would
reintroduce the sagitta error the stored centre exists to prevent.

**Penetration depth does not come from solid distance.**
`BRepExtrema_DistShapeShape` returns `0` for every pair that touches or overlaps,
so it cannot supply a signed depth. Rather than invent one, the signed surface
clearance is computed as exact centreline distance minus the sum of the radii —
still OCCT, still exact curves. Solid distance and solid intersection corroborate
the sign, and where a boolean is degenerate (coincident or coplanar faces) that
is recorded as a numerical limitation instead of being read as a result.

## Reproducibility

The GLB is byte-identical across runs. The STEP is not, in two declared ways,
neither of them in the geometry:

- OCCT stamps a wall-clock timestamp into the ISO-10303-21 `FILE_NAME` header, so
  the file hash changes on every write. `contentSha256` in the review is the same
  file with that timestamp normalised, and it is stable.
- Repeating an export inside one interpreter shifts
  `NEXT_ASSEMBLY_USAGE_OCCURRENCE` numbering, because the writer keeps
  process-global state. One export per process — what the CLI does — reproduces
  `contentSha256` exactly.

Nothing rewrites the header to manufacture a stable hash. Both hashes are
recorded and the nondeterministic layer is named.

`cad-review.json` is deterministic in every field it computes itself: sorted
keys, fixed indent, and no timestamp — a timestamp would make two identical
reviews differ and a stale one look fresh. Two runs differ in exactly one field,
`artifacts.step.sha256`, because that field records the real bytes of a file
whose header carries a timestamp. The review reports that hash honestly rather
than substituting a normalised one; `artifacts.step.contentSha256` beside it is
stable.

## Why this lives in `packages/`

The consumer is specific to one producer's interchange format, so it does not
belong in `packages/cadpy`, which is the generic shared artifact runtime. Putting
it in a skill would ship it inside the installable plugin, which is also wrong
for a POC companion that is explicitly **not** proposed upstream.

`packages/` is the repository's home for shared Python source that skills vendor
when they need it. Nothing vendors this package today, so it stays out of the
plugin bundle while still living where shared source belongs.

## Licensing

This package is original work for this MIT repository. It was written against the
documented interchange format; the producer's AGPL sources, JSON Schema document,
classification tables and rule implementations are **not** vendored here, and no
AGPL code was copied or translated.

The test fixture is generated output data retained under its originating
project's terms — see
`tests/python/packages/rc_cad_handoff/fixtures/PROVENANCE.md`.

## Tests

```bash
scripts/test/test-python.sh
# or just this package
PYTHONPATH="$PWD" .venv/bin/python -m unittest discover -s tests/python/packages/rc_cad_handoff -t .
```

The tests exercise real OCCT geometry. No expected CAD measurement is hard-coded:
each numeric expectation is either the producer's own value or arithmetic on the
manifest performed inside the test.
