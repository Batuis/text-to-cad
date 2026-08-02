# Render and inspect Stabileo RC footing handoffs

**POC — first consumer checkpoint implemented. No upstream PR.**

> **Status, 2026-08-01.** The consumer is implemented in
> `packages/rc_cad_handoff` with tests under
> `tests/python/packages/rc_cad_handoff`. See
> [Implemented checkpoint](#implemented-checkpoint-2026-08-01) at the end of this
> document for what was built, what it measured, and what remains open. The
> design sections below are unchanged and remain the record of why.

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

Design recorded; consumer implemented. No upstream PR. No Netgen/NGSolve, and no dependency on
PR #22 or PR #40.

---

# Implemented checkpoint — 2026-08-01

The first coherent consumer checkpoint. Everything below was measured, not assumed.

## Where the code lives, and why not in a skill

`packages/rc_cad_handoff` — an independent Python package depending on `cadpy`.

The consumer is specific to one producer's interchange format, so it does not belong in
`packages/cadpy`, which is the generic shared artifact runtime. Putting it in a skill would ship
it inside the installable `plugins/cad` bundle, which is wrong for a POC companion this document
already says must stay out of upstream. `packages/` is where shared Python source lives; nothing
vendors this package, so it never reaches the plugin.

`scripts/test/test-python.sh` gained one runner line. No skill, plugin, viewer or `cadpy` source
was modified.

## cadpy reused unchanged

| Need | Reused |
|---|---|
| STEP assembly write + scene load | `cadpy.step_export.export_build123d_step_scene` |
| Manifest hash into STEP metadata | `cadpy.step_metadata`, `cadpy.file_metadata.text_to_cad_identity_metadata` |
| Meshing | `cadpy.step_scene.mesh_step_scene`, `scene_export_shape` |
| GLB (native, Y-up declared) | `cadpy.glb.export_native_glb_from_scene` |

Geometry uses OCCT through `OCP` directly — `GC_MakeArcOfCircle`,
`BRepOffsetAPI_MakePipeShell`, `BRepPrimAPI_MakeBox`, `BRepExtrema_DistShapeShape`,
`BRepAlgoAPI_Common`/`_Cut`, `BRepBndLib`. No general collision API exists on `develop`, as this
document's audit already found, so the cross-check is new code scoped to this manifest.

## What the checkpoint produced

From the canonical manifest (88,101 bytes, SHA-256 `795e9de2…3aa7`):

- 16 named solids — 2 concrete components, 8 column dowels, 6 starter ties;
- 38 arcs realised exactly from their supplied centres; **0** approximated, **0** chords
  substituted;
- STEP + GLB with every solid separately named `concrete:<bodyId>` / `bar:<barId>`;
- `cad-review.json`, deterministic and timestamp-free;
- all **12** authoritative `prohibitedOverlap` pairs independently resolved and measured, with
  CAD agreeing with the producer on every one, worst delta **0.0016 mm** against the approved
  0.5 mm band.

## Three findings the geometry forced

**1. `radius`/`sweepDeg` are nominal; `centre` is authoritative.** All 38 arcs have endpoints
equidistant from their supplied centre to machine precision (worst 5.6e-17 m), so
`(start, end, centre)` always defines a genuine circular arc. But on the twelve 135° stirrup
hooks — the arcs whose bend plane is inclined in z — the realised radius exceeds the declared
`radius` by 0.0748 mm, sweep by 0.2355°, and length by 0.2382 mm. A consumer that validated
`|start − centre| == radius` strictly would reject twelve exact arcs. This consumer builds from
the centre and reports the deviation under
`approximationInventory.nominalBendParameterDeviations`, separately from the approximation
inventory, because the curve itself is exact. Worth a contract-clarity note on the producer
side; not a data defect.

**2. Solid distance cannot give penetration depth, but exact centrelines can.**
`BRepExtrema_DistShapeShape` returns 0 for all twelve pairs — confirming contact-or-overlap and
nothing more. Signed clearance is therefore taken as exact centreline distance minus the sum of
the radii, which reproduces the producer's own `measured` to 0.0016 mm. That turns the
cross-check from a classification agreement into a real numerical one.

**3. OCCT booleans are degenerate exactly where this cage is interesting.** Two pairs
interpenetrate along *coincident* centrelines, and `BRepAlgoAPI_Common` returns an empty result
for them; one starter tie's legs are coplanar with the footing's top face, and the clip boolean
returns empty there too. Both are recorded as `numericalLimitation` issues. Neither is allowed
to read as a result: a zero intersection volume never downgrades an interpenetration to
contact, and a failed clip is never reported as "no material inside".

## Cover and containment, as the policy requires

| Check | Policy | What the consumer did |
|---|---|---|
| `barCollision` | `MAY_CROSS_CHECK` | measured all 12 pairs; agreement |
| `barClearSpacing` | `MAY_CROSS_CHECK` | no findings to cross-check |
| `concreteCover` (footing) | `MAY_OBSERVE_NOT_COMPARABLE` | measured; labelled `NOT_COMPARABLE` |
| `concreteCover` (column stub) | `OUT_OF_SCOPE` | **not measured at all** |
| `reinforcementContainment` | `MAY_OBSERVE_NOT_COMPARABLE` | measured; labelled `NOT_COMPARABLE` |

Footing cover is measured against the pad's bottom and sides only. The top face is excluded: it
carries the internal interface, and the requirement declares no `surface` scope, which this
format defines as the producer modelling no per-face distinction — explicitly not a wildcard
over every face. The limitation is recorded as an `observationScopeLimitation` rather than
resolved by assumption.

**The observation itself:** minimum observed cover **0.036 m** against a 0.050 m placement
intent. Reported as `coverObservationBelowRequirement` with `comparison: NOT_COMPARABLE`. It is
a geometric observation and not a breach verdict — the producer reports containment as
`NOT_EVALUATED`, so there is no verdict to contradict, and the footing mat geometry is declared
unmodelled, so this document carries only part of the reinforcement. It is exactly the kind of
new evidence option 2-C was chosen to produce, and it needs triage on the producer side rather
than a conclusion here.

## Reproducibility, stated rather than claimed

GLB is byte-identical across runs. STEP is not, in two ways, neither in the geometry: the
ISO-10303-21 `FILE_NAME` header carries a wall-clock timestamp, and a second export inside one
interpreter shifts `NEXT_ASSEMBLY_USAGE_OCCURRENCE` numbering via process-global writer state.
One export per process reproduces the normalised content hash exactly. Nothing rewrites the
header to fake a stable hash; both hashes are recorded and the nondeterministic layer is named.

`cad-review.json` is deterministic in every field it computes itself and carries no timestamp.
Across two runs it differs in exactly one field — `artifacts.step.sha256` — because that field
records the real bytes of the STEP file, whose header timestamp changes. Reporting that hash
truthfully is preferred to substituting a normalised one; `contentSha256` sits beside it and is
stable. A test pins the difference to that single field.

## Licensing

No producer source, JSON Schema document, classification table or rule implementation was
copied. The parser and validator are independent implementations written against the documented
format. The one artifact carried across is the generated manifest, kept as a test fixture with
its provenance and licensing recorded in
`tests/python/packages/rc_cad_handoff/fixtures/PROVENANCE.md`; it is not relabelled MIT.

## Still open

- Viewer linking and issue highlighting by body name — deliberately not started.
- Reading `cad-review.json` back into the producer as a persisted review record.
- An invalid-derivative fixture (translated bar, broken cover, broken spacing). The real
  document already carries twelve genuine overlaps, so the disagreement path is tested by
  injecting a skewed producer measurement instead.
- A generic, rebar-agnostic solid-clearance utility remains the only plausible upstream
  contribution, and is not proposed here.

---

# Reviewing the model locally — 2026-08-01

How to regenerate the artifacts and inspect them in the existing CAD Viewer, with the review
read **beside** the Viewer rather than inside it. Nothing in this section adds a Viewer panel,
a findings overlay, or a second web application.

> **This model contains unresolved detailing findings and is not construction-ready.**

## Prerequisites

Python, per `CONTRIBUTING.md`:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-dev.txt
```

Viewer, per `CONTRIBUTING.md` and `AGENTS.md`:

```bash
npm --prefix viewer install
npm --prefix packages/cadjs install        # required: see note below
npm --prefix packages/implicitjs install   # only for the implicitjs suite
npm --prefix viewer run build              # serve mode needs viewer/dist
```

`npm --prefix packages/cadjs install` is not optional for tests. Three viewer suites
(`renderState`, `fileSessionState`, `sidebar`) import through the `viewer/packages/cadjs`
symlink; Node resolves their `three` import from the **real** `packages/cadjs/` path, which
does not see `viewer/node_modules`. Without it those three files fail with
`ERR_MODULE_NOT_FOUND: three` — a missing install, not a code defect. With it the viewer suite
is 400/400.

Netgen and NGSolve are **not** installed and are not required.

## Canonical input

The manifest is the Stabileo-side fixture, read directly from the PR19 worktree and pinned by
hash — the CLI refuses to run if it does not match:

```
web/src/lib/export/__fixtures__/rc-footing-cad-poc.handoff.json
88,101 bytes
sha256 795e9de26f2eb8ce8d51f2ac7130336702fc534588f390071e3bd40bc03aa0e7
```

## Generating the artifacts

```bash
.venv/bin/rc-cad-handoff \
  <stabileo-pr19>/web/src/lib/export/__fixtures__/rc-footing-cad-poc.handoff.json \
  -o tmp/rc-cad-handoff \
  --expect-sha256 795e9de26f2eb8ce8d51f2ac7130336702fc534588f390071e3bd40bc03aa0e7
```

Output — `tmp/` is disposable, untracked (`.gitignore`), and safe to delete at any time:

| File | Bytes |
|---|---|
| `tmp/rc-cad-handoff/rc-cad-Z1-det3-dem2.step` | 347,551 |
| `tmp/rc-cad-handoff/rc-cad-Z1-det3-dem2.glb` | 1,964,408 |
| `tmp/rc-cad-handoff/cad-review.json` | 107,024 |

**None of these are committed.** `AGENTS.md` requires permanent CAD artifacts to live under
`models/`; these are a throwaway review run, not durable fixtures, so they go to `tmp/` and are
regenerated on demand instead of being tracked.

Determinism, reconfirmed on two runs: the GLB is byte-identical; the STEP differs only in its
ISO-10303-21 `FILE_NAME` timestamp, with `contentSha256` stable at
`db6ac10558b0eaea4f5ca466b7f32149af07350945cda411606bd1450d510187`; `cad-review.json` differs
in exactly one field, `artifacts.step.sha256`, which honestly records those raw STEP bytes.

## Starting the Viewer

Documented `serve` entrypoint only, run from `skills/cad-viewer`:

```bash
npm --prefix scripts/viewer run serve -- \
  --host 127.0.0.1 \
  --dir /absolute/path/to/text-to-cad/tmp/rc-cad-handoff \
  --shutdown-after 12h --json
```

Read the bound port from the final `{...}` stdout line rather than assuming `4178`. `--dir`
must be absolute and becomes the `?dir=` root; `file=` is relative to it. The server refuses
any path outside that root.

```
http://127.0.0.1:4178/?dir=%2Fabsolute%2Fpath%2Fto%2Ftext-to-cad%2Ftmp%2Frc-cad-handoff&file=rc-cad-Z1-det3-dem2.glb
```

This does not collide with Stabileo's own server on port 4000.

## Which file to open, and why both

| Open | What the Viewer gives you |
|---|---|
| `…​.glb` | mesh view only — orbit/pan/zoom, appearance, metadata (path, size, sha256) |
| `…​.step` | **assembly tree with the stable body names**, per-body show/hide, inspect/focus |

`references/viewer-features.md` is explicit that assembly trees and part hide/show are STEP
features; `.glb` is mesh-only. So the GLB confirms the delivered mesh loads and measures
correctly, and the **STEP** is what you drive for anything that needs a body name. Both come
from the same run and carry the same `concrete:<bodyId>` / `bar:<barId>` names.

### STEP sidecar note

The catalog scanner lists a standalone `.step` with an empty hash until its hidden render
sidecar exists, because it expects `.rc-cad-Z1-det3-dem2.step.glb`. Opening the STEP once makes
the Viewer generate that sidecar itself (`stepArtifactGenerationAvailable: true`), after which
the catalog entry resolves normally. The sidecar is the Viewer's own derived cache — hidden,
inside disposable `tmp/`, untracked, and regenerated on demand. Nothing here fabricates a
sidecar or renames a file to satisfy the scanner.

Splitting the outputs into `artifacts/` and `viewer/` subdirectories was considered and
rejected: the review records `artifacts.step.path` / `artifacts.glb.path` relative to the
output directory, so moving either file would leave the review pointing at a path that does not
exist. Keeping one flat directory preserves that provenance; the transient scanner warning is
the cheaper cost.

## Reading the review beside the Viewer

There is no CLI that reads an existing `cad-review.json` back — `rc-cad-handoff` recomputes a
review rather than reporting a stored one. To inspect the artifact you already generated,
without recomputing anything:

```bash
python3 - tmp/rc-cad-handoff/cad-review.json <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
s, src, art = r["summary"], r["source"], r["artifacts"]
kind = lambda k: [i for i in r["issues"] if i["kind"] == k]
print("THIS MODEL CONTAINS UNRESOLVED DETAILING FINDINGS AND IS NOT CONSTRUCTION-READY.\n")
print(f"manifest      {src['manifestSha256']}")
print(f"subject       {src['subject']['name']} ({src['subject']['kind']}), scope {art['assemblyLabel']}")
print(f"solids        {s['concreteComponents']} concrete + {s['barSolids']} bars; {s['totalArcs']} arcs, {s['approximatedArcs']} approximated")
for k in ("step", "glb"):
    a = art[k]
    print(f"{k.upper():4s}          {a['sha256']}  ({a['sizeBytes']} B)"
          + (f"\n              contentSha256 {a['contentSha256']}" if a.get("contentSha256") else ""))
print(f"overlaps      {s['authoritativeFindingCount']} producer findings; "
      f"{s['agreementCount']}/{s['crossCheckedPairCount']} agree, {s['disagreementCount']} disagree; "
      f"worst delta {s['worstDeltaM']*1000:.4f} mm")
for i in kind("coverObservationBelowRequirement"):
    print(f"cover         {i['minimumObservedCover']*1000:.0f} mm observed vs {i['requiredDistance']*1000:.0f} mm "
          f"placement intent — {i['comparison']} ({i['checkId']})")
for p in r["observationPolicy"]:
    print(f"check         {p['checkId']:44s} producer={p['producerVerdictStatus']:14s} consumer={p['consumerAction']}")
for i in kind("numericalLimitation"):
    who = i.get("barIdA","") + ((" / " + i["barIdB"]) if i.get("barIdB") else "")
    print(f"unmeasurable  {who}")
print(f"intentional   {s['unreportedClosePairCount']} close pairs declared non-reportable by the producer")
for i in kind("unsupportedCondition"):
    print(f"unsupported   {i['code']}")
print(f"\ncleanPass={s['cleanPass']} blocked={s['blockedByUnsupportedConditions']}\n{s['cleanPassRationale']}")
PY
```

That surfaces, from the stored file: manifest identity and model scope; both artifact hashes;
12 prohibited overlaps with CAD agreeing on all 12 and a worst delta of 0.0016 mm; 36 mm
observed cover against 50 mm placement intent marked `NOT_COMPARABLE`; containment
`NOT_EVALUATED`; column cover `OUT_OF_SCOPE`; the unmeasurable interface tie; 48 intentional
contacts; the unmodelled footing mat; and the not-construction-ready verdict.

## Stable-ID reconciliation

The link between the review and the Viewer is the body name — `bar:<barId>` in the assembly
tree is the same `barId` the review reports. To check a finding:

1. take the `barId` from `cad-review.json`;
2. open the **STEP** in the Viewer and expand the tree;
3. hide everything except that bar (or pair);
4. confirm the geometry matches what was reported.

Four worked examples, all confirmed this way:

| Finding | Stable IDs | What the Viewer shows |
|---|---|---|
| Coincident centrelines, `cadCentrelineDistance` 0.0 m, degenerate boolean | `bar:F1-C1-dowel-4` + `bar:F1-C1-dowel-6` | one apparent shaft, two hook feet diverging at the base — two Ø16 bars in the same space |
| 1.1716 mm centreline separation, clearance −14.828 mm | `bar:F1-C1-dowel-0` + `bar:F1-C1-dowel-4` | two shafts touching along their length |
| Governing 36 mm observed cover | `bar:F1-C1-dowel-0` (all eight dowels tie at 0.036 m) | hook foot sitting near the pad's bottom face |
| Unmeasurable interface tie | `bar:F1-C1:starter:stirrup:0.0000` | tie lying flush in the footing's top face — the coplanar case where the clip boolean returns empty |

Independent corroboration: rebuilding the centrelines from the manifest and measuring minimum
distance reproduces 1.1752 mm against the review's 1.1716 mm, and ~0.1 mm against 0.0 mm for
the coincident pairs, both within polyline sampling error of the review's exact OCCT values.

## Limitations found while doing this

- **The GLB is 1000× smaller than life.** `cadpy`'s `glb_mesh_payload.CAD_TO_GLB_SCALE = 0.001`
  is a millimetre→metre conversion, correct for `cadpy`'s usual mm-authored STEP models but
  wrong for this manifest, which is authored and built in metres. The 2.000 m footing exports
  as 0.002 glTF units. The factor is **uniform** — every body shares it to float precision, so
  proportions, relative positions and the Z-up→Y-up conversion (right-handed, verified against
  the manifest; nothing mirrored) are all intact, and the Viewer frames the model normally.
  But against the glTF convention of 1 unit = 1 metre the assembly is not to scale, and the GLB
  declares no unit or up-axis metadata to say so. Not corrected here.
- No interactive findings overlay. Issues are not highlighted in the Viewer; the review is read
  separately, as above. That is the deliberate scope of this checkpoint.
- The GLB path has no assembly tree, so body-name work has to go through the STEP.
- Model scope remains a footing column-transfer cage: 2 concrete bodies, 8 dowels, 6 starter
  ties. The footing mat is declared unmodelled, so this document carries only part of the
  reinforcement.

## Upstream conflict

Upstream PR #92 (`feat/viewer-feedback`, open, last updated 2026-06-25) touches
`CadWorkspace.js`, `CadViewer.js`, `cadManifestStore.js`, `httpHandlers.mjs`,
`localAssetBackend.mjs` and `cadDirectoryScanner.mjs`, plus `skills/cad-viewer/SKILL.md` and
`.gitignore`. This workflow uses the Viewer entirely as shipped and modifies none of them; an
overlay would have had to.

## Cleanup and regeneration

```bash
rm -rf tmp/rc-cad-handoff       # includes the Viewer's hidden .step.glb sidecar
```

Then rerun the generation command above. `tmp/` is disposable and untracked; nothing under it
is a source of truth.
