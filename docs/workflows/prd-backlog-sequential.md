---
title: PRD Backlog Triage and Sequential Execution
description: Turn PRD gaps into opaque Backlog items, then promote and finish them one at a time with discovery evidence.
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

## Non-Negotiables

- Do not let `repoctl` parse PRD or Backlog prose into task scope, files, validation, area, or repo metadata.
- Do not create implementation tasks while merely listing PRD gaps.
- Do not promote a Backlog item from memory; always `backlog list` and `backlog show` first.
- Do not implement multiple Backlog items in one task unless the user explicitly asks for a combined scope and the items are inseparable.
- Do not finish a Backlog-origin repo task without recorded `## Discovery` evidence.

## Phase 1: PRD Gap Triage

Use when the user says things like:

```text
PRD 보고 아직 안 된 것 Backlog로 정리해줘.
외부에서 정리한 요구사항 보고 미구현 항목 리스트업해줘.
```

Required sequence:

```bash
./scripts/repoctl backlog list --json
./scripts/repoctl meta inventory --json
./scripts/repoctl meta query --topic <known-topic> --json   # when a known topic exists
./scripts/repoctl meta suggest --text "<PRD phrase>" --json  # candidate hint only
```

Then inspect the relevant PRD text and repo files directly. Add one Backlog item per independently shippable missing capability:

```bash
./scripts/repoctl backlog add "Short missing capability" --body-file /tmp/prd-gap.md --json
./scripts/repoctl backlog list --json
./scripts/repoctl backlog show BL-... --json
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
./scripts/repoctl meta query --topic <known-topic> --json
./scripts/repoctl meta suggest --text "<Backlog/PRD phrase>" --json
```

Read the raw Backlog block, inspect candidate files directly, then promote with explicit fields:

```bash
./scripts/repoctl task create \
  --backlog-id BL-... \
  --slug <english-kebab-slug> \
  --area repo \
  --repo-id <id> \
  "Task title"
```

`repoctl task create --backlog-id` must fail if `--slug` or `--area` is missing. In configured multi-repo workspaces, product tasks must also pass the selected stable `--repo-id`. Do not work around those gates.

## Phase 3: Execute One Task

Required sequence:

```bash
./scripts/repoctl task start T-...
```

Before editing `repos/`, fill the task's `## Discovery` section with:

```md
- Candidate query: `<repoctl meta query/suggest commands used>`
- Candidate files reviewed: `repos/path`, ...
- Chosen files: `repos/path`, ...
```

Then implement the smallest complete change for that one item.

## Phase 4: Verify and Finish

Run focused validation first, then metadata gate:

```bash
cd <selected-product-repo> && <focused test or smoke command>
./scripts/repoctl meta check --changed --json
```

Write verification evidence outside `repos/`, then finish:

```bash
./scripts/repoctl task finish T-... --verification-file /tmp/verification.md --json
./scripts/repoctl check --json
./scripts/repoctl backlog list --json
./scripts/repoctl task list --json
```

Expected final state for one completed item:

- promoted Backlog item removed
- finished task archived under `docs/archive/tasks/`
- Board has no stale row for the finished task
- verification section includes tests and metadata gate evidence
- `## Discovery` remains in the archived task

## Phase 5: Continue Sequentially

After one task is archived, return to `backlog list --json` and repeat Phase 2 for the next item.

If an item is ambiguous, do not guess. Leave it in Backlog, or create a task and mark it blocked only when the user asked to start that item and a concrete blocker is known.

## Anti-Patterns

| Anti-pattern | Correct behavior |
| --- | --- |
| Creating tasks directly while listing PRD gaps | Add opaque Backlog items only |
| Parsing `Area:` or `Likely files:` from Backlog raw text | Agent reads context and passes explicit task fields |
| Running `meta suggest` and blindly editing the first result | Inspect files directly before choosing scope |
| Finishing several Backlog items in one archive task | Promote and finish one item at a time |
| Leaving `## Discovery` placeholders in a Backlog-origin repo task | Fill discovery evidence before finish |
