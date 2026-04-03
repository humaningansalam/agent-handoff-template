---
id: T-YYYYMMDDHHMMSSZ
title: Replace with task title
status: todo
owner: unassigned
# optional: record branch/worktree if used
branch: ""
created: YYYY-MM-DD
# optional: frontend | backend | infra | docs | ops
area: ""
# optional: parent task ID for child tasks
parent: ""
# optional: prerequisite task IDs
depends_on: []
---

# T-YYYYMMDDHHMMSSZ - Title

## Context Docs

<!-- List only the minimum context docs needed for this task. -->
- `project/PRD.md`

<!-- Add exact workflow files only when relevant. Example:
- `project/workflows/db-migration.md`
-->

## Goal

State the outcome in one clear sentence.

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

## Execution Log

<!-- Append only at meaningful checkpoints. Keep each entry to 1–2 concise lines. -->
- YYYY-MM-DD HH:MM TZ: task created

## Verification

- Command(s) run
- Evidence captured
- Result

## Handoff

- Next exact step: <single precise action>
- First file to open: `<path/to/file>`
- First command to run: `<command>`
- Done when: <objective completion condition>

<!--
This section should let the next agent start in ~30 seconds.

Bad example:
- Next: continue from here
- File: check docs
- Command: run tests
- Done when: looks good

Good example:
- Next exact step: Update the task status and verification notes after running the targeted test.
- First file to open: `project/BOARD.md`
- First command to run: `rg "T-20260326084215Z" project/BOARD.md project/tasks/`
- Done when: The task row and task file both reflect the latest verified state and the next agent can continue without guessing.

Parent-task note:
- If this task becomes a parent/coordinating task, add sections such as
  `## Open Child Tasks`, `## Completed Child Tasks`,
  `## Shared Interfaces / Decisions`, and `## Integration Done When`
  only when needed.
-->
