# 문서 안내

갱신일: 2026-09-20

설계 문서(PRD·ARCHITECTURE·CONTRACT·ADR)와 구현 계획(`phases/0-mvp/`)이 있고 구현이 step 0 까지 진행됐다. 먼저 [현재 인계](CURRENT_HANDOFF.md)의 "지금 상태" 표를 읽는다.

## 현재 문서의 구분

| 위치 | 상태와 용도 |
|---|---|
| [CURRENT_HANDOFF.md](CURRENT_HANDOFF.md) | 최근 사용자 의도, 미결 사항, 다음 문서 작성 논의 |
| [product/PRODUCT_BRIEF.md](product/PRODUCT_BRIEF.md) | 최신 제품 방향, 기존 서비스와의 차별성 가설, 검증할 사항 |
| [archive/2026-09-16-gateless/](archive/2026-09-16-gateless/README.md) | 이전 Gateless 기획·도메인·아키텍처와 조사·실험 자료. 현행 요구사항이 아닌 참고 이력 |
| [PRD.md](PRD.md) | 검토용 v0.1. MVP 범위·사용자 흐름·공모전 시연·수용 기준 제안 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 기술 설계 v0.3 제안. 스택·모델 평가안·계약 필드·이벤트 순서·DB 제약·검증 기준. 구현·실연동 검증 전 |
| [CONTRACT.md](CONTRACT.md) | 계약 v1의 완전한 요청·이벤트·결과·오류 예시. 계약 테스트 fixture로 사용 |
| [VERIFICATION_LOG.md](VERIFICATION_LOG.md) | 실제 외부 도구를 호출한 검증의 원본 기록. 2026-09-20 Codex CLI 실연동 1회(Step 15) |
| [DEPLOY.md](DEPLOY.md) | 배포 런북. VM(systemd 4개 + Caddy + 백업 타이머)과 운영자 Mac(launchd 연결 프로그램) 설치 순서와 확인 명령. 설정 파일은 `deploy/`. VM·도메인은 미지정 |
| [등록 방향 정리 전 원문](archive/2026-09-19-before-agent-registration/README.md) | 이전 제품 개요와 handoff. 현행 요구사항과 구분 |
| [GLOSSARY.md](GLOSSARY.md) | 코드 식별자와 일치하는 도메인 용어와 금지 표현. 2026-09-20 채움 |
| [UI_GUIDE.md](UI_GUIDE.md) | 화면 가이드 제안. 심사자 첫 방문 흐름, 화면 목록, 상태 배지·색·컴포넌트·폴링 규칙. 2026-09-20 채움, 경로는 구현 시 확정 |
| [adr/](adr/0000-principles.md) | 프로젝트 원칙·공모전 제약(0000)과 결정별 ADR. 0001 Codex 우선, 0002 서버 스택, 0004 중앙 규칙 기반, 0005 접근 모델, 0006 배포 구성은 확정, 0003 진단 모델은 작업 가정 |
| [presets/nextjs.md](presets/nextjs.md) | 재사용 가능한 스택 프리셋. Next.js 채택을 뜻하지 않음 |

## 이전 자료를 사용할 때

- 보관 문서의 `확정`, `freeze`, `source of truth`, `다음 세션`은 당시 맥락이다. 현재 구현 지시로 적용하지 않는다.
- 이전 설계의 재사용 여부는 새 제품 범위가 정해진 뒤 판단한다. 보관은 모든 과거 결정의 폐기를 뜻하지 않는다.
- 실험 결과와 원시 자료는 보존했다. 당시 검증한 사실·한계는 참고할 수 있지만 새 서비스의 구현이나 검증 완료를 뜻하지 않는다.
- `scripts/execute.py`는 `docs/*.md`와 `docs/adr/*.md`를 비재귀적으로 주입한다. 보관 폴더는 이 자동 주입 대상에서 제외된다. 수동으로 전체 문서를 읽을 때에도 현행 문서와 이력을 구분한다.

## 새 문서 작성 순서 — 논의안

1. 제품 개요: 문제, 첫 사용자, 기존 대안, 제품 가설, 미확인 사항.
2. PRD와 MVP 범위: 첫 시나리오, 포함·제외 기능, 성공 지표, 수용 기준.
3. 사용자 흐름: 시작, 결과 확인, 질문·승인, 실패·재개와 필요한 화면.
4. 기술 설계: 위 흐름에 필요한 데이터·상태·연동·실행 구조.
5. 구현 계획: 작은 단계와 검증 방법.

제품 개요는 기존 위치를 유지하고 PRD는 루트 템플릿을 채웠다. MVP 범위·사용자 흐름·공모전 시연은 현재 PRD 안에서 관리한다. 기술 설계는 기존 ARCHITECTURE.md를 사용하며 구현 계획의 위치는 이후 정한다. 같은 역할의 문서를 중복 생성하지 않는다. 도메인 모델·Work Engine·ADR을 이전 구성을 따라 자동으로 다시 만들지 않는다.
