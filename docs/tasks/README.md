# Tasks

This folder contains live task files, parent tasks, and canonical creation templates. Done/canceled tasks are immutable; child files may remain here only until their parent archives them.

Create most tasks with `./scripts/repoctl task create "Task title"` from the workspace root.
Use `./scripts/repoctl task create --type parent "Parent title"` for coordinating parent tasks.
Use `./scripts/repoctl backlog add/list/show/remove` to manage human-written Backlog items consistently. After reading an item and enough repo context, create an executable task with explicit `./scripts/repoctl task create --backlog-id BL-... ...` arguments and refine Goal, Discovery, Verification, and Handoff as work proceeds.
repoctl uses `TEMPLATE.md` or `PARENT_TEMPLATE.md` internally; these files are operational templates, not example tasks.

Backlog text is free-form planning text. repoctl treats each backlog item as an opaque raw block; it must not infer `area`, likely files, expected behavior, validation, or task body sections from that text.
Repo-scoped live tasks should fill in structured `## Discovery` with the inspected sources, candidate files, and selected files; `repoctl check` warns when this evidence is missing. The finish gate blocks placeholder discovery for Backlog-origin repo changes.
Use `repoctl meta query` and `repoctl meta suggest` only as discovery aids; inspect the files yourself and keep the final task scope explicit.
Use `./scripts/repoctl task discovery add T-... --query "..." --reviewed repos/path --chosen repos/path --json` to record structured Discovery without hand-editing the task file. Free-form Discovery prose is not enough unless it preserves the exact `Candidate query`, `Candidate files reviewed`, and `Chosen files` fields.
Candidate queries append to history. Reviewed files accumulate. Chosen files are the current active edit set; replace that set with `--replace-chosen ... --reason "..."` when scope changes.
Use `./scripts/repoctl task show T-... --json` for task inspection and `./scripts/repoctl task log append T-... "message" --json` for timestamped execution log entries.
When `## Verification` is complete, finish with `./scripts/repoctl task finish T-... --json`. Pass `--verification-file` only when the evidence already exists as an external file.

Agents and humans write task meaning in Markdown. repoctl alone writes `.repoctl-state` baseline data, fingerprints, ownership decisions, completion receipts, Closure, Board changes, and archive transitions.

For live tasks, keep `First file to open` pointed at an existing workspace file. repoctl preserves `First command to run` as text only; it does not shell-parse or execute that field. After completion, `Last Active Handoff` is historical and is not revalidated against the current filesystem.

Do not reopen a completed task ID. Create a new task with `--follow-up-of T-old`; the new task receives a new baseline while the completed task and receipt remain unchanged.

Example task files live under `examples/tasks/` and are documentation-only references.

If `repoctl` is installed on `PATH`, the shorter `repoctl ...` form is equivalent. The wrapper resolves the workspace root from the script location, so invoking it by explicit path from `repos/` or a nested directory is also supported.

Standalone tasks reaching `done` or `canceled` move to `docs/archive/tasks/` and are removed from `docs/BOARD.md`.
Child tasks reaching `done` or `canceled` are also removed from `docs/BOARD.md`, but may stay here until their parent task is archived.
