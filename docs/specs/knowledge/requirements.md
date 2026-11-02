# Knowledge Requirements

Knowledge stores concise reusable decisions, invariants, and failure modes as direct v2 claim/reason/source records. Direct records are authoritative; projections are derived accelerators.

Default query uses a bounded direct-record fallback when a projection is missing, stale, or invalid. Projection damage is a warning; direct-record corruption remains an error. Source digest drift is reported without hiding a record. A missing source may relocate only when one bounded candidate has the recorded identity; ambiguous candidates fail closed.

The only active lifecycle labels are `current` and `superseded`; neither implies human approval. Normal query and derived Graph read validated v2 records only. The explicit, dry-run-first `knowledge migrate` command reports every remaining v1 record and converts one ID at a time with an original-byte witness. Missing reasons or source references are not guessed and conflicting source digests are rejected. Exact duplicates can consolidate only when complete claim/kind/reason/source identity matches and no old references remain. After conversion the v1 file is no longer an ordinary query input; no perpetual runtime v1 equivalence or compatibility status is maintained.

## Immutable one-time legacy conversion (v0.14.1)

A referenced v1 original is historical evidence and must **never** be rewritten or deleted to create the current v2 view. Explicit `knowledge migrate` produces or identifies v2 and a digest-verified original witness, keeping the v1 path and bytes intact for current record citations. Only verified one-time proofs suppress repeated legacy-migration warnings. A missing or corrupt proof must fail closed; legacy content itself never becomes a normal current query head. Converted historical `replaces` targets are permitted only after proof validation. Source drift, absent applicability and ambiguous equivalent claims remain visible blockers; never infer a new human approval or amend a v2 citation digest merely to pass a check.
