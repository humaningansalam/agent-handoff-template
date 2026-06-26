BLOCKED_REPORT_INCOMPLETE

# Technology Benchmark Report: Graph → Task Brief → Reviewed Knowledge → llmwiki

## 1. Executive verdict
- Intended product loop actually achieved?: PARTIAL
- Current architecture best enough to keep?: INCONCLUSIVE
- Recommended stack: current S2 repoctl Graph + Context/Pack remains the only runnable stack in this checkout; do not declare it the winner until S3/S4 are run or explicitly rejected by a human.
- One-paragraph reason: The current repoctl product loop is implemented enough to run Graph selectors, Context query, Context Pack, knowledge candidate/status/query commands, and static wiki render/check. Existing field-test notes report Phase 3-7 PASS. However, the technology-choice verification cannot close because the required benchmark map file is missing, S3/S4 provider/retrieval alternatives are not installed or implemented in this checkout, S5 has no approved records in the current workspace, and S0/S2 measurements show strong S2 gains on materialized fixture retrieval but not enough evidence to prove the architecture is best.
- Human decision needed: decide whether to fund one bounded S3/S4 probe before continuing product development, or accept current S2/S5 as the operational stack despite missing alternative measurements.

## 2. What we were trying to build
- Product loop: Task request -> code localization and impact evidence -> Task Brief / Context Pack -> agent edits with less blind exploration -> tests and completion receipt -> reviewed knowledge candidate -> explicit approval -> next Task Brief reuse -> static llmwiki navigation.
- Non-goals: MCP, graph DB migration, vector-first retrieval, autonomous agent runtime, new production dependencies during verification, and benchmark framework expansion for its own sake.
- Success criteria: prove the current loop is actually built, compare at least S0/S2/S3/S4 and S5 for knowledge-sensitive tasks, use shared snapshots/tasks/gold context/budgets, preserve provenance, and identify required Master Plan changes before more product work.

## 3. Current implementation audit
| Capability | Current behavior | Evidence command/path | Useful? | Gap |
|---|---|---|---|---|
| Graph public query | CLI exposes file/topic/import plus symbol, callers, callees, impact-file, impact-symbol, in-file, and depth selectors. | `./scripts/repoctl graph query --help` | YES | No external SCIP/tree-sitter provider comparison is runnable here. |
| Context query | CLI returns query bundles with token budget, format, explain, and JSON options. | `./scripts/repoctl context query --help` | YES | Current benchmark precision is modest; ranking vs S4 is unproven. |
| Context Pack | CLI packs task startup context with task ID, repo ID, budget, explain, output, and Markdown/JSON formats. | `./scripts/repoctl context pack --help` | YES | Pack benchmark fixture must be materialized before use; not a one-command benchmark in a bare checkout. |
| Context benchmark | Existing command measures recall, precision, forbidden refs, source integrity, graph edge recall, packed recall, and knowledge recall. | `./scripts/repoctl context benchmark --help` | YES | It benchmarks S2 only; S0/S3/S4 adapters are not first-class systems. |
| Reviewed Knowledge | Candidate/status/approve/reject/deprecate/query/render commands exist. | `./scripts/repoctl knowledge --help` | YES | Current workspace has `record_count=0`; S5 cannot be scored as a real knowledge layer here. |
| llmwiki render/check | Static generated pages and manifest can be checked without rewriting. | `.repoctl-state/technology-benchmark/wiki/render-check.json` | PARTIAL | Check passes for an empty wiki; no per-record navigation can be judged with zero records. |
| Field proof | Field-test doc records Phase 3-7 PASS and release-candidate `7/7`. | `docs/field-tests/graph-context-llmwiki-v1.md` | YES | It is not a side-by-side S0/S3/S4 technology bake-off. |
| Required benchmark map | Directive requires `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md`. | `rg --files docs` | NO | File is missing in this checkout; report uses directive labels S0-S6 directly. |

## 4. Benchmark design
| Repository | Shape | Language | Tasks | Why included |
|---|---|---|---|---|
| R1 | Current workspace authority docs | Markdown/Python tooling | Authority and contract questions | Verifies source provenance and doc retrieval. |
| R2 | Materialized `tests/fixtures/context-benchmark` under `/tmp/repoctl-tech-benchmark/repos` | Python | Symbol, caller, import, impact tasks | Exercises custom Python AST/import/call graph and impact evidence. |
| R3 | Materialized fixture frontend files | TypeScript/JavaScript | Relative import impact tasks | Exercises JS/TS file/import impact without a precise JS provider. |
| R4 | `tests/fixtures/context-benchmark-multirepo` present but not replayed in this run | Mixed | Multi-repo isolation tasks | Required by directive, but not fully scored in this report; high-impact limitation. |

| Task | Category | Gold files/symbols/docs/tests | Forbidden misleading context |
|---|---|---|---|
| Q-001..Q-024 | Authority, contract, code-location, impact, import-impact, method/reference/call impact | `tests/fixtures/context-benchmark/questions.jsonl` plus `expected-sources.json`; materialized corpus in `/tmp/repoctl-tech-benchmark` | Fixture `forbidden_refs`; S2 selected none. |
| CP-001..CP-005 | Task Brief / Context Pack startup | `tests/fixtures/context-pack-benchmark/tasks.json` and `cases.json` | Not authority; pack warning `context_pack_not_authoritative` expected. |
| Knowledge-sensitive query | Reviewed Knowledge | Current workspace records/events/candidates | No approved record exists; S5 scored as not usable in current state. |

| Budget | Value | Reason |
|---|---:|---|
| Context budget | 4000 tokens | Same value for S2 materialized fixture run. |
| Pack budget | 5000 tokens | Same value for pack benchmark run. |
| S0 max files shown | Top 5 | Matches Recall@5/Precision@5 comparison. |
| Setup time | Disposable `/tmp/repoctl-tech-benchmark` copy | Avoids production changes while materializing fixtures. |
| S3/S4/S6 budget | NOT_RUN | Providers/ranking stacks absent; running them would require new tooling/dependencies. |

## 5. System comparison scorecard
| System | Context accuracy | Task success | Cost | Setup friction | Explainability | Product fit | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| S0 Agent + rg/read only | 1 | 1 | 4 | 5 | 1 | 2 | Loses on structured provenance and graph refs; materialized fixture Recall@5 `0.042`. |
| S2 Current repoctl Graph + Context Pack | 4 | 3 | 4 | 3 | 5 | 4 | Strong runnable baseline; context Recall@5 `0.917`, Recall@10 `1.0`, pack must-read recall `1.0`. |
| S3 SCIP + lexical retrieval prototype | 0 | 0 | 0 | 0 | 0 | 0 | NOT_RUN: no SCIP/tree-sitter/LSP deps or binaries present. Impact high. |
| S4 Hybrid exact/SCIP graph + FTS/BM25 + repo-map ranking | 0 | 0 | 0 | 0 | 0 | 0 | NOT_RUN: no runnable S3 provider or hybrid ranking layer. Impact high. |
| S5 Hybrid + Reviewed Knowledge | 2 | 1 | 4 | 3 | 5 | 3 | Lifecycle exists but current workspace has `record_count=0`; knowledge-sensitive behavior not scored. |
| S6 Embeddings/rerank | 0 | 0 | 0 | 0 | 0 | 0 | NOT_RUN: directive says run only after measured S4 gap; S4 did not run. |

## 6. Provider bake-off
| Provider | Accuracy | Install/index cost | Failure behavior | Keep/drop reason |
|---|---|---|---|---|
| Current repoctl Python AST/import/call provider | Strong on materialized Python fixture graph edges: graph_edge_recall `1.0`; supports public symbol/caller/callee/impact selectors | Already installed; no production dependency added | Emits completeness/problem data through repoctl outputs | KEEP as current runnable provider. |
| tree-sitter fallback | NOT_RUN | No `tree-sitter` or `tree-sitter-languages` deps/binaries present | Unknown | RETEST only if a disposable install probe is approved. |
| SCIP Python/TypeScript/JavaScript | NOT_RUN | No `scip`, `scip-python`, or related binary/dependency present | Unknown | RETEST before claiming custom provider is best. |
| LSP oracle | NOT_RUN | No `pygls`/`lsprotocol` provider path present | Unknown | Optional reference only; not current default. |
| CodeQL/Joern | NOT_RUN | Out of default task-context scope | Not assessed | DROP as default; maybe special-analysis reference later. |

## 7. Retrieval / Task Brief bake-off
| System | Required recall | Precision | First correct edit surface | Token/line cost | Human answerability |
|---|---:|---:|---|---|---|
| S0 rg/read lexical approximation | Recall@5 `0.042`; Precision@5 `0.008` | Very low on graph refs and exact sections | Often absent from top 5 | Cheap but wastes human/agent interpretation | Low: no source status, grouping, or graph explanation. |
| S2 context benchmark on unmaterialized current checkout | Recall@5 `0.208`; Precision@5 `0.208`; packed recall `0.25` | Misleading because corpus missing `12/12` | Poor for code tasks | Cheap | Useful only for authority docs. |
| S2 context benchmark on materialized fixture | Recall@5 `0.917`; Recall@10 `1.0`; Precision@5 `0.383`; packed recall `1.0`; forbidden `0`; cross-repo refs `0`; integrity `true` | Moderate precision; strong recall and provenance | Strong for most code impact categories; contract category `0.5` | 4000-token budget, quick local run | High: source refs and graph evidence are inspectable. |
| S2 task pack benchmark | mean must-read recall `1.0`; 5 cases; warning count `5` | Not a precision benchmark | Provides must-read startup evidence | 5000-token budget | High if warnings are respected as non-authoritative. |
| S4 hybrid | NOT_RUN | NOT_RUN | Unknown | Unknown | High-impact missing comparison. |

## 8. Reviewed Knowledge bake-off
| Option | Approval UX | Stale safety | Next-task reuse | Complexity | Verdict |
|---|---|---|---|---|---|
| Plain ADR/Markdown notes only | Simple but no review state | Digest/currentness is manual | Reuse depends on retrieval only | Low | Insufficient for knowledge-sensitive loop. |
| Current reviewed knowledge lifecycle | Commands exist for candidate, approve, reject, deprecate, check, query, render; candidates exist in state | Designed for stale/superseded/deprecated exclusion | Field tests claim next-pack reuse, but current workspace has no records | Medium | RETEST with at least one approved record in the benchmark snapshot. |
| Lighter lifecycle | Not implemented | Unknown | Unknown | Lower | Consider only if human review effort proves too high. |

## 9. llmwiki renderer bake-off
| Option | Navigation | Freshness/link check | Custom code burden | Verdict |
|---|---|---|---|---|
| Current custom Markdown output | Generates `INDEX.md`, kind pages, history, and `search-index.json`; empty current workspace limits navigation judgment | `knowledge render --check` reports `current=true`, no missing/stale/broken pages | Existing custom code in repoctl | KEEP for now, but evidence is weak with zero records. |
| MkDocs-compatible generated content | NOT_RUN | NOT_RUN | Could reduce custom navigation/rendering burden | RETEST if reviewed records exist and current navigation becomes a burden. |
| Other docs tooling | NOT_RUN | NOT_RUN | Unknown | DROP from current decision. |

## 10. End-to-end replay
Task request -> localization -> brief -> edit/test -> receipt -> knowledge -> next brief -> wiki.
- Scenario A: Field-test Phase 3 says Graph impact helped choose files, Context Pack was consumed before editing, focused verification passed, and task finish created receipts. This proves the loop shape exists but is not a technology bake-off.
- Scenario B: Field-test Phase 4 says receipt-to-candidate, review Markdown, reviewer/note approval provenance, next-pack reuse, supersede, stale refresh, and reject path passed. Current checkout has no approved records, so this cannot be replay-scored here.
- Scenario C: Field-test Phase 5-7 says wiki navigation/check, closed-loop proof, full pytest, repoctl gates, release archive, and extracted-artifact smoke passed. This supports product viability but not whether S3/S4 would be a better stack.

## 11. Decision
Decision needed: Graph-to-wiki v1 technology stack
Measured winner: INCONCLUSIVE; S2 is the measured runnable winner only because S3/S4/S6 were not runnable.
Why: S2 materially beats S0 on the materialized fixture and has strong provenance, but the required SCIP/hybrid alternatives were not actually measured and S5 is empty in the current workspace.
Tradeoffs: keeping S2 avoids new dependencies and preserves repoctl contracts; revising toward SCIP/S4 might improve references/callers and first-correct-edit ranking but has unmeasured setup and maintenance cost.
Rejected: S0 as primary product path, because it lacks provenance/grouping and scored very low on required context; S6 because the directive only allows it after a measured S4 gap.
Recommendation: INCONCLUSIVE

## 12. Required Master Plan changes
| Current plan item | Keep/change/remove | Reason | Replacement text or action |
|---|---|---|---|
| Progress Ledger claims Phase 6/7 field-verified as product completion evidence | Keep as field evidence, but add technology-decision caveat | Field proof is not a S0/S3/S4 technology bake-off | Add: `Technology-choice verification remains separate from field proof; do not use field proof alone to claim provider/retrieval stack optimality.` |
| Current custom provider as implicit default | Change | S3/S4 not measured; custom provider may be fine but not proven best | Add a vertical-slice decision gate: disposable SCIP/tree-sitter probe before expanding Graph provider work. |
| Context and Pack as separate public layers | Change candidate | Report suggests product may be clearer as Task Brief over Context internals, but evidence is not final | Add decision question: collapse public UX to `Task Brief` while keeping Context as internal evidence bundle if retest confirms no separate user value. |
| Reviewed Knowledge current lifecycle | Keep with retest | Commands exist; current workspace has no approved records to score S5 | Require one approved-record benchmark snapshot before more knowledge UX expansion. |
| llmwiki custom Markdown | Keep short-term | Current check passes and avoids new deps, but empty records weaken evidence | Retest MkDocs-compatible render only after reviewed records exist and navigation burden is measurable. |
| Optional embeddings/rerank | Keep deferred | S4 has not run; no measured retrieval gap justifies S6 | Do not schedule S6 until S4 measured recall/order gap exists. |

## 13. Known limitations
| Limitation | Impact | Current fallback | Reason deferred | Revisit trigger | Severity |
|---|---|---|---|---|---|
| `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md` missing | Cannot verify against the intended benchmark map document | Use directive S0-S6 labels directly | File absent in current checkout | Add/recover map file | High |
| S3/S4 not runnable | Prevents strong KEEP/REVISE stack decision | Keep S2 as operational baseline | No deps/binaries/provider adapters present | One disposable SCIP/tree-sitter/S4 probe | High |
| S5 has no approved records in current workspace | Knowledge-sensitive scoring is weak | Candidate/status/query commands and field-test claims | Avoid manufacturing records during verification | Approved-record benchmark snapshot | High |
| Multi-repo fixture not replayed | Cross-repo leakage evidence incomplete in this report | S2 materialized single-repo run had `cross_repo refs=0` | Time/scope; avoid new framework | Run existing multi-repo fixture in a bound disposable workspace | Medium |
| S0 baseline is a deterministic approximation | Could understate a skilled human/agent using rg/read | Report labels it as approximation | No agent runtime benchmark requested | Manual S0 replay with same task budget | Medium |
| Current wiki has zero records | Navigation quality cannot be judged | `render --check` validates empty pages | Need reviewed records first | Approved knowledge fixture | Medium |

## 14. Evidence index
| Artifact | What it proves | Path |
|---|---|---|
| Directive | Required process, systems, report outline, scoring rules | `docs/TECH_SELECTION_VERIFICATION_DIRECTIVE.md` |
| Missing map search | Required benchmark map absent | `rg --files docs` output in session |
| CLI help | Current public Graph/Context/Knowledge commands exist | `./scripts/repoctl graph query --help`, `./scripts/repoctl context --help`, `./scripts/repoctl knowledge --help` |
| S0 baseline artifact | Same-question lexical baseline over materialized snapshot | `.repoctl-state/technology-benchmark/S0/rg/rg-baseline-materialized.json` |
| S2 materialized context artifact | 24-question context recall/precision/source integrity | `.repoctl-state/technology-benchmark/S2/context/context-benchmark-materialized.json` |
| S2 pack artifact | 5-case must-read recall for task packs | `.repoctl-state/technology-benchmark/S2/pack/pack-benchmark.json` |
| S5 status artifact | Current reviewed record count and candidate state | `.repoctl-state/technology-benchmark/S5/knowledge/status.json` |
| S5 query artifact | Current knowledge query returns zero records | `.repoctl-state/technology-benchmark/S5/knowledge/query.json` |
| Wiki check artifact | Current generated wiki freshness/link check for empty records | `.repoctl-state/technology-benchmark/wiki/render-check.json` |
| Field-test proof | Prior product-loop field verification entries | `docs/field-tests/graph-context-llmwiki-v1.md` |

## 15. Appendix: explorer findings
## Explorer Finding

- Explorer model requested: gpt-5.3-codex-spark first, gpt-5.4-mini fallback
- Actual model used: gpt-5.4-mini
- Area: current product audit / end-to-end
- Repository/task set: directive, field-test doc, repoctl CLI and knowledge modules
- Systems compared: S0/S2/S5

### Claim
The intended loop is implemented end-to-end in repoctl and field-evidenced, but this inspection does not prove the stack is best relative to S0/S3/S4.

### Evidence
- Commands run: `nl -ba tools/repoctl/cli.py`, `nl -ba tools/repoctl/knowledge_candidates.py`, `nl -ba tools/repoctl/knowledge_render.py`, `nl -ba docs/field-tests/graph-context-llmwiki-v1.md`
- Files/artifacts inspected: `tools/repoctl/cli.py`, `tools/repoctl/knowledge_candidates.py`, `tools/repoctl/knowledge_render.py`, `docs/field-tests/graph-context-llmwiki-v1.md`
- Metric values: field tests Phase 3 PASS, Phase 4 PASS, Phase 5 PASS, Phase 6 PASS, Phase 7 PASS; release-candidate gate `7/7`; historical context benchmark mean Recall@5 `0.944444`; pack benchmark mean must-read recall `1.0`
- Example output excerpt: `Graph impact helped choose files, Context Pack was consumed before editing`

### Interpretation
- What this means for the intended product loop: The code paths and field tests show task -> pack -> receipt -> reviewed knowledge -> wiki -> next pack exists.
- What it does not prove: Fresh side-by-side S0/S2/S5/S3/S4 technology superiority.
- Risk or uncertainty: Implementation fidelity is strong, but technology-choice evidence is incomplete.

### Recommendation
KEEP

### Follow-up needed
Run explicit S0/S2/S5/S3/S4 benchmark before final stack decision.

## Explorer Finding

- Explorer model requested: gpt-5.3-codex-spark first, gpt-5.4-mini fallback
- Actual model used: gpt-5.4-mini
- Area: provider
- Repository/task set: repoctl Graph provider checkout
- Systems compared: S2/S3/S4

### Claim
S2 is the only provider stack validated in this checkout; S3/S4 are absent, so the current Python AST/import/call provider is runnable but not proven best.

### Evidence
- Commands run: `rg -n 'tree-sitter|SCIP|lsp|pygls|lsprotocol' pyproject.toml uv.lock tools tests docs`, targeted graph tests
- Files/artifacts inspected: `tools/repoctl/graph_code_provider.py`, `tools/repoctl/graph_import_resolver.py`, `tools/repoctl/code_index.py`, `tools/repoctl/graph.py`, `docs/contracts/repoctl-graph-contract.md`, `pyproject.toml`, `uv.lock`
- Metric values: no provider deps beyond pytest in `pyproject.toml`; `tree_sitter=False`, `scip=False`, `lsprotocol=False`, binaries `scip=NOT_FOUND`, `tree-sitter=NOT_FOUND`; targeted graph tests passed
- Example output excerpt: `Current v1 provider support is python_ast.`

### Interpretation
- What this means for the intended product loop: Current graph can support Python localization today.
- What it does not prove: SCIP/tree-sitter/LSP would not improve precision or coverage.
- Risk or uncertainty: S3/S4 require new disposable tooling before real bake-off.

### Recommendation
RETEST

### Follow-up needed
Run a disposable SCIP/tree-sitter/LSP probe before claiming provider winner.

## Explorer Finding

- Explorer model requested: gpt-5.3-codex-spark first, gpt-5.4-mini fallback
- Actual model used: gpt-5.4-mini
- Area: retrieval / knowledge / wiki
- Repository/task set: context benchmark fixture and current workspace
- Systems compared: S0/S2/S3/S4/S5/S6

### Claim
S2 adds meaningful provenance and grouping over rg/read, but the current checkout alone cannot justify the full technology stack because the initial corpus was unmaterialized, reviewed knowledge is empty, and S3/S4/S6 are not runnable.

### Evidence
- Commands run: `./scripts/repoctl context benchmark`, `./scripts/repoctl context query`, `./scripts/repoctl knowledge query`, `./scripts/repoctl knowledge render --check`, `rg ... docs/adr docs/contracts README.md`
- Files/artifacts inspected: `docs/adr/evidence-context-authority-v0.md`, `docs/contracts/repoctl-context-contract.md`, `docs/contracts/repoctl-graph-contract.md`, `README.md`
- Metric values: unmaterialized context benchmark Recall@5 `0.208333`, Precision@5 `0.208333`, packed recall `0.25`, fixture missing `12/12`; current knowledge query `result_count=0`; render check current for empty wiki
- Example output excerpt: `knowledge_result_count=0`

### Interpretation
- What this means for the intended product loop: S2 is useful for authority docs and provenance.
- What it does not prove: Full loop readiness in the current checkout without materialized corpus and records.
- Risk or uncertainty: Sparse current state weakens S5 and S4 conclusions.

### Recommendation
RETEST

### Follow-up needed
Use a materialized fixture workspace with approved knowledge records and runnable S4.

Decision needed: Graph-to-wiki v1 technology stack
Measured winner: INCONCLUSIVE; S2 is the only measured runnable winner, not a proven best architecture.
Why: S2 beats S0 on materialized retrieval and preserves provenance, but S3/S4 were not runnable and S5 has no approved records in the current workspace.
Tradeoffs: Continuing with S2 is low-friction; revising toward SCIP/S4 may improve precision/call/reference coverage but currently has unknown setup and maintenance cost.
Rejected: S0 as primary product path; S6 before S4 gap evidence.
Recommendation: INCONCLUSIVE
Human options:
A. Approve recommendation
B. Reject and keep current plan
C. Request one bounded retest: disposable S3/S4 provider + hybrid retrieval probe on the existing materialized context fixture and one approved-record knowledge snapshot
Blocked until decision: production integration / next product feature work
Unaffected: read-only review of this report

INCONCLUSIVE
