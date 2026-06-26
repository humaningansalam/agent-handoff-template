TECH_DECISION_READY

# Technology Benchmark Report: Graph → Task Brief → Reviewed Knowledge → llmwiki

## 1. Executive verdict
- Intended product loop actually achieved?: YES
- Current architecture best enough to keep?: KEEP
- Recommended stack: KEEP current S2/S5 architecture: repoctl custom Graph provider + Context/Task Pack + reviewed Knowledge lifecycle + custom static Markdown llmwiki; keep S3/S4/S6 as measured optional probes, not default product work.
- One-paragraph reason: The bounded retest restored the missing benchmark map, ran S0/S2/S3/S4 over the same materialized fixture and budgets, created an approved-record S5 snapshot, and replayed the multi-repo fixture. S2 beat S0 and the disposable S4 hybrid on required context recall and provenance: S2 Recall@5 `0.917`, Recall@10 `1.0`, Precision@5 `0.383`; S4 Recall@5 `0.708`, Recall@10 `0.875`, Precision@5 `0.300`; S0 Recall@5 `0.292`, Precision@5 `0.067`. S3 was runnable only as a disposable `scip-typescript` probe and did not improve the mixed fixture. S5 proved approved-record query, next-pack reuse, wiki render/check reuse, and stale default exclusion.
- Human decision needed: approve keeping S2/S5 as the default architecture and deferring S3/S4/S6 to measured optional upgrades.

## 2. What we were trying to build
- Product loop: Task request → code localization and impact evidence → Task Brief / Context Pack → agent edits with less blind exploration → tests and completion receipt → reviewed knowledge candidate → explicit approval → next Task Brief reuse → static llmwiki navigation.
- Non-goals: MCP, graph DB migration, vector-first retrieval, autonomous agent runtime, committed production dependencies for provider experiments, and benchmark framework expansion.
- Success criteria: restore the benchmark map, compare S0/S2/S3/S4 on the same materialized task fixture and budget, include S5 knowledge-sensitive behavior, replay multi-repo isolation, and produce a reviewable KEEP/REVISE/INCONCLUSIVE decision.

## 3. Current implementation audit
| Capability | Current behavior | Evidence command/path | Useful? | Gap |
|---|---|---|---|---|
| Benchmark map | Restored as a committed docs artifact after search found it absent from checkout and git history. | `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md` | YES | The original file was absent; the restored map is derived from the directive's S0-S6 labels. |
| Graph public query | CLI supports exact file/topic/import plus symbol, callers, callees, impact-file, impact-symbol, in-file, and depth selectors. | `./scripts/repoctl graph query --help` | YES | External providers remain optional; no default provider rewrite justified. |
| Context query and benchmark | Existing command measures recall, precision, source integrity, forbidden refs, graph edge recall, packed recall, and knowledge recall. | `.repoctl-state/technology-benchmark/S2/context/context-benchmark-materialized.json` | YES | Precision is moderate, but S4 did not beat it. |
| Context Pack | Existing pack and pack benchmark commands produce must-read startup context with non-authoritative warnings. | `.repoctl-state/technology-benchmark/S2/pack/pack-benchmark.json` | YES | Keep as Task Brief implementation surface; do not split more public concepts. |
| Reviewed Knowledge | Candidate approval creates immutable record and event; query returns reviewed record; source drift makes it stale and excluded by default. | `.repoctl-state/technology-benchmark/S5/knowledge/*.json` | YES | Lifecycle is justified at current complexity; keep explicit approval. |
| llmwiki render/check | Render/check reuses the approved record ID and source bundle with freshness/link checks. | `.repoctl-state/technology-benchmark/S5/knowledge/render-check-current.json` | YES | MkDocs retest is not justified yet because custom Markdown passes required freshness/navigation checks. |
| Multi-repo isolation | Repo-id matched questions returned forbidden cross-repo ref count `0` for both `web` and `api`. | `.repoctl-state/technology-benchmark/multirepo/multirepo-filtered-summary.json` | YES | Running all fixture questions against a single repo intentionally shows cross-repo refs for opposite-repo questions; report uses matched repo_id rows. |

## 4. Benchmark design
| Repository | Shape | Language | Tasks | Why included |
|---|---|---|---|---|
| R1 | Workspace docs and contracts | Markdown/Python tooling | Authority and contract questions | Verifies source provenance and doc retrieval. |
| R2 | Materialized `tests/fixtures/context-benchmark` under `/tmp/repoctl-tech-benchmark2/repos` | Python | Symbol, caller, import, impact tasks | Exercises custom AST/import/call graph and impact evidence. |
| R3 | Materialized fixture frontend files | TypeScript/JavaScript | Relative import impact tasks | Exercises JS/TS baseline and disposable `scip-typescript` probe. |
| R4 | Collection workspace `repos/web` + `repos/api` under `/tmp/repoctl-tech-multirepo` | Python multi-repo | Same basename/symbol isolation tasks | Verifies explicit repo_id prevents same-name leakage. |

| Task | Category | Gold files/symbols/docs/tests | Forbidden misleading context |
|---|---|---|---|
| Q-001..Q-024 | Authority, contract, code-location, impact, import-impact, method/reference/call impact | `tests/fixtures/context-benchmark/questions.jsonl` and `expected-sources.json` | Fixture `forbidden_refs`; S0/S2/S3/S4 selected `0`. |
| CP-001..CP-005 | Task Brief startup | `tests/fixtures/context-pack-benchmark/tasks.json` and `cases.json` | Pack is non-authoritative; warnings are expected. |
| S5-K-001 | Knowledge-sensitive source authority decision | Candidate from `docs/adr/evidence-context-authority-v0.md`, approved record `K-20260626084606Z--decision-a25661cb` | Stale source after digest drift must be excluded by default. |
| Q-MR-001..Q-MR-008 | Multi-repo isolation | `tests/fixtures/context-benchmark-multirepo/*` | Opposite repo same basename/symbol refs. |

| Budget | Value | Reason |
|---|---:|---|
| max files/refs shown | 5 for Precision@5, 10 for Recall@10 | Same across S0/S2/S3/S4. |
| Context budget | 4000 tokens | Matches S2 fixture benchmark and S4 temp scorer budget. |
| Pack budget | 5000 tokens | Matches pack benchmark and S5 next-pack check. |
| Setup/index time | Recorded separately | Prevents hiding provider setup cost in retrieval quality. |
| Temporary artifacts | `/tmp/...` and `.repoctl-state/technology-benchmark/...` | No production dependency or adapter committed. |

## 5. System comparison scorecard
| System | Context accuracy | Task success | Cost | Setup friction | Explainability | Product fit | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| S0 Agent + rg/read only | 2 | 2 | 5 | 5 | 1 | 2 | Loses: Recall@5 `0.292`, Precision@5 `0.067`, no structured provenance. |
| S2 Current repoctl Graph + Context Pack | 5 | 4 | 4 | 4 | 5 | 5 | Winner: Recall@5 `0.917`, Recall@10 `1.0`, Precision@5 `0.383`, source integrity `true`. |
| S3 SCIP/tree-sitter provider probe | 1 | 1 | 2 | 2 | 3 | 2 | Loses for current fixture: `scip-typescript` required temp config and scored Recall@5 `0.083`; no Python SCIP gain measured. |
| S4 Hybrid exact provider + lexical + repo-map/dependency ranking | 3 | 3 | 3 | 3 | 4 | 3 | Loses: temp hybrid Recall@5 `0.708`, Recall@10 `0.875`, Precision@5 `0.300`; first edit rank improves but accuracy drops vs S2. |
| S5 S2 + Reviewed Knowledge | 4 | 4 | 4 | 4 | 5 | 5 | Keep: approved record reused by query, pack, render/check, and stale exclusion works. |
| S6 Embeddings/rerank | 0 | 0 | 0 | 0 | 0 | 0 | NOT_RUN by rule: S4 did not expose a retrieval gap that justifies embeddings/rerank. |

## 6. Provider bake-off
| Provider | Accuracy | Install/index cost | Failure behavior | Keep/drop reason |
|---|---|---|---|---|
| Current repoctl Python AST/import/call provider | S2 materialized fixture graph_edge_recall `1.0`; context Recall@5 `0.917` | Already present; no production dependency added | Emits completeness/problem data through repoctl artifacts | KEEP as default provider. |
| `scip-typescript` disposable probe | S3 Recall@5 `0.083`; only TS fixture documents visible; did not improve full benchmark | `npm exec @sourcegraph/scip-typescript`; failed without `tsconfig.json`; with temp `tsconfig.json`, index succeeded in `real 1.32s`, output `1373` bytes | Missing project config produced `no files got indexed`; successful probe remained temp-only | DROP as default now; revisit only for real TS projects with existing config. |
| tree-sitter disposable availability | CLI install via `npm exec tree-sitter-cli -- --version` succeeded (`0.26.9`) but no grammar/index probe was needed after S4 lost | Requires temp grammar/parser setup to produce useful refs | Not integrated with repoctl contracts | DROP from default; keep as fallback candidate only after language coverage failure. |
| SCIP Python / LSP | Python module/binary absent in current checkout; explorer found disposable `uv --with` can resolve related packages but no production provider path exists | Would require temp adapter and schema interpretation | High implementation uncertainty | RETEST only if future Python reference/caller failures beat current provider. |

## 7. Retrieval / Task Brief bake-off
| System | Required recall | Precision | First correct edit surface | Token/line cost | Human answerability |
|---|---:|---:|---|---|---|
| S0 rg/read lexical approximation | Recall@5 `0.292`; Recall@10 `0.833` | Precision@5 `0.067` | Mean rank `7.75` | Low setup cost, high interpretation cost | Low: paths only, no lifecycle/source status. |
| S2 context benchmark | Recall@5 `0.917`; Recall@10 `1.0` | Precision@5 `0.383` | Existing repoctl explanation/source refs; first rank not separately exported | 4000-token budget; no new dependency | High: digest/source refs/completeness and graph evidence. |
| S4 temp hybrid | Recall@5 `0.708`; Recall@10 `0.875` | Precision@5 `0.300` | Mean rank `5.42`, better than S0 but below S2 accuracy | Temp scorer only; uses graph + BM25 + SCIP TS refs | Medium-high: explainable reasons but not a product command. |
| S2 task pack benchmark | mean must-read recall `1.0`; required must-read count `6`; warning count `5` | Not a precision benchmark | Must-read docs are surfaced | 5000-token budget | High if non-authoritative warnings are respected. |

## 8. Reviewed Knowledge bake-off
| Option | Approval UX | Stale safety | Next-task reuse | Complexity | Verdict |
|---|---|---|---|---|---|
| Plain ADR/Markdown notes only | Simple but no approval event or reviewed status | Manual only | Retrieval may find source docs but cannot distinguish current reviewed decision | Low | Loses for knowledge-sensitive tasks. |
| Current reviewed knowledge lifecycle | Candidate from source, approve with reviewer/note, immutable record, append-only event | Source digest drift changes record status to stale; default query returns `0`, `--include-stale` returns the record | `context pack` included record `K-20260626084606Z--decision-a25661cb`; render/check reused same record ID | Medium | KEEP: complexity is justified by currentness and reuse. |
| Lighter lifecycle | Not implemented | Would need to re-prove stale exclusion | Unknown | Lower | Not needed now. |

## 9. llmwiki renderer bake-off
| Option | Navigation | Freshness/link check | Custom code burden | Verdict |
|---|---|---|---|---|
| Current custom Markdown output | Render produced index/kind/history/per-record pages for record `K-20260626084606Z--decision-a25661cb` | `knowledge render --check` returned `current=true`, `broken_links=[]`, record_count `1` before drift | Existing code, no new dependency | KEEP. |
| MkDocs-compatible generated content | NOT_RUN | NOT_RUN | Might reduce custom rendering later | Do not schedule now; current renderer passed bounded evidence. |
| Other docs tooling | NOT_RUN | NOT_RUN | Unknown | DROP from current decision. |

## 10. End-to-end replay
Task request → localization → brief → edit/test → receipt → knowledge → next brief → wiki.
- Scenario A: S2 materialized benchmark localizes source docs, graph files, Python symbols/callers, and impact refs with Recall@5 `0.917`, Recall@10 `1.0`, and forbidden refs `0`.
- Scenario B: S5 approved `KC-20260626084606Z--decision-a25661cb` into `K-20260626084606Z--decision-a25661cb`; `knowledge query`, `context pack`, and `knowledge render --check` all reused that record identity while current.
- Scenario C: After appending a drift marker to the source ADR in the disposable workspace, `knowledge check` reported `knowledge_source_digest_drift`, default `knowledge query` returned `0`, and `--include-stale` returned the stale record.

## 11. Decision
Decision needed: Graph-to-wiki v1 technology stack
Measured winner: S2/S5 current architecture
Why: S2 has the best measured retrieval accuracy and provenance; S4 did not beat S2; S3 adds setup/config cost without current accuracy gain; S5 proves reviewed record reuse and stale exclusion.
Tradeoffs: S4 had better first-correct-surface mean rank than S0 and may become useful later, but its current temp probe lowers recall/precision relative to S2 and would add uncommitted adapter work. `scip-typescript` can be useful for real TS repos with existing config, but the current fixture does not justify making it primary.
Rejected: S0 as product path; S3 as default provider; S4 as default retrieval stack; S6 because S4 did not expose a gap.
Recommendation: KEEP current S2/S5 architecture

## 12. Required Master Plan changes
| Current plan item | Keep/change/remove | Reason | Replacement text or action |
|---|---|---|---|
| S2 Graph/Context/Pack as default product path | Keep | Measured winner on same materialized fixture and budget | No rewrite required before product work continues. |
| Reviewed Knowledge lifecycle | Keep | Approved-record query, next-pack reuse, render/check, and stale exclusion all passed | No rewrite required; keep explicit human approval and immutable records. |
| S3/S4 provider/retrieval alternatives | Change to optional measured upgrade only | Bounded retest did not beat S2 | Add only if future failures show S2 recall/precision gap on real tasks. |
| S6 embeddings/rerank | Keep deferred | S4 did not show a gap | No scheduled work. |
| Benchmark map reference | Keep restored map | Required directive input was absent | Keep `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md` as the comparison map. |
| Public product concepts | Keep current implementation, avoid adding more | Context/Pack/Knowledge/wiki loop is field-evidenced and measured | If wording is simplified later, use Task Brief as UX label without changing authority boundaries. |

## 13. Known limitations
| Limitation | Impact | Current fallback | Reason deferred | Revisit trigger | Severity |
|---|---|---|---|---|---|
| Restored map is reconstructed from directive, not recovered from original file history | Low-medium | Report states search commands and absence | Original file absent from checkout and git history | Original map is found elsewhere | Medium |
| S4 is a disposable scorer, not a product command | Medium | S2 remains product path | User explicitly disallowed committed adapters/frameworks | Real task shows S2 ranking failure | Medium |
| S3 `scip-typescript` needed temp `tsconfig.json` | Medium | Current Graph provider and lexical JS/TS file evidence | No committed dependency/config allowed | Real TS repo already has config and S2 misses refs | Medium |
| Multi-repo fixture CLI scores all fixture questions per selected repo | Low | Filtered repo_id-matching rows prove explicit repo selection has `0` leakage | Existing benchmark command behavior not changed during verification | Need first-class multi-repo aggregate report | Low |
| S0 is deterministic approximation, not a live human agent | Medium | Uses same task texts/gold refs/budget | No agent runtime benchmark framework allowed | Human wants live-agent A/B replay | Medium |

## 14. Evidence index
| Artifact | What it proves | Path |
|---|---|---|
| Restored benchmark map | S0-S6 system map and decision rules are back in checkout | `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md` |
| S2 context benchmark | Current repoctl context accuracy on materialized fixture | `.repoctl-state/technology-benchmark/S2/context/context-benchmark-materialized.json` |
| S0/S3/S4 hybrid probe | Same-task comparative metrics for lexical baseline, SCIP TS probe, temp hybrid scorer | `.repoctl-state/technology-benchmark/S4/hybrid/hybrid-results.json` |
| Provider availability | Local and disposable provider availability checks | `.repoctl-state/technology-benchmark/probe/provider-availability.txt`, `.repoctl-state/technology-benchmark/S3/provider/*` |
| Pack benchmark | Task Brief must-read recall | `.repoctl-state/technology-benchmark/S2/pack/pack-benchmark.json` |
| S5 approved record | Candidate, approval, query, pack, render/check, drift, and stale exclusion artifacts | `.repoctl-state/technology-benchmark/S5/knowledge/` |
| Multi-repo replay | repo_id-matched forbidden/cross-repo counts `0` for web/api | `.repoctl-state/technology-benchmark/multirepo/multirepo-filtered-summary.json` |
| Field-test proof | Prior product-loop field verification entries | `docs/field-tests/graph-context-llmwiki-v1.md` |

## 15. Appendix: explorer findings
## Explorer Finding

- Explorer model requested: gpt-5.3-codex-spark
- Actual model used: gpt-5.4-mini
- Fallback reason: the multi-agent model override list available in this environment did not include `gpt-5.3-codex-spark`; the highest requested available fallback was `gpt-5.4-mini`.
- Area: provider/retrieval
- Repository/task set: workspace root plus `tests/fixtures/context-benchmark`
- Systems compared: S2/S3/S4

### Claim
A bounded disposable S3 probe is feasible here without production edits, and the existing fixture-driven benchmark flow can compare artifacts; a true production S4 path is absent, so S4 must remain a temporary scorer/probe unless later justified.

### Evidence
- Commands run: `rg -n "tree-sitter|SCIP|scip|BM25|repo-map" pyproject.toml uv.lock tools tests docs scripts -S`; `./scripts/repoctl context benchmark --help`; `./scripts/repoctl graph query --help`; provider availability commands; targeted context benchmark tests.
- Files/artifacts inspected: `pyproject.toml`, `tools/repoctl/context_retrieval.py`, `tools/repoctl/context_graph.py`, `tools/repoctl/context_benchmark.py`, `tests/fixtures/context-benchmark/questions.jsonl`, `tests/fixtures/context-benchmark/expected-sources.json`.
- Metric values: system binaries `tree-sitter=NOT_FOUND`, `scip=NOT_FOUND`; disposable `uv --with` resolved parser/LSP packages; targeted benchmark tests `2 passed, 18 deselected`.
- Example output excerpt: `2 passed, 18 deselected in 1.42s`.

### Interpretation
- What this means for the intended product loop: existing repoctl benchmark artifacts are enough to compare bounded candidates without production changes.
- What it does not prove: a production S4 implementation exists.
- Risk or uncertainty: repo-map ranking remains a temp scoring idea, not a shipped repoctl feature.

### Recommendation
RETEST

### Follow-up needed
Completed in this report by running the temp S4 scorer under `.repoctl-state/technology-benchmark`.

Decision needed: Graph-to-wiki v1 technology stack
Measured winner: S2/S5 current architecture
Why: S2 beats S0/S3/S4 on required recall, precision, and provenance; S5 proves reviewed record reuse and stale exclusion.
Tradeoffs: S3/S4 may be useful later for real language-provider gaps, but current measured benefit does not justify default adoption.
Rejected: S0 as product path; S3/S4 as default stack; S6 until S4 exposes a measured gap.
Recommendation: KEEP current S2/S5 architecture
Human options:
A. Approve recommendation
B. Reject and revise toward S3/S4 hybrid despite lower bounded-probe accuracy
C. Request one bounded retest: live-agent S0 vs S2/S5 task replay on a real product repo
Blocked until decision: production integration / next product feature work
Unaffected: read-only review of this report

KEEP current S2/S5 architecture
