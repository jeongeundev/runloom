"""Jira 상태 옮기기 전송 — 세 순간 outbox(`jira_deliveries`) 소비 (ADR-0024 결정 11·12, ARCHITECTURE "Jira 소스 —
phase 18" 상태 옮기기·오류 분류).

- 행은 업무 상태가 세 순간에 들어갈 때 `repo.set_work_status` 와 같은 트랜잭션에서 생긴다(`queue_jira_transition`).
  여기서는 트랜잭션 밖에서 Jira 를 부르고, claim(`attempts + 1` fence)으로 여러 소비자 중 하나만 보낸다.
- 전환 한 건: ① 같은 업무에 더 늦게 생긴 전환 행이 있으면 `skipped`(옛 순간으로 되돌리지 않는다) ② 이슈 상태가 이미
  목표면 `delivered`, 완료 범주인데 순간이 `done` 이 아니면 `skipped` ③ 목표로 가는 전환이 없으면 `failed` ④ 전환 POST.
  ②의 확인 덕에 다시 보내도 중복이 없으므로 응답을 잃으면 `pending` 으로 돌아가 다음 바퀴에 ②부터 한다(`unknown` 없음).
- 401 은 `pending` 그대로 연결에 `auth_failed_at` 을 쓰고 그 워크스페이스를 멈춘다. 429 는 `Retry-After`(최소 60초)
  뒤로 물러나 그 워크스페이스의 이번 바퀴를 멈춘다. 5xx·연결 오류는 백오프, `MAX_ATTEMPTS` 번째에 `failed`.
  400·403·404·전환 없음은 `failed` — 다시 보내지 않는다. 반영 실패는 업무 상태·실행과 따로 남고 알림은 보내지 않는다.
- Jira 상태는 완료 판정 근거가 아니다 — 여기서는 Runloom 상태를 Jira 로 옮기기만 한다.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from sqlite3 import Connection, Row

from workflow.adapters import repo
from workflow.adapters.jira_client import (
    JiraBadRequest,
    JiraClient,
    JiraError,
    JiraForbidden,
    JiraNotFound,
    JiraRateLimited,
    JiraUnauthorized,
    JiraUnavailable,
)
from workflow.domain.jira_intake import already_in, choose_transition
from workflow.server.github_delivery import (
    BACKOFF_SECONDS,
    CLAIM_SECONDS,
    MAX_BACKOFF_SECONDS,
    MIN_RATE_LIMIT_SECONDS,
    _plus_seconds,
)

MAX_ATTEMPTS = 8  # 5xx·429 가 이만큼 이어지면 멈춘다(백오프 합 약 2시간)

# 화면 문구(마지막 오류·note) — ARCHITECTURE 오류 분류 표
SUPERSEDED = "다음 순간으로 대체"
ALREADY = "이미 그 상태"
DONE_IN_JIRA = "Jira 에서 이미 완료 범주"
NO_TRANSITION = "전환 없음 — 현재 상태 {status}"
BAD_REQUEST = "전환에 입력할 칸이 있음 — Jira 에서 옮기세요"
FORBIDDEN = "권한 없음 — 이 이슈를 옮길 수 없음"
NOT_FOUND = "이슈를 찾을 수 없음"
UNAUTHORIZED = "Jira 토큰 확인 필요 — 다시 연결"
RATE_LIMITED = "Jira 요청 한도 — 잠시 뒤"
UNAVAILABLE = "Jira 응답 없음 — 다시 시도"
GAVE_UP = "Jira 응답 없음 — {n}회 시도 뒤 멈춤"
REJECTED = "Jira 가 요청을 받지 않음"


@dataclass
class JiraDeliveryReport:
    delivered: int = 0  # 전환 성공·이미 목표 상태
    skipped: int = 0  # 다음 순간으로 대체·Jira 에서 이미 완료 범주
    failed: int = 0  # 전환 없음·400·403·404·시도 상한
    deferred: int = 0  # 일시 실패 — 물러났다가 다시
    uncertain: int = 0  # 반영 불확실(`unknown`) — 전환에는 없다(후속 이슈 생성용)
    rate_limited: bool = False  # 어느 워크스페이스의 이번 바퀴를 멈췄다

    def any(self) -> bool:
        return any(asdict(self).values())


class _Stop(Exception):
    """401·429 — 같은 토큰의 다른 요청도 막히므로 그 워크스페이스의 이번 바퀴를 멈춘다."""


def deliver_jira_updates(
    conn: Connection, client_for: Callable[[str], JiraClient | None], now: str
) -> JiraDeliveryReport:
    """보낼 차례인 행을 생긴 순으로 보낸다. `client_for(session_id)` 가 None 이면(끊김·토큰 없음·토큰 오류) 그
    워크스페이스 행은 건드리지 않는다 — 시도 수도 늘지 않는다."""
    report = JiraDeliveryReport()
    clients: dict[str, JiraClient | None] = {}
    stopped: set[str] = set()
    for row in repo.jira_deliveries_due(conn, now):
        session_id = row["session_id"]
        if row["action"] != "transition" or session_id in stopped:
            continue
        if session_id not in clients:
            clients[session_id] = client_for(session_id)
        client = clients[session_id]
        if client is None:
            continue
        if not repo.claim_jira_delivery(conn, row["delivery_id"], row["attempts"], now=now,
                                        claim_until=_plus_seconds(now, CLAIM_SECONDS)):
            continue
        try:
            _transition(conn, client, row, row["attempts"] + 1, now, report)
        except _Stop:
            stopped.add(session_id)
    return report


def _transition(
    conn: Connection, client: JiraClient, row: Row, attempts: int, now: str, report: JiraDeliveryReport
) -> None:
    def record(state: str, *, error: str | None = None, note: str | None = None, next_at: str | None = None) -> None:
        repo.record_jira_delivery(conn, row["delivery_id"], attempts, state=state, now=now, last_error=error,
                                  note=note, next_at=next_at)

    later = [r for r in repo.list_jira_deliveries(conn, row["work_item_id"]) if r["action"] == "transition"]
    if later[-1]["delivery_id"] != row["delivery_id"]:
        record("skipped", note=SUPERSEDED)
        report.skipped += 1
        return
    issue_id, target = row["issue_id"], row["target"]
    try:
        name, category = client.issue_status(issue_id)
        if already_in(name, target):
            record("delivered", note=ALREADY)
            report.delivered += 1
            return
        if category == "done" and row["moment"] != "done":
            record("skipped", note=DONE_IN_JIRA)
            report.skipped += 1
            return
        transition_id = choose_transition(client.transitions(issue_id), target)
        if transition_id is None:
            record("failed", error=NO_TRANSITION.format(status=name))
            report.failed += 1
            return
        client.transition(issue_id, transition_id)
    except JiraUnauthorized:
        record("pending", error=UNAUTHORIZED)
        repo.mark_jira_auth_failed(conn, row["session_id"], now=now)
        raise _Stop from None
    except JiraRateLimited as exc:
        if _give_up(record, attempts, report):
            return
        record("pending", error=RATE_LIMITED, next_at=_plus_seconds(now, max(MIN_RATE_LIMIT_SECONDS, exc.retry_after)))
        report.rate_limited = True
        raise _Stop from None
    except JiraUnavailable:
        if _give_up(record, attempts, report):
            return
        backoff = min(BACKOFF_SECONDS * 2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
        record("pending", error=UNAVAILABLE, next_at=_plus_seconds(now, backoff))
        report.deferred += 1
        return
    except (JiraBadRequest, JiraForbidden, JiraNotFound, JiraError, ValueError) as exc:
        record("failed", error=_rejection(exc))
        report.failed += 1
        return
    record("delivered")
    report.delivered += 1


def _give_up(record: Callable[..., None], attempts: int, report: JiraDeliveryReport) -> bool:
    if attempts < MAX_ATTEMPTS:
        return False
    record("failed", error=GAVE_UP.format(n=attempts))
    report.failed += 1
    return True


def _rejection(exc: Exception) -> str:
    """분류 문구만 남긴다 — 응답 본문·토큰을 싣지 않는다."""
    if isinstance(exc, JiraBadRequest):
        return BAD_REQUEST
    if isinstance(exc, JiraForbidden):
        return FORBIDDEN
    if isinstance(exc, JiraNotFound):
        return NOT_FOUND
    return REJECTED
