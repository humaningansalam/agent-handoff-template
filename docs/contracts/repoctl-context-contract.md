# repoctl Context contract

`repoctl context query` returns a non-authoritative evidence bundle for one explicit product repository. It never mutates product source, task scope, or project authority; after a successful query it atomically stores one bounded regenerable result receipt under `.repoctl-state/result-receipts/**` so later Discovery input can prove membership in the producer evidence manifest. Compact visibility and citable membership are separate: omitting evidence from the display budget does not make a producer-returned member uncitable.

Context is not authoritative. Source authorities remain the repo registry, source documents, `.repometa`, task completion receipts, and reviewed knowledge records. Graph contributes derived relationship evidence only.

## Implementation boundary

This document describes the implemented Context query and Task Pack surface. Ordinary queries use the persistent current-evidence index, stale-path overlays, and the completion catalogue's finite file-outcome frontier; they do not enumerate cold completion history. They first fix a bounded current working set, then join only independently retrieved current files to the exact retained cell for each file's current version. Fresh Reviewed/Chosen/passed outcomes may corroborate ordering only after current identity and query evidence; Excluded and other roles remain visible evidence and never become scope or a hard filter. Outcomes for documents, symbols, relationships, tasks, artifacts, and Knowledge stay in immutable cold evidence or their explicit lifecycle stores. Context also projects declared, set-aware component membership and confirmed crossings from the current Graph snapshot. Stored producer result receipts remain flat; Context exposes a separate versioned public projection, and Discovery freezes canonical capsules only for explicitly selected manifest members. `past_decision` and `failure_mode` are separate explicit cold-history modes whose results are isolated to `related_history`.

Context is not a complete Git diff manifest. When the caller explicitly asks to review the current changed set, Git status/diff owns membership and Context or Graph may add meaning and relationships after that set is fixed. A stale-path overlay replaces stale indexed text for query ranking; it does not prove that every changed path is visible in a bounded semantic result. Field-specific score boosts require labeled benchmark evidence rather than treating one repository's dirty paths as a universal ranking rule.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, `auto` uses the persistent FTS/source-symbol index to choose bounded owner/source/test/config hypotheses, exact structured-data/dotfile matches, and applicable project knowledge. It projects provider-confirmed call/import/test/structured-file relations from up to three resolved typed anchors while keeping source, test, authority, history, and reviewed-Knowledge lanes separate. Reviewed Knowledge qualifies through either an exact query match or an explicit source/path relation; reviewed status alone is never sufficient.

Without `--json` or `--format markdown`, the text view still lists the compact working set, Graph-anchor provenance, and bounded continuations; it is not a digest-only status line.

Context never triggers a hidden Graph build. With a healthy materialization, queries read the persistent SQLite evidence index and overlay only changed paths. If the Graph snapshot or evidence index is missing, unreadable, incompatible, or digest-mismatched, Context returns a typed partial result from live source, declared config, structured data, document, task, and Knowledge evidence instead of hard-failing. Graph relations are marked unavailable, while JSON `next_actions` use the structured cause code to return the required `graph build` or `graph build --rebuild` command plus a command that resumes the same Context query. When a Graph snapshot remains usable and only indexed source evidence is degraded, the original typed evidence warning remains visible and is not relabeled as `context_graph_unavailable`. The fallback never repairs Graph/index state; a successful result writes only its regenerable result receipt.

Text indexing is limited to files up to 1 MiB. Registered Context-source languages use `current_source`; semantic Graph support remains a separate capability. This makes HTML and stylesheet files searchable without claiming symbols, calls, or imports for them. Well-known product manifests keep `product_manifest`; formats and boundaries that explicitly denote configuration, such as INI/env-style files, Dockerfile variants, Compose files, workflow files, and non-document repository dotfiles, use `config`. A hidden Markdown file outside the canonical product-document paths does not become configuration merely because its name starts with a dot. General JSON/YAML/TOML/XML content that is not already registered as a manifest or configuration boundary uses `structured_data`. That data remains indexable and selectable by exact path or filename and can be reached through explicit Reviewed-Knowledge applicability or a typed Graph relation, but body vocabulary alone cannot promote it into `likely_change_surface` or a lexical Graph seed. The persistent index and live fallback call the same source-role owner. Lexical and path/filename identity comparison canonicalizes case, separators, snake case, and CamelCase into ordered parts. Exact provider symbol and provider-relationship identity instead use a separate linear raw-surface scanner and case-insensitive, surface-preserving comparison: punctuation, whitespace, and identifier-part boundaries inside the selected surface are not deleted or rejoined. Whitespace and the closed grouping, call, and list delimiter set of parentheses, brackets, braces, commas, and semicolons may bound an unquoted provider selector. Any other adjacent sigil, operator, or punctuation remains inside that surface even when the lexical token grammar would split on it, so an internal token never receives an independent exact identity by losing `$`, `#`, `@`, or another unclassified boundary. Unicode combining marks that form one valid identifier remain identifier constituents rather than being reclassified as sigils; an ordinary unsupported-alphabet identifier inside prose still requires a whole-query, bounded quote, or real sigil to become exact. Quoted content preserves every character inside the quotes, and its token substrings likewise do not independently authorize a second exact provider identity. A quoted pair forms an independent selector only when both sides meet the same start, end, whitespace, or closed-delimiter boundary; embedded and repeated quote pairs remain part of the surrounding surface. Whole-query quote removal requires one matched enclosing pair; an unmatched quote remains in the provider surface. Non-empty whole-query and bounded quoted raw surfaces are exact-eligible independently of the lexical canonicalizer's supported alphabet, and sigiled unquoted surfaces are scanned without first requiring a lexical token, so non-ASCII or symbolic provider identities are not discarded before comparison. Token cleanup used for lexical file/path identity never changes the provider-exact surface. Exact full path, selected-repo suffix, filename, and provider identity remain eligible independently of broad body recall and outrank ordinary body-term matches. A whole-query identity, an explicitly quoted identity, or a code-shaped token such as a path, dotted name, snake/camel identifier, flag, or alphanumeric identifier may form an identity selector. Selector intent alone does not authorize punctuation rewriting for provider exactness, so `SHA-256`, including a quoted `"SHA-256"`, is not an exact match for a provider symbol reported as `sha256`; it may remain named or lexical evidence. An ordinary word inside a longer natural-language request does not become an exact symbol selector by itself. A natural-language sequence that happens to match a filename or provider symbol may strengthen lexical ranking, but only a query-explicit named identity joins the named Graph-anchor cohort. If one such basename resolves to multiple current paths, Context reports `ambiguous` and performs no traversal for that cohort. When one query-explicit named-file cohort and one exact provider symbol identify different paths without an exact file selector, Context likewise reports `ambiguous`; when both identify the same path, the provider-symbol anchor retains its symbol-specific traversal boundary.

Shared documents receive one closed `document_role` classification from their canonical path: operating authority, product authority, governance authority, procedure, reference, template, generated view, or unspecified. The same role owner drives indexed and live-fallback retrieval lanes, authority weighting, compact grouping, and Task Pack projection. `docs/PRD.md` and split `docs/prd/**` documents retain product-authority semantics; active procedures have their own bounded recall lane; references remain lower-priority supporting material. A document's location inside the product repository does not upgrade a reference into authority. Templates are excluded from ordinary semantic recall but remain reachable through an explicit path or filename identity. Generated Knowledge views are never eligible source evidence.

The persistent index performs bounded, path-diverse chunk recall independently for product source, product tests, exact structured data, product documents, canonical workspace documents, governance, procedures, other workspace documents, and supporting evidence. Ordinary prior-outcome retrieval does not rank raw completion receipts or task-artifact bodies: it joins bounded current candidates to the shared current-subject frontier. Explicit `past_decision` and `failure_mode` queries search cold catalogue history on demand, but their bounded matches remain isolated in `related_history` and cannot affect current retrieval, ranking, Graph projection, component projection, or scope inputs. Auto retrieval reserves one best result from each non-empty active lane before filling the remaining per-lane quotas, so a large source corpus cannot starve later procedure or applicable current-outcome evidence. The lane candidates are merged before one field-aware rank. Changed-path overlays replace stale indexed chunks before the same lane-balanced selection. Canonical ordering compares typed anchor strength before the composite score, so an exact identity cannot be displaced by an arbitrarily large weak body score. Path, section/symbol, and body coverage remain separate evidence fields. The `fts` diagnostic is the sign-normalized relevance `-bm25(chunks, 4.0, 3.0, 1.0)`, preserving SQLite's BM25 magnitude and ordering rather than exposing the negative raw rank or converting result position into a synthetic score.

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

The JSON payload is `repoctl.context.bundle` schema version 15 with `authoritative: false`.

Default `--json` output is the compact agent-facing view. It includes:

```text
query.mode
completeness
groups
relationship_candidates
component_crossings
component_crossing_count
component_crossings_truncated
graph_seed_refs
continuations
bundle_digest
```

Visible source items and seed-anchor coverage may carry `component_ids`; confirmed query-relevant crossings carry the endpoint membership sets. These fields are present only in compact JSON. Text and Markdown remain concise human views and do not reproduce every topology annotation.

The command envelope also returns `data.result_receipt` using `repoctl.repository-understanding.result-receipt-projection` schema version 1. This public projection carries the producer, canonical typed request, result ID, and receipt digest without copying the stored flat receipt into every response. The default view lists visible member tuples at `compact.representative_citations[*].primary_citation` and reports visible, cited, manifest-member, selectable, omitted, and per-authority omitted counts. `--full` adds the same immutable manifest under `manifest.items`; it never widens membership. Request and manifest-member fields retain their declared JSON types instead of coercing numbers or other values to strings. Legal Git path characters are encoded by the machine-record serializer rather than rejected because of Markdown delimiters. Receipt reads and writes reject a receipt file or parent path that is a symlink or resolves outside the workspace. A projected representative citation that is not in the stored manifest fails with `result_receipt_projection_invalid`. Repeating an identical result is byte-idempotent; the same producer/repository/result identity with a different request or membership fails with `result_receipt_conflict` instead of overwriting evidence.

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

Compact groups coalesce evidence by canonical subject while preserving typed sections and claims. Exact identity and freshness rank ahead of current typed connectivity; lexical relevance remains a hypothesis. The bounded projection preserves source/test/document/Knowledge diversity and explicitly accounts for omitted eligible evidence. Language, extension, directory labels, and display roles never prove ownership. Compact/full JSON, completeness, result citations, seed refs, and Task Pack consume the same producer-owned selection identity. Human views present a concise subset without changing that identity.

In Graph-expanding modes, compact source selection closes around the primary query identity. For one resolved exact, explicit, or strong primary anchor, the existing source budget reserves exactly one current resolved distance-one Graph-only endpoint when one exists: `CALLS` and `IMPORTS_FILE` may connect in either direction, while a structured-file relation connects only from the primary to its endpoint. A second Graph-only neighbor cannot consume an optional source slot; independently query-identified sources and bounded multi-anchor working sets remain eligible. A novel query term, role label, or directory proximity alone does not open another source slot.

Test selection keeps a query-explicit test first, then a current test connected to a visible source only by a resolved distance-one `TESTS_FILE`, `IMPORTS_FILE`, or `CALLS` relation directed from test to source. Unresolved, reverse, structured-file, and longer-path relations do not establish that connection. A lexical test is suppressed only when the Graph snapshot is current and that exact test path was successfully analyzed without failure by every applicable import/call provider; a provider that classifies the path as unsupported is ignored. Global partial capability does not disable usable path-scoped evidence, while any applicable failed or unanalyzed path retains lexical fallback. The shared identity tier also keeps a query-named test ahead of a weak test echo reached from a secondary source. These relations remain exploration evidence and never imply authority, ownership, edit scope, or Chosen scope. When actionable product source or test evidence exists, compact `must_read` suppresses root operating, governance, and procedure documents supported only by weak lexical path, section, body, or FTS evidence. Full evidence retains them, while product-repository documents, query-explicit exact or named document identities, and authority/startup/invariant modes remain eligible.

A typed relation corroborates a candidate only when its endpoint or analyzer source-fact identifies that candidate's exact canonical subject. Explicit `file_impact` may use file-scope evidence; other file-wide relations remain navigation evidence. Corroboration may affect bounded ranking but never changes the subject's authority, edit scope, or underlying Graph role.

The first unfamiliar query is a ranked, provenance-bearing hypothesis rather than an oracle result.

`groups` organizes selected evidence into:

```text
must_read
likely_change_surface
callers_and_dependents
tests_and_verification
reviewed_knowledge
prior_task_outcome
related_history
supporting_evidence
warnings_and_completeness
```

In `auto`, `reviewed_knowledge` accepts exact record matches as well as records structurally linked to selected paths. `prior_task_outcome` is a separate bounded file-only lane joined only after current candidates are fixed. The join requires the same repository, stable `file:<path>` role key, exact current file-version digest, and an independently retrieved current file candidate. A fresh Reviewed/Chosen/passed role may corroborate later ordering behind current typed and identity evidence; other recorded roles remain neutral or negative episode evidence without suppressing a current candidate. The join never changes Graph seeds, creates relations, or expands eligibility. Catalogue unavailability produces one typed warning and no cold fallback; hot-policy subject omission reports partial completeness rather than a false authoritative miss.

Raw completion history is queried only when the caller selects `past_decision` or `failure_mode`. Those modes append bounded, validated task/artifact matches to `related_history`; cold matches do not participate in current candidate eligibility or ranking, `likely_change_surface`, Graph anchors, `graph_seed_refs`, traversal, component selection, or task scope. An empty current lane remains empty rather than being populated from history.

For Graph-expanding modes, a reviewed Knowledge result becomes a code anchor only when its typed query match is `exact` or `strong`. Weak partial/FTS matches may remain visible in the Knowledge lane but cannot project code. Code paths come only from the record's literal `applies_to.paths` entries and `source_refs` explicitly typed as `current_source` that resolve to a current file inside the selected repository. Root workspace documents and all other source-ref kinds remain provenance-only; legacy scope/file aliases, task-derived changed-file prose, claims, summaries, titles, and filenames inferred from text are never code applicability.

Knowledge paths use the same repo-relative/workspace-relative resolver as Graph selectors with the current repository path set. Ambiguous, invalid, missing, stale, superseded, deprecated, or cross-repository paths fail closed. Full results expose `query_match_strength`, `code_anchor_status`, `code_path_resolutions`, and `resolved_code_paths`. A resolved code candidate carries `reviewed_knowledge_path` evidence and related record IDs, but its role is `knowledge_linked_source` or `knowledge_linked_test`; it is exploration evidence, not edit scope or authoritative ownership. A direct exact code identity remains stronger than a Knowledge-linked path.

Applicability navigation is separate from code-anchor eligibility. A current Reviewed Knowledge result may expose `applicability_path_resolutions` and `resolved_applicability_paths` from its explicit `applies_to.paths` entries even when its query match is weak. When that Knowledge item is selected into the compact bundle, a resolved applicability path may add a bounded `workspace.open` / `graph.file` continuation. This continuation is navigation only: it never creates source evidence, seeds Graph expansion, changes ranking or `code_anchor_status`, enters `likely_change_surface`, or implies edit scope. Provenance-only refs and ambiguous, invalid, missing, non-current, or cross-repository paths do not produce applicability continuations.

The repository identity is stored once at bundle level. `graph_anchor` reports typed anchor resolution; `graph_anchor.selection_coverage` reports which eligible anchors fit the traversal bound; root `working_set_coverage` reports which eligible subjects fit the visible bundle. These boundaries are independent. `resolved` proves current Graph identity, not semantic ownership. JSON preserves the complete typed projection and omission accounting; text and Markdown preserve source-linked working-set provenance and continuations but may omit JSON-only topology annotations. Full output may add diagnostics but cannot change selection identity. `project_knowledge` remains three distinct lanes: current project documents, task history, and explicitly reviewed reusable records. A lane that was not loaded or queried reports `null`, not a misleading zero.

## Graph Evidence

Context consumes the materialized Graph through internal Python objects; it must not parse `graph query` stdout or invoke compiler providers. Context passes only typed anchors to Graph. Exact ambiguity fails closed. In a multi-anchor request, fresh unambiguous anchors may continue while stale, ambiguous, or unresolved anchors remain explicitly accounted. Traversal is bounded by mode and preserves anchor provenance and typed continuations. Candidate ranking and deterministic tie-breaks are implementation strategy, not authority semantics.

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

When product source is stale, Context excludes every Graph relation whose endpoint is stale and overlays only the current file text. The in-memory evidence query replaces stale rows with those current chunks before FTS evaluation; its scored overlay rows remain the retrieval candidates instead of being overwritten by unscored raw copies. If a query-explicit identity is stale in Graph, anchor resolution is `unresolved`: its current source text may remain the compact primary, but Context must not substitute a weaker fresh lexical candidate or use that candidate to regain traversal. Provider configuration or Graph/provider input-version drift has no equivalent live relation overlay, so Context reports the materialized Graph as stale and requires an explicit Graph refresh. When receipt or task-artifact evidence is stale, related history is omitted and `task_history` becomes partial until the next explicit Graph build. Root document changes such as `docs/BOARD.md` do not force a product-wide fallback scan.

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

There is no mandatory Context -> Graph -> `rg` order. Start from Context for ambiguous intent. When it returns a fresh, connected, provenance-bearing set with sufficient coverage, use that as the initial working set; refine, refresh, traverse, or fall back to exact search/direct reads when it reports ambiguity, staleness, or missing coverage. Use Graph or direct read for a known file, narrow `rg` for a known exact string or symbol, and task/Knowledge for past decisions. Typed Graph continuations may be followed repeatedly in any useful direction. Create or start the task before the first product mutation, then record Candidate, Reviewed, and Chosen evidence as scope becomes concrete. A scoped Context Pack is optional durable handoff evidence, not an exploration gate. The pack is non-authoritative. Default `--json` output is compact and contains:

```text
stage
input_digest
stop_reason
budget
render_projection
seed.query_preview
seed.notes
seed.selected_result_evidence
seed.graph_seed_refs
groups.must_read
groups.edit_candidates
groups.supporting_evidence
groups.likely_change
groups.impact
groups.verification
groups.warnings
```

`render_projection` is `full` when detailed Markdown fits the requested budget. If required sources dominate the budget, it becomes `required_reference_manifest`: full JSON keeps required item details and digests, while Markdown and compact JSON project required items to source identities so an agent can open them directly without paying for repeated excerpts.

Use `--full --json` to include the raw nested Context bundle and full evidence details.

When `--output` is supplied, the requested path is invalidated before generation and the full artifact is written there only after successful construction. A failed run therefore cannot leave an older pack at that path looking current. Markdown stdout reports only the artifact path instead of duplicating the complete pack; omit `--output` when the rendered Markdown itself is required on stdout.

Task Pack generation does not activate resume guidance. After reviewing the current four-field Handoff, bind it explicitly:

```bash
./scripts/repoctl task handoff bind T-... --json
./scripts/repoctl task handoff bind T-... --context-pack .repoctl-state/context-pack/T-....md --json
```

`task start` and `task show` never create or replace a binding. The machine binding receipt stores exact Handoff and structured Task/repository input digests plus the optional Pack path, artifact digest, and input digest; it does not interpret or certify Handoff prose. Separately, task creation records generated Handoff body digests and template version under machine-owned origin state. An unchanged generated placeholder is readable with a typed warning but cannot be bound, even after renderer/config copy changes or under a receipt written by an older repoctl. A Handoff without origin state is never classified by comparing its prose with current or frozen historical templates. It remains typed inactive under an older binding while the agent regenerates, replaces, or explicitly reviews it; one fresh bind records that review directly in the provenance-aware binding receipt. `task show --summary --json` reports `current | unbound | stale | unknown | historical`, and only `current` is freshness-active. `task resume --json` owns workspace selection as `no_live | single_live | ambiguous`, never falls back to archived history, and preserves the selected task plus typed unhealthy health even when repository layout parsing fails. It returns current prose as `readable_handoff`; it returns the same prose as `executable_handoff` only when repository lifecycle health is also executable. Context history and its `task.show` continuation remain inspection evidence, not resume selection. A malformed receipt, origin state, or live Handoff fails closed. Archived Handoffs remain readable historical evidence.

JSON Pack binding verifies the current task-pack schema and canonical Pack digest. Markdown binding requires the fixed first-line machine envelope with schema versions, Task/repository identity, canonical `input_digest`, and a digest of the rendered body. Legacy Markdown without that envelope is historical text only. Binding and inspection never rewrite the artifact.

`stage: bootstrap` is available before active Chosen files exist. It contains AGENTS, the task, explicit Context Docs, product identity/manifests, capability warnings, and canonical product authority. Product authority is `docs/PRD.md` when present; otherwise the task query selects one bounded relevant document under `docs/prd/**`, with `README.md` or `INDEX.md` as deterministic no-query fallbacks. Context Doc paths are resolved once at the input boundary as canonical workspace-relative paths; non-canonical aliases and workspace escapes are rejected, and generated Knowledge views are excluded in both workspace and selected-product document trees. Task Context Docs and the selected product authority are required evidence and never compete with lexical search results. The pack is not required before initial file inspection. `stage: scoped` is used after Discovery has an active Chosen set.

`edit_candidates` contains exactly the active Chosen set. `supporting_evidence` contains Reviewed minus Chosen, so the two sets are disjoint. That subtraction is neutral supporting evidence. Context does not infer edit scope from task prose, receipt history, basenames, or generated/ignored files.

Retrieval query text comes from the active Discovery episode's single Candidate query. A distinct query clears earlier Reviewed/result/note evidence and carries current Chosen scope forward. A selected Context receipt supplies the exact query; same-query modes and typed Graph follow-up selectors accumulate without changing the episode. Goal and Handoff prose are not parsed as symbols. A test is directly connected only through explicit Discovery evidence, a provider-confirmed relation, or manifest mapping.

Task packs do not query reviewed knowledge or completion history. Both indexed and live-fallback retrieval exclude those source kinds before ranking, so hidden history cannot change Pack source/test selection. Use normal `context query` for path-linked history, an explicit historical mode for broader retrieval, or an explicit Context Doc when a task pack needs durable historical context.

Task Pack schema version 4 carries the active query, Reviewed and Chosen sets, notes, flat selected-result provenance with its validated typed request and episode identity, and the exact Context-produced `graph_seed_refs`. Full JSON retains its exact inputs. Default compact JSON bounds query and note text; selected-result provenance keeps its stable IDs, authority and ref plus a typed `request_preview` and a `request_digest` of the exact request. Result receipts are checked only when a new selection is recorded; deleting a regenerable receipt later does not invalidate task Markdown or a Pack derived from that recorded evidence. Legacy selected-result records remain historical without guessing a missing request.

`input_digest` covers task content, the active Discovery episode, Reviewed and Chosen sets, current notes and selected-result provenance, exact Graph seed refs, explicit Context Docs, the canonical source identities produced by required/discovery/fallback/query/verification candidate owners, repository identity and content fingerprints, observed HEAD, Graph snapshot, and capability matrix. Pack construction and freshness inspection call the same collector and projection; they do not reinterpret a source digest as raw file bytes or maintain a second fallback path. A saved pack is stale when recomputing those inputs produces a different digest; read-only inspection commands do not rewrite it.

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

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. `noise` is the canonical contamination label. Legacy `forbidden_refs`, `selected_forbidden` / `forbidden_selected`, and `--require-no-forbidden` identify that same visible-noise set rather than a second classification; `generated_or_ignored_noise` remains a separate derived diagnostic. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The benchmark records labeled working-set recall, first-correct rank, visible recall, precision, labeled noise/forbidden selection, Graph-edge recall, actual serialized compact byte size, and estimated token cost. Fixtures include ordinary-language symbol collisions, repository-area isolation, direct owner/test working sets, and multi-owner Graph traversal. Public-flow tests assert role-correct placement and traversability; blind-agent field copies determine whether a normal development request reuses the working set instead of repeating broad discovery. Internal excerpt budgets are not accepted as output-size evidence. Ranking thresholds are release gates only when they correspond to agent-visible results and have real benchmark history.

`context benchmark --attribution` is an explicit diagnostic mode that appends one non-authoritative fixture capsule to the existing benchmark artifact. It keeps `available`, `retrieved`, `compact_visible`, `selected`, `reviewed`, `chosen`, `verified`, and `later_reused` independent, uses `unknown` when the required exact artifact was not captured, and records retrieval rank, lane, score breakdown, and typed Graph/Knowledge contribution without feeding any value back into ranking or lifecycle state. When one exact result member has multiple full-evidence occurrences, its retrieval rank and scalar diagnostics come from the first occurrence while typed Graph/Knowledge flags are unioned across all occurrences. Selected evidence must match an exact producer result/member citation; Reviewed, Chosen, and verification come only from the captured completion outcome; later reuse additionally requires a later cited exact subject/version outcome or a canonically valid approved Knowledge record. Captured timestamps require timezone offsets. Every capsule remains `claim_scope: correlation_only` and non-gating. Benchmark comparison validates identical pinned commit, agent, model, prompt digest, and permission digest, at least four repetitions per arm, distinct canonical output paths and file digests, command-generated execution identities, and path-revalidated workspace witnesses. Those in-band artifacts are caller-rewritable, so the current comparison always reports `insufficient_evidence` with `independent_execution_receipt_missing`; only a future independently issued harness receipt that binds pre-run workspace evidence to the final output could permit `eligible`. Every incomplete, mismatched, missing, tampered, copied, shared, or same-workspace execution artifact remains insufficient evidence. Benchmark Graph/provider state, including bundled semantic tools, stays under the temporary benchmark state root, so a cold diagnostic run writes only an explicitly requested output artifact. Default benchmark, field-gate, Context query, Task, result receipt, completion history, Graph, and Knowledge behavior do not consume or emit this optional capsule.
