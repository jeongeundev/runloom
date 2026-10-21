# Step 8: ops-docs — 업그레이드·n8n 문서

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D9·D11·D33·D35), phases/24-human-api/index.json(이전 step summary — step 4·5 가 실제로 바꾼 경로)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 주소 표, docs/adr/0032-*.md
- docs/SELFHOST.md "업그레이드"(v25 등 이전 항목 형식 — `tests/test_selfhost_files.py` 766~777 근처가 `- v25(phase 23)` bullet 을 다음 `\n- ` 까지 잘라 검사한다), `deploy/selfhost/`(install.sh·install-runner.sh 안내 문구), docs/CURRENT_HANDOFF.md, AGENTS.md "명령어"
- 옛 JSON 경로 찾기 — 저장소 루트에서 아래를 실행한다(새 경로 `/api/v1/…` 와 화면 경로 `/operator/github/sources/{id}/runner`·`/work/{key}/internal-requests`·`/responsibilities/add` 는 잡지 않는다. 2026-10-21 설계 시점 120줄). 목표는 마지막 줄 `hits=0`:

```bash
python3 - <<'PY'
import pathlib
import re

OLD = re.compile(
    r"(?<![\w/}-])"
    r"(/connector/(?:claim|exchange|heartbeat|registrations)"
    r"|/executions/\{"
    r"|/sources/(?:n8n|\{source\}|\{s\})/chains"
    r"|/human-requests"
    r"|/metrics\.(?:json|csv)"
    r"|/field-mappings"
    r"|/internal-requests"
    r"|/work-items/"
    r"|/responsibilities(?!/(?:add|remove))(?![\w-])"
    r"|/github/sources"
    r"|/operator/github/sources/[^/\s`]+/baseline)"
)


def old_paths(line):
    found = []
    for m in OLD.finditer(line):
        if line[: m.start()].endswith("/api/v1"):
            continue
        found.append(m.group(1))
    return found


if __name__ == "__main__":
    # 옛 경로를 일부러 남기는 곳: ADR-0032, ARCHITECTURE "사람용 API — phase 24" 절, `→` 와 /api/v1 이 같은 줄(옛→새 대응)
    files = [p for p in pathlib.Path("docs").rglob("*.md")
             if "archive" not in p.parts and not p.name.startswith("0032-")]
    files += [pathlib.Path("README.md"), pathlib.Path("AGENTS.md")]
    hits = 0
    for p in files:
        if not p.exists():
            continue
        in_phase24 = False
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if line.startswith("## "):
                in_phase24 = p.name == "ARCHITECTURE.md" and line.startswith("## 사람용 API — phase 24")
            if in_phase24 or ("→" in line and "/api/v1" in line):
                continue
            if old_paths(line):
                hits += 1
                print(f"{p}:{n}: {line.strip()[:120]}")
    print(f"hits={hits}")
PY
```

## 작업

1. `docs/SELFHOST.md` "업그레이드" 에 v26 항목(`- v26(phase 24)` bullet — v25 항목과 같은 형식): 순서(D35) — 진행 중 실행 0건 확인 → `backup create` → `install.sh`(스키마 26) → **러너 재설치 `install-runner.sh` 필수**(옛 러너는 옛 주소를 불러 404 — 실행을 받지 못함) → **n8n 워크플로우의 Runloom 주소를 `/api/v1/sources/n8n/chains` 로 수정** → 확인(`/healthz` 200, 러너 claim 이어짐, `/me` API 토큰 절). 멤버 토큰 사용법 한 단락(만들기·`Authorization: Bearer`·읽기 전용).
2. n8n 예시·README 는 step 5 가 이미 고쳤다 — 확인만.
3. **문서 전부**(README D33 — ADR·`VERIFICATION_LOG.md`·ARCHITECTURE 지난 phase 절·CONTRACT 옛 절·GLOSSARY·`docs/product/*`·`docs/github/` 등 포함)의 옛 JSON 경로를 ARCHITECTURE "사람용 API — phase 24" 주소 표의 새 경로로 고친다. 제외는 `docs/archive/` 와 `phases/*/` 뿐. 화면 경로(D10)는 고치지 않는다. 위 스크립트가 `hits=0` 이 될 때까지. 스크립트가 화면 경로나 새 경로를 잘못 잡으면 그 줄은 고치지 말고 summary 에 남긴다.
4. `docs/CURRENT_HANDOFF.md` 맨 위 "다음 작업" 을 "24-human-api 완료 — PR 병합 → 셀프호스트 v26(러너 재설치·n8n 주소) → ② 인계 리포트 설계" 로(할 일 목록 — 모두 사용자 지시 뒤).

## 테스트

- 문서 step 이지만 `tests/test_selfhost_files.py`·`tests/test_n8n_example.py`·`tests/workflow/contracts/test_v1.py`(CONTRACT ` ```json ` 블록 개수·모델 대응 — 옛 절의 경로만 바꾸고 블록은 늘리거나 줄이지 않는다) 등 문서를 읽는 테스트가 통과해야 한다. 스크립트 마지막 줄을 summary 에 남긴다.

## 금지사항 (이 step)

- `src/`·`tests/` 의 동작을 바꾸지 마라. 이유: 문서 step.
- 셀프호스트에 실제로 업그레이드하지 마라. 이유: 사용자 지시 뒤.
- `docs/archive/`·`phases/*/` 를 고치지 마라. 이유: 보관물과 하네스 실행 기록이다(README D33).

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(주소·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(요청 본문·쿼리·토큰 이름)에서 명령·경로를 받아 실행하지 않는다.
   - 비밀값(연결 토큰 `wfc_…`, **멤버 토큰 `wfm_…` 원문**, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 로그인·초대 링크 원본)을 DB·로그·응답(발급 응답 1회 제외)·템플릿·백업에 넣지 않는다. DB 에는 `token_sha256` 만.
   - 웹 폼과 API 는 같은 도메인·`work_actions`·`views` 함수를 쓴다. API 전용 규칙(권한·상태 전이·중복 방지)을 따로 만들지 않는다.
   - GLOSSARY 이름을 그대로 쓴다(`WorkItem`·업무 키, `Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다(ADR-0009).
3. 성공이면 `phases/24-human-api/index.json` 의 이 step 만 `completed` 로 바꾸고 `summary`(생성·수정 파일, 결정, 테스트 수, 다음 step 주의점 한 줄)와 `verification`(실제 실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`)을 남긴다. 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인 못 한 것)은 `concerns` 에.
4. 3회 수정 후에도 실패하면 `error` + `error_message`. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 후 즉시 중단. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항 (공통)

- 실제 Claude/Codex·GitHub·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 가짜 클라이언트·`MockTransport`·FastAPI `TestClient` 로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/.claude/`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 셀프호스트에 업그레이드를 적용하지 않는다. 이유: 실사용 환경 — 적용은 phase 뒤 사용자 지시.
- 새 의존성(Python 패키지, JS·CSS 프레임워크, CDN)을 추가하지 않는다. 이유: ADR-0002.
- 화면(HTML) 라우트·화면 폼 POST 경로(`/tasks…`, `/work/{key}/…`, `/me…`, `/responsibilities/add`·`/remove`, `/sources/tokens…`, `/operator/…`, `/operator/github/app/callback`, `/requests/…`, `/team…` 등)와 `/healthz` 를 옮기지 않는다. 이유: README 결정 D10 — JSON API 만 `/api/v1` 로 옮긴다. 경로 문자열 일괄 치환을 하지 마라 — 같은 접두어의 화면 폼이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈·화면을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- 기존 테스트를 깨뜨리지 마라.
