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
      "message": "task finish requires --verification-file",
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
- They are allowed to be incomplete; `problems` remain authoritative.

## MCP implication

Future MCP tools must call repoctl handlers or consume this JSON contract. They must not parse human stdout, mutate `.repometa` directly, or bypass task/Board/archive gates.
