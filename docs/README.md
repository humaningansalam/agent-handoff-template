# Workspace Control Docs

Root `docs/**` contains the workspace task/control ledger, repoctl contracts/workflows, and root template PRD/context.

## What Lives Here

- `BOARD.md` - live task registry only; task frontmatter remains authoritative.
- `tasks/` - live task files plus creation templates; completed tasks are immutable.
- `archive/tasks/` - non-live task originals after completion or cancellation.
- `contracts/` - machine-facing repoctl JSON, Graph, Context, debug, and module-boundary contracts.
- `adr/` - empty adopter ADR slot; the template ships only `.gitkeep`.
- `workflows/` - reusable workspace operating procedures.
- `knowledge/records/` - approved Reviewed Knowledge records owned by the adopter workspace.
- `knowledge/events/` - append-only Reviewed Knowledge lifecycle events.
- `knowledge/generated/` - non-authoritative llmwiki render output.
- `PRD.md` - root template PRD; after copying, adopters may delete it or replace it with private workspace PRD/context.
- `REPOS.md` - optional adopter repo map when multiple product repositories need stable labels.

Root docs are adopter-owned private workspace docs. Keep reusable workspace contracts and workflows here. Keep product-repo-local public-safe documentation inside the relevant product repo under `repos/**` when appropriate. Large private PRD/context may be split under root `docs/prd/`.

## Common Commands

- Create and start a product task in a single-repo workspace: `./scripts/repoctl task create --start --json "Task title"`
- Create parent task: `./scripts/repoctl task create --type parent "Parent title"`
- List tasks: `./scripts/repoctl task list --json`
- Show task: `./scripts/repoctl task show T-... --json`
- Bind a reviewed pause/transfer Handoff: `./scripts/repoctl task handoff bind T-... --json`
- Bind an optional reviewed Context Pack with it: `./scripts/repoctl task handoff bind T-... --context-pack .repoctl-state/context-pack/T-....md --json`
- Append log: `./scripts/repoctl task log append T-... "message" --json`
- Record an explicitly rejected Discovery file: `./scripts/repoctl task discovery add T-... --reviewed repos/decoy --excluded repos/decoy --json`
- Bind a structured check outcome to a recorded subject: `./scripts/repoctl task verification add T-... --status passed --evidence-ref verification.txt --subject repos/path --json`
- Block with transition intent: `./scripts/repoctl task block T-... --reason "waiting for upstream API" --json`
- Cancel with transition intent: `./scripts/repoctl task cancel T-... --reason "superseded by T-new" --json`
- Finish task from completed `## Verification`: `./scripts/repoctl task finish T-... --json`
- Finish with an external verification artifact: `./scripts/repoctl task finish T-... --verification-file /tmp/T-...-verification.md --json`
- Finish and create a reusable Knowledge review candidate atomically: `./scripts/repoctl task finish T-... --knowledge-kind invariant --knowledge-claim "<reusable invariant>" --knowledge-applies-to src/owner.py --json`
- Finish after an accidental product commit: `./scripts/repoctl task finish T-... --use-committed-diff --json`
- Create follow-up work: `./scripts/repoctl task create --follow-up-of T-old --slug follow-up "Follow-up title" --json`
- Show version: `./scripts/repoctl version` or `./scripts/repoctl version --json`
- Check workspace: `./scripts/repoctl check --json`
- Enable local repoctl diagnostics: set `"debug_mode": true` in `docs/repoctl.json`; events append to `docs/tasks/.repoctl-state/debug/events.jsonl`.
- Summarize feature use and task selection evidence: `./scripts/repoctl debug summary --json`
- Audit every completion receipt, task artifact, and derived catalogue: `./scripts/repoctl check --audit-history --json`
- Rebuild workspace-scoped completion history: `./scripts/repoctl history rebuild --workspace --json`
- Check metadata: `./scripts/repoctl meta check --json`
- Build/update Graph: `./scripts/repoctl graph build --repo-id main --json`
- Query materialized Graph: `./scripts/repoctl graph query --repo-id main --file path --json`
- Follow completion evidence: `./scripts/repoctl graph query --repo-id main --task T-... --json` or `--artifact docs/archive/tasks/T-...md`
- Query Context compact JSON: `./scripts/repoctl context query "question" --repo-id main --json`
- Query Context raw/debug JSON: `./scripts/repoctl context query "question" --repo-id main --full --json`
- Pack task context compact JSON: `./scripts/repoctl context pack --task T-... --repo-id main --json`
- Build knowledge candidate: `./scripts/repoctl knowledge candidate build --source docs/adr/example.md --repo-id main --kind decision --claim '<reusable claim>' --json`
- Preview a knowledge candidate from an already-finished task receipt: `./scripts/repoctl knowledge candidate build --from-task T-... --repo-id main --kind invariant --claim "<reusable invariant>" --dry-run --json`. Normal closeout uses the `task finish --knowledge-*` flags. The task receipt supplies provenance, while `--claim` or `--claim-file` supplies the reusable statement.
- Approve knowledge and synchronize Graph: `./scripts/repoctl knowledge approve KC-... --repo-id main --reviewed-by <label> --note-file /tmp/review.md --json`
- Query reviewed knowledge compact JSON: `./scripts/repoctl knowledge query "question" --repo-id main --json`
- Inspect a full reviewed record: `./scripts/repoctl knowledge show K-... --repo-id main --json` or add `--full` to the query.
- Render llmwiki view: `./scripts/repoctl knowledge render --repo-id main --json`
- Check llmwiki view: `./scripts/repoctl knowledge render --repo-id main --check --json`

## Visible Command Reference

Run any listed leaf with `--help` for its inputs. Context benchmark/materialization commands are internal field-gate diagnostics and are intentionally absent from normal help.

| Surface | Visible command leaves |
|---|---|
| Root | `version`, `check`, `debug summary` |
| Field gates | `field-gate run`, `field-gate compare` |
| Repositories | `repo list`, `repo show`, `repo check`, `repo adopt` |
| Tasks | `task create`, `task list`, `task resume`, `task show`, `task doctor`, `task log append`, `task handoff bind`, `task discovery add`, `task verification add`, `task baseline resolve`, `task start`, `task finish`, `task block`, `task cancel` |
| Backlog | `backlog add`, `backlog list`, `backlog show`, `backlog remove` |
| Metadata | `meta init`, `meta check`, `meta status`, `meta show`, `meta query`, `meta suggest`, `meta set`, `meta remove`, `meta move`, `meta exclude` |
| Evidence | `index code`, `graph build`, `graph query`, `history rebuild`, `context query`, `context pack` |
| Knowledge | `knowledge candidate build`, `knowledge candidate list`, `knowledge candidate show`, `knowledge candidate check`, `knowledge candidate refresh`, `knowledge rebuild`, `knowledge status`, `knowledge event list`, `knowledge event show`, `knowledge approve`, `knowledge show`, `knowledge reject`, `knowledge deprecate`, `knowledge check`, `knowledge query`, `knowledge render` |
| Upgrade | `upgrade status`, `upgrade plan`, `upgrade postflight`, `upgrade apply` |

## Boundaries

- `repos/` is the product repository boundary and is ignored by the root repo.
- Root `docs/**` controls workspace operations and may hold the adopter-owned private root PRD/context.
- Graph, Context / Task Pack, Reviewed Knowledge, and llmwiki are shipped repoctl capabilities.
- Debug mode records bounded repoctl feature activity automatically but remains ignored, non-authoritative state.
- Generated llmwiki pages must not be re-ingested as factual authority. Use records/events and original source refs instead.
