---
title: PRD Backlog Triage and Sequential Execution
description: Turn PRD gaps into opaque Backlog items, then promote and finish them one at a time with explicit Chosen scope.
tags:
  - prd
  - backlog
  - discovery
  - sequential
authorized_scope:
  - docs/PRD.md
  - docs/BOARD.md
  - docs/tasks/**
  - docs/archive/tasks/**
  - repos/**
expected_output:
  - PRD gap Backlog items or one finished archived task per promoted item
---

# PRD Backlog Triage and Sequential Execution

Use this workflow when a user asks to compare a PRD or external product notes against the current repo and list missing work, then solve those items sequentially.

This workflow is agent-facing. Users do not need to mention `repoctl`, Backlog, task lifecycle, or metadata gates.

## Phase 1: PRD Gap Triage

Use when the user says things like:

```text
PRD 보고 아직 안 된 것 Backlog로 정리해줘.
외부에서 정리한 요구사항 보고 미구현 항목 리스트업해줘.
```

Read the relevant PRD and source. Use direct reads, search, Context, Graph or metadata only as needed to establish the gap. Check existing Backlog items to avoid duplicates, then add independently useful missing capabilities:

```bash
./scripts/repoctl backlog list --json
./scripts/repoctl backlog add "Short missing capability" --body-file /tmp/prd-gap.md --json
```

Backlog body guidance:

```text
Evidence: <PRD line/section or user note>; <repo files checked and why this appears missing>.
```

Keep Backlog raw blocks short. They are human notes, not structured task definitions.

## Phase 2: Promote One Item

Use when the user says to start, implement, solve, or continue a Backlog item.

Required sequence:

```bash
./scripts/repoctl backlog list --json
./scripts/repoctl backlog show BL-... --json
```

Read the selected item and the relevant source, then promote with explicit fields:

```bash
./scripts/repoctl task create \
  --backlog-id BL-... \
  --slug <english-kebab-slug> \
  --area repo \
  --repo-id <id> \
  "Task title"
```

`repoctl task create --backlog-id` must fail if `--slug` or `--area` is missing. In configured multi-repo workspaces, product tasks must also pass the selected stable `--repo-id`.

## Phase 3: Execute One Task

```bash
./scripts/repoctl task start T-...
```

Before editing `repos/`, record the canonical workspace-relative files that the task may change:

```bash
./scripts/repoctl task discovery add T-... \
  --chosen repos/path \
  --note "Why this scope or source matters" \
  --json
```

Repeat `--chosen` for a coherent multi-file change. The Note is optional. Do not register searches, opened files, rejected candidates, or tool use.

Then implement the smallest complete change for that one item.

## Phase 4: Verify and Finish

Run the relevant behavior check in the selected product repository. Return to the workspace root for repoctl commands. Keep a useful result in `## Verification` if needed, then finish:

```bash
./scripts/repoctl task finish T-... --json
```

Finish checks changed-file metadata and updates the archive and Board. A separate metadata check is useful when diagnosing a metadata error.

## Phase 5: Continue Sequentially

After one task is archived, return to `backlog list --json` and repeat Phase 2 for the next item.

If an item is ambiguous, do not guess. Leave it in Backlog, or create a task and mark it blocked only when the user asked to start that item and a concrete blocker is known.
