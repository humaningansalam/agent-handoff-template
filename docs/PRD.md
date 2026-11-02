# Product Requirements: Agent Handoff Workspace

## Purpose

This workspace lets different agents, models, and sessions continue the same project work unit without relying on chat history. Product requirements and design are written first under `docs/`; independent Git repositories under `repos/` implement them; repoctl keeps the current work boundary legible and safe without owning agent execution, model placement, deployment, or delivery.

## Product model

The default operating layer is **Task, Board, and Workflows**.

- **Task** is the current execution record: Goal, actual current scope, current decisions or constraints, meaningful result or blocker, next action, completion condition, and links to source documents.
- **Board** lists live work and deferred backlog. Task frontmatter remains lifecycle authority.
- **Workflows** describe repeated, risky, or order-sensitive procedures.
- **Handoff** is the four-field restart instruction inside a Task, not a history store or independent product plane.

The understanding and reuse layer is **Graph and Knowledge**.

- **Graph** exposes current code structure, references, impact, entrypoints, implementations, dependents, and relevant tests with provenance, freshness, and explicit completeness.
- **Knowledge** preserves only reusable claim/reason/source records. `current` means not superseded, not human-approved. New releases expose no `reviewed` alias.

Source retrieval and ranking remain Graph/Knowledge internal read helpers. No separate Context/Pack/Capsule command, export, or durable user lifecycle exists.

## Read map

Read only the documents needed for the change:

- [Architecture overview](architecture/overview.md) — workspace boundaries and data flow.
- [Task requirements](specs/task/requirements.md) — Task, Handoff, resume, audit, and migration behavior.
- [Knowledge requirements](specs/knowledge/requirements.md) — direct records, projection fallback, lifecycle, and source identity.
- [Graph requirements](specs/graph/requirements.md) — relation queries, degradation, language support, and task-oriented results.
- [Language-index research](research/graph-language-indexes.md) — non-authoritative comparison of LSP, SCIP, and tree-sitter reuse.
- `docs/adr/` — important decisions and their outcomes.
- `docs/workflows/` — procedures that are repeated, risky, or order-sensitive.


## Primary flows

### Known change

1. Read `docs/PRD.md` and the linked capability document needed for the change.
2. Create and start one Task; record only the actual current Chosen files.
3. Implement in the selected product repository and run useful checks.
4. Keep only concise current results in the Task, then finish it.

No Graph or Knowledge query, export, or verification-registration action is mandatory.

### Ambiguous or large-codebase change

1. Use direct reads/search and Graph independently.
2. Ask for the work path: entrypoint → implementation → dependencies/callers → relevant tests.
3. Treat unsupported, partial, ambiguous, not-found, and zero-impact as different outcomes.
4. Record only the chosen current scope in the Task; do not copy ranked candidates or full output into it.

### Pause and resume

1. Keep exactly the next step, first file, first command, and done condition in Handoff.
2. Bind after the current Task contract, Chosen/Notes, repository observation, child lifecycle, and Handoff are correct.
3. Resume a known Task by explicit ID.

Execution Log additions and optional Verification prose do not alone make a Handoff stale. A failure that changes the next action must update Goal, constraints/Notes, or Handoff itself.

### Reuse a conclusion

1. Save a concise claim, technical reason, and sources only when the conclusion will matter again.
2. Query valid direct records even when a derived projection is missing, stale, or invalid.
3. Keep source drift visible without hiding the record; relocate a source only by unique identity evidence.
4. Replace an obsolete record explicitly.

## Requirements

- Task creation, start, scope change, block, cancel, and finish must not copy full dirty/residue/path arrays, fixture inventories, command output, queue state, or role/request history into Markdown.
- Task audit is read-only and warning-only. Migration defaults to dry-run and rewrites only recognized machine-generated dumps.
- Existing product code and adopter-authored documents are preserved during upgrade.
- Completed Task evidence is not rewritten or re-sealed as newly verified by a text migration.
- A valid Graph snapshot remains queryable when optional provider cache or text/evidence index components are unavailable.
- Current-source overlay and stale-relation exclusion remain intact.
- Optional native Tree-sitter indexes provide syntax-grounded Rust/Go/Java/Kotlin/Swift definitions and uniquely-resolved direct calls; missing type-level resolution is explicitly partial. Existing Python, TypeScript, Dart and C# providers remain independently available.
- `docs/PRD.md` is the physical minimum. Additional documents are created only when real complexity requires them; empty folders and empty templates are not required.
- repoctl supplies context and safe boundaries. It does not own external agents, queues, model/provider selection, deployment, or delivery.
- MCP is a future external access layer, not part of this release.

## Success criteria

- A new agent can recover the current Goal, actual scope, constraints, next action, and source documents without reading operational history.
- Normal Task lifecycle operations keep raw path arrays and bulk output out of Markdown.
- A plain log or optional Verification addition does not invalidate an otherwise current Handoff.
- Valid Knowledge records remain discoverable through projection failure with an explicit degraded warning.
- Research documents are searchable but are not product authority by location.
- Representative Graph queries return a useful work path, not merely a snapshot or edge count.
- Derived Graph/Knowledge maintenance does not erase valid source authority.
- Upgrade preserves repository boundaries, dirty baselines, lifecycle identities, adopter documents, and product code.

## Non-goals

Autonomous agent execution; model/provider routing; Coordinator/Expert/Worker hierarchies; external completion queues; deployment ownership; proof that a command ran; mandatory Graph or Knowledge usage; automatic scope inference; generated wiki/state systems; MCP implementation in this release.
