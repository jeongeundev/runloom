# Phase 8 — GitHub 업무에서 수정·검토 자동 순환

작성일: 2026-09-23. 상태: 구현 계획 작성 완료, 모든 step pending, 실행하지 않음. 사용자 요청은 phase 생성이다. GitHub Issues 버그 수정 → 검토를 이번 계획의 기준으로 삼는다. Jira·기록 추천·Jev는 후속 범위다.

## 목표와 구현 기본값

기존 GitHub 업무를 가져와 담당자의 기존 로컬 Agent가 수정하고 검증된 결과를 원본 이슈에 반영하며, 기존 검토를 연결하거나 새 검토 Task를 생성·실행한다. changes_requested는 제한된 재작업으로 이어지고 필요한 사람 응답 후 재개한다. 다른 독립 업무를 대기시키지 않는다.

계획 기본값(구현 전 step 0에서 계약으로 기록):
- 셀프호스트 운영자 전용 연결, 환경변수 기반 저장소 한정 자격 증명. OAuth/GitHub App 멀티테넌트 서비스는 이번 범위 밖이다. 공개 session의 전역 토큰 사용 금지.
- GitHub REST 폴링. 명시적 저장소·라벨/이슈 선택·시작 시각의 opt-in 범위. 전체 과거 백로그 자동 착수 금지.
- GitHub assignee ID→등록 Agent 및 검토 Agent를 운영자가 연결. 무담당/복수 담당/미연결은 명시적인 대기. GitHub 담당과 서비스 인증 사용자는 같다고 가정하지 않는다.
- 수정과 검토는 같은 로컬 저장소 커밋을 읽을 수 있는 등록에서 수행. 다른 컴퓨터로 커밋 전송·자동 push/PR/merge는 제외한다.
- 수정 결과 ready_for_review→검토 연결/생성. 검토는 result_commit을 직접 읽고 approved/changes_requested/needs_information을 제출한다.
- 자동 수정 재시도 기본 1회(설정으로 감소/비활성 가능); 상한 이후 사람 요청. 검토 승인만으로 원본 이슈 close/기준 브랜치 merge하지 않는다.
- 원본 반영은 안정된 식별자를 가진 댓글 생성/갱신. 네트워크 불확실 시 조정하고 중복 재POST를 피한다. 후속 Task의 GitHub 이슈 복제는 제외한다.
- 기존 진단/보고서 데모 검증은 보존하고 일반 버그 검증을 분리한다. 기존 v1 계약을 깨야 하면 버전 전략을 step 0에서 정한다.
- 운영자 웹 응답 경로로 시작하며 임의 GitHub 댓글을 승인 명령으로 실행하지 않는다.

이 기본값은 사용자가 모든 세부사항을 이미 확정했다는 뜻이 아니다. 리뷰 가능한 계획 선택이며 외부 계정·대상·비용은 실연동 전에 확인한다.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | mvp-contract | MVP 계약과 현재 코드 간극 고정 |
| 1 | cycle-contracts | 업무 순환 계약 |
| 2 | task-readiness | 목록의 실행 가능 판단 |
| 3 | followup-policy | 후속 생성과 검토 재작업 정책 |
| 4 | cycle-storage | 데이터 보존 마이그레이션과 저장 |
| 5 | github-client | GitHub HTTP 경계 |
| 6 | source-settings | 운영자 소스 설정과 담당 연결 API |
| 7 | issue-ingestion | GitHub 업무 수집과 직접 등록 |
| 8 | bug-execution | 보고서 데모와 분리된 일반 버그 실행 |
| 9 | commit-review | 결과 커밋의 읽기 전용 검토 |
| 10 | cycle-worker | 판정·독립 실행·후속 생성 워커 |
| 11 | human-resume | 사람 요청과 응답 후 재개 |
| 12 | github-results | 원본 이슈 결과 반영 outbox |
| 13 | cycle-ui | 연결·업무 목록·결과·대기 화면 |
| 14 | cycle-e2e | 전체 업무 순환과 장애 회귀 |
| 15 | cycle-runbook | 문서 동기화와 실연동 준비 |
| 16 | github-live | 실제 GitHub와 실제 Agent 전체 검증 |

step 0~15는 실제 GitHub 자격·유료 모델 없이 구현 및 대역 검증 가능하다. step 16은 실제 외부 대상과 권한이 필요하다. 자격이 없으면 phase는 마지막 step에서 blocked이며 실연동 완료를 주장하지 않는다.

## 현재 코드에서 확인한 간극

- adapters/task_sources.py의 GitHub/Jira는 fixture다.
- worker._code_result_checks와 local_tool은 report_output/expected-report 중심의 데모 검사에 연결되어 있다.
- generic 검토는 인계 디렉터리를 읽으며 결과 커밋 checkout 검토 계약이 없다.
- worker._spawn_successors는 사전 등록된 후속 Task를 시작하며 결과에서 새 Task를 만들지 않는다.
- source 세션·Agent 등록은 있지만 일반 조직 사용자와 GitHub 담당자 인증 매핑은 없다.
- 하네스는 service를 자동 선택하지 않고 현재 HEAD에서 feat-8-github-task-cycle를 생성한다.

## 실행 전 확인과 명령

현재 작업 트리에는 이전 제품 문서 변경이 있을 수 있다. git status로 확인하고 사용자가 보존하려는 문서·phase를 리뷰 가능한 커밋으로 정리한 상태에서 실행한다. 자동 stash/reset이나 포괄 git add를 하지 않는다. 이 phase 생성 작업은 커밋·하네스 실행·배포를 수행하지 않는다.

```bash
git status --short
git branch --show-current
python3 scripts/execute.py 8-github-task-cycle --engine claude
```

첫 실행 브랜치는 service, 재개는 feat-8-github-task-cycle다. 다른 브랜치에서 새로 실행하지 않는다. 완료 후 service 병합은 별도 단계이며 main에는 병합·배포하지 않는다. scripts/execute.py를 이 계획 때문에 수정하지 않는다.

## 전체 수용 기준

- 기록 없는 운영자가 지정 업무를 접수하고 Agent·입력·위임 설정만으로 실행한다.
- A/B 독립 착수, A 결과로 기존 C 입력 보충, B 결과로 새 F 검토 생성·착수, D/E는 적법한 응답 후 재개.
- 실제 결과 커밋·테스트를 검토하고 수정 요청 재실행·재검토 및 상한을 지킨다.
- 중복 이슈·결과·응답·재시작은 Task/Execution을 중복 생성하지 않는다. 원격 댓글의 불확실 전달은 조회·조정한다.
- source 권한·데모 격리·비밀값 보호·기존 DB 데이터 보존·heartbeat·기존 테스트를 유지한다.
- 실제 GitHub 왕복과 실제 Agent 검증을 대역 e2e와 구분해 기록한다.

구현 결과에 따라 docs/product/ROADMAP.md의 MVP 상태와 docs/CURRENT_HANDOFF.md를 갱신한다. 미래 v0.2~기록 분석 기능을 이 phase 완료 조건에 추가하지 않는다.
