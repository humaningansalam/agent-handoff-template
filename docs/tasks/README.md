# Tasks

This folder contains live task files, parent tasks, canonical creation templates, and tasks that are no longer live but remain reopenable until their parent task is archived.

Create most tasks with `./scripts/repoctl task create "Task title"` from the workspace root.
Use `./scripts/repoctl task create --type parent "Parent title"` for coordinating parent tasks.
Use `./scripts/repoctl backlog add/list/show/remove` to manage human-written Backlog items consistently. After reading an item and enough repo context, create an executable task with explicit `./scripts/repoctl task create --backlog-id BL-... ...` arguments and then refine the task file with the concrete scope, files, plan, and handoff.
repoctl uses `TEMPLATE.md` or `PARENT_TEMPLATE.md` internally; these files are operational templates, not example tasks.

Backlog text is free-form planning text. repoctl treats each backlog item as an opaque raw block; it must not infer `area`, likely files, expected behavior, validation, or task body sections from that text.
Repo-scoped live tasks should fill in `## Discovery` with the inspected sources, candidate files, and selected files; `repoctl check` warns when this evidence is missing. The finish gate blocks placeholder discovery for Backlog-origin repo changes.
Use `repoctl meta query` and `repoctl meta suggest` only as discovery aids; inspect the files yourself and keep the final task scope explicit.
Use `./scripts/repoctl task discovery add T-... --query "..." --reviewed repo/path --chosen repo/path --json` to record structured Discovery without hand-editing the task file.
Use `./scripts/repoctl task show T-... --json` for task inspection and `./scripts/repoctl task log append T-... "message" --json` for timestamped execution log entries.
Finish with a verification file outside `repo/`: `./scripts/repoctl task finish T-... --verification-file /tmp/T-...-verification.md --json`.

Example task files live under `examples/tasks/` and are documentation-only references.

If `repoctl` is installed on `PATH`, the shorter `repoctl ...` form is equivalent. The wrapper resolves the workspace root from the script location, so invoking it by explicit path from `repo/` or a nested directory is also supported.

Standalone tasks reaching `done` or `canceled` move to `docs/archive/tasks/` and are removed from `docs/BOARD.md`.
Child tasks reaching `done` or `canceled` are also removed from `docs/BOARD.md`, but may stay here until their parent task is archived.
