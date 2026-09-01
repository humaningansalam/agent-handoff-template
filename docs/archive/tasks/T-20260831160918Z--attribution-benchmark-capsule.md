---
id: T-20260831160918Z
title: "Add non-authoritative outcome attribution to existing benchmarks"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260831T160918Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: [T-20260831160903Z, T-20260831160911Z]
document_language: "en"
handoff_origin_commitment: "sha256:47ccb6b62bb1ae9ef0b9c1117d3c9a9c7ac4e0abf236d5c40348dbc56b6f77d9"
---

# T-20260831160918Z - Add non-authoritative outcome attribution to existing benchmarks

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260831160918Z--attribution-benchmark-capsule.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the existing Context benchmark artifact/compare path and its field-gate tests.

## Discovery

- Candidate query: `available retrieved compact visible selected reviewed chosen verified later reused attribution benchmark`
- Candidate files reviewed: `tools/repoctl/context_benchmark.py`, `tools/repoctl/cli.py`, `tools/repoctl/result_receipts.py`, `tools/repoctl/discovery_outcomes.py`, `tools/repoctl/graph_store.py`, `tools/repoctl/graph_typescript_provider.py`, `tools/repoctl/knowledge_candidates.py`, `tools/repoctl/knowledge_projection.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tests/fixtures/context-benchmark`, `docs/contracts/repoctl-context-contract.md`
- Chosen files: `tools/repoctl/context_benchmark.py`, `tools/repoctl/cli.py`, `tools/repoctl/graph_store.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tests/fixtures/context-benchmark/attribution-cases.json`, `docs/contracts/repoctl-context-contract.md`

## Goal

Add one opt-in, non-authoritative attribution capsule to the existing Context benchmark/compare artifacts so field evaluation can distinguish candidate availability, retrieval, compact visibility, explicit task selection/review/choice, verification, and exact later reuse without changing normal ranking or lifecycle evidence.

## Scope

- Task Key: `RCTL-090-P1-CONTEXT-OUTCOME-ATTRIBUTION`
- Priority: P1 measurement integrity. Do not start until `T-20260831160903Z` and `T-20260831160911Z` are done.
- Domain Expert Key: `DE-REPOCTL-CONTEXT-EVALUATION`
- Allowed implementation files:
  - `tools/repoctl/context_benchmark.py`
  - `tools/repoctl/cli.py`
  - `tools/repoctl/graph_store.py`
  - `tests/repoctl/context/test_context_benchmark.py`
  - `tests/repoctl/context/test_field_gate.py`
  - `tests/fixtures/context-benchmark/attribution-cases.json`
  - `docs/contracts/repoctl-context-contract.md`
- Existing surfaces that must be reused:
  - `run_context_benchmark()` already builds the full `ContextBundle` with `explain=True`.
  - `compact_context_bundle()` already exposes the compact-visible set.
  - Context result receipt manifest/projection helpers already define selectable and representative citations.
  - Discovery completion outcomes already own selected citations, Reviewed/Excluded, active Chosen, and verification references.
  - `compare_context_benchmarks()` and `field-gate` already own benchmark comparison and release evidence.
- Hypothesis to validate:
  - The missing field signal is a stable candidate-level join across existing artifacts, not absence of Graph/Knowledge use and not a need for another evaluator.
  - Rank/lane/score plus typed Graph/Knowledge contribution at retrieval time, joined to explicit task outcome and later exact reuse, is sufficient to diagnose where useful evidence is lost.
- Non-goals:
  - A new top-level evaluation command, service, database, telemetry pipeline, feature-use logger, or agent orchestration system.
  - Any change to normal Context ranking, Task Pack, result-receipt schema, Discovery mutation semantics, completion receipts, Graph, or Knowledge approval.
  - Recording clicks, file opens, hidden model reasoning, or inferred human intent.
  - Claiming causality from one trace or automating external agent/model replay.
  - Implementing semantic owner/direct-test, release/version intent, identity dedup, lifecycle, decomposition, or compression ranking changes in this task.

## Acceptance Criteria

1. Extend the existing `context benchmark` command with one explicit opt-in attribution mode; do not introduce a sibling framework or top-level command. Default benchmark, field-gate, normal `context query`, Task, and receipt behavior must remain unchanged.
2. The attribution artifact must use stable candidate/member/subject identities and report these stages independently: `available` (selectable manifest membership), `retrieved` (full bundle evidence), `compact_visible`, `selected`, `reviewed`, `chosen`, `verified`, and `later_reused`.
3. A stage that cannot be proven from an exact artifact must be `unknown`, not `false`. In particular, old field receipts without a captured compact projection cannot be retroactively labeled compact-visible or omitted.
4. For every retrieved candidate, persist its full-evidence rank, lane/group, composite score, existing score breakdown, and observed typed Graph/Knowledge contribution. Do not invent a scalar contribution when the current explain data only supports a typed boolean or component field.
5. `selected` must require an exact producer result/member citation. `reviewed`, `chosen`, and `verified` must come only from the task-owned completion outcome. Path-text coincidence is insufficient.
6. `later_reused` must require a later timestamp plus an exact current subject/version outcome or approved Knowledge record cited by the later result. A later query merely returning the same path without outcome/Knowledge provenance is not reuse.
7. Add an attribution fixture within the existing context benchmark fixture family containing at least: one candidate that reaches every stage, one retrieved but compact-hidden candidate, one compact-visible but unselected candidate, one selected/reviewed but unverified candidate, and one stale-version candidate that must not count as later reuse.
8. Every attribution artifact must state `claim_scope: correlation_only` by default and must be non-gating in field-gate. Correctness/safety/completeness metrics remain ahead of token, cost, or time metrics.
9. Existing benchmark compare may report a causal-eligibility status only when both arms carry identical pinned commit, agent, model, prompt digest, and permission digest; each run declares a fresh workspace; and each arm has at least four repetitions. Any missing/mismatched field or n<4 must return `insufficient_evidence`, never a causal claim.
10. The attribution mode must be zero-mutation except for an explicitly requested benchmark output artifact. It must not write result receipts, task state, completion history, Knowledge state, Graph state outside the benchmark's existing temporary state, or ranking caches.
11. No attribution field may be consumed by normal ranking, Task/receipt validation, or Knowledge selection. Tests must assert this isolation.
12. Targeted tests and the full repository suite must pass; the current baseline is 826 tests.

## Required Runtime and History Evidence

- Recompute and record the current 12-project observational baseline separately from the earlier audit snapshot: 310 Context receipts, all 310 with Graph candidates, 14,013 Graph selectable references, nine direct Graph query receipts (areum 8, maetda 1), and webturn Knowledge exposure in 13 Context receipts with 39 selectable Knowledge references plus three current approved heads.
- Do not overwrite the earlier 261/12,697 snapshot; label both with their collection time/commit so growth is not misreported as contradiction.
- Run the attribution fixture twice at the same pinned commit and prove byte-stable candidate identities/stages after canonicalization.
- Run benchmark compare protocol tests for: valid 4-vs-4 metadata, 3-vs-4 insufficient sample size, commit mismatch, prompt mismatch, permission mismatch, and non-fresh workspace.
- Show that a single field trace reports correlation only, even when every stage is present.
- Required final commands:
  - `.venv/bin/python -m pytest tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q`
  - `./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark --repo-id main --json`
  - `.venv/bin/python -m pytest -q`
  - `./scripts/repoctl check --json`

## Reuse and YAGNI Boundary

- Reuse full/compact bundle construction, result manifest/projection helpers, completion outcome validation, benchmark compare, and field-gate artifact envelopes.
- One additive attribution case file and one optional artifact section are the complexity budget. Do not add a daemon, durable global trace store, alternate scorer, second benchmark runner, or new authority.
- Keep the attribution data diagnostic. Any later ranking change requires a separate task, existing benchmark gates, and controlled on/off replay evidence.

## Execution Log

- 20260831T160918Z: task created via repoctl task create.
- 20260831T160918Z: Main Director scoped the task after verifying existing benchmark, receipt projection, outcome, Graph, and Knowledge surfaces.
- 20260831T185815Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260831T202723Z: Implemented exact artifact attribution using real Context bundles/receipts, validated completion outcomes, approved Knowledge projection, exact subject versions, and distinct protocol runs.
- 20260831T202723Z: Validated 15 targeted context/field-gate tests, 868 full-suite tests, repoctl check, diff check, and two byte-stable disposable-adopter CLI runs.
- 20260831T202724Z: Re-audited the 12 adopter workspaces read-only: the 2026-08-24 cutoff remains 261/12697; raw and indexed all-time totals are 314/314 Context-with-Graph and 14213 Graph refs, exposing the prior Director 310/14013 arithmetic error.
- 20260831T213849Z: Resolved the latest expert findings: partial reuse channels now preserve unknown, compact capture is trace-scoped, Knowledge provenance/timestamps/IDs fail closed before temporary writes, and causal protocol validates eight distinct external benchmark artifacts; 19 targeted and 871 full-suite tests pass, with stable disposable-adopter hash 2850d5b7650b045e9498ccd93f0aa0c202009bb509ba8f124c7715177a3269d3.
- 20260831T221311Z: agbrowse expert follow-up 01M1CW78CPCBR1YGG10H1S63X4 returned CHANGES_REQUIRED after independent 19/871 reruns, identifying copied execution evidence, incomplete Knowledge semantic validation, cold TypeScript cache mutation, and timezone-naive timestamp crashes; implemented command-generated execution/workspace witnesses, canonical Knowledge contract reuse, temporary provider tool state, and timezone-required parsing, with 26 targeted tests now passing.
- 20260831T222621Z: Validated the second expert repair set: 26 targeted and 878 full-suite tests pass; repoctl check and diff check pass; a cold disposable adopter remained byte-identical across default/attribution runs except requested output, created no Graph tool cache, and produced two stable six-candidate canonical hashes 36b139d569914e656a28db1036ce950295bee75beadd23ffa965785a4ed7d6fa with distinct execution IDs.
- 20260831T234353Z: agbrowse expert recovery session 01M1D35CB1P93BP8XA1E9APYC6 returned APPROVE after independently rerunning the strengthened fully re-digested copy exploit; local targeted 26/26, full 878/878, repoctl check, and diff check pass.
- 20260831T234451Z: task finished and verified.

## Verification

- Exact artifact projection: attribution candidates now come from the benchmark's real full `ContextBundle`, canonical Context result receipt and member capsules, compact receipt projection, validator-clean completion outcome, and validated approved Knowledge projection. Case IDs are labels only.
- Stage semantics: the fixture covers full-chain, compact-hidden, visible-unselected, selected-unverified, stale-version rejection, missing compact capture (`unknown`), and approved-Knowledge-only reuse. Tampered Knowledge approval binding, event digest, and record digest each fail closed.
- Isolation: default benchmark and the complete field-gate envelope contain no recursive `attribution` key; all normal benchmark fields are identical with attribution on/off; a recursive workspace manifest is unchanged across opt-in runs.
- Protocol: comparison validates four distinct, digest-bound in-band run records per arm but never promotes caller-rewritable evidence to causal eligibility. Ordinary 4-vs-4 and the strengthened one-output/eight-new-identities/eight-re-digested-copies exploit both remain `insufficient_evidence` with `independent_execution_receipt_missing`; other protocol defects remain separately diagnosed. Every result is `correlation_only` and non-gating.
- `.venv/bin/python -m pytest tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q` => `26 passed in 128.84s`.
- `.venv/bin/python -m pytest -q` => `878 passed in 528.93s`.
- Cold disposable adopter runtime: default `context benchmark` => exit 0, 34 questions, recall@5 `0.941176`, and no attribution field. The first default and first attribution run left the recursive workspace file manifest byte-identical except for the explicitly requested attribution output; no Graph/provider tool cache appeared under adopter state. Two real `context benchmark --attribution` runs => exit 0 with no problems, six candidates, distinct command execution IDs, the same workspace witness, `claim_scope: correlation_only`, and `non_gating: true`; canonical candidate/stage/retrieval bytes were identical with SHA-256 `36b139d569914e656a28db1036ce950295bee75beadd23ffa965785a4ed7d6fa`.
- `./scripts/repoctl check --json` => `ok: true`, no problems or warnings. `git diff --check` => exit 0.
- Historical field snapshot A: collected `2026-08-31T14:50:56Z` from the `v0.9.0` checkout later verified by Director as `7889a16`, filtering receipt mtimes from `2026-08-24`: 261 Context receipts, 261/261 with Graph candidates, 12,697 Graph selectable refs, nine direct Graph receipts (areum 8, maetda 1), 48 Knowledge refs across 16 Context receipts, and 735 Task-history refs.
- Director snapshot B: reported `2026-08-31T16:16:25Z` from clean `v0.9.0` HEAD `7889a16` as 310 Context receipts and 14,013 Graph refs. Re-reading the retained per-project output shows this was an arithmetic error: its rows sum to 314 and the stored selectable data sum to 14,213.
- Current read-only field recomputation: collected `2026-08-31T20:22:55Z` with instrumentation checkout `ed3987eac98db017509ce695c49361069b2091ec`; both raw receipt files and receipt-index entries reproduce 314 Context receipts, 314/314 with Graph candidates, 14,213 Graph selectable refs, and nine direct Graph receipts. Webturn independently reproduces 13 Context receipts with 39 Knowledge refs and three approved current heads. The 12 original projects were not mutated.
- Expert conversation `01M1CMEMKQZFVBQ7N1YRWRFCR9`, continued through sessions `01M1CR70FCZMX4HDFA355CRTHB`, `01M1CW78CPCBR1YGG10H1S63X4`, and `01M1CYXWNF9S90YYDX7SCDVZGD`, produced the repaired partial-channel, trace-scoped compact capture, Knowledge provenance/semantic/timestamp/ID containment, copied-execution, distinct-workspace, cold provider-cache, and fully re-digested-copy findings. Recovery session `01M1D35CB1P93BP8XA1E9APYC6` independently reran the strengthened exploit (`1 passed in 26.52s`), found no remaining blockers, and returned `DECISION: APPROVE`.

## Last Active Handoff

- Next exact step: Run task doctor, bind this reviewed Handoff, and finish the task now that local verification and the agbrowse domain-expert review pass.
- First file to open: `docs/tasks/T-20260831160918Z--attribution-benchmark-capsule.md`
- First command to run: `./scripts/repoctl task doctor T-20260831160918Z --json`
- Done when: the reviewed Handoff is bound, task doctor passes, and the task finishes without weakening normal Context, Task, receipt, Graph, Knowledge, or field-gate behavior.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260831160918Z--attribution-benchmark-capsule.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260831160918Z.json`
- Git delivery: Not managed by repoctl.
