# Workspace Control Docs

Root `docs/**` contains the workspace task/control ledger, repoctl contracts/workflows, and root template PRD/context.

## What Lives Here

- `BOARD.md` - live task registry only; task frontmatter remains authoritative.
- `tasks/` - live task files plus creation templates; completed tasks are immutable.
- `archive/tasks/` - non-live task originals after completion or cancellation.
- `contracts/` - machine-facing repoctl JSON, Graph, Context, and module-boundary contracts.
- `adr/` - empty adopter ADR slot; the template ships only `.gitkeep`.
- `workflows/` - reusable workspace operating procedures.
- `knowledge/records/` - approved Reviewed Knowledge records owned by the adopter workspace.
- `knowledge/events/` - append-only Reviewed Knowledge lifecycle events.
- `knowledge/generated/` - non-authoritative llmwiki render output.
- `PRD.md` - root template PRD; after copying, adopters may delete it or replace it with private workspace PRD/context.
- `REPOS.md` - optional adopter repo map when multiple product repositories need stable labels.

Root docs are adopter-owned private workspace docs. Keep reusable workspace contracts and workflows here. Keep product-repo-local public-safe documentation inside the relevant product repo under `repos/**` when appropriate. Large private PRD/context may be split under root `docs/prd/`.

## Common Commands

- Create task: `./scripts/repoctl task create "Task title"`
- Create parent task: `./scripts/repoctl task create --type parent "Parent title"`
- List tasks: `./scripts/repoctl task list --json`
- Show task: `./scripts/repoctl task show T-... --json`
- Append log: `./scripts/repoctl task log append T-... "message" --json`
- Finish task from completed `## Verification`: `./scripts/repoctl task finish T-... --json`
- Finish with an external verification artifact: `./scripts/repoctl task finish T-... --verification-file /tmp/T-...-verification.md --json`
- Finish after an accidental product commit: `./scripts/repoctl task finish T-... --use-committed-diff --json`
- Create follow-up work: `./scripts/repoctl task create --follow-up-of T-old --slug follow-up "Follow-up title" --json`
- Show version: `./scripts/repoctl --version` or `./scripts/repoctl version --json`
- Check workspace: `./scripts/repoctl check --json`
- Check metadata: `./scripts/repoctl meta check --json`
- Build/update Graph: `./scripts/repoctl graph build --repo-id main --json`
- Query materialized Graph: `./scripts/repoctl graph query --repo-id main --file path --json`
- Follow completion evidence: `./scripts/repoctl graph query --repo-id main --task T-... --json` or `--artifact docs/archive/tasks/T-...md`
- Query Context compact JSON: `./scripts/repoctl context query "question" --repo-id main --json`
- Query Context raw/debug JSON: `./scripts/repoctl context query "question" --repo-id main --full --json`
- Pack task context compact JSON: `./scripts/repoctl context pack --task T-... --repo-id main --json`
- Build knowledge candidate: `./scripts/repoctl knowledge candidate build --source docs/adr/example.md --repo-id main --kind decision --json`
- Suggest knowledge candidate from a finished task receipt: `./scripts/repoctl knowledge candidate suggest --from-task T-... --repo-id main --kind invariant --json`. If the derived claim is not concise and complete, pass `--claim` or `--claim-file` instead of editing candidate state.
- Approve knowledge: `./scripts/repoctl knowledge approve KC-... --repo-id main --reviewed-by <label> --note-file /tmp/review.md --json`
- Query reviewed knowledge compact JSON: `./scripts/repoctl knowledge query "question" --repo-id main --json`
- Inspect a full reviewed record: `./scripts/repoctl knowledge show K-... --repo-id main --json` or add `--full` to the query.
- Render llmwiki view: `./scripts/repoctl knowledge render --repo-id main --json`
- Check llmwiki view: `./scripts/repoctl knowledge render --repo-id main --check --json`

## Boundaries

- `repos/` is the product repository boundary and is ignored by the root repo.
- Root `docs/**` controls workspace operations and may hold the adopter-owned private root PRD/context.
- Graph, Context / Task Pack, Reviewed Knowledge, and llmwiki are shipped repoctl capabilities.
- Generated llmwiki pages must not be re-ingested as factual authority. Use records/events and original source refs instead.
