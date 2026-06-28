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

This template solves the workspace coordination problem around agents. It does not replace the agents and does not replace the product repository.

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
- retrieve source-linked Graph/Context evidence for the active task
- promote only reviewed, source-linked knowledge into durable records
- render llmwiki pages as disposable views, not authority

## Success Criteria

- A new agent can resume a live task from `AGENTS.md`, `docs/BOARD.md`, the task file, and Handoff without guessing.
- Repo-scoped work uses an explicit product repository boundary under `repos/` and does not mutate unrelated repositories by path accident.
- Task finish leaves auditable verification evidence and stable completion receipts.
- Graph and Context answers preserve source refs, repo IDs, digests, and completeness warnings.
- Reviewed Knowledge records require approval and remain separate from generated llmwiki output.

## Core Requirements

### Task And Control Ledger

- Keep live task registry in `docs/BOARD.md`.
- Keep executable task state in task frontmatter and required sections under `docs/tasks/**`.
- Archive completed standalone tasks under `docs/archive/tasks/**`.
- Require Handoff and Verification evidence so another agent can resume or audit the work.

### Product Repository Boundary

- Treat root as the private workspace/control-plane repository.
- Treat `repos/` or configured `repos/<repo-id>/` entries as product repository boundaries.
- Require explicit `repo_id` where multi-repo ambiguity exists.
- Keep root operations, task state, contracts, workflows, and repoctl tooling separate from product code changes.

### repoctl Mutation Gates

- Use repoctl as the mutation boundary for Board, Backlog, task lifecycle, archive transitions, `.repometa` validation, and release upgrades.
- Return stable JSON envelopes for agent consumption.
- Fail visibly when required verification, metadata, repository identity, or integrity checks are missing.

### Evidence And Context

- Build Graph as a deterministic, read-only evidence snapshot over source facts, metadata, receipts, imports, symbols, calls, and artifacts.
- Build Context / Task Pack outputs as source-linked evidence bundles for questions and task startup.
- Preserve source refs, digests, repo namespaces, and completeness diagnostics.
- Do not turn Context output into task scope, source authority, or reviewed knowledge automatically.

### Reviewed Knowledge And llmwiki

- Store reviewed knowledge in `docs/knowledge/records/**` with lifecycle events in `docs/knowledge/events/**`.
- Require explicit review before durable knowledge exists.
- Treat generated llmwiki pages as regenerable, non-authoritative views.
- Do not ingest generated llmwiki output as future source evidence.

## Non-Goals

- This template is not an autonomous agent runtime.
- MCP transport is not included in this template release.
- Chat/session memory is not project authority.
- Generated llmwiki pages are not source authority.

## Adoption Rule

After copying the template, either delete this file or replace it with the adopter workspace's private PRD/context. If the context grows, keep `docs/PRD.md` as a short index and split details under root `docs/prd/`.
