# repoctl Context contract

`repoctl context query` returns a read-only evidence bundle for one explicit product repository.

Context is not authoritative. Source authorities remain the repo registry, source documents, `.repometa`, task completion receipts, and reviewed knowledge records. Graph contributes derived relationship evidence only.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, `auto` uses the persistent FTS/source-symbol index to choose a bounded owner/source/test hypothesis set, exact config/dotfile matches, and applicable project knowledge. It projects provider-confirmed call/import/test/structured-file relations from up to three resolved typed anchors while keeping source, test, authority, history, and reviewed-Knowledge lanes separate. Reviewed Knowledge qualifies through either an exact query match or an explicit source/path relation; reviewed status alone is never sufficient.

Without `--json` or `--format markdown`, the text view still lists the compact working set, Graph-anchor provenance, and bounded continuations; it is not a digest-only status line.

Context never triggers a hidden Graph build. With a healthy materialization, queries read the persistent SQLite evidence index and overlay only changed paths. If the Graph snapshot or evidence index is missing, unreadable, incompatible, or digest-mismatched, Context returns a typed partial result from live source, config, document, task, and Knowledge evidence instead of hard-failing. Graph relations are marked unavailable, while JSON `next_actions` use the structured cause code to return the required `graph build` or `graph build --rebuild` command plus a command that resumes the same Context query. The fallback is read-only and never repairs materialized state.

Text indexing is limited to files up to 1 MiB. Registered Context-source languages use `current_source`; semantic Graph support remains a separate capability. This makes HTML and stylesheet files searchable without claiming symbols, calls, or imports for them. JSON/YAML/TOML/INI/env-style configuration, Dockerfile variants, Compose files, workflow files, and repository dotfiles use the separate `config` kind; manifests and documents keep their own typed kinds. Identifier comparison canonicalizes case, separators, snake case, and CamelCase into ordered parts. Exact full path, selected-repo suffix, filename, and provider symbol/section identity remain eligible independently of broad body recall and outrank ordinary body-term matches. A whole-query identity, an explicitly quoted identity, or a code-shaped token such as a path, dotted name, snake/camel identifier, flag, or alphanumeric identifier may form an exact selector. An ordinary word inside a longer natural-language request does not become an exact symbol selector by itself.

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

The JSON payload is `repoctl.context.bundle` schema version 13 with `authoritative: false`.

Default `--json` output is the compact agent-facing view. It includes:

```text
query.mode
completeness
groups
relationship_candidates
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

`evidence` contains raw query-matching source/document chunks plus provider-confirmed Graph relations projected from lexical source and test anchors. Retrieval selects the strongest matching chunk from distinct paths before allowing additional sections from the same path, so one large file cannot consume the candidate set. Full evidence exposes typed `evidence_kinds`, `anchor_strength`, `document_role` when applicable, canonical `query_term_matches` separated by path/provider-section/body field, field score diagnostics, and section kind; display-only selection reasons do not drive seed or ranking decisions. Token cost never participates in retrieval or evidence selection.

`relationship_candidates` is a separate non-authoritative lane, not evidence that an edge exists. Analyzer-owned relationship source facts are indexed as `provider_relationship` sections with their `source_fact_id`, so an exact runtime identity selects the corresponding analyzer fact without parsing query prose inside Graph or collapsing identity to a line range. Context projects compatible unresolved candidates selected by that fact ID, or from its bounded typed file-anchor set when no exact relationship identity exists, even when multiple anchors prevent confirmed Graph traversal. Every item keeps the source location, exact runtime identity, structured resolution state/reason, compatible target definitions, target counts/truncation, and typed continuations. Stale source or target paths are excluded. Candidate targets do not receive Graph ranking score, enter `relations`, or become Chosen scope automatically.

Compact groups merge chunks with the same path into one file-level item and preserve their locations in a `sections` list. A global budget returns at most eight actionable items, normally three to eight when enough evidence exists, with small per-lane limits so authority, source, test, history, and Knowledge do not compete in one ranking. Source and test compaction uses the same bounded evidence-coverage rule as lexical anchor selection: every current source/test/config profile remains eligible, while the visible set prefers bounded lexical breadth, source/test/config lane, evidence role, and repository component before typed Graph coherence. Repeated vocabulary or the same term echoed across several fields cannot accumulate unbounded selection value. A weak isolated singleton can remain an explicit omission instead of consuming a compact slot when it adds no lane, role, component, exact identity, or Graph-supported evidence. Redundant lexical consumers may be omitted from the compact view, but full evidence keeps every hypothesis for diagnostics. Text-only and Graph-unsupported files use the same lexical coverage path. When matching authority and procedure documents both exist, compact `must_read` reserves one of each before filling its remaining slots. Each item carries its selection reason; typed continuations are deduplicated at bundle level. `graph_anchor.selection_coverage` describes only the bounded seed set. Separate root-level `working_set_coverage` describes the source/test items actually visible after per-lane and global compact bounds, so a complete anchor set cannot hide a partial displayed working set. Full selection diagnostics retain every eligible, selected, omitted, and coverage-omitted path; compact diagnostics bound those path lists without flattening field identity.

When one path has both direct query evidence and Graph relations, its compact primary role remains `change_candidate` or `test_candidate`; provider-confirmed relationships remain available through relation evidence and typed continuations. A test reached directly from a non-test Graph anchor also carries `anchor_connected_test`, which ranks ahead of a lexical test seed that is connected only to its own unrelated dependencies. A path reached only through Graph keeps its dependency role.

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

The repository identity is stored once at bundle level. Compact completeness contains operational Graph availability, freshness state, the root-evidence drift indicator, and a structured `graph_anchor` status/code for Graph-expanding modes. It also exposes `project_knowledge` as three explicit lanes: current project documents, task history, and explicitly reviewed reusable records. A lane that was not loaded or queried reports `null` instead of a misleading zero or an empty loaded value, including reviewed-record lifecycle data. `resolved` includes `seed_anchors` with path, typed provenance, anchor strength, and lexical rank/lane when applicable; it means the typed anchor resolved in the current Graph, not that semantic ownership is proven. Orthogonal `graph_anchor.selection_coverage` and root `working_set_coverage` each report `complete | partial`, eligible/selected/omitted counts, coverage-omitted paths, `unrepresented_field_term_evidence`, and bounded lane/role/component diagnostics for their own selection boundary. Field identity is preserved: a body match omitted after the same canonical term was selected from a path remains body-field evidence and is never relabeled as an unrepresented query term. `ambiguous` preserves conflicting exact candidates without choosing one, and `unresolved` reports that no eligible typed anchor exists. Compact JSON, plain text, and Markdown render the same anchor provenance and both coverage meanings. Freshness counts and materialization digests are diagnostic-only. Typed continuations are deduplicated once at bundle level: current source, config, and test items own their file selector; documents own their document selector; reviewed Knowledge owns its `knowledge_record` selector and any validated navigation-only applicability selector; completion history owns its task selector; and a pure call relation owns its exact symbol selector with `in_file`. Repeated repo IDs, score breakdowns, provider inventories, and raw relation paths are omitted. Full output retains deterministic scoring, item-level continuations, typed anchor provenance/rank/lane diagnostics, and relation evidence including merged `origin_paths`, per-origin distance, edge kinds, providers, and minimum distance.

## Graph Evidence

Context consumes the materialized Graph through internal Python objects; it must not parse `graph query` stdout or invoke compiler providers. Graph expansion has one anchor-resolution owner and one traversal consumer. It first preserves exact path/filename/symbol/relationship evidence and explicit reviewed-Knowledge paths. If neither exists, it aggregates every current source/test/config candidate by file and selects bounded typed lexical-file hypotheses from canonical path, provider-section, and body query matches. No mixed cohort may delete a single-term candidate from eligibility or omission diagnostics. Only when every selectable candidate is a weak single-term match does selection keep one top fallback, with the remaining paths still explicit. Selection greedily adds candidates that contribute bounded new query-concept breadth, a new source/test/config lane or evidence role, or a distinct repository component. Field identity remains intact in diagnostics, while lexical breadth saturates before repeated terms or multi-field echoes can dominate the bound. Fresh direct-test relations and typed relations among the current query candidates then corroborate otherwise comparable candidates without proving semantic ownership; raw Graph degree remains a later tie-break and cannot displace stronger distinct query evidence. Shared-parent affinity is the final deterministic tie-break. Stale candidates remain visible in candidates and omission diagnostics but cannot consume a traversal slot. The support profile and neighborhood traversal reuse one projection index for the same snapshot. At most three anchors traverse, including Reviewed-Knowledge anchors. Every full anchor reports `exact_identity`, `provider_symbol`, `reviewed_knowledge`, or `lexical_file` provenance; lexical hypotheses are never relabeled as exact. If eligible structured evidence remains outside the bound, `graph_anchor.selection_coverage` becomes `partial` without changing the meaning of anchor resolution.

Exact identity kinds do not use a numeric path/filename/symbol precedence. One exact symbol plus exact file evidence for the same path is a compatible interpretation and keeps the narrower symbol anchor. Multiple exact symbols, multiple exact file paths, or exact symbol/file interpretations that point at different paths return `ambiguous`. Exact ambiguity expands nothing. For a bounded lexical or Knowledge hypothesis set, stale or snapshot-missing anchors are accounted individually: valid fresh anchors continue traversal, while the resolution becomes `unresolved` only when none remain. Diagnostic candidates are retained.

Mode policies are explicit and bounded:

```text
auto          direct incoming/outgoing call, import, structured-file, and test relations; depth 1
code_location outgoing callees/imports/structured dependencies plus direct tests; depth 1
call_impact   incoming/outgoing calls through depth 2 plus direct tests; imports are excluded
file_impact   incoming/outgoing imports and structured-file relations through depth 2,
              plus direct calls and tests at depth 1
```

A provider-symbol anchor restricts first-hop `CALLS` relations to that exact symbol rather than every symbol in the file. It does not attach file-wide import or structured-file dependencies that cannot be attributed to that symbol; direct file-level tests remain eligible. Exact owner definitions remain ahead of surrounding consumers and Graph-only dependencies; relation relevance propagates only inside the selected mode policy.

Context never passes raw free-form tokens to Graph. Retrieval converts structured current-source evidence into typed file/provider-symbol anchors, and Graph accepts only those typed anchors. Graph-derived Context items use `source_ref.kind: graph_relation`, preserve exact provider relations and endpoint identities, and remain current-query evidence rather than durable knowledge records. When the same relation is reachable from multiple seeds, one relation is emitted with the union of `origin_paths` and the minimum distance for each origin; origin count does not increase its score. Explicit `repoctl graph query` selectors remain a separate iterative exploration interface.

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

When product source is stale, Context excludes every Graph relation whose endpoint is stale and overlays only the current file text. Provider configuration or Graph/provider input-version drift has no equivalent live relation overlay, so Context reports the materialized Graph as stale and requires an explicit Graph refresh. When receipt or task-artifact evidence is stale, related history is omitted and `task_history` becomes partial until the next explicit Graph build. Root document changes such as `docs/BOARD.md` do not force a product-wide fallback scan.

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

There is no mandatory Context -> Graph -> `rg` order. Start from Context for ambiguous intent and use its resolved owner/test/impact working set instead of restarting the same broad discovery. Use Graph or direct read for a known file, narrow `rg` for a known exact string or symbol, and task/Knowledge for past decisions. Typed Graph continuations may be followed repeatedly in any useful direction. Create or start the task before the first product mutation, then record Candidate, Reviewed, and Chosen evidence as scope becomes concrete. A scoped Context Pack is optional durable handoff evidence, not an exploration gate. The pack is non-authoritative. Default `--json` output is compact and contains:

```text
stage
input_digest
stop_reason
budget
render_projection
must_read
edit_candidates
supporting_evidence
likely_change
impact
verification
warnings
```

`render_projection` is `full` when detailed Markdown fits the requested budget. If required sources dominate the budget, it becomes `required_reference_manifest`: full JSON keeps required item details and digests, while Markdown and compact JSON project required items to source identities so an agent can open them directly without paying for repeated excerpts.

Use `--full --json` to include the raw nested Context bundle and full evidence details.

When `--output` is supplied, the requested path is invalidated before generation and the full artifact is written there only after successful construction. A failed run therefore cannot leave an older pack at that path looking current. Markdown stdout reports only the artifact path instead of duplicating the complete pack; omit `--output` when the rendered Markdown itself is required on stdout.

Task Pack generation does not activate resume guidance. After reviewing the current four-field Handoff, bind it explicitly:

```bash
./scripts/repoctl task handoff bind T-... --json
./scripts/repoctl task handoff bind T-... --context-pack .repoctl-state/context-pack/T-....md --json
```

`task start` and `task show` never create or replace a binding. The machine receipt stores exact Handoff and structured Task/repository input digests plus the optional Pack path, artifact digest, and input digest; it does not interpret or certify Handoff prose. `task show --summary --json` reports `current | unbound | stale | unknown | historical`, and only `current` is active. A malformed receipt or malformed live Handoff fails closed. Archived Handoffs remain readable historical evidence.

JSON Pack binding verifies the current task-pack schema and canonical Pack digest. Markdown binding requires the fixed first-line machine envelope with schema versions, Task/repository identity, canonical `input_digest`, and a digest of the rendered body. Legacy Markdown without that envelope is historical text only. Binding and inspection never rewrite the artifact.

`stage: bootstrap` is available before active Chosen files exist. It contains AGENTS, the task, explicit Context Docs, product identity/manifests, capability warnings, and canonical product authority. Product authority is `docs/PRD.md` when present; otherwise the task query selects one bounded relevant document under `docs/prd/**`, with `README.md` or `INDEX.md` as deterministic no-query fallbacks. Context Doc paths are resolved once at the input boundary as canonical workspace-relative paths; non-canonical aliases and workspace escapes are rejected, and generated Knowledge views are excluded in both workspace and selected-product document trees. Task Context Docs and the selected product authority are required evidence and never compete with lexical search results. The pack is not required before initial file inspection. `stage: scoped` is used after Discovery has an active Chosen set.

`edit_candidates` contains exactly the active Chosen set. `supporting_evidence` contains Reviewed minus Chosen, so the two sets are disjoint. Context does not infer edit scope from task prose, receipt history, basenames, or generated/ignored files.

Retrieval query text comes from Candidate query history. Goal and Handoff prose are not parsed as symbols. A test is directly connected only through explicit Discovery evidence, a provider-confirmed relation, or manifest mapping.

Task packs do not query reviewed knowledge or completion history. Use normal `context query` for path-linked history, an explicit historical mode for broader retrieval, or an explicit Context Doc when a task pack needs durable historical context.

`input_digest` covers task content, Discovery query history, Reviewed and Chosen sets, explicit Context Docs, the canonical source identities produced by required/discovery/fallback/query/verification candidate owners, repository identity and content fingerprints, observed HEAD, Graph snapshot, and capability matrix. Pack construction and freshness inspection call the same collector and projection; they do not reinterpret a source digest as raw file bytes or maintain a second fallback path. A saved pack is stale when recomputing those inputs produces a different digest; read-only commands do not rewrite it.

Budget values are estimates and use the name `estimated_tokens`. Early stop is deterministic and reports one of:

```text
required_evidence_satisfied
budget_reached
no_more_eligible_evidence
required_evidence_exceeds_budget
```

`final_render_estimated_tokens` must not exceed `maximum_estimated_tokens` unless even the required-source reference manifest exceeds the budget. That irreducible case returns `ok: false`, problem code `context_pack_required_evidence_exceeds_budget`, and stop reason `required_evidence_exceeds_budget`; it never writes an oversized artifact as a successful pack.

Each Task Pack group item records `requirement: required | optional` and preserves `document_role` when the source is a classified shared document. Required evidence is AGENTS, the selected canonical product-authority document, the task source ref, explicit Context Docs, and scoped Chosen refs. Query-relevant authority and procedure candidates receive bounded reservation before general source fill, and bundle candidates whose canonical path is already required or explicit are not projected again. Budget trimming may shorten excerpts and remove optional evidence. If that is insufficient, rendering may collapse required items to a deduplicated path-and-section manifest, but it must never remove a required source ref. Full JSON retains the detailed required items; the compact reference projection retains source identity and the source-pack digest.

## Benchmark Labels

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The benchmark records labeled working-set recall, first-correct rank, visible recall, precision, labeled noise/forbidden selection, Graph-edge recall, actual serialized compact byte size, and estimated token cost. Fixtures include ordinary-language symbol collisions, repository-area isolation, direct owner/test working sets, and multi-owner Graph traversal. Public-flow tests assert role-correct owner/test placement; actual blind-agent copy checks determine whether a normal development request reuses the working set instead of repeating broad discovery. Internal excerpt budgets are not accepted as output-size evidence. Source-integrity, forbidden-source, and cross-repo contamination gates remain; ranking thresholds are release gates only when they correspond to agent-visible results and have real benchmark history.
