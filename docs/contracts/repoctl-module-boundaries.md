# repoctl Module Boundaries

These boundaries keep the small product model stable: Task owns current work and Handoff, Graph owns typed source navigation and its read-only evidence search, and Knowledge owns optional reusable reasoning.

## Ownership

- **Task lifecycle** owns task frontmatter, Board membership, archive transitions, repository start baselines, pre-existing dirty ownership, Chosen-scope closure checks, Handoff bindings, completion receipts, and live-task resume selection.
- **Task Markdown** owns human meaning: Goal, Chosen and Notes, Execution Log, optional Verification prose, and the four Handoff fields. Repoctl validates structure but does not infer correctness from prose.
- **Graph evidence search** is the bounded, read-only source-linked retrieval implementation inside Graph. It reuses source documents, explicit historical queries, Knowledge, and materialized relations without writing scope or query-selection state.
- **Graph** owns a rebuildable typed relation snapshot over provider facts, metadata, documents, task/history subjects, and Knowledge. Queries never rebuild it implicitly.
- **History projection** owns the derived completion catalogue, checkpoint, bounded hot history, and exact cold-history lookup. Completion receipts and Task artifacts remain the source history.
- **Knowledge** owns current durable records, source resolution, query projection, explicit replacement links, and migration reporting for preserved older records.
- **Backlog** owns opaque raw block CRUD only.
- **Metadata** owns `.repometa` policy, annotations, exclusions, move repair, and metadata validation.
- **Index providers** own read-only technical facts such as language, imports, symbols, calls, effects, and structured dependencies.
- **CLI** owns argparse, JSON envelopes, human presentation, and command wiring.
- **Debug diagnostics** owns opt-in sanitized bounded observations under ignored local state. It never owns product or lifecycle authority.

## Derived-layer rules

- Graph and index state are disposable derivatives. A stale derivative may require maintenance but does not make a healthy workspace upgrade incomplete.
- Graph evidence and typed relation results may propose navigation, never Task scope, ownership, or authority.
- Lexical candidates and compatible unresolved relationships remain separate from confirmed typed relations.
- Explicit history searches appear only in explicit history modes and cannot alter current-source ranking, Graph seeds, traversal, or Chosen.
- Completion receipt hashes bind history identity. They do not prove that code was checked or a test passed.
- Knowledge source digests identify recorded provenance. Later drift is a warning and does not hide the saved decision.
- Direct Knowledge records need no approval event. Older events are preserved files and are not an execution or query lane.
- Current Graph searches and relation queries create no persistent result receipt. Retired result and Discovery state is ignored; historical completion receipts retain their isolated read boundary.
- `.repometa` stores sparse human metadata, never Graph fields or derived retrieval state.
- The Dart semantic provider owns resolved RPC source facts and invocation validity; the structured resolver owns SQL compatibility. Graph consumes their typed results without a second language scanner.
- Multi-repo identity comes from the repository registry, not path-name inference.
- MCP, if added, calls stable command handlers or consumes JSON; it does not parse human output or bypass mutation boundaries.

## Repository layout

The preferred single product Git root is `repos/`. A collection layout uses registered roots under `repos/<name>/`. A product repository may itself be a monorepo; packages, apps, and services inside it are scoped surfaces rather than new workspace roots.

Root Task/control state and each product Git repository remain separate. Root scripts locate the workspace from their own path.

## Forbidden shortcuts

- No natural-language parsing of Backlog, PRD, Task, Verification, or Handoff prose into scope, status, or proof.
- No project-specific hardcode in core discovery defaults.
- No direct `.repometa` mutation outside repoctl in normal operation.
- No hidden state-changing recovery in `task doctor`, query commands, or `next_actions`.
- No automatic query receipts, Discovery episodes, verification registrations, Pack bindings, Knowledge approvals, or generated wiki state reintroduced under a different name.
