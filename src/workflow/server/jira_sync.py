"""Jira 이슈 가져오기 — 워커가 프로젝트마다 부르는 JQL 폴링 (ADR-0024 결정 4~9, ARCHITECTURE "Jira 소스 — phase 18").

- `search/jql` 을 `nextPageToken` 끝까지 따라가며 이슈마다 `repo.upsert_jira_issue`(한 트랜잭션)로 저장한다. 모든 페이지를
  저장한 뒤에만 커서(본 이슈의 가장 늦은 `updated`, 포함 경계)를 옮긴다. 도중에 실패하면 커서는 그대로이고 이미 저장한
  이슈는 다음 바퀴에 digest 로 흡수된다.
- JQL 에는 프로젝트 id 와 커서 숫자만 넣는다(`jira_intake.search_jql`). 이슈 유형 거르기·시작점·종류 매핑은 받은 뒤 repo 가 한다.
- 401 은 연결에 `auth_failed_at` 을 쓰고 멈춘다(다시 연결할 때까지 워커가 이 워크스페이스를 부르지 않는다). 429 는
  `retry_after_seconds`. 그 밖의 Jira 오류는 `error` 에만 남긴다. Jira 에 쓰지 않는다.
- 원본 닫힘은 상태 범주 `done` 이다(스냅숏 행 `state`) — 준비 판정·후속 결정의 `source_closed` 가 그대로 쓴다. Runloom 업무를
  닫거나 도는 실행을 끊지 않는다.
"""

from dataclasses import dataclass, field
from sqlite3 import Connection, Row

import httpx

from workflow.adapters import repo
from workflow.adapters.errors import NotFound
from workflow.adapters.jira_client import HttpJiraClient, JiraClient, JiraError, JiraRateLimited, JiraUnauthorized
from workflow.adapters.secret_store import JIRA_API_TOKEN, SecretStore
from workflow.contracts.jira import JiraIssueSnapshot
from workflow.domain.issue_intake import IntakeFacts
from workflow.domain.jira_intake import intake_facts, next_cursor_ms, search_jql
from workflow.server import jira_connect

# 프로젝트마다 가져오기 간격 — GitHub 소스(`GITHUB_SYNC_INTERVAL_SECONDS`)와 같은 값
JIRA_SYNC_INTERVAL_SECONDS = 60


@dataclass
class JiraSyncResult:
    """한 프로젝트의 가져오기 결과. 로그·워커용이며 상태가 아니다(상태는 DB 의 커서·스냅숏)."""

    created: list[str] = field(default_factory=list)  # 새 업무
    updated: int = 0  # 스냅숏이 바뀐 이미 받은 이슈(후속 업무에 처음 붙은 것 포함)
    error: str | None = None
    retry_after_seconds: int | None = None
    auth_failed: bool = False


def sync_project(conn: Connection, client: JiraClient, source_id: str, now: str) -> JiraSyncResult:
    """프로젝트 하나를 한 번 가져온다. Jira 오류는 예외 대신 결과로, DB 오류는 그대로 올린다(커서가 넘어가지 않았다)."""
    result = JiraSyncResult()
    row = repo.jira_project_row(conn, source_id)
    if row is None:
        raise NotFound(f"jira project {source_id}")
    session_id = row["session_id"]
    project = repo.jira_project_config(row)
    connection = repo.get_jira_connection(conn, session_id)
    if not project.enabled:
        return result
    if connection is None or connection["disconnected_at"] is not None:
        result.error = "jira_not_connected"
        return result
    run = repo.get_github_source(conn, session_id, project.github_source_id)
    mappings = repo.list_field_mappings(conn, session_id, "jira")
    jql = search_jql(project.project_id, row["cursor_ms"])
    seen: list[JiraIssueSnapshot] = []
    token: str | None = None
    while True:
        try:
            snapshots, token = client.search_issues(jql, next_page_token=token, site_url=connection["site_url"])
        except JiraUnauthorized as exc:
            repo.mark_jira_auth_failed(conn, session_id, now=now)
            result.error, result.auth_failed = str(exc), True
            return result
        except JiraRateLimited as exc:
            result.error, result.retry_after_seconds = str(exc), exc.retry_after
            return result
        except JiraError as exc:
            result.error = str(exc)
            return result
        for snapshot in snapshots:
            upsert = repo.upsert_jira_issue(conn, session_id, project, snapshot, site_url=connection["site_url"],
                                            run=run, mappings=mappings, now=now)
            if upsert.created:
                result.created.append(upsert.work_item_id)
            elif upsert.changed:
                result.updated += 1
            seen.append(snapshot)
        if token is None:
            break
    repo.advance_jira_cursor(conn, source_id, next_cursor_ms(seen, row["cursor_ms"]), now=now)
    return result


def task_intake_facts(conn: Connection, session_id: str, task_id: str) -> IntakeFacts | None:
    """준비 판정의 담당·입력·원본 부분 — 이 단계에 Jira 이슈가 붙어 있을 때(수정 단계)만. 아니면 None
    (`github_sync.task_intake_facts` 가 본다). 지시는 [맡기기](`operator`)나 Runloom 후속(`followup`)뿐이다."""
    issue = repo.get_jira_issue_by_task(conn, session_id, task_id)
    if issue is None:
        return None
    task = repo.get_task(conn, task_id)
    project = repo.get_jira_project(conn, session_id, issue["source_id"])
    run = repo.get_github_source(conn, session_id, project.github_source_id) if project is not None else None
    return intake_facts(
        request=task["request"],
        run_mode=task["run_mode"],
        state=issue["state"],
        delegated_by=issue["delegated_by"],
        max_rework_rounds=run.max_rework_rounds if run is not None else None,
    )


class JiraClients:
    """워커의 `jira_for` — 연결 행 → 클라이언트. 끊겼거나 토큰 오류 표시가 있거나 토큰 파일이 없으면 None.
    토큰은 부를 때마다 비밀 파일에서 읽는다(DB·메모리에 들고 있지 않는다)."""

    def __init__(self, secrets: SecretStore, *, transport: httpx.BaseTransport | None = None):
        self._secrets = secrets
        self._transport = transport

    def __call__(self, connection: Row) -> JiraClient | None:
        if connection["disconnected_at"] is not None or connection["auth_failed_at"] is not None:
            return None
        token = self._secrets.read(JIRA_API_TOKEN)
        if token is None:
            return None
        return HttpJiraClient(jira_connect.api_base_url(connection), connection["email"], token,
                              transport=self._transport)
