# Render and inspect Stabileo RC footing handoffs

**POC / DESIGN PHASE — exploratory. No implementation, no upstream PR.**

Companion to the canonical Stabileo-side document. This branch exists on a **fork**
(`Batuis/text-to-cad`) as a design record and remote backup while the architecture is chosen. It is
not a contribution proposal yet, and nothing here is pushed to `earthtojake/text-to-cad`.

- **Canonical document:** Stabileo `docs/poc/rc-footing-cad-review.md`, on branch
  `pr/19-rc-cad-constructibility` (draft PR lambdaclass/stabileo#90).
- **This document follows it.** If the two disagree on the shared contract, the Stabileo document
  is correct and this one is stale.
- Base: upstream `develop` @ `258236e3c`.

## What Stabileo is asking of this repository

Stabileo produces reinforced-concrete footing details: concrete geometry plus physical rebar
centrelines with exact arcs, roles, marks and cover/spacing requirements. It emits **DXF R12 (2D)**
and cannot produce solids.

It needs three things this repository is genuinely good at:

1. **STEP + GLB generation** from a neutral manifest, with named bodies.
2. **A 3D review surface** — CAD Viewer, for a human or an agent to inspect.
3. **An independent geometric check** — a second opinion computed from the same manifest by a
   different codebase.

## What it is *not* asking for

**Not clash detection authority.** Stabileo already implements arc-exact bar-to-bar collision with
a broad-phase spatial hash and concrete-cover containment, with regulation-aware classification.
Moving that here would replace a tested, code-aware checker with a generic one.

The check implemented on this side is a **cross-check**: it measures against requirements Stabileo
supplies and reports agreement or disagreement with Stabileo's own verdict. Disagreement is a bug
in one of the two implementations and is the most valuable output of the exercise.

Also out of scope: **modal FEA**. This POC does not depend on PR #22 and does not install
Netgen/NGSolve.

## Capability audit — upstream `develop` @ `258236e3c`

| Capability | Status |
|---|---|
| STEP export / scene / targets / metadata / hashing | **on develop, reusable unchanged** — `packages/cadpy/src/cadpy/step_export.py`, `step_scene.py`, `step_artifact.py`, `step_artifacts.py`, `step_metadata.py`, `step_hash.py`, `step_targets.py` |
| GLB export / topology / mesh payload | **on develop, reusable unchanged** — `glb.py`, `glb_topology.py`, `glb_mesh_payload.py` |
| STL / 3MF | on develop — `stl.py`, `threemf.py` |
| Artifact + file metadata, source hashing | **on develop — use for determinism and staleness** — `metadata.py`, `file_metadata.py`, `source_hash.py` |
| Assembly composition / flatten / export | on develop — `assembly*.py` |
| Render + reporting | on develop — `render.py`, `reporting.py` |
| Skills `cad`, `cad-viewer`, `dxf`, `step-parts` | on develop |
| **General collision / clearance / containment API** | **NOT on develop.** Only `models/robots/lyra/lyra_parts/clearance.py` (model-specific) and robot collision *meshes* |
| On-demand STEP collision analysis | **open PR only** — #40 (`895ee55`), **210 commits behind** develop, and aimed at display edges rather than rebar clearance |
| CAD Viewer feedback loop | **open PR only** — #92 (`7328bbe`), 109 behind. Touches `viewer/src/client/components/CadWorkspace.js`, which #22 also touches — a live conflict between those two PRs |
| Solid-part modal FEA | **open PR only** — #22, out of scope |

## Contribution boundary

| Category | Content |
|---|---|
| **Reusable unchanged** | STEP/GLB/metadata/hash/assembly/report. Most of what this POC needs |
| **Plausible generic upstream addition** | A *rebar-agnostic* solid-clearance utility, if written — no CIRSOC, no Stabileo concepts |
| **Must stay out of upstream** | The `RcCadHandoffV1` importer, bar-role semantics, cover/spacing rules, CIRSOC clause references, issue taxonomies. Stabileo-specific |

### Licensing

text-to-cad is **MIT**; Stabileo is **AGPL-3.0**.

- MIT → AGPL is a permitted one-way relicense: Stabileo may absorb MIT code, retaining the notice.
- **AGPL → MIT is not.** No Stabileo code, tables or test fixtures may be copied into this
  repository. Anything contributed here must be written fresh for this repository.
- The recommended process boundary is **arm's-length** (files + CLI), which keeps both licences
  untouched and avoids creating a derivative work in either direction.

## Repository rules this POC must respect

From `AGENTS.md` on `develop`:

- Branch from `develop`; PRs target `develop`; `main` is publish-only.
- `develop` uses **symlinks** across generated runtime, viewer package and plugin paths — edit the
  source target, not the copy. Verify with `scripts/dev/setup-symlinks.sh --check`.
- **Every skill must be self-contained at runtime.** No importing from sibling skills, `skills/`
  root, or the repository root; no adding those to `sys.path`/`PYTHONPATH`. Shared helpers live
  under `packages/` and are **vendored** into skill runtimes.
- Refresh generated runtimes/plugins with `scripts/bundle/bundle.sh`; check with `--check`.
- Never hand-edit `plugins/cad/VERSION`.
- All CAD/robot artifacts belong under `models/` — no ad hoc artifact directories. Review media
  (PNGs, GIFs) must **not** go in `models/`; render to `/tmp` and attach.
- CAD Viewer is started via the documented `serve` entrypoint with an absolute `?dir=`; read the
  bound port from its `--json` line rather than assuming 4178. Never stop an existing Viewer.
- Checks: `scripts/test/test.sh`, or focused `test-python.sh` / `test-js.sh`.

## Shared contract — mirrors the canonical document

Summary only; the authoritative field table with owner, requiredness, unit, coordinate space,
validation and absent-behaviour lives in the Stabileo document.

| Group | Content |
|---|---|
| Envelope | `schema` = `RcCadHandoffV1`, `schemaVersion`, `generatedAt`, `generator` |
| Identity | project, `model.elementId`, `source.gitRevision` |
| Revisions | analysis, design, detailing, document — all four echoed back in the review for staleness |
| Certificate | maturity (incl. `IMPLEMENTED_PROVISIONAL`), verifier/code/edition |
| Units / frame | metres, degrees, **Z-up right-handed**, explicit `origin` translation + optional quaternion |
| Concrete | footing (required), pedestal (optional), supported column (optional), each with a stable `bodyId` |
| Bars | id, mark, role, `diameterMm`, material, `layerId`, `ownerElementIds`, `cuttingLength`, segments |
| Segments | `straight \| arc`; arcs carry `radius`, `sweepDeg` **and `centre`** |
| Ends | `straight \| hook \| continuous`; hook geometry only when actually established |
| Requirements | required cover, required clear spacing, optional clause refs — **Stabileo owns these** |
| Honesty | assumptions, unsupported conditions, and `stabileoVerdict` to cross-check against |

**Why `centre` is mandatory on arcs:** start, end, radius and sweep do **not** determine an arc in
three dimensions — two centres satisfy them in any plane, and the plane is free. Without the centre
the only reconstructable curve is the chord, and the deviation is the full sagitta (5.9 mm on a Ø8
90° bend; 12.3 mm on a 135° hook — larger than the bars being checked). A missing centre must
degrade to a chord **with `arcApproximated: true` declared**, never silently.

**GLB is Y-up by glTF convention.** The Z-up → Y-up conversion belongs on this side and must be
declared in the GLB metadata. The manifest stays Z-up.

## Planned pipeline on this side

```
RcCadHandoffV1 (JSON, validated)
  → build concrete solids from explicit primitives
  → sweep a circular profile along each bar centreline, honouring stored arc centres
  → name every body: concrete:<bodyId> / bar:<barId>
  → STEP (cadpy step_export) + GLB (cadpy glb, Y-up declared)
  → deterministic metadata sidecar (cadpy source_hash / file_metadata)
  → cross-check: bar/bar intersection, clear spacing, cover, containment,
    duplicate/coincident bars, dowel-starter interference, tie/longitudinal clash,
    incomplete-geometry blockers
  → RcCadReviewV1 JSON: issues with deterministic IDs, measured values, the requirement
    violated, and agreement/disagreement with stabileoVerdict
  → CAD Viewer for inspection
```

Determinism: identical input must produce identical STEP/GLB/review bytes. No timestamps inside
geometry payloads. Issue IDs are a hash of `(checkKind, sorted participant IDs, quantised
measurement)` — order-independent and stable across runs.

Artifacts go under `models/` per repository policy; review screenshots go to `/tmp`.

## Fixtures

1. **Clean** — a real Stabileo PR18 footing. Expect zero issues **and** agreement with
   `stabileoVerdict`.
2. **Invalid derivative** — a controlled mutation of fixture 1 (one bar translated into a
   collision, one moved outward to break cover, two brought together to break clear spacing), with
   deterministic issue IDs, measured evidence, and visible Viewer highlighting.

A blocker from `unsupportedConditions` must be reported as a **blocker**, never as a clean pass.

## Responsibility boundary

**Stabileo owns** structural design, regulation requirements, required cover and spacing, physical
reinforcement intent, revision and certificate state.
**This side owns** geometric realization, intersection, measured clearances, containment, and
visible artifact inspection.
**This side must never silently change the Stabileo design** — it returns findings, never edits.

## Status

Design only. No code. No upstream PR. No Netgen/NGSolve. Awaiting the user's architecture
decisions, recorded in the canonical Stabileo document.
