# repoctl debug-mode contract

Debug mode records how repoctl's own control-plane features behave during ordinary development. It is local diagnostic evidence, not a feedback-writing mode, agent runtime, or source of Task, Graph, Knowledge, metadata, or Verification authority.

## Activation and invariance

Set the optional top-level `debug_mode` field in `docs/repoctl.json` to JSON boolean `true`. Missing or `false` disables capture; any other type fails with `invalid_debug_mode`.

Debug mode does not change command arguments, stdout, stderr, exit status, task files, product files, or Handoff freshness. It adds no hook, daemon, or agent instruction. Each ordinary repoctl invocation attempts to append one event to the ignored journal `docs/tasks/.repoctl-state/debug/events.jsonl`; reading `debug summary` does not record itself. Journal failure never replaces the command result.

When the journal would exceed 8 MiB, repoctl marks `capture-truncated`, starts a new bounded generation, and continues recording current events. `repoctl debug summary --json` then reports `capture.incomplete`; delete the ignored debug directory before a new observation period when a complete window is required.

## Recorded data

An event contains a UTC timestamp, duration, command identity, recognized option names before the standard `--` delimiter, argument count, validated repository/task IDs, exit status, observed problem/warning codes, and bounded feature counts. Parse failures retain no option names. Context and Graph results retain only their producer, opaque result ID, repository ID, and visible-member counts. A successful `task discovery add` retains only opaque result identity and counts for newly added selections.

Raw argument values, queries, source refs, excerpts, stdout, stderr, error messages, task prose, claims, reasons, credentials, environment variables, and full result payloads are never stored.

## Summary meaning

- `commands` reports calls, success/failure, duration, and `later_same_shape_success_after_failure`. A same-shape signal shares command, validated target, argument count, and non-output option names; because values are deliberately not stored, it is only a retry clue and never proof that the same request recovered.
- `context_sources.graph` separates Graph availability and resolved anchors from semantic `graph_relation` evidence and navigation continuations. A `graph.file` continuation is navigation, not a Graph relation.
- `context_sources.knowledge` distinguishes a Context consultation, returned records, and records visible in the emitted result.
- `context_sources.task_history` records explicit history attempts, including unavailable and not-applicable outcomes, separately from visible history evidence.
- `discovery_selections` reports visible result members and newly added selections observed while capture was enabled. `selected_outside_capture` means no matching result event was retained.

Exposure does not prove selection, and selection does not prove usefulness or correctness. Final feedback must compare this summary with Task goals, Chosen scope, Verification, completion outcomes, the PRD, and the resulting product behavior. Direct Graph counts alone cannot establish Graph use because Context can consult it; zero Reviewed Knowledge does not imply that ordinary project documents or task history were absent.
