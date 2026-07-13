# repoctl Graph contract

`repoctl graph build --json` emits a read-only derived snapshot for one explicit product repository.

Graph is not authoritative. Source authorities remain repo registry, code index, `.repometa`, and structured task completion receipts.

## Command

```bash
./scripts/repoctl graph build --repo-id main --json
```

Direct single-repo layout may omit `--repo-id` when `repos/.git` is the only target. Configured multi-repo layouts must pass `--repo-id`.

`graph query` builds the same in-memory snapshot and filters it through typed selectors:

```bash
./scripts/repoctl graph query --repo-id web --file src/app.py --json
./scripts/repoctl graph query --repo-id web --topic auth --json
./scripts/repoctl graph query --repo-id web --import axios --json
./scripts/repoctl graph query --repo-id web --symbol validate_token --json
./scripts/repoctl graph query --repo-id web --symbol validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id web --callers-of validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id web --callees-of login --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id web --impact-file services/token_service.py --depth 2 --json
./scripts/repoctl graph query --repo-id web --impact-symbol issue_token --in-file services/token_service.py --depth 2 --json
```

Query does not parse `graph build` stdout, require a snapshot file, or use a database.

Exactly one primary selector is required: `--file`, `--topic`, `--import`, `--symbol`, `--callers-of`, `--callees-of`, `--impact-file`, or `--impact-symbol`. `--in-file` narrows symbol selectors. `--depth` bounds impact traversal.

## Snapshot

```json
{
  "schema": "repoctl.graph.snapshot",
  "schema_version": 1,
  "authoritative": false,
  "repository": {
    "id": "main",
    "path": "repos",
    "identity_source": "reserved"
  },
  "capabilities": [
    "anchor",
    "artifact",
    "change_event",
    "cross_file_import_calls",
    "file",
    "import_ref",
    "import_resolution",
    "repository",
    "same_file_calls",
    "symbol",
    "task",
    "topic"
  ],
  "sources": [],
  "completeness": {
    "status": "partial",
    "capabilities": {
      "source_inventory": "complete",
      "file_inventory": "complete",
      "imports": "complete",
      "symbols": "partial",
      "calls": "partial",
      "task_history": "complete"
    },
    "provider_coverage": {
      "symbols": {
        "status": "partial",
        "eligible_paths": ["src/app.py", "scripts/run.sh"],
        "analyzed_paths": ["src/app.py"],
        "unsupported_paths": ["scripts/run.sh"],
        "failed_paths": [],
        "evidence_level": "precise"
      }
    },
    "inventory_complete": true,
    "identity_collisions": 0,
    "metadata_store_valid": true,
    "receipt_set_complete": true,
    "index_truncated": false,
    "code_facts_complete": true,
    "parse_error_count": 0,
    "provider_failures": []
  },
  "nodes": [],
  "edges": [],
  "snapshot_digest": "sha256:..."
}
```

## Node Kinds

```text
repository
file
import_ref
topic
task
change_event
artifact
symbol
anchor
```

Each node has an opaque `id` and a typed `identity` object. Clients must not split `id` strings to recover typed fields.

Identity rules:

```text
repository = repo_id
file       = repo_id + normalized repo-relative path
topic      = repo_id + exact topic name
import_ref = repo_id + language + raw import string
task       = task_id from completion receipt
symbol     = repo_id + provider + provider_symbol_id
anchor     = repo_id + provider + source range
```

## Edge Kinds

```text
CONTAINS
DECLARES_IMPORT
HAS_TOPIC
TASK_RECORDED_CHANGE
CHANGE_AFFECTED_FILE
TASK_VERIFIED_BY
DEFINES
ANCHORS
RESOLVES_TO
IMPORTS_FILE
CALLS
```

`DECLARES_IMPORT` points to `import_ref`, not to file, module, package, or symbol. Resolvers may add `RESOLVES_TO` without changing this meaning.

`RESOLVES_TO` points from an `import_ref` node to an unambiguous resolved file node. It is provider evidence, not package-manager or runtime inference.

`IMPORTS_FILE` points from the importing file node to the resolved imported file node. It is added only when resolution is unambiguous.

`CALLS` points from a precise provider symbol node to another precise provider symbol node. Current support covers Python provider-confirmed same-file calls, same-class method calls, and selected cross-file calls through imported Python functions. String matching alone must not create `CALLS`.

All semantic providers consume the same policy-eligible Code Index entry set. `classification: excluded` files may remain inventory nodes, but they do not produce source parsing, `DEFINES`, `ANCHORS`, `CALLS`, `RESOLVES_TO`, or `IMPORTS_FILE` evidence. `excluded_override` is an annotation-policy exemption and remains eligible for semantic analysis.

Python `CALLS` resolution follows lexical scopes. Nested function, class, lambda, and comprehension bindings are not attributed to the wrong scope. Parameters and local assignments/imports shadow outer symbols; module imports and simple module aliases may resolve calls; `global` and `nonlocal` declarations are honored; ambiguous or order-unsafe bindings fail closed.

Impact and caller/callee queries consume `CALLS` and `IMPORTS_FILE` evidence that already exists in the snapshot. They must not create new call edges by name matching query strings.

`HAS_TOPIC` uses repo-local topic nodes. Same topic text in two repositories is not the same graph entity.

Task edges are produced only from structured task completion receipts under `docs/tasks/.repoctl-state/completions/`. Graph must not parse task Markdown, verification prose, or diff summaries to infer task/file relations.

`working_tree_diff` evidence has `attribution: task_working_tree`. `committed_range` evidence has `attribution: range_observed`; Graph and Context must describe those paths as files observed in the completion range, not as task-owned commits or task-owned changes. One invalid receipt makes only `task_history` partial and does not remove current file/import evidence or other valid receipts.

repoctl accepts `committed_range` only when `start_head` is an ancestor of `observed_head`. Branch switches, resets, or rebases that break this ancestry block finish with `repo_history_rewritten`. Committed-range evidence is not combined implicitly with task-new working-tree changes.

Completion receipt shape:

```json
{
  "schema": "repoctl.task.completion",
  "schema_version": 2,
  "task_id": "T-...",
  "repo_id": "web",
  "status": "done",
  "task_path_at_completion": "docs/archive/tasks/T-...md",
  "content_sha256": "sha256:...",
  "changed_entries": [
    {
      "change": "modified",
      "path": "src/app.py",
      "old_path": ""
    }
  ],
  "repo_evidence": {
    "mode": "committed_range",
    "attribution": "range_observed",
    "start_head": "...",
    "observed_head": "...",
    "diff_fingerprint_sha256": "sha256:..."
  },
  "verification": {
    "source": "task_section",
    "source_sha256": "sha256:...",
    "normalized_sha256": "sha256:...",
    "stored_sha256": "sha256:...",
    "truncated": false
  }
}
```

Symbol and anchor edges are produced only by precise providers. Current v1 provider support is `python_ast`. Name-only `facts.index.symbol_names` values must not be treated as symbol identities.

## Provenance

Each edge has:

```text
assertion
source
```

v1 assertion values:

```text
observed
declared
default
recorded
resolved
```

Reserved future assertion values:

```text
inferred
```

Graph facts keep source namespaces separate:

```text
facts.index       code-index observed facts
facts.annotation  .repometa annotation declared facts
facts.policy      .repometa policy default facts
facts.receipt     task completion recorded facts
facts.provider    precise provider resolved facts
```

## Determinism

The same inputs must produce the same canonical snapshot digest.

Rules:

- nodes are ordered by `id`
- edges are ordered by `kind`, `from`, `to`, `assertion`, `source`
- source records are ordered by `kind`, `assertion`, `digest`
- list attributes are deduped and sorted unless order is semantically meaningful
- `snapshot_digest` is computed with `snapshot_digest` omitted
- no generated timestamp appears in the canonical body

## Compatibility

- v1 clients may ignore unknown node kinds, edge kinds, facts, and attributes.
- Existing v1 kind and edge semantics must not change.
- Provider additions must add facts or edges instead of overwriting existing source facts.
- Storage backend is not part of this contract.
- Breaking identity or edge semantic changes require `schema_version: 2`.

## Query Result

`graph query` returns a derived subgraph:

```json
{
  "repository": {
    "id": "web",
    "path": "repos/web",
    "identity_source": "pinned"
  },
  "snapshot_digest": "sha256:...",
  "query": {
    "type": "file",
    "path": "src/app.py"
  },
  "query_status": "not_found",
  "matches": [],
  "nodes": [],
  "edges": [],
  "paths": [],
  "completeness": {},
  "warnings": []
}
```

Query selectors are exact typed selectors. Clients must not pass an `id` string and expect repoctl to split it.

`matches` contains the selector's direct node candidates. `paths` contains ordered evidence for caller, callee, and impact traversal; each path includes `from`, `edge`, `to`, `reason`, and `source`.

Simple symbol names are fail-closed. If a symbol selector matches multiple precise symbols, `graph query` exits nonzero with `graph_query_ambiguous_symbol` and returns candidate matches with path, qualified name, symbol kind, provider, and source range so the caller can retry with `--in-file` or a qualified name.

Unsupported or incomplete provider coverage is reported through `completeness` and `warnings`; query must not claim a complete call graph when only file-level import impact is available.

Capability coverage values are `complete`, `partial`, `unsupported`, or `unavailable`. They are computed from provider execution over eligible, analyzed, unsupported, and failed paths rather than a language warning allowlist. `evidence_level` separately describes whether emitted evidence is precise or conservative; it does not claim whole-language exhaustiveness.

Query outcome and evidence completeness are separate axes:

| `query_status` | JSON `ok` | Exit | Meaning |
|---|---:|---:|---|
| `found` | `true` | 0 | A supported query found direct matches. |
| `not_found` | `true` | 0 | No direct match was found in the available evidence. |
| `unsupported` | `false` | 1 | No provider/capability exists for this query. |
| `unavailable` | `false` | 1 | A defined provider could not produce evidence for this run. |

Invalid selectors, invalid paths, and ambiguous symbols are ordinary problems, not `query_status` values. `not_found` with `completeness.status: partial` means only "not found in currently available evidence"; it does not prove absence.
