# repoctl Context Contract

## Purpose

Context answers an agent's immediate reading question with a bounded, source-linked working set. It combines current source, project authority, procedures, Task history, Knowledge, and already-materialized Graph relations without turning any result into edit scope or authority.

Use it for ambiguous intent:

```bash
./scripts/repoctl context query "where is invoice retry decided" --repo-id main --json
```

For a known file or exact identity, direct reads, `rg`, and `graph query` remain first-class entry points. There is no mandatory tool order.

## Query modes

Public modes are:

```text
auto
startup-reading
code-location
call-impact
file-impact
authority
contract
past-decision
failure-mode
invariant
```

Mode selection changes bounded ranking and Graph traversal policy. It never changes source authority or Task scope.

`startup-reading` preserves project entry sources ahead of incidental lexical matches and suggests orientation before implementation. Product-root `AGENTS.md` and `CONTRIBUTING.md` are collected when present; rules in ancestor directories still need direct inspection for a chosen file. Manifest verification hints respect a recognized declared package manager, but require review of local guidance, working directory and runtime prerequisites before execution. Retrieval benchmarks measure source visibility and navigation coverage, not an agent's understanding or product acceptance.

## Evidence model

A Context item carries a `source_ref` with repository-aware path identity and, when available, section, lines, source fact, provider symbol, and content digest. Full output may also expose evidence kinds, anchor strength, field matches, score diagnostics, and document role.

Current source classification is closed and shared by persistent indexing and live overlays:

- `current_source`
- `config`
- `structured_data`

Generated or ignored control artifacts are excluded. Shared documents keep one semantic role through collection, indexing, retrieval, and Pack construction so consumers do not reinterpret them from folder names.

The public working groups are:

```text
must_read
likely_change_surface
tests_and_verification
callers_and_dependents
reviewed_knowledge
related_history
supporting_evidence
warnings_and_completeness
```

The stable `reviewed_knowledge` group contains current direct Knowledge records. Older records remain preserved migration input and do not enter Context until an agent saves their useful conclusion in the current format. `tests_and_verification` contains source/test relationships and runnable project hints; it does not claim a check ran or create verification state.

Compact selection prefers distinct useful paths before repeating sections from one file. Source relevance may propose a member but cannot prove semantic ownership. Confirmed Graph relations and compatible unresolved `relationship_candidates` remain separate lanes.

## Graph integration

Context consumes the last materialized Graph through typed internal objects. It does not parse Graph stdout, invoke providers during a query, or pass free-form tokens into Graph selectors.

Anchor provenance is one of:

- `exact_identity`
- `provider_symbol`
- `reviewed_knowledge`
- `lexical_file`

`resolved` means the typed Graph identity exists. It does not mean the file owns the behavior. A provider-symbol anchor restricts symbol-level call traversal; file-level imports and direct test relations remain separately typed.

Default traversal remains bounded:

```text
auto          direct call/import/structured/test relations, depth 1
code_location outgoing callees/imports/structured dependencies and direct tests, depth 1
call_impact   incoming/outgoing calls through depth 2 and direct tests
file_impact   imports/structured-file relations through depth 2, direct calls/tests at depth 1
```

Top-level `graph_seed_refs` preserve source-bound continuation inputs. `completeness.graph_anchor` explains identity and selection coverage. Explicit `graph query` stays available for iterative file, symbol, import, topic, task, artifact, caller, callee, and impact traversal.

When product source changes after materialization, Context overlays current text and excludes stale Graph relations whose endpoints are stale. A stale query-explicit Graph identity remains unresolved; Context does not silently substitute a weaker anchor to regain traversal. Provider/configuration drift requires an explicit `graph build`.

## Project documents, history, and Knowledge

`project_knowledge` reports project documents, Task history, and reusable Knowledge as distinct lanes. A lane not loaded reports `null` rather than a misleading zero.

Ordinary modes rank independently retrieved current material. Completed Tasks appear only through explicit `past_decision` and `failure_mode` history lookup or exact Graph task/artifact selection. Cold history cannot add or reorder current candidates, seed Graph traversal, or define Chosen.

Knowledge qualifies through a query match or explicit current path/source relation. `applies_to` may provide a bounded navigation continuation. That continuation never becomes source evidence, a Graph seed, ranking boost, or scope. Replaced current-format records appear only when history is explicitly requested.

Source drift on a Knowledge record produces a warning while the saved conclusion remains visible. The source link tells the agent what current material to recheck.

## Output and completeness

Default JSON is a bounded projection. `--full --json` adds raw evidence and diagnostics without changing the identity or meaning of visible members. Text and Markdown preserve source links and useful continuations but may omit JSON-only topology details.

Completeness accounts separately for:

- source/index availability
- Graph availability and freshness
- anchor identity resolution
- Graph traversal selection coverage
- visible working-set coverage
- project document, Task-history, and Knowledge lanes

Warnings are typed. Missing Graph can still yield lexical source, documents, Tasks, and Knowledge. A valid persistent evidence index is reused; changed paths are overlaid rather than forcing an unchanged-source rescan.

Context and Graph queries are read-only. They do not create persistent result receipts, selection manifests, Discovery episodes, Task mutations, or future feedback state. Repeating a query may refresh only ordinary derived search/index state already owned by Context; it does not create a per-query ledger.

## Human output

`--format markdown` presents the same selected bundle in a readable order: interpretation, must-read authority, likely change surface, tests, callers/dependents, Knowledge/history, and warnings. Rendered output is a view and must not be re-ingested as a new source of authority.

## Task Context Pack

`context pack` produces a bounded one-time Task reading bundle:

```bash
./scripts/repoctl context pack --task T-... --repo-id main --format markdown
./scripts/repoctl context pack --task T-... --repo-id main \
  --format markdown --output /tmp/T-context.md
```

The Pack reads current Task, Chosen and Notes, explicit Context Docs, repository state, source identities, and materialized Graph state. It uses the Task title as the retrieval seed and Chosen as the only edit-candidate set.

Pack stages are:

- `bootstrap` before Chosen exists
- `scoped` after Chosen exists

Required Pack material includes `AGENTS.md`, the Task, canonical product authority (`docs/PRD.md` or a selected document under `docs/prd/`), explicit Context Docs, and scoped Chosen paths. Optional material fills remaining budget from query relevance, Graph relations, and project hints.

Default compact Pack output contains the current task/stage, `input_digest`, stop reason, budget, render projection, seed query/notes/Graph refs, and bounded groups:

```text
must_read
edit_candidates
likely_change
impact
verification
warnings
```

`edit_candidates` is exactly current Chosen. Explicit Context Docs remain required reading, while retired Reviewed fields do not create Pack groups or inputs. The `verification` group contains project commands/manifests and related test hints, not recorded outcomes.

The canonical `input_digest` covers Task content, Chosen/Notes, Graph seed refs, Context Docs, included source identities, repository observation, Graph snapshot, and capability matrix. It helps identify the generated view; there is no Pack freshness service or binding lifecycle.

`--output` invalidates the requested destination before construction and writes only after a successful build. An absolute `/tmp` path is supported for disposable output. When Markdown is written, stdout reports the path instead of duplicating the full body. Pack generation never writes Task, Discovery, Handoff, or completion state.

A Pack cannot be passed to `task handoff bind`. Resume reads Task and Handoff directly. A saved Pack may become outdated like any exported report and should be regenerated only when someone actually needs a new view.

## Budget behavior

Budget values are estimates. Deterministic stop reasons are:

```text
required_evidence_satisfied
budget_reached
no_more_eligible_evidence
required_evidence_exceeds_budget
```

Optional excerpts are shortened or removed first. If required material still does not fit, rendering collapses it to a deduplicated path/section reference manifest. If even that exceeds the maximum, the command fails with `context_pack_required_evidence_exceeds_budget` and does not write a successful artifact.

Every group item declares `requirement: required | optional`. Full JSON retains detailed required items; compact reference projection retains their source identity and source-pack digest.

## Benchmark boundary

Internal field-gate fixtures may label sources `must_find`, `acceptable`, `supporting`, or `noise`. They measure working-set recall, first-correct rank, precision, visible contamination, Graph edge recall, serialized size, and estimated token cost. These diagnostics tune retrieval only. They are not user task state, verification proof, or a reason to add runtime query bookkeeping.
