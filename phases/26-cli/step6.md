# Step 6: usage-docs — `docs/CLI.md`·SELFHOST·문서 정합

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D26·D27·D29~D31), `phases/26-cli/index.json`(이전 step summary 전부)
- `docs/adr/0034-cli.md`, `docs/ARCHITECTURE.md` "CLI — phase 26", `docs/GLOSSARY.md` phase 26 절
- `src/workflow/cli/`(step 3~5 결과), `pyproject.toml`
- `docs/SELFHOST.md`("업그레이드" 절 — v26·v27 항목 꼴), `README.md`(저장소 루트 — 사용 문서 링크 위치), `tests/test_selfhost_files.py`(SELFHOST 문서를 단정하는 규칙 — 문서에 적는 `WORKFLOW_*` 환경변수는 배포 파일에 있어야 한다 등)

## 작업

1. **`docs/CLI.md`**(새 문서, 한국어):
   - 설치: 저장소 안 `python3 -m pip install -e .`, 다른 Mac 의 팀원 `python3 -m pip install "git+https://github.com/jeongeundev/runloom@<태그 또는 커밋>"`(D29). 서버 의존성도 함께 깔린다는 점.
   - 토큰: 웹 `/me` 의 "API 토큰" 절에서 발급(읽기 전용 / 내 권한 전부 차이) → `runloom login --server URL` 에 붙여 넣기(표준 입력 — 예: `pbpaste | runloom login --server http://localhost:8000`). 환경변수 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER`·`RUNLOOM_HOME`, 자격 파일 경로·권한, http 는 localhost 만.
   - 명령별 예시(실제 출력 꼴은 step 4·5 의 구현에서 가져온다): `next`·`show`·`report`·`decide`·`respond`·`start`·`stop`·`list`, `--json`, exit code.
   - 에이전트에게 쓰게 하는 법(D26): 터미널의 Claude Code·Codex 에게 "runloom next 로 내 다음 업무와 인계 리포트를 읽고, 확인할 것은 runloom decide 로 답한 뒤 runloom start 로 시작해" 같은 지시 예. 러너가 띄운 에이전트는 쓰지 않는다(D12 — 등록 `--env` 로 `RUNLOOM_TOKEN` 을 넘길 수 없다).
   - 하지 않는 것(D1·D2·D27): 맡기기·우선순위·판단 제안·맞춤 풀이 요청·다시 만들기는 웹, 직접 작업 리포트 없음, **Cloudflare Access 뒤 서버는 아직 지원하지 않음**.
2. **`docs/SELFHOST.md`** "업그레이드" 절에 항목 `- v27 그대로(phase 26)` 꼴로 이번 변경: 스키마 변화 없음, `GET /api/v1/me` 때문에 `install.sh` 재실행 필요, 러너 재설치는 선택(`RUNLOOM_*` 예약 이름·`wfm_` 로그 마스킹을 원하면). CLI 사용은 `docs/CLI.md` 링크. `tests/test_selfhost_files.py` 규칙을 지킨다 — **SELFHOST.md 에는 `RUNLOOM_` 로 시작하는 환경변수 이름을 쓰지 않는다**(`test_selfhost_md_env_names_are_real` 이 배포 파일에 없는 `RUNLOOM_…` 이름을 실패시킨다). 러너 재설치 이유는 "CLI 토큰 환경변수를 러너 등록에서 막고 멤버 토큰을 로그에서 가린다"처럼 이름 없이 쓴다.
3. `docs/README.md` 의 문서 표에 `CLI.md` 와 `adr/0034-cli.md` 행을 더한다(저장소 루트에는 README 가 없다 — 새로 만들지 마라).
4. 문서 정합: ARCHITECTURE "CLI — phase 26" 과 ADR-0034 가 실제 구현(명령·인자·문구·exit code)과 다르면 문서를 구현에 맞춰 고치고 summary 에 무엇을 고쳤는지 남긴다. GLOSSARY phase 26 절 제목의 "(미구현)" 은 그대로 둔다(phase 12~25 선례와 같다).

## 테스트

문서 step 이다. 소스를 바꿔야 하면(문서와 구현 불일치를 구현 쪽에서 고쳐야 할 때) 그 부분만 테스트 먼저로 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
python3 -m workflow.cli --help
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. `docs/CLI.md` 의 명령 예시를 `python3 -m workflow.cli <명령> --help` 출력과 대조한다(`pip install -e .` 는 빌드 백엔드를 네트워크로 받을 수 있어 AC 에 넣지 않았다 — 가능하면 실행해 `runloom --help` 도 확인하고 결과를 summary 에 적는다).
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`, `"verification"`(실행한 AC 명령과 종료 코드 목록), 걸리는 점은 `"concerns"`(실제 셀프호스트·실제 에이전트로 확인하지 않은 것 포함). 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- 셀프호스트에 실제로 설치·업그레이드하지 마라. 이유: phase 뒤 사용자 지시로 한다.
- 문서에 실제 토큰·비밀값 예시 원문을 넣지 마라(`wfm_…` 처럼 줄여 쓴다). 이유: AGENTS.md 비밀값 규칙.
- `CURRENT_HANDOFF.md` 를 고치지 마라. 이유: 로컬 전용 문서이고 하네스 뒤 사람이 갱신한다.
- 기존 테스트를 깨뜨리지 마라.
