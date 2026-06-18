# BOARD

Thin registry of live tasks. Status definitions and operating rules are in `AGENTS.md`.

Board items are added when a task becomes live and removed when the task is no longer live.
For standalone tasks, this means reaching `done` or `canceled` (archival). Child tasks are removed from the board when they are no longer live (`done` or `canceled`), even if their files remain in `docs/tasks/` until the parent task is archived.
Do not update board items for status or owner changes.

Do not edit status or owner in Board; use `repoctl task ...` commands and task frontmatter.
An empty Board only means there are no live tasks; it is not product readiness proof.
Use `./scripts/repoctl check --fix-board` to repair stale live task entries.
Use `./scripts/repoctl backlog add/list/show/remove` to manage Backlog items.

## Board

## Backlog

<!-- Backlog items are opaque raw blocks managed by repoctl. -->
