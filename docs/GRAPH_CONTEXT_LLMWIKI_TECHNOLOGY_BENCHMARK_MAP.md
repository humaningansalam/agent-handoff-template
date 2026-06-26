# Graph Context llmwiki Technology Benchmark Map

Status: recovered benchmark map for technology-choice verification  
Canonical path: `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md`

## Recovery Note

This file was expected by `docs/TECH_SELECTION_VERIFICATION_DIRECTIVE.md` and was not present in the current checkout or reachable git history when searched with:

```bash
find . -path './.git' -prune -o -iname '*BENCHMARK*MAP*' -print
git log --all --name-only -- docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md
```

The benchmark labels and decision rules below restore the missing map from the authoritative directive's S0-S6 system definitions and the current product architecture. This file is a comparison map, not a product implementation plan.

## Purpose

The map defines the systems that must be compared before declaring the Graph -> Task Brief -> Reviewed Knowledge -> llmwiki architecture best enough to keep.

The comparison must answer:

1. Does repoctl beat a plain agent using `rg` and direct reads for the same tasks?
2. Does the current custom Graph/Context stack remain sufficient once provider alternatives are tested?
3. Does Reviewed Knowledge add measurable value for knowledge-sensitive task starts?
4. Are embeddings or rerankers needed now, or only after a measured hybrid retrieval gap?

## Systems

| Label | System | What it represents | Required evidence | Default verdict rule |
|---|---|---|---|---|
| S0 | Agent + `rg`/read only | No repoctl Graph, Context Pack, Reviewed Knowledge, or wiki; deterministic lexical/file inspection baseline | Same task text, same materialized snapshot, same top-K and budget | Must be beaten on provenance and required-context recall to justify repoctl product layers. |
| S1 | Aider-style repo map baseline | Lightweight structure/ranking approximation using definitions, mentions, and token-budget packing | Optional; useful when cheap to approximate without importing Aider | Informational only unless it beats S2 materially. |
| S2 | Current repoctl Graph + Context Pack | Current custom code index/Graph provider, Context query, Task Pack, source refs, digests, completeness | Existing repoctl commands and fixture benchmarks | Keep only if close to best measured system and provenance stays stronger. |
| S3 | SCIP and/or tree-sitter provider prototype | External precise provider or parser fallback for definitions/references/imports | Disposable provider probe only; no committed dependency | Revise provider plan only if measurable accuracy gain exceeds setup/ops burden. |
| S4 | Hybrid exact/SCIP graph + FTS/BM25 + repo-map ranking | Exact provider refs plus lexical scoring and dependency/repo-map style rank boosts | Disposable retrieval probe over same gold refs | Revise retrieval plan if S4 materially improves precision, first edit surface, or recall. |
| S5 | Hybrid + Reviewed Knowledge | S2/S4 evidence plus approved current knowledge records, stale exclusion, next-pack reuse, wiki render | Approved-record benchmark snapshot | Keep lifecycle only if it improves knowledge-sensitive tasks without hiding source authority. |
| S6 | Optional embeddings/rerank | Semantic retrieval or reranking after S4 misses labeled context | Run only after measured S4 retrieval gap | Do not adopt without targeted gain and fallback behavior. |

## Required Repository Shapes

| Label | Shape | Minimum contents | Purpose |
|---|---|---|---|
| R1 | Small Python repository | Simple functions/imports plus docs | Baseline definition/import behavior. |
| R2 | Medium Python repository | Aliases/classes/cross-file calls | Provider precision, callers, and impact. |
| R3 | Medium TypeScript/JavaScript repository | Relative imports and package-like structure | JS/TS import and fallback behavior. |
| R4 | Configured multi-repo pair | Same basenames, symbols, or topics across repos | Namespace isolation and leakage detection. |

## Required Task Categories

At least twelve task texts must be scored across the same snapshots and budgets:

| Category | Minimum count | Measures |
|---|---:|---|
| Definition/reference/caller/impact | 4 | Provider and graph accuracy. |
| Bug or behavior change requiring tests | 3 | First correct edit surface and verification hints. |
| Import/dependency including JS/TS | 2 | Import resolver and language fallback. |
| Knowledge-sensitive decision/invariant/failure mode | 2 | S5 currentness, provenance, and reuse. |
| Multi-repo ambiguity/isolation | 1 | Explicit repo namespace safety. |

## Metrics

Every compared system must report, or explicitly mark `NOT_RUN` with blocker type:

- Recall@5 and Recall@10 over required source refs
- Precision@5
- forbidden selected refs
- source-ref integrity
- first correct edit surface rank
- setup/index time
- query/scoring time where available
- output explainability/provenance
- cross-repo forbidden ref count for R4
- stale/current knowledge behavior for S5

## Budget Contract

Use the same per-task budget for S0, S2, S3, and S4:

| Budget | Default |
|---|---:|
| max files shown | 5 |
| max source refs evaluated for recall | 10 |
| max context/query budget | 4000 tokens or comparable character cap |
| pack budget | 5000 tokens |
| setup/index time | recorded separately, not hidden in query score |
| temporary artifacts | `/tmp/...` or `.repoctl-state/technology-benchmark/...` only |

## Decision Rules

- Do not call S2 best unless S3/S4 were actually run or explicitly rejected with measured blocker/cost evidence.
- Do not run S6 until S4 has a measured retrieval gap.
- Do not keep Reviewed Knowledge complexity merely because commands exist; S5 must prove approved-record reuse and stale exclusion.
- Prefer the lowest-operational-burden stack that meets accuracy, provenance, and task-success requirements.
- Any missing high-impact system keeps the final report `BLOCKED_REPORT_INCOMPLETE` or `INCONCLUSIVE`.
