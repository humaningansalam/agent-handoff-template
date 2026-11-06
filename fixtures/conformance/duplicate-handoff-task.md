---
id: T-20260609184046Z
title: "Review payment idempotency after a production retry"
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

Prevent a duplicate charge when the payment provider retries a callback.

## Handoff

- Next exact step: Review the canonical retry rule against the incident.
- First file to open: `docs/specs/conformance.md`
- First command to run: `./scripts/repoctl task show T-20260609184046Z --json`
- Done when: The documented charge rule and actual retry evidence agree.

## Context Docs

- Requirement: `docs/specs/conformance.md`

## Work Area

- Task record: `docs/tasks/T-20260609184046Z--conformance.md`

## Discovery

- Chosen files: none yet
- Notes: The duplicate-charge incident is still being investigated.

## Execution Log

- 20260609T184046Z: Confirmed a callback retry in the provider logs.

## Verification

- Pending review of the original provider receipt.

## Handoff

- Next exact step: An unauthorized second handoff.
