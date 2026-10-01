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

후속 이슈 등록(ADR-0024 결정 13 — 행은 `repo.create_followup_once` 의 새 업무 가지에서 생긴다):
- 같은 프로젝트에 이슈를 만들고(이슈 유형 = 설정 이름의 `choices_json` id, 라벨 `runloom`·`runloom-RUN-<n>`), 한
  트랜잭션에서 행 `delivered` + 새 업무 원본 칸을 채운 뒤 원인 이슈와 `Relates` 로 잇는다(링크 생략·실패는 `note` 만).
- 생성 POST 의 응답을 잃으면(5xx·연결 오류) `unknown` — 다음 바퀴에 라벨 JQL 로 먼저 찾아 있으면 그것으로 기록하고,
  없을 때만 다시 POST 한다(두 번 만들지 않는다). claim 이 만료된 `sending` 도 같다. 400·403·404 는 `failed`.
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
from workflow.contracts.v1 import format_work_key
from workflow.domain.adf import markdown_to_adf
from workflow.domain.jira_intake import (
    JIRA_LINK_TYPE,
    already_in,
    choose_transition,
    followup_description,
    followup_label,
    followup_labels,
    followup_summary,
)
from workflow.domain.work_keys import work_path
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
# 후속 이슈 등록
CREATE_BAD_REQUEST = "필수 칸 — {message} — Jira 프로젝트 설정 확인"
CREATE_FORBIDDEN = "권한 없음 — 이슈를 만들 수 없음"
CREATE_NOT_FOUND = "프로젝트를 찾을 수 없음"
NO_ISSUE_TYPE = "이슈 유형 {name} 없음 — 목록 새로 고침"
SEVERAL_FOUND = "같은 라벨 이슈 {n}개 — 가장 앞 것으로"
NO_LINK_TYPE = f"링크 유형 {JIRA_LINK_TYPE} 없음 — 링크 생략"
LINK_FAILED = "링크 실패 — {reason}"


@dataclass
class JiraDeliveryReport:
    delivered: int = 0  # 전환 성공·이미 목표 상태
    skipped: int = 0  # 다음 순간으로 대체·Jira 에서 이미 완료 범주
    failed: int = 0  # 전환 없음·400·403·404·시도 상한
    deferred: int = 0  # 일시 실패 — 물러났다가 다시
    uncertain: int = 0  # 반영 불확실(`unknown`) — 후속 이슈 생성 응답을 잃음(전환에는 없다)
    rate_limited: bool = False  # 어느 워크스페이스의 이번 바퀴를 멈췄다

    def any(self) -> bool:
        return any(asdict(self).values())


class _Stop(Exception):
    """401·429 — 같은 토큰의 다른 요청도 막히므로 그 워크스페이스의 이번 바퀴를 멈춘다."""


def deliver_jira_updates(
    conn: Connection, client_for: Callable[[str], JiraClient | None], now: str, *, public_url: str = ""
) -> JiraDeliveryReport:
    """보낼 차례인 행을 생긴 순으로 보낸다. `client_for(session_id)` 가 None 이면(끊김·토큰 없음·토큰 오류) 그
    워크스페이스 행은 건드리지 않는다 — 시도 수도 늘지 않는다. `public_url` 은 후속 이슈 본문의 Runloom 업무 주소."""
    report = JiraDeliveryReport()
    clients: dict[str, JiraClient | None] = {}
    stopped: set[str] = set()
    for row in repo.jira_deliveries_due(conn, now):
        session_id = row["session_id"]
        if session_id in stopped:
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
            if row["action"] == "create_issue":
                _create_issue(conn, client, row, row["attempts"] + 1, now, report, public_url)
            else:
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


def _create_issue(
    conn: Connection, client: JiraClient, row: Row, attempts: int, now: str, report: JiraDeliveryReport,
    public_url: str,
) -> None:
    def record(state: str, *, error: str | None = None, next_at: str | None = None) -> None:
        repo.record_jira_delivery(conn, row["delivery_id"], attempts, state=state, now=now, last_error=error,
                                  next_at=next_at)

    session_id = row["session_id"]
    project = repo.jira_project_row(conn, row["source_id"])
    work = repo.get_work_item(conn, session_id, row["work_item_id"])
    site_url = repo.get_jira_connection(conn, session_id)["site_url"]
    work_key = format_work_key(work["key_number"])
    uncertain = row["state"] in ("unknown", "sending")  # 전에 POST 가 닿았을 수 있다
    posting = False
    try:
        if uncertain:
            found = sorted(client.find_issues_by_label(project["project_id"], followup_label(work["key_number"])),
                           key=lambda ref: int(ref.issue_id))
            if found:
                note = SEVERAL_FOUND.format(n=len(found)) if len(found) > 1 else None
                _created(conn, client, row, attempts, found[0], site_url, now, report, note)
                return
        choices = repo.get_jira_choices(conn, session_id, row["source_id"])
        type_id = next((t.id for t in choices.issue_types if t.name.casefold() == row["target"].casefold()), None)
        if type_id is None:
            record("failed", error=NO_ISSUE_TYPE.format(name=row["target"]))
            report.failed += 1
            return
        cause = repo.get_jira_issue(conn, session_id, row["source_id"], row["cause_issue_id"])
        description = followup_description(
            work_key=work_key, cause_key=cause["issue_key"] if cause is not None else row["cause_issue_id"],
            request=work["request"], work_url=f"{public_url}{work_path(work_key)}" if public_url else None,
        )
        posting = True
        ref = client.create_issue(project["project_id"], type_id, followup_summary(work["title"]),
                                  markdown_to_adf(description), followup_labels(work["key_number"]))
    except JiraUnauthorized:
        record("unknown" if uncertain else "pending", error=UNAUTHORIZED)
        repo.mark_jira_auth_failed(conn, session_id, now=now)
        raise _Stop from None
    except JiraRateLimited as exc:
        if _give_up(record, attempts, report):
            return
        record("unknown" if uncertain else "pending", error=RATE_LIMITED,
               next_at=_plus_seconds(now, max(MIN_RATE_LIMIT_SECONDS, exc.retry_after)))
        report.rate_limited = True
        raise _Stop from None
    except JiraUnavailable:
        if _give_up(record, attempts, report):
            return
        backoff = min(BACKOFF_SECONDS * 2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
        if posting or uncertain:  # POST 가 닿았는지 모른다 — 다음 바퀴에 라벨로 먼저 찾는다
            record("unknown", error=UNAVAILABLE, next_at=_plus_seconds(now, backoff))
            report.uncertain += 1
        else:
            record("pending", error=UNAVAILABLE, next_at=_plus_seconds(now, backoff))
            report.deferred += 1
        return
    except (JiraBadRequest, JiraForbidden, JiraNotFound, JiraError, ValueError) as exc:
        record("failed", error=_create_rejection(exc))
        report.failed += 1
        return
    _created(conn, client, row, attempts, ref, site_url, now, report, None)


def _created(conn: Connection, client: JiraClient, row: Row, attempts: int, ref, site_url: str, now: str,
             report: JiraDeliveryReport, note: str | None) -> None:
    """이슈 기록(행 + 새 업무 원본 칸, 한 트랜잭션) 뒤 원인 이슈와 잇는다. 링크는 이슈를 다시 만들 이유가 아니다."""
    if not repo.record_jira_issue_created(conn, row["delivery_id"], attempts, issue_id=ref.issue_id, key=ref.key,
                                          site_url=site_url, now=now, note=note):
        return
    report.delivered += 1
    try:
        type_name = next((t for t in client.issue_link_types() if t.casefold() == JIRA_LINK_TYPE.casefold()), None)
        if type_name is None:
            link_note = NO_LINK_TYPE
        else:
            client.link_issues(type_name, inward_issue_id=row["cause_issue_id"], outward_issue_id=ref.issue_id)
            return
    except (JiraError, ValueError) as exc:
        link_note = LINK_FAILED.format(reason=_link_reason(exc))
    repo.note_jira_delivery(conn, row["delivery_id"], " · ".join(x for x in (note, link_note) if x))


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


def _create_rejection(exc: Exception) -> str:
    if isinstance(exc, JiraBadRequest):
        return CREATE_BAD_REQUEST.format(message=exc.message or "Jira 오류")
    if isinstance(exc, JiraForbidden):
        return CREATE_FORBIDDEN
    if isinstance(exc, JiraNotFound):
        return CREATE_NOT_FOUND
    return REJECTED


def _link_reason(exc: Exception) -> str:
    if isinstance(exc, JiraForbidden):
        return "권한 없음"
    if isinstance(exc, JiraNotFound):
        return "이슈를 찾을 수 없음"
    if isinstance(exc, (JiraUnavailable, JiraRateLimited)):
        return "Jira 응답 없음"
    return REJECTED
