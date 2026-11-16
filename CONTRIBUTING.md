# Contributing to agent-handoff-template

한국어 또는 영어로 이슈와 PR을 작성할 수 있습니다. 이 프로젝트는 사람이 작성하는 Task·Board·Handoff와 문서 구조의 계약을 관리합니다. 실행 도구의 버그와 Graph·Knowledge·종료 검사 개선은 별도 [repoctl](https://github.com/humaningansalam/repoctl)에 남겨 주세요.

## 현재 제품과 과거 소스를 구분합니다

현재 문서 전용 제품은 [v1.0.0](https://github.com/humaningansalam/agent-handoff-template/tree/v1.0.0)과 `release/template-1.0.0` 계열입니다. 기본 `main`에는 분리 이전 통합 control-plane 소스가 남아 있습니다. `main`의 Python 도구나 오래된 README를 문서 전용 제품의 설치·개발 요구로 해석하지 마세요.

GitHub 접수 양식과 이 기여 안내 같은 저장소 운영 변경은 기본 브랜치에 반영합니다. 분리 이전 통합 runtime의 자동 릴리스는 비활성화하며, 새 문서 릴리스는 문서 전용 source와 검증된 릴리스 절차를 명시적으로 선택합니다. 문서 제품 자체의 변경은 문서 전용 source 기준에서 작업하고 PR의 base와 대상 계약을 명시합니다. 버그를 분리 이전 runtime에 고치거나 두 제품을 다시 합치지 않습니다. 기존 기본 브랜치·release branch·태그를 임의 이동하지 않습니다.

## 좋은 신고와 제안

Bug에는 문제가 있는 문서·필드·문장과 기대하는 작성 결과, 최소 예시를 적습니다. 문구·링크 오류에는 경로와 설명으로 충분합니다. Python·OS·전체 프로젝트 덤프를 필수로 요구하지 않습니다.

Feature에는 어느 작성·재개·인계 상황에서 어떤 정보가 부족했는지와 원하는 결과를 적습니다. 새로운 필드·체크리스트를 먼저 제안하기보다 그 정보가 없어서 생긴 실제 판단 비용을 설명해 주세요. 유연한 문서 사용을 특정 에이전트·모델·실행 순서로 고정하지 않습니다.

## 문서 변경 검증

변경한 안내를 따라 최소 Task·Board·Handoff 예시를 작성해 보고, 관련 링크와 네 필드 Handoff의 의미가 모순되지 않는지 확인합니다. 원본 Task 양식·Board·계약 문구를 변경하는 일과 GitHub 접수 양식을 변경하는 일을 구분합니다.

문서 전용 source 루트에서는 다음 명령으로 계약 JSON 문법과 diff를 확인할 수 있습니다.

```sh
python3 -m json.tool docs/template-contract.json >/dev/null
python3 -m json.tool fixtures/conformance/cases.json >/dev/null
git diff --check
```

위 JSON 경로는 문서 전용 source 기준입니다. 분리 이전 `main`에 있다고 가정하지 않습니다. 해당 브랜치의 `.github/workflows/ci.yml`은 문서 계약·소유권·artifact 검사를 실행합니다. 기존 payload를 바꾸지 않는 운영 파일 변경에서는 다음 명령으로 배포물 동일성을 확인할 수 있습니다.

```sh
python3 .github/scripts/build_release_archive.py
```

문서 제품 payload를 변경하면 다음 버전의 manifest와 conformance를 함께 검토합니다. 기존 태그·공개 archive·adopter 문서를 덮어쓰지 않습니다. GitHub 양식만 바뀌면 새 template release나 31개 adopter 재적용이 필요하지 않습니다.

## 작업 기록을 중복하지 않습니다

**별도 GitHub Task 양식은 두지 않습니다.** 사용자 workspace의 Task·Board는 현재 실행 범위와 인계의 원본입니다. GitHub 이슈는 공유할 문제·요청, PR은 구현 판단과 검토, CI는 실제 검사 결과를 담습니다. 관련 번호나 링크로 연결하고 Task 상태·Handoff·receipt를 복제하거나 자동 동기화하지 않습니다.

문서 수정, 합의된 유지보수, 양식에 맞지 않는 질문은 빈 이슈나 바로 PR로 작성할 수 있습니다. 작은 변경에 이슈 생성을 강제하지 않습니다. 비공개 Task ID와 전체 기록은 공개할 필요가 없습니다.

## PR 작성과 병합

한 PR은 하나의 논리적 변경과 그 테스트·필요 문서를 함께 다룹니다. 관계없는 포맷 변경이나 의존성 갱신은 분리합니다. 짧은 작업 브랜치를 사용하고 기존 명명 관행을 불필요하게 바꾸지 않습니다. 예: `fix-42-handoff-link`, `docs-contribution-guide`.

제목은 `fix(tasks): ...`, `feat: ...`, `docs: ...`, `chore: ...`처럼 최종 변경을 설명합니다. 본문에는 변경 이유, 실제 검증, 관련 작업과 필요한 영향만 남깁니다. CI 통과 선언 체크박스와 파일별 작업 일지는 추가하지 않습니다.

병합 전 최신 diff와 실제 CI를 확인하고 PR 제목·본문을 정리합니다. 일반 변경은 Squash를 사용하되, 적용 중인 보호 규칙을 우회하거나 존재하지 않는 필수 검사·독립 리뷰어를 가정하지 않습니다. PR 병합, 릴리스 게시, adopter 적용, 제품 수락은 각각 구분합니다.

## 기존 라벨을 활용한 분류

현재의 `bug`, `enhancement`, `documentation` 라벨을 각각 버그, 사용 흐름 개선, 문서 중심 변경에 사용합니다. 양식은 `bug` 또는 `enhancement`만 기본 적용합니다. `[Bug]` 제목 접두사, 별도 Task 분류, 우선순위 자기평가를 중복 요구하지 않습니다. 기존 라벨이나 과거 이슈를 일괄 삭제·재분류하지 않습니다.

검토 후 우선순위가 필요하면 근거를 남기고, 정보 대기에는 필요한 정보, 차단에는 선행 조건과 해제 조건을 적습니다. 정상 해결은 닫고, 중복·미채택·재현 불가는 이유와 관련 이슈를 남깁니다. 추가 라벨·보드·자동화는 반복되는 실제 필요가 있을 때만 도입합니다.
