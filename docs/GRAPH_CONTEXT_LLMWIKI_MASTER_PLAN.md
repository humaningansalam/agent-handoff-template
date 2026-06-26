---
title: Graph · Context · Reviewed Knowledge · llmwiki Product Completion Plan
status: authoritative-execution-plan
scope:
  - Graph
  - Evidence Context
  - Agent Context Pack
  - Reviewed Knowledge
  - llmwiki Render
excluded:
  - MCP
execution_mode: continuous-until-product-done
canonical_repo_path: docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
---

# Graph · Context · Reviewed Knowledge · llmwiki Product Completion Plan

## 0. 이 문서의 지위

이 파일은 MCP를 제외한 제품 개발의 **단일 실행 계약**이다.

저장소에 넣을 때의 정본 경로는 다음으로 한다.

```text
docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md
```

`docs/PRD.md`에는 이 문서를 장기 제품 목표와 완료 기준의 정본으로 가리키는 짧은 링크와 요약만 둔다. 다른 임시 계획, 채팅 요약, 테스트 수, 커밋 수, “대부분 완료”라는 표현은 이 문서의 완료 판정을 덮어쓸 수 없다.

이 계획은 기존 구현을 폐기하거나 처음부터 다시 만드는 계획이 아니다. 현재 존재하는 Graph, Context, Pack, Knowledge lifecycle, Render 구현을 재사용하고, **실제 사용자가 쓸 수 없는 빈 부분만 기능으로 완성**한다.

## 0.1 Approved technology decision

2026-06-26 human decision: **KEEP current S2/S5 architecture as the V1 default**.

The decision is based on the bounded technology benchmark in `docs/TECHNOLOGY_BENCHMARK_REPORT.md` and the restored comparison map in `docs/GRAPH_CONTEXT_LLMWIKI_TECHNOLOGY_BENCHMARK_MAP.md`. It means this plan continues under the following default stack:

- S2 repoctl custom Graph provider plus Context/Task Pack remains the default product path.
- S5 Reviewed Knowledge lifecycle remains part of V1.
- The current custom static Markdown llmwiki remains the V1 renderer.
- S3/S4/S6 are not default product work for V1.

This is not a claim of global optimality for all future repositories. The approved claim is only: **best-enough default architecture for V1 based on the bounded technology benchmark**.

Future revisit triggers:

- Revisit S3/S4 only if real tasks show measured S2 provider or retrieval gaps.
- Revisit S6 embeddings/rerank only after S4 first shows a measured retrieval gap.
- Do not add SCIP, tree-sitter, BM25, MkDocs, embedding, or reranker production work without such measured triggers and a new human decision.

---

# 1. 실행자에게 내리는 최상위 명령

다음 명령은 모든 Phase와 작업보다 우선한다.

> 이 문서를 처음부터 끝까지 실행하라. 한 Phase를 끝냈다는 이유로 작업을 종료하지 마라. 현재 Phase의 기능을 구현하고, 작업 중인 원본과 분리된 복사본에서 실제 사용자 흐름을 충분히 실행하고, 결과물을 사람이 직접 읽어 유용성을 확인한 뒤, 하나의 사용 가능한 기능 묶음으로 Git commit 하라. 그 다음 Phase로 즉시 계속하라. 구현 의미가 실제로 둘 이상이고 제품 계약을 바꾸는 경우에만 human feedback을 요청하라. 최종 Definition of Done을 모두 만족하기 전에는 “완료”라고 보고하지 마라.

실행자는 Phase 종료 시 다음 중 하나만 선택한다.

```text
CONTINUE   다음 Phase 또는 다음 vertical slice로 즉시 진행
HUMAN      제품 의미를 결정해야 해서 사람의 선택이 필요함
DONE       최종 Definition of Done 전체를 만족함
BLOCKED    외부 의존성 또는 재현 가능한 기술 차단으로 진행 불가
```

`Phase N 완료`는 세션 종료 사유가 아니다. 기본 상태는 항상 `CONTINUE`다.

---

# 2. 제품의 최종 목적

이 제품은 에이전트가 매 세션 저장소를 처음부터 다시 뒤지는 문제를 해결해야 한다.

최종 사용 흐름은 다음과 같다.

```text
실제 저장소
  → Graph가 파일·심볼·import·호출·작업 증거를 구조화
  → Evidence Context가 현재 질문/작업에 필요한 증거만 선택
  → Agent Context Pack이 읽기 순서와 변경 영향·테스트·과거 결정을 전달
  → 에이전트가 실제 코드를 변경하고 검증
  → 완료 증거에서 Knowledge Candidate 생성
  → 사람이 검토하고 승인
  → Reviewed Knowledge가 다음 Context Pack에 재사용
  → llmwiki가 사람이 탐색 가능한 장기 기억으로 렌더
```

완성 후 사용자는 최소한 다음 질문에 CLI와 생성 문서만으로 답할 수 있어야 한다.

1. 이 파일 또는 심볼은 어디에 정의되어 있는가?
2. 이것을 누가 호출하거나 import 하는가?
3. 이것을 바꾸면 어떤 파일·심볼·테스트가 영향을 받는가?
4. 이번 작업을 시작하기 전에 반드시 읽어야 할 파일과 결정은 무엇인가?
5. 이 설계는 왜 선택되었고 무엇을 깨뜨리면 안 되는가?
6. 이전에 실패한 접근은 무엇이며 현재도 유효한가?
7. 이 지식의 원본 근거는 무엇이고 지금도 최신인가?

---

# 3. 현재 기준선과 실제 미완성 지점

실행자는 아래 기준선을 먼저 인정하고, 이미 있는 기능을 같은 이름으로 다시 만들지 않는다.

| 영역 | 현재 존재하는 기능 | 제품상 남은 핵심 문제 |
|---|---|---|
| Graph build | 파일·토픽·import·task evidence·정밀 Python symbol/anchor와 `CALLS`, `RESOLVES_TO`, `IMPORTS_FILE` 계열 관계가 존재함 | 공개 `graph query`가 사실상 `--file`, `--topic`, `--import`에 머물러 symbol/caller/callee/impact를 사용자가 직접 질의하지 못함 |
| Evidence Context | source ref, digest, completeness, Graph·문서·Reviewed Knowledge retrieval과 token budget이 존재함 | 결과가 실제 작업 표면, 호출 영향, 테스트 후보, 읽기 순서로 충분히 조직되지 않았고 실제 작업 성공을 증명하지 못함 |
| Agent Context Pack | task 기반 pack, `must_read`, reviewed knowledge, benchmark가 존재함 | 에이전트가 사람이 읽을 수 있는 pack을 실제 작업 시작 전에 소비하는 제품 흐름과 실사용 성공 증거가 부족함 |
| Knowledge Candidate/Review | source·pack·receipt 기반 candidate, check, approve, reject, refresh, stale, supersede, deprecate, events가 존재함 | review UX, 적용 대상 연결, 중복/충돌 판단, 실제 작업 후 지속적으로 재사용되는 폐쇄 루프가 부족함 |
| llmwiki Render | deterministic render, manifest, `--check`, kind별 Markdown 페이지가 존재함 | per-record 탐색, 링크·backlink, 적용 대상, lifecycle·source 상태를 따라가는 실제 wiki 탐색성이 부족함 |
| Gate/Benchmark | fixture recall, source integrity, multi-repo isolation, render check가 존재함 | retrieval fixture는 실제 에이전트 작업 성공이나 생성 페이지의 실용성을 증명하지 않음 |
| Product contract | 부분 계약과 ADR이 존재함 | `docs/PRD.md`가 비어 있고, 문서상 Graph v0 제약과 실제 구현 사이 drift가 있음 |

이 표의 목적은 “아무것도 안 됐다” 또는 “전부 거의 됐다” 같은 추상적인 판정을 금지하는 것이다.

---

# 4. 절대 금지 사항

## 4.1 범위 금지

- MCP 구현, MCP server, MCP adapter, MCP transport를 만들지 않는다.
- 새로운 autonomous agent runtime을 만들지 않는다.
- Graph database로 마이그레이션하지 않는다.
- vector database, embedding pipeline, LLM reranker를 선제적으로 넣지 않는다.
- llmwiki v1을 웹 애플리케이션이나 호스팅 서비스로 확대하지 않는다.
- 현재 제품 완료와 무관한 maintenance harness, approval harness, safe-writer, hook framework를 확장하지 않는다.
- 새로운 task manager 또는 두 번째 authority store를 만들지 않는다.

## 4.2 개발 방식 금지

- 테스트만 추가한 commit을 만들지 않는다. 단, 기존 장애를 고정하는 회귀 테스트와 수정이 같은 commit에 포함되는 경우는 허용한다.
- helper 한 개, 문구 한 줄, fixture 한 개마다 commit 하지 않는다.
- 구현 전에 수십 개의 추상 테스트와 gate를 먼저 증축하지 않는다.
- 기존 release-candidate gate로 표현 가능한 검증을 위해 새 gate framework를 만들지 않는다.
- 실제 실패 사례가 없는 가상 corner case를 무한히 추가하지 않는다.
- Phase를 닫기 위한 별도 감사 문서만 계속 만들지 않는다.
- “테스트 통과”, “코드가 존재”, “대부분 구현”만으로 제품 완료를 선언하지 않는다.
- 이미 field-verified와 committed 상태인 Phase를 구체적인 회귀 증거 없이 다시 열지 않는다.
- 명명, 내부 refactor 방식, 테스트 도구 선택처럼 실행자가 판단 가능한 일을 사람에게 되묻지 않는다.

## 4.3 출력 금지

다음 표현은 최종 상태가 아니므로 단독 종료 보고로 사용하지 않는다.

```text
대부분 완료
부분 완료
기능적으로 완료에 가까움
감사가 필요함
다음에는 할 수 있음
Phase N 완료
```

---

# 5. 작업 단위와 Git commit 계약

## 5.1 Vertical slice 정의

하나의 vertical slice는 사용자가 실제 명령으로 실행할 수 있는 기능 하나를 뜻한다.

좋은 예:

```text
Graph symbol selector + ambiguity response + callers/impact output
Context Pack Markdown output + 읽기 순서 + 실제 task 사용
Knowledge review summary + explicit approval provenance
llmwiki per-record pages + index/backlinks + stale source 표시
```

나쁜 예:

```text
테스트 helper 추가
fixture 이름 변경
parser 함수 분리
문서 문구 수정
한 edge enum 추가
```

## 5.2 Commit 가능 조건

다음 조건을 모두 만족해야 commit 한다.

1. 사용자가 실행할 수 있는 기능 또는 사용자에게 직접 보이는 결함 수정이 있다.
2. production code와 필요한 contract/docs가 같은 의미로 정렬되어 있다.
3. 해당 기능의 targeted tests가 통과한다.
4. 원본 작업공간과 분리된 복사본에서 실제 CLI 흐름이 통과한다.
5. JSON exit code만 보지 않고 생성된 pack 또는 wiki 페이지를 직접 열어 내용이 유용한지 확인했다.
6. slice 내부에 known broken path, placeholder, 임시 TODO가 남지 않았다.
7. commit 하나를 revert했을 때 하나의 제품 기능 묶음이 되돌아간다.

## 5.3 Commit 크기와 수

- 기본은 **vertical slice 하나당 commit 하나**다.
- 큰 Phase는 2~4개의 vertical slice commit으로 나눌 수 있다.
- 현재 기준선에서 최종 완료까지 예상 범위는 대략 6~12개의 제품 commit이다.
- 12개를 넘길 것으로 보이면 scope drift 여부를 먼저 점검한다.
- 15개를 넘기기 전에 human feedback을 받아 범위와 commit 전략을 재확정한다.
- one-line hotfix나 독립적인 보안 수정이 아닌 이상 한두 줄짜리 micro commit을 만들지 않는다.
- `WIP`, `prep`, `tests only`, `more coverage`, `cleanup part 7` 같은 commit은 금지한다.

권장 commit message:

```text
feat(graph): expose symbol callers and impact queries
feat(context): produce actionable task context packs
feat(knowledge): add reviewable provenance and applicability
feat(llmwiki): render linked record and topic pages
fix(context): prevent stale knowledge from entering task packs
release(repoctl): close graph-to-wiki field loop
```

## 5.4 Commit 전 검증 순서

```text
구현 중: 변경 영역 targeted test
slice 완성: 관련 test module + CLI smoke
commit 직전: fresh copy 실사용 시나리오
Phase 종료: 해당 Phase의 전체 acceptance scenario
최종 release: 전체 suite + release artifact fresh-copy E2E
```

같은 코드 변경 없이 같은 전체 suite를 반복 실행하지 않는다.

---

# 6. 복사본 실사용 테스트 계약

Unit test와 fixture benchmark는 복사본 실사용 테스트를 대체하지 못한다.

## 6.1 작업 중 fresh copy 생성 예시

환경에 맞게 조정할 수 있지만, 원본 작업공간과 분리되어야 한다.

```bash
FIELD_ROOT="$(mktemp -d)"
cp -a . "$FIELD_ROOT/workspace"
rm -rf \
  "$FIELD_ROOT/workspace/.git" \
  "$FIELD_ROOT/workspace/.venv" \
  "$FIELD_ROOT/workspace/.uv-cache" \
  "$FIELD_ROOT/workspace/.repoctl-state" \
  "$FIELD_ROOT/workspace/docs/knowledge/generated"
cd "$FIELD_ROOT/workspace"
```

위 명령은 root `.git`만 제거하고 `repos/.git` 또는 `repos/<repo-id>/.git` 같은 product repository Git identity는 유지해야 한다. 환경상 유지할 수 없다면 복사본의 product repo를 새로 `git init`하고 baseline commit을 만든 뒤 실행한다.

## 6.2 복사본 테스트에서 반드시 확인할 것

- 새 process에서 CLI가 실행되는가?
- 현재 작업공간의 cache 또는 generated output에 의존하지 않는가?
- 명령 exit code와 JSON envelope가 올바른가?
- 사람이 보는 non-JSON 출력이 이해 가능한가?
- Context Pack에 실제 필요한 파일·caller·test·knowledge가 들어가는가?
- candidate → approve → query → render가 실제 파일을 만들고 다음 query에 재사용되는가?
- generated wiki link를 따라가며 근거와 lifecycle을 확인할 수 있는가?
- multi-repo에서 다른 repo의 source ref가 섞이지 않는가?
- parse error나 unsupported language가 있을 때 거짓 completeness를 주장하지 않는가?

## 6.3 단일 증거 파일

복사본 실사용 결과는 문서 수십 개로 흩뜨리지 않는다. 다음 단일 파일에 Phase별로 append 한다.

```text
docs/field-tests/graph-context-llmwiki-v1.md
```

각 실행은 아래만 기록한다.

```md
## <date> — <phase / slice>
- Copy source: <working tree | release archive>
- Repository shape: <single | multi>, <languages>
- User scenario: <one sentence>
- Commands: <essential commands only>
- Observed output: <what a user actually saw>
- Human inspection: <useful / not useful + concrete reason>
- Result: PASS | FAIL
- Commit candidate: <planned hash/message or none>
```

raw log 전체, 숨은 reasoning, 수백 줄 JSON을 문서에 붙이지 않는다. 필요한 artifact path만 남긴다.

---

# 7. 테스트 코드 증식 방지 규칙

새 테스트는 아래 셋 중 하나에 반드시 연결되어야 한다.

1. 이 문서의 명시적인 Acceptance Criterion
2. 복사본 실사용에서 실제 발생한 regression
3. stable public contract의 backward compatibility

그 외 테스트는 추가하지 않는다.

추가 규칙:

- public behavior 한 개를 여러 층에서 중복 검증하지 않는다.
- helper abstraction은 동일 패턴이 최소 3곳에 반복되고 실제 중복을 줄일 때만 만든다.
- fixture corpus는 실제 실패를 재현할 때만 확장한다.
- benchmark metric을 추가하기 전에 그 metric이 어떤 product decision을 바꾸는지 적는다.
- metric이 decision을 바꾸지 않으면 추가하지 않는다.
- 기존 field gate에 scenario 한 개를 추가할 수 있으면 새 runner를 만들지 않는다.
- 테스트 수, line coverage, fixture 수는 Phase 완료 기준이 아니다.

---

# 8. Human feedback 요청 규칙

## 8.1 반드시 물어야 하는 경우

다음 중 하나일 때만 사람에게 묻는다.

- source authority 또는 knowledge authority 경계를 바꾸려는 경우
- 기존 JSON schema 또는 CLI를 breaking change 해야 하는 경우
- 자동 knowledge approval을 도입하려는 경우
- stale/superseded/deprecated record의 보존 정책을 바꾸는 경우
- llmwiki를 static Markdown에서 web application으로 확대하려는 경우
- 지원 언어 범위를 크게 늘리며 구현 비용이 달라지는 경우
- 두 UX가 모두 합리적이고 이후 데이터 호환성에 장기 영향을 주는 경우
- final human acceptance에서 실제 usefulness가 기준에 못 미치는 경우

질문 형식:

```text
Decision needed: <one concrete decision>
Option A: <behavior and cost>
Option B: <behavior and cost>
Recommendation: <one option + reason>
Blocked work: <exact slice only>
Unaffected work continuing: <what can continue now>
```

선택을 기다리는 동안 독립적인 다음 작업이 있으면 계속 진행한다.

## 8.2 묻지 말아야 하는 경우

- 함수명, 파일 분리, 내부 class 구조
- 어떤 targeted test를 먼저 실행할지
- 기존 contract에 맞는 명백한 오류 수정
- commit에 같은 vertical slice의 docs와 tests를 포함할지
- 이미 이 문서에 정해진 Phase 순서
- “다음 Phase도 할까요?”

---

# 9. Phase 상태 모델

각 Phase는 다음 상태 중 하나만 갖는다.

```text
NOT_STARTED
IMPLEMENTING
IMPLEMENTED_UNVERIFIED
FIELD_VERIFIED
COMMITTED
BLOCKED_HUMAN
DONE
```

`DONE` 조건:

```text
구현 완료
+ targeted tests 통과
+ fresh-copy 실사용 통과
+ 사람이 출력 내용을 직접 확인
+ 의미 있는 묶음 commit 완료
+ contract/docs 정합성 완료
```

Phase별 상태는 이 문서 하단의 Progress Ledger에만 갱신한다. 작은 수정마다 상태 문서를 추가하지 않는다.

---

# 10. Phase 0 — Contract Lock and Baseline Truth

## 목적

다시는 완료 기준이 사후에 바뀌지 않도록 이 문서를 저장소 정본으로 설치하고, 현재 구현과 문서의 명백한 drift만 고정한다.

이 Phase는 장기 감사 프로젝트가 아니다. **한 개 commit을 넘기지 않는다.**

## 구현 작업

1. 이 파일을 `docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md`에 둔다.
2. `docs/PRD.md`를 짧은 제품 목적·범위·최종 흐름·이 계획 링크로 교체한다.
3. 현재 Graph가 실제로 생성하는 additive edge/capability와 기존 Graph 문서의 차이를 명시한다.
4. 기존 기능 inventory를 Progress Ledger에 기록한다.
5. 앞으로 Phase 완료 판정은 이 문서의 AC만 사용한다고 명시한다.

## 하지 않을 것

- repository 전체에 대한 새 audit framework
- Phase별 별도 completion certificate
- 새 benchmark framework
- 새 maintenance workflow
- 현재 기능 재작성

## Acceptance Criteria

- `docs/PRD.md`가 더 이상 placeholder가 아니다.
- 이 문서가 Graph, Context, Knowledge, llmwiki의 scope와 완료 기준을 명확히 정의한다.
- 현재 구현과 문서 drift 목록이 구체적인 file/contract 변경으로 연결된다.
- 다음 실행자가 30초 안에 Phase 1의 첫 기능 작업을 시작할 수 있다.

## Commit

```text
docs(product): lock graph-to-llmwiki completion contract
```

이 초기 contract commit만 docs-only 예외로 허용한다.

## 종료 행동

commit 후 보고만 하고 멈추지 않는다. 즉시 Phase 1로 진행한다.

---

# 11. Phase 1 — Graph Public Product API

## 제품 목표

내부에 이미 생성되는 Graph 관계를 사용자가 직접 질의할 수 있게 만든다.

사용자는 파일명 grep이 아니라 다음을 물을 수 있어야 한다.

```text
validate_token 정의는 어디인가?
누가 validate_token을 호출하는가?
TokenFlow.validate가 무엇을 호출하는가?
services/token_service.py를 바꾸면 어디가 영향받는가?
이 import가 어느 repo-local file로 resolve되는가?
```

## 지원 범위

### 필수

- Python precise symbol identity
- same-file function calls
- same-class method calls
- cross-file imported Python calls
- Python import resolution
- JS/TS relative import file impact
- file/topic/import 기존 selector backward compatibility
- single-repo와 configured multi-repo namespace

### 조건부

JS/TS symbol-level call graph는 실제 target repo field test에서 file-level import impact가 부족하다고 증명되고 human feedback을 받은 경우에만 추가한다.

## 공개 CLI 계약

기존 `graph query`를 유지하면서 primary selector를 확장한다.

```bash
./scripts/repoctl graph query --repo-id main --file auth/flow.py --json
./scripts/repoctl graph query --repo-id main --topic auth --json
./scripts/repoctl graph query --repo-id main --import services.token_service.issue_token --json
./scripts/repoctl graph query --repo-id main --symbol validate_token --json
./scripts/repoctl graph query --repo-id main --symbol validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id main --callers-of validate_token --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id main --callees-of login --in-file auth/flow.py --json
./scripts/repoctl graph query --repo-id main --impact-file services/token_service.py --depth 2 --json
./scripts/repoctl graph query --repo-id main --impact-symbol issue_token --in-file services/token_service.py --depth 2 --json
```

정확히 하나의 primary selector만 허용한다.

```text
--file
--topic
--import
--symbol
--callers-of
--callees-of
--impact-file
--impact-symbol
```

`--in-file`과 `--depth`는 보조 selector다.

## 결과 계약

모든 결과는 최소 다음을 포함한다.

```json
{
  "query": {},
  "matches": [],
  "nodes": [],
  "edges": [],
  "paths": [],
  "completeness": {},
  "warnings": []
}
```

세부 규칙:

- `matches`: selector가 직접 찾은 node 후보
- `paths`: impact/caller/callee 관계를 사용자가 이해할 수 있는 ordered evidence path
- 각 path는 `from`, `edge`, `to`, `reason`, `source`를 포함
- `completeness`: parse error, unsupported provider, truncated index, provider failure를 노출
- simple symbol name이 여러 개면 임의 선택하지 않음
- ambiguity는 candidate path, qualified name, line range, kind를 반환하고 `graph_query_ambiguous_symbol`로 실패
- unsupported language는 빈 성공이 아니라 capability/completeness warning을 반환
- exact provider symbol id를 내부적으로 유지하고 client가 opaque id를 파싱하도록 요구하지 않음

## Contract 정리

- 현재 생성되는 `CALLS`, `RESOLVES_TO`, `IMPORTS_FILE` 의미를 Graph contract에 기록한다.
- 기존 v1 client가 unknown edge를 무시할 수 있다는 additive compatibility를 유지한다.
- snapshot identity 의미를 깨지 않는 한 schema version을 불필요하게 올리지 않는다.
- 과거 Graph v0 ADR의 “no resolution” 문구는 superseded 또는 historical decision으로 명확히 표시한다.

## 구현 순서

1. symbol selector와 ambiguity handling
2. callers/callees typed traversal
3. impact-file/impact-symbol depth-bounded traversal과 evidence path
4. human-readable CLI output
5. contract/README 정렬
6. copy field scenarios

## Targeted tests

아래 public behavior만 고정한다.

- unique symbol match
- ambiguous symbol fail-closed
- same-file caller
- same-class method caller
- cross-file imported caller
- file import impact for JS/TS
- depth bound and cycle safety
- repo namespace isolation
- parse error completeness
- old file/topic/import selector compatibility

## Fresh-copy 실사용 시나리오

### Scenario G1 — Python change impact

```text
issue_token 정의를 찾음
→ callers와 importers 확인
→ depth 2 impact 확인
→ 관련 test file 후보를 사람이 확인
```

### Scenario G2 — Ambiguous symbol

동일 이름 `login`이 여러 파일에 있을 때 guess하지 않고 후보를 반환하는지 확인한다.

### Scenario G3 — JS/TS file impact

relative import를 가진 TypeScript 파일 변경이 importer file까지 연결되는지 확인한다. symbol call graph를 지원한다고 거짓 주장하지 않는다.

### Scenario G4 — Multi-repo

`web/app.py`와 `api/app.py` 같은 basename/symbol이 서로 섞이지 않는지 확인한다.

## Phase 1 Exit Criteria

- 사용자가 symbol, callers, callees, file/symbol impact를 public CLI로 질의할 수 있다.
- ambiguity와 incompleteness가 구조적으로 드러난다.
- 최소 Python 2개와 JS/TS 1개의 실제 복사본 scenario가 유용한 결과를 낸다.
- existing selectors가 깨지지 않는다.
- Graph contract와 실제 edge/capability가 일치한다.
- Phase 관련 vertical slice가 commit 되어 있다.

## 권장 commit 묶음

```text
feat(graph): expose symbol callers and callees queries
feat(graph): add explainable file and symbol impact traversal
```

구현 규모가 작으면 하나로 합친다.

## 종료 행동

Progress Ledger를 갱신하고 즉시 Phase 2로 진행한다.

---

# 12. Phase 2 — Evidence Context v1

## 제품 목표

Context가 단순 검색 결과 목록이 아니라, 현재 질문 또는 작업을 해결하기 위한 **증거 묶음**이 되게 한다.

## 필수 질문 유형

```text
code_location
call_impact
file_impact
authority_or_contract
past_decision
invariant
failure_mode
```

사용자가 질문 유형을 명시하지 않아도 deterministic heuristic으로 분류할 수 있지만, 결과에는 어떤 유형으로 처리했는지 표시한다. 잘못 분류했을 때 `--mode`로 명시할 수 있게 한다.

예:

```bash
./scripts/repoctl context query "What calls validate_token?" --repo-id main --mode call-impact --json
./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --mode authority --json
```

## Context Bundle 그룹

Context 결과는 다음 그룹으로 정리한다.

```text
must_read
likely_change_surface
callers_and_dependents
tests_and_verification
reviewed_knowledge
supporting_evidence
warnings_and_completeness
```

각 item은 최소 다음을 가진다.

```text
source_ref
content_sha256
selection_reason
score_breakdown 또는 deterministic reason
repo_id
status/currentness
```

Graph item은 relation path와 provider source를 포함한다.

## 출력 형식

JSON contract를 유지하고 사람이 읽는 Markdown 출력을 추가한다.

```bash
./scripts/repoctl context query "..." --repo-id main --format json
./scripts/repoctl context query "..." --repo-id main --format markdown
```

Markdown은 JSON을 단순 dump하지 않는다. 다음 순서로 보여준다.

```text
질문과 해석
필수 읽기
변경 가능 표면
호출/의존 관계
관련 테스트와 검증 힌트
현재 Reviewed Knowledge
경고와 불완전성
원본 근거
```

## Retrieval 원칙

- Graph 객체를 내부 API로 직접 소비하고 CLI stdout을 parse하지 않는다.
- generated wiki, previous context output, candidate cache를 source authority로 사용하지 않는다.
- stale/superseded/deprecated knowledge는 기본 pack에 들어오지 않는다.
- source ref, digest, repo namespace를 잃지 않는다.
- token budget 때문에 mandatory source를 silently drop하지 않는다.
- mandatory source가 budget을 넘으면 truncation이 아니라 explicit budget warning을 반환한다.
- generic lexical match가 precise Graph relation보다 위에 오지 않도록 질문 유형별 scoring을 조정한다.

## 구현 순서

1. 새 Graph typed query/traversal을 Context 내부에 연결
2. 질문 유형과 group contract 구현
3. actionable test/verification hints 생성
4. Markdown rendering
5. stale/forbidden/cross-repo 보호
6. real-task labeled set으로 조정

## Targeted tests

- 각 질문 유형당 대표 public behavior 1~2개
- mandatory source가 budget에서 보존됨
- generated output self-ingestion 방지
- stale knowledge 기본 제외
- cross-repo isolation
- parse error completeness
- Markdown과 JSON이 동일한 source identity를 가짐

## Quality Gate

fixture recall만 보지 않고, 최소 10개의 실제 질문을 사람이 label 한다.

필수 기준:

- required source recall@5: `>= 0.90`
- authority/contract required source recall@5: `1.00`
- supported Graph edge recall: `1.00`
- forbidden generated source: `0`
- cross-repo source leakage: `0`
- source digest integrity: `100%`
- 사람이 평가한 answerability: 10개 중 최소 9개 질문을 bundle만 보고 답할 수 있음

새 metric framework를 만들지 말고 기존 benchmark result와 단일 field-test 문서에 필요한 수치만 추가한다.

## Fresh-copy 실사용 시나리오

### Scenario C1 — Code change question

```text
What is impacted if validate_token changes?
```

결과에 definition, callers, 관련 file, verification hint가 들어가야 한다.

### Scenario C2 — Authority question

```text
Why must generated wiki not become source authority?
```

결과에 정확한 ADR/contract section과 current digest가 있어야 한다.

### Scenario C3 — Stale knowledge

source를 변경한 뒤 stale record가 default context에 섞이지 않고 경고/상태로 드러나는지 확인한다.

## Phase 2 Exit Criteria

- Context가 작업 가능한 group 구조와 Markdown 출력을 제공한다.
- 실제 질문 10개 기준을 통과한다.
- Graph relation, source docs, Reviewed Knowledge의 경계가 유지된다.
- 결과를 사람이 직접 읽었을 때 다음 행동을 결정할 수 있다.
- 기능 묶음이 commit 되어 있다.

## 권장 commit

```text
feat(context): return actionable evidence groups
feat(context): add human-readable evidence bundle output
```

가능하면 하나의 완결된 commit으로 합친다.

## 종료 행동

즉시 Phase 3으로 진행한다.

---

# 13. Phase 3 — Agent Context Pack v1 and Actual Consumption

## 제품 목표

Context Pack이 단순 artifact가 아니라 에이전트가 실제 작업 시작 전에 읽고 행동 범위를 좁히는 표준 입력이 되게 한다.

MCP는 사용하지 않는다. CLI와 workspace operating contract만 사용한다.

## 공개 사용 흐름

```bash
./scripts/repoctl task start T-... --json
./scripts/repoctl context pack \
  --task T-... \
  --repo-id main \
  --format markdown \
  --output .repoctl-state/context-pack/T-....md
```

에이전트는 repo file을 수정하기 전에 다음을 읽는다.

```text
AGENTS.md
active task file
생성된 Context Pack Markdown
pack이 지정한 must_read source
```

`AGENTS.md`의 repo-scoped task read order에 이 절차를 짧게 반영한다. Context Pack은 task scope authority가 아니며, 후보 파일을 실제로 열어 확인해야 한다.

## Pack 구조

```text
Task identity and goal
Read first
Likely change surface
Definitions and callers
Imports and dependents
Relevant tests and verification commands
Current decisions/invariants/failure modes
Known ambiguity and completeness warnings
Source references and pack digest
```

필수 group:

```text
must_read
likely_change
impact
verification
reviewed_knowledge
warnings
```

## Pack 선택 규칙

- task의 explicit `Context Docs`는 항상 `must_read`
- `Discovery` chosen files는 `likely_change` 후보이지만 권위 있는 scope로 승격하지 않음
- Graph direct callers/importers와 관련 test는 `impact`/`verification`
- current Reviewed Knowledge만 포함
- stale/superseded/deprecated status는 기본 제외하고 exclusion summary를 표시
- pack은 repo ID와 source digest를 보존
- task가 잘못된 repo selector를 쓰면 fail closed
- pack이 비었는데 성공으로 끝나지 않음

## 실제 소비 증명

새 agent runtime을 만들지 않는다. 다음 방식으로 증명한다.

1. fresh copy에서 실제 repo-scoped task를 만든다.
2. 새 세션 또는 context를 초기화한 실행자가 `AGENTS.md`, task, generated pack만 먼저 읽는다.
3. broad whole-repo scan 없이 pack이 가리킨 후보를 확인한다.
4. 실제 code change와 tests를 수행한다.
5. task verification에 사용한 pack digest와 유용했던 source를 짧게 기록한다.
6. unrelated file 수정 없이 task를 finish 한다.

이는 fixture recall보다 우선하는 실사용 증거다.

## 최소 실제 작업 3개

### Task P1 — Python symbol change

- known caller가 있는 function 변경
- pack이 definition, caller, 관련 test를 제공
- 변경과 test 통과

### Task P2 — File/import change

- Python 또는 JS/TS import 관계가 있는 file 변경
- pack이 importer/dependent file과 verification을 제공

### Task P3 — Knowledge-sensitive change

- 과거 decision 또는 invariant를 위반하기 쉬운 변경
- pack이 Reviewed Knowledge를 제공
- 에이전트가 그 결정에 맞게 구현

최소 2개의 서로 다른 repository shape 또는 language surface에서 실행한다.

## Targeted tests

- pack group contract
- explicit Context Docs 보존
- Graph impact와 test hint 포함
- current knowledge 포함, historical status 제외
- Markdown/JSON identity 일치
- empty/ambiguous/incomplete pack fail/warn behavior
- task repo namespace

실제 agent 성공 자체를 mock unit test로 만들지 않는다. field-test evidence로 남긴다.

## Phase 3 Exit Criteria

- repo-scoped task 시작 절차가 Context Pack을 실제 입력으로 사용한다.
- Markdown pack이 사람이 읽기 쉽다.
- 세 개의 실제 copied task가 성공한다.
- 세 task 모두 unrelated file 변경이 없다.
- pack이 없을 때보다 적어도 필요한 source 탐색을 명확히 줄였다는 human inspection 기록이 있다.
- 기능이 commit 되어 있다.

## 권장 commit

```text
feat(context): make task packs directly consumable by coding agents
```

## 종료 행동

즉시 Phase 4로 진행한다.

---

# 14. Phase 4 — Reviewed Knowledge v1 Product Loop

## 제품 목표

작업 후 확인된 결정·불변조건·실패모드를 사람이 검토해 장기 기억으로 승격하고, 다음 작업에 실제로 재사용되게 한다.

## Knowledge 종류

v1은 다음 세 종류만 유지한다.

```text
decision
invariant
failure_mode
```

새 ontology를 만들지 않는다.

## 필수 lifecycle

```text
source evidence
→ candidate
→ candidate check
→ human review
→ approve or reject
→ reviewed record
→ query / context reuse / llmwiki render
→ source drift
→ stale
→ refresh candidate
→ supersede or deprecate
```

## Candidate source

필수 지원:

```text
explicit authority document
current Context Pack
completed task receipt
```

완료 작업에서 만드는 candidate는 다음 연결 정보를 포함해야 한다.

```text
origin task id
verification artifact/source
changed files
related symbols when available
repo id
source refs and digests
candidate kind
```

## Review UX

기존 JSON만으로 human review를 강요하지 않는다. 사람이 읽는 review summary를 제공한다.

권장 명령 계약:

```bash
./scripts/repoctl knowledge candidate show KC-... --repo-id main
./scripts/repoctl knowledge candidate check KC-... --repo-id main --json
./scripts/repoctl knowledge candidate review KC-... --repo-id main --format markdown
```

review summary는 다음을 보여준다.

```text
candidate claim
kind
source excerpts/ref
origin task and verification
applies-to files/symbols/topics
similar current records
possible conflict or duplicate
source currentness
approve/reject/supersede next command
```

새 `review` subcommand 대신 기존 `show --format markdown`을 확장해도 된다. 중요한 것은 public human UX다.

## Approval provenance

승인은 명시적 human action이어야 한다.

승인 record/event에는 최소 다음을 남긴다.

```text
reviewer identity or label
review note or reason
approved candidate id
source digest set
related/superseded record ids
approval timestamp
```

기본 reviewer label을 설정할 수 있지만 빈 승인 근거를 silently 허용하지 않는다. 기존 compatibility가 깨진다면 optional field로 추가하고 final field-test에서 명시적으로 사용한다.

## Duplicate와 conflict

- exact same source+claim candidate 중복을 경고
- 같은 applies-to 대상의 current decision과 충돌 가능성을 보여줌
- 자동 reject/merge하지 않음
- human이 supersede, coexist, reject를 선택

## Stale 처리

- source digest drift는 record body를 수정하지 않음
- default query/context에서 stale 제외
- refresh는 새 candidate를 생성
- 승인 시 원 record를 supersede
- historical query는 explicit option에서만 노출
- llmwiki에는 current/historical 상태가 명확히 구분됨

## Fresh-copy 폐쇄 루프

### Scenario K1 — Receipt to Knowledge

```text
actual task finish
→ completion receipt
→ invariant candidate
→ human review
→ approve
→ knowledge query
→ next Context Pack에 포함
```

### Scenario K2 — Conflict/Supersede

```text
existing decision
→ replacement candidate
→ review에서 관련 record 확인
→ approve --supersedes
→ default query는 새 record만 반환
```

### Scenario K3 — Source Drift

```text
source 변경
→ knowledge check에서 stale
→ default context에서 제외
→ refresh candidate
→ human approve
→ lifecycle 유지
```

## Targeted tests

- receipt provenance and applies-to
- human-readable review summary
- reviewer/note provenance
- duplicate/conflict warning
- explicit approval only
- stale default exclusion
- refresh/supersede lifecycle integrity
- multi-repo isolation

이미 존재하는 lifecycle test를 복제하지 말고, 새 public behavior와 regression만 추가한다.

## Phase 4 Exit Criteria

- 실제 completed task에서 candidate를 만들 수 있다.
- 사람이 JSON을 해석하지 않고 review decision을 내릴 수 있다.
- 승인 근거와 reviewer가 추적된다.
- approved knowledge가 다음 Context Pack에 실제 포함된다.
- stale/supersede loop가 fresh copy에서 통과한다.
- 기능이 commit 되어 있다.

## 권장 commit

```text
feat(knowledge): make candidate review and provenance actionable
feat(knowledge): close receipt-to-reviewed lifecycle in task context
```

가능하면 하나로 합친다.

## 종료 행동

즉시 Phase 5로 진행한다.

---

# 15. Phase 5 — llmwiki v1 Useful Static Product

## 제품 목표

현재의 kind별 Markdown export를 GitHub, IDE, local Markdown viewer에서 실제 탐색 가능한 static wiki로 만든다.

웹 서버와 UI framework는 만들지 않는다.

## 출력 구조

single-repo 기본:

```text
docs/knowledge/generated/
  INDEX.md
  decisions.md
  invariants.md
  failure-modes.md
  records/
    <record-id>.md
  targets/
    files/
      <encoded-path>.md
    symbols/
      <encoded-symbol-id>.md
  history.md
  search-index.json
  manifest.json
```

multi-repo:

```text
docs/knowledge/generated/<repo-id>/...
```

`targets/topics/`는 reviewed record에 reliable topic/applicability 정보가 있을 때만 생성한다. claim text에서 임의 topic ontology를 추론하지 않는다.

## INDEX.md

최소 내용:

```text
repository
current record counts by kind
stale/superseded/deprecated counts
links to kind pages
recently approved/replaced records
source health summary
how to verify freshness
```

## Kind pages

`decisions.md`, `invariants.md`, `failure-modes.md`는 각 record의 짧은 summary와 per-record link를 제공한다. 전체 상세 내용을 중복 복사하지 않는다.

## Per-record page

각 `records/<id>.md`는 다음을 포함한다.

```text
title and kind
current lifecycle status
claim
summary/rationale
applies-to files/symbols/tasks/topics
source references with section and currentness
origin task and verification evidence
reviewer and review note
supersedes / superseded-by / deprecated reason
event timeline
backlinks to kind and target pages
```

## Target pages

file/symbol target page는 해당 대상을 적용 범위로 가진 current knowledge와 historical knowledge를 구분해서 링크한다.

예:

```text
Target: auth/flow.py
Current invariants
Current decisions
Known failure modes
Historical/superseded records
Related source refs
```

## Search index

`search-index.json`은 static generated artifact다. 최소 필드:

```text
record_id
repo_id
kind
status
title
claim
summary
applies_to
source_paths
page_path
```

검색 엔진을 만들지 않는다. 이후 도구가 사용할 수 있는 deterministic index만 제공한다.

## Link와 manifest integrity

`knowledge render --check`는 다음을 검증한다.

- expected page missing
- manifest/content digest drift
- broken internal Markdown link
- orphan manifest-owned page
- unreadable page
- source status mismatch
- repo namespace mismatch
- stale current-page representation
- generated page self-ingestion 금지 경계

operator가 직접 만든 unowned note는 삭제하지 않는다.

## Human-readable 품질

페이지는 raw JSON dump가 아니어야 한다.

- opaque id는 표시하되 title/claim보다 앞세우지 않음
- source path와 section을 클릭 가능한 relative link로 렌더
- current와 historical status를 눈에 띄게 구분
- 빈 section을 대량 출력하지 않음
- 한 페이지에서 다음 탐색 경로가 항상 보임

## Fresh-copy 실사용 시나리오

### Scenario W1 — Decision navigation

INDEX → Decisions → Record → Source → Target page를 따라가며 “왜 이 결정을 지켜야 하는가?”에 답할 수 있어야 한다.

### Scenario W2 — Failure mode before change

file target page에서 관련 failure mode를 찾아 다음 Context Pack의 reviewed knowledge와 일치하는지 확인한다.

### Scenario W3 — Lifecycle

superseded decision은 current kind page의 주 항목에서 빠지고 history와 replacement link에서 확인되어야 한다.

### Scenario W4 — Stale source

source drift 후 render/check가 stale 상태를 정확히 보여주며 오래된 지식을 current라고 표시하지 않아야 한다.

## Human acceptance

사람이 5개의 실제 질문을 wiki만 사용해 확인한다.

예:

1. 현재 Graph authority decision은 무엇인가?
2. 이 파일에 적용되는 invariant는 무엇인가?
3. 이 decision이 어떤 source section에서 왔는가?
4. 무엇이 이 record를 대체했는가?
5. 어떤 source가 stale인가?

기준:

- 5개 중 5개 답을 찾을 수 있음
- 각 답은 3번 이하의 page navigation으로 도달
- source와 lifecycle을 확인할 수 있음
- raw record JSON을 직접 열 필요가 없음

## Targeted tests

- per-record page
- links/backlinks
- current/history separation
- target pages
- search index determinism
- broken link detection
- stale source rendering
- owned stale cleanup and unowned preservation
- multi-repo namespace

## Phase 5 Exit Criteria

- static wiki가 단순 4개 summary page를 넘어 record와 target을 탐색할 수 있다.
- source, reviewer, lifecycle, applies-to가 보인다.
- render/check가 link와 freshness integrity를 검증한다.
- 5개 human question 모두 통과한다.
- 기능이 commit 되어 있다.

## 권장 commit

```text
feat(llmwiki): render linked record and target pages
feat(llmwiki): verify links lifecycle and source freshness
```

규모가 맞으면 하나로 합친다.

## 종료 행동

즉시 Phase 6으로 진행한다.

---

# 16. Phase 6 — Full Closed-Loop Field Proof

## 제품 목표

개별 기능이 아니라 Graph → Pack → Work → Knowledge → llmwiki → Next Pack 전체가 실제로 이어지는지 증명한다.

## Golden Workflow A — Change Impact

```text
real repo 선택
→ graph build
→ symbol/file impact query
→ context query
→ 관련 code/test 확인
→ 사람이 결과 유용성 판정
```

PASS 조건:

- 정확한 definition과 direct caller/importer가 보임
- ambiguity를 guess하지 않음
- relevant verification hint가 존재
- unrelated repo source가 없음

## Golden Workflow B — Agent Work

```text
fresh copy
→ task create/start
→ Context Pack Markdown 생성
→ 새 agent/session이 pack 소비
→ 실제 code change
→ targeted test
→ task finish와 completion receipt
```

PASS 조건:

- task 성공
- unrelated file change 없음
- pack이 가리킨 source가 실제 변경/검증에 도움 됨
- broad blind scan 없이 작업을 시작할 수 있음
- verification evidence가 남음

## Golden Workflow C — Knowledge Reuse

```text
completion receipt
→ candidate build
→ candidate check/review
→ human approve
→ knowledge query
→ llmwiki render/check
→ 다음 task Context Pack
```

PASS 조건:

- 승인 전에는 durable current knowledge가 되지 않음
- 승인 후 query/wiki/pack 세 곳에 동일 record identity가 나타남
- source drift 후 stale로 제외됨
- refresh/supersede 후 replacement가 current가 됨

## 실행 규모

최소 다음을 만족한다.

- copied workspace 3개 실행
- real or realistic task 3개
- repository shape 최소 2종
- Python precise call scenario 최소 1개
- JS/TS import impact scenario 최소 1개, 해당 surface가 프로젝트에 존재할 때
- multi-repo isolation scenario 1개, multi-repo가 지원 대상일 때
- knowledge-sensitive scenario 1개

## 기존 benchmark와의 관계

기존 fixture benchmark와 release-candidate gate도 통과해야 하지만, 그것만으로 이 Phase를 닫지 않는다.

필수 증거:

```text
fixture quality gate PASS
fresh-copy Golden Workflow A PASS
fresh-copy Golden Workflow B PASS
fresh-copy Golden Workflow C PASS
human wiki acceptance PASS
```

## 실패 처리

- 실제 실패가 발견되면 먼저 product code를 수정한다.
- 그 실패를 다시 막는 최소 regression test만 추가한다.
- 새 framework나 범용 gate를 만들지 않는다.
- 수정 후 해당 workflow만 재실행하고, Phase 종료 직전에 전체 relevant suite를 한 번 실행한다.

## Phase 6 Exit Criteria

- 세 Golden Workflow가 모두 PASS다.
- 다음 task가 이전 approved knowledge를 실제로 받는다.
- field-test 단일 문서에 human inspection 결과가 있다.
- 남은 P0/P1 product defect가 없다.
- P2/P3는 known limitations에 구체적인 영향과 workaround를 기록한다.
- 수정 묶음이 commit 되어 있다.

## 권장 commit

```text
fix(product): close graph-to-wiki field workflow gaps
```

실패가 없고 코드 변경이 없다면 evidence/docs만을 위해 억지 commit을 만들지 않는다. 기존 Phase commit과 final release commit에 포함한다.

## 종료 행동

즉시 Phase 7로 진행한다.

---

# 17. Phase 7 — Release Artifact and Final Completion

## 제품 목표

working tree가 아니라 배포 가능한 release artifact에서도 전체 제품 흐름이 동작하는지 확인한다.

## Release 검증

1. 전체 targeted/relevant suite 통과
2. 전체 repository test suite 한 번 통과
3. workspace contract check 통과
4. release archive 생성
5. 빈 fresh directory에 archive extract
6. product repo를 init/adopt
7. Golden Workflow A의 smoke
8. Golden Workflow B의 task/pack smoke
9. Golden Workflow C의 candidate/approve/query/render/check smoke
10. generated wiki를 사람이 직접 확인

예시 핵심 명령:

```bash
uv run pytest -q
./scripts/repoctl check --json
./scripts/repoctl meta check --json
uv run python -m tools.repoctl.release dist
```

실제 명령은 현재 release contract를 따른다.

## Documentation 완료

최종 문서에는 다음만 유지한다.

- `docs/PRD.md`: 제품 목적, scope, 이 plan 링크
- 이 master plan: 완료 기준과 final ledger
- Graph contract: 실제 edge/query/capability
- Evidence Context authority ADR/contract: 현재 경계
- README/docs README: 실제 사용자 명령
- field-test 단일 evidence 문서

중복 완료 보고서와 Phase별 감사 파일은 만들지 않는다.

## Known Limitations 형식

남은 제한은 다음 형식으로만 허용한다.

```text
Limitation: <concrete unsupported behavior>
Impact: <who cannot do what>
Current fallback: <safe behavior>
Reason deferred: <measured reason>
Revisit trigger: <specific signal>
Severity: P2 | P3
```

“향후 개선”, “고도화 필요” 같은 모호한 문구는 금지한다.

## Final Definition of Done

아래를 모두 만족해야 `DONE`이다.

### Graph

- public symbol, callers, callees, file/symbol impact query가 있다.
- ambiguity와 completeness가 fail-closed로 드러난다.
- supported Python call edges와 Python/JS/TS import impact가 fresh copy에서 유용하다.
- contract와 구현이 일치한다.

### Context

- 질문/작업별 actionable evidence groups가 있다.
- JSON과 Markdown이 있다.
- source refs, digests, repo namespace, currentness를 보존한다.
- 실제 질문 quality 기준을 통과한다.

### Agent Context Pack

- repo task 시작 전에 에이전트가 실제 pack을 읽는 표준 흐름이 있다.
- 3개의 copied real-use task가 성공한다.
- must-read, likely-change, impact, verification, reviewed knowledge가 유용하다.

### Reviewed Knowledge

- receipt/pack/source에서 candidate를 만들 수 있다.
- human-readable review와 explicit approval provenance가 있다.
- current/stale/superseded/deprecated lifecycle이 안전하다.
- approved knowledge가 다음 pack에 재사용된다.

### llmwiki

- per-record, kind, target, history, search index가 연결되어 있다.
- source와 lifecycle을 따라갈 수 있다.
- `render --check`가 integrity와 stale 상태를 검증한다.
- human acceptance 5/5를 통과한다.

### Delivery

- release artifact fresh-copy E2E가 통과한다.
- P0/P1 defect가 없다.
- 모든 기능은 의미 있는 vertical slice commit으로 들어갔다.
- 문서와 실제 CLI가 일치한다.
- MCP가 추가되지 않았다.

## 최종 commit

실제 release change가 있을 때만 생성한다.

```text
release(repoctl): complete graph context knowledge and llmwiki v1
```

## 최종 보고 형식

```md
DONE

## Shipped product flows
- Graph: <commands and verified behavior>
- Context Pack: <real task evidence>
- Reviewed Knowledge: <closed-loop evidence>
- llmwiki: <navigation and freshness evidence>

## Fresh-copy evidence
- <scenario -> result -> evidence path>

## Commits
- <hash> <message> <user-visible capability>

## Human decisions
- <decisions made or none>

## Known limitations
- <P2/P3 only, concrete format>

## Why this is done
- <map every Final Definition of Done item to evidence>
```

`DONE` 첫 줄이 없으면 최종 완료 보고가 아니다.

---

# 18. Phase 종료 시 continuation 기록

각 Phase가 끝날 때 Progress Ledger를 갱신하고 아래 네 줄을 실행 로그에 남긴다.

```text
Phase result: FIELD_VERIFIED | COMMITTED | BLOCKED_HUMAN
Commit: <hash and message | pending>
Decision: CONTINUE | HUMAN
Next exact slice: <one user-visible capability>
```

`Decision: CONTINUE`이면 같은 세션에서 바로 다음 slice를 시작한다.

---

# 19. Progress Ledger

이 표는 실행자가 실제 상태에 맞게 갱신한다. 시작 시 현재 commit과 evidence를 확인하되, 새로운 감사 시스템을 만들지 않는다.

## Phase 0 Baseline Truth

- `docs/PRD.md` was a placeholder and is now the short product scope plus this plan link.
- `docs/adr/repoctl-graph-v0.md` described the original no-resolution baseline while current Graph build already emits precise provider symbols, `CALLS`, `RESOLVES_TO`, `IMPORTS_FILE`, and expanded completeness fields.
- `docs/contracts/repoctl-graph-contract.md` records additive Graph capabilities and now documents the shipped public selector surface.
- Phase 1 closed the real product gap: public `--symbol`, caller, callee, and impact selectors over already-derived Graph evidence.
- Fresh-copy Phase 0 smoke passed for graph build, file query, context query, wiki render/check, and workspace check.

| Phase | State | User-visible demo | Fresh-copy evidence | Commit(s) | Blocker |
|---|---|---|---|---|---|
| Phase 0 — Contract Lock | COMMITTED | PRD points to this master plan; Graph ADR/contract record current capabilities and Phase 1 gap | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 0 entry | `37c9a63` | none |
| Phase 1 — Graph Product API | COMMITTED | symbol/callers/callees/impact public CLI selectors return matches, paths, completeness, and ambiguity candidates | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 1 entry | `310e9f1` | none |
| Phase 2 — Evidence Context v1 | COMMITTED | context query returns actionable JSON/Markdown groups with Graph caller evidence, source authority refs, and stale knowledge exclusion | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 2 entry | `779e2e6` | none |
| Phase 3 — Agent Context Pack v1 | COMMITTED | three fresh-copy repo tasks generated Markdown packs, used pack evidence before editing, passed focused verification, and finished with receipts | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 3 entry | `7a9cf0d` | none |
| Phase 4 — Reviewed Knowledge v1 | COMMITTED | receipt candidate review Markdown, reviewer/note approval provenance, next-pack reuse, supersede, stale refresh | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 4 entry | `dd05e85` | none |
| Phase 5 — llmwiki v1 | COMMITTED | navigable static wiki with index, kind pages, records, file targets, history, search index, lifecycle/source freshness | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 5 entry | `4590fc0` | none |
| Phase 6 — Closed-loop Field Proof | FIELD_VERIFIED | Golden A/B/C pass, fixture quality gates pass, next-pack reuse and stale refresh proven | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 6 entry | this commit | none |
| Phase 7 — Release and Completion | FIELD_VERIFIED | full pytest, repoctl gates, release archive, extracted-artifact Graph/Context/Knowledge/wiki/field-gate smoke | `docs/field-tests/graph-context-llmwiki-v1.md` Phase 7 entry | this commit | bare extracted artifact is an upgrade source; minimal workspace state is required for workspace-mutating commands |

---

# 20. 첫 실행 지시

이 파일을 받은 실행자는 다음 순서로 시작한다.

1. `AGENTS.md`, `docs/PRD.md`, 이 파일을 읽는다.
2. 현재 Git status와 최근 제품 commit을 확인한다.
3. 현재 public CLI help와 Graph/Context/Knowledge/Render smoke를 한 번만 실행한다.
4. Progress Ledger의 기준선을 실제 상태로 갱신한다.
5. Phase 0을 한 commit 이내로 닫는다.
6. 즉시 Phase 1에서 `graph query --symbol`과 ambiguity behavior부터 구현한다.
7. 기능을 충분히 묶은 뒤 fresh copy에서 G1/G2를 실행한다.
8. 통과하면 commit하고 다음 slice로 계속한다.

첫 실행에서 maintenance harness, 새 benchmark framework, 대규모 test refactor에 손대지 않는다.

---

# 21. 실행용 Goal 문구

사용자는 실행자에게 이 문구를 그대로 전달할 수 있다.

```text
`docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md`를 MCP 제외 제품 개발의 단일 정본으로 읽고 끝까지 실행해.

한 Phase를 끝냈다고 멈추지 마. 각 Phase에서 실제 사용자 기능을 구현하고, 원본과 분리된 fresh copy에서 실사용 흐름을 실행하고, 출력 pack/wiki를 직접 읽어 유용성을 확인한 뒤, 작은 수정마다가 아니라 하나의 사용 가능한 vertical slice로 묶어서 git commit 해.

테스트·fixture·gate만 늘리는 작업은 금지한다. test-only commit, micro commit, 새 maintenance harness, 새 benchmark framework도 금지한다. 기존 기능은 재작성하지 말고 이 계획의 구체적인 gap만 닫아.

제품 의미나 authority/breaking contract처럼 진짜 선택이 필요한 경우에만 HUMAN으로 질문하고, 그 외에는 CONTINUE로 다음 slice와 다음 Phase를 바로 진행해. 최종 Definition of Done 전체와 release artifact fresh-copy E2E가 통과하기 전에는 완료라고 하지 마.
```
