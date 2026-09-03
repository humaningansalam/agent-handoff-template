---
id: T-20260903084650Z
title: "Reduce completion history search vocabulary"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "unassigned"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260903T084650Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
---

# T-20260903084650Z - Reduce completion history search vocabulary

## Context Docs

- `docs/PRD.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260903084650Z--history-lexical-diet.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.
- Backlog origin: `BL-2c3cd9cb44f4`
- Domain Expert Key: `DEK-HISTORY-RETRIEVAL-HYGIENE`
- Primary surface: completion-catalogue event search vocabulary and explicit Context history retrieval.

## Discovery

- Candidate query: `Which completion-receipt and archived-task fields currently become catalogue search terms, and what is the smallest projection that preserves useful explicit history queries without indexing machine serialization noise?`
- Candidate files reviewed:
  - `tools/repoctl/completion_catalogue.py`
  - `tests/repoctl/test_completion_catalogue.py`
  - `tests/repoctl/context/test_context_query.py`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
  - `tests/repoctl/graph/test_graph_receipts.py`
- Chosen files:
  - `tools/repoctl/completion_catalogue.py`
  - `tests/repoctl/test_completion_catalogue.py`
  - `tests/repoctl/context/test_context_query.py`
  - `docs/contracts/repoctl-discovery-outcome-contract.md`
  - `tests/repoctl/graph/test_graph_receipts.py`
- Notes: `Updated the existing explicit-history authority fixture to place its semantic sentinel in the supported Verification section.`

## Goal

Reduce completion-catalogue search vocabulary to human-semantic task fields so schema keys, digests, timestamps, and numeric fragments do not inflate events or create false explicit-history matches.

## Scope

- Project search terms from task identity/title, changed paths, explicit Discovery text, and meaningful Verification/failure text; do not tokenize canonical receipt JSON or the complete archived task.
- Exclude machine schema/hash/timestamp/numeric noise unless it appears in the selected human-authored task text.
- Preserve exact task/artifact lookup and ordinary Context isolation.
- Keep the change inside the existing catalogue implementation; do not add a search backend, schema, abstraction layer, or automatic outcome reuse.
- Verify representative vocabulary is at most 128 terms without truncation and semantic history queries still work.

## Execution Log

- 20260903T084650Z: task created via repoctl task create.
- 20260903T084847Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260903T085959Z: scope changed: removed none; added tests/repoctl/context/test_context_query.py; reason=Context history fixture changed to keep semantic retrieval assertions inside the selected human-authored Verification projection.
- 20260903T092137Z: Reduced completion-history vocabulary to bounded semantic task fields, rebuilt workspace catalogue v6, updated affected history fixtures, and passed release/full/audit gates.
- 20260903T092155Z: task finished and verified.

## Verification

- `./scripts/repoctl field-gate run repoctl-release --full --json` — passed 7/7 gates; retrieval benchmark recall@5 `0.971429`, precision@5 `0.79381`, visible recall `0.980952`, graph-edge recall `1.0`, and zero selected noise.
- `uv run pytest -q` — passed, `762 passed in 570.67s`.
- `uv run ruff check tools/repoctl tests/repoctl` — passed.
- `uv run python -m compileall -q tools/repoctl` — passed.
- `./scripts/repoctl check --json` — passed; workspace completion catalogue current at sequence 15.
- `./scripts/repoctl check --audit-history --json` — passed; all 15 events and source authorities audited.
- `git diff --check` — passed.

## Last Active Handoff

- Next exact step: Review closure readiness, then finish and archive this completed workspace task through repoctl.
- First file to open: `docs/tasks/T-20260903084650Z--history-lexical-diet.md`
- First command to run: `./scripts/repoctl task doctor T-20260903084650Z --json`
- Done when: repoctl archives the task with a completion receipt and no live Board entry remains for it.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260903084650Z--history-lexical-diet.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260903084650Z.json`
- Git delivery: Not managed by repoctl.
