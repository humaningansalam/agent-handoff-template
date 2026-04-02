---
id: T-YYYYMMDDHHMMSSZ
title: Replace with parent task title
status: todo
owner: unassigned
# optional: record branch/worktree if used
branch: ""
created: YYYY-MM-DD
# optional: frontend | backend | infra | docs | ops
area: ""
# parent tasks should leave this empty
parent: ""
# optional: prerequisite task IDs
depends_on: []
---

# T-YYYYMMDDHHMMSSZ - Parent Title

## Context Docs

<!-- List only the minimum context docs needed for this task. -->
- `project/PRD.md`

<!-- Add exact workflow files only when relevant. Example:
- `project/workflows/db-migration.md`
-->

## Goal

State the cross-cutting outcome in one clear sentence.

## In Scope

- List concrete deliverables
- Keep items testable

## Out of Scope

- List explicit non-goals
- Prevent scope drift

## Plan

- Step 1
- Step 2
- Step 3

## Open Child Tasks

- `project/tasks/T-YYYYMMDDHHMMSSZ--child-task.md`

## Completed Child Tasks

- `project/tasks/T-YYYYMMDDHHMMSSZ--completed-child-task.md`

## Shared Interfaces / Decisions

- Decision 1

## Integration Done When

- Condition 1

## Execution Log

- YYYY-MM-DD: started task

## Verification

- Command(s) run
- Evidence captured
- Result

## Handoff

- Next exact step: Move a completed child from `## Open Child Tasks` to `## Completed Child Tasks` and sync the board row state.
- First file to open: `project/tasks/T-YYYYMMDDHHMMSSZ--parent-title.md`
- First command to run: `rg "T-YYYYMMDDHHMMSSZ" project/BOARD.md project/tasks/`
- Done when: The parent task reflects the latest child state, the board rows match the current execution state, and the next agent can continue without guessing.

<!--
Use this template only when the work needs a coordinating parent task across multiple narrower child tasks.
Child tasks should use `project/tasks/TEMPLATE.md` with `parent: "T-..."` filled in.
-->
