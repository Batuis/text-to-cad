# Fixture provenance and licensing

## `rc-footing-cad-poc.handoff.json`

| | |
|---|---|
| Size | 88,101 bytes |
| SHA-256 | `795e9de26f2eb8ce8d51f2ac7130336702fc534588f390071e3bd40bc03aa0e7` |
| Contract | `RcCadHandoffV1`, schema version 1 |
| Producer | `stabileo-rc-cad-handoff` 1.0.0 |
| Upstream project | Stabileo (`lambdaclass/stabileo`), branch `pr/19-rc-cad-constructibility`, draft PR #90 |
| Producing source fixture SHA-256 | `15ce4e150919bf8f91ef1e3fae36dcde584b770fea45861465742654153e3e79` |

### What this file is

**Generated output data, not source code.** It is one document emitted by
Stabileo's `RcCadHandoffV1` exporter from a committed project fixture. It
contains geometry, identifiers, requirements and verdicts — no program text, no
algorithm, and no part of any Stabileo implementation module.

It is committed here so the consumer's tests exercise a real production document
rather than a hand-built imitation, which is the only way the tests can prove the
consumer reads the actual contract.

### Licensing

Stabileo is **AGPL-3.0**. This repository is **MIT**.

- This file is **not** covered by this repository's MIT licence. It is
  third-party generated data, retained under its originating project's terms,
  and it is included here solely as a test input.
- No Stabileo source was copied to produce or to read it. The consumer in
  `packages/rc_cad_handoff` is an independent implementation written against the
  documented interchange format. The format's field names and values appear in
  that implementation because interoperability requires them, which is a
  functional requirement rather than an expressive copy.
- Stabileo's JSON Schema document is deliberately **not** vendored. Structural
  and semantic validation is implemented independently in
  `packages/rc_cad_handoff/src/rc_cad_handoff/manifest.py`.
- **AGPL → MIT relicensing is not permitted, and nothing here attempts it.** This
  file is not relabelled as MIT, and no Stabileo code, classification table or
  rule implementation is present in this repository.

### Regenerating

Re-export from Stabileo rather than editing this file by hand. A hand-edited
manifest would no longer be a production document, and its recorded SHA-256 —
which the consumer can pin with `--expect-sha256` — would no longer mean
anything.
