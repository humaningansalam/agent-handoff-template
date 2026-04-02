# AGENTS.md

Canonical operating rules for this repository.

## Default Operating Mode

- Default mode is **single-agent + single active task**.
- Do not assume parallel execution unless explicitly planned and safe.

## Read Order (Always Follow)

1. `AGENTS.md`
2. `project/TASKS.md`
3. Assigned task file in `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
4. Read only the docs listed in the active task's `## Context Docs`.
5. By default, product context lives in `project/PRD.md`.
6. Check `project/workflows/INDEX.md` only when a reusable/high-risk/repeated procedure may apply, then read only the matching workflow file(s).

If there is no active/assigned task:

- Choose a `todo` item from `project/TASKS.md`, or
- Start a new task immediately by creating `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
  from `project/tasks/TEMPLATE.md` and adding it to the Board, or
- Promote an item from `## Backlog` if the work was previously captured there.

## Task File Rules

- Each live task is a single file under `project/tasks/`.
- Task filename convention: `T-YYYYMMDDHHMMSSZ--slug.md` (UTC).
- `project/TASKS.md` is the repo-wide task board.
- Board rows are for live tasks with task files; planned items without a task file belong in `## Backlog`.
- Task frontmatter is local task metadata.
- When `status` or `owner` changes, update both the board row and the task file in the same edit/commit.
- Keep task files practical and execution-oriented.
- Prefer one task file per independently shippable unit.

## Documentation Language Policy

- Write generated repository documents (task files, plans, walkthroughs) in Korean by default.
- Do not translate code, filenames, commands, identifiers, API names, logs, or quoted external text.
- Override only if a task explicitly requires another language.

## Handoff Rules (`## Handoff` inside each task)

- Handoff is embedded in the task file, not in a separate session file.
- Every task file must include a `## Handoff` section.
- `## Handoff` must let the next agent start in ~30 seconds.
- Required fields:
  - **Next exact step**
  - **First file to open**
  - **First command to run**
  - **Done when**

## Archive Rules

- When a task is completed, move the original task file to `project/archive/tasks/`.
- Do not rewrite it into a separate summary document.
- Preserve original filename: `T-YYYYMMDDHHMMSSZ--slug.md`.
- Keep `project/TASKS.md` in sync with archive moves.

## Parallel Work Policy

Parallel work is **not** the default.

Parallel work is allowed only if all conditions are satisfied:

- No simultaneous edits to the same file/interface boundary
- Each task can be completed and verified independently
- Branch/worktree isolation is feasible per task

## Workflow Creation Policy

- Both humans and agents may create or update workflow docs.
- Add a workflow only when it is reusable, high-risk, or repeatedly needed.
- Do not create workflows for one-off task-local notes.
- Keep one-off task-local instructions inside the task file.
