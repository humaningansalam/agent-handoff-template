---
name: maintenance-workflow
description: Repo 수준 maintenance harness 전용 `/maintenance-workflow` 직접 호출 진입점.
argument-hint: "[focus]"
disable-model-invocation: true
allowed-tools:
  - Agent(maintenance-cartographer)
  - Agent(maintenance-planner)
  - Agent(maintenance-plan-critic)
  - Agent(maintenance-implementer)
  - Agent(maintenance-evaluator)
  - Agent(maintenance-skeptic)
  - Read
  - Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)
  - Bash(uv run pytest *)
disallowed-tools:
  - Skill
  - TaskCreate
  - TaskUpdate
  - TaskList
---

# /maintenance-workflow
## 범위
이 workflow는 repo 수준 유지보수에만 사용한다. 대상은 문서, rules, skills, agents, hooks, tools, tests, templates, harness contract다.

`repo/**`, product-repo runtime 작업, 생성된 산출물, secret, deployment, database, MCP/Notion write, live external mutation은 범위 밖이다.

## 오케스트레이션
Claude Code 대화형 native-loop에서 실행한다. 중단되면 같은 세션을 `/r`로 resume하고, 중간 phase에서 새로 시작하거나 수동으로 건너뛰지 않는다.

상위 Claude Code loop가 phase agent를 호출한다. Python 도구는 deterministic checker/gate/state helper일 뿐이며 agent loop를 직접 구동하지 않는다.

phase helper CLI를 workflow driver처럼 직접 호출하지 않는다. phase 전환과 pass eligibility는 hook/checker가 structured evidence와 metadata를 보고 갱신한다. `current-run-state.json`의 `pass_eligibility.calculated.eligible`가 `true`이면 추가 evaluate/decide 명령 없이 최종 첫 줄로 `pass`를 출력한다.

다른 Skill은 로드하지 않는다. 검증/디버깅/리뷰 루틴도 이 workflow의 phase agent, evidence artifact, hook/checker state 안에서만 처리한다.

## Phase 순서
1. Intake 및 bounded authority read
2. Checker policy가 affected surface를 semantic surface class로 분류하고 route를 계산한다. Ambiguous route나 `CRITICAL_HARNESS`는 `maintenance-cartographer`부터, clear route는 `maintenance-planner`부터 시작한다.
3. `maintenance-planner`가 affected surfaces와 AC ids를 safe writer metadata로 기록
4. Checker가 policy-derived profile/route/required evidence를 state에 반영
5. `TINY_DOC`: `awaiting-human-approval` -> approval freeze -> `maintenance-implementer` -> top-level host verification + `--kind execution-review --verification-passed true|false` -> checker-gated decision
6. `STANDARD`: `maintenance-plan-critic` -> `awaiting-human-approval` -> approval freeze -> `maintenance-implementer` -> `maintenance-evaluator` -> checker-gated decision
7. `CRITICAL_HARNESS`: `STANDARD` 흐름 뒤 기본은 `maintenance-skeptic`까지 완료한다. 단일 surface/P2-P3의 명시적 typo/token 교체처럼 기계적으로 검증 가능한 계획은 `--verification-mode mechanical`로 plan metadata에 고정하고 checker route가 skeptic을 요구하지 않을 때 evaluator evidence 뒤 바로 checker-gated decision으로 간다.

## Evidence Artifact
각 worker 이후 `ops/agent-harness/evidence/*.json` 아래에 대응 evidence artifact를 즉시 남긴다. 아직 생성되지 않은 evidence artifact를 먼저 읽지 않는다. Evidence artifact는 safe writer로만 기록한다. `Write/Edit/MultiEdit`로 `ops/agent-harness/**`를 직접 쓰지 않는다.

다음 worker를 호출하기 전에 방금 완료된 worker의 safe writer JSON evidence를 반드시 먼저 기록한다. Checker가 요구하지 않는 profile 단계는 호출하지 않는다. `maintenance-evaluator`를 호출하기 전에는 `--kind execution`, `maintenance-skeptic`을 호출하기 전에는 `--kind execution-review` artifact가 존재해야 한다.

`uv run python -m tools.agent_harness.safe_artifact_writer write --kind <cartography|plan|plan-review|execution|execution-review|skeptic-review> --workflow-id <active workflow_id> --candidate-id <id-if-required> --active-candidate-id <id> --queued-candidate-id <id> --queue-policy <auto-continuation|human-decision> --status <passed|failed> --summary "short factual evidence" --blocking-finding "optional blocker"`
Evidence command prefix is exact: use `uv run python -m tools.agent_harness.safe_artifact_writer write ...` only. Do not use direct `python`, script paths like `tools/agent_harness/safe_artifact_writer.py`, `PYTHONPATH=...`, `rg`, or `git` as evidence persistence commands; mechanical content checks use `Read`, and Bash is limited to safe writer or `uv run pytest *`.

검증/리뷰 worker는 필요한 경우 finding matrix flags를 반복 전달한다: `--finding-id`, `--finding-surface`, `--finding-expected`, `--finding-observed`, `--finding-verdict <pass|fail|warn>`, `--finding-severity <P0|P1|P2|P3>`, `--retry-target <retry-plan|retry-implementation|retry-evaluation>`, `--checked-command`, `--checked-surface`, `--evidence-ref`. Finding row는 최소 `--finding-id`와 `--finding-verdict`가 필요하다.

`--workflow-id`는 `ops/agent-harness/current-run-state.json` 또는 active session marker의 `workflow_id`와 같아야 한다. 임의 사람이 만든 id를 쓰지 않는다. `--kind plan`은 승인 freeze용 structured metadata를 함께 써야 하므로 `--affected-surface <path>`와 `--acceptance-criteria-id <AC-id>`를 하나 이상 반복 전달한다. 단일 surface/P2-P3 기계적 typo/token 교체는 `--verification-mode mechanical`을 함께 전달하고, 의미 판단·여러 파일·P0/P1·권한/코드 semantics 변경은 기본 `semantic`을 유지한다.

`--kind plan-review`는 phase gate metadata를 함께 써야 하므로 `--approval-ready true|false`를 전달한다. Plan/FML/approval readiness를 artifact 본문에서 추정하지 않는다.

`schema_version`, `worker`, `evidence_kind`는 safe writer가 `--kind`에서 자동 생성한다. Agent는 JSON literal을 만들지 않는다. `$(cat <<EOF ...)`, heredoc, pipe, redirect, command substitution, stdin은 절대 사용하지 않는다. worker 상세 output은 transcript에 남긴다.

Evidence paths: `evidence/cartography.json`, `evidence/plan.json`, `evidence/plan-review.json`, `evidence/execution.json`, `evidence/execution-review.json`, `evidence/skeptic-review.json`.

`current-run-state.json`은 직접 쓰지 않는다. hook/checker가 JSON evidence와 metadata에서 state와 run-scoped canonical artifact를 생성한다. heredoc, redirection, `tee`, 임의 Python snippet, phase helper command는 artifact persistence에 쓰지 않는다.

## Gate 규칙
- Cartography를 실행한 경우 reviewable cartography 전에는 active candidate를 정하지 않는다.
- Cartography queue가 있으면 user-facing 승인 대기 메시지에 후보군 요약, active candidate, queued/deferred candidates, queue_policy를 명시한다.
- queued candidate가 없으면 user-facing 출력에서 queue policy를 `none` 또는 terminal로 표시한다. `auto-continuation`/`human-decision`은 실제 queued candidate가 있을 때만 표시한다.
- `queue_policy: auto-continuation`은 같은 문제 shard라 pass 후 다음 plan으로 내부 진행할 수 있고, `human-decision`은 별도 추천이라 남은 후보를 보여주고 닫는다.
- Checker policy는 plan/approval의 affected surfaces, surface class, severity, ambiguity를 보고 `TINY_DOC`, `STANDARD`, `CRITICAL_HARNESS`와 route를 결정한다. `TINY_DOC`면 plan-review/evaluator/skeptic agent를 호출하지 않고 승인/구현/top-level host verification으로 전이한다.
- `STANDARD`/`CRITICAL_HARNESS`에서는 plan review safe writer가 `--approval-ready true` metadata를 기록하기 전에는 `awaiting-human-approval`을 반환하지 않는다.
- Plan review가 `--approval-ready false`로 기록되면 즉시 `maintenance-planner`를 다시 호출한다. planner가 완료되기 전에는 `--kind plan`을 다시 쓰지 않는다. 그 다음에만 revised plan을 safe writer로 기록하고 `maintenance-plan-critic`을 다시 호출한다.
- Reviewed plan을 사용자에게 보여주고 다음 turn에서 `승인: <candidate_id> <plan_contract_hash_prefix>` exact phrase를 받기 전에는 구현하지 않는다. Approval phrase는 `--kind plan` safe writer CLI 출력의 `approval_phrase` 또는 `ops/agent-harness/latest-plan-metadata.json`의 `plan_contract_hash[:12]`에서만 가져온다. Artifact `sha256`, `plan_sha256`, 8-character hash, 직접 계산한 hash를 사용자 승인 문구로 쓰면 안 된다.
- Approval freeze는 plan contract hash, affected surfaces, AC ids를 고정한다. scope, AC identity, surface class, profile, route, permission semantics가 바뀌면 재승인이 필요하다.
- Approval freeze는 승인 전 dirty worktree baseline도 기록한다. 승인 전부터 더러웠던 파일은 residual risk로 보고, 승인 후 새로 더러워진 approved surface 밖 파일은 hard blocker다.
- 구현 edit은 approved affected surfaces 안에만 허용된다.
- Checker `route_cursor.next_required_worker`가 있으면 continuation은 그 worker만 호출한다. 이미 완료된 evaluator/skeptic/implementer를 다시 호출하지 않는다.
- `maintenance-implementer`가 tool/edit/wall-clock budget을 초과하면 같은 implementer를 다시 호출하지 말고 `--kind execution --status failed --retry-target retry-implementation` evidence를 남기거나 structured evidence로 표현할 수 없으면 `needs-human-decision`으로 닫는다.
- `CRITICAL_HARNESS` affected surface가 4개 이상이면 cartography가 active candidate와 queued shard candidates를 먼저 기록해야 한다. 넓은 critical 변경을 한 candidate 구현 sandbox로 밀어넣지 않는다.
- Metadata-only blocker는 `retry-approval-metadata`, `retry-artifact-metadata`, `retry-scope-ledger`, `retry-verification-metadata`처럼 typed retry로 좁힌다.
- Agent output은 evidence view일 뿐 final decision이나 gate input이 아니다. Gate input은 structured metadata/state다.
- `pass`는 checker-calculated eligibility와 profile별 필수 evidence, blocking finding 없음이 모두 필요하다.
- Skeptic 이후 `pass_eligibility.calculated.blocked_by`가 `tests_not_passed`만 남으면 checker agent를 찾지 말고 허용된 targeted verification을 실행한 뒤 execution-review safe writer에 `--verification-passed true|false` metadata를 기록한다.
- Checker가 이미 pass-eligible이면 추가 phase helper/evaluate/decide 시도를 하지 않는다.
- Active candidate가 pass된 뒤 남은 queued candidate는 `auto-continuation`일 때만 자동 plan phase에 진입한다. `human-decision`이면 pass report에서 남은 후보를 표시하고 닫는다.

## 사용자 표시 Decision
최종 사용자-facing status는 `awaiting-human-approval`, `pass`, `needs-human-decision`, `stop`, `fail`만 사용한다.
