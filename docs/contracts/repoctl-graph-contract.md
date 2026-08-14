# repoctl Graph contract

`repoctl graph build --json` materializes a derived snapshot for one explicit product repository.

Graph is not authoritative. Source authorities remain repo registry, source files, `.repometa`, structured task completion receipts, and explicitly reviewed Knowledge records.

The first build analyzes all eligible source files and materializes a persistent SQLite evidence index for source symbols, module text, documents, manifests, and verification hints. The manifest, snapshot, semantic-provider results, evidence-index binding, and bounded completion-catalogue identity are admitted as one materialization; query and incremental build fail with a typed recovery action when a required member is missing, invalid, or bound to another snapshot. Later builds compare Git/content identities and update only changed files plus semantic dependents that can be affected by those changes. `graph query` reads the materialized snapshot and never runs a compiler provider or rescans product sources. Ordinary build/freshness reads the catalogue head/checkpoint and committed finite hot projection; it never opens retained event sidecars or walks or hashes the completion-receipt archive. `repoctl history rebuild` is the separate explicit recovery boundary that owns full receipt enumeration.

## Implementation boundary

The implementation materializes the node/edge snapshot documented below and returns node-selector continuations. Graph build recognizes component declarations through an immutable manifest-provider registry, and Graph/Context query projections annotate current subjects and confirmed relations with set-aware component membership and crossings. There is no separate topology ledger or owner class. Discovery freezes exact canonical result members from the current flat producer receipt; the producer receipt itself remains a flat selection contract.

### Derived topology projection

Topology is an additive projection over canonical subjects and existing typed relations. Component identity exists only when a registered immutable provider recognizes a current manifest and reads a valid declared name or explicit module root. The shipped registry covers package manifests plus Unity assembly definitions, statically declared Swift package/target roots, and static Gradle settings modules/project-directory mappings; dynamic, ambiguous, unsupported, or malformed declarations contribute no component fact. A directory, filename pattern without a provider declaration, `.repometa` area, language, or extension does not create a component or ownership, and existing edge semantics do not change. The ordered provider IDs and explicit parser revisions are part of Graph materialization identity, so a parser contract change makes an older snapshot stale instead of silently reusing its topology.

```text
citable subject
  component_ids[]

typed relation
  from_component_ids[]
  to_component_ids[]
  crossed_component_ids[]
```

Membership is a set: overlapping root and nested declarations are both preserved for every covered current subject. A crossing exists only when a fresh confirmed typed relation connects non-identical membership sets. A bridge remains the ordinary subject and relation path that participates in that crossing; there is no second boundary/bridge ledger or owner class. Compact output returns only query-relevant crossings, total counts, truncation, and the existing typed continuations alongside them; continuations do not duplicate component fields. Stale endpoints and relations without a typed continuation are omitted.

### Exact result-member boundary

The current result receipt stores a canonical request plus flat `{authority, ref}` entries. Discovery validates an explicitly selected tuple against that receipt and freezes its canonical member capsule.

## Command

```bash
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl graph build --repo-id main --rebuild --json
./scripts/repoctl history rebuild --repo-id main --json
```

Direct single-repo layout may omit `--repo-id` when `repos/.git` is the only target. Configured multi-repo layouts must pass `--repo-id`.

Graph `--rebuild` discards reusable provider results and is reserved for Graph recovery or provider/schema changes. It does not scan completion receipts or repair the completion catalogue. `history rebuild` validates the selected repository's full cold receipt authority and regenerates only completion-catalogue state. Normal Graph/Context operations never invoke it implicitly.

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

Query never updates the Graph materialization. A successful query atomically records only a regenerable compact result receipt under `.repoctl-state/result-receipts/**`; it does not change source, task state, or Graph facts. The cache is bounded by finite count, byte, and age limits and is collected at write time; a machine-owned insertion sequence plus receipt digest defines count/byte eviction order, while file time is used only for maximum age. Eviction is deterministic and cannot invalidate evidence already frozen into a task citation. If no snapshot exists, the query returns `graph_snapshot_missing` and tells the caller to run `graph build`. Existing state that is unreadable, malformed, incomplete, incompatible, or bound to another repository identity is a typed hard failure and requires an explicit rebuild; it is never treated as an absent snapshot. Source changes become visible after the next explicit build; queries remain pinned to the returned `snapshot_digest` until then.

Materialized implementation state lives under `.repoctl-state/graph/<repo-id>/`. It contains one canonical snapshot, one manifest, and one fixed result file per semantic provider. It does not create per-query or per-file ledgers.

Incremental invalidation follows semantic boundaries:

- Python and Dart refresh changed files and their reverse import dependents.
- TypeScript/JavaScript refresh the affected `tsconfig.json`/`jsconfig.json` unit; unconfigured sources use reverse import dependents.
- C# refreshes the affected `.csproj` compilation unit.
- Provider configuration or provider input-version changes refresh that provider. Dart configuration includes root or nested `pubspec.yaml`, `pubspec.lock`, and the adjacent `.dart_tool/package_config.json`; changing any of them invalidates the affected Dart provider paths even when no indexed source file changed.
- Deleted and renamed files remove their old symbols, calls, and RPC facts before updated facts are merged.
- Completion history consumes only catalogue entries after the admitted checkpoint. Source freshness remains owned by the normal Graph inventory and provider invalidation paths; Context independently checks exact current file-version keys before showing outcome evidence. A gap or digest mismatch disables the history/outcome capability with an explicit recovery action; it does not trigger an implicit archive scan.

Exactly one primary selector is required: `--file`, `--topic`, `--import`, `--symbol`, `--callers-of`, `--callees-of`, `--impact-file`, `--impact-symbol`, `--task`, or `--artifact`. File selectors and `--in-file` accept either canonical repo-relative paths or workspace-relative paths prefixed by the selected repository path; one resolver normalizes both forms against indexed file identities. If both interpretations exist and differ, the selector fails with `graph_query_ambiguous_path` and returns both canonical candidates. A not-found path returns at most three exact basename/suffix candidates and returns none when no canonical identity is related; it never dumps provider inventories or arbitrary fuzzy suggestions.

Default build JSON contains the snapshot digest, node/edge counts, compact capability/provider status, materialization status, and updated-path counts. Provider path inventories and the raw snapshot are available only with `--full`.

Default query JSON contains a stable `result_digest`, direct matches, at most three decision-relevant relations, at most three non-authoritative relationship candidates, and bounded reusable continuations. Under the current result format, the command envelope's `data.result_receipt` content-binds the exact typed selector and flat identities visible on this compact surface to the producer, repository, and result digest. `--full` exposes diagnostics but keeps the same receipt request and membership. Query-specific traversals are returned under `paths`; queries without a traversal projection return their compact edges under `relations`. It omits node/edge counts, displayed/omitted statistics, provider coverage, analyzed-path inventories, freshness counts, and materialization digests. Compact freshness contains only state and the root-evidence drift indicator. File and symbol queries traverse importers/imports, callers/callees, direct tests, related tasks/artifacts/documents, and reviewed Knowledge in both directions. Every compact relation preserves evidence type, assertion/provider, confidence, capability completeness, and per-relation freshness. Use `--full --json` for raw nodes/edges and provider diagnostics.

Context projection is a separate internal consumer of the same snapshot. `project_context_neighborhood()` accepts only typed file or provider-symbol anchors and an explicit Context mode policy; Graph never receives raw natural-language tokens or task-retrieval candidates. Task ownership is exposed by the separate typed `context_task_path_support()` lookup. For each requested task ID, in caller order, it returns at most 24 current, non-deleted, same-repository `TASK_CHANGED_FILE(role=path)` targets, excluding paths unavailable in the current inventory or explicitly excluded for freshness. This lookup does not inspect task prose or the query, choose an eligible task, rank current text, or add paths to neighborhood seeds, `related_paths`, relations, or authority. Context owns task matching and ordering, loads and ranks current source at those paths, and for natural-language recovery uses the first strong task with eligible current lexical owners. Exact typed task or artifact matches are projected by Context only as `EXACT_TASK` evidence after anchor and traversal selection; they are not `HISTORY_CORROBORATION` and cannot create Graph traversal seeds. A naturally corroborated current file may still be chosen as an ordinary lexical Context anchor; that follows from current query evidence and Context's eligibility guard, not from a Graph task edge. Context may derive a bounded `lexical_file` anchor from its structured retrieval fields before calling the neighborhood boundary. Every requested anchor is accounted as resolved, ambiguous, or unresolved; missing file nodes and non-unique provider symbols are never silently ignored. Symbol anchors restrict first-hop call edges to the resolved symbol. Conflicting exact ambiguity produces no traversal, while a multi-anchor lexical/Knowledge request may continue with its resolved anchors and report the others as unresolved. Each mode fixes relation direction and maximum depth before traversal begins. Traversal tracks visited state per `(file, origin seed)` rather than per file, so an origin can continue through a node that is also another seed; merged relations retain per-origin minimum distances for scoring.

After freshness and projection resolution, Context turns each surviving anchor into one typed seed ref using the anchor's exact source ref and digest. Compact output and Task Pack carry that producer object outside excerpt budgets; neither reconstructs it from rankings, paths, language rules, or prose.

`KNOWLEDGE_APPLIES_TO` is materialized only when the record's derived lifecycle status is `reviewed`, from its literal `applies_to.paths` entry or a `source_ref` explicitly typed as `current_source`, after the shared repository selector resolver finds exactly one current file in the selected repository. A stale record may remain in Graph as historical provenance, but it never emits code-applicability edges. Root-document and other provenance-only `source_refs`, legacy aliases, task-derived changed files, and Knowledge prose do not create this edge. Ambiguous, invalid, missing, or cross-repository paths produce no edge.

The active snapshot materializes only completion events retained by the finite hot catalogue policy, not the full completion archive. Current source topology and current Reviewed Knowledge heads remain active. Exact `--task` / `--artifact` selectors validate the requested cold catalogue record and construct a query-local ephemeral projection; they do not add the record to the active snapshot, alter ordinary freshness, or seed unrelated traversal. Context `past_decision` / `failure_mode` selectors use the same explicit cold boundary and expose their matches only as `related_history`.

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
    "direct_tests",
    "document",
    "file",
    "import_ref",
    "import_resolution",
    "knowledge",
    "language_capabilities",
    "repository",
    "rpc_resolution",
    "same_file_calls",
    "structured_file_relations",
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
      "rpc_resolution": "complete",
      "structured_relations": "complete",
      "task_history": "complete",
      "knowledge": "complete"
    },
    "provider_coverage": {
      "symbols": {
        "capability": "symbols",
        "status": "partial",
        "eligible_paths": ["src/app.py", "scripts/run.sh"],
        "analyzed_paths": ["src/app.py"],
        "unsupported_paths": ["scripts/run.sh"],
        "failed_paths": [],
        "evidence_level": "precise",
        "coverage_gaps": []
      },
      "rpc": {
        "capability": "rpc",
        "status": "complete",
        "eligible_paths": ["lib/client.dart"],
        "analyzed_paths": ["lib/client.dart"],
        "unsupported_paths": [],
        "failed_paths": [],
        "evidence_level": "precise",
        "coverage_gaps": []
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
document
knowledge
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
KNOWLEDGE_DERIVED_FROM_TASK
```

`DECLARES_IMPORT` points to an importer-scoped `import_ref`, not to file, module, package, or symbol. Its typed identity preserves form, relative level, module, imported name, and raw display text. Resolvers may add `RESOLVES_TO` without changing this meaning. `--import <raw>` may return several distinct occurrence nodes when the same text appears in different files or import forms.

`RESOLVES_TO` points from an `import_ref` node to an unambiguous resolved file node. It is provider evidence, not package-manager or runtime inference.

`IMPORTS_FILE` points from the importing file node to the resolved imported file node. It is added only when resolution is unambiguous.

Python absolute imports are resolved against the repository root and structured setuptools roots declared by `pyproject.toml` through `tool.setuptools.package-dir` or `tool.setuptools.packages.find.where`. repoctl does not execute `setup.py`, mutate `sys.path`, or guess source roots from directory names. If one module identity maps to more than one file, resolution fails closed.

Python resolution consumes AST-derived import occurrences with explicit `module`/`from` form, relative level, module name, and imported name; it does not recover those semantics by splitting the raw import string. A relative import resolves only when every possible configured module identity for the importer yields the same target. For `from package import name`, a certain package attribute takes precedence over a same-named submodule, a possible or dynamic package attribute fails closed, and submodule fallback is used only when the package attribute is absent. Callable definitions, aliases, and explicit `from` re-exports are propagated to a fixed point so direct and module-qualified calls share one exported-callable identity.

`CALLS` points from a precise provider symbol node to another precise provider symbol node. String matching alone must not create `CALLS`.

`TESTS_FILE` points from a typed test-role file to a production file reached by either a provider-resolved import or a provider-resolved cross-file call. Import/call resolution and test-role classification remain separate facts: exact provider evidence may be high-confidence while a convention-derived test role is explicitly recorded as such. Compact output must not duplicate the same endpoints as both `TESTS_FILE` and `IMPORTS_FILE`. Graph does not infer tests by matching source/test basenames. `TASK_CHANGED_FILE` is recorded receipt evidence. Knowledge edges are created only from approved records; pending candidates never enter Graph. Reviewed and stale records preserve source task, source digest set, and freshness.

`USES_FILE` is the single file-to-file edge for syntax-resolved structured dependencies. Its `facts.relations[]` entries use a closed relation enum and preserve the exact reference, source line, operation, and confidence. The provider recognizes Docker `COPY`/`ADD` sources, Compose `build.dockerfile`/`env_file`/config files, local workflow actions and files executed by `run`, shell `source` and explicit file commands, SQL schema/seed dependencies, and client or SQL RPC calls resolved to a unique SQL routine definition. Python and JavaScript/TypeScript RPC parsing runs only for source entries with a supported static client import; unrelated source files are classified from indexed import facts without being sent through those bounded parsers. The provider parses exact format syntax and fails closed on dynamic variables, ambiguous paths, or ambiguous unqualified SQL objects; it does not infer dependencies from prose, filenames alone, or arbitrary command arguments. File and Context traversal consume the same edge in both directions, while impact traversal follows its dependency direction.

Dart RPC discovery has a separate analyzer-owned contract and no token-scanner fallback. `package:analyzer` identifies `SupabaseClient.rpc` from its resolved package/library/owner/member identity, preserves receiver type and source anchors, and emits facts only for selected-repository source paths. Normal method invocations and immediately invoked resolved method tear-offs produce the same typed fact. A resolved tear-off that is stored, passed, or otherwise escapes direct invocation makes RPC enumeration incomplete because its later runtime arguments cannot be attributed without guessing. Native package resolution may inspect the nearest package configuration and dependencies outside the repository, including the pub cache; dependency source outside the selected repository is not emitted as project source evidence.

Each analyzer-confirmed Dart RPC invocation is preserved as one source fact even when its arguments are dynamic or invalid. The analyzer's actual-to-formal parameter bindings and required parameter set produce a structured invocation contract: `valid` or `invalid`, with unmatched arguments, missing required parameters, duplicate bindings, and a closed reason code. Routine and `params` evidence are selected from those formal bindings rather than positional counts or label-text fallbacks, and the contract is never inferred from analyzer error text. Routine evidence is `known | unknown`; parameter evidence is `complete | partial | unknown`, with one shared invariant that forbids reasons on `complete` evidence and requires a closed reason on non-complete evidence. Runtime routine strings are matched exactly against SQL catalog names; they are not case-folded as though they were unquoted SQL source identifiers. Schema selection is a separate typed evidence axis and is `unknown/schema_not_observed` unless the source provider proves it. A dotted routine literal remains one opaque routine name and is never split into schema plus function. If schema selection is unknown, exact routine/parameter matches are retained as candidates but the outcome is `incomplete/schema_not_observed` and no edge is created, even when only one candidate exists. Only a proven schema may produce a linked target. An invalid invocation resolves to `incomplete` and never creates an edge. Every fact ID receives exactly one `linked | unresolved | ambiguous | incomplete` resolution, a typed reason, and `candidate_compatibility: none | compatible | unknown | incompatible`. Only `linked` creates `USES_FILE/sql_rpc_dependency`; that relation preserves the originating `source_fact_id`.

Compatible non-linked targets are projected separately as `relationship_candidates`; they never appear under `relations` or `paths`. Each item preserves the source path/range, exact runtime RPC identity, resolution outcome/reason, compatible SQL identities and locations, `authoritative: false`, total/truncation fields, and typed file continuations. Parameter mismatches and unknown-compatibility targets are not presented as compatible candidates. File-node `facts.rpc` retains all source facts, resolutions, compatibility states, and outcome counts so omitted non-candidates remain inspectable with `--full`. `rpc_resolution` completeness is `partial` whenever any preserved fact has an `incomplete` outcome, even if source enumeration itself was complete.

All semantic providers consume the same policy-eligible Code Index entry set. `classification: excluded` files may remain inventory nodes, but they do not produce source parsing, `DEFINES`, `ANCHORS`, `CALLS`, `RESOLVES_TO`, or `IMPORTS_FILE` evidence. `excluded_override` is an annotation-policy exemption and remains eligible for semantic analysis.

Provider support is compiler/analyzer backed:

- Python uses the stdlib AST with an explicit lexical-scope model.
- TypeScript and JavaScript use the TypeScript compiler API and checker-resolved call targets. A project-local compiler is preferred; an official bundled compiler is used when the repository has none.
- Dart uses `package:analyzer` resolved ASTs through an AOT helper. It follows the nearest native package configuration, while emitted project symbols, calls, and RPC facts remain restricted to selected-repository source paths.
- C# and Unity use Roslyn `SemanticModel` over `.csproj` compilation units.

Structured file relations are a separate bounded provider and do not claim compiler-level language semantics. SQL is indexed as actionable source text, while Dockerfile variants, Compose/workflow YAML, env/config files, and repository dotfiles remain typed config evidence. Kotlin and other inventory-only languages are not promoted to new semantic providers by this change.

Python `CALLS` resolution follows lexical scopes. Nested function, class, lambda, and comprehension bindings are not attributed to the wrong scope. Parameters and local assignments/imports shadow outer symbols; module imports and simple module aliases may resolve calls; `global` and `nonlocal` declarations are honored; ambiguous or order-unsafe bindings fail closed.

A Python `CALLS` edge records the lexical target when that call expression executes; it is not a whole-program reachability proof that every enclosing control-flow path initializes the binding before invocation. The bounded provider does not evaluate version-dependent annotation expressions because the product interpreter version and `__future__` policy are not authoritative Graph inputs.

Each semantic provider declares its own capability evidence level and coverage gaps. Python call evidence is conservative because dynamic call targets are not exhaustive, so a successfully analyzed Python repository must not claim complete call coverage.

Impact and caller/callee queries consume `CALLS`, `IMPORTS_FILE`, and `USES_FILE` evidence that already exists in the snapshot. They must not create new call or dependency edges by matching query strings.

`HAS_TOPIC` uses repo-local topic nodes. Same topic text in two repositories is not the same graph entity.

Task edges are produced only from structured task completion receipts under `docs/tasks/.repoctl-state/completions/`. Graph must not parse task Markdown, verification prose, or diff summaries to infer task/file relations. Generic completion claims are not eligible Knowledge candidates; only reusable decisions, invariants, and failure modes may proceed to explicit review.

`working_tree_diff` evidence has `attribution: task_working_tree` and may emit `TASK_CHANGED_FILE`. `committed_range` evidence has `attribution: range_observed`; it emits `TASK_RECORDED_CHANGE` and `CHANGE_AFFECTED_FILE` observation provenance but never `TASK_CHANGED_FILE`. Graph and Context must describe those paths as files observed in the completion range, not as task-owned commits or task-owned changes. One invalid receipt makes only `task_history` partial and does not remove current file/import evidence or other valid receipts.

repoctl accepts `committed_range` only when `start_head` is an ancestor of `observed_head`. Branch switches, resets, or rebases that break this ancestry block finish with `repo_history_rewritten`. Committed-range evidence is not combined implicitly with task-new working-tree changes.

Current schema-v4 completion receipt shape (`discovery_outcome` is required and
its full body is defined in the Discovery outcome contract; the compact example
below shows a valid empty outcome):

```json
{
  "schema": "repoctl.task.completion",
  "schema_version": 4,
  "task_id": "T-...",
  "repo_id": "web",
  "status": "done",
  "completed_at": "20260811T120000Z",
  "started_at": "2026-08-11T11:45:00.123456Z",
  "completed_event_at": "2026-08-11T12:00:00.654321Z",
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
    "diff_fingerprint_sha256": "sha256:...",
    "fingerprint_manifest": {
      "mode": "committed_range",
      "repo_id": "web",
      "repo_path": "repos/web",
      "start_head": "...",
      "observed_head": "...",
      "changed_entries": [
        {"change": "modified", "path": "src/app.py"}
      ],
      "entry_fingerprints": [
        {"change": "modified", "path": "src/app.py", "fingerprint_sha256": "sha256:..."}
      ]
    },
    "ownership": {},
    "path_transitions": [
      {
        "path": "src/app.py",
        "effect": "write",
        "basis": ["observed_change"],
        "before": {"kind": "file", "blob_oid": "...", "executable": false},
        "after": {"kind": "file", "blob_oid": "...", "executable": false}
      }
    ]
  },
  "verification": {
    "source": "task_section",
    "source_sha256": "sha256:...",
    "normalized_sha256": "sha256:...",
    "stored_sha256": "sha256:...",
    "truncated": false
  },
  "discovery_outcome": {
    "schema": "repoctl.task.discovery-completion-outcome",
    "schema_version": 1,
    "repository": {
      "id": "web",
      "path": "repos/web",
      "identity_source": "pinned"
    },
    "subjects": [],
    "active_chosen": [],
    "episodes": [],
    "verification_records": [],
    "outcome_digest": "sha256:0938727814f8781e50f31fc3b54a00e05e86427cfea3e8fc0c443863bede5a80"
  }
}
```

Receipt evidence uses only these closed mode/attribution pairs:

```text
none              / none
working_tree_diff / task_working_tree
committed_range   / range_observed
```

The receipt filename, `task_id`, and task ID encoded by `task_path_at_completion` must agree. `content_sha256` must bind to exactly one live or archived artifact for that task; a missing artifact, another task's artifact, or simultaneous live/archive matches are invalid. Current schema v4 records a microsecond execution interval, a stable before/after transition for every changed path, and a valid completion-bound Discovery outcome; `none/none` uses an empty transition list. When present, `fingerprint_manifest.entry_fingerprints[]` covers `changed_entries` by exact `change + path + old_path` identity. `committed_range/range_observed` remains observed range evidence and never becomes task or child ownership evidence.

Preserved schema-v2 receipts are accepted only at the isolated legacy boundary. `completed_at` and `repo_evidence.ownership` were not required by the published v2 shape; when present, the historical timestamp may use the compact workspace timestamp or an RFC3339 UTC timestamp and ownership must satisfy its original structured contract. Missing legacy fields remain absent evidence rather than acquiring inferred values. Schema-v2 receipts never gain invented v3 transitions: child attribution is possible only while the Git-owned verifier can still prove their recorded repository identity, unchanged HEAD, start state, and exact terminal fingerprint. A new task start records task-state v4. Finish emits schema v4 when a valid Discovery outcome exists and otherwise emits transition schema v3; neither legacy shape acquires invented outcome facts.

Schema-4 completion receipts carry the completion-bound Discovery outcome defined in `repoctl-discovery-outcome-contract.md`; catalogue projection exposes only its bounded recorded roles. Graph continues to consume structured receipt/catalogue evidence and must not parse task Markdown itself. Legacy v2/v3 receipts expose no Discovery-outcome capability and gain no inferred episode roles.

Receipt-derived Knowledge uses the same artifact identity rule. Its immutable `source_refs` retain the declared path and digest, while `resolved_source_refs` may point navigation and `KNOWLEDGE_SOURCED_FROM` at the unique byte-identical archive artifact after a parent task archives a completed child. This relocation requires the exact task ID, repository ID, completion-receipt ref, declared verification artifact, receipt path, filename, and digest binding; it never follows a lookalike path or a digest-only match. Invalid, missing, duplicate, reversed, or content-changing moves remain stale/invalid and produce no current Knowledge behavior.

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
  "candidates": [],
  "paths": [],
  "relationship_candidates": [],
  "relationship_candidate_count": 0,
  "relationship_candidates_truncated": false,
  "result_digest": "sha256:...",
  "continuations": [],
  "relations": [],
  "completeness": {},
  "warnings": []
}
```

Query selectors are exact typed selectors. Clients must not pass an `id` string and expect repoctl to split it.

`matches` contains the selector's direct node candidates. `candidates` contains at most three exact canonical path corrections or ambiguity choices and is unrelated to `relationship_candidates`. `paths` contains ordered confirmed traversal evidence; every compact path includes an `evidence` object with type, assertion, provider, confidence, completeness, and freshness. Missing confidence is `unknown`; it is never synthesized from assertion prose. Stored lifecycle freshness and root-evidence drift take precedence over materialization-level currentness. `result_digest` binds the query, snapshot, direct matches, path corrections, relationship candidates, and paths so selected Graph results can be referenced without storing an execution log.

`continuations` makes the displayed compact subgraph traversable without parsing node IDs or provider-specific symbol IDs. Relation/path selection and continuation selection share one budget, so every displayed non-current neighbor has a typed continuation. The current match may be omitted when neighbor actions consume the three-item budget because its selector is already present in `query`. Compact results contain a typed selector with normalized `value` and optional `in_file`, the supported follow-up `query_types`, and stable action enums such as `graph.file`, `graph.callers_of`, `task.show`, or `workspace.open`. The selector supplies identity and the bundle supplies `repo_id`, so actions do not repeat command arguments. `--full` also preserves the source node ID, kind, and label. Clients may repeatedly feed selectors into `graph query`; a continuation remains evidence for the returned `snapshot_digest`, not a durable locator after source changes.

Continuation coverage is:

```text
file         -> graph file / impact-file
symbol       -> graph symbol and capability-supported call/impact queries
import_ref   -> graph import
topic        -> graph topic
task         -> graph task / task show
artifact     -> graph artifact / workspace open
document     -> workspace open
knowledge    -> knowledge show
change_event -> owning graph task
```

`--task` first performs an exact cold lookup and returns a query-local projection of the recorded task, completion artifact, change events, and affected current or historical file identities. `--artifact` resolves the canonical task identity from the artifact selector, validates the same cold record, and follows it back through recorded file evidence. Receipt `task_path_at_completion` remains historical evidence. If no exact cold record exists, the selector may match a retained active-snapshot node. These selectors consume structured completion evidence only; their ephemeral nodes and edges are not persisted into the Graph snapshot.

Every query payload includes `freshness`. `current` means product file identities, root evidence identities, provider configuration, and Graph/provider input versions still match the materialized manifest. `stale` emits `graph_snapshot_stale` and a typed `graph_refresh` action; the stored result remains queryable as historical derived evidence but must not be presented as current. Full freshness reports exact `changed_paths`, `changed_root_paths`, `changed_provider_configs`, provider-owned `provider_stale_paths`, their `semantic_stale_paths` union, the canonical relation-level `stale_paths`, `provider_state_changed`, and `graph_input_version_changed`. Source or target paths in `stale_paths` are excluded from Graph relationship candidates and Context Graph projections until rebuild; Context may still return live source text from a stale-path overlay. Rebuild explicitly before relying on changed relations. Root evidence probes reuse stored content digests when path kind, mode, size, and mtime are unchanged, so freshness checks do not reread every document or receipt body.

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

Invalid selectors and invalid paths are ordinary problems. Ambiguous symbols return candidate matches, and path-form collisions return `query_status: ambiguous` plus canonical candidates; both exit nonzero with a typed problem. `not_found` with `completeness.status: partial` means only "not found in currently available evidence"; it does not prove absence.
