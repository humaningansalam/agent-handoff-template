# Workflow Index

Some workflow documents may also define lightweight operational standards when a separate standards document would add unnecessary structure. `repo-metadata.md` is one such workspace-level workflow document.

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

If the workflow list grows, group workflows by procedure type, not by team ownership.

Preferred categories:
- Core Workflows
- Data / Migration Workflows
- Delivery / Release Workflows
- Incident / Recovery Workflows
- Quality / Review Workflows
- Deprecated / Archived Workflows

## Active Workflows

### Quality / Review Workflows

| Workflow | Use When | Do Not Use When | Tags | Expected Output |
| --- | --- | --- | --- | --- |
| `prd-backlog-sequential.md` | Comparing PRD/external notes to repo, listing missing work, or solving Backlog items one at a time | One-off read-only questions, immediate single known-file fixes | prd, backlog, discovery, sequential | PRD gap Backlog items or one finished archived task per promoted item |
| `repo-metadata.md` | Creating/modifying graph-relevant files in `repos/`, or using `.repometa` to discover candidate files before task promotion | Root workspace files only, generated/vendor/build outputs | metadata, repo, graph, discovery | valid `repos/.repometa` policy/annotation state and inspected discovery candidates |
| `repoctl-upgrade.md` | Updating an adopting workspace to a newer repoctl/control-plane release | Product repo changes, project-specific workflow edits, or Board/Backlog/task cleanup | upgrade, release, control-plane | dry-run plan, inspected apply result, and repoctl verification gates |

## Add a New Workflow

Create a new workflow only when at least two of these are true:

- the same procedure has appeared 3+ times
- the order of steps is safety-critical
- task files keep repeating the same instructions
- multiple agents or humans need the same procedure
- the procedure benefits from a reusable verification checklist
