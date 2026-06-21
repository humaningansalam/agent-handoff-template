# repoctl Graph v0 roadmap

## Purpose

Add a deterministic, read-only repository evidence graph on top of existing repoctl authorities.

Graph v0 must help agents inspect repository structure without becoming a new source of truth for tasks, Board state, `.repometa`, or repository identity.

## Position

repoctl Graph is:

- a derived repository evidence snapshot
- scoped by explicit `repo_id`
- built from code index facts, `.repometa` metadata, task completion receipts, and precise providers
- storage-neutral JSON first
- deterministic and safe for machine clients

repoctl Graph is not:

- a Code Property Graph
- a GraphRAG knowledge extractor
- a graph database product
- a task authority
- a `.repometa` authority
- an import resolver
- a symbol intelligence provider

## Core Rules

- Graph is read-only. It never mutates `.repometa`, tasks, Board, Backlog, or repo registry config.
- Graph uses `RepoTarget` from the repo registry. It must not infer repository identity from path strings or prose.
- Configured multi-repo workspaces require explicit `--repo-id`.
- Unconfigured collection layouts cannot build Graph snapshots.
- Snapshot output is derived state. Source authorities remain code index, `.repometa`, repo registry, and task completion receipts.
- Unknown node kinds, edge kinds, and attributes must be safely ignorable by v1 clients.
- Existing v1 node and edge semantics must not change. New providers add new facts or edges instead of redefining old ones.

## Current Status

Graph v0 is the delivered baseline for fast repository understanding. It is not the final Graph product.

Delivered v0 scope:

- repository, file, import_ref, and topic graph
- deterministic build/query JSON
- repo_id-scoped multi-repo routing
- `.repometa` policy and annotation provenance
- task completion receipt evidence
- Python AST symbol and anchor provider
- release manifest inclusion
- regression tests for corrupt receipt rejection, deleted/renamed file evidence, relative imports, nested functions, and query normalization

Stable core node kinds:

```text
repository
file
import_ref
topic
```

Additional v0 provider node kinds:

```text
task
change_event
artifact
symbol
anchor
```

Stable core edge kinds:

```text
CONTAINS
DECLARES_IMPORT
HAS_TOPIC
```

Additional v0 provider edge kinds:

```text
TASK_RECORDED_CHANGE
CHANGE_AFFECTED_FILE
TASK_VERIFIED_BY
DEFINES
ANCHORS
```

## Outside v0

- No import resolution.
- No task Markdown parsing.
- No Graph DB, SQLite cache, or persistent store.
- No llmwiki integration.

## Future Graph Expansion Guardrail

Do not treat this section as the next scheduled project. Graph v0 can stop here until there is a concrete need for more accurate cross-file code intelligence.

If Graph is expanded later for more accurate repository understanding, the work should be resolver/provider accuracy, not MCP or a knowledge-base layer.

Order when that expansion is intentionally scheduled:

1. Add deterministic import resolution.
   - Start with Python relative and package imports.
   - Add `import_ref RESOLVES_TO file` only when resolution is unambiguous.
   - Keep unresolved raw `import_ref` nodes instead of guessing.
2. Add JS/TS relative import resolution.
   - Start with `./` and `../` imports.
   - Add `tsconfig paths`, workspace package, and package export handling only with tests.
3. Add reference/call provider edges.
   - Add `REFERENCES` and `CALLS` only for provider-confirmed facts.
   - Do not infer calls from string matching.
4. Add incremental/cache support only after build cost becomes a measured problem.

MCP, llmwiki, or other consumers can read Graph later, but they do not make Graph more accurate. Do not use them as a substitute for resolver/provider accuracy when the objective is better repository understanding.

## Identity Contract

Every node has both an opaque `id` and a typed `identity` object.

Clients must not parse `id` strings to recover typed fields. Query commands must use typed selectors such as `--repo-id`, `--file`, `--topic`, or `--import`.

Identity rules:

```text
repository = repo_id
file       = repo_id + normalized repo-relative path
topic      = repo_id + exact topic name
import_ref = repo_id + language + raw import string
```

IDs use canonical percent-encoding for path and value components.

Example:

```json
{
  "id": "repo:web:file:src%2Fapp.py",
  "kind": "file",
  "identity": {
    "repo_id": "web",
    "path": "src/app.py"
  }
}
```

Topics are repo-local:

```text
repo:web:topic:auth
repo:api:topic:auth
```

Any future organization-wide concept mapping must use additive concept nodes or edges. It must not merge repo-local topics by name.

## Provenance Contract

Graph facts keep source provenance separate.

Assertion classes:

```text
observed   code index observed the fact
declared   .repometa annotation declared the fact
default    .repometa policy default applied the fact
recorded   task completion receipt recorded the fact
resolved   future resolver/provider resolved the fact
inferred   future heuristic or LLM inferred the fact
```

v0 uses only:

```text
observed
declared
default
```

Do not add numeric confidence values until a producer has a calibrated basis for them.

File facts must keep source namespaces separate:

```json
{
  "facts": {
    "index": {
      "language": "python",
      "classification": "annotated",
      "symbol_names": ["App", "run"],
      "call_names": ["client.fetch"],
      "dependencies": ["client"],
      "observed_effects": ["net"],
      "parse_status": "ok"
    },
    "annotation": {
      "role": "service",
      "purpose": "serve the application",
      "topics": ["auth"],
      "declared_effects": ["net"],
      "caution": []
    },
    "policy": {
      "areas": ["backend"],
      "topics": ["service"]
    }
  }
}
```

Important distinctions:

```text
observed_effects != declared_effects
annotation topics != policy topics
symbol_names != symbol entities
raw imports != resolved modules/files/packages
```

## Snapshot Shape

```json
{
  "schema": "repoctl.graph.snapshot",
  "schema_version": 1,
  "authoritative": false,
  "repository": {
    "id": "web",
    "path": "repos/web",
    "identity_source": "pinned"
  },
  "capabilities": [
    "repository",
    "file",
    "import_ref",
    "topic"
  ],
  "sources": [
    {
      "kind": "code_index",
      "assertion": "observed",
      "digest": "sha256:..."
    },
    {
      "kind": "repometa_annotation",
      "assertion": "declared",
      "digest": "sha256:..."
    },
    {
      "kind": "repometa_policy",
      "assertion": "default",
      "digest": "sha256:..."
    }
  ],
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

`snapshot_digest` is computed from the canonical snapshot body with `snapshot_digest` omitted.

## Determinism

The same inputs must produce byte-equivalent canonical JSON.

Rules:

- node order: `id`
- edge order: `kind`, `from`, `to`, then stable attributes
- list attributes: dedupe and sort unless order is semantically meaningful
- source order: `kind`, `assertion`, `digest`
- canonical JSON: `sort_keys=True` with fixed separators
- no `generated_at` in the canonical snapshot body
- no environment-specific absolute paths in node identity

## Completeness

Graph build must call code index with no output limit.

If code index reports truncation, Graph build fails. A truncated Graph snapshot is not acceptable.

Parse errors do not automatically fail the build:

- keep file nodes
- set `code_facts_complete=false`
- increment `parse_error_count`
- preserve per-file `parse_status` and `parse_error`

## Module Boundaries

Planned files:

```text
tools/repoctl/graph_model.py
  GraphNode
  GraphEdge
  GraphSnapshot
  ID encoding
  canonical serialization
  digest calculation
  validation

tools/repoctl/graph.py
  target resolution integration
  code-index provider integration
  repometa provider integration
  graph fragment merge
  build_graph()

tools/repoctl/meta.py
  read-only metadata facts provider that preserves policy/annotation provenance

tools/repoctl/cli.py
  graph argparse commands
  JSON envelope
  human output
```

Graph must not directly parse `.repometa` shard internals when the `meta` module can provide the needed read-only facts.

## Metadata Provider Requirement

Add a read-only provider in `meta.py` before Graph consumes metadata.

Target shape:

```python
@dataclass(frozen=True)
class RepoMetadataFacts:
    path: str
    workspace_path: str
    classification: str
    areas: tuple[str, ...]
    policy_topics: tuple[str, ...]
    annotation: dict[str, Any] | None
```

```python
def read_metadata_facts(
    root: Path,
    *,
    target: RepoTarget,
) -> tuple[list[RepoMetadataFacts], list[Problem], dict[str, Any]]:
    ...
```

This provider must keep policy topics separate from annotation topics. Graph must not consume `FileClassification.default_topics` as a combined topic source.

## PR Plan

### PR 1: Graph Core

Deliver:

- `repoctl graph build`
- deterministic read-only snapshot JSON
- repository/file/import_ref/topic nodes
- CONTAINS/DECLARES_IMPORT/HAS_TOPIC edges
- provenance-preserving metadata integration
- graph contract docs
- ADR
- tests
- release manifest update

Files:

```text
docs/adr/repoctl-graph-v0.md
docs/contracts/repoctl-graph-contract.md
tools/repoctl/graph_model.py
tools/repoctl/graph.py
tools/repoctl/meta.py
tools/repoctl/cli.py
tests/repoctl/test_graph.py
repoctl-upgrade-manifest.json
README.md
```

Acceptance:

- `./scripts/repoctl graph build --repo-id main --json` works for direct `repos/.git`.
- Configured multi-repo builds only the selected repo.
- Configured multi-repo without `--repo-id` fails.
- Unconfigured collection layout fails.
- Raw imports become `import_ref` nodes, not file/module/package nodes.
- Annotation topics and policy topics keep separate provenance.
- `.repometa` files are unchanged by graph build.
- Same inputs produce byte-equivalent canonical snapshots.
- Code index truncation fails graph build.
- Parse errors are reflected in completeness while file nodes remain.
- ID encoding handles `:`, `/`, spaces, and Unicode without collisions.

### PR 2: Graph Query

Deliver in-memory queries over `GraphSnapshot`.

Commands:

```bash
./scripts/repoctl graph query --repo-id web --file src/app.py --json
./scripts/repoctl graph query --repo-id web --topic auth --json
./scripts/repoctl graph query --repo-id web --import axios --json
```

Rules:

- Query uses `build_graph()` directly.
- Query does not parse stdout JSON from `graph build`.
- Query does not require a DB or snapshot file.
- Query uses typed selectors, not `id` string splitting.

### PR 3: Task Completion Receipts

Deliver structured task completion receipts before adding task graph edges.

Receipt shape:

```json
{
  "schema": "repoctl.task.completion",
  "schema_version": 1,
  "task_id": "T-...",
  "repo_id": "web",
  "status": "done",
  "changed_entries": [
    {
      "change": "modified",
      "path": "src/app.py",
      "old_path": ""
    }
  ],
  "verification": {
    "archive_path": "docs/archive/tasks/T-...md",
    "content_sha256": "..."
  }
}
```

Rules:

- Receipt is written in the same task finish transaction as archive and Board updates.
- Receipt handles rename/delete/copy semantics explicitly.
- Graph task provider consumes receipts only.
- Graph must not parse task Markdown or diff summaries to infer changed files.

Additive graph concepts after receipts:

```text
task
change_event
artifact
TASK_RECORDED_CHANGE
CHANGE_AFFECTED_FILE
TASK_VERIFIED_BY
```

### PR 4: Precise Code Provider

Deliver symbol and anchor graph facts through a real provider, not current name lists.

Possible providers:

- SCIP
- LSP-backed extractor
- language-specific precise indexer

Additive graph concepts:

```text
symbol
anchor
package
DEFINES
REFERENCES
RESOLVES_TO
CALLS
```

Rules:

- Existing `symbol_names` remain observed file facts.
- Existing `import_ref` nodes remain raw import facts.
- Resolved relations are additive and do not replace observed raw facts.

## v0 Regression Tests

```text
test_graph_build_direct_repo_uses_main
test_graph_build_configured_multi_requires_repo_id
test_graph_build_configured_multi_includes_only_selected_repo
test_graph_build_unconfigured_collection_fails
test_graph_imports_are_raw_import_refs
test_graph_topics_keep_policy_and_annotation_provenance
test_graph_build_does_not_mutate_repometa
test_graph_snapshot_is_byte_stable
test_graph_index_truncation_fails
test_graph_parse_error_keeps_file_node_and_marks_completeness
test_graph_id_encoding_avoids_collisions
```

## v0 Completion Criteria

Graph v0 is complete for this roadmap when:

- Build, query, task receipt, and precise provider behavior are implemented and tested.
- Graph v1 contract remains backward compatible across query, task receipt, and provider additions.
- No Graph implementation mutates source authorities.
- No Graph provider infers repository identity from prose or path strings outside the repo registry.
- No task graph edge is produced without structured task receipt evidence.
- No symbol identity is produced from name-only `code_index` facts.
- All release/upgrade managed files are included in `repoctl-upgrade-manifest.json`.
- `uv run pytest -q` passes.
