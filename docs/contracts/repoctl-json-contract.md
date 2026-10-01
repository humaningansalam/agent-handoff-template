# repoctl JSON Contract

`repoctl --json` is the stable machine-facing interface for agents and future adapters. This contract freezes the common envelope and the meaning of key cross-command fields; command-specific payloads may add fields under `data`.

## Envelope

Success:

```json
{
  "ok": true,
  "command": "task.finish",
  "data": {},
  "warnings": [],
  "problems": [],
  "next_actions": []
}
```

Failure:

```json
{
  "ok": false,
  "command": "task.finish",
  "data": {
    "action_inputs": {
      "unchosen_actual_paths": ["src/extra.py"]
    }
  },
  "warnings": [],
  "problems": [
    {
      "severity": "error",
      "code": "actual_changes_outside_chosen",
      "message": "repository changes fall outside the task Chosen scope",
      "path": "src/extra.py"
    }
  ],
  "next_actions": [
    {
      "label": "Review changed paths outside Chosen scope",
      "kind": "task_scope_review",
      "target_ref": "data.action_inputs.unchosen_actual_paths",
      "choices": ["add_to_chosen", "revert_change", "move_to_follow_up"]
    }
  ]
}
```

- `ok` is true when no error-severity problem exists.
- `command` is a stable dotted command identity such as `task.finish` or `meta.check`.
- `data` is the command-specific object.
- `warnings` contains advisory problem objects.
- `problems` contains command-blocking problem objects.
- `next_actions` contains read-only recovery guidance.

Command payload fields appear only under `data`. Serialization rejects a missing command, non-object data, or unknown top-level fields rather than relocating them.

## Problems and repository identity

A problem has stable `severity`, `code`, and `message`; `path` and `cause_code` are optional.

```json
{
  "severity": "error",
  "code": "annotation_required",
  "message": "file matches coverage rule: matched coverage pattern src/**",
  "path": "repos/src/service.py"
}
```

Repo-aware payloads carry explicit repository context:

```json
{
  "repository": {
    "id": "main",
    "path": "repos",
    "identity_source": "reserved"
  },
  "files": [
    {
      "path": "src/service.py",
      "workspace_path": "repos/src/service.py"
    }
  ]
}
```

File-entry `path` is repo-relative. `workspace_path` is workspace-root-relative. A suggested repository ID is not stable until `repo adopt` pins it.

## Task and Handoff fields

Bare `task resume` reports `no_live | single_live | ambiguous`. `task resume <TASK_ID>` selects one returned live Task for that response and never persists a current-task pointer or selects archive history.

Handoff freshness and lifecycle health are independent:

- `data.resume_guidance.status` is `current | inactive | historical`.
- `task show` exposes lifecycle health at `data.health`.
- `task resume` exposes it at `data.resume_guidance.health`.
- `readable_handoff` preserves prose for inspection.
- `blocked_by_health` is true when lifecycle state is not executable.
- `executable_handoff` is non-null only when a current binding and lifecycle health both permit execution.

`task resume --compact` is an opt-in restart view; default and `--full` legacy consumers retain their string fields unchanged. The compact view keeps the complete reviewed section once in `readable_handoff`; a non-null `executable_handoff` contains `field_labels` (references to the four unique validated canonical labels in that section) and `reviewed_context_ref` pointing to that complete context. The fields are not a standalone authorization: preserve constraints and evidence in the reviewed context and cited sources. `handoff_source` provides the original section command. A current binding does not make historical prose current or settle conflicting instructions; no prose is ranked by position/date or removed. Invalid, unbound, stale or unhealthy instructions never acquire an executable projection. `--full --compact` adds full health details to this same view.

`task discovery add` returns current `chosen_files`, `notes`, per-input update details, and counts. It does not return a query episode, Reviewed/Excluded disposition, selected result, or structured check state.

`task doctor` and `task finish` share hard closure checks for repository identity, baseline ownership, actual paths outside Chosen, committed-range validity, and changed-file metadata. They do not expose a verification status or decide whether optional `## Verification` prose is complete. `finish_ready` means repoctl's lifecycle checks pass; it is not a claim that product behavior is correct.

A Chosen-scope decision action owns the complete path list at `data.action_inputs.unchosen_actual_paths` and uses `add_to_chosen | revert_change | move_to_follow_up` choices. A baseline decision similarly owns its complete list under `data.action_inputs.baseline_conflicts` and offers `task | preexisting`. These actions omit a command because repoctl cannot make the decision.

`task block` and `task cancel` require exactly one of `--reason` or `--reason-file`. Their normalized intent appears under `data.reason` with `data.reason_source: argument | file`. They append one Execution Log entry and preserve optional Verification prose.

`task.finish` reports `closure_scope: "task"` and `product_readiness: "not_evaluated"`. Product correctness, release readiness, commit, push, PR, and deployment remain outside the command's claim.

## Completion receipt v5

New completion receipts use schema v5. They retain stable Task/artifact identity, repository identity, changed entries, baseline/transition information when available, and metadata-gate history. They omit `verification` and `discovery_outcome`.

Receipt and task-artifact hashes protect lifecycle-history identity. They do not assert that a command ran, a file state was tested, or a result passed. Readers continue to accept immutable v2–v4 receipts under their historical schema, including their legacy verification and Discovery outcome fields. Legacy v2 working-tree child evidence may also be read after a descendant commit when repository identity, ancestry, the original fingerprint commitment, and the committed terminal path state are all provable. Replay uses an isolated temporary Git index/worktree and never rewrites the source repository or historical records; missing or unreproducible evidence remains an error.

## Context, Graph, and Pack output

`context query` and `graph query` return their evidence directly. Repeating either command creates no persistent result-receipt cache or selectable-result state. Stable source refs, typed relations, repository identity, completeness, freshness, and continuations remain in the command payload.

An explicit `resume [TASK_ID]` or `task resume [TASK_ID]` query (optionally followed by a colon and the question) routes to the existing read-only compact Task resume projection before repository candidate retrieval. `data.bundle` is null and `data.task_resume` contains that projection; selection, freshness, lifecycle errors and warnings are preserved. An explicit conflicting repository selector is rejected. General implementation queries retain ordinary Context behavior.

Default Context JSON is a bounded working projection. `--full` adds raw evidence and diagnostics without changing the meaning of visible members. Graph `--full` likewise adds raw nodes, edges, and provider diagnostics.

`context pack` returns one bounded Task view and optionally writes the same requested representation to `--output`. Its input projection is based on current Task, Chosen/Notes, Context Docs, repository observation, source identities, and Graph state. It has no binding or current/stale lifecycle status and writes no Task/Handoff state.

## Knowledge output

`knowledge add` saves a durable record in one operation and returns the record path plus derived projection status. A projection or Graph synchronization failure is a warning after the record is safely stored, with an explicit maintenance action.

Knowledge queries may report source drift while continuing to return the saved conclusion. The projection contains current direct records only. `knowledge check` and upgrade postflight report preserved older records that require one-time agent migration.

## Compact projections

Compact task responses retain authoritative counts while bounding large presentation arrays. A bounded array has a matching count and truncation field when callers need to know that details were omitted. Complete decision inputs stay untruncated under `data.action_inputs`.

Compact task repository state uses typed values:

- `repo_head_state`: `commit | unborn | unavailable | not_applicable`
- `observed_since_baseline`: `observed | baseline_missing | unavailable | not_applicable`

`field-gate run repoctl-release --json` returns a compact gate summary by default. `--full` adds child commands and diagnostics. `--output` writes the full digest-verifiable gate artifact even when stdout is compact.

## `next_actions`

- Actions never perform recovery automatically.
- They never infer task scope from prose.
- `command` is exact and copy-paste-safe or absent. It contains no placeholders, invented evidence files, redirection, or unresolved IDs.
- Decision actions use stable `kind`, `target_ref`, and enum `choices` rather than pretending to be executable.
- Every `target_ref` resolves to a non-empty untruncated string list in the same envelope.
- Problems remain authoritative when recovery needs a human or agent decision.

## Upgrade status

Upgrade postflight separates authoritative readiness from derived maintenance. Invalid repository identity, source authority, or durable state can fail the command. Stale or invalid derived state, or a projection required by durable records, produces maintenance warnings while the overall status may be `ready_with_maintenance` with exit 0. An unused missing Graph and empty Knowledge store remain `ready` without initialization work.

## Adapter implication

Future adapters must call repoctl handlers or consume this JSON contract. They must not parse human stdout, mutate `.repometa` directly, or bypass Task, Board, baseline, archive, and repository-selection gates.
