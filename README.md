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
| Knowledge/memory systems | Long-term agent knowledge | Reviewed Knowledge and llmwiki keep durable records separate from generated views |

## Use this when

- handoff quality matters more than chat/session history
- you want task files to be the execution unit
- you need a private workspace repo and a separate product code repo
- multiple agent tools may operate in the same workspace
- you want Graph, Context / Task Pack, Reviewed Knowledge, or llmwiki capabilities on a stable workspace contract

## Prerequisites

- Bash
- Git
- Python 3.11 or newer
- Node.js when TypeScript/JavaScript semantic Graph analysis or the complete integration suite is required; without it, repoctl reports that optional provider as unavailable and continues with the remaining evidence

## Upgrading from v0.9.0 through v0.10.2

v0.10.3 includes the v0.10.1 history, diagnostic, and metadata hardening, accepts terminal child tasks retained under `docs/tasks/`, and preserves compatibility with v0.9.0 task-level verification receipts. Upgrade planning rejects managed-content drift when the adopter already reports v0.10.3; use a newer release instead of forcing a same-version replacement. Context consumers migrating directly from v0.8.0 must also follow the [v0.9.0 receipt migration](docs/contracts/repoctl-json-contract.md#v090-context-receipt-migration).

## Fresh adoption

Run these commands from the workspace root. For one product repository, clone it directly into `repos/`; `repos/` itself is the product Git root and receives the reserved repo ID `main`.

1. Clone the product repository and confirm its identity:
   ```bash
   git clone <product-repo-url> repos
   ./scripts/repoctl repo list --json
   ```
2. Initialize the repo-local metadata store:
   ```bash
   ./scripts/repoctl meta init --repo-id main --json
   ```
3. Review `repos/.repometa/policy.json`. Apply deliberate project-specific policy changes as described in the metadata workflow, use `repoctl meta` for annotations and exclusions (which create shards on demand), then validate and commit the store in the product repository:
   ```bash
   ./scripts/repoctl meta check --repo-id main --json
   cd repos
   git add .repometa
   git diff --cached -- .repometa
   git commit -m "Initialize repoctl metadata"
   cd ..
   ```
4. Initialize the empty Reviewed Knowledge projection:
   ```bash
   ./scripts/repoctl knowledge rebuild --repo-id main --json
   ```
5. Materialize the initial Graph:
   ```bash
   ./scripts/repoctl graph build --repo-id main --json
   ```
6. Create and start the first product task. A single configured repository is selected automatically:
   ```bash
   ./scripts/repoctl task create --start --json "First product change"
   ```

For a collection layout, place each product Git root at `repos/<name>/`, run `./scripts/repoctl repo adopt --all --json`, and replace `main` with the pinned repo ID in later commands.

## Resume an existing workspace

1. Read `AGENTS.md`
2. Run `./scripts/repoctl task resume --json`
3. Open the selected live task and `docs/BOARD.md` when inspection is needed
4. Continue only from a non-null `executable_handoff`; otherwise inspect the typed selection or drift before acting

`resume_guidance.status` answers whether the bound Handoff is current. `resume_guidance.health` separately reports whether repository lifecycle evidence is healthy; a current Handoff can coexist with unhealthy lifecycle state. In that case `readable_handoff` remains available for inspection, `blocked_by_health` is true, and `executable_handoff` is null.

When the result is `ambiguous`, select a returned live candidate with `./scripts/repoctl task resume <TASK_ID> --json`. Selection is read-only and does not create a persistent current-task pointer.

Repoctl-generated Handoffs carry one explicit marker and remain non-executable until their four fields are replaced, the marker is removed, and the Handoff is explicitly bound. Legacy unmarked Handoffs likewise require one fresh bind; legacy v3 receipts remain readable but inactive until replaced by a v4 binding. Repository configuration failures still preserve the selected `single_live` task and appear as typed unhealthy lifecycle evidence.

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
- **Discovery outcome contract**: `docs/contracts/repoctl-discovery-outcome-contract.md`
- **Graph snapshot and traversal contract**: `docs/contracts/repoctl-graph-contract.md`
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
- `repoctl graph build` materializes a deterministic snapshot plus a persistent source/symbol/document evidence index; later builds reuse unchanged Code Index and provider results and update only changed semantic units.
- `repoctl graph query` reads that snapshot without rebuilding, reports exact freshness, and supports file/topic/import/symbol/call/impact traversal. Explicit task and completion-artifact selectors validate one cold catalogue record and build a query-local projection. Query-visible files and confirmed relations carry set-aware component membership and crossings only when registered manifest providers find valid project declarations.
- `repoctl context query` is the integrated discovery view for ambiguous repository work. It returns a bounded evidence hypothesis with typed follow-up actions; an explicit current-diff review starts from Git's changed set, while narrow `rg`, explicit Graph queries, and direct reads remain available for known identities. Its `project_knowledge` summary reports documents, task history, and reviewed reusable records as separate lanes; zero reviewed records never means the document knowledge base is empty. Explicit `past_decision` and `failure_mode` modes isolate bounded cold-history matches in `related_history`; those matches cannot affect the already determined current working set or Graph seeds.
- Discovery records explicit Reviewed, Excluded, Chosen, result-member, and structured verification evidence; root-only tasks may also bind checks to existing non-product workspace artifacts without inventing product Discovery or Chosen scope. A `todo` task may record Discovery before start, but `doing`/`blocked` outcome mutations and every verification record require a current immutable start scope. When outcome state exists, repoctl requires its `active_chosen` identities to match every canonical explicit value in the Task's structured Chosen projection; invalid explicit values are errors, not absent data. Task finish freezes the result into the completion receipt and publishes bounded catalogue ingress. Ordinary Context joins independently retrieved current candidates to that freshness-checked hot frontier, while ordinary Graph build and freshness checks use the catalogue head/checkpoint and committed hot projection instead of scanning the completion archive or retained event sidecars. Cold history and workspace artifacts never define current candidates, ranking, Graph seeds, relations, component membership, or task scope.
- Live task views expose a decomposition advisory only when large Chosen projection, repeated Discovery episodes, and multiple structured verification records coincide. It asks the agent to review the next milestone boundary without inferring semantics or mutating the task.
- Generated llmwiki pages are non-authoritative views; records/events and original source refs remain the authority.
- MCP, if ever added, should be transport over repoctl contracts, not a second mutation path.
