# AGENTS.md

Canonical operating rules for this workspace. Tool adapters defer here and must not duplicate or contradict policy.

## Workspace and Repository Boundary

- Root owns agent operations, tasks, PRD, workflows, and repoctl tooling. Root `tools/`, `tests/`, and `scripts/` are control-plane surfaces.
- `repos/` is the product boundary. Each product repository has its own `.git`; root Git may be absent or unusable. Run product Git, build, and test commands inside `repos/` or `repos/<repo-id>/`.
- Root `.gitignore` must ignore `/repos/`; submodules are not used.
- Ambiguous product requests target the selected product repository unless workspace or repoctl tooling is explicitly named.
- Root scripts resolve the workspace from their own location, never from `git rev-parse`.

| Request | Task | Boundary |
|---|---:|---|
| Product change under `repos/` | Yes | create/resume -> start -> edit/verify -> finish |
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

Read only what the work needs, in this order:

1. `AGENTS.md`
2. `docs/BOARD.md`
3. Assigned task file
4. Parent task when `parent` is set
5. Files under the task's `## Context Docs`
6. `docs/PRD.md`, or the relevant authority under `docs/prd/`
7. `docs/workflows/INDEX.md` for a reusable, repeated, or high-risk procedure

When no task is live, create one only for current product work. Never select archived history as live work. Use a root-only parent only to coordinate independently verifiable repo-scoped children.

## Product Discovery and Work Loop

1. Explore without changing product files or task scope. Use compact `context query` for ambiguous intent, Git status/diff for a changed-set review, Graph or direct reads for a known file, `rg` for an exact identity, and task history or Knowledge for prior decisions.
2. Create or resume the product task and run `task start` before the first product mutation. Read-only exploration is not gated on task creation or start.
3. Record the Candidate query and the files actually Reviewed and Chosen once scope is concrete. Record Excluded only for an explicit rejection; Reviewed minus Chosen is neutral.
4. Edit the smallest coherent scope and verify observable behavior.
5. Finish through repoctl only when Chosen scope, actual changes, metadata, and Verification agree.

Context and Graph are independent entry points; neither is a mandatory precursor to the other.

- Use top-level `graph_seed_refs` as ranked, source-bound continuation inputs after inspecting their source.
- Use `completeness.graph_anchor.seed_anchors` only to interpret coverage and provenance.
- `exact_identity`, `provider_symbol`, and `reviewed_knowledge` are explicit anchors; `lexical_file` is a ranked hypothesis.
- `resolved` proves that a typed Graph path exists; it does not prove ownership, authority, or edit scope.
- Follow typed imports, calls, tests, task/document relations, or impact paths only when they answer the current question.
- Do not restart a coherent Context result with another broad repository search. Use narrow confirmation, a refined query, or an explicit Graph refresh when evidence is ambiguous, missing, or stale.
- Do not require tool-choice logs or justification for skipped discovery features.

When selecting evidence from a result receipt, record the exact producer, result ID, authority, and member citation. For a Context member omitted from the compact projection, rerun the same query with `--full` and select an exact manifest member. Prior task outcomes are corroboration only; they never create current candidates, ownership, authority, Chosen scope, or a substitute for current evidence.

`graph query` reads the last materialized snapshot and never rebuilds automatically. Build or rebuild explicitly when required. With a valid snapshot, Context uses the persistent evidence index and overlays changed or stale paths rather than rescanning unchanged source. Without a valid Graph, Context may return lexical source, document, task, and Knowledge evidence while marking Graph relations unavailable.

A Context Pack is optional read-only handoff evidence and never defines scope or authority. If used, regenerate it after final Chosen scope and bind that exact artifact. `task start` and `task show` never create or refresh a Handoff binding.

## Backlog

Backlog is deferred work only. Manage it with `repoctl backlog add/list/show/remove`. Before promotion, list and show the item, then create a task with explicit slug, area, title, and repository selection. Repoctl must not derive implementation scope, files, metadata, or validation from Backlog or PRD prose.

## Task and Machine-State Invariants

- Live tasks are under `docs/tasks/`; standalone done or canceled tasks move to `docs/archive/tasks/`.
- Task filenames use `T-YYYYMMDDHHMMSSZ--english-kebab-slug.md`. Non-ASCII titles require an explicit English slug.
- Status is one of `todo`, `doing`, `blocked`, `done`, or `canceled`.
- Task frontmatter is authoritative. Board is only the live registry; do not use it as status authority.
- Child `parent` frontmatter is authoritative. Parent child lists are summaries; `owner` and `depends_on` are informational.
- Repoctl is the mutation boundary for Board, Backlog, task lifecycle/archive, and `.repometa`.
- Repoctl Task and Board writes must hold `docs/tasks/.repoctl.lock.d` and use atomic writes.
- Do not hand-edit lifecycle-managed frontmatter, baselines, fingerprints, ownership decisions, Handoff binding receipts, result receipts, completion receipts, catalogue state, or archive metadata.
- Humans and agents own Goal, Discovery, Execution Log, Verification, and Handoff meaning.

The task-start repository scope is immutable.

- A `todo` task may record Discovery before start.
- A `doing` or `blocked` task may mutate Discovery only with a current start baseline for the same repository scope.
- Every structured verification mutation, in every task status, requires current matching task-start evidence.
- A legacy live task without current start evidence must use a fresh follow-up task.

When machine Discovery state exists, its `active_chosen` identities and the Task's complete Chosen projection must agree. Every explicit Chosen value must be a canonical workspace-relative path; invalid values are lifecycle errors, not silently absent data. Reconcile approved scope through `task discovery add --replace-chosen ... --reason ...`, never by editing machine state.

Pre-existing dirty product files remain outside task ownership. Finish and cancel preserve them or require explicit ownership resolution.

A decomposition warning from `task show` or `task doctor` is advisory only. Repoctl never infers semantic independence, splits a task, or rewrites scope automatically.

## Handoff and Verification

Every task contains a four-field Handoff:

- **Next exact step**
- **First file to open**: an existing workspace file while the task is live
- **First command to run**: inert text that repoctl never parses or executes
- **Done when**

Generated Handoff text contains exactly one `<!-- repoctl: generated-handoff -->` marker and is an inactive placeholder. Replace the four fields with task-specific restart instructions and remove the marker before binding. Repoctl preserves a valid authored Handoff and rejects marked, malformed, or mismatched bindings; it never infers authorship from prose.

A Handoff binding records review of the exact four fields, structured task inputs, and observed repository state. Any later Handoff, task contract, Discovery, Execution Log, Verification, bound Context Pack, child lifecycle, or observed repository-state change makes it `inactive` until reviewed and rebound. Public Handoff freshness is only `current | inactive | historical`; only `current` is an execution instruction. Historical `Last Active Handoff` content is not revalidated after completion.

Keep the Execution Log short, append-only, and UTC-stamped through `task log append`. Verification records commands, evidence, and results. A Worker's inability to run a gate is evidence of an unrun gate, not passed verification. Before pause or transfer, align Handoff with the latest meaningful log and run `task handoff bind`.

`task verification add --artifact` is restricted to a currently started root-only task with current root-only start evidence. The artifact must be an existing canonical workspace-relative regular file outside `repos/**`. It records Reviewed evidence only and never creates product Discovery, Chosen scope, or ordinary product-outcome corroboration. Product verification uses `--subject`. Pre-start, unsupported legacy, reclassified product, absolute, missing, traversal, product, non-regular, and symlink-escape artifact inputs fail closed.

Block or cancel with explicit transition intent: `task block T-... --reason "..."` or `task cancel T-... --reason "..."`. Use `--reason-file` only when that intent already exists in a UTF-8 file. These transitions append the reason to Execution Log and never replace Verification.

## Finish, Committed Changes, and Archive

Finish directly after Verification is complete. Use `--verification-file` only for an external verification artifact.

Prefer finishing before committing product changes. If product changes were committed after task start, `--use-committed-diff` is allowed only when:

- the recorded start HEAD is an ancestor of the current HEAD;
- no task-new working-tree changes remain; and
- `task doctor T-... --use-committed-diff --json` passes the same committed-range preflight.

A committed range is observed Git evidence, not proof that every commit or path belongs to the task. Repoctl does not own commit, push, PR, deploy, or delivery. `task doctor` reports current Chosen-versus-diff drift as an advisory; `task finish` is the hard closure gate for unchosen changes.

Standalone done or canceled tasks archive immediately and leave Board. Completed children may remain live-path files until the parent closes. A parent archives only after every child is done, canceled, or re-parented. Completed tasks and completion receipts are immutable.

Additional work uses `task create --follow-up-of T-old ...`. The follow-up receives a new baseline; the old task and receipt are not moved, reopened, or rewritten.

## Reviewed Knowledge

Create durable Knowledge only for a reusable cross-task decision, invariant, or failure mode. Routine implementation detail stays in task history.

The normal closeout path is `task finish --knowledge-kind ...` with an explicit `--knowledge-claim` or `--knowledge-claim-file` and any literal applicability paths. Source documents, task artifacts, and completion receipts provide provenance; they never substitute for the reusable claim.

`knowledge candidate suggest --from-task ... --dry-run` is a preview for an already-finished task, not the normal closeout path. Candidate review and approval are explicit. Never hand-edit candidate, record, or event JSON. Approval creates the durable record and attempts Graph synchronization; if synchronization fails, keep the durable record and perform the reported typed rebuild action.

## Repository Metadata

- `<product-repo>/.repometa/*` is the canonical sparse file metadata store. Inline source metadata is invalid.
- Use `repoctl meta ...`; do not edit `.repometa` directly in normal work.
- `meta query` and `meta suggest` are discovery hints, not scope decisions; inspect the source before choosing it.
- Product task finish runs the changed-file metadata gate and requires a usable selected product Git repository.

## Documentation, Workflows, and Adapters

- Public templates are English. Adopting workspaces may use their configured team language for live tasks, logs, and project workflows.
- Keep code, paths, commands, identifiers, API names, logs, external quotes, and `.repometa` keys and values in English.
- Create workflow documents only for reusable, repeated, or high-risk procedures. Keep one-off instructions in the task.
- Parallel tasks must not share files, generated boundaries, or interface boundaries without coordination.
- `AGENTS.md` is the single policy source. Adapter files are thin shims and must not duplicate or contradict it.
