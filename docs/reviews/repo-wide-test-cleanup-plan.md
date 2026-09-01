# Repository-wide test cleanup plan

## Scope and baseline

This is the TC0 inventory for the entire tracked `tests/**` surface. It is not a sample, a changed-file review, or a cleanup patch.

- Tracked files: 70
- Python test/support files: 59
- Python lines: 29,892
- Fixture-data lines: 1,175
- Total tracked test-surface lines: 31,067
- Pytest collection: 882 cases
- Physical residue excluded from authority: 10 `__pycache__` directories and 70 `.pyc` files
- File dispositions: 25 `keep`, 23 `consolidate`, 14 `support-review`, 8 `delete-candidate`
- Exact duplicate normalized test bodies: 0
- Parameterization: 40 `@pytest.mark.parametrize` decorators across 32 test definitions
- Direct public CLI assertion call sites written as `assert main([...])`: 1,026
- `monkeypatch.setattr` calls: 419; 344 (82%) only inject `find_workspace_root`

The canonical row-by-row inventory is `docs/reviews/repo-wide-test-inventory.csv`. Every tracked test, fixture, helper, support module, and package marker has exactly one disposition.

## Domain totals

| Domain | Files | Lines | Collected cases | Primary wave |
|---|---:|---:|---:|---|
| Context and fixtures | 17 | 9,473 | 287 | TC1 |
| Task lifecycle and catalogue | 9 | 8,558 | 241 | TC2 |
| Graph/provider/component | 10 | 4,623 | 95 | TC3 |
| Knowledge | 6 | 2,427 | 48 | TC4 |
| Metadata | 5 | 808 | 34 | TC5 |
| Repository | 6 | 918 | 42 | TC5 |
| Workspace | 4 | 710 | 34 | TC5 |
| Maintenance | 6 | 2,185 | 66 | TC6 |
| Root/release | 7 | 1,365 | 35 | TC5/TC6/TC7 |

The two largest files, `test_context_query.py` and `test_task_lifecycle.py`, contain 10,180 lines (34% of Python test code) and collect 345 cases (39% of the suite). The ten largest Python files contain 18,369 lines (61%). Cleanup should start where concentration is highest, but size alone is not deletion proof.

Cleanup-wave row ownership reconciles to all 70 tracked files: TC1 16, TC2 8, TC3 9, TC4 5, TC5 14, TC6 8, and TC7 10. Empty package markers are owned only by TC7 and are excluded from the earlier domain-wave globs below.

## Ranked over-engineering findings

1. `shrink:` remove repeated workspace construction, CLI success-envelope checks, and semantically identical happy paths from the 5,965-line Context query and 4,215-line task lifecycle suites. Keep one public-path regression per distinct failure mode. [`tests/repoctl/context/test_context_query.py`, `tests/repoctl/task/test_task_lifecycle.py`]
2. `shrink:` audit 1,026 direct `assert main([...])` source call sites as workflows, not isolated assertions. Consolidate repeated create/start/show/finish and context-query invocations when their observable exit/result/filesystem behavior is identical. [`tests/repoctl/**`]
3. `shrink:` reuse existing support owners instead of local clones. `_materialize` exists in five files; `_sha256_text` in three; `commit_all` and `init_repo` in three; workspace/receipt writers also repeat. Four functions in `context_test_helpers.py` have zero tracked Python call sites and are function-level deletion candidates after collection and full-suite proof: `_write_context_benchmark_corpus`, `_approve_superseded_context_knowledge`, `_approve_deprecated_context_knowledge`, and `_write_pack_benchmark_task`. Do not create a new fixture framework. [`tests/repoctl/context_test_helpers.py`, `tests/repoctl/task_lifecycle_helpers.py`, `tests/repoctl/knowledge_test_helpers.py`]
4. `delete:` eight empty `__init__.py` package markers are deletion candidates. Replacement: native namespace-package collection, only after import and full-suite proof. [`tests/repoctl/**/__init__.py`]
5. `shrink:` the Context fixture corpus is 1,175 lines across 11 files. Preserve loaded case IDs and ground truth, but remove duplicated literal payloads and redundant expected-source declarations when one canonical case already proves the same observable retrieval behavior. The two `mutation-cases.json` files have no benchmark or test loader; their only non-inventory reference is `repoctl-upgrade-manifest.json`, so TC1 must not delete them under a test-only scope without a separately authorized manifest decision. Do not add a fixture DSL or generator. [`tests/fixtures/context-*`]
6. `shrink:` task finish, parent lifecycle, completion catalogue, and upgrade suites repeat Git/workspace setup and success-path envelopes. Preserve rollback, interruption, provenance, digest, legacy, and committed-range branches; merge only equivalent success paths. [`tests/repoctl/task/**`, `tests/repoctl/test_completion_catalogue.py`, `tests/repoctl/test_upgrade.py`]
7. `shrink:` Context pack/benchmark and Knowledge candidate/lifecycle/render suites repeat document/task/receipt construction. Reuse their existing helpers and retain distinct authority, drift, ambiguity, supersession, and artifact-integrity failures. [`tests/repoctl/context/**`, `tests/repoctl/knowledge/**`]
8. `shrink:` Graph build/call/RPC/receipt suites have repeated materialization and provider setup. Consolidate shared setup only; provider-specific semantic differences, stale pruning, exact selector identity, RPC fail-closed linking, and structured ambiguity remain separate. [`tests/repoctl/graph/**`]
9. `shrink:` maintenance scope guard and artifact writer tests are large but mostly protect security boundaries. Collapse command spelling/input permutations only when they hit the same parser branch and problem code; preserve path containment, approval binding, concurrency, retry, and zero-mutation faults. [`tests/maintenance/**`]
10. `yagni:` do not treat all 419 monkeypatches as mock bloat. 344 are root-locator injection required for isolated workspaces. Review only the remaining 75 internal/fault patches; keep atomic-write, unavailable-tool, interruption, and write-failure injection. [`tests/**`]
11. `shrink:` private production-surface use is split across 13 static references: seven module-private call sites in the new typed-relation tests, one direct `_retrieval_evidence` import in the attribution benchmark tests, and five direct Dart/provider serialization imports. Replace a private reference only when a public path proves the same observable failure without obscuring a provider-specific or fail-closed boundary. Private access alone is not deletion proof. [`tests/repoctl/context/test_context_query.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/graph/test_graph_dart_rpc.py`]
12. `native:` remove generated cache residue from developer worktrees through existing ignore/cleanup behavior, not tracked tests or a new cleanup dependency. [`tests/**/__pycache__`]

TC0 authorizes no test-count or line-count reduction forecast. Each wave reports only its measured, evidence-backed delta after replacement and protected-boundary proof.

## Disposition rules

- `keep`: currently compact or uniquely protects a boundary. It may still be simplified locally.
- `consolidate`: high-confidence repetition exists, but deletion requires a surviving observable regression map.
- `support-review`: fixture/helper/support data is not directly deletable; audit consumers and duplicate ownership first.
- `delete-candidate`: deletion appears unnecessary, but the wave must prove collection/import/behavior parity before removal.

No row is authorized for deletion solely because it is large, old, private, parameterized, or mock-heavy.

## Mandatory protected boundaries

Cleanup must retain an explicit surviving test for every applicable boundary:

- security and permissions
- repository and cross-repository isolation
- symlink/path escape and non-regular files
- data loss, atomicity, rollback, and interruption
- task lifecycle transition and start-scope immutability
- legacy compatibility and migration
- freshness, staleness, ambiguity, and exact identity
- digest, tamper, receipt, catalogue gap/prefix/source parity
- concurrency and retry safety
- zero-mutation failure
- bounded cold-history access
- public CLI JSON, filesystem mutation, and exit behavior

Implementation-detail order, helper call counts, and intermediate object shapes are not protected unless they are themselves a documented public contract.

## Cleanup waves

### TC1 — Context tests and fixtures

Scope:

```text
tests/repoctl/context/test_*.py
tests/repoctl/context_test_helpers.py
tests/fixtures/context-benchmark/**
tests/fixtures/context-benchmark-multirepo/**
tests/fixtures/context-pack-benchmark/**
```

Start with repeated workspace/query calls in `test_context_query.py`, then pack/benchmark setup and fixture literal duplication. Preserve public compact/full projection, citations, relation direction/distance/assertion, path-scoped provider fallback, stale overlays, isolation, budget, authority, and field-gate behavior.

### TC2 — Task lifecycle, receipts, and catalogue

Scope:

```text
tests/repoctl/task/test_*.py
tests/repoctl/task_lifecycle_helpers.py
tests/repoctl/test_completion_catalogue.py
tests/repoctl/test_result_receipts.py
```

Consolidate repeated task create/start/show/doctor/finish happy paths and workspace setup. Preserve atomic writes, rollback, generated Handoff provenance, immutable start scope, legacy migration/compatibility, Chosen/outcome alignment, verification version binding, receipt/catalogue integrity, cold-history bounds, and zero-mutation failures.

### TC3 — Graph/provider/component

Scope:

```text
tests/repoctl/graph/test_*.py
tests/repoctl/test_code_index.py
```

Consolidate repeated materialization/provider scaffolding and output-form duplication. Preserve provider-specific semantics, call/import identity, Dart RPC, structured ambiguity, snapshot digest/freshness, component crossing, exact selectors, and repository boundaries.

### TC4 — Knowledge

Scope:

```text
tests/repoctl/knowledge/test_*.py
tests/repoctl/knowledge_test_helpers.py
```

Consolidate repeated candidate/approve/query/render setup. Preserve explicit approval authority, candidate/record/event digest binding, source drift, supersession/deprecation, archive relocation, rollback, global identity, and generated-view non-authority.

### TC5 — Repository, metadata, workspace, and upgrade

Scope:

```text
tests/repoctl/meta/test_*.py
tests/repoctl/repository/test_*.py
tests/repoctl/workspace/test_*.py
tests/repoctl/test_upgrade.py
tests/repoctl/test_cli_discoverability.py
```

Consolidate repository/workspace setup and validator permutations that reach the same branch. Preserve repository identity and boundary, symlink escape, duplicate Git toplevel alias, metadata shard integrity, missing-file no-mutation, stale upgrade plans, compatibility, rollback, and multi-repo isolation.

### TC6 — Maintenance, permissions, and release

Scope:

```text
tests/maintenance/**
tests/test_permissions_registry.py
tests/test_release_repository.py
```

Consolidate equivalent scope-guard input spellings, approval setup, and artifact path permutations. Preserve containment, dirty-scope binding, approval hashes, JSONL concurrency, retry safety, permission registry, release reproducibility, and fail-closed prompt approval.

### TC7 — Shared support and closeout

Scope:

```text
tests/conftest.py
tests/repoctl/io_audit.py
tests/repoctl/**/__init__.py
docs/reviews/repo-wide-test-inventory.csv
docs/reviews/repo-wide-test-cleanup-plan.md
```

Remove only support code with zero live consumers, verify the eight empty marker candidates, reconcile every inventory row to a final disposition, and publish actual before/after lines and cases. Do not add a shared fixture framework.

## Per-wave evidence contract

Every cleanup wave must record:

| Required evidence | Meaning |
|---|---|
| Deleted test/fixture/helper | Exact removed subject |
| Surviving replacement | Existing test that remains |
| Observable regression | Failure the replacement catches |
| Protected boundary | Safety/lifecycle rule retained |
| Before/after cases and lines | Measured delta, not a target |

Required gates for each wave:

```bash
.venv/bin/python -m pytest <domain> -q
.venv/bin/python -m pytest -q
./scripts/repoctl field-gate run release-candidate --repo-id main --json
./scripts/repoctl check --json
git diff --check
```

TC7 additionally runs:

```bash
./scripts/repoctl check --audit-history --json
.venv/bin/python -m pytest --collect-only -q
```

No wave may add `skip`, `xfail`, a new dependency, a new pytest plugin, a new base class, or production changes to make cleanup pass.
