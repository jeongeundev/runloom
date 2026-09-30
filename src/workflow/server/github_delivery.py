"""원본 이슈 결과 반영 outbox — ADR-0014 결정 8, ARCHITECTURE "원본 반영 상태 (`SourceDelivery.state`)".

- `queue_source_updates` 는 원본 이슈에서 온 Task 마다 댓글 본문(요약·결과 커밋·검토·사람 요청·운영자 화면 링크)을
  만들어 `source_deliveries` 에 넣는다. 본문이 저장된 최신 revision 과 같으면 아무것도 만들지 않는다.
- `deliver_source_updates` 는 Task 당 최신 revision 하나만 보낸다(이전 revision 은 `pending` 으로 남고 보내지 않는다).
  본문 첫 줄의 marker `<!-- runloom:task=<task_id> -->` 와 저장한 `comment_id` 로 한 댓글을 만들고 고친다.
- HTTP 는 repo 트랜잭션 밖에서 한다. claim(`sending` + 만료 시각 + `attempts` fence)으로 여러 소비자 중 하나만 보내고,
  만료 뒤 늦게 끝난 소비자의 기록은 버린다.
- POST 응답을 잃으면(연결 오류·timeout·5xx, 또는 claim 이 만료된 `sending` = 전송 후 crash) `unknown` 으로 두고, 다시
  보내기 전에 댓글 목록에서 marker 를 찾는다: 찾으면 `delivered`, 전 페이지를 봤는데 없으면 `pending`, 조회 실패면 `unknown`.
  PATCH 는 같은 댓글을 덮어쓰므로 응답을 잃어도 다시 보낸다. 원격 exactly-once 는 주장하지 않는다 — marker 가 첫 줄인
  다른 댓글을 누가 만들면 그것을 우리 댓글로 볼 수 있고, 조회와 POST 사이에 생긴 댓글은 알 수 없다.
- 사람이 지운 댓글(PATCH 404)은 다시 만들지 않고 `failed` 다. 403·404·그 밖의 GitHub 거절도 `failed`, rate limit 은
  `pending` 으로 물러나고 이번 바퀴를 멈춘다. 반영 실패는 Task 상태·실행과 따로 남는다.
- 중지된 소스(`enabled=false`)에는 쓰지 않는다. PR 생성·푸시·병합·이슈 종료는 하지 않는다 — 클라이언트에 그런 동작이 없다.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import ArtifactMissing, NotFound
from workflow.adapters.github_client import (
    GitHubClient,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRepositoryNotAllowed,
    GitHubUnavailable,
)
from workflow.contracts.github import GitHubSourceConfig, SourceDelivery
from workflow.contracts.v1 import CodeChangeResult, CodeReviewResult, ExecutionRequest, format_work_key
from workflow.domain.execution_policy import policy_for
from workflow.domain.pull_request import head_branch, pr_title
from workflow.domain.work_keys import work_path

CLAIM_SECONDS = 120  # 한 claim 이 HTTP(댓글 목록 여러 페이지 포함)를 끝낼 시간. 지나면 전송 후 crash 로 본다
BACKOFF_SECONDS = 30  # n 번째 시도 실패 뒤 30·2^(n-1) 초, 최대 1시간
MAX_BACKOFF_SECONDS = 3600
MIN_RATE_LIMIT_SECONDS = 60  # 대기 시간이 없는 rate limit 은 최소 1분(GitHub 권고)
MAX_COMMENT_PAGES = 30  # marker 조회 상한(100개씩). 넘으면 조회 실패로 `unknown` 유지
SUMMARY_LIMIT = 500


def marker(task_id: str) -> str:
    return f"<!-- runloom:task={task_id} -->"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _plus_seconds(now: str, seconds: int) -> str:
    moved = (_parse(now) + timedelta(seconds=seconds)).astimezone(UTC)
    return moved.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _due(at: str | None, now: str) -> bool:
    return at is None or _parse(at) <= _parse(now)


def _backoff(now: str, attempts: int) -> str:
    return _plus_seconds(now, min(BACKOFF_SECONDS * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SECONDS))


def _rate_limit_until(now: str, exc: GitHubRateLimited) -> str:
    wait = max(MIN_RATE_LIMIT_SECONDS, exc.retry_after_seconds or 0)
    if exc.reset_epoch is not None:
        wait = max(wait, int(exc.reset_epoch - _parse(now).timestamp()))
    return _plus_seconds(now, wait)


# --- 본문 ------------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SUMMARY_LIMIT else flat[:SUMMARY_LIMIT - 1] + "…"


def _verified_result(conn: Connection, store: ArtifactStore, task_id: str) -> tuple[Row, bytes] | None:
    """판정을 통과한 가장 최근 결과 (실행, 결과 봉투 바이트)."""
    for execution in reversed(repo.list_executions(conn, task_id)):
        if execution["result_artifact_id"] is None:
            continue
        verdict = repo.get_verdict(conn, execution["execution_id"])
        if verdict is None or json.loads(verdict["verdict_json"]).get("outcome") != "passed":
            continue
        try:
            return execution, repo.read_artifact(conn, store, execution["result_artifact_id"])
        except (NotFound, ArtifactMissing):
            return None
    return None


def _result_lines(conn: Connection, store: ArtifactStore, task: Row) -> list[str]:
    verifier = policy_for(task["kind"]).verifier
    found = _verified_result(conn, store, task["task_id"])
    if found is None:
        return []
    execution, content = found
    try:
        if verifier == "code_change":
            fix = CodeChangeResult.model_validate_json(content)
            if fix.result_commit is None:
                return [f"- 수정: 정보 필요 — {_one_line(fix.summary)}"]
            request = ExecutionRequest.model_validate_json(execution["request_json"])
            branch = head_branch(task["task_id"], work_key=request.work_key, branch_seq=request.branch_seq)
            where = (  # 러너가 결과 브랜치를 origin 에 올렸는가 (ADR-0018 결정 4)
                f"  - 결과 브랜치 `{branch}` 를 원격에 올렸습니다. 검토 승인 뒤 초안 PR 을 엽니다."
                if execution["branch_pushed"] == 1
                else "  - 결과 커밋은 담당자의 로컬 저장소에만 있습니다. 자동으로 푸시하지 않습니다."
            )
            return [
                f"- 수정 결과(검증 통과): {_one_line(fix.summary)}",
                f"  - 기준 커밋 `{fix.base_commit}` → 결과 커밋 `{fix.result_commit}`",
                where,
            ]
        if verifier == "commit_review":
            review = CodeReviewResult.model_validate_json(content)
            blocking = sum(1 for f in review.findings if f.severity == "blocking")
            return [
                f"- 검토 결과: `{review.outcome}` — {_one_line(review.summary)}",
                f"  - 검토한 커밋 `{review.reviewed_commit}`, 차단 지적 {blocking}건",
            ]
    except (ValidationError, ValueError):
        return []
    return []


def source_update_body(conn: Connection, store: ArtifactStore, task: Row, public_url: str) -> str:
    """원본 이슈 댓글 본문. 시각을 넣지 않는다 — 같은 상태면 같은 본문이라 새 revision 이 생기지 않는다."""
    task_id = task["task_id"]
    work = repo.work_item_of_task(conn, task_id)
    work_key = format_work_key(work["key_number"])
    lines = [
        marker(task_id),
        f"### Runloom 작업 현황 — {pr_title(work_key, _one_line(task['title']))}",
        "",
        f"- 상태: **{task['status']}** · {task['status_reason']}",
        *_result_lines(conn, store, task),
    ]
    related = [task]
    for successor in repo.successors_of(conn, task_id):
        if not policy_for(successor["kind"]).cycle:
            continue
        related.append(successor)
        lines.append(f"- 후속 `{successor['kind']}`: **{successor['status']}** · {successor['status_reason']}")
        lines.extend(_result_lines(conn, store, successor))
    questions = [
        r["question"] for row in related for r in repo.list_human_requests(conn, row["task_id"]) if r["state"] == "open"
    ]
    lines.extend(f"- 사람 확인 필요: {_one_line(q)}" for q in questions)
    if questions:
        lines.append("  - 응답은 Runloom 운영자 화면에서 받습니다. 이 댓글에 답해도 반영되지 않습니다.")
    if public_url:
        lines.append(f"- 상세: {public_url}{work_path(work_key)} (Runloom 운영자 로그인이 필요합니다 — 공개 링크가 아닙니다)")
    else:
        lines.append(f"- 상세: Runloom 운영자 화면의 Task `{task_id}`")
    lines += ["", "_Runloom 은 PR 생성·푸시·병합·이슈 종료를 자동으로 하지 않습니다._"]
    return "\n".join(lines)


def queue_source_updates(conn: Connection, store: ArtifactStore, public_url: str, now: str) -> int:
    """켜진 소스의 원본 이슈 Task 중 실행이나 사람 요청이 생긴 것의 본문을 outbox 에 넣는다. 새로 만든 revision 수.
    아직 아무 일도 시작하지 않은 Task(담당 없음 대기 등)에는 댓글을 달지 않는다."""
    created = 0
    for session_id in repo.github_source_sessions(conn):
        for config in repo.list_github_sources(conn, session_id):
            if not config.enabled:
                continue
            for issue in repo.list_source_issues(conn, session_id, config.source_id):
                task = repo.get_task(conn, issue["task_id"])
                if not repo.list_executions(conn, task["task_id"]) and not repo.list_human_requests(
                    conn, task["task_id"]
                ):
                    continue
                _, new = repo.enqueue_source_delivery_once(
                    conn, task["task_id"], source_update_body(conn, store, task, public_url), now
                )
                created += new
    return created


# --- 전달 ------------------------------------------------------------------------------------


@dataclass
class DeliveryReport:
    created: int = 0  # 새 댓글 POST 성공
    updated: int = 0  # 기존 댓글 PATCH 성공
    reconciled: int = 0  # 응답을 잃은 POST 를 marker 로 찾음
    requeued: int = 0  # marker 가 없음을 확인해 다시 보낼 수 있게 됨
    uncertain: int = 0  # 반영 불확실(`unknown`)로 남음
    deferred: int = 0  # 일시 실패 — 물러났다가 다시
    failed: int = 0  # 반영 실패(403·404·거절·삭제된 댓글)
    rate_limited: bool = False  # 이번 바퀴를 멈췄다

    def any(self) -> bool:
        return any(asdict(self).values())


class _Stop(Exception):
    """rate limit — 같은 토큰의 다른 요청도 막히므로 이번 바퀴를 멈춘다."""


def _uncertain(row: SourceDelivery, now: str) -> bool:
    """POST 가 닿았는지 모른다: `unknown`, 또는 claim 이 만료된 POST(`comment_id` 없음)의 `sending`."""
    return row.state == "unknown" or (row.state == "sending" and row.comment_id is None and _due(row.next_at, now))


def deliver_source_updates(
    conn: Connection, client: GitHubClient, now: str, *, source_id: str | None = None
) -> DeliveryReport:
    """`source_id` 를 주면 그 소스의 반영만 보낸다 — 소스마다 자격(클라이언트)이 다를 수 있다(ADR-0017)."""
    report = DeliveryReport()
    for target in repo.delivery_targets(conn):
        if source_id is not None and target["source_id"] != source_id:
            continue
        if not GitHubSourceConfig.model_validate_json(target["config_json"]).enabled:
            continue
        try:
            _deliver_task(conn, client, target, now, report)
        except _Stop:
            report.rate_limited = True
            break
    return report


def _deliver_task(conn: Connection, client: GitHubClient, target: Row, now: str, report: DeliveryReport) -> None:
    rows = repo.list_source_deliveries(conn, target["task_id"])
    latest_id = rows[-1].delivery_id
    # 1) 닿았는지 모르는 POST 부터 조정한다. 그 전에는 이 Task 에 아무것도 보내지 않는다
    for row in rows:
        if _uncertain(row, now):
            if not _due(row.next_at, now) or not _reconcile(conn, client, target, row, now, report):
                return
        elif row.state == "sending":
            if not _due(row.next_at, now):
                return  # 다른 소비자가 보내는 중
            if row.delivery_id != latest_id:
                _supersede(conn, row, now)  # 중단된 이전 revision 의 PATCH — 최신 본문이 덮어쓴다
    # 2) 최신 revision 하나만 보낸다
    rows = repo.list_source_deliveries(conn, target["task_id"])
    latest = rows[-1]
    if latest.state not in ("pending", "sending") or not _due(latest.next_at, now):
        return
    comment_id = next((r.comment_id for r in reversed(rows) if r.comment_id is not None), None)
    attempts = repo.claim_source_delivery(
        conn, latest.delivery_id, expected_state=latest.state, now=now,
        claim_until=_plus_seconds(now, CLAIM_SECONDS), comment_id=comment_id, for_send=True,
    )
    if attempts is None:
        return
    body = _body_of(conn, latest.delivery_id)

    def record(state: str, *, comment: int | None = comment_id, next_at: str | None = None,
               error: str | None = None) -> None:
        repo.record_source_delivery(conn, latest.delivery_id, attempts=attempts, state=state, comment_id=comment,
                                    next_at=next_at, last_error=error, now=now)

    repository = target["repository_full_name"]
    try:
        if comment_id is not None:
            client.update_comment(repository, comment_id, body)
            record("delivered")
            report.updated += 1
        else:
            created = client.create_comment(repository, target["issue_number"], body)
            record("delivered", comment=created)
            report.created += 1
    except GitHubRateLimited as exc:
        record("pending", next_at=_rate_limit_until(now, exc), error=str(exc))
        raise _Stop from None
    except GitHubUnavailable as exc:
        if comment_id is None:  # 서버가 만들었을 수 있다 — 다음 바퀴에 marker 조회
            record("unknown", next_at=now, error=str(exc))
            report.uncertain += 1
        else:
            record("pending", next_at=_backoff(now, attempts), error=str(exc))
            report.deferred += 1
    except (GitHubForbidden, GitHubNotFound, GitHubRepositoryNotAllowed, GitHubError) as exc:
        record("failed", error=str(exc))
        report.failed += 1


def _body_of(conn: Connection, delivery_id: str) -> str:
    return conn.execute("SELECT body FROM source_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()["body"]


def _supersede(conn: Connection, row: SourceDelivery, now: str) -> None:
    attempts = repo.claim_source_delivery(
        conn, row.delivery_id, expected_state="sending", now=now, claim_until=_plus_seconds(now, CLAIM_SECONDS),
        comment_id=row.comment_id, for_send=False,
    )
    if attempts is not None:
        repo.record_source_delivery(conn, row.delivery_id, attempts=attempts, state="pending",
                                    comment_id=row.comment_id, next_at=None, last_error="새 revision 으로 대체",
                                    now=now)


def _reconcile(
    conn: Connection, client: GitHubClient, target: Row, row: SourceDelivery, now: str, report: DeliveryReport
) -> bool:
    """marker 로 POST 결과를 확인한다. 확인됐으면(찾음·없음) True, 아니면 False(이 Task 는 이번 바퀴에 더 보내지 않음)."""
    attempts = repo.claim_source_delivery(
        conn, row.delivery_id, expected_state=row.state, now=now, claim_until=_plus_seconds(now, CLAIM_SECONDS),
        comment_id=None, for_send=False,
    )
    if attempts is None:
        return False

    def record(state: str, *, comment: int | None = None, next_at: str | None = None, error: str | None = None) -> None:
        repo.record_source_delivery(conn, row.delivery_id, attempts=attempts, state=state, comment_id=comment,
                                    next_at=next_at, last_error=error, now=now)

    try:
        found = _find_marker(client, target["repository_full_name"], target["issue_number"], target["task_id"])
    except GitHubRateLimited as exc:
        record("unknown", next_at=_rate_limit_until(now, exc), error=str(exc))
        raise _Stop from None
    except (GitHubForbidden, GitHubNotFound, GitHubRepositoryNotAllowed) as exc:
        record("failed", error=str(exc))
        report.failed += 1
        return False
    except GitHubError as exc:
        record("unknown", next_at=_backoff(now, attempts), error=str(exc))
        report.uncertain += 1
        return False
    if found is None:
        record("pending", error="marker 없음 — 다시 보냄")
        report.requeued += 1
    else:
        record("delivered", comment=found)
        report.reconciled += 1
    return True


def _find_marker(client: GitHubClient, repository: str, number: int, task_id: str) -> int | None:
    """첫 줄이 이 Task 의 marker 인 댓글 ID. 전 페이지를 봤는데 없으면 None."""
    expected = marker(task_id)
    page: int | None = 1
    for _ in range(MAX_COMMENT_PAGES):
        result = client.list_comments(repository, number, page)
        for comment in result.comments:
            first = comment.body.splitlines()[0].strip() if comment.body else ""
            if first == expected:
                return comment.comment_id
        page = result.next_cursor
        if page is None:
            return None
    raise GitHubError(f"댓글이 {MAX_COMMENT_PAGES} 페이지를 넘어 marker 를 확인하지 못함")
