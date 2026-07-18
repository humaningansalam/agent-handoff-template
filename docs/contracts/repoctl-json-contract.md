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
- `command`: stable dotted command name where practical, such as `task.finish` or `meta.check`.
- `data`: command-specific structured result. It must be an object.
- `warnings`: advisory problem objects that do not fail the command.
- `problems`: error or warning objects with stable `code` values.
- `next_actions`: advisory recovery hints. They must not imply that repoctl changed state.

Command-specific values exist only under `data`. Commands must not mirror `task`, `result`, `repository`, counters, paths, or any other payload field at the top level for compatibility.
Serialization validates this boundary and rejects missing `command`, non-object `data`, or unknown top-level fields instead of silently relocating producer mistakes.

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

Command results that could be mistaken for broader readiness carry an explicit scope. `task.finish` reports `closure_scope: "task"` and `product_readiness: "not_evaluated"`. The release-candidate field gate reports `scope: "workspace_control_plane"`, `applicability: "repoctl_release_candidate"`, and `product_readiness: "not_evaluated"`.

## Compact projections

Compact task responses retain authoritative counts while bounding path arrays. Each bounded array has a matching count and truncation field, such as `baseline_conflicts`, `baseline_conflict_count`, and `baseline_conflicts_truncated`. Full task state remains available from the non-summary task view and machine-owned task evidence.

`field-gate run release-candidate --json` returns the compact gate view by default: gate status, scalar summary values, problem/warning counts and codes, and the run digest. `--full --json` exposes child commands and nested diagnostic summaries. `--output` always writes the full digest-verifiable artifact even when stdout uses the compact view.

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
- Actions that require a user-owned decision may include a stable `kind`, a `source` path into the response data, complete `targets`, and enum `choices`. Commands use placeholders for those choices rather than guessing ownership or scope.
- They are allowed to be incomplete; `problems` remain authoritative.

For example, a baseline conflict action uses `kind: "baseline_ownership_resolution"`, sources paths from `data.repo_changes.baseline_conflicts`, preserves every affected path in `targets`, and offers `task` or `preexisting`. A Chosen-scope action uses `kind: "task_scope_review"`, sources paths from `data.repo_changes.scope.unchosen_actual_paths`, preserves every affected path in `targets`, and offers `add_to_chosen`, `revert_change`, or `move_to_follow_up`.

## MCP implication

Future MCP tools must call repoctl handlers or consume this JSON contract. They must not parse human stdout, mutate `.repometa` directly, or bypass task/Board/archive gates.
