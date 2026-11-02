# Knowledge Requirements

Knowledge stores concise reusable decisions, invariants, and failure modes as direct v2 claim/reason/source records. Direct records are authoritative; projections are derived accelerators.

Default query uses a bounded direct-record fallback when a projection is missing, stale, or invalid. Projection damage is a warning; direct-record corruption remains an error. Source digest drift is reported without hiding a record. A missing source may relocate only when one bounded candidate has the recorded identity; ambiguous candidates fail closed.

The only active lifecycle labels are `current` and `superseded`; neither implies human approval. Normal query and derived Graph read validated v2 records only. The explicit, dry-run-first `knowledge migrate` command reports every remaining v1 record and converts one ID at a time with an original-byte witness. Missing reasons or source references are not guessed and conflicting source digests are rejected. Exact duplicates can consolidate only when complete claim/kind/reason/source identity matches and no old references remain. After conversion the v1 file is no longer an ordinary query input; no perpetual runtime v1 equivalence or compatibility status is maintained.
