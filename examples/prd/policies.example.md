# Policies (Example)

Use this file for stable rules, constraints, and guardrails.
Do not use it for step-by-step flow; put that in `runtime`.

## Core Business Rules

List rules that many tasks depend on.

Examples:
- charging rules
- refund or reversal rules
- approval requirements
- access / permission rules
- pricing or quota rules

## Safety / Guardrails

Document behaviors that must be prevented or handled conservatively.

Examples:
- actions requiring human confirmation
- forbidden changes
- escalation conditions
- safety limits

## Immutable Constraints

Document rules that should not be changed casually.

Examples:
- legal/compliance requirements
- data retention constraints
- irreversible domain rules
- prohibited shortcuts

## Quality Rules

List the rules that affect eligibility, ranking, review, or penalties.

Examples:
- quality thresholds
- score-based restrictions
- retry eligibility
- publication requirements

## Notes

- Use `runtime` for flow and state transitions.
- Use `contracts` for schemas, payloads, and structured formats.
