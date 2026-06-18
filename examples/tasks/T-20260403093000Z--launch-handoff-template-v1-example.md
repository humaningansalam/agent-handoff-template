---
id: T-20260403093000Z
title: "Launch handoff template v1 (example)"
status: doing # todo, doing, blocked, done, canceled (see AGENTS.md)
owner: "agent"
repo_ref: "chore/T-20260403093000Z-template-launch-example"
created: 20260403T093000Z
area: "docs"
parent: ""
depends_on: []
---

# T-20260403093000Z - Launch handoff template v1 (example)

## Context Docs

<!--
Example:
- `docs/PRD.md`
-->

## Goal

Demonstrate a parent task that coordinates multiple narrower child tasks toward a single integrated outcome.

## In Scope

- Coordinate README refinement work
- Coordinate template/example alignment work
- Track shared decisions across child tasks

## Out of Scope

- Redesigning the repository structure
- Adding new operating layers beyond the existing template

## Plan

- Finalize the user-facing quick-start messaging
- Align live templates and examples
- Verify the final set of docs reads consistently

## Live Child Tasks

<!--
This list is a coordination summary, NOT the authoritative source.
The child's `parent` frontmatter field is the authoritative link.
-->
- `docs/tasks/T-20260403093100Z--refine-readme-copy.md`
- `docs/tasks/T-20260403093200Z--align-example-task-files.md`

## Non-Live Child Tasks

- `docs/tasks/T-20260403092900Z--clarify-parent-task-guidance.md`

## Shared Interfaces / Decisions

- Keep canonical task-routing and parent-task decision criteria in `AGENTS.md`
- Keep `README.md` as a lightweight usage guide rather than a duplicate policy document

## Integration Done When

- README, templates, and examples all reflect the same task model
- Parent/child task usage is understandable from examples without reading extra notes
- The next agent can continue coordination from this file alone

## Execution Log

<!-- Append only at meaningful checkpoints. Keep each entry to 1–2 concise lines. -->
- 20260403T093000Z: created parent task example to show coordination across narrower child tasks
- 20260403T114530Z: recorded shared documentation decisions and child-task status

## Verification

- Command(s) run: `rg -n "parent task|PARENT_TEMPLATE|depends_on|area:" README.md AGENTS.md docs/tasks examples/tasks`
- Evidence captured: parent-task guidance and example files use the same terminology and metadata shape
- Result: pass

## Handoff

- Next exact step: Move any live child task that reached done/canceled to the `## Non-Live Child Tasks` section and update the shared decisions if the guidance changed.
- First file to open: `docs/tasks/T-20260403093000Z--launch-handoff-template-v1-example.md`
- First command to run: `rg "T-20260403093" docs/BOARD.md docs/tasks/`
- Done when: The parent task reflects current child-task state (including non-live tasks), shared decisions are up to date, and coordination can continue without re-reading unrelated docs.
