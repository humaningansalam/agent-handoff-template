---
id: T-20260831160911Z
title: "Audit normal tail-pending completion catalogues correctly"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260831T160911Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
handoff_origin_commitment: "sha256:8cf0233235c0aeb513ad9c87102f9900c07c8cdcfff6e38024b1678e341511e3"
---

# T-20260831160911Z - Audit normal tail-pending completion catalogues correctly

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260831160911Z--tail-pending-audit.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: completion catalogue full-audit validation and `check --audit-history` regressions.

## Discovery

- Candidate query: `tail_pending checkpoint cold prefix pending sidecars head audit prefix_mismatch`
- Candidate files reviewed: `tools/repoctl/completion_catalogue.py`, `tools/repoctl/cli.py`, `tests/repoctl/test_completion_catalogue.py`, `tests/repoctl/workspace/test_check.py`, `docs/contracts/repoctl-discovery-outcome-contract.md`
- Chosen files: `tools/repoctl/completion_catalogue.py`, `tests/repoctl/test_completion_catalogue.py`, `tests/repoctl/workspace/test_check.py`, `docs/contracts/repoctl-discovery-outcome-contract.md`

## Goal

Make explicit full-history audit accept a valid normal `tail_pending` catalogue while preserving all corruption, gap, source-parity, and no-mutation guarantees.

## Scope

- Task Key: `RCTL-090-P0-TAIL-PENDING-AUDIT`
- Priority: P0 correctness and safety. This task has no implementation dependency and may run in parallel with `T-20260831160903Z`.
- Domain Expert Key: `DE-REPOCTL-HISTORY-CATALOGUE`
- Allowed implementation files:
  - `tools/repoctl/completion_catalogue.py`
  - `tools/repoctl/cli.py` only if the existing audited summary needs an additive field
  - `tests/repoctl/test_completion_catalogue.py`
  - `tests/repoctl/workspace/test_check.py`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
- Root cause verified in the current `v0.9.0` checkout:
  - `completion_catalogue_status()` correctly recognizes `tail_pending` when the checkpoint/cold log ends at sequence N and head/sidecars end at N+M.
  - `audit_completion_catalogue()` scans only the cold JSONL and passes that prefix to `_validate_scan_against_state()`.
  - `_validate_scan_against_state()` then requires the head to equal the cold terminal event, so a valid pending sidecar chain is mislabeled `prefix_mismatch`.
  - Source parity is also currently based on the cold scan alone, so a correct fix must audit cold plus validated pending events rather than merely suppressing the head comparison.
- Required implementation boundary:
  - Validate the committed cold prefix against checkpoint and hot projection exactly as today.
  - Reuse `_pending_sidecar_events()` to validate the read-only chain from the committed terminal event to the head.
  - Use the combined cold-plus-pending event set for exact receipt/task source parity and audit totals.
  - Do not ingest, append, rebuild, refresh, or publish any catalogue state during audit.
- Non-goals:
  - Automatically consuming the pending tail.
  - Changing normal bounded `check`, Context, Graph, or incremental refresh behavior.
  - Suppressing a real head, sidecar, prefix, gap, duplicate-task, receipt, artifact, or source-set mismatch.
  - Replacing the existing catalogue/checkpoint/projection design or adding a second history store.

## Acceptance Criteria

1. A valid topology with cold/checkpoint/projection at N and a contiguous pending sidecar chain N+1..M ending at the current head must pass `audit_completion_catalogue()` and `check --audit-history`.
2. The audit result's event count, terminal sequence/event ID/prefix, task IDs, and source parity must cover the combined cold and pending chain, not only the committed checkpoint prefix.
3. The full audit must be demonstrably read-only. A recursive path/type/size/SHA-256 manifest of the catalogue JSONL, head, checkpoint, projection slots, and sidecars must be identical before and after both a successful and a failed audit.
4. Tampering with one pending sidecar payload, previous-event link, sequence, prefix digest, or head terminal identity must still return the existing narrow typed corruption/gap reason.
5. Tampering with the committed cold prefix or projection must still return `prefix_mismatch`; no valid-corruption regression may be converted to success.
6. Exact source parity must still fail when a validated receipt/task artifact is absent from the combined event chain or an event has no validated source authority.
7. Ordinary `check --json` status and cost remain unchanged: it must continue reporting `tail_pending` without scanning cold history.
8. `tools/repoctl/cli.py` output changes, if any, are additive and confined to the explicit audited summary. No existing problem code is renamed.
9. Update the history contract to distinguish committed prefix validation from pending-chain validation, without weakening the explicit-audit authority boundary.
10. Targeted tests and the full repository suite must pass; the current baseline is 826 tests.

## Required Runtime and History Evidence

- Before the fix, capture the current field reproduction on a disposable copy of `noise`: bounded check reports main checkpoint 77/head 78 as `tail_pending`, while `check --audit-history --json` reports `completion_catalogue_prefix_mismatch`.
- After the fix, the same disposable `noise` copy must exit successfully with no history problem and an unchanged state manifest.
- On a disposable `areum` copy, the false `completion_catalogue_prefix_mismatch` must disappear while the independent `invalid_completion_receipt` problem remains. This proves the fix does not mask receipt corruption.
- Include a synthetic multi-event pending chain, not only a one-event tail.
- Record the failing and passing targeted commands in this task's Verification section.
- Required final commands:
  - `.venv/bin/python -m pytest tests/repoctl/test_completion_catalogue.py tests/repoctl/workspace/test_check.py -q`
  - `.venv/bin/python -m pytest -q`
  - `./scripts/repoctl check --audit-history --json`

## Reuse and YAGNI Boundary

- Reuse `completion_catalogue_status()`, `_read_catalogue_tail()`, `_pending_sidecar_events()`, `_validate_catalogue_sources()`, and the existing typed `CompletionCatalogueUnavailableReason` values.
- Prefer splitting committed-prefix validation from head/pending validation or one narrowly parameterized helper. Do not add a new audit framework, shadow replay engine, repair mode, or alternate event format.
- Audit remains explicit O(N) recovery/verification work; ordinary consumers keep their bounded checkpoint/tail path.

## Execution Log

- 20260831T160911Z: task created via repoctl task create.
- 20260831T160911Z: Main Director scoped the task from current code and live `tail_pending` field reproductions.
- 20260831T181331Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260831T185201Z: Implemented committed-prefix versus pending-chain full audit using the existing sidecar validator; added combined parity, typed tamper, no-mutation, bounded-check, and field regressions.
- 20260831T185202Z: Validation complete: 47 targeted and 856 full tests passed; noise and areum disposable field copies behaved as required; DE-REPOCTL-HISTORY-CATALOGUE Pro review session 01M1CHVHBQTYKKXJNYABQ32X14 returned APPROVE.
- 20260831T185309Z: task finished and verified.

## Verification

- Regression-first reproduction: the new multi-event pending-tail audit test failed before the production change with `completion_catalogue_prefix_mismatch` because the committed cold terminal did not equal the pending head.
- `.venv/bin/python -m pytest tests/repoctl/test_completion_catalogue.py tests/repoctl/workspace/test_check.py -q` => `47 passed in 13.74s`.
- `.venv/bin/python -m pytest -q` => `856 passed in 366.87s`.
- `./scripts/repoctl check --audit-history --json` => success with no problems after the workspace's previously unmaterialized first completion sidecar was explicitly initialized through `history rebuild --workspace`; `git diff --check` also passed.
- Focused synthetic coverage proves a two-event pending chain contributes to audit count, terminal identity, task IDs, and source parity without mutation. Pending payload, previous-event, sequence, prefix, head, committed-prefix, projection, source-set, and cross-boundary duplicate-task tampering retain narrow `CORRUPT`, `GAP`, `PREFIX_MISMATCH`, `SOURCE_MISMATCH`, or `DUPLICATE_TASK` failures. Successful and failed audits preserve a recursive path/type/size/SHA-256 manifest.
- Ordinary `check --json` remained bounded: a guarded integration test rejected any cold JSONL, event sidecar, receipt directory, or archive read and still reported `tail_pending`; explicit `check --audit-history` then audited the same three-event combined chain successfully.
- Disposable `/mnt/data/workspace/human/noise` copy: before the fix, bounded check reported checkpoint 77/head 78 as `tail_pending` while full audit returned `completion_catalogue_prefix_mismatch`; after the fix it audited 78 events through sequence 78 with no problems. Catalogue manifest stayed `8b60d731a79847807c4b824ef6e4eb458181a06b0305bce29235f869e5875d91` before and after both audits.
- Disposable `/mnt/data/workspace/human/areum` copy: the false prefix mismatch disappeared while `invalid_completion_receipt` and `missing_discovery_evidence` remained. `completion_catalogue_source_mismatch` then correctly exposed the event whose receipt lacked validated source authority. Catalogue manifest stayed `84c9900fb964507cb784639b14e192a6fc4f263773836b5a750bedd2f2826654` before and after both audits.
- `DE-REPOCTL-HISTORY-CATALOGUE` review used `agbrowse` session `01M1CHVHBQTYKKXJNYABQ32X14`; the live UI identified the model as Pro, the reviewer independently inspected the diff and reran targeted/full/runtime probes, changed no files, reported no blockers, and returned `DECISION: APPROVE`.

## Last Active Handoff

- Next exact step: Review the final task doctor output, bind this exact Handoff, and finish the task.
- First file to open: `docs/tasks/T-20260831160911Z--tail-pending-audit.md`
- First command to run: `./scripts/repoctl task doctor T-20260831160911Z --json`
- Done when: the task archives successfully, its completion sidecar is auditable as a normal pending tail, and dependent task `T-20260831160918Z` is unblocked.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260831160911Z--tail-pending-audit.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260831160911Z.json`
- Git delivery: Not managed by repoctl.
