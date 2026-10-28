# Step 4: cli-read — `runloom` 명령(login·logout·list·next·show·report)·텍스트 렌더·console script

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D4·D7·D8·D14~D18·D22~D26), `phases/26-cli/index.json`(이전 step summary)
- `docs/adr/0034-cli.md`, `docs/ARCHITECTURE.md` "CLI — phase 26"(**README 와 다르면 ARCHITECTURE 가 기준** — 명령 표·출력 예시·`--json` 키·문구·exit code), `docs/CONTRACT.md` 18·19·20절
- `src/workflow/cli/config.py`·`client.py`(step 3), `src/workflow/server/work_api.py`(`_item`·`_detail` 응답 칸), `src/workflow/server/report_api.py`·`handoff_reports.py`(`stage_reports`·`report_view` 칸), `src/workflow/contracts/v1.py`(리포트 JSON 모델 `HandoffReport` 등 — 절 이름)
- `pyproject.toml`, `src/workflow/connector/__main__.py`(모듈 실행 선례)
- 서버 테스트 픽스처: 업무·리포트(done·작성 중·실패·맞춤 풀이)를 만드는 기존 테스트(`tests/workflow/server/test_report_api.py`·`test_work_api.py`)

## 작업

1. `src/workflow/cli/main.py` — `main(argv=None, *, env=None, stdin=None, stdout=None, stderr=None, http=None) -> int`(ARCHITECTURE 시그니처). argparse, 공통 `--json`. `src/workflow/cli/__main__.py` 는 `main_entry()` 를 부른다.
2. `pyproject.toml` 에 `[project.scripts] runloom = "workflow.cli.main:main_entry"`(ARCHITECTURE 의 진입 함수). `python3 -m pip install -e .` 뒤 `runloom --help` 가 동작해야 한다.
3. 명령(ARCHITECTURE 명령 표 그대로):
   - `login --server URL`: 주소 정규화 → stdin 첫 줄 토큰(앞뒤 공백 제거, 비면 exit 2) → `GET /me` 확인 → 성공해야 저장 → `<이름>(<역할>, 토큰 <scope>)로 로그인했습니다.` 실패면 저장하지 않고 D24 오류.
   - `logout`: 파일 삭제 + 서버 토큰 폐기 안내(D8). 파일이 없어도 exit 0.
   - `list [--filter] [--repository] [--closed]`: 기본 `all`, 값 검사는 서버(422 를 D24 로).
   - `show KEY`: `GET /work-items/{key}` + `GET /work-items/{key}/reports` + `GET /me`(맞춤 풀이 고르기) → 업무 상세 + 리포트(D16·D17). 펼칠 단계 = 최신 원본 `created_at` 이 가장 늦은 단계. 맞춤 풀이 = 리포트 목록 응답의 `tailored` 중 `requested_by_member_id` 가 나이고 `base_report_id` 가 그 단계의 원본(`latest` 또는 `previous`) 중 하나인 것의 가장 최근 것, `stale` 이면 `옛 리포트 기준`.
   - `next`: `GET /work-items?filter=my_turn` → D14 정렬로 1건 → `show` 와 같은 출력 + 남은 건수 줄. 없으면 안내 문구.
   - `report REPORT_ID`: `GET /reports/{id}` → D18 렌더.
4. `src/workflow/cli/render.py` — 순수 함수(입력 dict → 문자열): 목록 줄, 업무 상세, 리포트(절 제목·주장 id·근거 id·`근거 확인 안 됨`·`기록 없음`·결정 항목 `item_id`·현재 답), 다른 단계 리포트 한 줄 목록, 상태 문구(`작성 중`·`리포트 없음(사유)`·`옛 리포트 기준`). 열린 사람 요청은 `request_id`·질문·허용 행동, 직접 작업은 브랜치.
5. `--json`: 단일 호출 명령은 응답 본문 그대로, `show`·`next` 는 D23 합친 객체(키 `detail`·`reports`·`report`·`tailored`, `next` 는 + `more_my_turn`), `login` 은 `/me` 응답, `logout` 은 `{"deleted": bool}`. `json.dumps(…, ensure_ascii=False, indent=2)`.
6. 오류 처리: `ConfigError` → stderr, exit 2. `ApiError` → `오류 <status> <code>: <message>`(401 이면 다시 로그인 안내 줄), exit 1. `Unreachable` → 서버 주소를 넣은 한 줄, exit 1.

## 테스트

`tests/workflow/cli/test_main.py`·`test_render.py`. `main(..., env={"RUNLOOM_HOME": tmp, ...}, http=TestClient(app), stdout=StringIO(), ...)` 로 실제 앱에 붙인다.
- login: 맞는 토큰 → 파일 생성·출력에 이름, 틀린 토큰 → exit 1·파일 없음, 빈 stdin → exit 2, `http://example.com` → exit 2.
- logout 뒤 다른 명령 → exit 2 + login 안내.
- list: 기본·`--filter my_turn`·잘못된 filter → exit 1(서버 422).
- next: 내 차례 3건(high 1·normal 2, `updated_at` 다르게) → high 가 나오고 `내 차례 2건 더`, normal 둘만 있으면 `updated_at` 오래된 것, 0건 → 안내·exit 0, 다른 멤버 차례 업무는 후보 아님. `--json` 키 집합.
- show: 리포트 없음 / 원본 done / 작성 중 / 실패(사유) / 단계 둘(최신 created_at 단계를 펼치고 나머지 한 줄) / 내 맞춤 풀이 함께 / 남이 요청한 맞춤 풀이는 펼치지 않음 / 원본을 다시 만든 뒤에도 옛 원본 기준의 내 맞춤 풀이가 펼쳐지고 `옛 리포트 기준` 이 붙음.
- 리포트 상태(작성 중·실패·done·맞춤 풀이)는 러너를 돌리지 않고 `repo` 의 리포트 행 생성·상태 변경 함수(`src/workflow/adapters/repo.py` 에서 `handoff_report` 로 찾는다)와 기존 `tests/workflow/server/test_report_api.py` 의 준비 방식으로 직접 심어도 된다.
- `pyproject.toml` 의 `[project.scripts]` 에 `runloom = "workflow.cli.main:main_entry"` 가 있음을 `tomllib` 로 단정(설치 없이 확인).
- `python3 -m workflow.cli --help` 를 subprocess 로 돌려 exit 0.
- report: 절 제목·근거 확인 안 됨·`why` null 의 `기록 없음`·결정 항목 id 와 답.
- render: 모델 텍스트를 그대로 출력(이스케이프·실행 없음), 출력에 토큰 없음.
- 읽기 전용 토큰으로 조회 명령 전부 exit 0.
## 테스트 먼저

소스를 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector`(↔`cli`) 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`), 걸리는 점이 있으면 `"concerns"`. 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- 행동 명령(`decide`·`respond`·`start`·`stop`)을 이 step 에서 만들지 마라. 이유: step 5 범위다.
- 대화형 프롬프트(확인 질문·선택 메뉴)를 넣지 마라. 이유: D25 — 에이전트가 쓰는 도구다(`login` 의 stdin 토큰만 예외).
- 서버 응답의 문자열(브랜치 이름·주소·리포트 내용)을 셸·git·파일 경로로 실행하거나 쓰지 마라. 이유: AGENTS.md CRITICAL 외부 입력 실행 금지, D21.
- 리포트 HTML(`…/html`)을 받아 출력하지 마라. 이유: D18 — CLI 는 JSON 을 텍스트로 렌더한다.
- `src/workflow/cli/` 에서 server·connector·domain·adapters 를 import 하지 마라. 이유: D5(경계 테스트가 막는다).
- 기존 테스트를 깨뜨리지 마라.
