# repoctl Graph contract

`repoctl graph build --json` materializes a derived snapshot for one explicit product repository.

Graph is not authoritative. Source authorities remain repo registry, code index, `.repometa`, and structured task completion receipts.

The first build analyzes all eligible source files and materializes a persistent SQLite evidence index for source symbols, module text, documents, manifests, verification hints, and task artifacts. The manifest, snapshot, semantic-provider results, and evidence-index binding are admitted as one materialization; query and incremental build fail with a typed recovery action when any member is missing, invalid, or bound to another snapshot. Later builds compare Git/content identities and update only changed files plus the semantic dependents that can be affected by those changes. `graph query` reads the materialized snapshot and never runs a compiler provider or rescans product sources.

## Command

```bash
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl graph build --repo-id main --rebuild --json
```

Direct single-repo layout may omit `--repo-id` when `repos/.git` is the only target. Configured multi-repo layouts must pass `--repo-id`.

`--rebuild` discards reusable provider results and is reserved for explicit recovery or provider/schema changes. Normal updates omit it.

`graph query` filters the stored snapshot through typed selectors:

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
./scripts/repoctl graph query --repo-id web --task T-... --json
./scripts/repoctl graph query --repo-id web --artifact docs/archive/tasks/T-...md --json
```

Query never updates Graph state. If no snapshot exists, it returns `graph_snapshot_missing` and tells the caller to run `graph build`. Existing state that is unreadable, malformed, incomplete, incompatible, or bound to another repository identity is a typed hard failure and requires an explicit rebuild; it is never treated as an absent snapshot. Source changes become visible after the next explicit build; queries remain pinned to the returned `snapshot_digest` until then.

Materialized implementation state lives under `.repoctl-state/graph/<repo-id>/`. It contains one canonical snapshot, one manifest, and one fixed result file per semantic provider. It does not create per-query or per-file ledgers.

Incremental invalidation follows semantic boundaries:

- Python and Dart refresh changed files and their reverse import dependents.
- TypeScript/JavaScript refresh the affected `tsconfig.json`/`jsconfig.json` unit; unconfigured sources use reverse import dependents.
- C# refreshes the affected `.csproj` compilation unit.
- Provider configuration or provider input-version changes refresh that provider.
- Deleted and renamed files remove their old symbols and calls before updated facts are merged.

Exactly one primary selector is required: `--file`, `--topic`, `--import`, `--symbol`, `--callers-of`, `--callees-of`, `--impact-file`, `--impact-symbol`, `--task`, or `--artifact`. File selectors and `--in-file` accept exact normalized paths relative to the selected product repository; workspace-qualified aliases are not accepted. `--in-file` narrows symbol selectors. `--depth` bounds impact traversal.

Default build JSON contains the snapshot digest, node/edge counts, compact capability/provider status, materialization status, and updated-path counts. Provider path inventories and the raw snapshot are available only with `--full`.

Default query JSON is the compact agent-facing view: direct matches, decision-relevant relation or traversal paths, reusable continuations, node/edge counts by kind, displayed/omitted counts, summarized completeness, and freshness counts. Matches, paths, and continuations share one eight-selector display budget: direct-match selectors are reserved first, and a path is displayed only when every traversable endpoint has its exact typed continuation in the same response. Symbol endpoints include their repository-relative source path so repeated names remain unambiguous. `impact-file` displays cross-file `CALLS` and `IMPORTS_FILE` paths; file-local `DEFINES` and `ANCHORS` remain in the snapshot and are represented by edge counts rather than exhaustive default output. Use `--full --json` when exact stale paths, raw nodes/edges, or full provider diagnostics are required.

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
import_ref = repo_id + importer path + language + typed import occurrence
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

`DECLARES_IMPORT` points to an importer-scoped `import_ref`, not to file, module, package, or symbol. Its typed identity preserves form, relative level, module, imported name, and raw display text. Resolvers may add `RESOLVES_TO` without changing this meaning. `--import <raw>` may return several distinct occurrence nodes when the same text appears in different files or import forms.

`RESOLVES_TO` points from an `import_ref` node to an unambiguous resolved file node. It is provider evidence, not package-manager or runtime inference.

`IMPORTS_FILE` points from the importing file node to the resolved imported file node. It is added only when resolution is unambiguous.

Python absolute imports are resolved against the repository root and structured setuptools roots declared by `pyproject.toml` through `tool.setuptools.package-dir` or `tool.setuptools.packages.find.where`. repoctl does not execute `setup.py`, mutate `sys.path`, or guess source roots from directory names. If one module identity maps to more than one file, resolution fails closed.

Python resolution consumes AST-derived import occurrences with explicit `module`/`from` form, relative level, module name, and imported name; it does not recover those semantics by splitting the raw import string. A relative import resolves only when every possible configured module identity for the importer yields the same target. For `from package import name`, a certain package attribute takes precedence over a same-named submodule, a possible or dynamic package attribute fails closed, and submodule fallback is used only when the package attribute is absent. Callable definitions, aliases, and explicit `from` re-exports are propagated to a fixed point so direct and module-qualified calls share one exported-callable identity.

`CALLS` points from a precise provider symbol node to another precise provider symbol node. String matching alone must not create `CALLS`.

All semantic providers consume the same policy-eligible Code Index entry set. `classification: excluded` files may remain inventory nodes, but they do not produce source parsing, `DEFINES`, `ANCHORS`, `CALLS`, `RESOLVES_TO`, or `IMPORTS_FILE` evidence. `excluded_override` is an annotation-policy exemption and remains eligible for semantic analysis.

Provider support is compiler/analyzer backed:

- Python uses the stdlib AST with an explicit lexical-scope model.
- TypeScript and JavaScript use the TypeScript compiler API and checker-resolved call targets. A project-local compiler is preferred; an official bundled compiler is used when the repository has none.
- Dart uses `package:analyzer` resolved ASTs through an AOT helper and restricts package resolution to the selected product repository.
- C# and Unity use Roslyn `SemanticModel` over `.csproj` compilation units.

Python `CALLS` resolution follows lexical scopes. Nested function, class, lambda, and comprehension bindings are not attributed to the wrong scope. Parameters and local assignments/imports shadow outer symbols; module imports and simple module aliases may resolve calls; `global` and `nonlocal` declarations are honored; ambiguous or order-unsafe bindings fail closed.

A Python `CALLS` edge records the lexical target when that call expression executes; it is not a whole-program reachability proof that every enclosing control-flow path initializes the binding before invocation. The bounded provider does not evaluate version-dependent annotation expressions because the product interpreter version and `__future__` policy are not authoritative Graph inputs.

Each semantic provider declares its own capability evidence level and coverage gaps. Python call evidence is conservative because dynamic call targets are not exhaustive, so a successfully analyzed Python repository must not claim complete call coverage.

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

Symbol and anchor edges are produced only by semantic providers. Name-only `facts.index.symbol_names` values must not be treated as symbol identities.

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
  "paths": [],
  "continuations": [],
  "node_count": 0,
  "edge_count": 0,
  "relations": [],
  "completeness": {},
  "warnings": []
}
```

Query selectors are exact typed selectors. Clients must not pass an `id` string and expect repoctl to split it.

`matches` contains the selector's direct node candidates. `paths` contains ordered evidence for caller, callee, and impact traversal; each path includes `from`, `edge`, `to`, `reason`, and `source`.

`continuations` makes the subgraph traversable without parsing node IDs or provider-specific symbol IDs. Compact results contain a typed selector with normalized `value` and optional `in_file`, the supported follow-up `query_types`, and stable action enums such as `graph.file`, `graph.callers_of`, `task.show`, or `workspace.open`. The selector supplies identity and the bundle supplies `repo_id`, so actions do not repeat command arguments. `--full` also preserves the source node ID, kind, and label. Clients may repeatedly feed selectors into `graph query`; a continuation remains evidence for the returned `snapshot_digest`, not a durable locator after source changes.

Continuation coverage is:

```text
file         -> graph file / impact-file
symbol       -> graph symbol and capability-supported call/impact queries
import_ref   -> graph import
topic        -> graph topic
task         -> graph task / task show
artifact     -> graph artifact / workspace open
change_event -> owning graph task
```

`--task` returns the recorded task, its completion artifact, change events, and affected current or historical file identities. Receipt `task_path_at_completion` remains historical evidence; the artifact node and continuation use the task's current canonical path after a parent archive moves it. `--artifact` follows that current artifact back through its task and recorded file evidence. These selectors consume structured completion receipts only; they do not parse task Markdown prose.

Every query payload includes `freshness`. `current` means product file identities and root evidence identities still match the materialized manifest. `stale` includes exact `changed_paths` and `changed_root_paths` and emits `graph_snapshot_stale`; the stored result remains queryable as historical derived evidence but must not be presented as current. Rebuild explicitly before relying on changed relations. Root evidence probes reuse stored content digests when path kind, mode, size, and mtime are unchanged, so freshness checks do not reread every document or receipt body.

Simple symbol names are fail-closed. If a symbol selector matches multiple precise symbols, `graph query` exits nonzero with `graph_query_ambiguous_symbol` and returns candidate matches with path, qualified name, symbol kind, provider, and source range so the caller can retry with `--in-file` or a qualified name.

Unsupported or incomplete provider coverage is reported through `completeness` and `warnings`; query must not claim a complete call graph when only file-level import impact is available.

Capability coverage values are `complete`, `partial`, `unsupported`, or `unavailable`. They are computed from provider execution over eligible, analyzed, unsupported, and failed paths rather than a language warning allowlist. `evidence_level` separately describes whether emitted evidence is precise or conservative; it does not claim whole-language exhaustiveness.

Repository-level coverage may be `complete` when no path is eligible for that semantic capability. Per-language diagnostics have a different scope: a registered non-semantic language profile such as YAML reports `symbols_status: unsupported` and `calls_status: unsupported`, never `complete`. A semantic language with a defined provider that failed reports `unavailable`; a semantic language with no provider reports `unsupported`.

In `language_capabilities`, `provider_defined` means that the current materialization contains a semantic provider result declaring that language. Static language intent remains in the registered language profile's `semantic_source` and `capability` fields.

Query outcome and evidence completeness are separate axes:

| `query_status` | JSON `ok` | Exit | Meaning |
|---|---:|---:|---|
| `found` | `true` | 0 | A supported query found direct matches. |
| `not_found` | `true` | 0 | No direct match was found in the available evidence. |
| `unsupported` | `false` | 1 | No provider/capability exists for this query. |
| `unavailable` | `false` | 1 | A defined provider could not produce evidence for this run. |

Invalid selectors, invalid paths, and ambiguous symbols are ordinary problems, not `query_status` values. `not_found` with `completeness.status: partial` means only "not found in currently available evidence"; it does not prove absence.
