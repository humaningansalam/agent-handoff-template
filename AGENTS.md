# AGENTS.md

Canonical operating rules for this workspace. Tool adapters defer here and must not duplicate or contradict policy.

## Workspace and Repository Boundary

- Root owns agent operations, tasks, PRD, workflows, and repoctl tooling. Root `tools/`, `tests/`, and `scripts/` are control-plane surfaces.
- `repos/` is the product boundary. Each product repository has its own `.git`; root Git may be absent or unusable. Run product Git, build, and test commands inside `repos/` or `repos/<repo-id>/`.
- Root `.gitignore` must ignore `/repos/`; submodules are not used.
- Ambiguous product requests target the selected product repository unless workspace or repoctl tooling is explicitly named.
- Root scripts resolve the workspace from their own location, never from `git rev-parse`.
- Root-only work normally needs no Task. When explicitly tracking it, use `--area docs` or `--area ops` without `--repo-id` or `--repo-ref`. An unscoped `task create --start` defaults to the only configured product repository; `--area docs --repo-id main` still selects product work.

| Request | Task | Boundary |
|---|---:|---|
| Product change under `repos/` | Yes | create/resume -> start -> edit/check -> finish |
| Backlog implementation | Yes | show item -> create task with `--backlog-id` |
| Root control-plane change | No | edit directly unless the user asks for a task |
| Read-only inspection | No | report without Board mutation |

## Session Start and Read Order

At session start and after compaction, resume the known live task with `./scripts/repoctl task resume <TASK_ID> --json`. Use `./scripts/repoctl task resume --json` when no task is known.

- `no_live` resumes nothing.
- `single_live` selects the only live task.
- `ambiguous` requires read-only selection with `./scripts/repoctl task resume <TASK_ID> --json`.
- Read the known Task ID directly; `task resume <TASK_ID> --json` returns the current four restart fields and source links. Do not repeat `resume` after opening the same Task.

- Only a non-null `executable_handoff` with `status: current` is an execution instruction.
- Board rows, task history, archived Handoffs, and `readable_handoff` are inspection evidence only.
- Handoff freshness and lifecycle health are independent. A current Handoff is not executable while lifecycle health is unhealthy.

Read only the selected Task, its relevant parent/Context Docs and applicable PRD authority. Resume identifies live work; open the Board for registry context when needed. A known small edit needs affected source, applicable rules and a relevant check. Use `docs/workflows/INDEX.md` for a reusable, repeated, or high-risk procedure.

Agent roles, model or provider selection, team hierarchy, and external execution orchestration belong to the adopting environment, not this common workspace package.

When no task is live, create one only for current product work. Never select archived history as live work. Use a root-only parent only to coordinate independently verifiable repo-scoped children.

For project orientation, confirm the selected Git root from `docs/repoctl.json` or `repo list`, then read its README and existing AGENTS/CONTRIBUTING guidance. Read nested AGENTS guidance on the path to the files you will touch; do not pre-read unrelated descendants. Start project requirements at `docs/PRD.md`; when it is an index, follow only the links relevant to the current request and its stated active authority. Retrieval rank, historical tasks, and the template README do not establish the active product specification. Resolve conflicting versions from current authority before editing.

Find implementation and dependencies in the selected repository's source, manifest, and relevant tests. Obtain check commands and their working directory from contribution guidance, manifest scripts, or CI; Graph source hints are read-only evidence, not an execution plan. Confirm runtime prerequisites and acceptance evidence separately (for example hardware, credentials, approved input data, or an existing worker). Use exact reads for known paths; a Graph build is not required to understand a small change or run its checks.

For changes spanning agents, languages, or shared interfaces, read [Code Quality](docs/workflows/code-quality.md). Before writing or changing tests, read [잘 작성된 테스트 코드](docs/workflows/well-written-tests.md).

## Product Work Loop

1. Explore without changing product files. Use `graph search` for an unknown owner, `graph query` or `graph workflow` for typed relationships and known entrypoints, and `knowledge query` for durable decisions. Use direct reads for exact identities.

2. Create or resume the product task and run `task start` before the first product mutation. Read-only exploration is not gated on task creation or start.
3. Once edit scope is concrete, record the canonical workspace-relative files in the Task's one Chosen set. Add a short Note only when the scope decision or source reference will help later.
4. Edit the smallest coherent scope and check observable behavior as ordinary development work.
5. Finish through repoctl when actual product changes fit Chosen scope and required repository metadata is valid. `## Verification` is optional free-form work notes; repoctl does not register, grade, hash, or require test results.

Graph searches and Knowledge lookups are read-only aids, never scope or test proof. Inspect cited source before edits; confirmed Graph relations are distinct from lexical candidates. A stale or partial Graph cannot prove no impact; refresh explicitly as a separate action. Do not repeat broad searches when the next file is already known. Record only material scope decisions, blockers and source references in the Task.

When `docs/repoctl.json` sets `debug_mode` to `true`, repoctl records bounded local command diagnostics under `docs/tasks/.repoctl-state/debug/events.jsonl`. The ignored journal does not change Task, Chosen, Handoff, Board, Graph, Knowledge, metadata, or command results.

## Backlog

Backlog is deferred work only. Manage it with `repoctl backlog add/list/show/remove`. Before promotion, list and show the item, then create a task with explicit slug, area, title, and repository selection. Repoctl must not derive implementation scope, files, metadata, or checks from Backlog or PRD prose.

## Task and Machine-State Invariants

- Live tasks are under `docs/tasks/`; standalone done or canceled tasks move to `docs/archive/tasks/`.
- Task filenames use `T-YYYYMMDDHHMMSSZ--english-kebab-slug.md`. Non-ASCII titles require an explicit English slug.
- Status is one of `todo`, `doing`, `blocked`, `done`, or `canceled`.
- Task frontmatter is authoritative. Board is only the live registry.
- Child `parent` frontmatter is authoritative. Parent child lists are summaries; `owner` and `depends_on` are informational.
- Repoctl is the mutation boundary for Board, Backlog, task lifecycle/archive, and `.repometa`.
- Repoctl Task and Board writes must hold `docs/tasks/.repoctl.lock.d` and use atomic writes.
- Do not hand-edit lifecycle-managed frontmatter, start baselines, fingerprints, ownership decisions, Handoff bindings, completion receipts, catalogue state, or archive metadata.
- Humans and agents own Goal, Chosen and Notes meaning, Execution Log, optional Verification notes, and Handoff meaning.

The task-start repository selection is immutable. A `todo` task may record Chosen before start; a `doing` or `blocked` task may change it only with a current start baseline for the same repository. Every Chosen value must be a canonical workspace-relative path. Replace an existing set through `task discovery add --replace-chosen ... --reason ...`. Retired Discovery fields and outcome files are inactive. Use the current Chosen/Notes form for scoped updates.

Pre-existing dirty product files remain outside task ownership. Finish and cancel preserve them or require explicit ownership resolution. A decomposition warning is advisory only; repoctl never infers semantic independence, splits a task, or rewrites scope automatically.

## Handoff and Work Notes

Every task contains a four-field Handoff:

- **Next exact step**
- **First file to open**: an existing workspace file while the task is live
- **First command to run**: inert text that repoctl never parses or executes
- **Done when**

Generated Handoff text contains exactly one `<!-- repoctl: generated-handoff -->` marker and is an inactive placeholder. Replace the fields with task-specific restart instructions, remove the marker, and run `task handoff bind` before a pause or transfer. Repoctl never infers authorship from prose.

A binding records review of those four fields, current task inputs, child state, and observed repository state. Later changes make it `inactive` until reviewed and rebound. Public freshness is `current | inactive | historical`; only `current` is an execution instruction. Historical `Last Active Handoff` content is not revalidated after completion.

Keep Goal, current work, remaining acceptance criteria and Handoff easy to find. Update the Handoff when the next action changes; a log entry does not update it. Keep the Execution Log short through `task log append`; consolidate repetitive waits, connection checks and superseded operational status in live Tasks while retaining meaningful requirements, decisions, causes, results and source references. Use `## Verification` only for commands or results worth retaining for a future reader. Missing notes, an unrun check, wording, or result status never blocks `task finish`.

Keep only effective decisions, constraints, blockers, outcomes and next actions in the Task. Cite an existing source by its real path and heading when additional detail exists. Do not invent references or copy transient request IDs, external dispatch queues, acknowledgements, or orchestration logs into shared Tasks. Do not copy the same long input into Goal, Execution Log, Verification and Handoff. Historic Task cleanup uses an explicit audited one-time migration; do not hand-edit receipt-bound completion records.

Block or cancel with explicit transition intent: `task block T-... --reason "..."` or `task cancel T-... --reason "..."`. Use `--reason-file` only when that intent already exists in a UTF-8 file. These transitions append the reason to Execution Log and preserve Verification unchanged.

## Finish, Committed Changes, and Archive

Prefer finishing before committing product changes. If product changes were committed after task start, `--use-committed-diff` is allowed only when:

- the recorded start HEAD is an ancestor of the current HEAD;
- no task-new working-tree changes remain; and
- `task doctor T-... --use-committed-diff --json` passes the same committed-range preflight.

A committed range is observed Git history, not proof that every commit or path belongs to the task. Repoctl does not own commit, push, PR, deploy, or delivery. `task doctor` and `task finish` share the same hard closure preflight for changes outside Chosen scope.

Standalone done or canceled tasks archive immediately and leave Board. Completed children may remain under `docs/tasks/` until the parent closes. A parent archives only after every child is done, canceled, or re-parented. Completed tasks and completion receipts are immutable. Receipt hashes bind lifecycle history to its task artifact; they do not claim that a test ran or passed.

`task cancel` creates no completion receipt. A canceled child of a live parent stays under `docs/tasks/` and leaves Board; its canceled frontmatter and Closure are the cancellation evidence. Do not require an archive path or finish receipt for that child. Task completion/receipt, observed test results, product acceptance, and commit/push/deployment are separate evidence; report only what each source establishes. Finishing does not require binding a generated Handoff unless the work is paused or transferred first.

Additional work uses `task create --follow-up-of T-old ...`. The follow-up receives a new baseline; the old task and receipt are not reopened or rewritten.

## Knowledge

Save Knowledge only for a reusable cross-task decision, invariant, or failure mode. Routine implementation detail stays in task history.

- Use `knowledge add` with an explicit claim, reason, source, and repository. `applies_to`, `replaces`, and `author` are optional.
- Or pass the matching `--knowledge-*` inputs to `task finish` when the conclusion is already ready.
- Saving the record completes the action. New records have no candidate, approval, refresh, or render lifecycle.
- Source changes produce a warning while the saved decision remains queryable. Correct an old decision by saving a replacement that names it.
- Preserve older Knowledge files as migration input. Before relying on an older conclusion, read it and save its reusable claim, reason, and current sources as a current record. Queries never interpret candidate/event-era state.

## Repository Metadata

- `<product-repo>/.repometa/*` is the canonical sparse file metadata store. Inline source metadata is invalid.
- Use `repoctl meta ...`; do not edit `.repometa` directly in normal work.
- `meta query` and `meta suggest` are discovery hints; inspect source before choosing it.
- Product task finish runs the changed-file metadata gate and requires a usable selected product Git repository.

## Documentation, Workflows, and Adapters

- Public templates are English. Adopting workspaces may use their configured team language for live tasks, logs, and project workflows.
- Keep code, paths, commands, identifiers, API names, logs, external quotes, and `.repometa` keys and values in English.
- Create workflow documents only for reusable, repeated, or high-risk procedures. Keep one-off instructions in the task.
- Parallel tasks must not share files, generated boundaries, or interface boundaries without coordination.
- `AGENTS.md` is the single policy source. Adapter files are thin shims and must not duplicate or contradict it.
- Upgrade seeds a missing `AGENTS.md` and preserves an existing one; reconcile relevant upstream policy changes in this file when needed. Postflight does not validate policy prose.
