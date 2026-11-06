---
id: T-20260609184047Z
title: "Coordinate callback recovery rollout"
status: doing
owner: "unassigned"
repo_ref: ""
repo_id: ""
created: 20260609T184046Z
area: "ops"
parent: ""
depends_on: []
---

# Review payment idempotency

## Goal

Coordinate safe rollout of the callback recovery rule across services.

## Handoff

- Next exact step: Review the canonical retry rule against the incident.
- First file to open: `docs/specs/conformance.md`
- First command to run: `./scripts/repoctl task show T-20260609184047Z --json`
- Done when: The documented charge rule and actual retry evidence agree.

## Context Docs

- Requirement: `docs/specs/conformance.md`

## Work Area

- Task record: `docs/tasks/T-20260609184047Z--conformance.md`

## Discovery

- Chosen files: none yet
- Notes: The duplicate-charge incident is still being investigated.

## Execution Log

- 20260609T184046Z: Confirmed a callback retry in the provider logs.

## Verification

- Pending review of the original provider receipt.
