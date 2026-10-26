# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·worker)를 가져와 인자로 쓴다
"""handoff_reports — 인계 리포트 서버 쪽 (phase 25). step 4: PR 근거 수집(ARCHITECTURE "PR 근거 수집", D22).
step 5: 판정 통과한 실행 → 원본 리포트 작업(`queue_original`)·시간 초과(`expire_reports`) — ARCHITECTURE "대상 판정 —
판정 지점 표"·"상태 전이". 여기서는 업무 순환(bug_fix·code_review)으로 본다 — 범용 종류는 test_worker, 접수 판단은
test_triage_judge, 결과 뒤 판단은 test_worker_next_step, 사내 요청 조사는 test_internal_investigation.
"""

import json
import logging

import httpx
import pytest

from workflow.adapters import repo
from workflow.adapters.github_client import (
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    HttpGitHubClient,
    InstallationTokenProvider,
    PullCommit,
    PullDetail,
    PullReviewComment,
)
from workflow.contracts.v1 import RUNNER_CAPABILITY_HANDOFF_REPORT
from workflow.server import handoff_reports
from workflow.server.handoff_reports import PrEvidence, PullEvidence
from workflow.server.worker import _plus_seconds

from .test_task_cycle import (  # noqa: F401 — 픽스처
    BASE,
    C1,
    FIX,
    NOW,
    REG_FIX,
    REG_REVIEW,
    REVIEW,
    SESSION,
    SOURCE,
    _to_first_review,
    clock,
    cycle,
    executions,
    finish_fix,
    finish_review,
    import_issue,
    make_worker,
    review_tasks,
    settings,
    worker,
)

REPO = "acme/billing"

pytestmark = pytest.mark.usefixtures("cycle")


class FakeGitHub:
    """PR 근거 세 메서드만. `fail` 에 (메서드, 번호) → 예외를 넣으면 그 호출이 던진다."""

    def __init__(self, *, body: str = "본문", commits: int = 1, comments: int = 1, fail: dict | None = None):
        self.body, self.commits, self.comments = body, commits, comments
        self.fail = fail or {}
        self.calls: list[tuple[str, str, int]] = []

    def _call(self, name: str, repo_name: str, number: int) -> None:
        self.calls.append((name, repo_name, number))
        if (name, number) in self.fail:
            raise self.fail[(name, number)]

    def get_pull_detail(self, repo_name: str, number: int) -> PullDetail:
        self._call("detail", repo_name, number)
        return PullDetail(number=number, title=f"PR {number}", body=self.body,
                          html_url=f"https://evil.example/{number}", state="open", draft=False, merged_at=None,
                          head_sha="d" * 40)

    def list_pull_commits(self, repo_name: str, number: int) -> list[PullCommit]:
        self._call("commits", repo_name, number)
        return [PullCommit(sha=f"{i:040x}", message=f"fix: {i}" + "가" * 300) for i in range(self.commits)]

    def list_pull_review_comments(self, repo_name: str, number: int) -> list[PullReviewComment]:
        self._call("comments", repo_name, number)
        return [
            PullReviewComment(author_login="rev" if i % 2 == 0 else None, path="src/a.py", line=i or None,
                              body="나" * 1500, created_at=NOW)
            for i in range(self.comments)
        ]


def _work_id(conn, task_id: str) -> str:
    return repo.get_task(conn, task_id)["work_item_id"]


def _detect(conn, work_item_id: str, number: int, updated_at: str) -> None:
    repo.upsert_work_pull_request(
        conn, session_id=SESSION, work_item_id=work_item_id, source_id=SOURCE, repository_full_name=REPO,
        pr_number=number, title=f"감지 {number}", head_branch=f"RUN-{number}", state="open", draft=False,
        author_login="kim-dev", merged_at=None, pr_updated_at=updated_at, matched_in="head", now=NOW,
    )


def _runloom_pr(conn, task_id: str, number: int | None, updated_at: str, state: str = "open") -> None:
    conn.execute(
        "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
        " head_branch, fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 1, ?, 'exec-fix', 'exec-rev', ?, ?, ?, ?)",
        (task_id, SESSION, SOURCE, REPO, f"task/{task_id}", state, number, NOW, updated_at),
    )


def _collect(conn, task_id: str, github) -> PrEvidence:
    return handoff_reports.collect_pr_evidence(conn, lambda _config: github, work_item_id=_work_id(conn, task_id))


def test_no_linked_pr_is_empty_without_calling_github(conn):
    task_id = import_issue(conn, 1)
    github = FakeGitHub()
    _runloom_pr(conn, task_id, None, NOW, state="pending")  # 아직 번호 없는 Runloom PR 은 대상이 아니다

    assert _collect(conn, task_id, github) == PrEvidence(pulls=(), error=None)
    assert github.calls == []


def test_linked_prs_are_read_in_order_with_body_commits_and_comments(conn):
    task_id = import_issue(conn, 1)
    work_item_id = _work_id(conn, task_id)
    _runloom_pr(conn, task_id, 31, "2026-10-06T13:00:00Z")
    _detect(conn, work_item_id, 77, "2026-10-06T14:00:00Z")
    github = FakeGitHub(commits=2, comments=2)

    evidence = _collect(conn, task_id, github)

    assert evidence.error is None
    assert [p.number for p in evidence.pulls] == [77, 31]  # 갱신 최근순
    assert github.calls == [
        ("detail", REPO, 77), ("commits", REPO, 77), ("comments", REPO, 77),
        ("detail", REPO, 31), ("commits", REPO, 31), ("comments", REPO, 31),
    ]
    first = evidence.pulls[0]
    assert first == PullEvidence(
        number=77, title="PR 77", url=f"https://github.com/{REPO}/pull/77", body="본문",
        commits=((f"{0:040x}", "fix: 0" + "가" * 194), (f"{1:040x}", "fix: 1" + "가" * 194)),
        comments=(("rev", "src/a.py", "나" * 1000), ("알 수 없음", "src/a.py:1", "나" * 1000)),
    )


def test_same_pr_from_both_tables_is_read_once_and_only_three_recent(conn):
    task_id = import_issue(conn, 1)
    work_item_id = _work_id(conn, task_id)
    _runloom_pr(conn, task_id, 10, "2026-10-06T19:00:00Z")
    for number, hour in ((10, 13), (11, 14), (12, 15), (13, 16), (14, 12)):
        _detect(conn, work_item_id, number, f"2026-10-06T{hour}:00:00Z")
    github = FakeGitHub()

    evidence = _collect(conn, task_id, github)

    assert handoff_reports.PR_EVIDENCE_MAX_PULLS == 3
    assert [p.number for p in evidence.pulls] == [10, 13, 12]
    assert [call[2] for call in github.calls if call[0] == "detail"] == [10, 13, 12]


def test_long_values_are_cut_to_the_architecture_limits(conn):
    task_id = import_issue(conn, 1)
    _detect(conn, _work_id(conn, task_id), 77, NOW)

    evidence = _collect(conn, task_id, FakeGitHub(body="x" * 9000, commits=80, comments=45))

    pull = evidence.pulls[0]
    assert (handoff_reports.PR_BODY_MAX, handoff_reports.PR_COMMITS_MAX, handoff_reports.PR_COMMENTS_MAX,
            handoff_reports.PR_COMMENT_MAX) == (4000, 50, 30, 1000)
    assert len(pull.body) == 4000
    assert len(pull.commits) == 50 and all(len(message) <= 200 for _sha, message in pull.commits)
    assert len(pull.comments) == 30 and all(len(body) == 1000 for _author, _where, body in pull.comments)


@pytest.mark.parametrize(("exc", "reason"), [
    (GitHubForbidden("GET x: HTTP 403"), "GitHub 가 거부함(Pull requests 읽기 권한 확인)"),
    (GitHubRateLimited("GET x: HTTP 429", 12, None), "GitHub 요청 한도"),
    (GitHubNotFound("GET x: HTTP 404"), "PR 을 찾을 수 없음"),
    (GitHubUnavailable("GET x: ConnectError"), "GitHub 에 닿지 않음"),
    (GitHubError("POST /app/installations/1/access_tokens: 응답 형식 오류"), "응답 형식 오류"),
])
def test_read_failure_drops_all_pr_evidence_with_a_reason(conn, exc, reason):
    task_id = import_issue(conn, 1)
    work_item_id = _work_id(conn, task_id)
    _detect(conn, work_item_id, 77, "2026-10-06T14:00:00Z")
    _detect(conn, work_item_id, 78, "2026-10-06T13:00:00Z")

    # 첫 PR 은 다 읽었어도 둘째 PR 에서 실패하면 PR 근거 전부 없이
    evidence = _collect(conn, task_id, FakeGitHub(fail={("comments", 78): exc}))

    assert evidence == PrEvidence(pulls=(), error=f"PR 읽기 실패 — {reason}")


def test_no_client_for_the_source_is_a_read_failure(conn):
    task_id = import_issue(conn, 1)
    _detect(conn, _work_id(conn, task_id), 77, NOW)

    assert _collect(conn, task_id, None) == PrEvidence(pulls=(), error="PR 읽기 실패 — GitHub 연결 없음")


class _InstallationAuth:
    def __init__(self, token: str):
        self.value = token

    def installation_token(self, installation_id: int) -> str:
        return self.value

    def invalidate(self, installation_id: int) -> None:
        pass


TOKEN = "ghs_INSTALLATIONSECRET0123456789"


def _http_client(handler) -> HttpGitHubClient:
    provider = InstallationTokenProvider(_InstallationAuth(TOKEN), 42)
    return HttpGitHubClient(provider, [REPO], transport=httpx.MockTransport(handler))


def _github_ok(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/commits"):
        return httpx.Response(200, json=[{"sha": "e" * 40, "commit": {"message": "fix: 하나"}}])
    if path.endswith("/comments"):
        return httpx.Response(200, json=[{"user": {"login": "rev"}, "path": "a.py", "line": 3, "body": "확인",
                                          "created_at": NOW}])
    return httpx.Response(200, json={"number": 77, "title": "고침", "body": None, "state": "open", "draft": False,
                                     "merged_at": None, "html_url": f"https://github.com/{REPO}/pull/77",
                                     "head": {"sha": "f" * 40}})


@pytest.mark.parametrize("handler", [
    _github_ok,
    lambda request: httpx.Response(403, json={"message": f"bad credentials {TOKEN}"}),
])
def test_installation_token_and_authorization_header_never_leave_the_client(conn, caplog, handler):
    task_id = import_issue(conn, 1)
    _detect(conn, _work_id(conn, task_id), 77, NOW)
    caplog.set_level(logging.DEBUG)

    evidence = _collect(conn, task_id, _http_client(handler))

    assert evidence.pulls or evidence.error == "PR 읽기 실패 — GitHub 가 거부함(Pull requests 읽기 권한 확인)"
    for text in (repr(evidence), caplog.text):
        assert TOKEN not in text and "Bearer" not in text and "authorization" not in text.lower()


# --- step 5: 판정 통과 → 원본 리포트 작업 ------------------------------------------------------


def capable(conn, connector_id: str) -> None:
    """러너가 마지막 claim 에 리포트 능력을 보고했다."""
    repo.record_runner_capabilities(conn, connector_id, [RUNNER_CAPABILITY_HANDOFF_REPORT])
    conn.commit()


def reports(conn, execution_id: str) -> list:
    return conn.execute("SELECT * FROM handoff_reports WHERE source_execution_id = ? ORDER BY created_at, rowid",
                        (execution_id,)).fetchall()


def fix_passed(conn, store, worker, **finish) -> tuple[str, str]:
    """수정 단계 착수 → 결과 → 판정 tick. 반환은 (수정 Task, 실행)."""
    task_id = import_issue(conn, 1)
    worker.tick()
    execution_id = executions(conn, task_id)[0]["execution_id"]
    finish_fix(conn, store, execution_id, **finish)
    worker.tick()
    return task_id, execution_id


def test_passed_fix_result_queues_one_original_report_with_inputs(cycle, conn, store, worker):
    capable(conn, cycle["billing"])

    task_id, execution_id = fix_passed(conn, store, worker)

    (row,) = reports(conn, execution_id)
    assert (row["job_type"], row["status"], row["reason"]) == ("original", "queued", None)
    assert (row["agent_id"], row["connector_id"], row["source_task_id"]) == (FIX, cycle["billing"], task_id)
    assert row["work_item_id"] == _work_id(conn, task_id) and row["created_at"] == NOW
    inputs = json.loads(row["inputs_json"])
    assert (inputs["local_registration_id"], inputs["checkout_commit"], inputs["base_commit"]) == (REG_FIX, C1, BASE)
    assert (inputs["pull_requests"], inputs["pr_evidence_error"]) == ([], None)
    evidence = inputs["evidence"]
    # 판정 때의 산출물 — 검토 착수가 뒤에 원인 실행에 매다는 인계 묶음은 들어가지 않는다
    own = {a["artifact_id"] for a in repo.artifacts_of(conn, execution_id) if a["kind"] != "handoff_bundle"}
    assert set(evidence["artifacts"]) == own
    assert evidence["commits"] == sorted([BASE, C1])
    assert (evidence["pulls"], evidence["source_work"], evidence["verification"], evidence["claims"]) == (
        [], True, True, [])
    request = inputs["request"]
    assert request.startswith("# 인계 리포트: RUN-")
    assert "## 업무 원문 (source:work)" in request and "outcome: ready_for_review" in request
    assert f"- commit:{C1}" in request and "연결된 PR 없음" in request
    assert all(f"- artifact:{a}" in request for a in own)
    # 후속 착수는 리포트를 기다리지 않는다 — 같은 tick 에 검토가 착수했다(대기는 claim 에서, step 6)
    (review,) = review_tasks(conn, task_id)
    assert len(executions(conn, review["task_id"])) == 1


def test_failed_fix_verdict_makes_no_report(cycle, conn, store, worker):
    capable(conn, cycle["billing"])
    _task_id, execution_id = fix_passed(conn, store, worker, before_exit=0)
    assert reports(conn, execution_id) == []


def test_needs_information_fix_result_queues_a_report(cycle, conn, store, worker):
    capable(conn, cycle["billing"])
    _task_id, execution_id = fix_passed(conn, store, worker, outcome="needs_information", kinds=())
    (row,) = reports(conn, execution_id)
    inputs = json.loads(row["inputs_json"])
    assert row["status"] == "queued"
    # 결과 커밋이 없으면 기준 커밋을 체크아웃한다 — base 는 같아서 null
    assert (inputs["checkout_commit"], inputs["base_commit"]) == (BASE, None)
    assert inputs["evidence"]["verification"] is False


def test_passed_review_result_queues_a_report_checking_out_the_reviewed_commit(cycle, conn, store, worker):
    capable(conn, cycle["billing"])
    _fix_task, _fix_exec, review_task, review_exec = _to_first_review(conn, store, worker)
    finish_review(conn, store, review_exec, outcome="changes_requested")

    worker.tick()

    (row,) = reports(conn, review_exec)
    inputs = json.loads(row["inputs_json"])
    assert (row["status"], row["agent_id"], row["source_task_id"]) == ("queued", REVIEW, review_task)
    assert (inputs["local_registration_id"], inputs["checkout_commit"], inputs["base_commit"]) == (REG_REVIEW, C1, BASE)
    # 입력 인계 묶음과 그 안의 선행 산출물도 확인 가능한 근거다
    request_ids = set(json.loads(repo.get_execution(conn, review_exec)["request_json"])["input_artifact_ids"])
    assert request_ids <= set(inputs["evidence"]["artifacts"])


def test_same_verdict_twice_keeps_one_row(cycle, conn, store, worker, make_worker, settings):
    capable(conn, cycle["billing"])
    _task_id, execution_id = fix_passed(conn, store, worker)
    worker.tick()
    make_worker().tick()

    (row,) = reports(conn, execution_id)
    again = handoff_reports.queue_original(conn, store, settings, lambda _c: None, execution_id=execution_id,
                                           now="2026-10-06T13:00:00Z")
    assert again["report_id"] == row["report_id"]
    assert len(reports(conn, execution_id)) == 1


def test_runner_without_the_capability_records_unavailable(cycle, conn, store, worker):
    _task_id, execution_id = fix_passed(conn, store, worker)  # 러너가 능력을 보고하지 않음(NULL)

    (row,) = reports(conn, execution_id)
    assert (row["status"], row["reason"], row["finished_at"]) == (
        "unavailable", "러너가 리포트를 지원하지 않음 — 러너 재설치 필요", NOW)
    assert (row["agent_id"], row["connector_id"]) == (FIX, cycle["billing"])


def _queue_again(conn, store, settings, execution_id: str):
    conn.execute("DELETE FROM handoff_reports WHERE source_execution_id = ?", (execution_id,))
    conn.commit()
    return handoff_reports.queue_original(conn, store, settings, lambda _c: None, execution_id=execution_id, now=NOW)


def test_api_agent_and_deleted_agent_and_revoked_runner_are_unavailable(cycle, conn, store, worker, settings):
    capable(conn, cycle["billing"])
    _task_id, execution_id = fix_passed(conn, store, worker)

    conn.execute("UPDATE connectors SET revoked_at = ? WHERE connector_id = ?", (NOW, cycle["billing"]))
    assert _queue_again(conn, store, settings, execution_id)["reason"] == "러너 연결 없음"
    conn.execute("UPDATE agents SET connection_type = 'api' WHERE agent_id = ?", (FIX,))
    assert _queue_again(conn, store, settings, execution_id)["reason"] == "API 연결형 에이전트"
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM agents WHERE agent_id = ?", (FIX,))
    row = _queue_again(conn, store, settings, execution_id)
    assert (row["status"], row["reason"], row["agent_id"], row["connector_id"]) == (
        "unavailable", "원인 에이전트 없음", FIX, None)


def test_pr_evidence_is_collected_when_the_row_is_made(cycle, conn, store, worker, settings):
    capable(conn, cycle["billing"])
    task_id, execution_id = fix_passed(conn, store, worker)
    _detect(conn, _work_id(conn, task_id), 77, NOW)
    conn.execute("DELETE FROM handoff_reports")
    conn.commit()

    row = handoff_reports.queue_original(conn, store, settings, lambda _c: FakeGitHub(commits=1),
                                         execution_id=execution_id, now=NOW)

    inputs = json.loads(row["inputs_json"])
    assert inputs["pull_requests"] == [{"number": 77, "title": "PR 77", "url": f"https://github.com/{REPO}/pull/77"}]
    assert inputs["pr_evidence_error"] is None
    assert inputs["evidence"]["pulls"] == [77]
    assert f"{0:040x}" in inputs["evidence"]["commits"]
    assert "### pr:77 PR 77" in inputs["request"]


def test_pr_read_failure_keeps_the_report_without_pr_evidence(cycle, conn, store, worker):
    capable(conn, cycle["billing"])
    task_id = import_issue(conn, 1)
    _detect(conn, _work_id(conn, task_id), 77, NOW)
    worker.tick()
    execution_id = executions(conn, task_id)[0]["execution_id"]
    finish_fix(conn, store, execution_id)

    worker.tick()  # 이 워커에는 GitHub 클라이언트가 없다

    (row,) = reports(conn, execution_id)
    inputs = json.loads(row["inputs_json"])
    assert (row["status"], inputs["pull_requests"], inputs["evidence"]["pulls"]) == ("queued", [], [])
    assert inputs["pr_evidence_error"] == "PR 읽기 실패 — GitHub 연결 없음"
    assert "PR 읽기 실패 — GitHub 연결 없음" in inputs["request"]


# --- step 5: 시간 초과 --------------------------------------------------------------------


def test_queued_and_leased_reports_time_out_at_the_wait_boundary(cycle, conn, store, worker, clock):
    capable(conn, cycle["billing"])
    clock.now = _plus_seconds(NOW, 0)  # `utc_now` 표기(마이크로초) — 실제 워커 시각과 같은 모양이어야 경계를 문자열로 비교한다
    _task_id, execution_id = fix_passed(conn, store, worker)
    (row,) = reports(conn, execution_id)
    assert repo.lease_handoff_report(conn, cycle["billing"], now=NOW)["status"] == "leased"

    clock.now = _plus_seconds(NOW, 1799)
    worker.tick()
    assert reports(conn, execution_id)[0]["status"] == "leased"

    clock.now = _plus_seconds(NOW, 1800)  # 정확히 30분이면 시간 초과(경계 포함)
    worker.tick()
    (row,) = reports(conn, execution_id)
    assert (row["status"], row["reason"], row["finished_at"]) == ("timed_out", "30분 안에 리포트가 오지 않음", clock.now)


def test_expire_uses_the_setting_and_leaves_finished_reports(cycle, conn, store, worker, settings, clock):
    import dataclasses

    capable(conn, cycle["billing"])
    clock.now = _plus_seconds(NOW, 0)
    _task_id, execution_id = fix_passed(conn, store, worker)
    (row,) = reports(conn, execution_id)
    result_artifact = repo.get_execution(conn, execution_id)["result_artifact_id"]
    conn.execute("UPDATE handoff_reports SET status = 'done', artifact_id = ?, finished_at = ? WHERE report_id = ?",
                 (result_artifact, NOW, row["report_id"]))
    conn.commit()
    short = dataclasses.replace(settings, handoff_report_wait_seconds=120)

    assert handoff_reports.expire_reports(conn, short, now=_plus_seconds(NOW, 7200)) == 0
    assert reports(conn, execution_id)[0]["status"] == "done"

    conn.execute("UPDATE handoff_reports SET status = 'queued', artifact_id = NULL, finished_at = NULL")
    conn.commit()
    assert handoff_reports.expire_reports(conn, short, now=_plus_seconds(NOW, 119)) == 0
    assert handoff_reports.expire_reports(conn, short, now=_plus_seconds(NOW, 120)) == 1
    assert reports(conn, execution_id)[0]["reason"] == "2분 안에 리포트가 오지 않음"


def test_handoff_reports_does_not_import_the_worker():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(handoff_reports))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names} | {node.module for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"worker", "web", "workflow.server.worker", "workflow.server.web"} & imported
