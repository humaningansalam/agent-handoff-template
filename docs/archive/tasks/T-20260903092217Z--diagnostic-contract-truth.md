---
id: T-20260903092217Z
title: "Diagnostic and contract truth"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "unassigned"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260903T092217Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
---

# T-20260903092217Z - Diagnostic and contract truth

## Context Docs

- `docs/PRD.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`
- `docs/contracts/repoctl-module-boundaries.md`

## Work Area

- Task record: `docs/tasks/T-20260903092217Z--diagnostic-contract-truth.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.
- Backlog origin: `BL-834819060faf`

## Discovery

- Candidate query: `Which current Context completeness, freshness-warning, dirty-cancel, history-consumption, and CLI contract paths fail the B0101-03 acceptance criteria?`
- Candidate files reviewed:
  - `tools/repoctl/context.py`
  - `tools/repoctl/cli.py`
  - `docs/PRD.md`
  - `docs/contracts/repoctl-context-contract.md`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/task/test_task_cancel_block.py`
  - `tests/repoctl/test_cli_discoverability.py`
  - `docs/contracts/repoctl-module-boundaries.md`
  - `tests/repoctl/task/test_task_finish.py`
- Chosen files:
  - `tools/repoctl/context.py`
  - `tools/repoctl/cli.py`
  - `docs/PRD.md`
  - `docs/contracts/repoctl-context-contract.md`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/task/test_task_cancel_block.py`
  - `tests/repoctl/test_cli_discoverability.py`
  - `docs/contracts/repoctl-module-boundaries.md`
  - `tests/repoctl/task/test_task_finish.py`
- Notes:
  - `These are the existing implementation, authority, and behavior-test surfaces named by the promoted backlog; no new diagnostic layer or command is needed.`
  - `Terra High found one stale authority statement and the existing baseline-conflict cancel journey that can carry the path assertion without adding a test case.`

## Goal

Make Context completeness and freshness diagnostics reflect usable current evidence, make dirty-cancel errors point to the live task, and align active authority on explicit-only completion history.

## Scope

- Report compact Context as partial when readable current source remains available without Graph.
- Do not relabel missing metadata enrichment as uncertain Graph freshness; preserve genuine freshness warnings.
- Point both dirty-cancel rejection branches at the existing slugged task path while retaining complete affected paths in structured action inputs.
- Remove active PRD and contract claims that ordinary Context automatically joins completion outcomes.
- Exercise all 85 registered command help paths through the public repoctl script without adding per-command tests.

## Execution Log

- 20260903T092217Z: task created via repoctl task create.
- 20260903T092344Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260903T094106Z: Corrected Context completeness and metadata-freshness diagnostics, normalized dirty-cancel task paths, removed automatic-outcome-reuse contract residue, and verified all 85 registered help paths plus full release gates.
- 20260903T094117Z: task finished and verified.

## Verification

- `uv run pytest -q tests/repoctl/context/test_context_query.py tests/repoctl/task/test_task_cancel_block.py tests/repoctl/task/test_task_finish.py tests/repoctl/test_cli_discoverability.py` — passed, `277 passed in 84.54s`.
- `./scripts/repoctl field-gate run repoctl-release --full --json` — passed 7/7 gates; retrieval benchmark recall@5 `0.971429`, precision@5 `0.79381`, visible recall `0.980952`, graph-edge recall `1.0`, and zero selected noise.
- `uv run pytest -q` — passed, `762 passed in 516.71s`.
- `uv run ruff check tools/repoctl tests/repoctl` — passed.
- `uv run python -m compileall -q tools/repoctl` — passed.
- `./scripts/repoctl check --audit-history --json` — passed; all 16 completion events and source authorities audited.
- `git diff --check` — passed.

## Last Active Handoff

- Next exact step: Review closure readiness, then finish and archive this verified workspace task through repoctl.
- First file to open: `docs/tasks/T-20260903092217Z--diagnostic-contract-truth.md`
- First command to run: `./scripts/repoctl task doctor T-20260903092217Z --json`
- Done when: repoctl archives the task with a completion receipt and removes its Board entry.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260903092217Z--diagnostic-contract-truth.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260903092217Z.json`
- Git delivery: Not managed by repoctl.
