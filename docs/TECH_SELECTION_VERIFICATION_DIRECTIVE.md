---
title: Graph · Task Brief · Reviewed Knowledge · llmwiki Technology Choice Verification Directive
status: authoritative-verification-directive
scope:
  - confirm whether the current Graph-to-llmwiki V1 is the intended product
  - determine whether the current technology choices are best enough to keep
  - produce a report that a human reviewer can evaluate quickly
excluded:
  - MCP implementation
  - new production feature development before technology decision
  - benchmark framework expansion for its own sake
canonical_repo_path: docs/TECH_SELECTION_VERIFICATION_DIRECTIVE.md
primary_output: docs/TECHNOLOGY_BENCHMARK_REPORT.md
secondary_output_if_needed: docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
---

# Graph · Task Brief · Reviewed Knowledge · llmwiki Technology Choice Verification Directive

## 0. Status of this file

This directive is a **technology-choice verification instruction**, not a new product implementation plan.

It sits above the product Master Plan until the technology decision is closed:

```text
docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md
→ this verification directive
→ docs/TECHNOLOGY_BENCHMARK_REPORT.md
→ human technology decision
→ keep or rewrite docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
```

Do not continue Graph, Context, Knowledge, or llmwiki feature development while this verification is open. The current product loop may be field-verified, but that does **not** prove the chosen architecture is best enough to keep.

The job is to answer, with evidence:

```text
1. Did we actually build the product loop we intended?
2. Is the current architecture the best choice among realistic alternatives?
3. If not, exactly what should be revised before more product work continues?
```

---

# 1. Model routing requirement

Use code-exploration models for exploration, but force them to produce report-grade findings.

## 1.1 Priority

For repository/code/technology exploration, prefer the models in this order when the execution environment supports model selection:

```text
1. gpt-5.3-codex-spark   # highest priority for exploration
2. gpt-5.4-mini          # fallback / secondary explorer
```

If explicit model selection is unavailable, continue with the available agent, but record this in the report:

```text
Model routing note: requested gpt-5.3-codex-spark first, gpt-5.4-mini fallback; actual available model was <model or unknown>.
```

## 1.2 What the exploration agents must and must not do

Exploration agents may inspect code, run CLI commands, run scratch experiments, and compare tools.

Exploration agents must not be used merely as code specialists that dump raw findings. Every exploration output must be written in the structured finding format in Section 9.

The final report must be written for human product/architecture review, not for the exploring agent itself.

---

# 2. Non-negotiables

## 2.1 No production development during verification

Until the report and human technology decision are complete:

- Do not add new product features.
- Do not refactor production Graph/Context/Knowledge/wiki code.
- Do not add new production dependencies.
- Do not migrate storage, renderer, retrieval, or provider architecture.
- Do not add MCP.
- Do not declare the current design “best”.

Temporary experiments are allowed only under disposable locations:

```text
/tmp/...
.repoctl-state/technology-benchmark/...
```

Temporary adapters, downloaded indexes, generated maps, raw logs, and tool outputs must not become production files.

## 2.2 No fake benchmark closure

Do not claim completion if any of these happened:

- Only one system was tested.
- Only toy fixtures were tested.
- Recall was measured but precision/cost/task outcome were ignored.
- External tools were described from memory without being tried or clearly marked as untested.
- Raw logs were pasted instead of summarized.
- The report lacks a KEEP/REVISE recommendation.
- The report lacks a human decision request.

## 2.3 No micro commits

During benchmark/exploration, do not commit each tool install, note, fixture, or script.

Commit only after the report is coherent and reviewable. Recommended commit:

```text
docs(product): verify graph-to-wiki technology choice
```

If the human decision is REVISE, the Master Plan rewrite may be included in the same decision commit or the next single docs commit, but do not start production integration yet.

---

# 3. Inputs to read first

Read these before running experiments:

```text
AGENTS.md
docs/PRD.md
docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md
docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
docs/field-tests/graph-context-llmwiki-v1.md
docs/contracts/repoctl-graph-contract.md
docs/contracts/repoctl-context-contract.md
docs/adr/evidence-context-authority-v0.md
README.md
docs/README.md
```

If some files are missing in the current checkout, record that as evidence. Do not invent the missing state.

---

# 4. The product truth to verify

We are not building a graph viewer, benchmark harness, Markdown dumper, or autonomous agent runtime.

The intended product is this loop:

```text
Task request
→ code localization and impact evidence
→ Task Brief / Context Pack
→ agent edits code with less blind exploration
→ tests and completion receipt
→ reviewed knowledge candidate
→ explicit human approval
→ next Task Brief reuse
→ static llmwiki navigation
```

The verification must answer whether the current implementation achieves that loop **better than a baseline agent using rg/read**, and whether the internal architecture is the best practical way to keep developing it.

---

# 5. Systems to compare

Use the existing benchmark map labels. At minimum compare S0, S2, S3, and S4. Include S5 for knowledge-sensitive tasks. Include S6 only if S4 repeatedly misses labeled required context.

```text
S0  Agent + rg/read only
S1  Aider-style repo map baseline
S2  Current repoctl Graph + Context Pack
S3  SCIP + lexical retrieval prototype
S4  Hybrid: exact/SCIP graph + FTS/BM25 + repo-map ranking
S5  Hybrid + Reviewed Knowledge
S6  Optional embeddings/rerank only after measured S4 gap
```

If a system cannot be run, do not silently drop it. Mark it:

```text
NOT_RUN: <reason>
Impact on conclusion: <low | medium | high>
```

A NOT_RUN system with high impact prevents a strong “best architecture” claim.

---

# 6. Technology areas to benchmark

## 6.1 Code intelligence provider

Compare practical fit for:

```text
current repoctl custom Python AST/import resolver
tree-sitter fallback
SCIP Python / TypeScript / JavaScript where installable
optional LSP oracle when available
CodeQL or Joern only as special-analysis references, not default task context
```

Measure:

```text
definition precision/recall
reference/caller precision/recall
cross-file import resolution
method/alias behavior
unsupported-language honesty
index time
incremental refresh time
index size
setup friction
failure modes
```

## 6.2 Retrieval / Task Brief

Compare:

```text
rg/read baseline
Aider-style repo map or repo-map approximation
current repoctl context pack
exact graph expansion + FTS/BM25
exact graph expansion + FTS/BM25 + repo-map ranking
optional semantic retrieval/rerank only after measured need
```

Measure:

```text
required file recall@K
required block/line recall@K
precision@K
false impact count
first correct edit surface rank
token/line budget efficiency
human answerability
time to first useful context
task success / test success
```

## 6.3 Reviewed Knowledge

Compare:

```text
plain ADR/Markdown notes only
current reviewed knowledge lifecycle
lighter revised lifecycle if current one is too heavy
```

Measure:

```text
approval clarity
source provenance
stale exclusion
supersede/deprecate correctness
next task reuse
human review effort
implementation complexity
```

## 6.4 llmwiki rendering

Compare:

```text
current custom Markdown output
MkDocs-compatible generated content
other existing docs tooling only if easy to run in this environment
```

Measure:

```text
can a human answer the five required wiki questions?
click/navigation depth
source and lifecycle visibility
broken link/freshness check
custom code that can be deleted or avoided
setup/build cost
compatibility with GitHub/IDE Markdown
```

---

# 7. Corpus and task requirements

Use at least four repository shapes:

```text
R1  small Python repository
R2  medium Python repository with aliases/classes/cross-file calls
R3  medium TypeScript/JavaScript repository with relative/package imports
R4  configured multi-repo pair with same basenames or symbol names
```

Use at least twelve tasks:

```text
4  definition/reference/caller/impact tasks
3  bug or behavior-change tasks requiring tests
2  import/dependency tasks, including JS/TS
2  knowledge-sensitive tasks with decision/invariant/failure mode
1  multi-repo ambiguity/isolation task
```

For every task, record a gold context before judging systems:

```text
required files
required symbols/functions/classes
required line ranges or blocks
required tests/verification commands
required docs/ADR/contract/knowledge records
forbidden misleading files
expected safe output behavior
```

The gold context may be created by manual inspection, but it must be written down before system scoring. Do not move the goalposts after seeing a system output.

---

# 8. Experimental rules

Use the same repository snapshot, task text, and budget for all systems.

Minimum budget fields:

```text
max files shown
max source lines shown
max total tokens or chars
max exploration commands
max wall-clock per task
allowed setup time
```

For each system/task run, capture only essential raw artifacts under:

```text
.repoctl-state/technology-benchmark/<system>/<task-id>/
```

Do not paste raw JSON or full logs into the final report. Summarize and link artifact paths.

---

# 9. Required structured finding format for exploration agents

Every gpt-5.3-codex-spark or gpt-5.4-mini exploration result must use this shape:

```md
## Explorer Finding

- Explorer model requested: gpt-5.3-codex-spark | gpt-5.4-mini
- Actual model used: <model or unknown>
- Area: <provider | retrieval | knowledge | wiki | current product audit | end-to-end>
- Repository/task set: <ids>
- Systems compared: <S0/S1/S2/S3/S4/S5/S6>

### Claim
<one concrete claim>

### Evidence
- Commands run: `<essential commands only>`
- Files/artifacts inspected: `<paths>`
- Metric values: `<table or bullets>`
- Example output excerpt: `<short excerpt only>`

### Interpretation
- What this means for the intended product loop:
- What it does not prove:
- Risk or uncertainty:

### Recommendation
KEEP | REVISE | RETEST | DROP

### Follow-up needed
<none or one concrete item>
```

If an exploration output does not follow this structure, rewrite it into this structure before using it in the final report.

---

# 10. Final report requirement

Write the final report to:

```text
docs/TECHNOLOGY_BENCHMARK_REPORT.md
```

The report must let a reviewer understand the answer in under 10 minutes.

## 10.1 First line

The report must start with exactly one of:

```text
TECH_DECISION_READY
BLOCKED_REPORT_INCOMPLETE
```

Use `TECH_DECISION_READY` only when the report contains enough evidence for a human KEEP/REVISE decision.

Use `BLOCKED_REPORT_INCOMPLETE` when an important system could not be run or a key metric is missing. In that case, the report must list the exact blocker and the narrowest next step.

## 10.2 Required report sections

Use this exact outline:

```md
TECH_DECISION_READY

# Technology Benchmark Report: Graph → Task Brief → Reviewed Knowledge → llmwiki

## 1. Executive verdict
- Intended product loop actually achieved?: YES | PARTIAL | NO
- Current architecture best enough to keep?: KEEP | REVISE | INCONCLUSIVE
- Recommended stack:
- One-paragraph reason:
- Human decision needed:

## 2. What we were trying to build
- Product loop:
- Non-goals:
- Success criteria:

## 3. Current implementation audit
| Capability | Current behavior | Evidence command/path | Useful? | Gap |

## 4. Benchmark design
| Repository | Shape | Language | Tasks | Why included |
| Task | Category | Gold files/symbols/docs/tests | Forbidden misleading context |
| Budget | Value | Reason |

## 5. System comparison scorecard
| System | Context accuracy | Task success | Cost | Setup friction | Explainability | Product fit | Verdict |

## 6. Provider bake-off
| Provider | Accuracy | Install/index cost | Failure behavior | Keep/drop reason |

## 7. Retrieval / Task Brief bake-off
| System | Required recall | Precision | First correct edit surface | Token/line cost | Human answerability |

## 8. Reviewed Knowledge bake-off
| Option | Approval UX | Stale safety | Next-task reuse | Complexity | Verdict |

## 9. llmwiki renderer bake-off
| Option | Navigation | Freshness/link check | Custom code burden | Verdict |

## 10. End-to-end replay
Task request → localization → brief → edit/test → receipt → knowledge → next brief → wiki.
- Scenario A:
- Scenario B:
- Scenario C:

## 11. Decision
Decision needed: Graph-to-wiki v1 technology stack
Measured winner: <stack>
Why: <accuracy, task outcome, operations, complexity>
Tradeoffs: <known limits>
Rejected: <alternatives and measured reasons>
Recommendation: KEEP current plan | REVISE master plan

## 12. Required Master Plan changes
| Current plan item | Keep/change/remove | Reason | Replacement text or action |

## 13. Known limitations
| Limitation | Impact | Current fallback | Reason deferred | Revisit trigger | Severity |

## 14. Evidence index
| Artifact | What it proves | Path |

## 15. Appendix: explorer findings
<concise structured findings; no raw log dump>
```

Do not invent sections with vague names like “future improvements” or “misc notes”.

---

# 11. What the reviewer must be able to answer from the report

The report must answer these questions directly:

```text
1. Did the current product loop really become Graph → Task Brief/Pack → Work → Reviewed Knowledge → llmwiki → next Pack?
2. Did it beat or at least justify itself against plain agent + rg/read?
3. Is current custom Python AST enough, or should SCIP become primary provider?
4. Is tree-sitter useful as fallback, and where exactly?
5. Is SQLite FTS/BM25 needed now, or can current lexical retrieval remain?
6. Should Evidence Context and Agent Context Pack remain separate public layers, or collapse into one Task Brief product?
7. Is current Reviewed Knowledge lifecycle the right complexity level?
8. Should llmwiki stay custom Markdown, become MkDocs-compatible content, or use another docs renderer?
9. Are embeddings/rerankers actually needed now?
10. What exact Master Plan rewrite is required before more product work?
```

If any answer is unknown, the report must say `UNKNOWN` and explain the missing evidence.

---

# 12. Scoring rubric

Use a 0–5 score for each dimension. Do not use inflated scores without examples.

```text
5 = clearly best, measured evidence, low risk
4 = strong, minor known limitation
3 = usable, tradeoffs matter
2 = works only in narrow cases or too costly
1 = mostly unsuitable
0 = not run or failed completely
```

Dimensions:

```text
accuracy
task success impact
cost / latency / token budget
setup and operational burden
explainability / provenance
stale and lifecycle safety
implementation complexity
compatibility with current repoctl contracts
```

A system cannot be called the winner if:

```text
accuracy < 4
or task success impact < 3
or setup burden <= 2 and no fallback exists
or provenance/explainability < 4 for knowledge-bearing outputs
```

---

# 13. Decision rules

## 13.1 KEEP current plan

Recommend KEEP only if:

```text
current repoctl S2/S5 is within 10% of the best measured system on required context recall
and has equal or better provenance/explainability
and does not require major rewrite to support the next product loop
and alternatives add more operational burden than measured benefit
```

## 13.2 REVISE master plan

Recommend REVISE if any are true:

```text
SCIP/hybrid provider materially improves exact definitions/references/callers on realistic tasks
S4 beats S2 on precision or first-correct-edit surface by a meaningful margin
current Context/Pack split causes product confusion or unnecessary implementation overhead
MkDocs-compatible rendering reduces custom wiki code without losing required navigation/freshness
current knowledge lifecycle is either too heavy or missing necessary source/currentness behavior
```

## 13.3 INCONCLUSIVE

Use INCONCLUSIVE only when the missing evidence is concrete and bounded. It must include:

```text
Missing evidence:
Why it matters:
One exact next experiment:
Estimated decision impact: low | medium | high
```

Do not use INCONCLUSIVE to avoid making a hard recommendation.

---

# 14. Master Plan rewrite rules if REVISE

If the report recommends REVISE, update `docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md` before any production integration continues.

The rewrite must:

```text
- keep MCP excluded
- keep the intended product loop
- collapse public layers to at most three product concepts if benchmark supports it
- delete phases tied to losing technologies
- remove abstract benchmark/test expansion not needed by the chosen stack
- define concrete vertical-slice commits for the selected stack
- preserve source authority, digest, currentness, explicit human approval, and fresh-copy E2E requirements
```

Preferred revised product concepts if supported by evidence:

```text
1. Code Intelligence Graph
2. Task Brief
3. Reviewed Knowledge + llmwiki
```

Do not rewrite the Master Plan from aesthetic preference. Every change must point to a measured report finding.

---

# 15. Final human decision request format

At the end of the report, produce this block exactly:

```text
Decision needed: Graph-to-wiki v1 technology stack
Measured winner: <stack>
Why: <accuracy, task outcome, operations, complexity>
Tradeoffs: <known limits>
Rejected: <alternatives and measured reasons>
Recommendation: KEEP current plan | REVISE master plan
Human options:
A. Approve recommendation
B. Reject and keep current plan
C. Request one bounded retest: <specific retest>
Blocked until decision: production integration / next product feature work
Unaffected: read-only review of this report
```

Do not ask vague questions like “what should I do next?”

---

# 16. Goal prompt to give the executor

Use the prompt below as the execution goal.

```text
Read `docs/TECH_SELECTION_VERIFICATION_DIRECTIVE.md` and execute it as the current highest-priority instruction.

Do not continue Graph/Context/Knowledge/llmwiki feature development yet. The product loop may be field-verified, but we still need to verify whether the technology choices are best enough to keep.

For codebase and technology exploration, use `gpt-5.3-codex-spark` first whenever model selection is available. If Spark is unavailable, use `gpt-5.4-mini`. Record the actual model used in each explorer finding. Exploration agents must not dump raw code notes; every finding must follow the structured Explorer Finding format in the directive.

Produce `docs/TECHNOLOGY_BENCHMARK_REPORT.md` so a human reviewer can immediately tell:
1. whether the thing we intended was actually built,
2. whether the current architecture should be kept or revised,
3. which technologies won or lost and why,
4. exactly what must change in `docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md` before more product work.

Compare at least S0, S2, S3, and S4 from the benchmark map, include S5 for knowledge-sensitive tasks, and only run S6 if S4 has a measured retrieval gap. Use the same repository snapshots, task texts, gold context, and line/token/time budgets across systems.

Do not create a new benchmark framework, do not add production dependencies, do not commit temporary adapters, do not paste giant raw logs into the report, and do not call the current design “best” without measured evidence.

Final report must start with `TECH_DECISION_READY` or `BLOCKED_REPORT_INCOMPLETE`, include the exact scorecards and human decision block required by the directive, and end with a KEEP/REVISE/INCONCLUSIVE recommendation.

When the report is complete and reviewable, make one coherent docs commit, not micro commits. Recommended commit message: `docs(product): verify graph-to-wiki technology choice`.
```
