# repoctl Graph contract

`repoctl graph build --json` materializes a derived navigation snapshot for one product repository. Graph helps agents find code, callers, tests, configuration relations, completed work, and saved Knowledge. It does not choose Task scope or replace source authority.

Authoritative inputs remain the repository registry, product files, `.repometa`, Task completion receipts, and durable Knowledge records. Snapshot and query digests identify derived content; they do not prove that a command or test ran.

## Commands and state

```bash
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl graph build --repo-id main --rebuild --json
./scripts/repoctl graph query --repo-id main --file src/app.py --json
./scripts/repoctl history rebuild --repo-id main --json
```

Direct single-repository layouts may omit `--repo-id`. Configured multi-repository layouts must select one repository explicitly.

The first build analyzes eligible files and writes a canonical snapshot, manifest, persistent SQLite evidence index, and one fixed result per semantic provider under `.repoctl-state/graph/<repo-id>/`. Later builds reuse unchanged provider results and update changed files plus semantic dependents. `--rebuild` discards reusable Graph/provider state. It does not scan or repair completion history.

`history rebuild` is the explicit recovery boundary for validating completion receipts and rebuilding the bounded completion catalogue. Normal Graph and Context commands do not enumerate the cold completion archive.

`graph query` reads the last materialized snapshot. It does not run providers, rescan product files, mutate Tasks, or create per-query receipts. Repeating the same query creates no query ledger.

Source relations do not require `.repometa`, completion history, or Knowledge. Missing optional enrichment is reported as unavailable or partial. Malformed state is reported with a typed recovery action rather than treated as absent.

## Selectors

Exactly one primary selector is required:

```text
--file
--topic
--import
--symbol
--callers-of
--callees-of
--impact-file
--impact-symbol
--task
--artifact
```

Examples:

```bash
./scripts/repoctl graph query --repo-id web --file src/app.py --json
./scripts/repoctl graph query --repo-id web --symbol validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id web --callers-of validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id web --impact-file services/token_service.py --depth 2 --json
./scripts/repoctl graph query --repo-id web --task T-... --json
./scripts/repoctl graph query --repo-id web --artifact docs/archive/tasks/T-...md --json
```

File selectors and `--in-file` accept a repository-relative path or a workspace-relative path prefixed by the selected repository path. If both forms resolve to different files, the query fails with `graph_query_ambiguous_path` and returns both canonical choices. A missing path returns at most three exact basename or suffix corrections.

Simple symbol names also fail closed. Multiple precise matches return `graph_query_ambiguous_symbol` with path, qualified name, symbol kind, provider, and source range. Retry with `--in-file` or a qualified name.

## Materialized model

Snapshot schema remains `repoctl.graph.snapshot` version 1. Node IDs are opaque; clients use typed `identity` fields and must not split IDs.

Node kinds:

```text
repository
file
import_ref
topic
task
change_event
artifact
document
knowledge
symbol
anchor
```

Core identities:

```text
repository = repo_id
file       = repo_id + normalized repository-relative path
topic      = repo_id + exact topic name
import_ref = repo_id + importer path + language + typed import occurrence
task       = task_id from a completion receipt
symbol     = repo_id + provider + provider_symbol_id
anchor     = repo_id + provider + source range
```

Edge kinds:

```text
CONTAINS
DECLARES_IMPORT
HAS_TOPIC
TASK_RECORDED_CHANGE
CHANGE_AFFECTED_FILE
TASK_VERIFIED_BY
TASK_CHANGED_FILE
DEFINES
ANCHORS
RESOLVES_TO
IMPORTS_FILE
CALLS
TESTS_FILE
USES_FILE
KNOWLEDGE_APPLIES_TO
KNOWLEDGE_SOURCED_FROM
```

`TASK_VERIFIED_BY` is a retained v1 edge name from a Task node to its bound completion artifact. It makes no claim about tests or Verification prose.

Each edge preserves `assertion` and `source`. Version 1 assertion values are `observed`, `declared`, `default`, `recorded`, and `resolved`; `inferred` is reserved. Fact namespaces stay separate:

```text
facts.index       code-index observations
facts.annotation  .repometa declarations
facts.policy      .repometa policy defaults
facts.receipt     Task completion records
facts.provider    precise provider results
```

## Source and provider relations

`DECLARES_IMPORT` targets an importer-scoped `import_ref`. Its identity preserves form, relative level, module, imported name, and raw text. `RESOLVES_TO` and `IMPORTS_FILE` exist only when a provider resolves one target.

Python resolution uses AST import structure and declared setuptools roots. It does not execute `setup.py`, mutate `sys.path`, or guess roots from directory names. Ambiguous modules, dynamic package attributes, and unsafe bindings produce no resolved edge.

`CALLS` joins precise provider symbols. String equality alone never creates a call edge. Python follows lexical scopes; TypeScript and JavaScript use the TypeScript compiler API; Dart uses `package:analyzer`; C# and Unity use Roslyn over project compilation units. Dynamic or ambiguous targets fail closed. Provider coverage reports the resulting limits.

`TESTS_FILE` requires both a typed test-role classification and a provider-resolved import or cross-file call to production code. Filename similarity alone is insufficient. Compact output does not duplicate the same endpoints as both `TESTS_FILE` and `IMPORTS_FILE`.

`USES_FILE` represents bounded syntax-resolved file dependencies. Providers recognize supported Docker, Compose, workflow, shell, SQL, configuration, and RPC relations. Dynamic paths or ambiguous objects produce no confirmed edge.

RPC facts keep `linked`, `unresolved`, `ambiguous`, or `incomplete` resolution. Only `linked` creates `USES_FILE`. Compatible unresolved targets may appear under non-authoritative `relationship_candidates`; they never appear as confirmed relations. Dart RPC discovery is analyzer-owned and has no token-scanner fallback.

All semantic providers consume the same policy-eligible Code Index entries. `classification: excluded` files may remain inventory nodes but produce no semantic edges. `excluded_override` remains eligible.

Incremental invalidation follows provider boundaries:

- Python and Dart refresh changed files and reverse import dependents.
- TypeScript and JavaScript refresh the affected `tsconfig.json` or `jsconfig.json` unit; unconfigured sources use reverse import dependents.
- C# refreshes the affected `.csproj` unit.
- Provider configuration or parser revision changes invalidate that provider.
- Deleted and renamed files remove old symbols, calls, and structured facts before merge.

Impact and caller/callee queries traverse only relations already stored in the snapshot. Query text never creates new dependency edges.

## Component projection

Component membership is an additive projection over current subjects and typed relations. An immutable provider must recognize a manifest and a declared name or module root. Package manifests, Unity assembly definitions, static Swift package/target roots, and static Gradle module mappings are supported. Directories, extensions, `.repometa` areas, and filenames do not create components or ownership.

Membership is a set, so overlapping root and nested components are retained. A crossing exists only when a fresh confirmed relation connects different membership sets. Query output includes only relevant memberships and crossings; there is no separate topology or owner ledger.

## Completion history

Graph reads Task relations from structured completion receipts under `docs/tasks/.repoctl-state/completions/`. It never parses Task Markdown, `## Verification`, or prose summaries to infer changed files.

Current receipts use `repoctl.task.completion` schema version 5:

```json
{
  "schema": "repoctl.task.completion",
  "schema_version": 5,
  "task_id": "T-...",
  "repo_id": "web",
  "status": "done",
  "completed_at": "20260811T120000Z",
  "started_at": "2026-08-11T11:45:00.123456Z",
  "completed_event_at": "2026-08-11T12:00:00.654321Z",
  "task_path_at_completion": "docs/archive/tasks/T-...md",
  "content_sha256": "sha256:...",
  "changed_entries": [],
  "repo_evidence": {
    "mode": "none",
    "attribution": "none",
    "start_head": "...",
    "observed_head": "...",
    "diff_fingerprint_sha256": "",
    "fingerprint_manifest": {},
    "ownership": {},
    "path_transitions": []
  }
}
```

Version 5 contains neither `verification` nor `discovery_outcome`. Receipt and artifact hashes protect stored history identity; they do not claim that a test ran or passed.

Allowed evidence pairs are:

```text
none              / none
working_tree_diff / task_working_tree
committed_range   / range_observed
```

`task_working_tree` may emit `TASK_CHANGED_FILE`. `range_observed` emits `TASK_RECORDED_CHANGE` and `CHANGE_AFFECTED_FILE` but never claims Task ownership through `TASK_CHANGED_FILE`.

Receipt filename, `task_id`, artifact identity, and `content_sha256` must agree. Current transition data must cover changed entries exactly when present. A malformed receipt makes Task history partial without removing valid source relations.

Legacy receipt schemas 2 through 4 remain read-only inputs. Their old `verification` and schema-4 `discovery_outcome` fields are validated only as part of their published shape. Graph does not copy those fields into current Task scope or create new legacy state.

The active snapshot includes only the finite hot completion catalogue. Exact `--task` and `--artifact` selectors validate a cold catalogue record and build a query-local projection without changing the snapshot. Historical artifact paths remain evidence; they are not current edit scope.

## Knowledge

`repoctl.knowledge.record` version 2 records are saved directly and become queryable without candidate or approval events. Graph uses the current, non-superseded heads from the bounded Knowledge projection. Older records and events stay preserved outside the projection until an agent migrates the useful conclusion.

`KNOWLEDGE_APPLIES_TO` is created only from an explicit `applies_to.paths` entry or a `current_source` reference that resolves to one current file in the selected repository. Prose, Task changed files, root documents, ambiguous paths, and cross-repository paths do not create applicability.

`KNOWLEDGE_SOURCED_FROM` retains source navigation. A later source change produces a warning but does not remove a saved decision or its explicit applicability from search and Graph. A replacement record supersedes the named older record.

## Context projection

Context is a separate consumer of the same snapshot. It passes typed file or provider-symbol anchors plus a fixed mode policy; Graph does not receive raw natural-language query text. Every requested anchor is reported as resolved, ambiguous, or unresolved. Exact ambiguity creates no traversal.

Context may form a bounded lexical file hypothesis before calling Graph. Confirmed Graph relations and lexical candidates remain distinct. Traversal direction and depth are fixed by mode, and merged relations retain per-origin distance.

Surviving anchors become top-level `graph_seed_refs` with their exact typed source identity and digest. These are read-only continuation hints. They do not define Chosen scope and are not persisted as query receipts. Context Pack may carry the same hints as a one-time view or export.

## Query result

Default query JSON is compact:

```json
{
  "repository": {"id": "web", "path": "repos/web", "identity_source": "pinned"},
  "snapshot_digest": "sha256:...",
  "query": {"type": "file", "path": "src/app.py"},
  "query_status": "found",
  "matches": [],
  "candidates": [],
  "paths": [],
  "relationship_candidates": [],
  "relationship_candidate_count": 0,
  "relationship_candidates_truncated": false,
  "result_digest": "sha256:...",
  "continuations": [],
  "relations": [],
  "completeness": {},
  "freshness": {},
  "warnings": []
}
```

`matches` contains direct typed matches. `candidates` contains at most three exact path corrections or ambiguity choices. `relationship_candidates` contains explicitly non-authoritative structured possibilities. `paths` and `relations` contain confirmed evidence with assertion/provider, confidence, completeness, and freshness.

`result_digest` identifies the returned query projection. No later command must re-submit it, and repoctl does not store it as lifecycle state.

`continuations` let clients traverse the compact result without parsing node IDs. They contain a typed selector, supported query types, and actions such as `graph.file`, `graph.callers_of`, `task.show`, `knowledge.show`, or `workspace.open`. A continuation is meaningful for the returned snapshot and may change after rebuild.

`--full` adds raw nodes, edges, provider coverage, analyzed paths, counts, and materialization diagnostics.

Query status and evidence completeness are separate:

| `query_status` | JSON `ok` | Exit | Meaning |
|---|---:|---:|---|
| `found` | `true` | 0 | Supported query found a direct match. |
| `not_found` | `true` | 0 | No match exists in the available evidence. |
| `ambiguous` | `false` | 1 | More than one exact identity is possible. |
| `unsupported` | `false` | 1 | No provider supports the requested capability. |
| `unavailable` | `false` | 1 | A defined provider could not produce evidence. |

`not_found` with partial completeness does not prove absence. Capability values are `complete`, `partial`, `unsupported`, or `unavailable`; `evidence_level` separately reports precise or conservative evidence.

## Freshness

Every query reports freshness. `current` means product file identities, root evidence, provider configuration, and Graph input versions match the manifest. `stale` returns `graph_snapshot_stale` and a `graph_refresh` action.

Stale endpoints are excluded from confirmed Graph relations, relationship candidates, component crossings, and Context Graph projections. Context may still show current file text from its live overlay. Rebuild before relying on changed relations.

Full freshness reports changed product paths, root paths, provider configuration, provider-owned stale paths, semantic dependents, and Graph input-version changes. Freshness checks reuse stored digests when path kind, mode, size, and modification time are unchanged.

## Determinism and schema changes

The same admitted inputs produce the same canonical snapshot digest:

- nodes sort by `id`
- edges sort by kind, endpoints, assertion, and source
- source records sort by kind, assertion, and digest
- unordered lists are deduplicated and sorted
- `snapshot_digest` is computed with that field omitted
- the canonical body contains no generated timestamp

Storage layout is not part of the public schema. A breaking identity or edge change increments the snapshot schema; the agent rebuilds derived Graph state with the current runtime.
