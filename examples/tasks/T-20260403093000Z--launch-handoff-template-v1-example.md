---
id: T-20260403093000Z
title: "Launch handoff template v1 (example)"
status: doing # todo, doing, blocked, done, canceled (see AGENTS.md)
owner: "agent"
repo_ref: ""
repo_id: ""
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

Demonstrate a root-only parent task that coordinates independently verifiable repo-scoped child tasks toward one integrated product outcome.

## Work Area

- Task record: `examples/tasks/T-20260403093000Z--launch-handoff-template-v1-example.md`
- Primary surface: Root coordination only; product implementation remains in repo-scoped child tasks
- Area hint: docs

## In Scope

- Coordinate product search indexing work in the selected repository
- Coordinate the product search API integration in a separate repo-scoped child task
- Track the shared search contract and integration evidence across child tasks

## Out of Scope

- Editing product files directly from the parent task
- Adding workspace or repoctl features

## Plan

- Confirm each child has an explicit repository identity and independently verifiable outcome
- Keep the shared search contract consistent across child tasks
- Integrate only after both repo-scoped child tasks pass their own verification

## Live Child Tasks

<!--
This list is a coordination summary, NOT the authoritative source.
The child's `parent` frontmatter field is the authoritative link.
-->
- `docs/tasks/T-20260403093100Z--build-product-search-index.md`
- `docs/tasks/T-20260403093200Z--integrate-product-search-api.md`

## Non-Live Child Tasks

- `docs/tasks/T-20260403092900Z--define-product-search-contract.md`

## Shared Interfaces / Decisions

- Each child records `repo_id: "main"` and its own Discovery, chosen files, and verification
- The search result schema is the shared interface; implementation details remain owned by each child

## Integration Done When

- Search indexing and API integration are each verified in their repo-scoped child task
- The integrated product flow satisfies the shared search contract
- The next agent can continue coordination without treating the parent as product-edit scope

## Execution Log

<!-- Append only at meaningful checkpoints. Keep each entry to 1–2 concise lines. -->
- 20260403T093000Z: created parent task example to coordinate independently verifiable repo-scoped child tasks
- 20260403T114530Z: recorded the shared product contract and child-task status

## Verification

- Command(s) run: `rg -n "root-only parent|repo-scoped child|parent:|repo_id:" AGENTS.md docs/tasks examples/tasks`
- Evidence captured: the parent remains root-only while every implementation child owns explicit repository scope and independent verification
- Result: pass

## Handoff

- Next exact step: Check each repo-scoped child task's status and integration evidence, then update the coordination summary.
- First file to open: `examples/tasks/T-20260403093000Z--launch-handoff-template-v1-example.md`
- First command to run: `rg -n "^parent:|^repo_id:|T-20260403093" docs/tasks/ examples/tasks/`
- Done when: The parent reflects current child-task state, each live implementation child has repository scope and verification evidence, and the integrated contract is ready to check.
