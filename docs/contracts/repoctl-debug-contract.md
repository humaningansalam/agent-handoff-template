# repoctl Debug-Mode Contract

Debug mode records bounded local observations about repoctl commands. It is diagnostic data, not Task state, product telemetry, verification proof, or project authority.

## Activation and invariance

Set top-level `"debug_mode": true` in `docs/repoctl.json`. Missing or `false` disables capture; another type fails with `invalid_debug_mode`.

Enabled capture does not change arguments, stdout, stderr, exit status, product files, Task Markdown, Chosen, Handoff freshness, Graph, Knowledge, or metadata. Each ordinary invocation attempts to append one event to ignored local state at `docs/tasks/.repoctl-state/debug/events.jsonl`. `debug summary` does not record itself, and journal failure never replaces the command result.

The journal is capped at 8 MiB. On overflow, repoctl marks the capture incomplete, starts a new bounded generation, and keeps current events. Remove the ignored debug directory before a new observation window when complete history is needed.

## Recorded data

An event may contain:

- UTC timestamp and duration
- dotted command identity
- recognized option names and argument count, without values
- validated repository and Task IDs
- exit status and problem/warning codes
- for Context only, bounded counts for Graph availability/anchor state, Knowledge consultation, and explicit task-history consultation

Raw queries, paths from arguments, excerpts, Task prose, Knowledge claims/reasons, stdout, stderr, error messages, credentials, environment variables, and result payloads are not stored. Context and Graph result IDs, selectable members, Discovery selections, and verification outcomes are not recorded.

## Summary meaning

`debug summary --json` reports command counts, success/failure, duration, later same-shape success after failure, Context source consultation, and journal completeness. A request shape uses command, validated target, argument count, and non-output option names because raw values are deliberately absent.

The summary can show that a command ran or that Context consulted an available source lane. It cannot show that a result influenced a decision, that the user read it, that code was correct, or that a test passed. Compare it with the actual Task, changes, and product behavior only when diagnosing repoctl ergonomics.
