# repoctl Context contract

`repoctl context query` returns a read-only evidence bundle for one explicit product repository.

Context is not authoritative. Source authorities remain the repo registry, source documents, `.repometa`, task completion receipts, and reviewed knowledge records. Graph contributes derived relationship evidence only.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, `auto` uses the persistent FTS/source-symbol index to choose owner source, exact config/dotfile matches, and direct test anchors. It projects at most two levels of provider-confirmed import/call/test relations while keeping source, test, authority, history, and reviewed-Knowledge lanes separate. Reviewed Knowledge qualifies through either an exact query match or an explicit source/path relation; reviewed status alone is never sufficient.

Context never triggers a hidden Graph build. With a healthy materialization, queries read the persistent SQLite evidence index and overlay only changed paths. If the Graph snapshot or evidence index is missing, unreadable, incompatible, or digest-mismatched, Context returns a typed partial result from live source, config, document, task, and Knowledge evidence instead of hard-failing. Graph relations are marked unavailable and an explicit rebuild remains recovery guidance. The fallback is read-only and never repairs materialized state.

Text indexing is limited to files up to 1 MiB. Registered semantic languages and SQL use `current_source`; JSON/YAML/TOML/INI/env-style configuration, Dockerfile variants, Compose files, workflow files, and repository dotfiles use the separate `config` kind; manifests and documents keep their own typed kinds. Exact full path, selected-repo suffix, filename, symbol/section, config, and dotfile identity matches outrank ordinary body-term matches.

Direct query anchors remain ahead of Graph-only dependencies in source and test groups. After an exact path/symbol identity anchor, its immediate imported, called, tested, or structured owner dependency is reserved ahead of weaker sibling body matches. Graph expansion may enrich an anchor and rank related files within the expansion stage, but a shared dependency must not displace the exact anchor merely because several consumers reference it.

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
continuations
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

Compact groups merge chunks with the same path into one file-level item and preserve their locations in a `sections` list. A global budget returns at most eight actionable items, normally three to eight when enough evidence exists, with small per-lane limits so authority, source, test, history, and Knowledge do not compete in one ranking. Each item carries its reason and typed continuation. Selection counts, score diagnostics, provider coverage, and omitted-item statistics are available only with `--full` or `--explain`.

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

In `auto`, `reviewed_knowledge` accepts exact record matches as well as records structurally linked to selected paths. `related_history` is retrieved in its own lane and never displaces owner source or direct tests. Explicit historical modes may retrieve a broader set.

The repository identity is stored once at bundle level. Compact completeness contains only operational Graph availability, freshness state, and the root-evidence drift indicator; freshness counts and materialization digests are diagnostic-only. Typed continuations are deduplicated once at bundle level: current source, config, and test items own their file selector; documents own their document selector; reviewed Knowledge owns its `knowledge_record` selector; completion history owns its task selector; and a pure call relation owns its exact symbol selector with `in_file`. Repeated repo IDs, score breakdowns, provider inventories, and raw relation paths are omitted. Full output retains deterministic scoring, item-level continuations, and relation evidence.

## Graph Evidence

Context consumes the materialized Graph through internal Python objects; it must not parse `graph query` stdout or invoke compiler providers. `auto`, `code_location`, `call_impact`, and `file_impact` use the same query-centered file projection. The projection includes exact `IMPORTS_FILE`, provider-confirmed `CALLS`, direct or explicitly inferred `TESTS_FILE`, and syntax-resolved `USES_FILE` edges for SQL/Docker/Compose/workflow/shell dependencies. Exact owner definitions rank before surrounding consumers; lexical relevance propagates with distance decay.

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

There is no mandatory Context -> Graph -> `rg` order. Start from Context for ambiguous intent, Graph or direct read for a known file, `rg` for a known symbol, and task/Knowledge for past decisions. Create or start the task before the first product mutation, then record Candidate, Reviewed, and Chosen evidence as scope becomes concrete. A scoped Context Pack is optional durable handoff evidence, not an exploration gate. The pack is non-authoritative. Default `--json` output is compact and contains:

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

`stage: bootstrap` is available before active Chosen files exist. It contains AGENTS, canonical `docs/PRD.md`, the task, explicit Context Docs, product identity/manifests, and capability warnings. Task Context Docs and canonical PRD are required authority evidence and never compete with lexical search results. The pack is not required before initial file inspection. `stage: scoped` is used after Discovery has an active Chosen set.

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

Each Task Pack group item records `requirement: required | optional`. Required evidence is AGENTS, canonical PRD, the task source ref, explicit Context Docs, and scoped Chosen refs. Budget trimming may shorten excerpts and remove optional evidence, but it must never remove required source refs.

## Benchmark Labels

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The benchmark records labeled retrieval metrics plus the actual serialized compact byte size and its estimated token cost. Internal excerpt budgets are not accepted as output-size evidence. Existing source-integrity, forbidden-source, and cross-repo contamination gates remain; ranking thresholds are release gates only when they correspond to agent-visible results and have real benchmark history.
