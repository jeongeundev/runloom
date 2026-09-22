# 문서 안내

출시 계획 기준(2026-09-23): [제품 로드맵](product/ROADMAP.md)에 MVP → 반복 사용 → 기록 기반 설정 추천 → 검증된 자동화 확대 → v1.0의 범위·통과 기준·벤치마킹을 정리했다. [ADR-0013](adr/0013-existing-tasks-first-staged-rollout.md)에 따라 기존 업무 가져오기가 초기 경험이고, 자연어 워크플로우 생성·기록 분석은 필수 시작점이 아니다. 제품 개요 → ROADMAP → 해당 단계의 PRD 순서로 읽는다.

최신 제품 확장(2026-09-23): [ADR-0012](adr/0012-delegated-decisions-and-pattern-feedback.md). 위임된 AI 판단, 기존 업무 로그·개인 패턴·접근 가능한 LLM 메모리에서 자동화 후보 발견, 도입 전후 효과 비교와 개선을 포함한다. ADR-0011의 업무 순환을 유지·확장하며 Jev 채택과 모델 재학습은 미확정이다. 새 기능은 미구현이다.

갱신일: 2026-09-22

실서비스의 제품 기준은 [제품 개요](product/PRODUCT_BRIEF.md) → [PRD의 실서비스 업무 순환](PRD.md#실서비스-업무-순환--2026-09-22-확정) → [ADR-0011](adr/0011-task-driven-work-cycle.md) 순서로 읽는다. 업무 목록에서 준비된 일을 실행하고, 결과로 기존 업무를 연결하거나 새 업무를 생성하며, 사람 응답 후 재개하는 것이 목표다.

현재 `service`에는 phase 6 종류·후속 규칙과 phase 7 n8n 입구·출구가 병합되어 있다(phase 7 병합 `1ddc712`, heartbeat 수정 `0fc679a`). 새 업무 순환 전체는 미구현이다. 현재 계약은 [CONTRACT](CONTRACT.md), 전환 설계·검증 순서는 [ARCHITECTURE](ARCHITECTURE.md#실서비스-전환-설계--adr-0011), 실행 이력과 다음 작업은 [현재 인계](CURRENT_HANDOFF.md)를 따른다. 공개 데모 `main`은 phase 5이며 심사 기간 동결한다.

## 현재 문서의 구분

| 위치 | 상태와 용도 |
|---|---|
| [product/ROADMAP.md](product/ROADMAP.md) | 버전별 포함·제외·진입 및 통과 기준, 공식 자료 기반 벤치마킹과 실험 방법, 지표·다음 작업 |
| [CURRENT_HANDOFF.md](CURRENT_HANDOFF.md) | 지금 상태 표, 하네스 재개 방법, 미결 사항, 진단 모델 판단 경위 |
| [product/PRODUCT_BRIEF.md](product/PRODUCT_BRIEF.md) | 최신 제품 방향, 기존 서비스와의 차별성 가설, 검증할 사항 |
| [archive/2026-09-16-gateless/](archive/2026-09-16-gateless/README.md) | 이전 Gateless 기획·도메인·아키텍처와 조사·실험 자료. 현행 요구사항이 아닌 참고 이력 |
| [PRD.md](PRD.md) | 첫 절은 새 실서비스 요구·5개 업무 수용 시나리오. 이후 절은 이전 MVP·공모전 시연 기록 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 첫 절은 미구현 전환 설계와 구현 순서. 이후는 현재 스택·계약·DB·검증·phase 6·7 및 초기 설계 기록 |
| [CONTRACT.md](CONTRACT.md) | 계약 v1의 완전한 요청·이벤트·결과·오류 예시. 11절이 `KindSpec`·`SuccessorRule`·`GenericResult`·일반화된 `HandoffBundle`·`LocalTarget` 요청, 12절이 n8n 입구(`InboundChainRequest`·`InboundChainResponse`)·출구(`ChainCallback`)·오류표. 계약 테스트 fixture로 사용 |
| [VERIFICATION_LOG.md](VERIFICATION_LOG.md) | 실제 외부 도구를 호출한 검증의 원본 기록. 2026-09-20 Codex CLI 실연동 1회(Step 15), 2026-09-21 대본 e2e·VM 배포, 2026-09-22 세 번째 종류 `review` 자동 착수(대본 e2e)·실제 Claude 1회·n8n 입구·출구 e2e(테스트 안 수신기가 n8n 역할 — 실제 n8n 은 step 10) |
| [DIAG_EVAL.md](DIAG_EVAL.md) | 진단 모델 비교 평가 종합(phase 2-model-compare Step 2). 같은 하네스·프롬프트 v3·도구 v2 로 gpt-4.1-mini 와 gpt-4.1 을 5사례 × 3회씩 실행. mini `normal` 0/3(네 번째 미달)·gpt-4.1 `normal` 3/3·잘못된 수정 착수 둘 다 0/12·의도한 사유의 보류 mini 0/12·gpt-4.1 2/12·비용 US$0.13 / US$0.74. 네 평가의 조건 표·모델 비교표·지표 비교·모델별 통과 기준 판정·ADR-0003 을 gpt-4.1 로 갱신(또는 ADR-0007)하자는 제안과 비용 추정(진단 1회 US$0.049, 하루 60회 월 US$88.7). ADR 파일은 미수정 |
| [DIAG_EVAL_prompt-v3_gpt-4.1.md](DIAG_EVAL_prompt-v3_gpt-4.1.md) | 스크립트 산출 원본 — `gpt-4.1-2025-04-14` · 프롬프트 v3 · 도구 v2, 15회, `normal` 3/3, 통과 기준 충족, US$0.7394 |
| [DIAG_EVAL_prompt-v3_gpt-4.1-mini.md](DIAG_EVAL_prompt-v3_gpt-4.1-mini.md) | 스크립트 산출 원본 — `gpt-4.1-mini-2025-04-14` · 프롬프트 v3 · 도구 v2, 15회, `normal` 0/3, 계약 거부 3(`baseline_run_id=''`), 연도 오기 0/12, US$0.1305 |
| [DIAG_EVAL_2026-09-20_prompt-v2.md](DIAG_EVAL_2026-09-20_prompt-v2.md) | 이전 평가(phase 1-diag-fix Step 3, 프롬프트 v2·도구 v2, mini). `normal` 0/3·잘못된 수정 착수 0/12·US$0.10. 문법 거부·줄 범위 초과는 0 이 됐으나 결론 규칙 미준수·읽지 않은 근거 인용·`list_runs.before` 연도 오기(7/15)가 남아 상위 모델 비교를 판단한 원본 |
| [DIAG_EVAL_2026-09-20_prompt-v1.md](DIAG_EVAL_2026-09-20_prompt-v1.md) | 이전 평가(Step 17, 프롬프트 v1·도구 v1, mini). 5사례 × 3회 두 번 실행, `normal` 1/3·잘못된 수정 착수 0/12·총 US$0.23. 도구 반환·계약·프롬프트·모델 분리 분석의 원본 |
| [DEPLOY.md](DEPLOY.md) | 배포 런북. 공개 데모는 VM 한 대(systemd 5개 — 중앙 2·진단 2·연결 프로그램 + Caddy + 백업 타이머)에서 대본 에이전트로 돈다(ADR-0008). 설치·seed·연결·점검·`WORKFLOW_RESET_DB=1` 초기화. 설정 파일은 `deploy/`. VM·도메인은 미지정 |
| [github/](github/README.md) | GitHub 업무 순환(phase 8, [ADR-0014](adr/0014-github-task-cycle.md)) 셀프호스트 운영자 런북 — 최소 토큰 권한·환경변수·저장소/이슈 범위·검증 프로필·같은 기기 수정/검토 등록·v4 → v5 업그레이드·중지·복구·댓글 `반영 불확실` 조정, 테스트 결과와 실제 미검증 구분, 계획과 구현의 차이, step 16 실연동 체크리스트 |
| [n8n/](n8n/README.md) | n8n 연동 예시([ADR-0010](adr/0010-n8n-inbox-and-callback.md)). import 가능한 워크플로우 JSON `runloom-handoff.json`(노드 4개 — Webhook → HTTP Request → Wait → Slack)과 로컬 절차서(Docker n8n · 토큰 발급 · import · 실행 · callback 규칙 · 한계). 공개 데모 VM 은 허용 목록이 비어 대상이 아니다 |
| [등록 방향 정리 전 원문](archive/2026-09-19-before-agent-registration/README.md) | 이전 제품 개요와 handoff. 현행 요구사항과 구분 |
| [GLOSSARY.md](GLOSSARY.md) | 코드 식별자와 일치하는 도메인 용어와 금지 표현. 2026-09-22 종류·규칙·읽기 전용 실행 용어와 입구 토큰·callback·`chain_settled` 용어 반영 |
| [UI_GUIDE.md](UI_GUIDE.md) | 화면 가이드. 심사자 첫 방문 흐름, 화면 목록(`/kinds`·`/sources` 포함), 결과 카드(결과 봉투 포함), 상태 배지·색·컴포넌트·폴링 규칙, 체인 화면 callback 한 줄. 경로·문구는 구현된 템플릿과 같다 |
| [adr/](adr/0000-principles.md) | 프로젝트 원칙·공모전 제약(0000)과 결정별 ADR. 0001 Codex 우선, 0002 서버 스택, 0004 중앙 규칙 기반, 0005 접근 모델, 0006 배포 구성, 0008 공개 데모 대본 에이전트, [0009](adr/0009-registered-kinds-and-succession-rules.md) 업무 종류·후속 규칙은 워크스페이스 등록(흐름을 그리지 않는다), [0010](adr/0010-n8n-inbox-and-callback.md) n8n 은 입구·출구이고 판단은 Runloom 이 한다 — 여기까지 확정, 0003 진단 모델은 gpt-4.1 확정, 0007 사용량 한도 대기는 심사 이후 적용 |
| [presets/nextjs.md](presets/nextjs.md) | 재사용 가능한 스택 프리셋. Next.js 채택을 뜻하지 않음 |

## 문서의 기준과 이력

- 제품 목표는 PRODUCT_BRIEF·PRD의 실서비스 절과 ADR-0011을 따른다. ADR-0009·0010의 부분 대체 범위는 각 문서 상단에 표시했다.
- CONTRACT·GLOSSARY·UI_GUIDE는 현재 구현을 설명한다. 새 방향의 미구현 필드·화면을 현재 지원하는 것으로 해석하지 않는다.
- 아래 문서 표의 과거 phase 설명과 각 문서의 이전 MVP 절은 이력이다. 새 제품의 지원 범위를 제한하는 근거로 사용하지 않는다.

## 이전 자료를 사용할 때

- 보관 문서의 `확정`, `freeze`, `source of truth`, `다음 세션`은 당시 맥락이다. 현재 구현 지시로 적용하지 않는다.
- 이전 설계의 재사용 여부는 새 제품 범위가 정해진 뒤 판단한다. 보관은 모든 과거 결정의 폐기를 뜻하지 않는다.
- 실험 결과와 원시 자료는 보존했다. 당시 검증한 사실·한계는 참고할 수 있지만 새 서비스의 구현이나 검증 완료를 뜻하지 않는다.
- 루트 AGENTS.md가 매 step에 전달되고, 그 밖의 문서는 각 step의 "읽어야 할 파일"에서 지정한다. 새 실서비스 step은 ADR-0011과 PRD의 실서비스 절을 명시적으로 가리켜야 한다. 수동으로 전체 문서를 읽을 때에도 현행 문서와 이력을 구분한다.

## 새 문서 작성 순서 — 논의안

1. 제품 개요: 문제, 첫 사용자, 기존 대안, 제품 가설, 미확인 사항.
2. PRD와 MVP 범위: 첫 시나리오, 포함·제외 기능, 성공 지표, 수용 기준.
3. 사용자 흐름: 시작, 결과 확인, 질문·승인, 실패·재개와 필요한 화면.
4. 기술 설계: 위 흐름에 필요한 데이터·상태·연동·실행 구조.
5. 구현 계획: 작은 단계와 검증 방법.

제품 개요는 기존 위치를 유지하고 PRD는 루트 템플릿을 채웠다. MVP 범위·사용자 흐름·공모전 시연은 현재 PRD 안에서 관리한다. 기술 설계는 기존 ARCHITECTURE.md를 사용하며 구현 계획의 위치는 이후 정한다. 같은 역할의 문서를 중복 생성하지 않는다. 도메인 모델·Work Engine·ADR을 이전 구성을 따라 자동으로 다시 만들지 않는다.
