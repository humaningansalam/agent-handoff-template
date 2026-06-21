# Agent Workspace Control Plane

A repo-aware control substrate for Claude Code, Codex, Cursor, and other coding agents.

This is not an autonomous agent runtime. It provides deterministic task state, repo boundaries, handoff continuity, and metadata gates for external agents.

## What this is

This workspace separates:

- workspace operations in the root repo
- actual product code in `repos/` (preferred)
- task state in `docs/tasks/*.md`
- shared operating rules in `AGENTS.md`
- sparse file-level metadata in `<product-repo>/.repometa`

Agents do the reasoning and implementation. `repoctl` owns deterministic state transitions, mutation boundaries, and verification gates.

## Compared with adjacent tools

| Tool type | Focus | This project differs by |
|---|---|---|
| Markdown task managers | Tasks and Kanban | Adds repo separation, finish gates, and `.repometa` checks |
| Spec-driven tools | Spec -> plan -> tasks | Starts after task intent exists; preserves execution state and verification |
| Claude/Codex agents | Autonomous coding loop | Provides the workspace/state substrate they operate against |
| Claude plugins/hooks | Tool-specific enforcement | Core contract stays tool-agnostic through `AGENTS.md` |
| Knowledge/memory systems | Long-term agent knowledge | Future llmwiki should promote stable knowledge from task evidence |

## Use this when

- handoff quality matters more than chat/session history
- you want task files to be the execution unit
- you need a private workspace repo and a separate product code repo
- multiple agent tools may operate in the same workspace
- you want MCP, Graph, or llmwiki layers to sit on a stable workspace contract later

## 60-second start

1. Read `AGENTS.md`
2. Open `docs/BOARD.md`
3. Open the live task file
4. Continue from `## Handoff`

## Minimal structure

```text
.
|-- AGENTS.md
|-- README.md
|-- scripts/
|-- docs/
|   |-- README.md
|   |-- BOARD.md
|   |-- tasks/
|   |-- workflows/
|   |-- contracts/
|   |-- adr/
|   `-- archive/
`-- repos/
```

## Document map

- **Operating contract**: `AGENTS.md`
- **Task system guide**: `docs/README.md`
- **JSON output contract**: `docs/contracts/repoctl-json-contract.md`
- **repoctl module boundaries**: `docs/contracts/repoctl-module-boundaries.md`
- **Repo metadata identity ADR**: `docs/adr/repometa-identity-v0.md`
- **Field-test workflow**: `docs/workflows/v0-foundation-field-test.md`
- **Repo metadata rules**: `docs/workflows/repo-metadata.md`
- **Optional project context**: `docs/PRD.md`

## Notes

- `repos/` is the product code repository boundary.
- `docs/BOARD.md` is a live-task registry, not a status dashboard.
- Task state lives in task frontmatter, not in the board.
- Backlog items are raw planning blocks; agents read them and pass explicit task fields rather than relying on repoctl to parse intent.
- `.repometa` provides file-level discovery and changed-file metadata gates; `repoctl index code` extracts read-only technical facts, and neither is a generated graph.
- `repoctl graph build` emits a read-only deterministic snapshot over repository files, raw import references, repo-local topics, task completion receipts, and precise provider symbols/anchors.
- Future MCP should be transport over repoctl contracts, not a second mutation path.
- Future Graph and llmwiki layers should derive from task evidence, `.repometa`, and index facts without replacing their authority.
