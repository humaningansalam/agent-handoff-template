# Reviewed Knowledge State

This directory stores adopter-owned Reviewed Knowledge state for repoctl.

## Directories

- `records/` = approved Reviewed Knowledge records; adopter-owned source of truth.
- `events/` = append-only lifecycle events; adopter-owned source of truth.
- `generated/` = non-authoritative llmwiki render output; regenerable view.

Generated llmwiki pages are views. Do not re-ingest `generated/**` as factual authority for future Context, Knowledge, or task decisions. Use the original source refs, approved records, and lifecycle events instead.
