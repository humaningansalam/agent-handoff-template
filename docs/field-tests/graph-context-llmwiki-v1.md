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
