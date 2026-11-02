# Graph Requirements

Graph answers work-oriented questions with current source paths and typed provenance: entrypoint, implementation, dependencies/callers, and relevant tests. Confirmed relations remain separate from lexical candidates.

Snapshot relations, provider artifacts, and text/evidence index are separate capabilities. A missing optional provider cache or evidence index must not erase readable snapshot relations. Features that require a missing component report typed partial/unavailable status.

Current-source overlay excludes stale stored relations. Ambiguity fails closed. Unsupported, partial, ambiguous, not-found, and zero-impact are distinct. Benchmarks judge whether representative project navigation is recovered, not edge count or response bytes alone.

## Current Graph navigation interfaces

`graph query` reads the typed snapshot; `graph workflow --route /predict` or `--symbol audio_clock` produces ordered implementation, caller/dependent and test evidence without promoting candidates to confirmed relations. `graph search` retrieves bounded current-source and authority evidence. `graph workflow-benchmark` tests required stages and provenance, false positives and first-path correctness separately from candidate-inclusive recall. AST adapters are optional and expose partial coverage when resolution is not semantically provable.

## Work-path success and provenance

`graph workflow` returns entrypoints, implementations, callers/dependents and tests separately. Compiler/AST-confirmed edges are typed and distinguishable from textual candidates. A required but ungrounded stage is **not evaluated**, not an empty-success stage. Benchmarks include negative and stale cases, first action correctness, false-positive paths, provenance and required-stage precision; path-inclusive recall alone is never completion proof.

Document authority reflects declared adoption status as well as location: an explicitly draft/proposed requirement cannot be promoted to adopted authority by its filename.
