# Agent Task Handoff Template

A practical repository template for AI-agent execution that is **task-centered**, not session-centered.

## Why Task-Centered (Not Session-Centered)

Session-level global handoff files become stale quickly and split context across multiple places.

This template uses task files as the handoff unit. Each task contains its own execution state and `## Handoff`, so the next agent can continue immediately without searching for a separate session note.

## Lightweight Layered Structure

The structure is layered for clarity, but intentionally minimal.

### Core Always-Read Docs

- `AGENTS.md`: canonical operating rules
- `docs/TASKS.md`: repo-wide task board
- `docs/tasks/T-YYYYMMDDHHMMSSZ--slug.md`: assigned task execution unit

### Shared Context Docs

- `docs/PRD.md`: goals, scope, constraints, architecture overview, important decisions

### Conditional Workflows

- `docs/workflows/*.md`: active SOPs for risky/repetitive work; add only when needed
- Workflow docs may be created/updated by humans or agents
- Add a workflow only when it is reusable, high-risk, or repeatedly needed
- Do not create workflows for one-off task notes; keep one-off instructions in task files

### Archive

- `docs/archive/tasks/`: completed task file originals

### Tool Adapters

- `CLAUDE.md`
- `.cursor/rules/00-agent-os.mdc`

### Reference Examples

- `examples/`: reference-only examples; not active work

## Live vs Example Locations

- `docs/tasks/` contains active task files.
- `docs/workflows/` contains active project workflows.
- `examples/` contains reference-only examples and should not be treated as active work.

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
    └── workflows
        └── db-migration.example.md
```

## Quick Start

1. Read `AGENTS.md`.
2. Read `docs/TASKS.md`.
3. If no active task exists, create one from `docs/tasks/TEMPLATE.md`.
4. Name task file as `T-YYYYMMDDHHMMSSZ--slug.md` (UTC).
5. Add the task to `docs/TASKS.md`.
6. Start work.
7. Keep `## Handoff` updated.
8. On completion, move the task file to `docs/archive/tasks/`.

## Task Lifecycle

1. Create/update task file in `docs/tasks/`.
2. Use `T-YYYYMMDDHHMMSSZ--slug.md` naming for live tasks (UTC).
3. Track board status in `docs/TASKS.md` (`todo` -> `doing` -> `done`, or `blocked`).
4. Keep task frontmatter as local metadata and keep it synchronized.
5. When `status` or `owner` changes, update both `docs/TASKS.md` and the task frontmatter in the same edit/commit.
6. Execute and verify work in the task file.
7. Keep `## Handoff` current for the next agent.
8. When complete, move task file to `docs/archive/tasks/`.

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
