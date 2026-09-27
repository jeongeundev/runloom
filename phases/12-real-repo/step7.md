# Step 7: 알림 웹훅 — 대기열과 전송

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/adapters/callback_client.py, src/workflow/domain/callback_policy.py, src/workflow/server/worker.py (`_deliver_callbacks`, 백오프 상수, `create_human_request_once` 호출부 두 곳, `_reflect_failures`, step 6 의 PR 열림)
- src/workflow/adapters/secret_store.py (`NAMES`), src/workflow/adapters/db.py (step 6 의 v8), src/workflow/adapters/repo.py
- tests/workflow/adapters/test_callback_client.py, tests/workflow/server/test_worker.py (또는 callback 전달 테스트 파일)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- 알림 대기열: step 6 의 스키마 v8 에 ADR-0018 이 정한 알림 테이블(원인 키 유일 — 같은 사람 요청·같은 실패·같은 PR 에 한 번, 상태·시도 수·다음 시도 시각·마지막 오류 요약)을 더한다. step 6 과 같은 v8 마이그레이션 안에 넣는다(버전을 또 올리지 않는다 — 아직 배포 전이다).
- 원인: ① 새 사람 요청(`create_human_request_once` 가 새로 만든 경우만) ② PR 열림(step 6) ③ 수정·검토 업무 실패 확정(`_reflect_failures` 가 "실패" 로 마감한 경우). 알림 URL 이 설정되지 않았으면 쌓지 않는다.
- 전송: 새 모듈 `adapters/notify_client.py`(이름은 ADR-0018) — URL 은 `SecretStore` 에서 읽는다(`NAMES` 에 추가). Discord 호스트면 `{"content": 문구}`(2000자 자름), 그 밖은 ADR-0018 JSON. 문구 = 원인 한 줄 + 업무 제목 + 링크(업무 URL, PR URL). 비밀·이슈 본문 전체·로그를 넣지 않는다. 429 `retry_after` 존중, 그 외 실패는 callback 과 같은 지수 백오프·상한 5회 뒤 포기(업무 상태 불변). 시간 제한.
- URL 형식 검사는 저장 때(step 8) 하고, 전송 때도 형식이 깨졌으면 포기로 기록한다. URL 원문을 로그·DB·예외 문구에 넣지 않는다(호스트만).
- 워커 tick 에 전송 단계를 더한다. demo 모드 동작은 불변.

## 테스트 먼저

- 대기열: 세 원인이 각각 한 번만(재평가·재시작에도), URL 없으면 없음.
- notify_client: Discord 본문 형태·2000자, 일반 JSON 형태, 429 retry_after, 5회 뒤 포기, 업무 상태 불변.
- 로그·DB·예외 문자열에 URL(토큰 경로) 없음.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 — git 인자 배열은 러너 어댑터가 고정한다 / 비밀값(연결 토큰, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / 기본 모드(demo)와 phase 8·11 의 기존 GitHub 순환이 그대로 동작한다.
3. 성공이면 `phases/12-real-repo/index.json` 의 step 7 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
