# Product Requirements Document (PRD)

This document is the long-lived product/project context.

Use it for stable context, not step-by-step execution state.

## Product / Project Goal

Provide a minimal but robust operating template for human/agent and agent/agent collaboration using task-centered execution.

## Scope

In scope:

- Task-centered execution model
- Lightweight layered documentation
- Embedded handoff in each task file
- Minimal adapter files for different agent tools

Out of scope (for this template version):

- Heavy process layers (`reports/`, `standards/`, `decisions/`, etc.)
- Session-centered state files
- Mandatory parallel multi-agent orchestration

## Constraints

- Keep the repository simple and practical.
- Keep operational rules centralized in `AGENTS.md`.
- Keep tool-specific files as thin adapters.
- Avoid duplicate or conflicting policy text across files.

## Architecture Overview

Primary operating flow:

1. Read `AGENTS.md`
2. Read `project/BOARD.md`
3. Read assigned `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`
4. Read `project/PRD.md` only when broader context is needed
5. Read `project/workflows/*.md` only when a task explicitly depends on that workflow

Task files are the execution and handoff unit.

## Important Decisions

- Session handoff is embedded in task files under `## Handoff`.
- `project/BOARD.md` is the repository-wide board; task frontmatter is local metadata.
- Status/owner changes must update both board and task file together.
- Parallel work is optional and not default.
- Examples are reference-only and not active work.
- Do not use global session status files.
