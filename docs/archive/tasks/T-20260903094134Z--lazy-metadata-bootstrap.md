---
id: T-20260903094134Z
title: "Lazy metadata bootstrap"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "unassigned"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260903T094134Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
---

# T-20260903094134Z - Lazy metadata bootstrap

## Context Docs

<!-- Add only the minimum context docs needed for this task, or leave empty. -->

## Work Area

- Task record: `docs/tasks/T-20260903094134Z--lazy-metadata-bootstrap.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.
- Backlog origin: `BL-6880658b9204`

## Discovery

- Candidate query: `rg -n "init_store|ensure_store|temporary_repometa|meta init|shard skeleton" tools/repoctl tests/repoctl docs/workflows/repo-metadata.md`
- Candidate files reviewed:
  - `tools/repoctl/meta.py`
  - `tools/repoctl/cli.py`
  - `tools/repoctl/repositories.py`
  - `docs/workflows/repo-metadata.md`
  - `tests/repoctl/meta/test_meta_check.py`
  - `tests/repoctl/repository/test_repository_adoption.py`
  - `tests/repoctl/context/test_field_gate.py`
  - `tests/repoctl/context/test_context_query.py`
- Chosen files:
  - `tools/repoctl/meta.py`
  - `docs/workflows/repo-metadata.md`
  - `tests/repoctl/meta/test_meta_check.py`
  - `tests/repoctl/repository/test_repository_adoption.py`
  - `tests/repoctl/context/test_context_query.py`
- Notes:
  - `init_store eagerly materializes 16 empty shards; existing readers and writers already support policy-only bootstrap and lazy deterministic shard creation.`
  - `Preserve the symlink security scenario by materializing only the fixture shard it explicitly tests.`

## Goal

Initialize `.repometa` with `policy.json` only, creating a deterministic annotation shard only when the first annotation or exclusion is written, while preserving existing stores and public command behavior.

## Scope

- Remove eager empty annotation directory and shard creation from metadata initialization.
- Keep legacy populated and 16-shard stores readable without migration or deletion.
- Update the existing CLI journey, repository adoption, and symlink-safety tests; add no new test files.
- Align the metadata workflow with policy-only bootstrap and lazy shard creation.

## Execution Log

- 20260903T094134Z: task created via repoctl task create.
- 20260903T094424Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260903T095153Z: scope changed: removed tests/repoctl/context/test_field_gate.py; added tests/repoctl/context/test_context_query.py; reason=Terra High audit found the existing symlink-security fixture depends on eagerly-created empty shards; field-gate code needs no change.
- 20260903T100728Z: Implemented policy-only metadata bootstrap, lazy deterministic shard creation, workflow alignment, and existing-test updates; Terra High audit found and resolved one stale eager-shard fixture assumption.
- 20260903T100749Z: task finished and verified.

## Verification

- `uv run pytest -q tests/repoctl/context/test_context_query.py::test_cold_workspace_source_discovery_and_optional_enrichment_bootstrap tests/repoctl/meta tests/repoctl/repository/test_repository_adoption.py` -> passed (`44 passed`).
- `uv run ruff check tools/repoctl/meta.py tests/repoctl/meta/test_meta_check.py tests/repoctl/repository/test_repository_adoption.py tests/repoctl/context/test_context_query.py` -> passed.
- `uv run python -m compileall -q tools/repoctl` -> passed.
- `./scripts/repoctl field-gate run repoctl-release --full --json` -> passed (`7/7`); isolated bootstrap reported `temporary_repometa.created_count = 1`.
- `uv run pytest -q` -> passed (`762 passed`).

## Last Active Handoff

- Next exact step: Run task doctor, then finish and archive the task through repoctl.
- First file to open: `docs/tasks/T-20260903094134Z--lazy-metadata-bootstrap.md`
- First command to run: `./scripts/repoctl task doctor T-20260903094134Z --json`
- Done when: Doctor reports finish-ready and `task finish` archives the task with all five Chosen paths aligned.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260903094134Z--lazy-metadata-bootstrap.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260903094134Z.json`
- Git delivery: Not managed by repoctl.
