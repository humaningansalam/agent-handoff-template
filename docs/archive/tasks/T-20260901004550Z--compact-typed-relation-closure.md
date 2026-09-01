---
id: T-20260901004550Z
title: "Keep typed consumers dependencies and direct tests visible in compact Context"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T004550Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: ["T-20260901000834Z"]
follow_up_of: "T-20260901000834Z"
document_language: "en"
handoff_origin_commitment: "sha256:855200f9b6bc8f07048eb979f2fcc7184ba15f114ddaef635be8fb22539ced05"
---

# T-20260901004550Z - Keep typed consumers dependencies and direct tests visible in compact Context

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-context-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260901004550Z--compact-typed-relation-closure.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: compact Context source/test projection over current typed Graph facts.
- Follow-up of: `T-20260901000834Z`

## Discovery

- Candidate query: `compact evidence projection one-hop typed consumer dependency connected direct test disconnected lexical test`
- Candidate files reviewed: `tools/repoctl/context.py`, `tools/repoctl/cli.py`, `tests/repoctl/context/test_context_query.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tests/fixtures/context-benchmark/questions.jsonl`, `tests/fixtures/context-benchmark/expected-sources.json`, `docs/contracts/repoctl-context-contract.md`
- Chosen files: `tools/repoctl/context.py`, `tools/repoctl/cli.py`, `tests/repoctl/context/test_context_query.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tests/fixtures/context-benchmark/questions.jsonl`, `tests/fixtures/context-benchmark/expected-sources.json`, `docs/contracts/repoctl-context-contract.md`

## Goal

When Context has a resolved exact, explicit, or strong Graph anchor, keep the anchor plus at most one current one-hop typed consumer/dependency and one directly connected test visible inside the existing compact budget. Relations remain exploration evidence, never authority, ownership, edit scope, or Chosen scope.

## Scope

- Task Key: `RCTL-090-T2-P1-COMPACT-TYPED-RELATION-CLOSURE`.
- Priority: P1 Context correctness and completeness.
- Domain Expert Key: `DE-REPOCTL-CONTEXT-GRAPH-RETRIEVAL`.
- Pinned prerequisite commit: `7c5cb77`.
- Allowed implementation files:
  - `tools/repoctl/context.py`
  - `tools/repoctl/cli.py` only if a new release-candidate field-gate category threshold must be wired
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/context/test_context_benchmark.py`
  - `tests/repoctl/context/test_field_gate.py`
  - `tests/fixtures/context-benchmark/questions.jsonl`
  - `tests/fixtures/context-benchmark/expected-sources.json`
  - `docs/contracts/repoctl-context-contract.md`
- Do not modify `tests/fixtures/context-benchmark/corpus.json`, Graph providers/store/schema, result/completion receipts, outcomes, Knowledge, or catalogue code.
- Preserve `COMPACT_ITEM_LIMIT=8` and `COMPACT_CONTINUATION_LIMIT=8`; reserve no more than one relation source endpoint and one connected test.
- Endpoint eligibility requires a selected-repository current path, current content identity, non-stale resolved assertion, and an exact one-hop typed relation to the primary anchor.
- Handle incoming and outgoing `CALLS`/`IMPORTS_FILE` plus outgoing structured-file relations using existing mode, anchor identity, edge facts, and evidence roles.
- Prefer an exact/explicitly requested test, then a current test connected to selected sources by `TESTS_FILE`, resolved import, or resolved call. Use lexical fallback only when Graph capability is unavailable/partial; with complete Graph and no connected test, show no unrelated lexical test.
- Do not add an intent classifier, new ranker class, durable telemetry, all-neighbor closure, project-specific routing, Graph auto-rebuild, release/version boost, attribution feedback, or schema change.

## Director Amendment — 2026-09-01

The long field query:

```text
current isolated local Supabase owner operational controls waitlist pickup
dine-in push protocol gate customer denial rollback
```

is no longer a completion gate for this relation-closure task.

Runtime evidence isolates its failure before relation closure: the query selects
Dart menu-order anchors, so no current SQL migration/test anchor reaches the
closure stage. Task 2 must not fabricate an SQL endpoint, introduce query- or
project-specific ranking, add a classifier/ranker/schema, or increase a compact
budget to make that query pass.

Task 2 continues to require all of the following relation-closure cases:

- synthetic Q-004, Q-005, Q-020, and Q-025;
- Areum exact creature behavior bridge/direct-test;
- Gakza business-hours migration/direct-test;
- Gakza exact operational-controls path migration/direct-test;
- no disconnected lexical test;
- existing owner/test, structured dependency, workflow, shell, stale,
  unavailable, ambiguity, budget, authority, and isolation regressions.

The long query remains mandatory, not waived. It is transferred as the RED field
case of follow-up Task Key:

`RCTL-090-T3-P0-LONG-QUERY-ANCHOR-COHERENCE`

Task 2 may finish only after an independent Domain Expert directly inspects the
exact final diff, confirms that the remaining long-query failure is upstream of
closure, and approves the narrowed relation-closure contract.

## Acceptance Criteria

1. The resolved primary anchor remains visible and compact adds at most one current one-hop typed source endpoint that supplies an otherwise missing consumer/dependency role.
2. Endpoint tie-breaking is deterministic using existing candidate rank, typed-connection priority, and canonical path order.
3. Graph-only post-projection endpoints are eligible when current and exactly resolved; Q-025 must show `repos/compose.yml` and `repos/Dockerfile`.
4. Q-004 shows `repos/handlers/session_tokens.py` and `repos/handlers/session_login.py`; Q-005 shows `repos/frontend/src/api/tokens.ts` and `repos/frontend/src/client.ts`; Q-020 shows `repos/services/token_service.py` and `repos/handlers/cross_login.py`.
5. With complete current Graph, Q-004 does not show disconnected `test_render.py` or `backend/test_reconcile.py`.
6. Add fail-first benchmark categories `typed-consumer-closure`, `typed-structured-dependency-closure`, and `no-disconnected-test` without changing the corpus fixture.
7. Existing integrated owner/direct-test, multi-owner impact, workflow/script, and shell-source cases retain visible recall `1.0`; Graph-edge recall stays `1.0`; forbidden selection and cross-repo refs remain `0`.
8. Compact citations, evidence roles, Graph provenance, and continuations cover newly visible endpoints while the full selectable manifest remains a schema-compatible superset.
9. Missing, stale, partial, or ambiguous Graph never manufactures a current relation and preserves existing warnings/fallback boundaries.
10. Disposable Gakza replays show each exact migration with its direct SQL test; an Areum replay shows `lib/src/creature/creature_behavior_bridge.dart` with `test/creature_behavior_bridge_test.dart`, without mutating original workspace manifests.
11. Attribution remains correlation-only/non-gating and unused by normal ranking. Targeted tests, field gate, full suite, `repoctl check`, and `git diff --check` pass without reducing the current 880-test baseline.

## Required Runtime Evidence

- Capture fail-first compact-visible output for Q-004, Q-005, Q-020, and Q-025.
- Run the targeted context query/benchmark/field-gate tests and the benchmark category thresholds with source integrity, no-forbidden, and no-cross-repo gates.
- Run `field-gate run release-candidate`, the full suite, `repoctl check`, and `git diff --check`.
- Replay the two Gakza migration/test pairs and the Areum Dart source/test pair in disposable copies with temporary Graph/provider state and compare original workspace manifests before/after.

## Execution Log

- 20260901T004550Z: task created via repoctl task create.
- 20260901T004550Z: Pro Director session `01M1D3GMZH1DFM72680H358AXH` issued this task after Task 1, bounded compact relation closure to one source endpoint and one connected test, and deferred release/version ranking.
- 20260901T004647Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T005455Z: Captured fail-first disposable benchmark output: Q-004 showed only session_tokens.py plus unrelated test_render.py; Q-005 omitted client.ts; Q-020 omitted cross_login.py; Q-025 omitted Dockerfile. New typed-consumer and typed-structured categories each measured visible recall 0.5, and no-disconnected-test selected one forbidden lexical test.
- 20260901T005456Z: Implemented bounded relation closure by reusing existing coverage profiles and resolved Graph paths: one single-anchor source endpoint is reserved, multi-anchor selection is unchanged, and unresolved lexical tests are excluded only when a current resolved Graph anchor is usable. Updated benchmark labels, release field-gate thresholds, and the Context contract.
- 20260901T012637Z: Replayed the final candidate in disposable Areum and Gakza workspaces. Exact source queries selected the expected Areum bridge test and both expected Gakza migration/SQL-test pairs. The Director's long operational-controls natural-language query still selected Dart model anchors and no test; the target migration remained a lower-ranked full result, identifying a pre-closure anchor/ranking collision for Expert scope review.
- 20260901T012637Z: Fixed one targeted regression without adding a ranker or classifier by reusing existing path-name evidence to distinguish an explicitly related preselection test from a broad lexical test. Focused, targeted, release-candidate, full-suite, workspace, and whitespace gates then passed.
- 20260901T013614Z: Submitted `DE-REPOCTL-CONTEXT-GRAPH-RETRIEVAL` checkpoint through agbrowse session `01M1D98QN8HQ1DNWAXRP21EQ9X` (`https://chatgpt.com/c/6a962a1a-8cc0-83e8-8601-bf3e136623d7`). Expert returned `DECISION: REVISE`: the long Gakza query remains an explicit acceptance requirement that cannot be waived under the ranking non-goal, and the Expert's DevSpace connector failure prevented independent diff verification.
- 20260901T014409Z: Director checkpoint: acceptance amended on 2026-09-01. The required long Gakza query is an independently tracked pre-closure anchor/ranking RED case under RCTL-090-T3-P0-LONG-QUERY-ANCHOR-COHERENCE. Task 2 remains scoped to typed relation/direct-test closure and must not change ranking, add project-specific rules, schema, or budget.
- 20260901T024127Z: DevSpace-enabled DE-REPOCTL-CONTEXT-GRAPH-RETRIEVAL session 01M1DBCHK9PTDFKWF1SW2TH836 directly inspected the workspace and returned REVISE: preserve lexical fallback unless current import/call relation capability is complete; accept only resolved distance-1 TESTS_FILE/IMPORTS_FILE/CALLS test relations in test-to-source direction; require outgoing primary-to-endpoint structured source relations; and prevent a second Graph-only primary neighbor from occupying the optional source slot.
- 20260901T035900Z: Applied the DevSpace Expert REVISE findings with fail-closed path-scoped import/call provider coverage and exact/explicit/strong one-endpoint closure; focused and targeted regressions passed.
- 20260901T035900Z: DevSpace Expert directly edited missing/malformed provider coverage handling, ran focused checks, and returned DECISION: APPROVE after inspecting the current diff.
- 20260901T035901Z: Final validation passed: targeted 267, disposable Areum/Gakza exact replays, release-candidate 3/3, full suite 882, original workspace state preservation, repoctl check, and diff whitespace checks.
- 20260901T035954Z: task finished and verified.

## Verification

- Fail-first disposable benchmark: Q-004 omitted `repos/handlers/session_login.py` and selected disconnected `repos/tests/test_render.py`; Q-005 omitted `repos/frontend/src/client.ts`; Q-020 omitted `repos/handlers/cross_login.py`; Q-025 omitted `repos/Dockerfile`. New category visible recall was `0.5` for both typed closure categories and forbidden selection was `1` for the disconnected-test case.
- Focused regressions: `.venv/bin/python -m pytest tests/repoctl/context/test_context_query.py::test_context_query_prefers_connected_test_over_weak_lexical_test_candidate tests/repoctl/context/test_context_query.py::test_context_query_promotes_weak_test_seed_connected_to_retained_source tests/repoctl/context/test_context_query.py::test_context_query_auto_balances_product_source_tests_and_project_documents tests/repoctl/context/test_context_benchmark.py::test_context_benchmark_materializes_real_fixture_and_measures_retrieval_quality -q` -> `4 passed in 3.97s`.
- Targeted Context/benchmark/field-gate suite: `.venv/bin/python -m pytest tests/repoctl/context/test_context_query.py tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q` -> `265 passed in 181.21s`.
- Release-candidate field gate: `./scripts/repoctl field-gate run release-candidate --repo-id main --json` -> `ok: true`, `3/3` applicable workspace gates passed; isolated benchmark behavior reported by the command and benchmark category assertions passed in the targeted suite.
- Full suite: `.venv/bin/python -m pytest -q` -> `880 passed in 523.70s`.
- Workspace checks: `./scripts/repoctl check --json` -> no problems or warnings; `git diff --check` -> passed.
- Synthetic after-state: Q-004 shows `session_tokens.py` + `session_login.py` with no disconnected test; Q-005 shows `api/tokens.ts` + `client.ts`; Q-020 shows `token_service.py` + `cross_login.py`; Q-025 shows `compose.yml` + `Dockerfile`.
- Areum disposable replay: `lib/src/creature/creature_behavior_bridge.dart direct test` -> `repos/lib/src/creature/creature_behavior_bridge.dart`, `repos/lib/src/behavior/behavior_runtime.dart`, and `repos/test/creature_behavior_bridge_test.dart`.
- Gakza disposable replay: the business-hours query selected `20260717120000_restaurant_business_hours.sql` with `restaurant_business_hours.test.sql`; the exact operational-controls path query selected `20260715154000_restaurant_operational_controls.sql` with `restaurant_operational_controls.test.sql`.
- Field limitation requiring Expert decision: `current isolated local Supabase owner operational controls waitlist pickup dine-in push protocol gate customer denial rollback` still selects `menu_order_models.dart` and an artifact copy, with no test. Its Graph seeds are multiple Dart/tool paths; this is an anchor/ranking miss before the single-primary relation-closure rule, and no project-specific boost or new classifier was added.
- Original workspaces remained untouched by the disposable replays: Gakza HEAD `320c3f7699718928ece1b1ef79422ea499c06d2c` retained its pre-existing dirty paths; Areum HEAD `ae206a355b73407d010aa28a54314f255e469fec` remained clean.
- Domain Expert checkpoint: agbrowse session `01M1D98QN8HQ1DNWAXRP21EQ9X`, conversation `https://chatgpt.com/c/6a962a1a-8cc0-83e8-8601-bf3e136623d7`, `DECISION: REVISE`. Required resolution: either land a narrow fail-first anchor/ranking prerequisite for the long query or obtain an explicit Director amendment moving that query out of the current closure acceptance. Repeat Expert review after DevSpace access is restored.
- Director checkpoint: agbrowse session `01M1D9SNHRJ68KKFTMVGAKFVS0` in the existing Pro Director conversation selected option A. Task 2 remains `doing`; the Director Amendment above transfers the long query to `RCTL-090-T3-P0-LONG-QUERY-ANCHOR-COHERENCE` and requires a new independent Expert to inspect the exact final diff before Task 2 may finish.
- Final targeted Context/benchmark/field-gate suite after all Expert edits: `.venv/bin/python -m pytest tests/repoctl/context/test_context_query.py tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q` -> `267 passed in 195.05s`.
- Final release-candidate field gate: `./scripts/repoctl field-gate run release-candidate --repo-id main --json` -> `3/3` passed.
- Final full suite after the Expert's direct edits: `.venv/bin/python -m pytest -q` -> `882 passed in 574.52s`.
- Final disposable field replays retained the Areum bridge/direct-test and both Gakza migration/direct-test pairs. Original state remained unchanged: Areum HEAD `ae206a355b73407d010aa28a54314f255e469fec` and clean-status digest `e3b0c442...`; Gakza HEAD `320c3f7699718928ece1b1ef79422ea499c06d2c` and pre-existing status digest `ec7d423b...`.
- Final DevSpace Expert review: conversation `https://chatgpt.com/c/6a95d4d8-f220-83ee-a54b-7e673fe37b5c` directly edited `tools/repoctl/context.py` and `tests/repoctl/context/test_context_query.py` to fail closed on missing/malformed provider coverage, ran focused checks (`5 passed`), confirmed all four prior blockers closed, and returned `DECISION: APPROVE`.
- Final workspace gates: `./scripts/repoctl check --json` -> no problems or warnings; `git diff --check` -> passed; `task doctor` -> healthy and finish-ready.

## Last Active Handoff

- Next exact step: Run task doctor once more, then finish and archive this approved Task 2 before beginning TC0 repo-wide test inventory.
- First file to open: `docs/tasks/T-20260901004550Z--compact-typed-relation-closure.md`
- First command to run: `./scripts/repoctl task doctor T-20260901004550Z --json`
- Done when: repoctl finishes and archives Task 2 with no lifecycle blocker; the next task is TC0 whole-repository test inventory, not a partial test cleanup.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901004550Z--compact-typed-relation-closure.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901004550Z.json`
- Git delivery: Not managed by repoctl.
