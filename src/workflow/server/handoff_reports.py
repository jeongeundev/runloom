"""인계 리포트 서버 쪽 — 시작·수집·판정·행동 (ADR-0033, ARCHITECTURE "인계 리포트 — phase 25").

step 4: PR 근거 수집(D22). 중앙이 GitHub 에서 업무에 연결된 PR 의 본문·커밋·리뷰 코멘트를 읽는다(GET 만). 읽기 실패면
PR 근거 전부 없이 사유만 남기고 예외를 내보내지 않는다. 사유·로그에 토큰·헤더·응답 본문을 넣지 않는다.
`server/worker.py` 를 import 하지 않는다(순환 import).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection

from workflow.adapters import repo
from workflow.adapters.github_client import (
    GitHubClient,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
)
from workflow.contracts.github import GitHubSourceConfig

log = logging.getLogger("workflow.handoff_reports")

PR_EVIDENCE_MAX_PULLS = 3
PR_BODY_MAX = 4000
PR_COMMITS_MAX = 50
PR_COMMIT_LINE_MAX = 200
PR_COMMENTS_MAX = 30
PR_COMMENT_MAX = 1000

# 앞에서부터 처음 맞는 것. 나머지 `GitHubError`(설치 토큰 발급 실패·응답 형식·허용 저장소 밖)는 "응답 형식 오류".
_READ_FAILURES: tuple[tuple[type[GitHubError], str], ...] = (
    (GitHubForbidden, "GitHub 가 거부함(Pull requests 읽기 권한 확인)"),
    (GitHubRateLimited, "GitHub 요청 한도"),
    (GitHubNotFound, "PR 을 찾을 수 없음"),
    (GitHubUnavailable, "GitHub 에 닿지 않음"),
)


@dataclass(frozen=True)
class PullEvidence:
    number: int
    title: str
    url: str
    body: str
    commits: tuple[tuple[str, str], ...]  # (sha, 메시지 첫 줄)
    comments: tuple[tuple[str, str, str], ...]  # (작성자, 위치 표시, 본문)


@dataclass(frozen=True)
class PrEvidence:
    pulls: tuple[PullEvidence, ...] = ()
    error: str | None = None


def _linked_pulls(conn: Connection, work_item_id: str) -> list[tuple[str, str, int]]:
    """업무의 감지 PR + 업무 단계의 Runloom PR(번호 있는 것) — (source_id, 저장소, 번호), 중복 없이 갱신 최근순 상한 개."""
    rows = conn.execute(
        "SELECT source_id, repository_full_name, pr_number, pr_updated_at AS at FROM work_pull_requests"
        " WHERE work_item_id = ?"
        " UNION ALL SELECT p.source_id, p.repository_full_name, p.pr_number, p.updated_at FROM task_pull_requests p"
        " JOIN tasks t ON t.task_id = p.task_id WHERE t.work_item_id = ? AND p.pr_number IS NOT NULL"
        " ORDER BY at DESC",
        (work_item_id, work_item_id),
    ).fetchall()
    pulls: dict[tuple[str, int], tuple[str, str, int]] = {}
    for row in rows:
        key = (row["repository_full_name"].lower(), row["pr_number"])
        if key not in pulls:
            pulls[key] = (row["source_id"], row["repository_full_name"], row["pr_number"])
    return list(pulls.values())[:PR_EVIDENCE_MAX_PULLS]


def _read_pull(client: GitHubClient, repository: str, number: int) -> PullEvidence:
    detail = client.get_pull_detail(repository, number)
    commits = client.list_pull_commits(repository, number)
    comments = client.list_pull_review_comments(repository, number)
    return PullEvidence(
        number=number,
        title=detail.title,
        url=f"https://github.com/{repository}/pull/{number}",  # 링크는 저장소 이름과 번호로 계산한다
        body=detail.body[:PR_BODY_MAX],
        commits=tuple((c.sha, c.message[:PR_COMMIT_LINE_MAX]) for c in commits[:PR_COMMITS_MAX]),
        comments=tuple(
            (c.author_login or "알 수 없음", c.path if c.line is None else f"{c.path}:{c.line}", c.body[:PR_COMMENT_MAX])
            for c in comments[:PR_COMMENTS_MAX]
        ),
    )


def collect_pr_evidence(
    conn: Connection, github_for: Callable[[GitHubSourceConfig], GitHubClient | None], *, work_item_id: str
) -> PrEvidence:
    """행을 만들 때(원본·다시 만들기) 부른다 — 트랜잭션 밖. PR 이 없으면 빈 값, 하나라도 읽기 실패면 PR 근거 없이 사유."""
    linked = _linked_pulls(conn, work_item_id)
    if not linked:
        return PrEvidence()
    work = conn.execute("SELECT session_id FROM work_items WHERE work_item_id = ?", (work_item_id,)).fetchone()
    clients: dict[str, GitHubClient | None] = {}
    pulls: list[PullEvidence] = []
    try:
        for source_id, repository, number in linked:
            if source_id not in clients:
                config = repo.get_github_source(conn, work["session_id"], source_id)
                clients[source_id] = github_for(config) if config is not None else None
            client = clients[source_id]
            if client is None:
                return PrEvidence(error="PR 읽기 실패 — GitHub 연결 없음")
            pulls.append(_read_pull(client, repository, number))
    except GitHubError as exc:
        reason = next((text for kind, text in _READ_FAILURES if isinstance(exc, kind)), "응답 형식 오류")
        log.warning("인계 리포트 PR 근거 읽기 실패 %s: %s", work_item_id, type(exc).__name__)
        return PrEvidence(error=f"PR 읽기 실패 — {reason}")
    return PrEvidence(pulls=tuple(pulls))
