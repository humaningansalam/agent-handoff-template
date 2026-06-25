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
