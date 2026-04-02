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

- YYYY-MM-DD: started task

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
- Next exact step: Archive the completed task and remove its row from the board.
- First file to open: `project/BOARD.md`
- First command to run: `rg "T-20260326084215Z" project/BOARD.md`
- Done when: Row for `T-20260326084215Z` is removed from `project/BOARD.md` and task file is moved to `project/archive/tasks/T-20260326084215Z--repo-cleanup.md`.
-->
