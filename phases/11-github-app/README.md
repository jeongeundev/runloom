# Phase 11 — GitHub App 연결: 버튼 하나로 저장소 연결·업무 전부 가져오기

작성일: 2026-09-27. 상태: 구현 계획 작성 완료, 모든 step pending. 계기: 셀프호스트 설치 뒤 사용자가 GitHub 연결 화면(로컬 저장소 ID·RFC 3339 시작 시각·검증 프로필 ID·GitHub 숫자 사용자 ID·`.env` 토큰)을 "누가 이렇게 하나하나 입력하나, 이게 더 병목"이라고 지적. 결정: 실제 서비스(Linear·Copilot coding agent·Coolify 등)가 쓰는 흐름으로 간다.

## 목표 흐름

1. `/operator/github` 에서 [GitHub 연결] → GitHub 의 "App 만들기" 화면(manifest 로 이름·권한이 채워짐)에서 [Create].
2. GitHub 설치 화면에서 저장소 선택(전체/선택) → [Install].
3. 설치 저장소마다 소스가 자동으로 생기고 열린 이슈가 **전부** 업무 목록에 "대기"로 들어온다. 새 이슈도 주기 조회로 따라온다(웹훅 없음 — 127.0.0.1).
4. 실행은 지시한 것만: 목록의 [에이전트에게 맡기기] 또는 GitHub 에서 `runloom` 라벨. 그 뒤 수정 → 검토 → 재작업은 자동.
5. 러너가 보고한 GitHub remote(owner/name)로 에이전트·로컬 저장소·검증 프로필을 자동 매칭. 내부 ID 입력 없음. 고급 설정(토큰 연결·담당자 매핑·재작업 상한)은 접어 둔다.

## 계획 기본값 (step 0 이 ADR-0017 로 고정)

1. 사용자 자신의 GitHub App(manifest 생성 → 설치). 인증은 App JWT(RS256, `PyJWT[crypto]`) → 설치 토큰(캐시·만료 전 갱신).
2. 비밀(App 개인 키·client secret·webhook secret·PAT)은 데이터 볼륨의 0600 비밀 파일(`adapters/secret_store.py`, `WORKFLOW_SECRET_DIR`). DB·백업·로그·응답 제외. AGENTS.md 비밀값 규칙을 "환경변수 또는 비밀 저장소"로 바꾼다.
3. ADR-0014 "전체 백로그는 받지 않는다" → "전체를 목록에 가져오되 실행은 지시한 것만".
4. 기존 환경변수 토큰·라벨 필터 소스는 그대로 호환.
5. 러너 계약은 바꾸지 않는다 — 기존 `discovered` dict 에 `github_repository`(owner/name 만, URL 원문·인증 정보 제외).

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | app-design | ADR-0017, ADR-0014 범위 갱신, AGENTS.md 비밀값 규칙, ARCHITECTURE 절, GLOSSARY, phase 순서(12-real-repo) |
| 1 | secret-store | 0600 비밀 파일 저장소 |
| 2 | app-auth | manifest code 교환, App JWT·설치 토큰, 설치 저장소 목록, 클라이언트 토큰 공급자 |
| 3 | source-config | `intake`(filtered/all_open)·`trigger_label`·기본 담당·자동 결정 칸 |
| 4 | connector-remote | 러너가 GitHub owner/name 보고 |
| 5 | intake-all | 열린 이슈 전부 가져오기, 트리거 라벨 자동 착수, 클라이언트 선택 |
| 6 | auto-match | 에이전트·로컬 저장소·검증 프로필 자동 결정, 새 대기 코드 |
| 7 | connect-flow | App 생성·callback·setup·토큰 연결·[맡기기] 경로 |
| 8 | connect-ui | `/operator/github` 재설계, 업무 목록 [에이전트에게 맡기기] |
| 9 | app-verify | 가짜 GitHub e2e, SELFHOST·github 문서, 인계 |

모든 step 은 가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다([Create]·[Install] 은 사용자가 누른다). 늦어지면 토큰 연결(고급) → 라벨 트리거 → 담당자 매핑 순으로 뺀다.

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 11-github-app --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)는 건드리지 않는다. 병합 뒤 `deploy/selfhost/install.sh` 재실행으로 반영한다.
