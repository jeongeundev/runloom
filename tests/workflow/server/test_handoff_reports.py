"""handoff_reports — 인계 리포트 서버 쪽 (phase 25). step 4: PR 근거 수집(ARCHITECTURE "PR 근거 수집", D22)."""

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
from workflow.server import handoff_reports
from workflow.server.handoff_reports import PrEvidence, PullEvidence

from .test_task_cycle import (  # noqa: F401 — 픽스처
    NOW,
    SESSION,
    SOURCE,
    cycle,
    import_issue,
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
