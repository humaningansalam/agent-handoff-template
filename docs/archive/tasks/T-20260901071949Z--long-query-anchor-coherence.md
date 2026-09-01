---
id: T-20260901071949Z
title: "Resolve coherent Context anchors for ambiguous long queries"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T071949Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901004550Z"
document_language: "en"
handoff_origin_commitment: "sha256:8eca2e238875c445d7793b24bc1164d445454d4dd18547b53bb2672292fad754"
---

# T-20260901071949Z - Resolve coherent Context anchors for ambiguous long queries

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-graph-contract.md`

## Work Area

- Task record: `docs/tasks/T-20260901071949Z--long-query-anchor-coherence.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: Context Graph-anchor selection and compact source/test coherence for ambiguous natural-language queries.
- Follow-up of: `T-20260901004550Z`

## Discovery

- Candidate query: `ambiguous long query coherent current source direct test narrow named symbol artifact copy multi-anchor selection`
- Candidate files reviewed:
  - ``tools/repoctl/context.py`, `tools/repoctl/context_retrieval.py`, `tools/repoctl/cli.py`, `tests/repoctl/context/test_context_query.py`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tests/fixtures/context-benchmark/corpus.json`, `tests/fixtures/context-benchmark/questions.jsonl`, `tests/fixtures/context-benchmark/expected-sources.json`, `docs/contracts/repoctl-context-contract.md`, `docs/contracts/repoctl-graph-contract.md`, and the read-only Gakza field workspace.`
  - `tests/fixtures/context-benchmark/corpus.json`
  - `tests/fixtures/context-benchmark/questions.jsonl`
  - `tests/fixtures/context-benchmark/expected-sources.json`
  - `tests/repoctl/context/test_context_benchmark.py`
  - `tools/repoctl/context.py`
  - `tools/repoctl/context_retrieval.py`
  - `tools/repoctl/cli.py`
  - `tests/repoctl/context/test_field_gate.py`
  - `docs/contracts/repoctl-context-contract.md`
- Chosen files:
  - `only the implementation files from `## Allowed Files` that the fail-first evidence proves necessary. Do not edit the Gakza or Areum adopter workspaces.`
  - `tests/fixtures/context-benchmark/corpus.json`
  - `tests/fixtures/context-benchmark/questions.jsonl`
  - `tests/fixtures/context-benchmark/expected-sources.json`
  - `tests/repoctl/context/test_context_benchmark.py`
  - `tools/repoctl/context.py`
  - `tools/repoctl/context_retrieval.py`
  - `tools/repoctl/cli.py`
  - `tests/repoctl/context/test_field_gate.py`
  - `docs/contracts/repoctl-context-contract.md`

## Goal

When an ambiguous query already retrieves a current source with broad field coverage and its resolved direct test, prevent narrow disconnected named-symbol/file echoes and byte-identical artifact copies from consuming the compact anchor working set. Preserve exact identity, legitimate independent multi-owner queries, bounded Graph traversal, and all authority boundaries.

## Scope

- Task Key: `RCTL-090-T3-P0-LONG-QUERY-ANCHOR-COHERENCE`.
- Priority: P0 repository-understanding correctness.
- Pinned implementation baseline: clean root commit `d0d37436409a09975ca02d6306c5676ec3d6c955`.
- Originating defect: the mandatory RED field case transferred from `T-20260901004550Z`.
- Domain Expert Key: `DE-REPOCTL-CONTEXT-ANCHOR-RANKING`.
- The worker must complete implementation and all local gates before requesting one final Expert review. Do not use an intermediate `agbrowse` call to choose the implementation.

## Root Cause / Verification Hypothesis

The current Gakza field replay proves that retrieval is not the missing layer:

- `supabase/migrations/20260715154000_restaurant_operational_controls.sql` is full-evidence rank `4`, score `58.586868`, and has a current resolved `USES_FILE` connection to its direct test.
- `supabase/tests/database/restaurant_operational_controls.test.sql` is full-evidence rank `6`, score `56.495226`.
- A fresh passed prior outcome also corroborates the migration, but history must remain only a weak non-authoritative tie-break.

Despite that evidence, Graph-anchor selection chooses:

1. `tool/local_supabase.dart::runLocalSupabase`,
2. `packages/gakza_contracts/lib/src/models/menu_order_models.dart::OrderFulfillmentType.dineIn`, and
3. a packaged artifact copy of `restaurant_operational_controls.dart`.

Compact output then shows the two menu-order paths and the unrelated `table_sessions_dine_in.test.sql`, omitting the migration/direct-test pair. The same result occurs with `include_linked_records=False`, so the defect is current anchor/working-set selection rather than missing history reuse.

The hypothesis to verify is that existing field coverage, typed one-hop connectivity, content identity, component/path evidence, and current source/test lanes are sufficient to select one coherent bounded working set. Narrow named identities or component novelty must not outrank a substantially broader current source/direct-test pair merely because an unquoted natural phrase resembles a provider symbol. A byte-identical artifact copy must not count as an independent semantic hypothesis unless the query explicitly identifies that path or it contributes unique current typed evidence.

## Allowed Files

Implementation changes are limited to:

- `tools/repoctl/context.py`
- `tools/repoctl/context_retrieval.py` only if a fail-first identity regression proves that unquoted natural punctuation is incorrectly promoted to explicit named identity
- `tools/repoctl/cli.py` only to wire one new category into the existing release-candidate Context benchmark gate
- `tests/repoctl/context/test_context_query.py`
- `tests/repoctl/context/test_context_benchmark.py`
- `tests/repoctl/context/test_field_gate.py`
- `tests/fixtures/context-benchmark/corpus.json`
- `tests/fixtures/context-benchmark/questions.jsonl`
- `tests/fixtures/context-benchmark/expected-sources.json`
- `docs/contracts/repoctl-context-contract.md`

The task record may change only for lifecycle evidence, Verification, and Handoff maintenance.

Read-only field evidence may come from:

- `/mnt/data/workspace/human/gakza`
- `/mnt/data/workspace/human/areum`

All Graph/evidence state for field replay must be written under a disposable external temporary directory, never into an adopter workspace.

## Non-goals

- No project-, path-, SQL-, Supabase-, Gakza-, `operational controls`-, or `dine-in`-specific production rule.
- No new intent classifier, alternate ranker class, second retrieval pipeline, embedding layer, telemetry store, evaluator, or fixture framework.
- No Graph provider/store/schema, component schema, result receipt, completion outcome, catalogue, Knowledge, Task, or attribution schema change.
- No increase to `COMPACT_ITEM_LIMIT`, `COMPACT_CONTINUATION_LIMIT`, Graph anchor budget, traversal depth, or neighbor fan-out.
- No automatic ownership, authority, edit scope, Chosen scope, or Knowledge promotion.
- No use of prior task outcome as an eligibility source or hard override; the result must pass with linked records disabled.
- No blanket exclusion of generated/artifact directories or byte-identical files from full evidence.
- No collapse of legitimate independent multi-owner queries into a single source.
- No release/version intent work, independent causal-execution receipt work, lifecycle change, or unrelated cleanup.
- No new dependency, plugin, `skip`, or `xfail`.

## Acceptance Criteria

1. Add exactly one general fail-first Context benchmark scenario for the collision class. It must contain a current source/direct-test pair with broad multi-field query coverage and a resolved one-hop relation, plus a narrow named-symbol/file decoy and a byte-identical packaged copy. Production code and fixture assertions must contain no adopter-specific routing rule.
2. On the pinned baseline, that scenario must fail because the coherent source/test pair is not compact-visible. Record the before-state anchors, visible paths, omission reason, and full ranks before implementing.
3. After implementation, the synthetic scenario's required source and direct test are compact-visible, its named/artifact decoys are forbidden or omitted, Graph-edge recall is `1.0`, and its category visible recall is `1.0`.
4. Rebuild current Graph state outside the Gakza workspace and run the exact query `current isolated local Supabase owner operational controls waitlist pickup dine-in push protocol gate customer denial rollback`. Compact `likely_change_surface` must include `repos/gakza-control-plane/supabase/migrations/20260715154000_restaurant_operational_controls.sql`, and `tests_and_verification` must include `repos/gakza-control-plane/supabase/tests/database/restaurant_operational_controls.test.sql`.
5. The same Gakza query must not compact-select either `menu_order_models.dart` path or `supabase/tests/database/table_sessions_dine_in.test.sql`. Full evidence may retain all current candidates.
6. Repeat the Gakza replay with `include_linked_records=False`; the current migration/direct-test pair must still pass. A prior outcome may corroborate later ordering but cannot create eligibility or authority.
7. A byte-identical packaged copy cannot consume a separate anchor slot solely through duplicate content, component novelty, or repeated lexical terms. An exact query for that copy must remain independently retrievable.
8. Exact path, filename, symbol, relationship, quoted/sigiled Unicode, and legal hyphenated provider-surface behavior remain unchanged except for the specifically proven natural-language named-identity bug, if that bug is part of the minimal fix.
9. Existing independent multi-owner behavior remains intact: Q-035 keeps both owners; Q-033 keeps its integrated owner/direct-test pair; no one-anchor rule is introduced.
10. Existing Q-004, Q-005, Q-020, and Q-025 relation-closure expectations remain visible with no disconnected test; `typed-consumer-closure` and `typed-structured-dependency-closure` stay at visible recall `1.0`.
11. Missing, stale, partial, ambiguous, unresolved, cross-repository, or non-current evidence never becomes a current Graph anchor or relation. Existing typed warnings and lexical fallback boundaries remain.
12. Existing component crossing and coverage-diversity tests remain green. Component novelty may fill a genuinely independent gap but cannot by itself defeat a more coherent current source/test hypothesis.
13. Compact and full source refs, content digests, Graph provenance, continuations, omission diagnostics, result citations, and repository identity remain consistent. Full selectable evidence remains a superset of compact representatives.
14. `COMPACT_ITEM_LIMIT`, `COMPACT_CONTINUATION_LIMIT`, Graph traversal budgets, and schema versions are unchanged.
15. Default Context, Task Pack, Graph, Task, completion history, Knowledge, and result-receipt behavior do not consume attribution data. Attribution remains opt-in, `correlation_only`, and non-gating.
16. Original adopter workspace product HEADs and status digests are byte-identical before and after field replay; only the explicitly named disposable state/output paths may change.
17. The existing Context benchmark and release-candidate field gate pass with source integrity, no forbidden selection, no cross-repository refs, and the new category threshold.
18. The focused tests, Context suite, release repository tests, full suite, `repoctl check --audit-history`, and `git diff --check` pass. Test count may change only for one independently meaningful fail-first regression; permutation growth is forbidden.
19. A `DE-REPOCTL-CONTEXT-ANCHOR-RANKING` reviewer directly inspects the exact final diff and runtime evidence and returns `APPROVE` before finish.

## Required Runtime Evidence

- Record the clean baseline commit, root status, 875-test full-suite result, 13-event history audit, and release-candidate gate result.
- Preserve the current Gakza before-state evidence: selected anchors, compact source/test paths, desired full ranks/scores, coverage omissions, and the `include_linked_records=False` result.
- Record the synthetic fixture before/after output and prove it uses the same general rule as the field replay.
- Record Gakza product repository HEADs and `git status --porcelain=v1` SHA-256 digests before and after each replay.
- Use a new disposable Graph state directory for the final replay and record its path, snapshot digest, provider completeness, and warnings.
- Run a focused regression set covering the new synthetic scenario, natural-language versus explicit identity, exact artifact retrieval, independent multi-owner behavior, and current direct-test selection.
- Run:

```bash
.venv/bin/python -m pytest tests/repoctl/context/test_context_query.py tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q
.venv/bin/python -m pytest tests/test_release_repository.py -q
./scripts/repoctl field-gate run release-candidate --repo-id main --json
.venv/bin/python -m pytest -q
./scripts/repoctl check --audit-history --json
git diff --check
```

- Verification must state every changed path and confirm that all changes are inside `## Allowed Files` plus lifecycle artifacts.
- Record the final Expert session ID and decision only after all local gates are complete.

## Execution Log

- 20260901T071949Z: task created via repoctl task create.
- 20260901T071949Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T071949Z: Main Director revalidated clean HEAD d0d3743, all TC0-TC7 archives/receipts/commits, 875 full tests, the 13-event history audit, and the release-candidate gate. A fresh temporary-state Gakza replay reproduced the mandatory long-query anchor failure with the desired migration/test at full ranks 4/6 but absent from compact output.
- 20260901T072205Z: Main Director issued RCTL-090-T3-P0-LONG-QUERY-ANCHOR-COHERENCE from clean d0d3743 after reproducing the mandatory Gakza RED case with external temporary Graph state; implement locally through all gates before one final Expert review.
- 20260901T073419Z: Fail-first Q-036 reproduced before production edits: full evidence ranks source/test-path evidence at 1 and 4; Graph anchors selected source, byte-identical packaged copy, and narrow dineIn symbol; compact selected only menu_order_models.py, omitting the source/direct test with compact_budget_exhausted. Source and copy share sha256:40a23f773c802334717648fe0016fe7244783af689e18695bb3887d5e723d414; dine-in was incorrectly marked explicit_named_symbol_identity.
- 20260901T075908Z: Implementation gates passed: Context/query/benchmark/field-gate 266 passed; release repository 8 passed; release-candidate 3/3; full suite 875 passed; 13-event audit passed; git diff --check passed. Fresh external Gakza state /tmp/gakza-anchor-final-u72e7_mi, snapshot sha256:7f27a0b99149b6d791f2d35d4fb8a41b71193ea261b421c57405418c8621b283. Linked-records true/false both compact-selected restaurant_operational_controls migration and direct test, excluded both menu_order_models paths and table_sessions_dine_in.test.sql; all three adopter HEAD/status digests were unchanged.
- 20260901T080658Z: Final Domain Expert DE-REPOCTL-CONTEXT-ANCHOR-RANKING directly reviewed the exact final diff and runtime evidence in agbrowse session 01M1DZPF820HA5JD38JWBXRQSP and returned APPROVE.
- 20260901T080708Z: task finished and verified.

## Verification

- Baseline and RED: clean implementation baseline `d0d37436409a09975ca02d6306c5676ec3d6c955`; Q-036 initially compact-selected only `repos/coherence/menu_order_models.py`, while the coherent source/direct test were omitted with `compact_budget_exhausted`. The source and packaged copy shared `sha256:40a23f773c802334717648fe0016fe7244783af689e18695bb3887d5e723d414`.
- General fix: unquoted hyphenated prose no longer becomes an explicit selector for a differently punctuated provider symbol; byte-identical non-explicit candidates without distinct current typed connectivity share one bounded anchor hypothesis. Exact identity and full evidence remain available.
- Synthetic PASS: Q-036 compact-visible source and direct test, both forbidden decoys absent, category visible recall `1.0`, Graph-edge recall `1.0`; exact `packaged/operational_controls.py` retrieval retained.
- Field PASS: external state `/tmp/gakza-anchor-final-u72e7_mi`, snapshot `sha256:7f27a0b99149b6d791f2d35d4fb8a41b71193ea261b421c57405418c8621b283`, no provider failures. Expected partial capability retained for unsupported SQL semantics; warnings were bounded hot-history and one pre-existing shell parse warning.
- Gakza query PASS with `include_linked_records=true` and `false`: compact source contains `repos/gakza-control-plane/supabase/migrations/20260715154000_restaurant_operational_controls.sql`; compact test contains `repos/gakza-control-plane/supabase/tests/database/restaurant_operational_controls.test.sql`; both `menu_order_models.dart` paths and `supabase/tests/database/table_sessions_dine_in.test.sql` are absent.
- Adopter read-only witness before/after: `gakza-admin-web` HEAD `3671f7da82e6332d38a70a3df339391c57c48a84`, status SHA-256 `f1bc6df346471077dc22a7bd63ba5e83b8399037227b3885c02204ee832d13c8`; `gakza-app` HEAD `e4dfe47d6e544388abf2e8fec536d92bc95f97c6`, status SHA-256 `c4c4e983c3e880d41c8bb4725cd0e88abc4a2310060addbba885a120a9eb0fe3`; `gakza-control-plane` HEAD `320c3f7699718928ece1b1ef79422ea499c06d2c`, status SHA-256 `deb0a79543d48412cb7561a8d07ee2e14af59ec59add0710ebc08a007a573179`.
- PASS: `.venv/bin/python -m pytest tests/repoctl/context/test_context_query.py tests/repoctl/context/test_context_benchmark.py tests/repoctl/context/test_field_gate.py -q` — `266 passed`.
- PASS: `.venv/bin/python -m pytest tests/test_release_repository.py -q` — `8 passed`.
- PASS: `./scripts/repoctl field-gate run release-candidate --repo-id main --json` — `3/3`, failed `0`.
- PASS: `.venv/bin/python -m pytest -q` — `875 passed`.
- PASS: `./scripts/repoctl check --audit-history --json` — 13 events audited, no problems or warnings.
- PASS: `git diff --check`.
- Expert PASS: `DE-REPOCTL-CONTEXT-ANCHOR-RANKING`, agbrowse session `01M1DZPF820HA5JD38JWBXRQSP`, conversation `https://chatgpt.com/c/6a96860c-6f70-83ee-9529-0fcf84a6804a`, decision `APPROVE` after direct inspection of the exact final implementation/test/contract diff and runtime evidence.
- Changed implementation/test/contract paths: `docs/contracts/repoctl-context-contract.md`, `tests/fixtures/context-benchmark/corpus.json`, `tests/fixtures/context-benchmark/expected-sources.json`, `tests/fixtures/context-benchmark/questions.jsonl`, `tests/repoctl/context/test_context_benchmark.py`, `tests/repoctl/context/test_field_gate.py`, `tools/repoctl/cli.py`, `tools/repoctl/context.py`, and `tools/repoctl/context_retrieval.py`. All are inside `## Allowed Files`; the remaining changes are task/Board lifecycle artifacts.

## Last Active Handoff

- Next exact step: Preflight lifecycle health, finish/archive this fully verified task, and create one local commit for the implementation plus completion artifacts.
- First file to open: `docs/tasks/T-20260901071949Z--long-query-anchor-coherence.md`
- First command to run: `./scripts/repoctl task doctor T-20260901071949Z --json`
- Done when: the task is archived with its completion receipt, the Board has no stale row, and the final local commit is clean.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901071949Z--long-query-anchor-coherence.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901071949Z.json`
- Git delivery: Not managed by repoctl.
