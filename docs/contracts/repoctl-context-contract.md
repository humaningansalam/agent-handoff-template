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

The bundle includes:

```text
query.mode
source_snapshots
completeness
candidates
packed_context
knowledge_results
groups
budget
bundle_digest
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

The pack is non-authoritative and must be read before editing repo files for repo-scoped tasks when available. It contains:

```text
must_read
likely_change
impact
verification
reviewed_knowledge
warnings
```

Legacy JSON groups such as `maybe_relevant` and `verification_hints` remain for compatibility. The agent-facing groups are aliases over the same source references plus direct task Graph evidence.

The pack may use task title, Goal, Discovery, Handoff, chosen files, and explicit Context Docs as retrieval seeds only. It must not convert task prose into source authority or automatic edit scope.
