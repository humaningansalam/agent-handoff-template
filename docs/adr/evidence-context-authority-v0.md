# ADR: Evidence Context authority v0

## Status

Proposed

## Context

Graph v0 is a deterministic, read-only evidence snapshot derived from repo registry, source index facts, `.repometa`, completion receipts, and precise providers. It is not an authority over tasks, Board state, repository identity, or metadata.

The next product layer should help agents and humans find the right evidence quickly. A full durable knowledge base is useful later, but adding it first would introduce another lifecycle before retrieval quality and source fidelity are measured.

## Decision

Introduce an Evidence Context layer before Reviewed Knowledge.

Evidence Context returns query- or task-specific source bundles. It does not create durable facts, mutate source authorities, or approve knowledge. It may rank, compress, and explain why evidence was selected.

Authority remains separated:

```text
Evidence  = Graph snapshot, source document, completion receipt
Context   = temporary evidence bundle for a query or task
Knowledge = reviewed durable records created only after explicit approval
```

## Authority Rules

- Context is non-authoritative and read-only.
- Context consumes Graph objects directly; it must not parse Graph CLI stdout.
- Context may use ADRs, contracts, allowed workflow docs, completion receipts, referenced verification/task archives, repo registry data, and Graph snapshots.
- Context must not ingest generated wiki pages, previous Context output, candidate output, temporary caches, raw Backlog prose, or active task claims as factual authority.
- Context must preserve source refs, digests, repo ID namespace, and completeness diagnostics.
- Context output must not automatically update task scope, Board, Backlog, `.repometa`, Graph, or source files.
- Reviewed Knowledge records require explicit human approval and must not be created by Context query alone.

## Knowledge Boundary

Reviewed Knowledge may be added after Context quality is measured. Initial reviewed kinds are limited to:

```text
decision
invariant
failure_mode
```

Knowledge records are immutable after approval. Lifecycle changes are append-only events. Source digest drift produces stale status without rewriting the record body.

## Non-Goals

- no graph database migration
- no vector-only retrieval
- no automatic ontology
- no automatic knowledge approval
- no chat/session memory as project authority
- no generated wiki self-ingestion
- no knowledge-driven task scope mutation
- no external agent integration in this roadmap

## Consequences

- The first useful product is `repoctl context`, not a full knowledge base.
- Retrieval quality can be benchmarked before introducing durable knowledge records.
- Source authority remains inspectable and reversible.
- Later Knowledge and llmwiki layers can build on the same source refs and digests without replacing task, metadata, or Graph contracts.
