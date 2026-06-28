# Maintenance Harness 계약

`/maintenance-workflow`의 공개 구조 계약이다. 중요한 동작은 hook, deterministic Python checker, generated state, behavior test가 강제한다.

## 소유 책임
- Skill: entrypoint, scope, phase order, phase agent 목록, user-facing decision taxonomy.
- Phase agents: 제한된 structured worker evidence만 생성한다. Human-readable output은 view일 뿐 gate input이 아니다.
- Hooks: scope guard, artifact-write guard, trace capture, approval-turn freeze, final-report gate.
- Checker/gate code: state schema, artifact lineage, approval hash, changed-file scope, pass eligibility.
- State: compact checkpoint와 artifact pointer, structured evidence validity만 보관한다. worker body, raw transcript, hidden reasoning은 보관하지 않는다.


## Architecture Map

| Plane | Owner | Responsibility |
|---|---|---|
| Control | `tools/agent_harness/policy.py`, `tools/agent_harness/checker.py`, `tools/agent_harness/harness.py` | surface class, route, required workers/artifacts, phase, retry, and pass inputs are derived from structured evidence and approval metadata. |
| Execution | `.claude/agents/maintenance-*.md` | phase workers produce recommendations and structured evidence only; they do not own final decisions. |
| State | `ops/agent-harness/current-run-state.json`, `tools/agent_harness/checker.py` | state is a checker-generated derived cache. |
| Artifact | `tools/agent_harness/paths.py`, `tools/agent_harness/safe_artifact_writer.py` | JSON evidence lives under `ops/agent-harness/evidence/*.json` with run-scoped canonical copies. |
| Safety | `tools/hooks/maintenance/enforce_scope.py`, `tools/hooks/maintenance/enforce_final_report.py`, `tools/hooks/maintenance/prompt_approval.py` | scope, approval freeze, safe writer use, and final report claims fail closed. |
| Observability | `tools/hooks/maintenance/trace.py` | events and `views/trace.md` are human-readable observation views, not gate inputs. |
| Adapter | `.agents/skills/maintenance-workflow/SKILL.md`, `.claude/skills/maintenance-workflow/SKILL.md`, `.claude/agents/maintenance-*.md` | adapters are thin entrypoints that point back to this contract for phase/profile/hook/evidence rules. |

## Surface Class Matrix

| Surface class | Examples | Profile |
|---|---|---|
| `LOW_RISK_PROSE` | README typo, non-contract docs prose, template prose | `TINY_DOC` |
| `INSTRUCTION_DOC` | `CLAUDE.md`, `.claude/rules/**`, non-maintenance `.claude/skills/**`, non-maintenance `.claude/agents/**` | `STANDARD` |
| `CONTRACT_DOC` | `docs/PRD.md`, `docs/OPERATIONS_CONTRACT.md` | `STANDARD` |
| `CRITICAL_HARNESS_DOC` | `docs/MAINTENANCE_HARNESS_CONTRACT.md`, `.claude/skills/maintenance-workflow/**`, `.claude/agents/maintenance-*.md` | `CRITICAL_HARNESS` |
| `HOOK_OR_PERMISSION` | `.claude/hooks/**`, `.claude/settings.json`, `tools/hooks/maintenance/**`, `tools/runtime/permission_policy.py` | `CRITICAL_HARNESS` |
| `HARNESS_CODE` | `tools/agent_harness/**`, `tools/registries/agent_registry.py` | `CRITICAL_HARNESS` |
| `HARNESS_TEST` | `tests/maintenance/**` | `CRITICAL_HARNESS` |

## Route Matrix

| Profile | Ambiguity | Route | Host verifier |
|---|---|---|---|
| `TINY_DOC` | clear | planner -> approval -> implementer -> host-verifier | allowed |
| `STANDARD` | clear | planner -> critic -> approval -> implementer -> evaluator | denied |
| `STANDARD` | ambiguous | cartographer -> planner -> critic -> approval -> implementer -> evaluator | denied |
| `CRITICAL_HARNESS` | any | cartographer -> planner -> critic -> approval -> implementer -> evaluator -> skeptic | denied |

Checker code, not an agent, classifies surface class/profile and derives the route from affected surfaces, severity, and ambiguity. Agents may recommend risk, but cannot self-skip required gates or self-declare pass eligibility.

## Hook Manifest

| Event | Hook owner | Input | Output | Failure behavior |
|---|---|---|---|---|
| `UserPromptExpansion` | `.claude/hooks/run_prompt_context.sh` | user prompt/session | bounded context | no permission grant |
| `UserPromptSubmit` | `.claude/hooks/run_prompt_context.sh` | user prompt/session | bounded context | no permission grant |
| `UserPromptSubmit` | `tools/hooks/maintenance/mark_active.py`, `tools/hooks/maintenance/prompt_approval.py` | user prompt and active marker | active run marker or approval freeze context | fail closed for unsafe approval state |
| `PreToolUse` | `tools/hooks/maintenance/enforce_scope.py` | tool request | allow/deny | deny on parse error, wrong phase, missing evidence, unapproved surface, or unsafe writer args |
| `PostToolUse` | capture hooks | completed tool event | bounded event append | observation only |
| `PermissionRequest` | `tools/hooks/maintenance/enforce_scope.py` | permission request | allow/deny | deny on unsafe maintenance tool access |
| `SubagentStart` | capture start hook | agent start payload | worker-start event | fail closed for blocked worker route |
| `SubagentStop` | capture trace hook | agent final payload | worker-end event | observation only; no evidence validity promotion |
| `Stop` | `tools/hooks/maintenance/enforce_final_report.py` | final assistant message and checker state | allow/block final response | block unsupported decision claims or false pass |

## Scope Authority
`/maintenance-workflow`는 명시적인 repo maintenance scope에서만 실행된다. Slash command execution은 collaboration mode나 maintenance scope 자체가 아니라 호출된 command 동안만 적용되는 runtime phase다. Implementation worker는 approval freeze에 포함된 approved affected surfaces만 수정한다.

허용되는 repo maintenance surface:
- root docs 및 repo contracts
- `.claude/rules/**`, `.claude/skills/**`, `.claude/agents/**`, `.claude/hooks/**`, `.claude/settings.json`
- `tools/**`, `tests/**`, `templates/**`

금지되는 surface:
- `repos/**`
- product-repo runtime 작업
- 생성된 wiki output
- secret, deployment, database, external service write, live external mutation

Scope gate는 요청된 edit가 Project collaboration인지 repo maintenance인지 분리하고, maintenance success path에서 금지 surface와 live external mutation을 제외한다.

## Phase Gate
1. Cartography는 ambiguity가 있거나 `CRITICAL_HARNESS` route일 때 candidate selection보다 먼저다.
2. Candidate selection이 draft plan보다 먼저다.
3. Plan review는 route-required prior evidence, plan, generated state를 기준으로 한다.
4. Human approval은 reviewed plan을 사용자에게 보여준 뒤 별도 user turn에서만 받는다.
5. Approval freeze는 candidate id, plan contract hash, affected-surface hash, AC identity hash, approval hash를 기록한다. Human approval은 `승인: <candidate_id> <plan_contract_hash_prefix>` exact phrase로만 인정한다. User-facing approval phrase는 safe writer `--kind plan` 출력의 `approval_phrase` 또는 `latest-plan-metadata.json.plan_contract_hash[:12]`가 정본이며 artifact `sha256`/`plan_sha256`나 8-character prefix를 쓰면 안 된다.
6. Implementation은 `approved_frozen` state가 필요하고 approved affected surfaces 안에만 머문다.
7. Checker가 profile별 필수 structured evidence를 계산한다. `TINY_DOC`는 critic/evaluator/skeptic agent를 요구하지 않고 top-level `host-verifier` evidence를 요구하며, `STANDARD`는 skeptic 단계를 요구하지 않는다. `CRITICAL_HARNESS`는 기본적으로 skeptic review를 요구한다. 단일 surface/P2-P3의 명시적 typo/token 교체처럼 기계적으로 검증 가능한 plan은 `verification_mode=mechanical`이 plan contract hash에 포함된 경우에만 skeptic 대신 evaluator의 targeted evidence로 닫을 수 있다.
8. `pass`는 checker-calculated 결과다. worker나 상위 model이 eligible state 없이 성공을 자가 선언하면 안 된다.

Approval freeze also records a pre-existing dirty worktree baseline. Files dirty before approval are residual risk, not candidate scope violations; files dirtied after approval outside approved surfaces remain hard blockers.

Checker state includes a `route_cursor` with completed workers, the next required worker, and remaining required artifacts. Continuations must execute the cursor's next worker only; route workers already completed cannot be rerun unless a typed retry target explicitly requires them.

CRITICAL implementer execution is budgeted. Non-converging implementer loops are fail-closed with structured execution evidence and `--retry-target retry-implementation`; they must not keep reading/editing indefinitely or rerun the implementer after a budget handoff.

Skeptic review is a delta review over approval freeze, plan metadata, execution/evaluation artifacts, checker state, and candidate changed files. Whole-repo search/status/find is allowed only for a concrete blocker hypothesis, not as a default reread of the repository.

Mechanical verification is not prompt parsing. It is an explicit plan metadata field, is included in the approval contract hash, and is accepted only for exactly one affected surface with P2/P3 severity. Broad edits, semantic prose changes, permission semantics, code behavior, P0/P1 severity, or multiple surfaces must stay on the default semantic route.

`CRITICAL_HARNESS` plans with four or more affected surfaces require cartography to split work into a queued shard set before any plan artifact is accepted. Broad critical work must become smaller candidates rather than one large implementer sandbox.

Retry targets may be typed (`retry-approval-metadata`, `retry-artifact-metadata`, `retry-scope-ledger`, `retry-verification-metadata`) so a metadata-only blocker does not reopen the full implementation/evaluation route.

## Instruction Authority
- `CLAUDE.md`는 bootstrap/pointer다. 긴 제품 계약이나 runtime 계약을 재정의하지 않는다.
- `docs/PRD.md`는 product purpose, scope/source model, actor boundary, instruction surface policy, product AC의 정본이다.
- `docs/OPERATIONS_CONTRACT.md`는 slash command runtime lifecycle, side-effect gates, sync status semantics, response categories, Final/Failure Report의 정본이다.
- 이 문서는 `/maintenance-workflow`의 public harness contract이며, phase gate, approval freeze, artifact/state lineage, scope containment, pass eligibility의 정본이다.
- Hook, checker, generated state, behavior test는 이 authority split을 실행 가능한 invariant로 강제한다.

## Artifact와 State
- Worker는 `ops/agent-harness/**` 아래 structured evidence와 사람이 읽는 view를 남긴다.
- Evidence artifact는 `evidence/cartography.json`, `evidence/plan.json`, `evidence/plan-review.json`, `evidence/execution.json`, `evidence/execution-review.json`, `evidence/skeptic-review.json`다.
- Human-readable trace는 `views/trace.md`다. durable lineage는 `runs/<workflow-id>/...` 아래 run-scoped canonical copy다.
- `current-run-state.json`은 checker-generated이며 worker가 직접 수정하지 않는다.
- State는 artifact path, canonical path, hash, candidate id, phase, structured worker status, approval freeze, retry target, pass eligibility를 저장한다.
- Human-readable reports are view only. route, approval, queue, candidate, pass status는 structured metadata와 checker-generated state만으로 결정한다.

## Permission 규칙
- Top-level workflow는 repo context read와 harness evidence artifact write만 수행한다.
- Top-level workflow는 generic task-management tool이나 nested skill을 사용하지 않는다.
- Bash heredoc, Python snippet, redirection, `tee`, shell filesystem mutation은 maintenance artifact persistence로 인정하지 않는다.
- Artifact persistence는 bounded writer의 argparse에 존재하는 structured flags만 허용한다. Common flags include `--kind`, `--status`, `--summary`, and `--workflow-id`; plan metadata uses `--candidate-id`, `--affected-surface`, and `--acceptance-criteria-id` to derive surface class, profile, route, reapproval triggers, and plan contract hash; review metadata uses `--approval-ready true|false` or `--verification-passed true|false`; finding matrix metadata uses `--finding-id`, `--finding-surface`, `--finding-expected`, `--finding-observed`, `--finding-verdict`, `--finding-severity`, `--retry-target`, `--checked-command`, `--checked-surface`, and `--evidence-ref`. Content payload flags, encoded content, file-path payloads, and markdown/prose literals are not evidence.
- Approval freeze 전 implementation edit은 차단되고, approved surfaces 밖 edit은 거부된다.
- Hook failure는 maintenance tool access와 final pass claim에 대해 fail closed다.

## Decision 분류
User-facing statuses:
- `awaiting-human-approval`
- `pass`
- `needs-human-decision`
- `stop`
- `fail`

Internal routes:
- `retry-plan`
- `retry-implementation`
- `retry-evaluation`
- `continue-topic`

Internal route는 user-facing decision이 아니다. 진짜 human decision이 필요하지 않으면 orchestrator가 다음 required phase를 계속 진행한다.

## Acceptance / FML Evidence
PRD의 `AC-001`-`AC-005`는 product acceptance 기준이다. 이 계약은 그 AC를 실행 가능한 gate로 검증한다.

- `AC-001` Scope authority: requested edit를 Project collaboration 또는 explicit repo maintenance scope로 분리하고, slash command execution을 별도 runtime phase로 취급하는지 확인한다.
- `AC-002` Instruction authority: `CLAUDE.md`, PRD, Operations Contract, Maintenance Harness Contract의 authority split을 유지하는지 확인한다.
- `AC-003` Maintenance harness boundary: approved affected surfaces, changed-file containment, `repos/**`와 generated output 차단, live external mutation 금지로 확인한다.
- `AC-004` Artifact/state lineage: bounded JSON evidence artifact, run-scoped canonical artifact, trace view, checker-generated state, approval freeze hash로 확인한다.
- `AC-005` Acceptance verification: profile별 필수 evidence, mandatory FML mapping/direct evidence, checker-calculated pass eligibility로 확인한다.

FML item이 있는 candidate는 plan에서 FML-to-AC mapping을 제시하고 evaluation에서 direct evidence를 연결해야 한다. Mandatory FML evidence가 비어 있으면 pass candidate가 될 수 없다.

## Pass Eligibility 조건
Pass candidate는 profile별 required artifacts가 fresh이고, mandatory worker evidence가 있고, approval freeze가 유효하고, changed files가 approved surfaces 안에 있고, 필수 evidence가 AC/FML matrix를 뒷받침하고, checker `blocked_by`가 비어 있을 때만 eligible이다.

Workflow profile은 checker가 plan/approval의 `affected_surfaces`, semantic surface class, severity, ambiguity로 결정한다. `.md`/`.txt` 확장자만으로 `TINY_DOC`가 되지 않는다. Instruction/contract/harness surfaces는 `STANDARD` 또는 `CRITICAL_HARNESS`로 승격된다. `TINY_DOC`는 plan-review/evaluator/skeptic agent를 요구하지 않고 승인/구현/top-level `host-verifier` verification으로 전이한다.
