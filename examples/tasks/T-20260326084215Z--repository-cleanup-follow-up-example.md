---
id: T-20260326084215Z
title: "Repository cleanup follow-up (example)"
status: doing # todo, doing, blocked, done, canceled (see AGENTS.md)
owner: "agent"
repo_ref: "chore/T-20260326084215Z-repo-cleanup-example"
created: 20260326T084215Z
area: "docs"
parent: ""
depends_on: []
---

# T-20260326084215Z - Repository cleanup follow-up (example)

## Context Docs

<!--
Example:
- `docs/PRD.md`
-->

## Goal

Demonstrate a concrete in-progress task file with embedded handoff.

## In Scope

- Align naming/policy docs with repository conventions
- Update references in README and operating docs

## Out of Scope

- Redesigning folder structure
- Adding new process layers

## Plan

- Confirm current file tree
- Apply minimal policy edits
- Verify docs and paths are consistent

## Execution Log

<!-- Append only at meaningful checkpoints. Keep each entry to 1–2 concise lines. -->
- 20260326T084215Z: created example task and started doc cleanup
- 20260326T101500Z: updated naming and workflow policy guidance

## Verification

- Command(s) run: `rg --files -uu | sort`, `rg -n "T-[0-9]{14}Z--" README.md docs/tasks examples/tasks`
- Evidence captured: canonical task filename examples use the current UTC basic format consistently
- Result: pass

## Handoff

- Next exact step: Confirm README quick-start text still matches the canonical task filename and current workspace conventions.
- First file to open: `README.md`
- First command to run: `rg -n "T-YYYYMMDDHHMMSSZ--slug\\.md|repo/" README.md`
- Done when: README references the canonical task filename and current workspace conventions only.
