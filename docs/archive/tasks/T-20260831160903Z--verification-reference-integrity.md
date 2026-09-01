---
id: T-20260831160903Z
title: "Preserve verification reference integrity at completion"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260831T160903Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
handoff_origin_commitment: "sha256:aa3ca902e54d8430349da9c862c9470f459277375da8a6c03b56e8580fcf3f15"
---

# T-20260831160903Z - Preserve verification reference integrity at completion

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260831160903Z--verification-reference-integrity.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: `tools/repoctl/discovery_outcomes.py`, `tools/repoctl/tasks.py`, and their task lifecycle/finish tests.

## Discovery

- Candidate query: `verification subject IDs completion projection silent filtering finish validation`
- Candidate files reviewed: `tools/repoctl/discovery_outcomes.py`, `tools/repoctl/tasks.py`, `tests/repoctl/task/test_task_lifecycle.py`, `tests/repoctl/task/test_task_finish.py`, `docs/contracts/repoctl-discovery-outcome-contract.md`
- Chosen files: `tools/repoctl/discovery_outcomes.py`, `tools/repoctl/tasks.py`, `tests/repoctl/task/test_task_lifecycle.py`, `tests/repoctl/task/test_task_finish.py`, `docs/contracts/repoctl-discovery-outcome-contract.md`

## Goal

Guarantee that every structured verification record freezes non-empty, exact-version subject or claim references in a validator-clean completion outcome. A verification record must never lose its coverage because a subject leaves `active_chosen`, a later episode changes roles, or the same file is rebound to a newer content digest.

## Scope

- Task Key: `RCTL-090-P0-VERIFICATION-REFERENCE-INTEGRITY`
- Priority: P0 correctness and audit integrity. This task has no implementation dependency and may run in parallel with `T-20260831160911Z`.
- Domain Expert Key: `DE-REPOCTL-EVIDENCE-INTEGRITY`
- Allowed implementation files:
  - `tools/repoctl/discovery_outcomes.py`
  - `tools/repoctl/tasks.py`
  - `tests/repoctl/task/test_task_lifecycle.py`
  - `tests/repoctl/task/test_task_finish.py`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
- Root cause verified in the current `v0.9.0` checkout:
  - Structured verification records retain only stable `subject_ids`/`claim_ids`.
  - `_state_subjects()` reconstructs the subject table from current Chosen, episode roles, and citations; it has no task-owned subject authority dedicated to verification records.
  - A verification-bound subject can therefore outlive the role/version object that originally made it selectable.
  - `completion_outcome_projection()` silently filters an unresolved subject ID instead of failing closed.
  - `finish_task()` publishes the projection without calling `validate_completion_outcome()` before archive/Board/receipt/catalogue writes.
- Required implementation boundary:
  - Preserve the exact canonical subject capsule covered by each verification record in bounded task-owned state, or an equivalently small representation that can reproduce the exact subject table later.
  - Do not synthesize a Reviewed role merely to keep a verification subject alive.
  - Do not resolve an old subject ID by reading the current file and pretending the old check covered the new version.
  - Projection must map every recorded subject and claim or raise a typed error before any finish mutation.
- Non-goals:
  - Rewriting, deleting, or sanitizing already completed receipts.
  - Weakening `validate_completion_outcome()` or accepting empty structured verification coverage.
  - Treating a prior version's passed check as current-version coverage.
  - Building a generalized event-sourcing, correction-lineage, or global subject-store framework.
  - Changing Context/Graph ranking, Knowledge lifecycle, completion catalogue policy, or product files under `repos/**`.

## Acceptance Criteria

1. Add a regression that records a Chosen-only file subject, verifies it, later removes it from `active_chosen`, and finishes. The completion receipt must retain the exact verified subject in its local subject table and the verification record must contain a non-empty local `subject_ids` list.
2. Add a content-version regression: verify file version V1, mutate the file, verify V2, then finish. The receipt must contain both exact version digests; the V1 record must point only to V1 and the V2 record only to V2.
3. `structured_verification_coverage()` must continue to count only the exact current file version. A passed V1 record must not satisfy V2 coverage before a V2 passed record exists.
4. A claim-only record remains valid only when every claim ID resolves to a frozen citation claim. A record whose subject and claim references cannot be resolved must fail with one stable typed problem; it must never be projected as `subject_ids: []` and `claim_ids: []`.
5. `completion_outcome_projection()` must either return a value accepted by `validate_completion_outcome()` or raise before returning. Remove the current unresolved-ID filtering behavior.
6. `finish_task()` must validate the completion outcome before preparing or committing task archive, Board, completion receipt, catalogue event/head, or sidecar writes. A failing regression must prove byte-for-byte no mutation across those paths.
7. `task doctor` must surface a live legacy state with orphan verification references before finish, using the same typed integrity reason rather than reporting zero coverage.
8. Existing immutable invalid receipts remain invalid and inspectable through `check --audit-history`; this change must not make them pass and must not edit adopter workspaces.
9. Update the Discovery outcome contract only for the new exact-reference/state invariant and any supported state-schema migration. Do not broaden authority or claim retroactive evidence.
10. Targeted tests and the full repository suite must pass; the current baseline is 826 tests.

## Required Runtime and History Evidence

- Reproduce the failure from a synthetic task using the exact role-removal and V1/V2 sequences above before applying the fix.
- Re-run a read-only audit over the 12 adopter workspaces. The current historical baseline is six completion receipts with thirteen verification records whose projected `subject_ids` and `claim_ids` are both empty: areum 1/3, gakza 2/3, greate-library 1/2, kinport 2/5. Those immutable records must remain reported, while all newly generated regression receipts contain zero empty records.
- Include one field-derived identity example in test notes, such as greate-library's verification of `packages/mcp-server/src/server.ts` after that exact subject no longer appeared in the task's final role sets.
- Record the failing and passing targeted commands in this task's Verification section.
- Required final commands:
  - `.venv/bin/python -m pytest tests/repoctl/task/test_task_lifecycle.py tests/repoctl/task/test_task_finish.py -q`
  - `.venv/bin/python -m pytest -q`
  - `./scripts/repoctl check --json`

## Reuse and YAGNI Boundary

- Reuse canonical subject construction, `version_digest`, `record_id`, `completion_outcome_projection()`, `validate_completion_outcome()`, and the existing finish transaction/rollback boundary.
- Prefer one bounded verification-subject pool or an equivalently direct extension over a new service, database, event log, resolver plugin, or cross-task identity layer.
- Keep completion outcome schema semantics local to the task receipt. No normal query or ranking path may depend on the new preservation detail.

## Execution Log

- 20260831T160903Z: task created via repoctl task create.
- 20260831T160903Z: Main Director scoped the task from current code and 12-workspace history evidence.
- 20260831T161731Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260831T171305Z: Reproduced Chosen-removal, V1/V2, and legacy-orphan failures; implemented schema-v2 exact verification subject closure, read-only v1 migration, typed reference failure, validator-clean projection, and the pre-write finish gate.
- 20260831T171313Z: Targeted lifecycle/finish tests passed 131/131, full suite passed 831/831, repoctl check passed, and the read-only 12-workspace audit preserved the historical 6-receipt/13-empty-record baseline without adopter writes.
- 20260831T180942Z: Resolved the final Pro review blockers; targeted suites passed 144/144, full suite passed 844/844, repoctl check passed, and agbrowse ChatGPT Pro session 01M1CFJSYH430YKS0HB2494RSC returned Decision: APPROVE.
- 20260831T181050Z: task finished and verified.

## Verification

- RED regressions: `.venv/bin/python -m pytest -q tests/repoctl/task/test_task_lifecycle.py::test_completion_outcome_retains_verified_chosen_subject_after_scope_replacement tests/repoctl/task/test_task_lifecycle.py::test_completion_outcome_preserves_each_exact_verified_file_version tests/repoctl/task/test_task_finish.py::test_task_finish_rejects_legacy_orphan_verification_without_mutation` initially reported `3 failed`; the role-removal projection dropped its only subject, the legacy orphan remained healthy, and the V1/V2 fixture exposed a Discovery-setup precondition that was corrected before production changes.
- Focused regressions after the fix: the same three node IDs => `3 passed in 1.46s`.
- Final Pro follow-up regressions covering alias ambiguity, malformed persisted types, exact v2 pool closure, v1 digest precedence, mixed or forged references, duplicate adds, and finish call order: `13 passed in 3.60s`.
- Targeted lifecycle/finish suites: `.venv/bin/python -m pytest tests/repoctl/task/test_task_lifecycle.py tests/repoctl/task/test_task_finish.py -q` => `144 passed in 25.87s`.
- Full suite: `.venv/bin/python -m pytest -q` => `844 passed in 368.06s` (the prior baseline was 826).
- Workspace validation: `./scripts/repoctl check --json` => `ok: true`, zero problems and warnings; `git diff --check` => clean.
- Read-only 12-workspace receipt scan retained the historical empty-reference distribution exactly: areum `1 receipt / 3 records`, gakza `2 / 3`, greate-library `1 / 2`, kinport `2 / 5`, all other listed workspaces `0 / 0`; total `6 receipts / 13 records`. No adopter files were written.
- `check --audit-history --json` remained non-passing on the existing adopter history. `invalid_completion_receipt` remains explicit for areum, greate-library, and kinport; gakza remains unavailable behind its pre-existing catalogue source mismatch while the direct immutable-receipt scan still finds `2 / 3`.
- Field identity example: greate-library task `T-20260823070742Z` added `packages/mcp-server/src/server.ts` to Chosen at `20260823T161746Z`, verified the task, then removed that exact path at `20260823T163946Z`; its immutable receipt now exposes two empty verification records because the final subject table no longer retains that verified capsule.
- agbrowse checkpoint reviews: ChatGPT Pro sessions `01M1CAJS3VJ7STYG180SZRDYWK` and `01M1CCWW23FQYGP8XDN8SPE0H0` returned `Decision: REVISE`; their eligibility/closure, migration, alias, malformed-state, record-order, regression, and first-effect-barrier findings were incorporated.
- agbrowse final exact-diff review: the live composer DOM label was verified as `Pro`; session `01M1CFJSYH430YKS0HB2494RSC` in conversation `6a95ac68-4200-83e9-a4af-2ef945735ac8` returned `Decision: APPROVE` with no remaining correctness or integrity blocker.

## Last Active Handoff

- Next exact step: Run the finish-readiness doctor, then finish this task if it remains healthy.
- First file to open: `docs/tasks/T-20260831160903Z--verification-reference-integrity.md`
- First command to run: `./scripts/repoctl task doctor T-20260831160903Z --json`
- Done when: doctor reports finish-ready, the task is archived with an immutable completion receipt, and the other two live tasks remain untouched.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260831160903Z--verification-reference-integrity.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260831160903Z.json`
- Git delivery: Not managed by repoctl.
