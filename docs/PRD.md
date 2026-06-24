# Product Requirements

This workspace is building repoctl's Graph to Evidence Context to Reviewed Knowledge to llmwiki product loop.

The authoritative execution plan and completion criteria live in:

```text
docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
```

## Purpose

repoctl should let agents and humans answer repository questions without rediscovering the same evidence every session:

- where files and symbols are defined
- what imports or calls them
- what changes are likely impacted
- what must be read before a task starts
- what decisions, invariants, and failure modes are current
- where each answer comes from and whether the source is still current

## Scope

In scope:

- Graph evidence over source, imports, precise provider symbols/calls, `.repometa`, and task receipts
- Evidence Context query bundles
- Agent Context Pack for repo-scoped task startup
- Reviewed Knowledge candidate, review, approval, stale, supersede, and deprecate lifecycle
- Static llmwiki Markdown render with source and lifecycle navigation
- Fresh-copy field proof and release artifact E2E

Out of scope:

- MCP implementation or transport
- graph database migration
- vector or embedding pipeline before measured need
- web application llmwiki
- new maintenance harnesses or benchmark frameworks

## Completion Rule

Completion is defined only by `docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md`.
Phase completion, test counts, or "mostly done" reports do not override that plan.
