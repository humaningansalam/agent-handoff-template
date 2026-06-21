# repoctl Graph contract

`repoctl graph build --json` emits a read-only derived snapshot for one explicit product repository.

Graph is not authoritative. Source authorities remain repo registry, code index, `.repometa`, and future structured task receipts.

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
```

Query does not parse `graph build` stdout, require a snapshot file, or use a database.

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
  "capabilities": ["artifact", "change_event", "file", "import_ref", "repository", "task", "topic"],
  "sources": [],
  "completeness": {
    "inventory_complete": true,
    "index_truncated": false,
    "code_facts_complete": true,
    "parse_error_count": 0
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
```

`DECLARES_IMPORT` points to `import_ref`, not to file, module, package, or symbol. Future resolvers may add `RESOLVES_TO` edges without changing this meaning.

`HAS_TOPIC` uses repo-local topic nodes. Same topic text in two repositories is not the same graph entity.

Task edges are produced only from structured task completion receipts under `docs/tasks/.repoctl-state/completions/`. Graph must not parse task Markdown, verification prose, or diff summaries to infer task/file relations.

Completion receipt shape:

```json
{
  "schema": "repoctl.task.completion",
  "schema_version": 1,
  "task_id": "T-...",
  "repo_id": "web",
  "status": "done",
  "task_path": "docs/archive/tasks/T-...md",
  "archive_path": "docs/archive/tasks/T-...md",
  "content_sha256": "sha256:...",
  "changed_entries": [
    {
      "change": "modified",
      "path": "src/app.py",
      "old_path": ""
    }
  ],
  "verification": {
    "task_path": "docs/archive/tasks/T-...md",
    "archive_path": "docs/archive/tasks/T-...md",
    "content_sha256": "sha256:..."
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
```

Reserved future assertion values:

```text
resolved
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
  "nodes": [],
  "edges": []
}
```

Query selectors are exact typed selectors. Clients must not pass an `id` string and expect repoctl to split it.
