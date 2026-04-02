# Workflow Index

Use this index to decide whether a workflow applies before opening individual workflow files.

Do not read every workflow by default.
Read only the workflow(s) that clearly match the current task.

## When to Use a Workflow

Use a workflow when at least one of these is true:

- the task follows a repeated procedure
- the order of operations matters
- mistakes are costly or hard to roll back
- multiple files/systems must be updated consistently
- the same instructions would otherwise be repeated across many tasks

Do not use a workflow for one-off task-local notes.
Keep one-off instructions inside the task file.

## Workflow Naming Rule

Workflow files use stable semantic names.

Examples:
- `db-migration.md`
- `release-rollback.md`
- `incident-triage.md`

Do not use timestamps for workflow filenames.

## Category Rule

Prefer these categories as the workflow list grows:

- Core Workflows
- Data / Migration Workflows
- Delivery / Release Workflows
- Incident / Recovery Workflows
- Quality / Review Workflows
- Deprecated / Archived Workflows

Use categories for the type of procedure, not for team names or ownership.

Use tags for domain/team/stack labels such as:
- `frontend`
- `backend`
- `infra`
- `api`
- `db`

Add a new category only when at least 3 workflows do not fit well under the existing categories.

## Active Workflows

No active workflows yet.

<!-- When adding the first workflow, use this shape:

## Data / Migration Workflows

| Workflow | Use When | Do Not Use When | Tags | Expected Output |
| --- | --- | --- | --- | --- |
| `db-migration.md` | ... | ... | ... | ... |

-->

When the first workflow is added:
1. Create it from `project/workflows/TEMPLATE.md`
2. Use a stable semantic filename
3. Place it under the best matching category
4. Link it from task `## Context Docs` only when relevant

## Add a New Workflow

Create a new workflow only when at least two of these are true:

- the same procedure has appeared 3+ times
- the order of steps is safety-critical
- task files keep repeating the same instructions
- multiple agents or humans need the same procedure
- the procedure benefits from a reusable verification checklist