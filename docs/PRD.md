# Agent Workspace Control Plane PRD

> Template adoption note: This file is a disposable seed. After copying this template, delete this file or replace it with the adopter workspace's private PRD/context. Do not keep this template PRD as live project truth. If the context grows, keep `docs/PRD.md` as a thin index and split details under `docs/prd/`.

## Problem

Coding agents can edit code, but they do not reliably preserve project state across tools, sessions, and handoffs. Without a shared control plane, teams fall back to chat history, scattered notes, or tool-specific state. That creates predictable failures:

- the next agent cannot tell what task is live, blocked, done, or abandoned
- product repository boundaries are inferred from paths or prose instead of explicit repo identity
- task completion claims are not tied to verification evidence
- metadata, task state, and generated context drift apart
- useful decisions and failure modes are rediscovered instead of reused
- generated summaries can be mistaken for source authority
- a new agent repeatedly scans a large repository to rediscover product authority, implementation owners, direct tests, and prior decisions that the workspace already contains
- semantically different documents compete as generic evidence, allowing indexes, templates, or broad references to displace the applicable authority or procedure and cause avoidable exploration and rework

This template solves workspace coordination and repository-understanding startup around agents. For ambiguous work, Context is the natural integrated discovery surface: it provides a compact, source-linked working set over current code, typed relationships, project authority, procedures, task history, and explicitly reviewed reusable knowledge. It narrows where to read and change before the agent spends tokens rescanning a large repository. Exact search, free iterative Graph traversal, and direct source inspection remain available for confirmation and deeper exploration.

## Target Users

- A developer or team using one or more coding agents against a private workspace.
- A maintainer who needs deterministic task state, handoff continuity, and upgradeable workspace tooling.
- An agent that needs machine-readable boundaries, evidence, and verification gates before changing product code.

## Product Goal

Make a copied workspace immediately usable as a private agent workbench where humans and agents can:

- know the current work state without reading chat history
- select the correct product repository explicitly
- mutate task/control state only through repoctl gates
- capture verification evidence before declaring work done
- start ambiguous repository work from a compact, high-signal Context bundle that combines current source, provider-confirmed Graph relations, current project documents, task history, and applicable Reviewed Knowledge without merging their authority
- follow typed continuations toward likely owner, change, dependency, and verification surfaces without repeatedly rescanning the repository
- keep exploring caller, callee, import, test, task, document, and Knowledge relationships through typed Graph continuations without a mandatory Context -> Graph -> `rg` sequence
- use narrow exact search and direct reads to confirm a known identity instead of repeating broad discovery that the working set already answered
- promote only explicitly reviewed, source-linked knowledge into durable records
- render llmwiki pages as disposable views, not authority

## Success Criteria

- A new agent can resume a live task from `AGENTS.md`, `docs/BOARD.md`, the task file, and Handoff without guessing.
- A live Handoff is active only after an explicit review binding to the exact structured Task and observed repository state; later input drift is visible as typed inactive resume guidance instead of silently blessing old prose.
- Repo-scoped work uses an explicit product repository boundary under `repos/` and does not mutate unrelated repositories by path accident.
- Task finish leaves auditable verification evidence and stable completion receipts.
- Graph and Context answers preserve source refs, repo IDs, digests, freshness, typed continuations, and completeness warnings.
- Compatible but unresolved relationships remain agent-visible as explicitly non-authoritative candidates without being promoted to Graph edges.
- A task may retain only the Context or Graph result references that actually informed Reviewed or Chosen files, without mandatory feature-use logging.
- For an ambiguous repository question or task startup, compact Context surfaces the applicable authority or procedure and the likely source, relation, and test surfaces ahead of generic indexes and templates.
- Context selects likely owner source, direct tests, and immediate impact paths into a bounded working set while keeping unrelated same-word files out of agent-visible slots when typed Graph evidence resolves the area.
- Generic natural-language words do not become exact symbol identities; explicit paths, quoted/code-shaped identities, and provider-confirmed symbols retain exact behavior and ambiguity fails closed.
- Compact Context exposes whether each Graph seed came from exact identity, provider symbol, Reviewed Knowledge, or a ranked lexical hypothesis so path resolution is never mistaken for proven semantic ownership.
- Repository-understanding quality is evaluated by owner/test/impact hit rate, first-correct rank, irrelevant visible slots, repeated broad-discovery behavior, and serialized output/token cost rather than Graph invocation count or `rg` prohibition.
- Document meaning remains consistent across materialized-index and live-fallback retrieval, compact grouping, and Task Pack projection; split `docs/prd/**` documents retain product-authority semantics.
- Current project documents, task history, and explicitly reviewed reusable records are reported as distinct project-knowledge lanes; zero Reviewed Knowledge records never means that project knowledge is empty.
- Reviewed Knowledge records require an explicit approval action and remain separate from generated llmwiki output; the actor may be a human or an authorized agent.

## Core Requirements

### Task And Control Ledger

- Keep live task registry in `docs/BOARD.md`.
- Keep executable task state in task frontmatter and required sections under `docs/tasks/**`.
- Archive completed standalone tasks under `docs/archive/tasks/**`.
- Require Handoff and Verification evidence so another agent can resume or audit the work.
- Keep the human-readable four-field Handoff as the collaboration surface while repoctl stores only a compact machine-owned binding receipt. Starting or showing a task must never create or refresh that receipt automatically.

### Product Repository Boundary

- Treat root as the private workspace/control-plane repository.
- Treat `repos/` or configured `repos/<repo-id>/` entries as product repository boundaries.
- Require explicit `repo_id` where multi-repo ambiguity exists.
- Keep root operations, task state, contracts, workflows, and repoctl tooling separate from product code changes.
- Keep versioned authority and procedures shared by humans and agents in workspace or product documentation, executable agent-only behavior in `.agents/skills/**`, and regenerable Graph/index state in `.repoctl-state/**`.

### repoctl Mutation Gates

- Use repoctl as the mutation boundary for Board, Backlog, task lifecycle, archive transitions, `.repometa` validation, and release upgrades.
- Return stable JSON envelopes for agent consumption.
- Fail visibly when required verification, metadata, repository identity, or integrity checks are missing.

### Evidence And Context

- Materialize Graph and its persistent evidence index as one deterministic boundary over source facts, metadata, documents, receipts, imports, symbols, calls, and artifacts; queries read it without hidden rebuilds or unchanged-source rescans.
- Build Context / Task Pack outputs as source-linked evidence bundles for questions and task startup, with changed-path overlays and typed continuations for iterative exploration.
- Let an optional Task Pack participate in resume guidance only when the user or agent explicitly binds that exact current artifact. JSON and Markdown artifacts must share one canonical input projection, reject missing/tampered/wrong-task/legacy artifacts, and become inactive when their producer-owned source identities, structured Task inputs, Graph snapshot, or repository observation changes.
- Resolve bounded lexical file hypotheses into the existing typed Graph projection so ambiguous natural-language work can include owners, direct tests, callers, callees, imports, and structured dependencies without a hidden rebuild or a second traversal engine.
- Preserve exact-identity fail-closed behavior. Keep every current source/test/config lexical hypothesis eligible, preserve canonical path/section/body query evidence, and allocate the bounded working set by saturating lexical breadth plus source/test/config lane, evidence-role, and repository-component coverage so vocabulary-heavy consumers cannot crowd out distinct owners. Use fresh typed relations among the current query candidates as later coherence corroboration, keep raw Graph degree later still, and use shared directory scope only as a final tie-break. When the whole cohort contains only weak single-term matches, select one top fallback while still reporting every omitted candidate; in a mixed cohort, a weak isolated singleton may remain diagnostic without consuming a slot unless it adds meaningful lane, role, or component evidence. Continue with fresh resolved anchors when another hypothesis is stale or absent, report anchor omissions independently from typed anchor resolution, and report final compact working-set omissions independently from anchor coverage.
- Preserve source refs, digests, repo namespaces, and completeness diagnostics.
- Keep confirmed relations and compatible unresolved relationship candidates as separate typed lanes; candidates must preserve their structured resolution reason and never imply task scope.
- Assign each shared document one closed semantic role and preserve that role through collection, indexing, changed-path overlays, retrieval, compact projection, and Task Pack construction instead of independently re-inferring its meaning at each consumer.
- Keep active authority and procedures eligible for ordinary retrieval, keep templates from consuming ordinary recall unless explicitly addressed, and never ingest generated views as source evidence.
- Treat current project documents, task history, and Reviewed Knowledge as separate complementary evidence lanes rather than one interchangeable corpus.
- Do not turn Context output into task scope, source authority, or reviewed knowledge automatically.

### Reviewed Knowledge And llmwiki

- Store reviewed knowledge in `docs/knowledge/records/**` with lifecycle events in `docs/knowledge/events/**`.
- Require an explicit review action before durable knowledge exists; do not infer approval from source prose or task completion alone.
- Treat generated llmwiki pages as regenerable, non-authoritative views.
- Do not ingest generated llmwiki output as future source evidence.

## Non-Goals

- This template is not an autonomous agent runtime.
- MCP transport is not included in this template release.
- Chat/session memory is not project authority.
- Generated llmwiki pages are not source authority.
- Context is the default integrated discovery surface for ambiguous work, not a ban on exact search, typed Graph traversal, or direct source inspection.
- Repository Understanding does not require moving the established documentation tree, mandatory per-document frontmatter, embeddings, or a second content-management system.

## Adoption Rule

After copying the template, either delete this file or replace it with the adopter workspace's private PRD/context. If the context grows, keep `docs/PRD.md` as a short index and split details under root `docs/prd/`.
