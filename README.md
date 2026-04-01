# Agent Task Handoff Template

A practical repository template for AI-agent execution that is **task-centered**, not session-centered.

## Why Task-Centered (Not Session-Centered)

Session-level global handoff files become stale quickly and split context across multiple places.

This template uses task files as the handoff unit. Each task contains its own execution state and `## Handoff`, so the next agent can continue immediately without searching for a separate session note.

## Lightweight Layered Structure

The structure is layered for clarity, but intentionally minimal.

### Core Always-Read Docs

- `AGENTS.md`: canonical operating rules
- `project/TASKS.md`: repo-wide task board and optional backlog
- `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`: assigned task execution unit

### Shared Context Docs

- `project/PRD.md`: single-file product context by default
- `examples/prd/`: optional split-PRD reference pattern for larger projects
- `docs/`: reserved for external/shareable documentation

### Conditional Workflows

- `project/workflows/*.md`: active SOPs for risky/repetitive work; add only when needed
- Workflow docs may be created/updated by humans or agents
- Add a workflow only when it is reusable, high-risk, or repeatedly needed
- Do not create workflows for one-off task notes; keep one-off instructions in task files

### Archive

- `project/archive/tasks/`: completed task file originals

### Tool Adapters

- `CLAUDE.md`
- `.cursor/rules/00-agent-os.mdc`

### Reference Examples

- `examples/`: reference-only examples; not active work

## Live vs Example Locations

- `project/tasks/` contains active task files.
- `project/workflows/` contains active project workflows.
- `docs/` is reserved for external/shareable docs.
- `examples/` contains reference-only examples and should not be treated as active work.
- `examples/prd/` contains an optional split-PRD pattern for projects that outgrow a single `project/PRD.md`.

## Backlog

`project/TASKS.md` may include a `## Backlog` section for planned work that does not have a task file yet.

When work actually starts, create `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
from `project/tasks/TEMPLATE.md` and add it to the Board.

## Documentation Language

Generated repository documents (task files, plans, walkthroughs) default to Korean.

Keep code, filenames, commands, identifiers, API names, logs, and quoted external text
in their original language.

See `AGENTS.md` for the canonical rule.

## Repository Tree

```text
.
├── LICENSE
├── README.md
├── AGENTS.md
├── CLAUDE.md
├── .cursor
│   └── rules
│       └── 00-agent-os.mdc
├── docs
├── project
│   ├── PRD.md
│   ├── TASKS.md
│   ├── tasks
│   │   └── TEMPLATE.md
│   ├── workflows
│   │   └── README.md
│   └── archive
│       ├── README.md
│       └── tasks
│           └── .gitkeep
└── examples
    ├── tasks
    │   └── TASK.example.md
    ├── workflows
    │   └── db-migration.example.md
    └── prd
        ├── README.md
        ├── PRD.index.example.md
        ├── product.example.md
        ├── policies.example.md
        ├── architecture.example.md
        ├── runtime.example.md
        └── contracts.example.md
```

## Quick Start

1. Read `AGENTS.md`.
2. Read `project/TASKS.md`.
3. In the task file, list only the minimum needed docs under `## Context Docs`.
4. If no active task exists, choose a `todo`, promote a backlog item, or create a new task from `project/tasks/TEMPLATE.md`.
5. Name task file as `T-YYYYMMDDHHMMSSZ--slug.md` (UTC).
6. Add the task to `project/TASKS.md`.
7. Start work.
8. Keep `## Handoff` updated.
9. On completion, move the task file to `project/archive/tasks/`.

## Task Lifecycle

1. Create/update task file in `project/tasks/`.
2. Use `T-YYYYMMDDHHMMSSZ--slug.md` naming for live tasks (UTC).
3. Track board status in `project/TASKS.md` (`todo` -> `doing` -> `done`, or `blocked`).
4. Keep task frontmatter as local metadata and keep it synchronized.
5. When `status` or `owner` changes, update both `project/TASKS.md` and the task frontmatter in the same edit/commit.
6. Execute and verify work in the task file.
7. Keep `## Handoff` current for the next agent.
8. When complete, move task file to `project/archive/tasks/`.

## Archive Rules

- Archive keeps original completed task files.
- Do not write additional archive summaries.
- Keep original naming (`T-YYYYMMDDHHMMSSZ--slug.md`).

## Handoff Model

Handoff is embedded in each task file under `## Handoff` with four required fields:

- Next exact step
- First file to open
- First command to run
- Done when

## Parallel Work Policy

Parallel work is optional, not default.

Use parallel execution only when tasks are independent, do not edit the same file/interface boundary, and can be isolated by branch/worktree.
