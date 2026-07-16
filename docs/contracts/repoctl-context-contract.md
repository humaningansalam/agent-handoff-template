# repoctl Context contract

`repoctl context query` returns a read-only evidence bundle for one explicit product repository.

Context is not authoritative. Source authorities remain the repo registry, source documents, Graph, `.repometa`, task completion receipts, and reviewed knowledge records.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, `auto` uses the persistent FTS/source-symbol index to choose current source and test anchors. It then projects at most two levels of provider-confirmed import/call relations, preserving both source and test anchors so a high-degree file cannot starve another seed. Related reviewed Knowledge and completion history are included only when their explicit source paths, changed files, or explicit file targets overlap the selected paths. It does not infer relationships from query wording.

Context never triggers a hidden Graph build. With a materialized Graph, queries read the persistent SQLite evidence index and do not reread unchanged product sources. Product paths or root evidence changed after materialization are removed from stored results and read directly as a small query-time overlay; deleted paths contribute no overlay. The overlay never mutates the index. When no materialized Graph exists, lexical document/current-source retrieval still succeeds through the explicit fallback and `warnings_and_completeness` reports that Graph relations are unavailable. When a Graph snapshot exists but its evidence index is missing, unreadable, incompatible, or bound to another snapshot digest, Context fails with a typed evidence-index problem and an explicit `graph build --rebuild` action; it does not hide index corruption behind a repository-wide scan. Run `repoctl graph build` explicitly when import/call traversal or completion-history projection is needed.

Current-source text indexing is limited to registered `semantic_source` language profiles and files up to 1 MiB. Larger files remain Graph inventory nodes but are omitted from Context text retrieval with a persistent `context_current_source_too_large` warning and path in evidence-index completeness. JSON, manifests, and documents enter through their typed evidence sources rather than being treated as arbitrary source code.

Direct query anchors remain ahead of Graph-only dependencies in source and test groups. Graph expansion may enrich an anchor and rank related files within the expansion stage, but a shared dependency must not displace a file selected directly by exact lexical or FTS evidence merely because several anchors import it.

Supported modes are:

```text
auto
startup_reading
code_location
call_impact
file_impact
authority_or_contract
past_decision
invariant
failure_mode
```

Hyphenated aliases such as `call-impact` are accepted. `authority` and `contract` map to `authority_or_contract`.

## Bundle

The JSON payload is `repoctl.context.bundle` with `authoritative: false`.

Default `--json` output is the compact agent-facing view. It includes:

```text
query.mode
completeness
groups
selection
knowledge_result_count
bundle_digest
```

Repository-wide source snapshots, provider path inventories, full evidence, and score diagnostics are omitted from the compact view. Compact JSON is serialized without pretty-print whitespace. Use `--full --json` or `--explain` only when diagnostics are required.

Use `--full --json` to include raw retrieval/debug fields:

```text
evidence
knowledge_results
source_snapshots
```

`evidence` contains raw query-matching source/document chunks plus provider-confirmed Graph relations projected from lexical source and test anchors. Retrieval selects the strongest matching chunk from distinct paths before allowing additional sections from the same path, so one large file cannot consume the candidate set. Token cost never participates in retrieval or evidence selection.

Compact groups merge chunks with the same path into one file-level item and preserve their locations in a `sections` list. Structured `evidence_role` values distinguish lexical change candidates from provider-confirmed imported/called dependencies, directly connected tests, authority documents, and supporting evidence. These roles come from source kind or Graph relations; Context does not call a file an owner based on a filename or natural-language guess. Compact output shortens excerpts and applies role-specific display limits; `selection` reports total, displayed, and omitted counts so an agent can refine the query or inspect `--full` without losing raw evidence.

When one path has both direct query evidence and Graph relations, its compact primary role remains `change_candidate` or `test_candidate`; provider-confirmed relationships remain available through relation evidence and typed continuations. A path reached only through Graph keeps its dependency role.

`groups` organizes selected evidence into:

```text
must_read
likely_change_surface
callers_and_dependents
tests_and_verification
reviewed_knowledge
related_history
supporting_evidence
warnings_and_completeness
```

In `auto`, `reviewed_knowledge` and `related_history` contain only evidence structurally linked to current-source paths in the query evidence. Explicit `authority_or_contract`, `invariant`, `past_decision`, and `failure_mode` modes may perform broader reviewed-Knowledge retrieval.

The repository identity is stored once at bundle level. Compact group items keep a file-level source ref, section locations, a primary evidence role, a bounded selection reason, and a bounded excerpt. Compact completeness includes the bounded Graph freshness status, changed-path counts, root-evidence status, and materialized input digest so default output never presents a stale snapshot as current. Typed continuations are deduplicated once at bundle level instead of repeated per item. Full item continuations are primary-first: current source/test items own their file selector, documents own their document selector, reviewed Knowledge owns its `knowledge_record` selector, completion history owns its task selector, and a pure `CALLS` relation owns its exact symbol selector with `in_file`. A producer that cannot construct that typed primary returns no continuations; normalization must not promote a secondary selector into the first slot. Compact projection walks `CONTEXT_GROUPS` in contract order and validates the raw primary before lossless deduplication, then reserves it before displaying the non-warning item. If the eight-selector continuation budget cannot admit that primary selector, or the item has no valid primary, the item is omitted with it; remaining capacity may include secondary symbol, source-document, or artifact continuations. Warning/completeness items do not consume the continuation budget. Shared selectors count once and merge their action enums. Repeated per-item repo IDs, current-status markers, score breakdowns, and raw relation paths are omitted. Full output retains raw chunk evidence, deterministic scoring, item-level continuations, and relation evidence.

## Graph Evidence

Context consumes the materialized Graph through internal Python objects; it must not parse `graph query` stdout or invoke compiler providers. `auto`, `code_location`, `call_impact`, and `file_impact` all use the same query-centered file projection seeded only by lexical current-source results. The projection includes exact `IMPORTS_FILE` edges and provider-confirmed same-file or cross-file `CALLS` edges. Lexical anchor relevance propagates over those edges with distance decay; generic graph connectivity alone is not relevance.

Context never converts free-form query tokens into Graph file or symbol selectors. Graph-derived Context items use `source_ref.kind: graph_relation`, preserve the exact provider relation and endpoint identities, and remain current-query evidence rather than durable knowledge records. Explicit `repoctl graph query` selectors remain a separate iterative exploration interface.

`repoctl graph query` is also an iterative exploration interface. Every result includes `continuations` derived from returned Graph node identities. The compact view keeps a normalized selector and supported follow-up query types; `--full` also includes the source node identity and label:

```text
file -> file, impact_file
symbol -> symbol, plus callers_of, callees_of, impact_symbol when call evidence is available
import_ref -> import
topic -> topic
task -> task evidence and task show
artifact -> artifact evidence and workspace open
```

Symbol continuations include `in_file` when available so repeated names remain unambiguous. Agents may follow these selectors repeatedly to inspect adjacent files and symbols. Continuations are tied to the result's `snapshot_digest`; run `graph build` after source changes, then rerun the query instead of treating an earlier traversal as durable knowledge.

Context group items use the same selector plus action-enum shape across stores without merging their authorities:

```text
current product file -> Graph file and impact actions
provider symbol relation -> Graph symbol action with in_file
completion history -> Graph task, task show, artifact, workspace open
reviewed Knowledge -> knowledge show
Knowledge source ref or root document -> workspace open
```

Reviewed Knowledge requires either a lexical query match or an explicit path/source relation to qualify. Reviewed status is a ranking signal after eligibility; it must not make unrelated records appear in every query. Explicit path relation is independently sufficient, so a linked invariant or decision is not lost merely because its prose uses different words.

When product source is stale, Context excludes every Graph relation whose endpoint is stale and overlays only the current file text. When receipt or task-artifact evidence is stale, related history is omitted and `task_history` becomes partial until the next explicit Graph build. Root document changes such as `docs/BOARD.md` do not force a product-wide fallback scan.

## Markdown Output

`--format markdown` renders the same bundle in a human-readable order:

```text
query interpretation
must-read evidence
change surface
callers/dependents
tests/verification hints
reviewed knowledge
related completion history
warnings/completeness
```

Markdown output is a view. It must not be ingested as a future Context, Knowledge, or llmwiki source.

## Task Pack

`repoctl context pack` turns a live task into a startup evidence pack:

```bash
./scripts/repoctl context pack --task T-... --repo-id main --format markdown --output .repoctl-state/context-pack/T-....md
```

The normal repo-scoped flow is: record a Candidate query, run compact `context query`, inspect the suggested product files, refine and repeat the query when needed, then record Reviewed and Chosen files before editing. Default retrieval does not infer intent from Goal or Handoff prose: it ranks current source lexically, then expands only the bounded Graph relations of the top source results. A scoped Context Pack is optional durable handoff evidence, not an edit gate. The pack is non-authoritative. Default `--json` output is compact and contains:

```text
stage
input_digest
stop_reason
budget
must_read
edit_candidates
supporting_evidence
likely_change
impact
verification
warnings
```

Use `--full --json` to include the raw nested Context bundle and full evidence details.

When `--output` is supplied, the full requested artifact is written to that path. Markdown stdout reports only the artifact path instead of duplicating the complete pack; omit `--output` when the rendered Markdown itself is required on stdout.

`stage: bootstrap` is available before active Chosen files exist. It contains AGENTS, the task, explicit Context Docs, product identity/manifests, and capability warnings; it must not add task history or raw Graph data and is not required before initial file inspection. `stage: scoped` is used after Discovery has an active Chosen set. Chosen/current source and directly connected tests remain the focus.

`edit_candidates` contains exactly the active Chosen set. `supporting_evidence` contains Reviewed minus Chosen, so the two sets are disjoint. Context does not infer edit scope from task prose, receipt history, basenames, or generated/ignored files.

Retrieval query text comes from Candidate query history. Goal and Handoff prose are not parsed as symbols. A test is directly connected only through explicit Discovery evidence, a provider-confirmed relation, or manifest mapping.

Task packs do not query reviewed knowledge or completion history. Use normal `context query` for path-linked history, an explicit historical mode for broader retrieval, or an explicit Context Doc when a task pack needs durable historical context.

`input_digest` covers task content, Discovery query history, Reviewed and Chosen sets, explicit Context Docs and their content digests, repository identity, observed HEAD/snapshot, and capability matrix. A saved pack is stale when recomputing those inputs produces a different digest; read-only commands do not rewrite it.

Budget values are estimates and use the name `estimated_tokens`. Early stop is deterministic and reports one of:

```text
required_evidence_satisfied
budget_reached
no_more_eligible_evidence
required_evidence_exceeds_budget
```

`final_render_estimated_tokens` must not exceed `maximum_estimated_tokens` unless the required evidence alone exceeds the budget, in which case the stop reason is `required_evidence_exceeds_budget`.

Each Task Pack group item records `requirement: required | optional`. Required evidence is AGENTS, the task source ref, explicit Context Docs, and scoped Chosen refs. Budget trimming may shorten excerpts and remove optional evidence, but it must never remove required source refs. Compact output also preserves every required item even when a normal display limit would be exceeded.

## Benchmark Labels

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The benchmark records labeled retrieval metrics plus the actual serialized compact byte size and its estimated token cost. Internal excerpt budgets are not accepted as output-size evidence. Existing source-integrity, forbidden-source, and cross-repo contamination gates remain; ranking thresholds are release gates only when they correspond to agent-visible results and have real benchmark history.
