# repoctl Context contract

`repoctl context query` returns a read-only evidence bundle for one explicit product repository.

Context is not authoritative. Source authorities remain the repo registry, source documents, `.repometa`, task completion receipts, and reviewed knowledge records. Graph contributes derived relationship evidence only.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, `auto` uses the persistent FTS/source-symbol index to choose owner source, exact config/dotfile matches, and direct test anchors. From one resolved typed anchor it projects only direct provider-confirmed call/import/test/structured-file relations while keeping source, test, authority, history, and reviewed-Knowledge lanes separate. Reviewed Knowledge qualifies through either an exact query match or an explicit source/path relation; reviewed status alone is never sufficient.

Context never triggers a hidden Graph build. With a healthy materialization, queries read the persistent SQLite evidence index and overlay only changed paths. If the Graph snapshot or evidence index is missing, unreadable, incompatible, or digest-mismatched, Context returns a typed partial result from live source, config, document, task, and Knowledge evidence instead of hard-failing. Graph relations are marked unavailable, while JSON `next_actions` use the structured cause code to return the required `graph build` or `graph build --rebuild` command plus a command that resumes the same Context query. The fallback is read-only and never repairs materialized state.

Text indexing is limited to files up to 1 MiB. Registered Context-source languages use `current_source`; semantic Graph support remains a separate capability. This makes HTML and stylesheet files searchable without claiming symbols, calls, or imports for them. JSON/YAML/TOML/INI/env-style configuration, Dockerfile variants, Compose files, workflow files, and repository dotfiles use the separate `config` kind; manifests and documents keep their own typed kinds. Identifier comparison canonicalizes case, separators, snake case, and CamelCase into ordered parts. Exact full path, selected-repo suffix, filename, and provider symbol/section identity remain eligible independently of broad body recall and outrank ordinary body-term matches.

Shared documents receive one closed `document_role` classification from their canonical path: operating authority, product authority, governance authority, procedure, reference, template, generated view, or unspecified. The same role owner drives indexed and live-fallback retrieval lanes, authority weighting, compact grouping, and Task Pack projection. `docs/PRD.md` and split `docs/prd/**` documents retain product-authority semantics; active procedures have their own bounded recall lane; references remain lower-priority supporting material. A document's location inside the product repository does not upgrade a reference into authority. Templates are excluded from ordinary semantic recall but remain reachable through an explicit path or filename identity. Generated Knowledge views are never eligible source evidence.

The persistent index performs bounded, path-diverse chunk recall independently for product source, product tests, product documents, canonical workspace documents, governance, procedures, other workspace documents, task history, and supporting evidence. Auto retrieval reserves one best result from each non-empty lane before filling the remaining per-lane quotas, so a large source corpus cannot starve later task-history or procedure evidence. The lane candidates are merged before one field-aware rank. Changed-path overlays replace stale indexed chunks before the same lane-balanced selection. Canonical ordering compares typed anchor strength before the composite score, so an exact identity cannot be displaced by an arbitrarily large weak body score. Path, section/symbol, and body coverage remain separate evidence fields. The `fts` diagnostic is the sign-normalized relevance `-bm25(chunks, 4.0, 3.0, 1.0)`, preserving SQLite's BM25 magnitude and ordering rather than exposing the negative raw rank or converting result position into a synthetic score.

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
selection.compact_projection.items
selection.compact_projection.continuations
```

`evidence` contains raw query-matching source/document chunks plus provider-confirmed Graph relations projected from lexical source and test anchors. Retrieval selects the strongest matching chunk from distinct paths before allowing additional sections from the same path, so one large file cannot consume the candidate set. Full evidence exposes typed `evidence_kinds`, `anchor_strength`, `document_role` when applicable, field score diagnostics, and section kind; display-only selection reasons do not drive seed or ranking decisions. Token cost never participates in retrieval or evidence selection.

Compact groups merge chunks with the same path into one file-level item and preserve their locations in a `sections` list. A global budget returns at most eight actionable items, normally three to eight when enough evidence exists, with small per-lane limits so authority, source, test, history, and Knowledge do not compete in one ranking. When matching authority and procedure documents both exist, compact `must_read` reserves one of each before filling its remaining slots. Each item carries its reason and typed continuation. Selection counts, score diagnostics, provider coverage, and omitted-item statistics are available only with `--full` or `--explain`.

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

For Graph-expanding modes, a reviewed Knowledge result becomes a code anchor only when its typed query match is `exact` or `strong`. Weak partial/FTS matches may remain visible in the Knowledge lane but cannot project code. Code paths come only from the record's literal `applies_to.paths` entries and `source_refs` explicitly typed as `current_source` that resolve to a current file inside the selected repository. Root workspace documents and all other source-ref kinds remain provenance-only; legacy scope/file aliases, task-derived changed-file prose, claims, summaries, titles, and filenames inferred from text are never code applicability.

Knowledge paths use the same repo-relative/workspace-relative resolver as Graph selectors with the current repository path set. Ambiguous, invalid, missing, stale, superseded, deprecated, or cross-repository paths fail closed. Full results expose `query_match_strength`, `code_anchor_status`, `code_path_resolutions`, and `resolved_code_paths`. A resolved code candidate carries `reviewed_knowledge_path` evidence and related record IDs, but its role is `knowledge_linked_source` or `knowledge_linked_test`; it is exploration evidence, not edit scope or authoritative ownership. A direct exact code identity remains stronger than a Knowledge-linked path.

Applicability navigation is separate from code-anchor eligibility. A current Reviewed Knowledge result may expose `applicability_path_resolutions` and `resolved_applicability_paths` from its explicit `applies_to.paths` entries even when its query match is weak. When that Knowledge item is selected into the compact bundle, a resolved applicability path may add a bounded `workspace.open` / `graph.file` continuation. This continuation is navigation only: it never creates source evidence, seeds Graph expansion, changes ranking or `code_anchor_status`, enters `likely_change_surface`, or implies edit scope. Provenance-only refs and ambiguous, invalid, missing, non-current, or cross-repository paths do not produce applicability continuations.

The repository identity is stored once at bundle level. Compact completeness contains operational Graph availability, freshness state, the root-evidence drift indicator, and a structured `graph_anchor` status/code for Graph-expanding modes. It also exposes `project_knowledge` as three explicit lanes: current project documents, task history, and explicitly reviewed reusable records. A lane that was not loaded or queried reports `null` instead of a misleading zero or an empty loaded value, including reviewed-record lifecycle data. `resolved` includes the selected seed path, `ambiguous` preserves equally strong candidates without choosing one, and `unresolved` reports that no eligible typed anchor exists. Freshness counts and materialization digests are diagnostic-only. Typed continuations are deduplicated once at bundle level: current source, config, and test items own their file selector; documents own their document selector; reviewed Knowledge owns its `knowledge_record` selector and any validated navigation-only applicability selector; completion history owns its task selector; and a pure call relation owns its exact symbol selector with `in_file`. Repeated repo IDs, score breakdowns, provider inventories, and raw relation paths are omitted. Full output retains deterministic scoring, item-level continuations, typed anchor candidates, and relation evidence.

## Graph Evidence

Context consumes the materialized Graph through internal Python objects; it must not parse `graph query` stdout or invoke compiler providers. Graph expansion starts only from typed exact path/filename/symbol evidence, a provider symbol with complete same-section query coverage and `strong` anchor strength, or an explicit reviewed-Knowledge path. Partial section matches and weak body-only lexical candidates remain visible retrieval evidence but never become Graph seeds.

Exact identity kinds do not use a numeric path/filename/symbol precedence. One exact symbol plus exact file evidence for the same path is a compatible interpretation and keeps the narrower symbol anchor. Multiple exact symbols, multiple exact file paths, or exact symbol/file interpretations that point at different paths return `ambiguous`. A missing snapshot file/symbol, a stale path excluded from the snapshot, or no eligible typed anchor returns `unresolved` with empty anchors and seed paths while retaining diagnostic candidates. Neither `ambiguous` nor `unresolved` expands relations.

Mode policies are explicit and bounded:

```text
auto          direct incoming/outgoing call, import, structured-file, and test relations; depth 1
code_location outgoing callees/imports/structured dependencies plus direct tests; depth 1
call_impact   incoming/outgoing calls through depth 2 plus direct tests; imports are excluded
file_impact   incoming/outgoing imports and structured-file relations through depth 2,
              plus direct calls and tests at depth 1
```

A provider-symbol anchor restricts first-hop `CALLS` relations to that exact symbol rather than every symbol in the file. It does not attach file-wide import or structured-file dependencies that cannot be attributed to that symbol; direct file-level tests remain eligible. Exact owner definitions remain ahead of surrounding consumers and Graph-only dependencies; relation relevance propagates only inside the selected mode policy.

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

`stage: bootstrap` is available before active Chosen files exist. It contains AGENTS, the task, explicit Context Docs, product identity/manifests, capability warnings, and canonical product authority. Product authority is `docs/PRD.md` when present; otherwise the task query selects one bounded relevant document under `docs/prd/**`, with `README.md` or `INDEX.md` as deterministic no-query fallbacks. Context Doc paths are resolved once at the input boundary as canonical workspace-relative paths; non-canonical aliases and workspace escapes are rejected, and generated Knowledge views are excluded in both workspace and selected-product document trees. Task Context Docs and the selected product authority are required evidence and never compete with lexical search results. The pack is not required before initial file inspection. `stage: scoped` is used after Discovery has an active Chosen set.

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

Each Task Pack group item records `requirement: required | optional` and preserves `document_role` when the source is a classified shared document. Required evidence is AGENTS, the selected canonical product-authority document, the task source ref, explicit Context Docs, and scoped Chosen refs. Query-relevant authority and procedure candidates receive bounded reservation before general source fill, and bundle candidates whose canonical path is already required or explicit are not projected again. Budget trimming may shorten excerpts and remove optional evidence, but it must never remove required source refs.

## Benchmark Labels

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The benchmark records labeled retrieval metrics plus the actual serialized compact byte size and its estimated token cost. Internal excerpt budgets are not accepted as output-size evidence. Existing source-integrity, forbidden-source, and cross-repo contamination gates remain; ranking thresholds are release gates only when they correspond to agent-visible results and have real benchmark history.
