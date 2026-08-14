# Reviewed Knowledge State

This directory stores adopter-owned Reviewed Knowledge state for repoctl.

Reviewed Knowledge is one curated lane inside the broader project knowledge base. `AGENTS.md`, `README.md`, `docs/PRD.md`, contracts, workflows, ADRs, product documentation, and task history remain current source-linked project knowledge consumed directly by Context and Graph. A zero record count here means only that no reusable claim has completed explicit review; it does not mean project knowledge is empty.

## Directories

- `records/` = approved Reviewed Knowledge records; adopter-owned source of truth.
- `events/` = append-only lifecycle events; adopter-owned source of truth.
- `generated/` = non-authoritative llmwiki render output; regenerable view.

Generated llmwiki pages are views. Do not re-ingest `generated/**` as factual authority for future Context, Knowledge, or task decisions. Use the original source refs, approved records, and lifecycle events instead.

The implemented current-head projection keeps immutable records/events as audit evidence while ordinary Knowledge queries, Graph materialization, Context, and default llmwiki rendering consume the digest-bound current heads. This hot projection has finite count and byte limits; approval and rebuild fail closed at the limit so ordinary agent work cannot grow with unbounded Knowledge history. Superseded, deprecated, and stale records remain available through explicit Knowledge history queries; default queries and rendering do not load them into the active result set. There is no separate paged historical llmwiki renderer in the current command surface.

The projection under `.repoctl-state/knowledge/<repo-id>/current-head.json` is derived and may be absent after a fresh clone or local-state loss. Recover it explicitly with `./scripts/repoctl knowledge rebuild --repo-id <id> --json`. Rebuild validates all durable records and lifecycle events for that repository before atomically publishing a replacement; approval never initializes an empty projection over existing durable history.

Approving a candidate updates the durable record/event state and synchronizes the selected repository's materialized Graph. A record is usable only when its current content matches `record_digest`, exactly one digest-valid approval event binds that content to its source candidate, and every declared supersession has one matching lifecycle event tied to that approval. Queries, Context, Graph, checks, and generated views fail closed on incomplete or altered approval state. Candidate applicability is explicit through `applies_to.paths`; changed files and prose remain provenance only.

Every candidate claim is explicit through `--claim` or `--claim-file`. Source documents, context packs, task artifacts, and completion receipts provide provenance only; repoctl never derives or replaces a reusable claim from their prose.
