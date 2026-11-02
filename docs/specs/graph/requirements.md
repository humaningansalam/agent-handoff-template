# Graph Requirements

Graph answers work-oriented questions with current source paths and typed provenance: entrypoint, implementation, dependencies/callers, and relevant tests. Confirmed relations remain separate from lexical candidates.

Snapshot relations, provider artifacts, and text/evidence index are separate capabilities. A missing optional provider cache or evidence index must not erase readable snapshot relations. Features that require a missing component report typed partial/unavailable status.

Current-source overlay excludes stale stored relations. Ambiguity fails closed. Unsupported, partial, ambiguous, not-found, and zero-impact are distinct. Benchmarks judge whether representative project navigation is recovered, not edge count or response bytes alone.
