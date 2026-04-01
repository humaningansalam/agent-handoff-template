---
id: T-20260326084215Z
title: Repository cleanup follow-up (example)
status: doing
owner: agent
branch: chore/T-20260326084215Z-repo-cleanup-example
created: 2026-03-26
---

# T-20260326084215Z - Repository cleanup follow-up (example)

## Context Docs

- `docs/PRD.md`

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

- 2026-03-26: created example task and started doc cleanup
- 2026-03-26: updated naming and workflow policy guidance

## Verification

- Command(s) run: `rg --files -uu | sort`, `rg -n "T-YYYYMMDDHHMMSSZ|T-YYYYMMDD-HHMMSS|T-xxx--" .`
- Evidence captured: naming conventions and example filenames are consistent
- Result: pass

## Handoff

- Next exact step: Update one remaining README sentence if it still mentions non-UTC task naming.
- First file to open: `README.md`
- First command to run: `rg -n "T-YYYYMMDD-HHMMSS|T-xxx--|UTC" README.md`
- Done when: README only references `T-YYYYMMDDHHMMSSZ--slug.md` and explicitly states UTC for live task naming.
