# Archive

Archive is for preserving completed task file originals.

## Rules

- Move completed standalone task files to `project/archive/tasks/` immediately.
- When a parent task is completed, move the parent task file and any remaining child task files to `project/archive/tasks/`.
- Keep original filename format, e.g. `T-YYYYMMDDHHMMSSZ--slug.md`.
- Do not create separate archive summaries.
- Do not create `summary.md`, `final.md`, or `handoff.md`.

## Notes

- Source of truth for closed work is the archived task file itself.
- Remove standalone rows from `project/BOARD.md` when they are archived.
- Remove child rows from `project/BOARD.md` when the child is completed, even if the file remains in `project/tasks/` until the parent is archived.
