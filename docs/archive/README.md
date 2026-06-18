# Archive

Archive is for preserving non-live task file originals.

## Rules

- Move standalone task files reaching `done` or `canceled` to `docs/archive/tasks/` immediately.
- When a parent task reaches `done` or `canceled`, move the parent task file and any remaining child task files to `docs/archive/tasks/`.
- Keep original filename format, e.g. `T-YYYYMMDDHHMMSSZ--slug.md`.
- Do not create separate archive summaries.
- Do not create `summary.md`, `final.md`, or `handoff.md`.

## Notes

- Source of truth for non-live work remains the task file itself, whether it is still under `docs/tasks/` or already moved to `docs/archive/tasks/`.
- Remove standalone items from `docs/BOARD.md` when they cease to be live (archived).
- Remove child items from `docs/BOARD.md` when the child reaches `done` or `canceled`, even if the file remains in `docs/tasks/` until the parent is archived.
