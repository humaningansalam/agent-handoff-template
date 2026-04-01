# Runtime (Example)

Use this file for flows, lifecycles, transitions, and failure handling.
Do not use it for high-level product goals or stable schema detail.

## Core Flow

Describe the main runtime path from start to finish.

Examples:
- request -> validate -> execute -> store -> notify
- collect -> analyze -> decide -> act -> review

## State / Lifecycle

List the important states and what they mean.

Suggested format:
- `state_name`
  - meaning:
  - entry condition:
  - exit condition:

## Failure Handling

Describe the expected failure categories and default response.

Examples:
- retry
- rollback
- requeue
- partial completion
- manual review
- refund / reversal

## Human / Supervisor Checkpoints

Document where a human or coordinating agent may intervene.

Examples:
- approval step
- escalation point
- release gate
- review checkpoint

## Observability

List the runtime evidence needed for debugging and supervision.

Examples:
- key logs
- metrics
- alerts
- summary artifacts
- audit trail fields

## Improvement / Review Loop

If the system includes recursive improvement or review, define the loop here.

Examples:
- run -> log -> review -> lessons -> next task
- execute -> score -> diagnose -> patch -> verify
