# repoctl JSON contract

`repoctl --json` output is the stable machine-facing contract for agents and future MCP wrappers.

This contract freezes the common envelope, not every command's full payload.

## Success envelope

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

## Failure envelope

```json
{
  "ok": false,
  "command": "task.finish",
  "data": {},
  "warnings": [],
  "problems": [
    {
      "severity": "error",
      "code": "missing_verification_file",
      "message": "task finish requires an external verification file or a completed Verification section",
      "path": "docs/tasks/T-...--slug.md"
    }
  ],
  "next_actions": [
    {
      "label": "Create verification evidence",
      "command": "cat > /tmp/T-...-verification.md"
    }
  ]
}
```

## Fields

- `ok`: `true` when no error-severity problem exists.
- `command`: stable dotted command name, such as `task.finish` or `meta.check`.
- `data`: command-specific structured result. It must be an object.
- `warnings`: advisory problem objects that do not fail the command.
- `problems`: error or warning objects with stable `code` values.
- `next_actions`: advisory recovery hints. They must not imply that repoctl changed state.

Command-specific values exist only under `data`. Commands must not mirror `task`, `result`, `repository`, counters, paths, or any other payload field at the top level for compatibility.
Serialization validates this boundary and rejects missing `command`, non-object `data`, or unknown top-level fields instead of silently relocating producer mistakes.

Task Handoff freshness and repository lifecycle health are separate machine contracts. `data.resume_guidance.status` is one of `current | inactive | historical`; only `current` is freshness-active. `task show` returns lifecycle health at `data.health`; `task resume` returns it at `data.resume_guidance.health`. A current Handoff may therefore accompany unhealthy lifecycle evidence and does not suppress the corresponding problems or make the command successful. For a current binding, `readable_handoff` preserves the reviewed prose for inspection. `blocked_by_health` is true whenever lifecycle health is not executable, and `executable_handoff` is non-null only when both Handoff freshness and lifecycle health permit execution.

Bare `task resume` reports `no_live | single_live | ambiguous`. `task resume <TASK_ID>` is the read-only selection form for one returned live candidate; it does not persist a current-task pointer or select terminal/archived history.

`task block` and `task cancel` require exactly one of `--reason` or `--reason-file`. Their `data.reason` is the whitespace-normalized transition intent and `data.reason_source` is `argument` or `file`. Neither command changes `## Verification`; both append one UTC-stamped Execution Log entry. `task cancel` reports complete `data.cancel_gate.residue_paths` and `baseline_conflicts` arrays, including when dirty cancellation is rejected.

## Problem object

```json
{
  "severity": "error",
  "code": "annotation_required",
  "message": "file matches coverage rule: matched coverage pattern src/**",
  "path": "repos/src/service.py"
}
```

`path` is optional. Codes are intended for agents and MCP wrappers; messages are for humans.

Repo-aware payloads should include repository context instead of overloading `path`:

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

`path` inside file entries remains repo-relative. `workspace_path` is workspace-root-relative when a caller needs a clickable location.

Command results that could be mistaken for broader readiness carry an explicit scope. `task.finish` reports `closure_scope: "task"` and `product_readiness: "not_evaluated"`. The `repoctl-release` field gate reports `scope: "workspace_control_plane"`, `applicability: "repoctl_release"`, and `product_readiness: "not_evaluated"`.

## v0.9.0 Context receipt migration

v0.9.0 is a breaking release for Context result-receipt consumers. The v0.8.0 default `data.result_receipt.selectable` list is not available through a compatibility flag or duplicate field. Consumers must use `data.result_receipt.compact.representative_citations[*].primary_citation` for evidence visible in the bounded response. To select a manifest member omitted from that projection, repeat the same Context command with `--full --json` and read `data.result_receipt.manifest.items`. The producer, typed request, `result_id`, and `receipt_digest` retain their meanings. Graph query receipts keep their command-specific flat `selectable` surface.

`task.doctor` and `task.finish` report current changed-Chosen structured-check coverage under `data.structured_verification`, including status and required, passed, missing, and nonpassing counts. Coverage uses the last appended record for each exact current subject version; earlier and repeated identical records remain immutable audit history but do not keep a later passed rerun nonpassing. While a task is live, doctor owns the complete union under `data.action_inputs.unverified_chosen_subjects` and may partition it into `missing_structured_verification_subjects` and `nonpassing_structured_verification_subjects`; either partition can emit a `task_verification_add` action. Missing evidence remains advisory. Any latest nonpassing record blocks doctor readiness and finish until a later passed record for that version recovers it. Finish adds a bounded `unverified_subjects` audit list with its count and truncation flag.

`task.doctor` reports Task/outcome scope identity under `data.discovery_outcome_alignment`. A recorded outcome is `aligned` only when repository identity and the complete Task Chosen and outcome `active_chosen` path sets agree. `invalid_task_chosen_values` contains every explicit Task value that cannot be a canonical workspace-relative path; these values are never treated as absent. A mismatch reports both one-sided path sets, sets structured-verification status to `scope_mismatch`, makes lifecycle health unhealthy, and blocks finish with `discovery_task_chosen_invalid`, `discovery_outcome_chosen_mismatch`, `discovery_outcome_chosen_invalid`, or `discovery_outcome_repository_mismatch`. The repair action returns to `task discovery add --replace-chosen`; repoctl never rewrites either owner by guessing which side was intended.

`task.doctor` and `task.finish` use the same closure gates. If current repository changes fall outside active Chosen scope, both report `actual_changes_outside_chosen` and the complete sorted set under `data.action_inputs.unchosen_actual_paths`; doctor sets `finish_ready: false`. The associated `task_scope_review` action is an explicit decision with `add_to_chosen | revert_change | move_to_follow_up` choices, not a fabricated shell command.

For `task verification add`, `--subject` refers to an existing Discovery subject or claim surface. Every verification mutation requires current task-start evidence. Repeated `--artifact` inputs are a separate started-root-task-only path for existing canonical workspace-relative files outside `repos/**`; they do not create product repository identity, Chosen scope, or ordinary hot corroboration. A `todo` task may record ordinary Discovery before start, but a `doing` or `blocked` task cannot mutate Discovery without a current baseline. Missing current task-start evidence or a mismatch between current Task repository classification and the immutable start scope returns `transition_evidence_incomplete` before Task or Discovery outcome bytes change.

For a live task, `task.show` and `task.doctor` may include `data.decomposition_advisory`. It is emitted only when the current Chosen subject count exceeds the existing `compact_path_limit`, at least two prior Discovery episodes are sealed, and at least two structured verification records exist. The object reports those counts and reason codes. Warning code `task_decomposition_recommended` offers only a task-boundary review path: it does not claim that milestones are semantically independent, infer a parent, auto-create a task, or mutate current scope.

## Compact projections

Compact task responses retain authoritative counts while bounding path arrays. Each bounded array has a matching count and truncation field, such as `baseline_conflicts`, `baseline_conflict_count`, and `baseline_conflicts_truncated`. Full task state remains available from the non-summary task view and machine-owned task evidence.

Context result receipts use a versioned public projection rather than returning the stored flat manifest in every compact response:

```json
{
  "schema": "repoctl.repository-understanding.result-receipt-projection",
  "schema_version": 1,
  "view": "compact",
  "producer": "context",
  "result_id": "sha256:...",
  "receipt_digest": "sha256:...",
  "request": {"kind": "context_query", "query": "owner", "mode": "auto"},
  "compact": {
    "representative_citations": [
      {
        "group": "likely_change_surface",
        "primary_citation": {"authority": "source", "ref": "repos/src/owner.py"}
      }
    ],
    "visible_item_count": 1,
    "cited_item_count": 1,
    "manifest_member_count": 1
  },
  "manifest": {
    "selectable_count": 12,
    "omitted_count": 11,
    "omitted_by_authority": {"graph": 11},
    "full_available": true
  }
}
```

The default projection size is bounded by visible compact evidence, not hidden manifest cardinality. `--full --json` changes `view` to `full` and adds the complete immutable membership at `manifest.items`; it does not change `result_id`, `receipt_digest`, request, compact citations, counts, or stored receipt bytes. Discovery may select a default `primary_citation` or an exact full `manifest.items` member. A representative citation outside the stored manifest is a typed `result_receipt_projection_invalid` failure. Graph query receipts retain their command-specific flat `selectable` surface.

`field-gate run repoctl-release --json` returns the compact gate view by default: gate status, scalar summary values, problem/warning counts and codes, and the run digest. `--full --json` exposes child commands and nested diagnostic summaries. `--output` always writes the full digest-verifiable artifact even when stdout uses the compact view.

Repository diagnostics separate stable targets from unbound candidates:

```json
{
  "placement": "collection",
  "registry_ready": false,
  "targets": [],
  "candidates": [
    {
      "path": "repos/web",
      "suggested_id": "web",
      "identity_status": "unbound"
    }
  ]
}
```

`suggested_id` is not a stable `repo_id` until `repoctl repo adopt` pins it into `docs/repoctl.json`.

## next_actions rules

`next_actions` are read-only guidance:

- They never perform recovery automatically.
- They must not infer task scope from natural language.
- They may include `command` or `path` for the user's next explicit action.
- Task create/start responses place a Handoff review/bind safety prerequisite before Discovery guidance so compact text mode, which renders the first action, cannot hide it while JSON retains the remaining actions.
- When `resume_guidance.handoff.generated_template` is true, replacement is the first recovery action and `task_handoff_bind` actions are omitted because the unchanged generated body is not bindable.
- Actions that require a user-owned decision may include a stable `kind`, a `source` evidence path, a `target_ref` path to one complete response-owned string list, and enum `choices`. Commands use placeholders for those choices rather than guessing ownership or scope.
- Every `target_ref` must resolve to a non-empty, untruncated string list in the same envelope. Actions do not duplicate that list under `targets` or expand every path into the command string.
- They are allowed to be incomplete; `problems` remain authoritative.

Compact domain summaries may remain bounded. When a decision action needs the complete set, `data.action_inputs` is the single untruncated owner. A baseline conflict action uses `kind: "baseline_ownership_resolution"`, `target_ref: "data.action_inputs.baseline_conflicts"`, and choices `task | preexisting`. A Chosen-scope action is emitted only for actual changes outside Chosen and uses `target_ref: "data.action_inputs.unchosen_actual_paths"` with choices `add_to_chosen | revert_change | move_to_follow_up`. `unused_chosen_paths` remains informational scope data and does not produce a scope-resolution action.

Compact task change summaries use typed temporal state. `repo_head_state` is `commit | unborn | unavailable | not_applicable`; `repo_head` exists only for a commit. `observed_since_baseline` is `observed | baseline_missing | unavailable | not_applicable`. These fields replace ambiguous public booleans for baseline availability and unborn repositories; internal finish evidence may still store exact baseline facts in completion receipts.

## MCP implication

Future MCP tools must call repoctl handlers or consume this JSON contract. They must not parse human stdout, mutate `.repometa` directly, or bypass task/Board/archive gates.
