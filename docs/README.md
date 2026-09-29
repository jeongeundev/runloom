# 문서 안내

갱신일: 2026-09-27

## 읽는 순서

1. [MVP 계획](product/MVP_PLAN.md) — 제품 정의, 이웃 도구와의 경계, 측정 설계, 배포 경로, 10/2 범위와 phase 계획. 지금 가장 먼저 본다.
2. [현재 인계](CURRENT_HANDOFF.md) — 다음 작업, 브랜치·phase 상태, 하네스 재개 방법.
3. [로드맵](product/ROADMAP.md) — MVP → v1.0 단계별 범위·통과 기준·지표.
4. [제품 개요](product/PRODUCT_BRIEF.md) — 문제, 가설, 차별성, 사용자와 에이전트.
5. [PRD](PRD.md) — 실서비스 업무 순환 요구와 수용 시나리오.
6. [ARCHITECTURE](ARCHITECTURE.md) · [CONTRACT](CONTRACT.md) — 전환 설계, 현재 구현·계약 v1(CONTRACT 는 계약 테스트 fixture).
7. [ADR](adr/) — 결정 기록. 0011(업무 순환)·0012(위임 판단·효과 검증)·0013(기존 업무에서 시작)·0014(GitHub 순환)·0015(측정 — phase 9, 미구현)가 최신 제품 기준.

## 문서 목록

| 위치 | 용도 |
|---|---|
| [product/](product/) | MVP 계획, 로드맵, 제품 개요 |
| [PRD.md](PRD.md) | 실서비스 요구·수용 기준 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 전환 설계와 현재 구현 |
| [CONTRACT.md](CONTRACT.md) | 계약 v1 요청·이벤트·결과·오류 예시 |
| [GLOSSARY.md](GLOSSARY.md) | 코드 식별자와 일치하는 용어·금지 표현 |
| [UI_GUIDE.md](UI_GUIDE.md) | 구현된 화면·상태 표시·컴포넌트 규칙 |
| [github/](github/README.md) | GitHub 업무 순환 운영 런북 |
| [n8n/](n8n/README.md) | n8n 입구·출구 연동 예시와 절차 |
| [SELFHOST.md](SELFHOST.md) | 셀프호스트 설치·러너 연결·백업·업그레이드·문제 해결(`deploy/selfhost/`, phase 10) |
| [DEPLOY.md](DEPLOY.md) | 공개 데모 VM 배포 런북(`deploy/` 파일과 테스트가 참조). 셀프호스트는 [SELFHOST.md](SELFHOST.md) |
| [VERIFICATION_LOG.md](VERIFICATION_LOG.md) | 실제 외부 도구를 호출한 검증의 원본 기록 |
| [adr/](adr/) | 결정 기록. 0003(진단 모델)·0005(익명 세션)·0006(VM 배포)·0008(대본 데모)은 공모전 데모 구성의 기록이다 |
| [adr/0019](adr/0019-service-selfhost-only.md) | `service` 브랜치는 셀프호스트만 — demo 모드·진단 데모·대본 에이전트 제거, 내장 종류 `bug_fix`·`code_review`, 스키마 v9(phase 13) |
| [archive/](archive/) | 이력. 현행 요구사항이 아니다 |

## 보관 자료

- [2026-09-27 공모전·이전 인계](archive/2026-09-27-contest-and-history/) — 공모전 시연·이전 MVP 절(PRD·제품 개요·화면 가이드·ARCHITECTURE 일부), 진단 모델 평가(DIAG_EVAL), 2026-09-26 이전 인계.
- [2026-09-19 등록 방향 정리 전](archive/2026-09-19-before-agent-registration/README.md), [2026-09-16 Gateless](archive/2026-09-16-gateless/README.md) — 이전 기획·조사.

보관 문서의 `확정`·`다음 세션` 같은 표현은 당시 맥락이며 현재 지시가 아니다. 새 phase 의 step 은 "읽어야 할 파일" 절에서 현행 문서를 명시적으로 가리킨다.
