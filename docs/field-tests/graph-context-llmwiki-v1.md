# Graph Context llmwiki v1 Field Tests

## 2026-06-25 - Phase 0 / Contract Lock

- Copy source: fresh copy at `/tmp/repoctl-phase0.Sltcbe`
- Repository shape: single repo, minimal product repo baseline
- User scenario: confirm the master plan is installed as the product completion contract and current CLI baseline is known before Phase 1 work.
- Commands: `./scripts/repoctl graph build --repo-id main --json`; `./scripts/repoctl graph query --repo-id main --file .gitkeep --json`; `./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --json`; `./scripts/repoctl knowledge render --repo-id main --json`; `./scripts/repoctl knowledge render --repo-id main --check --json`; `./scripts/repoctl check --json`
- Observed output: graph build, file query, context query, render, render check, and workspace check all returned ok in the fresh copy.
- Human inspection: useful. The context output pointed to the Graph authority ADR/contract, and the rendered wiki pages were intentionally sparse because the baseline has zero reviewed records.
- Result: PASS
- Commit candidate: `docs(product): lock graph-to-llmwiki completion contract`

## 2026-06-25 - Phase 1 / Graph Product API

- Copy source: fresh copies at `/tmp/repoctl-phase1.yEaPLw/workspace` and `/tmp/repoctl-phase1-multi.h4tp2H/workspace`
- Repository shape: single repo with Python and TypeScript fixtures; configured multi-repo with `web` and `api`
- User scenario: query a Python symbol, find its callers, inspect depth-2 file impact, fail closed on an ambiguous symbol, verify TS file-level import impact, and confirm multi-repo namespace isolation.
- Commands: `./scripts/repoctl graph query --repo-id main --symbol issue_token --in-file services/token_service.py`; `./scripts/repoctl graph query --repo-id main --callers-of issue_token --in-file services/token_service.py`; `./scripts/repoctl graph query --repo-id main --impact-file services/token_service.py --depth 2`; `./scripts/repoctl graph query --repo-id main --symbol authenticate`; `./scripts/repoctl graph query --repo-id main --impact-file frontend/src/api/tokens.ts`; `./scripts/repoctl graph query --repo-id web --symbol login --json`; `./scripts/repoctl graph query --repo-id api --symbol login --json`
- Observed output: symbol lookup returned one `issue_token` match; callers showed `login --CALLS--> issue_token`; depth-2 impact showed both `handlers/login.py --IMPORTS_FILE--> services/token_service.py` and the caller path; ambiguous `authenticate` returned two candidates and a nonzero `graph_query_ambiguous_symbol`; TS impact returned file-level `frontend/src/client.ts --IMPORTS_FILE--> frontend/src/api/tokens.ts`; multi-repo JSON returned only `repo:web:*` or `repo:api:*` nodes for the selected repo.
- Human inspection: useful. The non-JSON output gives enough direct evidence to decide what file/symbol to inspect next, and the ambiguous result avoids guessing while still listing retry candidates.
- Result: PASS
- Commit candidate: `feat(graph): expose symbol callers and impact queries`

## 2026-06-25 - Phase 2 / Evidence Context v1

- Copy source: fresh copy at `/tmp/repoctl-phase2-final.1Wl6N7/workspace`
- Repository shape: single repo with Python caller fixture plus existing ADR, contract, Graph, Knowledge, and render surfaces
- User scenario: ask a call-impact question, ask an authority question, then drift a reviewed knowledge source and confirm stale knowledge does not enter default context.
- Commands: `./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --format markdown`; `./scripts/repoctl context query "Why must generated wiki not become source authority?" --repo-id main --mode authority --format markdown`; `./scripts/repoctl knowledge candidate build --source docs/adr/evidence-context-authority-v0.md --repo-id main --json`; `./scripts/repoctl knowledge approve <KC> --repo-id main --json`; `./scripts/repoctl context query "Evidence Context authority" --repo-id main --json`
- Observed output: call-impact Markdown included `Callers And Dependents` with `login --CALLS--> validate_token`; authority Markdown put `docs/adr/evidence-context-authority-v0.md` Authority Rules and related contracts in `Must Read`; stale source drift returned zero default `knowledge_results` and lifecycle reported one stale record excluded.
- Human inspection: useful. The Markdown is not a JSON dump; it separates must-read sources, change surface, callers/dependents, verification hints, reviewed knowledge, supporting evidence, and warnings so the next action is visible.
- Result: PASS
- Commit candidate: `feat(context): return actionable evidence groups`

## 2026-06-25 - Phase 3 / Agent Context Pack v1

- Copy source: fresh copy at `/tmp/repoctl-phase3.YuG5np/workspace`
- Repository shape: single repo with Python caller/test fixture, TypeScript relative import fixture, reviewed knowledge record, and repo metadata gate
- User scenario: create three live repo-scoped tasks, generate and read Markdown Context Packs before editing, use pack evidence to make focused changes, verify, and finish tasks without unrelated repo changes.
- Commands: `./scripts/repoctl context pack --task <P1> --repo-id main --format markdown --output .repoctl-state/context-pack/<P1>.md`; `PYTHONPATH=repos uv run python -m pytest repos/tests/test_auth.py`; `./scripts/repoctl context pack --task <P2> --repo-id main --format markdown --output .repoctl-state/context-pack/<P2>.md`; `./scripts/repoctl graph query --repo-id main --impact-file frontend/src/api/tokens.ts --json`; `./scripts/repoctl context pack --task <P3> --repo-id main --format markdown --output .repoctl-state/context-pack/<P3>.md`; `uv run python` authority policy assertion; `./scripts/repoctl check --json`
- Observed output: P1 pack showed `login --CALLS--> validate_token` plus test callers before changing `auth/flow.py`; P2 pack showed `frontend/src/client.ts --IMPORTS_FILE--> frontend/src/api/tokens.ts` and metadata gate required/accepted `.repometa` annotation; P3 pack showed reviewed knowledge under `Current Decisions, Invariants, Failure Modes` before preserving generated wiki/context non-authority flags.
- Human inspection: useful. Reading only `AGENTS.md`, the task, and the generated pack was enough to identify the exact files for each task; broad repo scans were not needed, and task finish receipts were created for all three tasks.
- Result: PASS
- Commit candidate: `feat(context): make task packs directly consumable`

## 2026-06-25 - Phase 4 / Reviewed Knowledge v1 Product Loop

- Copy source: fresh copy at `/tmp/repoctl-phase4-clean.XARLyw`
- Repository shape: single repo with clean product git baseline, `auth.py`, `tests/test_auth.py`, `.repometa`, task lifecycle receipts, and reviewed knowledge records/events generated during the run
- User scenario: finish a real repo-scoped task, promote its completion receipt into reviewed knowledge, verify the next Context Pack reuses it, supersede it with a newer decision, then simulate source drift and approve a refreshed replacement.
- Commands: `./scripts/repoctl task create ... --start`; `./scripts/repoctl task finish <T> --verification-file /tmp/phase4-k1-verification.md --json`; `./scripts/repoctl knowledge candidate build --from-receipt <T> --repo-id main --kind invariant --json`; `./scripts/repoctl knowledge candidate show <KC> --repo-id main --format markdown`; `./scripts/repoctl knowledge approve <KC> --repo-id main --reviewed-by phase4-dogfood --note-file /tmp/phase4-k1-note.md --json`; `./scripts/repoctl context pack --task <next-task> --repo-id main --format markdown`; `./scripts/repoctl knowledge approve <replacement-KC> --repo-id main --supersedes <old-K> --reviewed-by phase4-dogfood --note-file /tmp/phase4-k2-note.md --json`; `./scripts/repoctl knowledge check --repo-id main --json`; `./scripts/repoctl knowledge candidate refresh --record-id <stale-K> --repo-id main --json`; `./scripts/repoctl knowledge approve <refresh-KC> --repo-id main --reviewed-by phase4-dogfood --note-file /tmp/phase4-k3-note.md --json`
- Observed output: K1 created receipt `docs/tasks/.repoctl-state/completions/T-20260625021152Z.json`, candidate `KC-20260625021152Z--centralize-token-validation-invariant-a256b82a`, and record `K-20260625021152Z--centralize-token-validation-invariant-a256b82a`; the Markdown review showed origin task, verification artifact, changed files, source refs, digest currentness, check status, and approve/reject/supersede commands; the next Context Pack included the reviewed record under `Current Decisions, Invariants, Failure Modes`; K2 default query returned replacement record `K-20260625021153Z--decision-a2567919` and excluded the superseded K1 record; K3 source drift made `knowledge check` fail, default query excluded stale K2, refresh created `KC-20260625021154Z--decision-a25637c4`, and approval produced current record `K-20260625021154Z--decision-a25637c4` superseding K2.
- Human inspection: useful. The review Markdown was sufficient to approve without reading raw JSON, and approval events/records carried reviewer label, review note, source digest set, candidate id, timestamp, and supersession relation. The Context Pack reuse proved reviewed knowledge affects the next task without becoming task scope authority.
- Result: PASS
- Commit candidate: `feat(knowledge): make candidate review and provenance actionable`

## 2026-06-25 - Phase 5 / llmwiki v1 Useful Static Product

- Copy source: fresh copy at `/tmp/repoctl-phase5.yFOhW9`
- Repository shape: single repo with `auth.py`, task completion receipt-derived failure mode, current and superseded decisions, one intentionally stale decision source, and generated wiki output under `docs/knowledge/generated`
- User scenario: render a static wiki, answer five human navigation questions from generated Markdown only, and verify `knowledge render --check` catches currentness/link integrity without rewriting pages.
- Commands: `./scripts/repoctl knowledge render --repo-id main --json`; `./scripts/repoctl knowledge render --repo-id main --check --json`; manual inspection of `INDEX.md`, `decisions.md`, `records/<id>.md`, `targets/files/auth.py.md`, `history.md`, and `search-index.json`
- Observed output: `INDEX.md` linked Decisions, Invariants, Failure Modes, History, and Search index with lifecycle counts; `decisions.md` separated current and historical records; per-record pages showed lifecycle status, claim, source section/currentness, reviewer/provenance, supersession, and navigation; `targets/files/auth.py.md` showed the current file-target failure mode; `history.md` linked current, superseded, and stale records; `search-index.json` exposed deterministic record rows with `applies_to.files`.
- Human inspection: useful. The five questions were answerable without raw JSON: current Graph authority decision from `INDEX.md → decisions.md → record`, auth file failure mode from `targets/files/auth.py.md`, source section from the record page, replacement from the superseded record/history link, and stale source from the stale record page with `status=\`digest_mismatch\``.
- Result: PASS
- Commit candidate: `feat(knowledge): render navigable static wiki`

## 2026-06-25 - Phase 6 / Full Closed-Loop Field Proof

- Copy sources: Golden A single-impact `/tmp/repoctl-phase6A-single.w6ltsG`; Golden A multi-repo `/tmp/repoctl-phase6A-multi.IW6Mgy`; Golden B `/tmp/repoctl-phase6B.ZB03an`; Golden C `/tmp/repoctl-phase6C.mjC4s9`; fixture gate copy `/tmp/repoctl-phase6-fixture.fpxO8Q`
- Repository shapes: direct single repo with Python caller and JS/TS relative import surfaces; configured `repos/web` + `repos/api` collection; task-work repo with clean product git baseline; knowledge-sensitive repo with receipt and document-derived records
- Golden Workflow A: PASS. `graph query --symbol issue_token --in-file services/token_service.py` returned one precise match; `--callers-of issue_token` showed `login --CALLS--> issue_token`; `--impact-file services/token_service.py --depth 2` included `handlers/login.py`; TS import impact included `frontend/src/client.ts`; configured `web` and `api` graph queries returned isolated `repo:web:*` and `repo:api:*` evidence only.
- Golden Workflow B: PASS. A live task generated `/tmp/phase6B-pack.md`, the pack pointed to `auth.py` and `tests/test_auth.py`, the actual edit was limited to `auth.py`, `PYTHONDONTWRITEBYTECODE=1 python3 -c "from tests.test_auth import check; check()"` passed, and `task finish` created receipt `docs/tasks/.repoctl-state/completions/T-20260625041753Z.json`; product git status after finish contained only `M auth.py`.
- Golden Workflow C: PASS. Receipt-derived candidate review `/tmp/phase6C-review.md` showed origin and changed files; approval produced reviewed record `K-20260625041911Z--capture-auth-invariant-knowledge-a2561f16`; `knowledge query`, `knowledge render --check`, and next Context Pack `/tmp/phase6C-pack.md` all reused the same record identity; a document-derived stale record was excluded by default query, refreshed, and superseded by `K-20260625042000Z--decision-a256f771`.
- Fixture quality gates: PASS in `/tmp/repoctl-phase6-fixture.fpxO8Q` after explicit materialization. `context benchmark` reported mean Recall@5 `0.916667` with source integrity true; `context pack-benchmark` reported mean must-read recall `1.0` across 5 cases.
- Human inspection: useful. The workflows proved the intended product chain rather than isolated commands: Graph impact helped choose files, Context Pack was consumed before editing, task finish produced receipt evidence, reviewed knowledge reappeared in query/wiki/pack, stale source was excluded, and refreshed replacement became current.
- Result: PASS
- Commit candidate: final release evidence commit

## 2026-06-25 - Phase 7 / Release Artifact E2E

- Copy source: release archive `/tmp/repoctl-phase7-artifact-smoke/dist/agent-workspace-control-plane-0.2.137.tar.gz`, extracted to `/tmp/repoctl-phase7-artifact-smoke/agent-workspace-control-plane-0.2.137`
- Repository shape: extracted upgrade artifact plus minimal workspace state (`docs/BOARD.md`, `docs/tasks`, `docs/archive/tasks`, direct `repos` git repository, `.repometa`, and `auth.py`)
- User scenario: run the released artifact as a fresh workspace, verify Graph/Context/Knowledge/wiki commands, and run the release-candidate field gate.
- Commands: `uv run pytest -q`; `git diff --check`; `./scripts/repoctl check --json`; `./scripts/repoctl meta check --json`; `uv run python -m tools.repoctl.release /tmp/repoctl-phase7-artifact-smoke/dist`; extracted artifact `./scripts/repoctl repo list --json`; `./scripts/repoctl graph query --symbol issue_token --repo-id main --json`; `./scripts/repoctl context query "Graph authority source bundle" --repo-id main --budget-tokens 1200 --json`; `./scripts/repoctl field-gate run release-candidate --repo-id main --json`; `./scripts/repoctl knowledge candidate build --source docs/adr/evidence-context-authority-v0.md --repo-id main --kind decision --json`; `./scripts/repoctl knowledge approve <KC> --repo-id main --json`; `./scripts/repoctl knowledge query "Context returns source bundles" --repo-id main --json`; `./scripts/repoctl knowledge render --repo-id main --json`; `./scripts/repoctl knowledge render --repo-id main --check --json`
- Observed output: full pytest passed `463 passed`; repoctl check and meta check returned ok; release builder produced `agent-workspace-control-plane-0.2.137.tar.gz`; extracted `repo list` returned the reserved `main` repo; Graph symbol query returned one precise `issue_token` match with `inventory_complete=true`; Context query returned ADR/contract evidence within budget; release-candidate field gate passed 7/7 gates with context benchmark mean Recall@5 `0.944444` and pack benchmark mean must-read recall `1.0`; Knowledge candidate approval produced record `K-20260625071132Z--decision-a25661cb`; knowledge query returned one reviewed record; wiki render/check reported `current=true`, no missing/stale/unreadable pages, and no broken links.
- Human inspection: useful. The generated record page contained `Approved from candidate` and source `status=\`current\``; generated pages were explicitly non-authoritative; the extracted artifact proved the full product chain without relying on the original checkout's generated state.
- Note: running `repo list` in the bare extracted archive before creating `docs/BOARD.md` correctly failed root detection because the archive is an upgrade source and preserves the adopting workspace's Board instead of shipping a live Board.
- Result: PASS
- Commit candidate: `release(repoctl): close graph context wiki loop`
