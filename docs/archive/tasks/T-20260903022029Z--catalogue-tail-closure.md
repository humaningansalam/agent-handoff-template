---
id: T-20260903022029Z
title: "Close completion catalogue tail and recovery"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "unassigned"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260903T022029Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
---

# T-20260903022029Z - Close completion catalogue tail and recovery

## Context Docs

- `docs/PRD.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`
- `docs/contracts/repoctl-graph-contract.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-json-contract.md`
- `docs/tasks/README.md`

## Work Area

- Task record: `docs/tasks/T-20260903022029Z--catalogue-tail-closure.md`
- Product repository: none selected
- Area hint: ops
- Domain Expert Key: `DEK-CATALOGUE-LINEAGE-RECOVERY`
- Primary surface: completion-catalogue ingress, pending-tail consumption, exact-history lookup, recovery action shaping, and public CLI journey tests.
- Baseline constraint: preserve every pre-existing uncommitted change. Do not reset, checkout, restore, stash, or rewrite unrelated work.

## Discovery

- Candidate query: Where does `task finish` publish completion-catalogue state, and which exact-history consumers ingest or validate a pending tail before lookup?
- Candidate files reviewed:
  - `tools/repoctl/completion_catalogue.py`
  - `tools/repoctl/graph_store.py`
  - `tools/repoctl/graph.py`
  - `tools/repoctl/context.py`
  - `tools/repoctl/cli.py`
  - `tests/repoctl/test_completion_catalogue.py`
  - `tests/repoctl/graph/test_graph_query.py`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/test_cli_discoverability.py`
- Chosen files:
  - `tools/repoctl/completion_catalogue.py`
  - `tools/repoctl/cli.py`
  - `tests/repoctl/test_completion_catalogue.py`
  - `tests/repoctl/graph/test_graph_query.py`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/test_cli_discoverability.py`

## Goal

Make the first and every later successful task completion immediately consumable by exact Graph and explicit Context history paths, while ensuring every emitted history-recovery command repairs the namespace that actually failed.

## Scope

### Required implementation

- Close the valid pending catalogue tail at the explicit cold-history read boundary instead of requiring an operator to run a full rebuild after normal finish.
- Reuse one bounded tail-ingestion path for exact task/artifact lookup and explicit `past-decision` / `failure-mode` search. Do not duplicate tail logic in Graph and Context callers.
- Keep ordinary Context and ordinary current-Graph queries independent of completion history.
- Preserve fail-closed behavior for corrupt sidecars, invalid ancestry, sequence gaps, digest mismatch, unsupported schema, and repository mismatch.
- Shape recovery actions from the actual failing catalogue namespace. A `main` failure must never silently fall back to `history rebuild --workspace`.
- Replace synthetic private-helper-only recovery assertions with public command journeys that execute the emitted command and rerun the original failed operation.

### Allowed files

- `tools/repoctl/completion_catalogue.py`
- `tools/repoctl/cli.py`
- `tests/repoctl/test_completion_catalogue.py`
- `tests/repoctl/graph/test_graph_query.py`
- `tests/repoctl/context/test_context_query.py`
- `tests/repoctl/test_cli_discoverability.py`
- `docs/contracts/repoctl-discovery-outcome-contract.md` only when implementation clarification is required
- `tools/repoctl/graph.py` or `tools/repoctl/context.py` only if the shared catalogue boundary cannot supply the public behavior without a caller change

### Acceptance Criteria

1. In a freshly extracted current release, the first normal `task finish` succeeds without a prior `history rebuild`.
2. Immediately after that first finish, all of the following succeed without an intervening rebuild:
   - `repoctl check --audit-history --json`
   - `repoctl graph query --repo-id main --task <finished-task-id> --json`
   - `repoctl graph query --repo-id main --artifact <archived-task-path> --json`
   - `repoctl context query <semantic-query> --mode past-decision --repo-id main --json`
3. Repeat the lifecycle for a second task. The same four consumers succeed with `head_sequence=2` and no `completion_catalogue_prefix_mismatch`.
4. Pending-tail consumption is idempotent: rerunning any consumer does not append duplicate JSONL events, advance sequence, or duplicate hot records.
5. Normal tail consumption follows only the committed prefix plus linked pending events. It does not enumerate or hash the full completion-receipt archive.
6. Ordinary auto Context does not open completion history, add historical candidates, alter current ranking, create Graph seeds, or infer Chosen scope.
7. Corrupt pending state remains typed-unavailable and is never overwritten by normal lookup.
8. An actual `check --audit-history --json` failure for repository `main` emits exactly `./scripts/repoctl history rebuild --repo-id main --json`, not the workspace namespace.
9. Executing that emitted command in a subprocess makes the original audit pass while completion receipt and archived task bytes remain unchanged.
10. Public JSON command identity and the six-key envelope remain stable for success, domain failure, and parse failure.
11. The tests no longer manufacture a `completion_history.catalogues[].problem_code` shape that public output does not contain.
12. The net implementation should remove or centralize duplicate state interpretation; do not add a second catalogue abstraction.

### Verification commands

```bash
uv run pytest -q \
  tests/repoctl/test_completion_catalogue.py \
  tests/repoctl/graph/test_graph_query.py \
  tests/repoctl/context/test_context_query.py \
  tests/repoctl/test_cli_discoverability.py

uv run pytest -q
uv run ruff check tools/repoctl tests/repoctl
uv run python -m compileall -q tools/repoctl
./scripts/repoctl check --json
./scripts/repoctl check --audit-history --json
./scripts/repoctl field-gate run repoctl-release --full --json
git diff --check
```

The Acceptance Criteria also require one fresh release-archive subprocess journey covering first finish, second finish, exact Graph task/artifact lookup, explicit Context history, emitted recovery execution, and post-recovery recheck. Calling `tools.repoctl.cli.main()` in-process is insufficient for that journey.

### Non-goals

- restoring automatic completion-outcome reuse or ranking boosts in ordinary Context
- scanning all cold completion receipts during finish or ordinary query
- adding a database server, daemon, vector store, new state schema, or compatibility alias
- weakening corruption, path-containment, immutable-receipt, or archive validation
- making a full Graph rebuild a prerequisite for exact historical lookup
- splitting `cli.py` wholesale or introducing service/repository/DI layers
- modifying product files under `repos/`
- committing, pushing, tagging, publishing, deploying, or declaring `PRODUCT_COMPLETE`

## Execution Log

- 20260903T022029Z: task created via repoctl task create.
- 20260903T023837Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260903T065057Z: Implemented one shared explicit cold-history boundary that admits valid pending tails; made first pending-only audit valid without mutation; preserved fail-closed behavior by validating projection changes before cold-log append; and localized audit recovery to the failing catalogue namespace.
- 20260903T065120Z: task finished and verified.

## Verification

- `uv run pytest -q tests/repoctl/test_completion_catalogue.py tests/repoctl/graph/test_graph_query.py tests/repoctl/context/test_context_query.py tests/repoctl/test_cli_discoverability.py` -> 280 passed.
- `uv run pytest -q` -> 762 passed.
- Fresh release-archive subprocess journey -> first and second finish, audit, exact Graph task/artifact, explicit Context history, emitted `--repo-id main` recovery, recovery execution, and immutable receipt/artifact checks passed.
- `uv run ruff check tools/repoctl tests/repoctl`; `uv run python -m compileall -q tools/repoctl`; `git diff --check` -> passed.
- `./scripts/repoctl check --json`; `./scripts/repoctl check --audit-history --json`; `./scripts/repoctl field-gate run repoctl-release --full --json` -> passed; all 7 release gates passed.

## Last Active Handoff

- Next exact step: Finish this task through repoctl, then request the assigned `DEK-CATALOGUE-LINEAGE-RECOVERY` expert review against the archived task and runtime evidence.
- First file to open: `tools/repoctl/completion_catalogue.py`
- First command to run: `./scripts/repoctl task doctor T-20260903022029Z --json`
- Done when: the task is archived and the assigned expert returns `APPROVE`, or any `CHANGES_REQUIRED` findings are implemented and revalidated in a follow-up task.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260903022029Z--catalogue-tail-closure.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260903022029Z.json`
- Git delivery: Not managed by repoctl.
