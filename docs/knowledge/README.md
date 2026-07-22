# Reviewed Knowledge State

This directory stores adopter-owned Reviewed Knowledge state for repoctl.

Reviewed Knowledge is one curated lane inside the broader project knowledge base. `AGENTS.md`, `README.md`, `docs/PRD.md`, contracts, workflows, ADRs, product documentation, and task history remain current source-linked project knowledge consumed directly by Context and Graph. A zero record count here means only that no reusable claim has completed explicit review; it does not mean project knowledge is empty.

## Directories

- `records/` = approved Reviewed Knowledge records; adopter-owned source of truth.
- `events/` = append-only lifecycle events; adopter-owned source of truth.
- `generated/` = non-authoritative llmwiki render output; regenerable view.

Generated llmwiki pages are views. Do not re-ingest `generated/**` as factual authority for future Context, Knowledge, or task decisions. Use the original source refs, approved records, and lifecycle events instead.

Approving a candidate updates the durable record/event state and synchronizes the selected repository's materialized Graph. A record is usable only when its current content matches `record_digest`, exactly one digest-valid approval event binds that content to its source candidate, and every declared supersession has one matching lifecycle event tied to that approval. Queries, Context, Graph, checks, and generated views fail closed on incomplete or altered approval state. Candidate applicability is explicit through `applies_to.paths`; changed files and prose remain provenance only.

Every candidate claim is explicit through `--claim` or `--claim-file`. Source documents, context packs, task artifacts, and completion receipts provide provenance only; repoctl never derives or replaces a reusable claim from their prose.
