"""metrics_api.py — 지표 JSON·CSV 와 기준선 가져오기 (phase 9 step 8, ADR-0015, ARCHITECTURE "측정 — phase 9").

운영자 세션만 쓰고 그 세션의 데이터만 보인다. GitHub 는 가짜 클라이언트(`app.state.github_client`)로 대신한다.
모르는 값은 JSON null·CSV 빈 칸이며 0 으로 채우지 않는다.
"""

import csv
import dataclasses
import io
import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server import metrics_api
from workflow.server.auth import SESSION_COOKIE, sign_session
from workflow.adapters.github_client import GitHubForbidden, GitHubRateLimited, GitHubUnavailable
from workflow.contracts.github import GitHubIssueSnapshot, GitHubSourceConfig, IssuePrLink
from workflow.contracts.v1 import ArtifactMeta, ExecutionEvent, ExecutionRequest
from workflow.domain.metrics import BASELINE_NOTE

from .conftest import event, meta_for, request_body, seed_execution, task_row
from .test_github_api import login

TOKEN = "github_pat_" + "M3tr1c" * 10
SOURCE = "ghs-1a2b3c4d"
OPENED_BEFORE = "2026-09-01T00:00:00Z"


@pytest.fixture
def settings(settings):
    return dataclasses.replace(settings, github_token=TOKEN, github_repos=("acme/billing",))


class FakeGitHub:
    def __init__(self, links=(), error: Exception | None = None):
        self.links = list(links)
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def list_issue_pr_links(self, repo_name: str, *, opened_before: str) -> list[IssuePrLink]:
        self.calls.append((repo_name, opened_before))
        if self.error is not None:
            raise self.error
        return self.links


def _link(issue: int, pr: int, opened: str, merged: str) -> IssuePrLink:
    return IssuePrLink(issue_number=issue, issue_title=f"이슈 {issue}", issue_opened_at=opened,
                       pr_number=pr, pr_merged_at=merged)


LINKS = [
    _link(1, 10, "2026-08-01T00:00:00Z", "2026-08-01T02:00:00Z"),  # 2시간
    _link(2, 20, "2026-08-02T00:00:00Z", "2026-08-02T06:00:00Z"),  # 6시간
]


def _source(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": SOURCE,
        "repository_full_name": "acme/billing",
        "workflow_repository_id": "billing",
        "label_filter": ["bug"],
        "selected_issue_numbers": [],
        "start_at": "2026-10-06T00:00:00Z",
        "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": "agent-claude-mac",
        "run_mode": "auto",
        "max_rework_rounds": 1,
        "enabled": True,
        "config_revision": 1,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


def _advance(conn, store, session_id: str, execution_id: str, *, ok: bool, started: str, finished: str,
             usage=None) -> None:
    def apply(seq: int, type_: str, data: dict, at: str) -> None:
        repo.append_event(conn, execution_id, ExecutionEvent.model_validate(event(execution_id, seq, type_, data, at)),
                          actor="connector:conn-1", now=at)

    apply(1, "accepted", {}, started)
    apply(2, "started", {"runtime_ref": "pid:1"}, started)
    extra = {"usage": usage} if usage is not None else {}
    if ok:
        data = b'{"outcome": "ready_for_review"}'
        created, _ = repo.store_artifact(
            conn, store, execution_id=execution_id, session_id=session_id,
            meta=ArtifactMeta.model_validate(meta_for(data, kind="code_change_result", name="r.json",
                                                      content_type="application/json")),
            data=data, now=finished,
        )
        apply(3, "result_ready", {"result_artifact_id": created.artifact_id, **extra}, finished)
    else:
        apply(3, "failed", {"code": "timeout", "message": "시간 초과", "process_stopped": True, **extra}, finished)


def _task(task_id: str, session_id: str) -> dict:
    return {**task_row(task_id), "session_id": session_id}


@pytest.fixture
def operator(app, conn, store) -> tuple[TestClient, str]:
    """운영자 세션: Task 2개(각 실행 1개 — 성공(비용 0.5·토큰 모름)·실패(사용량 모름)), 설정 변경 1번 사이에 둠."""
    client = TestClient(app)
    session_id = login(client)
    repo.insert_work_item_task(conn, _task("task-1", session_id), "2026-09-20T00:00:00Z")
    seed_execution(conn, "exec-1", "task-1")
    _advance(conn, store, session_id, "exec-1", ok=True, started="2026-09-20T00:10:00Z", finished="2026-09-20T00:20:00Z",
             usage={"cost_usd": 0.5, "input_tokens": 100})
    repo.save_github_source(conn, session_id, _source(), OPENED_BEFORE)  # config_revision 1 → 2
    repo.insert_work_item_task(conn, _task("task-2", session_id), "2026-09-25T00:00:00Z")
    repo.create_execution(conn, execution_id="exec-2", task_id="task-2", attempt_no=2, start_key="rework:1",
                          agent_id="agent-codex-mac", kind="bug_fix",
                          request=ExecutionRequest.model_validate(request_body("exec-2", "task-2")), assigned_connector_id=None,
                          predecessor_execution_id=None, now="2026-09-25T00:00:00Z")
    _advance(conn, store, session_id, "exec-2", ok=False, started="2026-09-25T00:00:00Z", finished="2026-09-25T00:30:00Z")
    return client, session_id


@pytest.fixture
def op(operator) -> TestClient:
    return operator[0]


@pytest.fixture
def fake(app) -> FakeGitHub:
    app.state.github_client = FakeGitHub(LINKS)
    return app.state.github_client


def error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    data = response.json()
    assert data["code"] == code, data
    return data


# --- 인증·세션 범위 -------------------------------------------------------------------------


def test_every_endpoint_requires_operator_session(client, conn):
    # 워크스페이스가 아닌 세션 행을 서명한 쿠키 — 셀프호스트에서는 로그인 안 된 것으로 본다
    repo.create_session(conn, "sess-other", "2026-09-20T00:00:00Z")
    client.cookies.set(SESSION_COOKIE, sign_session("sess-other", "test-session-secret"))
    for anonymous in (TestClient(client.app), client):
        error(anonymous.get("/metrics.json"), 401, "unauthenticated")
        error(anonymous.get("/metrics.csv"), 401, "unauthenticated")
        error(anonymous.post(f"/operator/github/sources/{SOURCE}/baseline"), 401, "unauthenticated")


def test_other_operator_session_sees_only_its_own_data(operator, conn, store, fake):
    op, _ = operator
    op.post(f"/operator/github/sources/{SOURCE}/baseline")
    before_json, before_csv = op.get("/metrics.json").json(), op.get("/metrics.csv").text
    # 다른 워크스페이스(운영자 세션)의 업무·실행·소스·기준선 — DB 에 직접 둔다
    other, other_source = "sess-other", "ghs-0000beef"
    repo.create_session(conn, other, "2026-09-20T00:00:00Z")
    repo.mark_operator(conn, other)
    repo.insert_work_item_task(conn, _task("task-other", other), "2026-09-21T00:00:00Z")
    seed_execution(conn, "exec-other", "task-other")
    _advance(conn, store, other, "exec-other", ok=True, started="2026-09-21T00:10:00Z",
             finished="2026-09-21T00:20:00Z", usage={"cost_usd": 9.0, "input_tokens": 900})
    repo.save_github_source(conn, other, _source(source_id=other_source, repository_full_name="acme/lib"),
                            OPENED_BEFORE)
    repo.replace_baseline(conn, other, other_source, [_link(3, 30, "2026-08-03T00:00:00Z", "2026-08-03T01:00:00Z")],
                          opened_before=OPENED_BEFORE, now="2026-09-21T00:00:00Z")
    assert len(repo.list_baseline(conn, other, other_source)[1]) == 1

    assert op.get("/metrics.json").json() == before_json
    assert op.get("/metrics.csv").text == before_csv
    assert "이슈 3" not in op.get("/metrics.csv").text
    error(op.post(f"/operator/github/sources/{other_source}/baseline"), 404, "not_found")
    assert fake.calls == [("acme/billing", OPENED_BEFORE)]


# --- JSON ---------------------------------------------------------------------------------


def test_metrics_json_reports_values_with_unknowns_apart(op):
    response = op.get("/metrics.json")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["from"], body["to"], body["group_by"]) == (None, None, None)
    [group] = body["groups"]
    assert group["key"] == "all" and group["bundles"] == 2
    assert group["execution_time"] == {"median": 1200.0, "n": 2, "incomplete": 0, "unknown": 0, "total": None}
    assert group["cost_usd"] == {"median": 0.5, "n": 1, "incomplete": 0, "unknown": 1, "total": 0.5}
    assert group["output_tokens"] == {"median": None, "n": 0, "incomplete": 0, "unknown": 2, "total": None}
    assert group["failure"] == {"numerator": 1, "denominator": 2, "rate": 0.5, "incomplete": 0, "unknown": 0}
    assert group["failed_codes"] == {"timeout": 1}
    assert group["reruns"]["numerator"] == 1
    assert group["first_pass"]["rate"] is None  # 검토 결과가 없다 — 0 이 아니라 모름
    assert set(group["handoff_blocked"]) == {"operator", "assignee", "system"}


def test_metrics_json_filters_by_period_and_groups_by_config_revision(op):
    body = op.get("/metrics.json", params={"from": "2026-09-21T00:00:00Z", "to": "2026-10-01T00:00:00+09:00"}).json()
    assert (body["from"], body["to"]) == ("2026-09-21T00:00:00Z", "2026-10-01T00:00:00+09:00")
    [group] = body["groups"]
    assert (group["bundles"], group["failure"]["denominator"], group["failed_codes"]) == (1, 1, {"timeout": 1})

    body = op.get("/metrics.json", params={"group_by": "config_revision"}).json()
    assert body["group_by"] == "config_revision"
    assert [(g["key"], g["bundles"], g["cost_usd"]["total"]) for g in body["groups"]] == [("1", 1, 0.5), ("2", 1, None)]


GH_ISSUE_ID = 5001


def seed_github_bundle(conn, session_id: str) -> None:
    """닫힌 원본 이슈 #7 에서 온 묶음: 열림 09-26 00:00 → 운영자 승인 02:00. 병합은 아직 모름."""
    snapshot = GitHubIssueSnapshot.model_validate({
        "repository_id": 700112233, "repository_full_name": "acme/billing", "issue_id": GH_ISSUE_ID, "number": 7,
        "title": "버그 7", "body": "재현", "state": "closed", "labels": ["bug"], "assignee_ids": [],
        "assignee_logins": [], "html_url": "https://github.com/acme/billing/issues/7",
        "created_at": "2026-09-26T00:00:00Z", "updated_at": "2026-09-26T05:00:00Z", "is_pull_request": False,
    })
    repo.upsert_source_issue(conn, session_id, SOURCE, snapshot, task=_task("task-gh", session_id),
                             now="2026-09-26T00:01:00Z")
    repo.update_task_status(conn, "task-gh", "완료", "검토 승인", finished_at="2026-09-26T02:00:00Z",
                            review_decision="approve", now="2026-09-26T02:00:00Z")


def record_merge(conn, session_id: str, link: IssuePrLink | None, now: str) -> None:
    repo.record_issue_merge(conn, session_id=session_id, source_id=SOURCE, github_issue_id=GH_ISSUE_ID, link=link,
                            now=now)


MERGE_7 = IssuePrLink(issue_number=7, issue_title="버그 7", issue_opened_at="2026-09-26T00:00:00Z", pr_number=70,
                      pr_merged_at="2026-09-26T04:00:00Z")


def test_github_bundle_is_done_at_merge_not_at_approval(operator, conn):
    """GitHub 이슈 묶음의 접수 → 완료는 병합 시각(ADR-0015 결정 9). 승인은 접수 → 승인 으로 따로."""
    op, session_id = operator
    seed_github_bundle(conn, session_id)
    params = {"from": "2026-09-26T00:00:00Z"}

    [group] = op.get("/metrics.json", params=params).json()["groups"]
    assert group["intake_to_done"] == {"median": None, "n": 0, "incomplete": 1, "unknown": 0, "total": None}
    assert group["intake_to_merge"]["incomplete"] == 1
    assert (group["intake_to_approval"]["median"], group["intake_to_approval"]["n"]) == (7200.0, 1)

    record_merge(conn, session_id, None, "2026-09-26T03:00:00Z")  # 조회했지만 병합 없음
    [group] = op.get("/metrics.json", params=params).json()["groups"]
    assert group["closed_unmerged"] == 1

    record_merge(conn, session_id, MERGE_7, "2026-09-26T06:00:00Z")
    [group] = op.get("/metrics.json", params=params).json()["groups"]
    assert (group["intake_to_done"]["median"], group["intake_to_done"]["n"]) == (14400.0, 1)
    assert (group["intake_to_merge"]["median"], group["intake_to_merge"]["n"]) == (14400.0, 1)
    assert group["closed_unmerged"] == 0


@pytest.mark.parametrize("params", [
    {"group_by": "agent"},
    {"from": "어제"},
    {"to": "2026-09-21"},  # 시간대 없는 날짜
    {"from": "2026-09-21T00:00:00Z", "to": "2026-09-21T00:00:00Z"},  # 빈 기간
    {"from": "2026-09-22T00:00:00Z", "to": "2026-09-21T00:00:00Z"},
])
def test_invalid_parameters_are_rejected(op, params):
    for path in ("/metrics.json", "/metrics.csv"):
        error(op.get(path, params=params), 422, "invalid_field")


# --- CSV ----------------------------------------------------------------------------------


def _rows(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(response.text)))


def test_metrics_csv_has_one_row_per_metric_with_blank_unknowns(op):
    rows = _rows(op.get("/metrics.csv"))
    assert list(rows[0]) == ["group", "area", "metric", "unit", "median", "n", "incomplete", "unknown", "total",
                             "numerator", "denominator", "note"]
    by_metric = {r["metric"]: r for r in rows if r["group"] == "all"}
    assert by_metric["cost_usd"] == {
        "group": "all", "area": "cost", "metric": "cost_usd", "unit": "usd", "median": "0.5", "n": "1",
        "incomplete": "0", "unknown": "1", "total": "0.5", "numerator": "", "denominator": "", "note": "",
    }
    assert by_metric["output_tokens"]["median"] == "" and by_metric["output_tokens"]["total"] == ""
    assert (by_metric["failure"]["numerator"], by_metric["failure"]["denominator"], by_metric["failure"]["median"]) \
        == ("1", "2", "")
    assert by_metric["failed_code:timeout"]["total"] == "1"
    assert by_metric["execution_time"]["unit"] == "seconds" and by_metric["execution_time"]["median"] == "1200.0"
    assert {"handoff_wait", "handoff_blocked:operator", "intake_to_human", "intake_to_done", "interventions",
            "response_time", "first_pass", "rework", "human_rejection", "input_tokens", "reruns",
            "bundles", "done_by_finished_at", "closed_failed", "intake_to_merge", "intake_to_approval",
            "closed_unmerged"} <= set(by_metric)
    assert by_metric["intake_to_approval"]["area"] == "speed" and by_metric["closed_unmerged"]["unit"] == "count"


def test_metrics_csv_follows_the_same_parameters(op):
    rows = _rows(op.get("/metrics.csv", params={"group_by": "config_revision"}))
    assert {r["group"] for r in rows} == {"1", "2", f"baseline:{SOURCE}"}  # 기준선은 그룹과 무관한 행 하나


# --- 기준선 가져오기 ------------------------------------------------------------------------


def test_baseline_import_uses_source_created_at_and_is_idempotent(op, fake, conn, operator):
    response = op.post(f"/operator/github/sources/{SOURCE}/baseline")
    assert response.status_code == 200, response.text
    assert response.json() == {"source_id": SOURCE, "opened_before": OPENED_BEFORE, "item_count": 2}
    assert fake.calls == [("acme/billing", OPENED_BEFORE)]
    revision = repo.get_config_revision(conn, operator[1])

    again = op.post(f"/operator/github/sources/{SOURCE}/baseline")
    assert again.json() == response.json()
    assert len(repo.list_baseline(conn, operator[1], SOURCE)[1]) == 2
    assert repo.get_config_revision(conn, operator[1]) == revision  # 관측 이력이라 설정 번호 불변

    [baseline] = op.get("/metrics.json").json()["baselines"]
    assert baseline["source_id"] == SOURCE and baseline["repository_full_name"] == "acme/billing"
    assert baseline["opened_before"] == OPENED_BEFORE and baseline["fetched_at"] is not None
    assert baseline["intake_to_merge"]["median"] == 4 * 3600 and baseline["intake_to_merge"]["n"] == 2
    assert baseline["note"] == BASELINE_NOTE

    [row] = [r for r in _rows(op.get("/metrics.csv")) if r["group"] == f"baseline:{SOURCE}"]
    assert (row["metric"], row["median"], row["n"], row["note"]) == ("intake_to_merge", "14400.0", "2", BASELINE_NOTE)


def test_baseline_before_import_is_shown_as_unknown(op):
    [baseline] = op.get("/metrics.json").json()["baselines"]
    assert baseline["imported"] is False
    assert (baseline["opened_before"], baseline["fetched_at"], baseline["intake_to_merge"]) == (None, None, None)
    assert baseline["note"] == BASELINE_NOTE


@pytest.mark.parametrize(("exc", "status", "code"), [
    (GitHubRateLimited("limit", retry_after_seconds=60, reset_epoch=None), 429, "github_rate_limited"),
    (GitHubForbidden("forbidden"), 502, "github_forbidden"),
    (GitHubUnavailable("down"), 502, "github_unavailable"),
])
def test_github_errors_keep_the_previous_baseline(app, op, fake, conn, operator, exc, status, code):
    op.post(f"/operator/github/sources/{SOURCE}/baseline")
    app.state.github_client = FakeGitHub(error=exc)
    data = error(op.post(f"/operator/github/sources/{SOURCE}/baseline"), status, code)
    if code == "github_rate_limited":
        assert data["details"] == {"retry_after_seconds": 60}
    assert len(repo.list_baseline(conn, operator[1], SOURCE)[1]) == 2


def test_baseline_needs_a_token_and_an_owned_source(app, settings, op, fake):
    error(op.post("/operator/github/sources/ghs-00000009/baseline"), 404, "not_found")
    app.state.settings = dataclasses.replace(settings, github_token="")
    app.state.github_client = None  # 가짜를 치우면 소스 자격 선택으로 — App·PAT·환경변수 토큰 모두 없음
    error(op.post(f"/operator/github/sources/{SOURCE}/baseline"), 409, "github_token_missing")
    assert fake.calls == []


def test_baseline_uses_the_source_credentials_without_env_token(app, settings, op, monkeypatch):
    """GitHub App 으로 연결한 소스는 WORKFLOW_GITHUB_TOKEN 없이 설치 토큰으로 가져온다(2026-09-27 실제 사용에서 발견)."""
    app.state.settings = dataclasses.replace(settings, github_token="")
    app.state.github_client = None
    seen = []

    def client_for(source, _settings, secrets, *, transport=None):
        seen.append((source.source_id, secrets is app.state.secrets))
        return FakeGitHub(LINKS)

    monkeypatch.setattr(metrics_api.github_clients, "client_for", client_for)
    response = op.post(f"/operator/github/sources/{SOURCE}/baseline")
    assert response.status_code == 200 and response.json()["item_count"] == 2
    assert seen == [(SOURCE, True)]


def test_responses_carry_no_token_or_paths(app, op, fake, settings):
    app.state.github_client = FakeGitHub(error=GitHubForbidden(f"Bearer {TOKEN}"))
    texts = [
        op.post(f"/operator/github/sources/{SOURCE}/baseline").text,
        op.get("/metrics.json").text,
        op.get("/metrics.csv").text,
    ]
    app.state.github_client = fake
    texts.append(op.post(f"/operator/github/sources/{SOURCE}/baseline").text)
    for text in texts:
        assert TOKEN not in text
        assert str(settings.db_path.parent) not in text and str(settings.artifact_dir) not in text
        assert "local-demo-report" not in text  # 로컬 등록(저장소 폴더) 식별자도 내지 않는다
    json.loads(texts[1])
