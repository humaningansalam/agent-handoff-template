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
