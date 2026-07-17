# AGENTS.md

Canonical operating rules for this workspace. Tool-specific adapters (`CLAUDE.md`, `.agents/rules/`, `.cursor/rules/`, `.codex/`) must defer here.

## Workspace Contract

- Root is the private workspace repo for agent operations, tasks, PRD, workflows, and repoctl tooling.
- `repos/` is the product code repo boundary. Each product repo must have its own `.git`; root `.git` may be absent or unusable, so run product `git` commands inside `repos/` or `repos/<repo-id>/`.
- Root `.gitignore` must ignore `/repos/`.
- Code work defaults to the selected product repo (`repos/`); root `tools/`, root `tests`, and `scripts/` are workspace/repoctl surfaces only.
- Ambiguous product requests such as “add search”, “fix list”, or “improve the CLI” belong in the selected product repo unless repoctl/workspace tooling is explicitly named.
- Submodules are not used.

## Read Order

1. `AGENTS.md`
2. `docs/BOARD.md`
3. Assigned task file in `docs/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
4. Parent task file, if the task frontmatter `parent` is non-empty
5. Only the docs listed in the task `## Context Docs`
6. `docs/PRD.md` when shared project context is needed
7. `docs/workflows/INDEX.md` only when a reusable/high-risk/repeated procedure may apply

For repo-scoped implementation tasks, use this order before editing product files:

1. Start the task.
2. Record the explicit Candidate query in `## Discovery`.
3. Run compact `repoctl context query` to find likely product files and tests.
4. Inspect those files read-only; use `repoctl graph query --file/--impact-file` and returned typed continuation actions to follow imports, callers, callees, adjacent files, completion tasks/artifacts, and linked source documents when needed. Refine the Context query when evidence is still insufficient.
5. Record Reviewed files and the active Chosen files.
6. Edit and verify. Generate a scoped Context Pack only when a durable handoff or relationship summary is useful.

```bash
./scripts/repoctl task discovery add T-... --query "<query>" --json
./scripts/repoctl context query "<query>" --repo-id main --json
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl task discovery add T-... --reviewed repos/path --chosen repos/path --json
./scripts/repoctl context pack --task T-... --repo-id main --format markdown --output .repoctl-state/context-pack/T-....md
```

Context Pack is optional read-only evidence and never defines task scope. If generated before Chosen files exist, refresh it after Discovery before relying on its scoped view.

`graph query` reads the last materialized snapshot and never rebuilds automatically. Its default response reports freshness counts; use `--full` when exact stale paths or raw relations are required. Run `graph build` explicitly when no snapshot exists or after changes that must be reflected in Graph traversal. With a snapshot, `context query` uses the persistent evidence index and reads only stale path overlays. Without a snapshot, it still returns lexical source/document evidence and marks Graph relations unavailable.

If no active task is assigned:

- Resume a live task from `docs/BOARD.md` if one exists.
- For product work under `repos/`, create a live task with `./scripts/repoctl task create ...`.
- For read-only questions/status checks, do not create a task.
- Use a root-only parent task only for coordination across multiple independently verifiable repo-scoped child tasks.

Scope matrix:

| Request scope | Task? | Boundary |
|---|---:|---|
| Product changes under `repos/` | Yes | `task start` -> edit/verify -> `task finish` |
| Backlog item promoted for implementation | Yes | `backlog show` -> explicit `task create --backlog-id` |
| Workspace/control-plane changes outside `repos/` | No | Write directly unless the user explicitly asks for a task |
| Read-only questions or inspections | No | Report findings without Board mutation |

## Explicit-Only Maintenance Workflow

- Never infer or automatically select `/maintenance-workflow` from words such as maintenance, readiness, repo-level, audit, review, cleanup, or hardening.
- Enter `/maintenance-workflow` only when the current user message literally invokes `/maintenance-workflow`, optionally followed by its focus arguments.
- Product development, product readiness, QA, review, and bug fixing under `repos/**` always use the normal repo-scoped task lifecycle, even when the request describes the work as repository maintenance.
- Workspace or repoctl changes outside `repos/**` follow the scope matrix directly by default; naming repoctl, tooling, tests, docs, or adapters does not implicitly invoke the maintenance harness.
- If the maintenance skill is opened without the explicit command, do not initialize or resume harness state. Return to this contract and follow the normal scope route.

## Backlog

- Backlog is for deferred ideas or planned work that should not be executed yet; work requested for now uses a task.
- Manage Backlog only through `./scripts/repoctl backlog add/list/show/remove`.
- To promote a Backlog item, `list` and `show` it first, read enough repo context, then run `./scripts/repoctl task create --backlog-id BL-...` with explicit `--slug`, `--area`, and title; pass `--repo-id` for product-repo work in configured multi-repo workspaces.
- `repoctl` must not parse Backlog or PRD prose into task scope, files, validation, area, repo metadata, or annotations.

## Task Rules

- Live tasks live under `docs/tasks/`; standalone done/canceled tasks move to `docs/archive/tasks/`.
- Filenames use `T-YYYYMMDDHHMMSSZ--english-kebab-slug.md`; non-ASCII titles require explicit `--slug`.
- Frontmatter `status` accepts only `todo`, `doing`, `blocked`, `done`, `canceled`.
- Task frontmatter is authoritative. Board rows are a live registry only; do not update Board rows for owner/status changes.
- Parent-child authority: child `parent` frontmatter is source of truth; parent child lists are convenience summaries.
- `owner` and `depends_on` are informational metadata, not locks/enforcement.
- Worker agents must not set lifecycle-managed fields such as `status: done`; use `repoctl task finish`.
- Humans and agents write task meaning: Goal, Discovery, Execution Log, Verification, and Handoff. Do not hand-edit `.repoctl-state` baselines, fingerprints, ownership decisions, completion receipts, or archive metadata; repoctl owns those machine records.

## repoctl Boundary

- `repoctl` is the canonical mutation boundary for Board, Backlog, task creation, task lifecycle, archive transitions, and `.repometa` validation.
- Task/Board writes must hold `docs/tasks/.repoctl.lock.d` and use atomic writes.
- Do not keep separate task creation wrappers; use `./scripts/repoctl task create`.
- Use `./scripts/repoctl task show T-... --summary --json` for compact task inspection, omit `--summary` when the full task body is needed, and use `./scripts/repoctl task log append T-... "message" --json` to append timestamped execution log entries.
- When `## Verification` is complete, finish directly with `./scripts/repoctl task finish T-... --json`. Use `--verification-file` only for an external artifact.
- Prefer finishing before committing product repo changes. If product changes were already committed after task start, use `--use-committed-diff`. This mode is allowed only when the recorded start HEAD is an ancestor of the current HEAD and no task-new working-tree changes remain.
- Use `./scripts/repoctl task doctor T-... --use-committed-diff --json` to preflight that same committed-range path before finish.
- A committed range is observed Git evidence, not proof that its commits belong to the task. repoctl does not manage commit, push, PR, deploy, or delivery ownership.
- Use `./scripts/repoctl task block T-... --json` after recording the blocker in `## Verification`, or pass `--verification-file` for an external blocker artifact.

## Working Commands

- Code search: `cd <selected-product-repo> && rg ...` (`repos/` for direct layout, `repos/<repo-id>/` for collection layout)
- Code Git: `cd <selected-product-repo> && git ...`
- Docker: `cd <selected-product-repo> && docker compose ...`
- Root checks: `./scripts/repoctl check --json`
- Changed metadata gate: `./scripts/repoctl meta check --changed --json`
- Status fallback: `rg "^status:" docs/tasks/T-*.md` and `cat docs/BOARD.md`

Root-level automation under `scripts/` must resolve the workspace root from the script location, not `git rev-parse`, because product repos are separate repositories.

## Task Sections

- Every task must include `## Handoff`; it should let the next agent restart in about 30 seconds.
- Handoff fields: **Next exact step**, **First file to open**, **First command to run**, **Done when**.
- For a live task, **First file to open** must resolve to an existing file inside the workspace. Historical `## Last Active Handoff` entries are not revalidated after completion. **First command to run** is stored as text and must not be parsed, classified, or executed by repoctl.
- `## Execution Log` is append-only, short, and uses real UTC timestamps. Prefer `repoctl task log append` over hand-written timestamps.
- `## Verification` records commands, evidence, and results. Worker inability to run a gate is evidence, not final verification; final gates are the manager/Codex responsibility.
- Keep `## Handoff` aligned with the latest meaningful execution log before stopping.

## Archive/Follow-Up

- Standalone done/canceled tasks are archived immediately and removed from Board.
- Done/canceled child tasks leave Board but may remain in `docs/tasks/` until the parent archives.
- Parent tasks archive only after live children are done, canceled, or re-parented.
- Done/canceled tasks and their completion receipts are immutable.
- Additional work uses a new task: `./scripts/repoctl task create --follow-up-of T-old --slug ... "Follow-up title" --json`. The new task gets a new baseline; the old task is not moved or rewritten.
- After finish, create a Knowledge candidate only when the task established a reusable cross-task decision, invariant, or failure mode. Task-derived candidates require an explicit `--claim` or `--claim-file`; the completion receipt supplies provenance, not the reusable claim. Preview with `knowledge candidate suggest --from-task ... --kind ... --claim ... --dry-run`, then use candidate show/check before explicit approval. Never hand-edit candidate JSON. Routine implementation details should remain in task history rather than durable Knowledge.

## Documentation Language

- Public templates in this repository are English.
- Live task files, execution logs, and project-specific workflow docs in adopting workspaces may use the team language, e.g. Korean when `docs/repoctl.json` sets `document_language: "ko"`.
- Keep code, filenames, commands, identifiers, API names, logs, external quotes, and `.repometa` field keys/values in English.

## Workflows

- Create/update workflow docs only for reusable, high-risk, or repeated procedures.
- Keep one-off task-local instructions in the task file.
- Task isolation is required for parallel work: no shared files, generated boundaries, or interface boundaries without coordination.

## Product Repo Metadata

- `<product-repo>/.repometa/*` is the canonical sparse file-level metadata store for the selected product repo; inline `@meta` or source-file metadata frontmatter is forbidden residue.
- Full schema and operations live in `docs/workflows/repo-metadata.md`.
- Use `repoctl meta ...`; do not directly edit `.repometa` in normal work.
- `repoctl meta query` and `repoctl meta suggest` are read-only discovery hints. Inspect files directly before choosing scope.
- Repo-scoped live tasks should fill `## Discovery` with structured candidate query, reviewed files, and chosen files. Prefer `./scripts/repoctl task discovery add ...`; hand-written prose is not enough unless it uses the exact structured fields.
- When a task changes a product repo, `repoctl task finish` runs the changed-file metadata gate. If `repos/` exists but its git repository is missing/unusable, finish blocks.
- If a task started with pre-existing dirty product repo state, finish separates baseline dirty files from task-new changes; pre-existing dirty state is not task scope.

## Adapter Policy

- `AGENTS.md` is the shared contract and single source of truth.
- Adapter files are thin shims and must not duplicate or contradict these rules.
- Generated agent files under `.claude/agents/` or `.codex/agents/` come from `ai/roles/`; update role sources and re-render instead of editing generated outputs.
- Reusable skills live in `.agents/skills/` as canonical source and mirror only when required by a tool.
