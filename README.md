# Agent Workspace Control Plane

A repo-aware control substrate for Claude Code, Codex, Cursor, and other coding agents.

This is not an autonomous agent runtime. It provides deterministic task state, repo boundaries, handoff continuity, and metadata gates for external agents.

## What this is

This workspace separates:

- workspace operations in the root repo
- actual product code repositories under `repos/`
- workspace task/control state in root `docs/**`
- shared operating rules in `AGENTS.md`
- sparse file-level metadata in `<product-repo>/.repometa`

Agents do the reasoning and implementation. `repoctl` owns deterministic state transitions, mutation boundaries, verification gates, Graph/Context evidence, Reviewed Knowledge records/events, and non-authoritative llmwiki rendering. Root `docs/**` contains private workspace task/control state, contracts, workflows, and adopter-owned PRD/context. `docs/PRD.md` ships as a template seed that adopters may delete, replace, or split under root `docs/prd/`.

## Compared with adjacent tools

| Tool type | Focus | This project differs by |
|---|---|---|
| Markdown task managers | Tasks and Kanban | Adds repo separation, finish gates, and `.repometa` checks |
| Spec-driven tools | Spec -> plan -> tasks | Starts after task intent exists; preserves execution state and verification |
| Claude/Codex agents | Autonomous coding loop | Provides the workspace/state substrate they operate against |
| Claude plugins/hooks | Tool-specific enforcement | Core contract stays tool-agnostic through `AGENTS.md` |
| Knowledge/memory systems | Long-term agent knowledge | Reviewed Knowledge and llmwiki keep durable records separate from generated views |

## Use this when

- handoff quality matters more than chat/session history
- you want task files to be the execution unit
- you need a private workspace repo and a separate product code repo
- multiple agent tools may operate in the same workspace
- you want Graph, Context / Task Pack, Reviewed Knowledge, or llmwiki capabilities on a stable workspace contract

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
|   |-- knowledge/
|   `-- archive/
`-- repos/
```

## Document map

- **Operating contract**: `AGENTS.md`
- **Task system guide**: `docs/README.md`
- **JSON output contract**: `docs/contracts/repoctl-json-contract.md`
- **repoctl module boundaries**: `docs/contracts/repoctl-module-boundaries.md`
- **Context query contract**: `docs/contracts/repoctl-context-contract.md`
- **Repo metadata rules**: `docs/workflows/repo-metadata.md`
- **Root template PRD / adopter workspace context**: `docs/PRD.md`
- **Optional repo map**: `docs/REPOS.md`
- **Reviewed Knowledge state**: `docs/knowledge/records/`, `docs/knowledge/events/`

## Notes

- `repos/` is the product code repository boundary.
- `docs/BOARD.md` is a live-task registry, not a status dashboard.
- Task state lives in task frontmatter, not in the board.
- Backlog items are raw planning blocks; agents read them and pass explicit task fields rather than relying on repoctl to parse intent.
- `.repometa` provides file-level discovery and changed-file metadata gates; `repoctl index code` extracts read-only technical facts, and neither is a generated graph.
- `repoctl graph build` emits a read-only deterministic snapshot over repository files, raw/resolved imports, repo-local topics, task completion receipts, and precise provider symbols/calls.
- `repoctl graph query` supports exact file/topic/import lookup plus symbol, callers, callees, and bounded file/symbol impact queries.
- `repoctl context query` groups source documents, Graph evidence, task receipts, and reviewed knowledge into actionable JSON or Markdown evidence bundles.
- Generated llmwiki pages are non-authoritative views; records/events and original source refs remain the authority.
- MCP, if ever added, should be transport over repoctl contracts, not a second mutation path.
