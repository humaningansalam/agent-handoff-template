# Tasks

This folder contains live task files, parent tasks, and canonical creation templates. Done/canceled tasks are immutable; child files may remain here only until their parent archives them.

Create most tasks with `./scripts/repoctl task create "Task title"` from the workspace root.
Use `./scripts/repoctl task create --type parent "Parent title"` for root-only coordination; product changes belong to repo-scoped child tasks.
Use `./scripts/repoctl backlog add/list/show/remove` to manage human-written Backlog items consistently. After reading an item and enough repo context, create an executable task with explicit `./scripts/repoctl task create --backlog-id BL-... ...` arguments and refine Goal, Discovery, Verification, and Handoff as work proceeds.
repoctl uses `TEMPLATE.md` or `PARENT_TEMPLATE.md` internally; these files are operational templates, not example tasks.

Backlog text is free-form planning text. repoctl treats each backlog item as an opaque raw block; it must not infer `area`, likely files, expected behavior, validation, or task body sections from that text.
Repo-scoped live tasks should fill in structured `## Discovery` with the inspected sources, candidate files, and selected files; `repoctl check` warns when this evidence is missing. The finish gate blocks placeholder discovery for Backlog-origin repo changes.
Use `repoctl meta query` and `repoctl meta suggest` only as discovery aids; inspect the files yourself and keep the final task scope explicit.
Use `./scripts/repoctl task discovery add T-... --query "..." --reviewed repos/path --chosen repos/path --json` to record structured Discovery without hand-editing the task file. Free-form Discovery prose is not enough unless it preserves the exact `Candidate query`, `Candidate files reviewed`, and `Chosen files` fields.
Candidate queries append to history. Reviewed files accumulate. Chosen files are the current active edit set; replace that set with `--replace-chosen ... --reason "..."` when scope changes.
When a specific Context or Graph result materially informed the selection, optionally attach only the selected references with `--result-producer context|graph --result-id <bundle_digest|result_digest> --result-authority source|graph|document|task_history|knowledge --result-ref <ref>`. This records typed `result -> selected ref` provenance; it is not mandatory feature-use logging and does not store every ranking.
Record the Candidate query first, run compact `repoctl context query`, and inspect the suggested product files read-only. Refine and repeat the query until the evidence is sufficient, then record Reviewed and Chosen files. A scoped Context Pack is optional when a durable handoff or relationship summary is useful.
Use `./scripts/repoctl task show T-... --summary --json` for compact task inspection; omit `--summary` only when the full task body is needed. Use `./scripts/repoctl task log append T-... "message" --json` for timestamped execution log entries.
When `## Verification` is complete, finish with `./scripts/repoctl task finish T-... --json`. Pass `--verification-file` only when the evidence already exists as an external file.

Agents and humans write task meaning in Markdown. repoctl alone writes `.repoctl-state` baseline data, fingerprints, ownership decisions, completion receipts, Closure, Board changes, and archive transitions.

For live tasks, keep `First file to open` pointed at an existing workspace file. repoctl preserves `First command to run` as text only; it does not shell-parse or execute that field. After completion, `Last Active Handoff` is historical and is not revalidated against the current filesystem.

Do not reopen a completed task ID. Create a new task with `--follow-up-of T-old`; the new task receives a new baseline while the completed task and receipt remain unchanged.

Example task files live under `examples/tasks/` and are documentation-only references.

If `repoctl` is installed on `PATH`, the shorter `repoctl ...` form is equivalent. The wrapper resolves the workspace root from the script location, so invoking it by explicit path from `repos/` or a nested directory is also supported.

Standalone tasks reaching `done` or `canceled` move to `docs/archive/tasks/` and are removed from `docs/BOARD.md`.
Child tasks reaching `done` or `canceled` are also removed from `docs/BOARD.md`, but may stay here until their parent task is archived.

Finishing a root coordination parent validates every completed descendant receipt before subtracting child-owned working-tree changes. Baseline comparison covers the union of product repositories recorded at parent start and product repositories currently present, so a repository adopted later cannot hide unclaimed changes or valid child attribution. A `working_tree_diff/task_working_tree` receipt claims each exact `change + path + old_path` identity it records; two child receipts claiming the same identity block as duplicate ownership before current-content matching. A sole claim is subtractable only when its per-entry fingerprint exists and matches the current entry. Missing, corrupt, task-ID-mismatched, incomplete, or drifted evidence fails closed instead of silently archiving descendants or assigning their changes to the parent.

Task-start dirty baselines cover the full recorded path set, not only paths still shown by `git status`. Restoring a tracked dirty file to `HEAD`, deleting an untracked baseline file, or otherwise changing its recorded path state remains a baseline conflict. Repo-scoped tasks may explicitly assign that conflict to the task; workspace tasks must restore the recorded state or move product ownership to a repo-scoped child. Finish and cancel preserve the baseline until one of those explicit outcomes occurs.
