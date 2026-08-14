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
- an initial Context result may be wrong or incomplete, so later work needs an explicit, freshness-checked outcome loop instead of treating the first ranking as truth
- large repositories need query-visible component membership and crossings without turning directory names into ownership or a second graph

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

### Repository-Understanding Loop

- Bind a discovery query, its citable evidence, explicit review decisions, and verification into one completion-bound outcome.
- Join only independently retrieved, current subjects to fresh applicable outcomes while keeping the first result a hypothesis, not authority or edit scope.
- Project declared component membership and crossings onto existing typed subjects and relations so an agent can traverse between relevant areas without a second graph.
- Keep the loop bounded as it is reused: hot state, ordinary query work, incremental refresh work, and agent-facing payload must converge instead of growing with task history.

## Success Criteria

- A new agent can resume a live task from `AGENTS.md`, `docs/BOARD.md`, the task file, and Handoff without guessing.
- A live Handoff is active only after an explicit review binding to the exact structured Task and observed repository state; later input drift is visible as typed inactive resume guidance instead of silently blessing old prose.
- Repo-scoped work uses an explicit product repository boundary under `repos/` and does not mutate unrelated repositories by path accident.
- Task finish leaves auditable verification evidence and stable completion receipts.
- Graph and Context answers preserve repository identity, source refs, digests, freshness, typed continuations, and completeness warnings.
- Compatible but unresolved relationships remain agent-visible as explicitly non-authoritative candidates without being promoted to Graph edges.
- For ambiguous work, compact Context improves owner/test/impact hit rate and first-correct rank while reducing irrelevant visible slots, broad rediscovery, serialized bytes, and token cost.
- Current project documents, task history, and explicitly reviewed reusable records remain distinct evidence lanes; generated views never become source authority.
- Reviewed Knowledge records require an explicit approval action and remain separate from generated llmwiki output; the actor may be a human or an authorized agent.
- The outcome loop admits only explicit, source-bound task outcomes as feedback and never turns them into ownership, authority, edit scope, or a hard filter.
- Repeating equivalent verified work does not increase hot rows, ordinary query reads, unchanged-delta refresh work, or later payload; cold audit growth stays off the ordinary retrieval path.

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
- Build Context / Task Pack outputs as source-linked evidence bundles for questions and task startup, with changed-path overlays scored in the same retrieval corpus as materialized evidence and typed continuations for iterative exploration.
- Let an optional Task Pack participate in resume guidance only when the user or agent explicitly binds that exact current artifact. JSON and Markdown artifacts must share one canonical input projection, reject missing/tampered/wrong-task/legacy artifacts, and become inactive when their producer-owned source identities, structured Task inputs, Graph snapshot, or repository observation changes.
- Resolve bounded lexical file hypotheses into the existing typed Graph projection so ambiguous natural-language work can include owners, direct tests, callers, callees, imports, and structured dependencies without a hidden rebuild or a second traversal engine.
- Preserve exact typed identities, source refs, digests, repository namespaces, field-level evidence, and typed continuations. Lexical relevance may propose a working-set member but never prove semantic ownership.
- Publish one coherent bounded projection for compact output, completeness, citations, seed refs, human views, and Task Pack; renderer differences must not change evidence identity.
- Keep confirmed relations and compatible unresolved relationship candidates as separate typed lanes; candidates must preserve their structured resolution reason and never imply task scope.
- Assign each shared document one closed semantic role and preserve that role through collection, indexing, changed-path overlays, retrieval, compact projection, and Task Pack construction instead of independently re-inferring its meaning at each consumer.
- Keep active authority and procedures eligible for ordinary retrieval, keep templates from consuming ordinary recall unless explicitly addressed, and never ingest generated views as source evidence.
- Treat current project documents, task history, and Reviewed Knowledge as separate complementary evidence lanes rather than one interchangeable corpus.
- Do not turn Context output into task scope, source authority, or reviewed knowledge automatically.

The completion-bound outcome and bounded-retention semantics are owned by `docs/contracts/repoctl-discovery-outcome-contract.md`. The additive component-crossing projection is owned by `docs/contracts/repoctl-graph-contract.md`.

### Reviewed Knowledge And llmwiki

- Store reviewed knowledge in `docs/knowledge/records/**` with lifecycle events in `docs/knowledge/events/**`.
- Require an explicit review action before durable knowledge exists; do not infer approval from source prose or task completion alone.
- Preserve receipt-derived source identity when a completed child task moves from the live task directory to the archive. A byte-identical, uniquely resolved archive move remains current provenance; missing, ambiguous, identity-mismatched, or digest-mismatched evidence fails closed.
- Treat generated llmwiki pages as regenerable, non-authoritative views.
- Do not ingest generated llmwiki output as future source evidence.

## Non-Goals

- This template is not an autonomous agent runtime.
- MCP transport is not included in this template release.
- Chat/session memory is not project authority.
- Generated llmwiki pages are not source authority.
- Context is the default integrated discovery surface for ambiguous work, not a ban on exact search, typed Graph traversal, or direct source inspection.
- Repository Understanding does not require moving the established documentation tree, mandatory per-document frontmatter, embeddings, or a second content-management system.
- Repository Understanding is not online self-training: query output and task prose do not auto-approve Knowledge, rewrite Graph facts, or become future authority.

## Acceptance And Rejection

Accept the repository-understanding loop only when repeated field scenarios demonstrate all of the following:

- later related work improves owner/test/impact hit rate and reduces broad rediscovery, serialized bytes, tokens, and elapsed exploration time
- the combined capture, storage, indexing, query, and reading cost across a related task pair is lower than the no-reuse baseline
- exact provenance survives while outcomes remain non-authoritative and current source, direct reads, exact search, and typed traversal stay independently available
- a typed continuation can cross a derived component boundary without a manually maintained second graph
- equivalent repetition leaves hot state, ordinary query work, incremental refresh work, and agent-facing payload stable rather than proportional to completed-task count

Reject or revisit the model when those field outcomes do not improve. Investigate retrieval, tokenization, and continuation presentation before adding more schema or fixture-specific ranking rules.

## Adoption Rule

After copying the template, either delete this file or replace it with the adopter workspace's private PRD/context. If the context grows, keep `docs/PRD.md` as a short index and split details under root `docs/prd/`.
