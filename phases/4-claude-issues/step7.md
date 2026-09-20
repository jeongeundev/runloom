# Step 7: github-issues-adapter

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — CRITICAL: HTTP 는 `adapters/`·`server/` 경계에서만, 비밀값은 환경변수에서만, 외부 입력에서 명령·경로를 실행하지 않는다
- `/docs/ARCHITECTURE.md` — "외부 의존" 표 (GitHub API 를 여기에 추가한다), "기술 스택"(HTTPX)
- `/docs/product/PRODUCT_BRIEF.md` — "Jira 등 업무 도구: 업무 입력의 후보"
- `/src/workflow/server/settings.py` — `ENV_KEYS`, `load_settings`, `Settings`
- `/src/workflow/adapters/` — 기존 어댑터(예: 진단 API 클라이언트가 있으면 그 HTTPX 사용법)
- `/deploy/env/central.env.example` — 키 목록이 `ENV_KEYS` 와 일치해야 한다 (`tests/test_deploy_files.py`, `tests/workflow/server/test_settings.py` 의 RecordingEnv)
- `/tests/workflow/server/test_settings.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

업무 입력의 첫 외부 출처. 운영자(실서비스에서는 로그인 사용자)가 저장소 하나를 지정하면 그 저장소의 열린 이슈를 읽어 업무로 가져온다. **읽기 전용**이며 되쓰기는 이 phase 범위가 아니다. 공개 저장소는 토큰 없이도 읽히지만 IP 당 시간 60회 한도가 있으므로 토큰을 권장하고, 짧은 캐시를 둔다.

## 작업

### `src/workflow/server/settings.py`

- `Settings.github: GitHubSettings | None` — `GitHubSettings(repo: str, token: str | None)`.
- `ENV_KEYS` 에 `GITHUB_ISSUES_REPO`, `GITHUB_TOKEN` 추가. `SECRET_KEYS` 에 `GITHUB_TOKEN`. `GITHUB_ISSUES_REPO` 가 비어 있으면 `github=None` (기능 꺼짐). 형식 `owner/name` 이 아니면 시작 시 `ValueError`.

### `src/workflow/adapters/github_issues.py` (신규)

```python
@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str            # 없으면 ""
    url: str             # html_url
    labels: tuple[str, ...]
    updated_at: str

class GitHubIssues:
    def __init__(self, repo: str, token: str | None, *, client: httpx.Client | None = None, cache_seconds: int = 60, now=utc_now): ...
    def list_open(self) -> list[Issue]: ...          # GET /repos/{repo}/issues?state=open&per_page=30 — pull request 는 제외(`pull_request` 키 있는 항목)
    def get(self, number: int) -> Issue | None: ...  # GET /repos/{repo}/issues/{number} — 404 → None
```

- 헤더: `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, 토큰 있으면 `Authorization: Bearer …`. 타임아웃 10초.
- `list_open` 결과를 `cache_seconds` 동안 메모리에 둔다. 403/429(한도)·5xx·네트워크 오류는 `GitHubUnavailable(RuntimeError)` 로 올리되 **캐시가 있으면 캐시를 돌려주고** `stale=True` 를 알 수 있게 한다 (반환형을 `IssueList(items, fetched_at, stale)` 로).
- 본문은 텍스트로만 다룬다. 명령·경로로 해석하지 않는다. 길이는 20,000자에서 자른다.

### `docs/ARCHITECTURE.md`

- 외부 의존 표에 `GitHub REST API (issues, 읽기 전용)` 행: 언제(가져오기 화면·가져오기 등록 시), 실패 시(화면에 "GitHub 에 연결할 수 없음 · 캐시 n분 전" 또는 기능 숨김).

### `deploy/env/central.env.example`

- 두 키 추가 (값 비움, 주석: 저장소 `owner/name`, 토큰은 읽기 전용 fine-grained, 비우면 기능 꺼짐).

### 테스트 (먼저 작성) — `tests/workflow/adapters/test_github_issues.py`

- `httpx.MockTransport` 로: 열린 이슈 목록 파싱(PR 제외, 라벨 이름 튜플), 토큰 헤더 유무, 캐시 60초 안에는 재요청 없음, 403 + 캐시 있음 → stale 반환, 403 + 캐시 없음 → `GitHubUnavailable`, 404 → None, 본문 None → "".
- `test_settings.py`: 두 키가 `ENV_KEYS` 에 있고 `RecordingEnv` 검사 통과, `GITHUB_ISSUES_REPO` 비면 `github is None`, 형식 오류 → ValueError.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters tests/workflow/server/test_settings.py tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "GITHUB_ISSUES_REPO\|GITHUB_TOKEN" deploy/env/central.env.example
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 테스트에서 실제 GitHub 에 접속하지 마라. 이유: 네트워크·한도에 좌우되는 비결정적 테스트. `MockTransport` 만.
- 이슈에 쓰기(댓글·상태·라벨)를 구현하지 마라. 이유: 이 phase 는 읽기 전용이다 (되쓰기는 phase 5 후보).
- 토큰을 로그·응답·DB 에 남기지 마라. 이유: AGENTS.md CRITICAL.
- 도메인(`src/workflow/domain/`)에서 이 어댑터를 import 하지 마라.
- 기존 테스트를 깨뜨리지 마라
