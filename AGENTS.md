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
6. `docs/PRD.md` when shared project context is needed; if it is absent, read only the relevant authority document under `docs/prd/`
7. `docs/workflows/INDEX.md` only when a reusable/high-risk/repeated procedure may apply

At session start or after compaction, run `./scripts/repoctl task resume --json`. `no_live` means no task is resumed, `single_live` identifies the only candidate, and `ambiguous` requires explicit selection. Only its non-null `executable_handoff` is an execution instruction; Board and task-history prose remain inspection evidence. Handoff freshness and repository lifecycle health are independent: a current Handoff does not make an unhealthy task safe to continue without resolving the reported health problems.

For repo-scoped implementation tasks, use this order before editing product files:

1. Explore without changing product source or task scope using the entry point that matches the known evidence: ambiguous intent -> compact `repoctl context query` as the integrated discovery view; an explicit current-diff/review request -> Git status/diff as the changed-set authority, followed by Graph or direct reads for relationships and meaning; known file -> Graph or direct read; known exact string or symbol -> `rg`; past decision or failure mode -> task history or Knowledge.
2. Use top-level `graph_seed_refs` as ranked, source-bound Graph continuation inputs. Read `completeness.graph_anchor.seed_anchors` only to interpret coverage and provenance: `exact_identity`, `provider_symbol`, and `reviewed_knowledge` are explicit anchors, while `lexical_file` is a ranked hypothesis. Treat a coherent returned source/test/relation set as the initial working set after inspecting the source; `resolved` means the typed path exists in Graph, not that semantic ownership is proven. Follow typed Graph continuations repeatedly when imports, callers, callees, direct tests, related tasks/documents, or impact paths are useful. Context and Graph are independent entry points; neither is a mandatory precursor to the other.
3. Do not restart the same broad repository discovery with `rg` after Context already returned a coherent working set. Narrow `rg` checks and direct reads remain appropriate for confirmation; refine the query or refresh Graph when Context reports ambiguity, missing coverage, or stale evidence.
4. Create or resume the task and start it before the first product mutation. Exploration is not gated on task creation or task start.
5. Record the explicit Candidate query and the files actually reviewed/chosen in `## Discovery` once task scope becomes concrete. A reviewed file that is not Chosen remains neutral supporting evidence; record `--excluded` only for a file explicitly rejected for the active episode.
6. Edit and verify. Generate a scoped Context Pack only when a durable handoff or relationship summary is useful.

Do not require agents to log which discovery features they used or justify why a feature was skipped.

The completion-bound outcome contract is specified in `docs/contracts/repoctl-discovery-outcome-contract.md`. Discovery can record explicit exclusions and selected result members, and `task verification add` can bind structured check outcomes to recorded subjects or claims. Do not infer exclusion from Reviewed minus Chosen. Ordinary Context queries join only independently retrieved current candidates to the bounded, freshness-checked outcome frontier; prior outcomes are corroboration, never automatic scope, ownership, or a substitute for current evidence.

```bash
./scripts/repoctl task discovery add T-... --query "<query>" --json
./scripts/repoctl context query "<query>" --repo-id main --json
# Context: copy producer/result_id plus one exact `compact.representative_citations[*].primary_citation`; rerun with `--full` and use `manifest.items` only when selecting an omitted member. Graph receipts expose `selectable` directly:
./scripts/repoctl task discovery add T-... --result-producer context --result-id "sha256:..." --result-authority source --result-ref repos/path --json
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl task discovery add T-... --reviewed repos/path --chosen repos/path --json
./scripts/repoctl task discovery add T-... --reviewed repos/decoy --excluded repos/decoy --json
./scripts/repoctl task verification add T-... --status passed --evidence-ref verification.txt --subject repos/path --json
./scripts/repoctl task verification add T-... --status passed --evidence-ref verification.txt --artifact docs/reviews/review.md --json
./scripts/repoctl context pack --task T-... --repo-id main --format markdown --output .repoctl-state/context-pack/T-....md
./scripts/repoctl task handoff bind T-... --context-pack .repoctl-state/context-pack/T-....md --json
```

Context Pack is optional read-only evidence and never defines task scope. If generated before Chosen files exist, refresh it after Discovery before relying on its scoped view. Before pausing or transferring a live task, review the four Handoff fields and explicitly run `task handoff bind`; add `--context-pack` only when that exact Pack should be active resume evidence. `task start` and `task show` never bind automatically. Only `current` resume guidance is active; `unbound`, `stale`, `unknown`, and `historical` Handoffs remain readable but must not be treated as current instructions.

`task verification add --artifact` is only for a root-only workspace task with current root-only task-start evidence. It accepts an existing canonical workspace-relative regular file outside `repos/**`, records it as Reviewed evidence without adding Chosen scope, and never promotes it into the ordinary product outcome frontier. A todo task, a legacy live task without the current start-state schema, malformed state, or a product-start task later reclassified by frontmatter is rejected before outcome state is written. Once a start baseline exists, product Discovery and verification mutations likewise require the current selected repository to match that immutable start scope. Product-repository checks use `--subject`; absolute paths, traversal, missing files, product paths, and symlink escapes are rejected.

`graph query` reads the last materialized snapshot and never rebuilds automatically. Its default response reports bounded freshness state only; use `--full` for freshness counts, exact stale paths, or raw relations. Run `graph build` explicitly when no snapshot exists or after changes that must be reflected in Graph traversal. With a snapshot, `context query` uses the persistent evidence index and reads only stale path overlays. Without a snapshot, it still returns lexical source/document/task/Knowledge evidence and marks Graph relations unavailable.

If no active task is assigned:

- Use `task resume` to select the workspace lifecycle state; never select from archived history or infer among multiple live candidates.
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
- Worker agents must not set lifecycle-managed fields such as `status: done` or `handoff_origin_commitment`; use the corresponding repoctl lifecycle command.
- Humans and agents write task meaning: Goal, Discovery, Execution Log, Verification, and Handoff. Do not hand-edit `.repoctl-state` baselines, fingerprints, Handoff-origin records, ownership decisions, completion receipts, regenerable query-result receipts, or archive metadata; repoctl owns those machine records.
- A Handoff binding records that the exact four-field Handoff, structured task inputs, and observed repository state were reviewed together. It does not certify the semantic correctness of the prose. Any later Handoff, task contract, Discovery, Execution Log, Verification, or observed repository-content change makes it stale until explicitly reviewed and rebound.
- Repoctl-generated creation/start Handoff text is a placeholder, not reviewed task meaning. Repoctl carries its exact generated-body commitment in lifecycle-managed task frontmatter and records its body digest plus template version in machine-owned origin state. Origin state is published before the generated task body, and the task-carried commitment keeps unchanged placeholder text classified as generated if that sidecar is later missing; a non-regular, symlinked, unreadable, or inconsistent origin remains a typed error. Later copy, language, title, path, or repository-hint changes therefore cannot make the placeholder executable. Replace it with task-specific restart instructions before binding; repoctl warns and rejects an unchanged generated Handoff without attempting semantic prose analysis. `task start` replaces only a machine-proven exact generated placeholder and preserves a structurally valid authored Handoff. A Handoff with neither origin state nor a task-carried commitment is never classified by matching its prose to current or historical templates; it remains inactive until an agent regenerates, replaces, or explicitly reviews it and records one fresh provenance-aware `task handoff bind`.
- A `todo` task may record Discovery before start. Once a task is `doing` or `blocked`, every Discovery outcome mutation requires a current task-start baseline with the same immutable repository scope; every structured verification mutation requires that evidence in every status. A legacy live task without it must use a fresh follow-up rather than creating machine state that cannot be completed.
- When machine-owned Discovery outcome state exists, its `active_chosen` identities and the Task's complete structured `Chosen files` projection must agree. Explicit Chosen values that are not canonical workspace-relative paths are typed lifecycle errors rather than silently discarded data. Direct prose edits that make either form diverge leave lifecycle health unhealthy and block finish; reconcile the approved set through `task discovery add --replace-chosen ... --reason ...` rather than editing machine state.
- `task show` and `task doctor` may recommend decomposition when a live task combines a Chosen set beyond the existing compact path window, repeated closed Discovery episodes, and multiple structured verification records. This is an advisory to move the next independently verifiable milestone into a new task; it does not prove semantic independence, split automatically, or rewrite current scope.

## repoctl Boundary

- `repoctl` is the canonical mutation boundary for Board, Backlog, task creation, task lifecycle, archive transitions, and `.repometa` validation.
- Task/Board writes must hold `docs/tasks/.repoctl.lock.d` and use atomic writes.
- Do not keep separate task creation wrappers; use `./scripts/repoctl task create`.
- Use `./scripts/repoctl task show T-... --summary --json` for compact task inspection, omit `--summary` when the full task body is needed, and use `./scripts/repoctl task log append T-... "message" --json` to append timestamped execution log entries.
- Use `./scripts/repoctl task handoff bind T-... --json` after reviewing a pause/transfer Handoff. If an optional Pack was generated and reviewed, bind that exact workspace-local artifact with `--context-pack <path>`; repoctl fails closed on missing, tampered, wrong-task, legacy, or stale artifacts.
- When `## Verification` is complete, finish directly with `./scripts/repoctl task finish T-... --json`. Use `--verification-file` only for an external artifact. If the task established a reusable cross-task decision, invariant, or failure mode, add `--knowledge-kind`, an explicit `--knowledge-claim` or `--knowledge-claim-file`, and any literal `--knowledge-applies-to` paths to the same finish command so the completion receipt and review candidate are persisted together.
- Prefer finishing before committing product repo changes. If product changes were already committed after task start, use `--use-committed-diff`. This mode is allowed only when the recorded start HEAD is an ancestor of the current HEAD and no task-new working-tree changes remain.
- Use `./scripts/repoctl task doctor T-... --use-committed-diff --json` to preflight that same committed-range path before finish.
- While a task is live, `task doctor` reports current Chosen-vs-diff drift as an advisory; `task finish` remains the hard closure gate for unchosen changes.
- A committed range is observed Git evidence, not proof that its commits belong to the task. repoctl does not manage commit, push, PR, deploy, or delivery ownership.
- Use `./scripts/repoctl task block T-... --json` after recording the blocker in `## Verification`, or pass `--verification-file` for an external blocker artifact.

## Working Commands

- Exact string/symbol confirmation: `cd <selected-product-repo> && rg ...` (`repos/` for direct layout, `repos/<repo-id>/` for collection layout)
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
- Dirty path details live in the machine-owned task-start baseline. The human Execution Log records only their count and baseline reference rather than duplicating path lists.
- `## Verification` records commands, evidence, and results. Worker inability to run a gate is evidence, not final verification; final gates are the manager/Codex responsibility.
- Keep `## Handoff` aligned with the latest meaningful execution log before stopping.

## Archive/Follow-Up

- Standalone done/canceled tasks are archived immediately and removed from Board.
- Done/canceled child tasks leave Board but may remain in `docs/tasks/` until the parent archives.
- Parent tasks archive only after live children are done, canceled, or re-parented.
- Done/canceled tasks and their completion receipts are immutable.
- Additional work uses a new task: `./scripts/repoctl task create --follow-up-of T-old --slug ... "Follow-up title" --json`. The new task gets a new baseline; the old task is not moved or rewritten.
- Ordinary validation resolves the bounded transitive chain of immutable `follow_up_of` ancestors reachable from current tasks; it does not enumerate unrelated archive history.
- Create a Knowledge candidate only when the task established a reusable cross-task decision, invariant, or failure mode. Every candidate requires an explicit `--claim` or `--claim-file`; source documents and completion receipts provide provenance, never the reusable claim. Prefer the integrated `task finish --knowledge-kind ... --knowledge-claim ...` closeout. `knowledge candidate suggest --from-task ... --dry-run` remains a preview path for an already-finished task, not the normal closeout. Use candidate show/check before explicit approval, and never hand-edit candidate JSON. Approval materializes the reviewed record and synchronizes Graph in the same command; if Graph synchronization fails, keep the durable record and follow the returned typed rebuild action. Routine implementation details should remain in task history rather than durable Knowledge.

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
- Pre-existing dirty product state remains outside task scope; finish and cancel preserve it or require explicit ownership resolution.

## Adapter Policy

- `AGENTS.md` is the shared contract and single source of truth.
- Adapter files are thin shims and must not duplicate or contradict these rules.
- Generated agent files under `.claude/agents/` or `.codex/agents/` come from `ai/roles/`; update role sources and re-render instead of editing generated outputs.
- Reusable skills live in `.agents/skills/` as canonical source and mirror only when required by a tool.
