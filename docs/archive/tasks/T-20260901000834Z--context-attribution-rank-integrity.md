---
id: T-20260901000834Z
title: "Preserve canonical candidate rank in Context attribution"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T000834Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260831160918Z"
document_language: "en"
handoff_origin_commitment: "sha256:cf759daaf1210cc9c283398934135f3911679dd0b40d69e1259f0cdc984d9caf"
---

# T-20260901000834Z - Preserve canonical candidate rank in Context attribution

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-context-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260901000834Z--context-attribution-rank-integrity.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the opt-in Context benchmark attribution projection only.
- Follow-up of: `T-20260831160918Z`
- Pinned prerequisite commit: `7c56971`

## Discovery

- Candidate query: `_retrieval_evidence duplicate full evidence path canonical first rank typed contribution union`
- Candidate files reviewed: `tools/repoctl/context_benchmark.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/fixtures/context-benchmark/attribution-cases.json`, `tools/repoctl/context.py`, `tools/repoctl/result_receipts.py`, `docs/contracts/repoctl-context-contract.md`
- Chosen files: `tools/repoctl/context_benchmark.py`, `tests/repoctl/context/test_context_benchmark.py`, `docs/contracts/repoctl-context-contract.md`

## Goal

When one immutable Context result member appears in multiple full-evidence section chunks, preserve its first canonical rank and representative score while losslessly unioning typed Graph and Knowledge contribution flags in the opt-in attribution capsule.

## Scope

- Task Key: `RCTL-090-T2-P0-ATTRIBUTION-RANK-INTEGRITY`
- Priority: P0 evaluation-evidence correctness.
- Domain Expert Key: `DE-REPOCTL-CONTEXT-EVALUATION`.
- Allowed implementation files:
  - `tools/repoctl/context_benchmark.py`
  - `tests/repoctl/context/test_context_benchmark.py`
  - `docs/contracts/repoctl-context-contract.md`
- Read-only evidence files:
  - `tests/fixtures/context-benchmark/attribution-cases.json`
  - `tools/repoctl/context.py`
  - `tools/repoctl/result_receipts.py`
- Root cause: `_retrieval_evidence()` keys by exact `(authority, ref)` but overwrites the entry for every later evidence occurrence, replacing the best rank/score and losing contribution flags observed only on earlier chunks.
- Implementation boundary: keep the first occurrence's rank, lane, score, and score breakdown; logical-OR only the existing typed Graph/Knowledge booleans across later occurrences.
- Non-goals:
  - Normal Context ranking or compact selection changes.
  - Attribution stage, Context/result/completion receipt schema, or candidate identity changes.
  - New evaluator, command, telemetry store, global registry, causal harness, or scalar contribution score.
  - Any path around `independent_execution_receipt_missing`.
  - Fixture-specific production routing or removal of module/symbol chunks.

## Acceptance Criteria

1. Attribution continues to join retrieval metadata by the exact existing `(authority, ref)` result-member identity.
2. Duplicate occurrences retain the smallest one-based full-evidence rank and the exact score/breakdown/lane from that occurrence; scores are never summed.
3. Existing `typed_contributions.graph` and `.knowledge` are logical unions across all duplicate occurrences.
4. `repos/auth/flow.py` retains full ranks `[9, 20]` while attribution reports rank `9` and the rank-9 score; `repos/handlers/session_login.py` also proves first-occurrence preservation.
5. All six attribution stage projections remain unchanged, including stale-version false and Knowledge-only true reuse.
6. `claim_scope: correlation_only`, `non_gating: true`, and in-band protocol `independent_execution_receipt_missing` behavior remain unchanged.
7. Default benchmark artifacts, metrics, ranking, field-gate, Task, receipts, Graph, Knowledge, and completion catalogue do not consume or emit the opt-in metadata.
8. Two same-input attribution runs are canonicalized byte-stable; the run is zero-mutation except an explicit output.
9. Targeted tests and the full suite pass without reducing the current 878-test baseline; `repoctl check` and `git diff --check` pass.

## Required Runtime Evidence

- Record the before-state duplicate ranks and current incorrect rank for `repos/auth/flow.py`.
- Run `tests/repoctl/context/test_context_benchmark.py`.
- Run the real `context benchmark --attribution` CLI twice and compare canonical candidate/stage/retrieval bytes.
- Compare default benchmark result/metric fields before and after the opt-in change.
- Re-run the fully re-digested 4-vs-4 copy exploit and require `insufficient_evidence` with `independent_execution_receipt_missing`.
- Run the full pytest suite, `repoctl check`, and `git diff --check`.

## Execution Log

- 20260901T000834Z: task created via repoctl task create.
- 20260901T000936Z: Pro Director session `01M1D3GMZH1DFM72680H358AXH` inspected commit `7c56971`, reran 26 targeted and 878 full tests, and issued this task before compact relation closure.
- 20260901T000952Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T001222Z: Captured fail-first runtime evidence on pinned commit 7c56971: repos/auth/flow.py full ranks [9,20] with rank-9 score 7.216669, but attribution projected rank 20 and score 3.216668; repos/handlers/session_login.py full ranks [2,4] with rank-2 score 115.065039 and Graph contribution, but attribution projected rank 4, score 14.171776, and graph=false.
- 20260901T001453Z: Implemented the minimal first-occurrence merge in _retrieval_evidence: scalar metadata is set once and only existing Graph/Knowledge booleans are ORed across duplicates. Added Q-003 attribution, real Q-004 full-bundle, and typed-union regressions; the focused 3-test set passes.
- 20260901T002631Z: Validation complete: context benchmark file 26 passed in 124.98s; full suite 880 passed in 511.10s; disposable CLI default/attribution runs preserved normal fields and workspace manifest, produced stable canonical hash sha256:24e333d2e3d8a02d94cd75a9e056f0abd0c54c0ea90e11f3a20413f2c20ff5b4, and projected auth/flow.py at rank 9 score 7.216669; repoctl check and diff check pass.
- 20260901T004441Z: agbrowse Expert session 01M1D5V94S738GG45FMXA0K36Z completed after 14m19s with DECISION: APPROVE; reviewer independently reran focused regressions, 28 context/field-gate tests, the 880-test full suite, repoctl check, and git diff --check with no blockers.
- 20260901T004509Z: task finished and verified.

## Verification

- Fail-first evidence on clean pinned commit `7c56971`:
  - `repos/auth/flow.py` full ranks `[9, 20]`, rank-9 score `7.216669`, but attribution incorrectly reported rank `20`, score `3.216668`.
  - `repos/handlers/session_login.py` full ranks `[2, 4]`, rank-2 score `115.065039` with Graph contribution, but projection incorrectly reported rank `4`, score `14.171776`, `graph=false`.
- Focused first-occurrence, real Q-004 full-bundle, and Graph/Knowledge union regressions: `3 passed in 12.72s` after failing `2/2` before implementation.
- `.venv/bin/python -m pytest tests/repoctl/context/test_context_benchmark.py -q` => `26 passed in 124.98s`.
- Disposable real CLI replay:
  - default question count `34`; no default attribution field.
  - default and attribution normal fields are equal; recursive workspace manifest is unchanged.
  - `repos/auth/flow.py` attribution rank `9`, score `7.216669`.
  - two canonical candidate/stage/retrieval hashes equal `sha256:24e333d2e3d8a02d94cd75a9e056f0abd0c54c0ea90e11f3a20413f2c20ff5b4`.
  - `claim_scope: correlation_only`; `non_gating: true`.
- The existing fully re-digested 4-vs-4 copy regression remains in the passing benchmark file and still requires `independent_execution_receipt_missing`.
- `.venv/bin/python -m pytest -q` => `880 passed in 511.10s`.
- `./scripts/repoctl check --json` => `ok: true`, no problems or warnings.
- `git diff --check` => exit `0`.
- Domain expert review: `DE-REPOCTL-CONTEXT-EVALUATION` session `01M1D5V94S738GG45FMXA0K36Z` (`https://chatgpt.com/c/6a95d4d8-f220-83ee-a54b-7e673fe37b5c`) independently reran the focused regressions, `28` context/field-gate tests, the `880`-test full suite, `repoctl check`, and `git diff --check`; no blockers found, `DECISION: APPROVE`.

## Last Active Handoff

- Next exact step: Run task doctor, finish the approved task, and commit its completed workspace diff before starting the Director-issued compact typed-relation closure task.
- First file to open: `docs/tasks/T-20260901000834Z--context-attribution-rank-integrity.md`
- First command to run: `./scripts/repoctl task doctor T-20260901000834Z --json`
- Done when: repoctl archives the task after all recorded runtime and independent Expert approval evidence passes closure gates.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901000834Z--context-attribution-rank-integrity.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901000834Z.json`
- Git delivery: Not managed by repoctl.
