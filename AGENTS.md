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

Run `./scripts/repoctl task resume --json` at session start and after compaction.

- `no_live` resumes nothing.
- `single_live` selects the only live task.
- `ambiguous` requires read-only selection with `./scripts/repoctl task resume <TASK_ID> --json`.
- Only a non-null `executable_handoff` with `status: current` is an execution instruction.
- Board rows, task history, archived Handoffs, and `readable_handoff` are inspection evidence only.
- Handoff freshness and lifecycle health are independent. A current Handoff is not executable while lifecycle health is unhealthy.

Read only what the work needs, using this order when applicable. Resume already identifies live work; open the Board only for registry context, and Task/parent/Context Docs only for the selected work. A known small edit needs its affected source, applicable rules and relevant check, not a fresh project-wide tour or unrelated PRD/workflows.

1. `AGENTS.md`
2. `docs/BOARD.md`
3. Assigned task file
4. Parent task when `parent` is set
5. Files under the task's `## Context Docs`
6. `docs/PRD.md`, or the relevant authority under `docs/prd/`
7. `docs/workflows/INDEX.md` for a reusable, repeated, or high-risk procedure

When no task is live, create one only for current product work. Never select archived history as live work. Use a root-only parent only to coordinate independently verifiable repo-scoped children.

For project orientation, confirm the selected Git root from `docs/repoctl.json` or `repo list`, then read its README and existing AGENTS/CONTRIBUTING guidance. Read nested AGENTS guidance on the path to the files you will touch; do not pre-read unrelated descendants. Start project requirements at `docs/PRD.md`; when it is an index, follow only the links relevant to the current request and its stated active authority. Retrieval rank, historical tasks, and the template README do not establish the active product specification. Resolve conflicting versions from current authority before editing.

Find implementation and dependencies in the selected repository's source, manifest, and relevant tests. Obtain check commands and their working directory from contribution guidance, manifest scripts, or CI; Context verification hints are suggestions to review, not an execution plan. Confirm runtime prerequisites and acceptance evidence separately (for example hardware, credentials, approved input data, or an existing worker). Use exact reads for known paths; neither a Graph build nor a Context Pack is required to understand a small change or run its checks.

## Product Work Loop

1. Explore without changing product files. Use compact `context query` for ambiguous intent, Git status/diff for a changed-set review, Graph or direct reads for a known file, `rg` for an exact identity, and explicit history or Knowledge queries for prior decisions.
2. Create or resume the product task and run `task start` before the first product mutation. Read-only exploration is not gated on task creation or start.
3. Once edit scope is concrete, record the canonical workspace-relative files in the Task's one Chosen set. Add a short Note only when the scope decision or source reference will help later.
4. Edit the smallest coherent scope and check observable behavior as ordinary development work.
5. Finish through repoctl when actual product changes fit Chosen scope and required repository metadata is valid. `## Verification` is optional free-form work notes; repoctl does not register, grade, hash, or require test results.

Context and Graph are independent read-only entry points; neither is a mandatory precursor to the other.

- Use top-level `graph_seed_refs` as ranked, source-bound continuation inputs after inspecting their source.
- Use `completeness.graph_anchor.seed_anchors` only to interpret coverage and provenance.
- `exact_identity`, `provider_symbol`, and `reviewed_knowledge` are source-bound anchors; `lexical_file` is a ranked hypothesis.
- `resolved` proves that a typed Graph path exists; it does not prove ownership, authority, or edit scope.
- Follow typed imports, calls, tests, task/document relations, or impact paths only when they answer the current question.
- Do not restart a coherent Context result with another broad repository search. Use narrow confirmation, a refined query, or an explicit Graph refresh when evidence is ambiguous, missing, or stale.
- Do not record searches, opened files, tool choices, or query results merely to prove that discovery happened.

`graph query` reads the last materialized snapshot and never rebuilds automatically. Build or rebuild explicitly when required. With a valid snapshot, Context uses the persistent evidence index and overlays changed or stale paths. Without a valid Graph, Context may still return lexical source, documents, tasks, and Knowledge while marking Graph relations unavailable.

A Context Pack is an optional one-time view or export of current task material. It never defines scope, authority, Handoff freshness, or completion readiness. Creating or exporting a Pack does not bind it to the Task.

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

The task-start repository selection is immutable. A `todo` task may record Chosen before start; a `doing` or `blocked` task may change it only with a current start baseline for the same repository. Every Chosen value must be a canonical workspace-relative path. Replace an existing set through `task discovery add --replace-chosen ... --reason ...`. Retired Discovery fields and outcome files are inactive. Preserve old Task prose and logs; the next scoped update writes the current Chosen/Notes form.

Pre-existing dirty product files remain outside task ownership. Finish and cancel preserve them or require explicit ownership resolution. A decomposition warning is advisory only; repoctl never infers semantic independence, splits a task, or rewrites scope automatically.

## Handoff and Work Notes

Every task contains a four-field Handoff:

- **Next exact step**
- **First file to open**: an existing workspace file while the task is live
- **First command to run**: inert text that repoctl never parses or executes
- **Done when**

Generated Handoff text contains exactly one `<!-- repoctl: generated-handoff -->` marker and is an inactive placeholder. Replace the fields with task-specific restart instructions, remove the marker, and run `task handoff bind` before a pause or transfer. Repoctl never infers authorship from prose.

A binding records review of those four fields, current task inputs, child state, and observed repository state. Later changes make it `inactive` until reviewed and rebound. Public freshness is `current | inactive | historical`; only `current` is an execution instruction. Historical `Last Active Handoff` content is not revalidated after completion.

Keep the Execution Log short and append-only through `task log append`. Use `## Verification` only for commands or results worth retaining for a future reader. Missing notes, an unrun check, wording, or result status never blocks `task finish`.

Preserve a long external request or review once, in the Task or an existing workspace source file, and reference its exact path/heading and requestRef when available. Keep the current outcome and effective decisions near Goal or Shared Interfaces / Decisions; identify which earlier instructions an amendment supersedes and which constraints remain in force. Handoff points to the current next step and those sources. Do not recopy full requests into Goal, log, Verification, and Handoff, trim meaningful events to meet a quota, or rewrite completed records. These notes do not replace Chosen, lifecycle frontmatter, or a current Handoff binding.

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
