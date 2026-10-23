# repoctl Debug-Mode Contract

Debug mode records bounded local observations about repoctl commands. It is diagnostic data, not Task state, product telemetry, verification proof, or project authority.

## Activation and invariance

Set top-level `"debug_mode": true` in `docs/repoctl.json`. Missing or `false` disables capture; another type fails with `invalid_debug_mode`.

`debug summary` and upgrade postflight expose the effective setting, whether it is explicit, and the journal path/presence. Upgrade preserves local settings; installation alone does not enable capture.

Enabled capture does not change arguments, stdout, stderr, exit status, product files, Task Markdown, Chosen, Handoff freshness, Graph, Knowledge, or metadata. Each ordinary invocation attempts to append one event to ignored local state at `docs/tasks/.repoctl-state/debug/events.jsonl`. `debug summary` does not record itself, and journal failure never replaces the command result.

The journal is capped at 8 MiB. On overflow, repoctl marks the capture incomplete, starts a new bounded generation, and keeps current events. Remove the ignored debug directory before a new observation window when complete history is needed.

## Recorded data

An event may contain:

- UTC timestamp and duration
- dotted command identity
- recognized option names and argument count, without values
- validated repository and Task IDs
- exit status and problem/warning codes
- Context source consultation counts; Graph/Knowledge query outcomes and returned source paths or record IDs; Knowledge saves, including task finish; Handoff freshness and resume executability

Returned references are bounded within the same event, with total count and truncation status. Raw queries, paths from arguments, excerpts, Task prose, Knowledge claims/reasons, stdout, stderr, error messages, credentials, environment variables, full result payloads, Discovery selections, and verification outcomes are not stored.

## Summary meaning

`debug summary --json` reports command counts, success/failure, duration, later same-shape success after failure, Context source consultation, and journal completeness. A request shape uses command, validated target, argument count, and non-output option names because raw values are deliberately absent.

Query status separates a returned match from a successful empty query. References link the returned context to original execution records for downstream-use analysis; neither a return nor a later same-shape success proves reuse or recovery. Direct file reads remain in the caller's execution record, not this repoctl journal.
