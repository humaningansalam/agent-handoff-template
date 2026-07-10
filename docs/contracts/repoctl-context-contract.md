# repoctl Context contract

`repoctl context query` returns a read-only evidence bundle for one explicit product repository.

Context is not authoritative. Source authorities remain the repo registry, source documents, Graph, `.repometa`, task completion receipts, and reviewed knowledge records.

## Command

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --format markdown
```

`--mode` is optional. When omitted, repoctl classifies the query deterministically. Supported modes are:

```text
code_location
call_impact
file_impact
authority_or_contract
past_decision
invariant
failure_mode
```

Hyphenated aliases such as `call-impact` are accepted.

## Bundle

The JSON payload is `repoctl.context.bundle` with `authoritative: false`.

Default `--json` output is the compact agent-facing view. It includes:

```text
query.mode
source_snapshots
completeness
groups
selected_source_refs
budget
knowledge_result_count
bundle_digest
```

`selected_source_refs` is derived from the full packed evidence, not the display-limited group items. Use it when an agent needs the complete source-ref set for the selected pack.

Use `--full --json` to include raw retrieval/debug fields:

```text
candidates
packed_context
knowledge_results
```

`groups` organizes packed evidence into:

```text
must_read
likely_change_surface
callers_and_dependents
tests_and_verification
reviewed_knowledge
supporting_evidence
warnings_and_completeness
```

Every grouped evidence item keeps `repo_id`, `status`, `source_ref`, `content_sha256`, `selection_reason`, and deterministic scoring or relation evidence when available.

## Graph Evidence

Context consumes Graph through internal Python objects and `query_graph`; it must not parse `graph query` stdout.

Graph-derived items use `source_ref.kind: graph_query` and preserve the Graph relation path. They are evidence for the current query only, not durable knowledge records.

## Markdown Output

`--format markdown` renders the same bundle in a human-readable order:

```text
query interpretation
must-read evidence
change surface
callers/dependents
tests/verification hints
reviewed knowledge
warnings/completeness
```

Markdown output is a view. It must not be ingested as a future Context, Knowledge, or llmwiki source.

## Task Pack

`repoctl context pack` turns a live task into a startup evidence pack:

```bash
./scripts/repoctl context pack --task T-... --repo-id main --format markdown --output .repoctl-state/context-pack/T-....md
```

The pack is non-authoritative and must be read before editing repo files for repo-scoped tasks when available. Default `--json` output is compact and contains:

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
reviewed_knowledge
warnings
```

Use `--full --json` to include the raw nested Context bundle and debug candidate details.

`stage: bootstrap` is used before active Chosen files exist. It contains AGENTS, the task, explicit Context Docs, product identity/manifests, and capability warnings; it must not add raw task history or Graph noise. `stage: scoped` is used after Discovery has an active Chosen set. Chosen/current source and directly connected tests appear before historical receipt evidence.

`edit_candidates` contains only the active Chosen set. Reviewed but unchosen files are `supporting_evidence`. Context does not infer edit scope from task prose, receipt history, basenames, or generated/ignored files.

Retrieval query text comes from Candidate query history. Goal and Handoff prose are not parsed as symbols. A test is directly connected only through explicit Discovery evidence, a provider-confirmed relation, or manifest mapping.

`input_digest` covers task content, Discovery query history, Reviewed and Chosen sets, explicit Context Docs and their content digests, repository identity, observed HEAD/snapshot, and capability matrix. A saved pack is stale when recomputing those inputs produces a different digest; read-only commands do not rewrite it.

Budget values are estimates and use the name `estimated_tokens`. Early stop is deterministic and reports one of:

```text
required_evidence_satisfied
budget_reached
no_more_eligible_evidence
required_evidence_exceeds_budget
```

`final_render_estimated_tokens` must not exceed `maximum_estimated_tokens` unless the required evidence alone exceeds the budget, in which case the stop reason is `required_evidence_exceeds_budget`.

## Benchmark Labels

Benchmark fixtures may label source refs as `must_find`, `acceptable`, `supporting`, or `noise`. `must_find` drives recall and first-correct rank; precision treats `must_find + acceptable` as relevant; `supporting` is reported separately and does not count against precision; visible `noise` is contamination. Verification hints use explicit `expected_verification_hints` labels rather than keyword inference.

The first release records first-correct rank, labeled precision/recall, supporting hits, generated/ignored noise, verification-hint accuracy, and output size as baseline measurements. Existing source-integrity, explicit-source recall, forbidden-source, and cross-repo contamination gates remain. New precision/recall thresholds are not release gates until real benchmark history justifies them.
