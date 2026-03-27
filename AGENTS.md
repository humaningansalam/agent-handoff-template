# AGENTS.md

Canonical operating rules for this repository.

## Default Operating Mode

- Default mode is **single-agent + single active task**.
- Do not assume parallel execution unless explicitly planned and safe.

## Read Order (Always Follow)

1. `AGENTS.md`
2. `docs/TASKS.md`
3. Assigned task file in `docs/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
4. `docs/PRD.md` only when product context/constraints/decisions are needed
5. `docs/workflows/*.md` only when the task explicitly maps to that workflow

If there is no active/assigned task:

- Choose a `todo` item from `docs/TASKS.md`, or
- Start a new task immediately by creating `docs/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
  from `docs/tasks/TEMPLATE.md` and adding it to the Board, or
- Promote an item from `## Backlog` if the work was previously captured there.

## Task File Rules

- Each active task is a single file under `docs/tasks/`.
- Task filename convention: `T-YYYYMMDDHHMMSSZ--slug.md` (UTC).
- `docs/TASKS.md` is the repo-wide task board.
- Task frontmatter is local task metadata.
- When `status` or `owner` changes, update both the board row and the task file in the same edit/commit.
- Keep task files practical and execution-oriented.
- Prefer one task file per independently shippable unit.

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

- When a task is completed, move the original task file to `docs/archive/tasks/`.
- Do not rewrite it into a separate summary document.
- Preserve original filename: `T-YYYYMMDDHHMMSSZ--slug.md`.
- Keep `docs/TASKS.md` in sync with archive moves.

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
