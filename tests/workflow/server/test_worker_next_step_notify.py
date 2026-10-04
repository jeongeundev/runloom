# ruff: noqa: F811 — test_task_cycle·test_triage_runs·test_worker_next_step·test_work_actions_next_step 픽스처를 인자로 쓴다
"""알림과 반환 → 다음 판단 — phase 22 step 8 (ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22" 알림·원인과 시작 지점 ③).

사내 요청이 생기면 받는 사람에게 `internal_request_received`, 결과 뒤 판단이 제안이 되면 `next_step_proposed` 를 한 번씩
쌓는다. 반환된 사내 요청은 원래 업무에서 결과 뒤 판단(`request_returned`)을 건다 — 시작할 수 없으면 판단이 만든 요청만
`next_step_human` 사람 요청으로 돌아간다. 조사·반환은 테스트가 행으로 넣는다. 실제 러너·웹훅·모델을 부르지 않는다.
"""

import dataclasses
import json

import pytest

from workflow.adapters import internal_request_store, repo
from workflow.adapters.db import connect
from workflow.adapters.secret_store import NOTIFY_WEBHOOK_URL, SecretStore, personal_webhook_name
from workflow.contracts.internal_request import InternalRequestCreate
from workflow.contracts.v1 import ExecutionRequest, format_work_key
from workflow.domain.work_keys import work_path
from workflow.server.work_actions import WorkActionError
from workflow.server.worker import Worker, enqueue_request_notification

from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    WEBHOOK,
    NoCallbacks,
    clock,
    config,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
)
from .test_triage_dispatch import triage_logs
from .test_triage_judge import submit
from .test_triage_runs import judge, new_issue  # noqa: F401 — 픽스처
from .test_work_actions_next_step import accept, admin, directory, docs, internal, internal_requests, propose  # noqa: F401
from .test_worker_next_step import HUMAN, capable, fix_result, next_body, requests_of, work_of  # noqa: F401

PUBLIC = "https://runloom.example"
SUMMARY = "net.netfilter 값은 131072\n근거는 노드 3대"


@pytest.fixture
def secrets(tmp_path) -> SecretStore:
    store = SecretStore(tmp_path / "secrets")
    store.write(NOTIFY_WEBHOOK_URL, WEBHOOK + "\n")
    return store


@pytest.fixture
def nsettings(settings):
    return dataclasses.replace(settings, public_url=PUBLIC)


@pytest.fixture
def make_nworker(app, nsettings, store, clock, secrets):
    def _make() -> Worker:
        return Worker(lambda: connect(nsettings.db_path), store, NoCallbacks(), nsettings, clock, secrets=secrets)

    return _make


@pytest.fixture
def nworker(make_nworker) -> Worker:
    return make_nworker()


def rows(conn, event: str) -> list:
    return conn.execute("SELECT * FROM notifications WHERE event = ? ORDER BY rowid", (event,)).fetchall()


def name_of(conn, member_id: str) -> str:
    return repo.get_member(conn, SESSION, member_id)["display_name"]


def returned(conn, request_id: str, task_id: str, execution_id: str, *, at: str = NOW) -> None:
    """받는 사람이 조사를 반환했다(조사 단계·검토는 생략 — 반환 행만)."""
    request = conn.execute("SELECT * FROM internal_requests WHERE request_id = ?", (request_id,)).fetchone()
    conn.execute(
        "INSERT INTO internal_request_investigations (request_id, task_id, created_at, returned_execution_id,"
        " returned_artifact_id, returned_summary, returned_at, returned_by_member_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, task_id, NOW, execution_id, repo.get_execution(conn, execution_id)["result_artifact_id"],
         SUMMARY, at, request["recipient_member_id"]),
    )
    conn.execute("UPDATE internal_requests SET state = 'accepted', accepted_at = ?, revision = 3 WHERE request_id = ?",
                 (NOW, request_id))
    conn.commit()


def human_request(conn, admin: str, wid: str, recipient: str) -> str:
    """사람이 원래 업무에서 직접 보낸 사내 요청 — 반환은 request_id."""
    return internal_request_store.create(conn, SESSION, admin, wid, InternalRequestCreate(
        system_id="kube_proxy", request_kind="investigation", recipient_member_id=recipient,
        expected_directory_revision=repo.get_config_revision(conn, SESSION), submission_key="human-1",
        purpose="호스트 sysctl 값 확인"), now=NOW)["request_id"]


def original_request_first(conn, store, worker, capable) -> tuple[str, str, str]:
    """수정 결과 `needs_information` 를 결과 뒤 판단 없이 원래 사람 요청으로 보낸 상태(그 tick 동안만 러너 능력을 비운다).
    반환은 (수정 Task, 실행, 업무)."""
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    repo.record_runner_capabilities(conn, capable["billing"], None)
    conn.commit()
    worker.tick()
    repo.record_runner_capabilities(conn, capable["billing"], ["verify_only", "after_result_triage"])
    conn.commit()
    return task_id, execution_id, work_of(conn, task_id)["work_item_id"]


# --- next_step_proposed ----------------------------------------------------------------


def test_next_step_proposal_notifies_the_turn_recipients_once(conn, store, nworker, make_nworker, admin):
    task_id, _, wid, log = propose(conn, store, nworker, HUMAN)

    (row,) = rows(conn, "next_step_proposed")

    recipients = repo.turn_recipients_of(conn, wid)
    assert recipients == (admin,)
    key = format_work_key(repo.get_work_item(conn, SESSION, wid)["key_number"])
    title = repo.get_task(conn, task_id)["title"]
    assert (row["task_id"], row["dedupe_key"], row["recipient_member_id"], row["channel"]) == (
        task_id, f"next_step_proposed:{log['triage_id']}:shared", admin, "shared")
    assert row["content"] == (
        f"[Runloom] 다음 단계 제안 — {title}: 사람 확인 — 재현 금액을 알려 주세요 · 확신도 0.82 → {name_of(conn, admin)}"
        f"\n{PUBLIC}{work_path(key)}")
    # 다시 돌거나 재시작해도 한 번
    nworker.tick()
    make_nworker().tick()
    assert len(rows(conn, "next_step_proposed")) == 1


def test_next_step_proposal_names_the_internal_request_recipient(conn, store, nworker, admin, directory):
    propose(conn, store, nworker, internal(directory))

    (row,) = rows(conn, "next_step_proposed")

    assert ": 사내 요청 · kube_proxy/investigation → 박조사 · 확신도 0.82 → " in row["content"]


def test_failed_next_step_is_not_notified_as_a_proposal(conn, store, nworker, admin):
    task_id, _ = fix_result(conn, store, nworker, outcome="needs_information")
    nworker.tick()
    (log,) = triage_logs(conn, work_of(conn, task_id)["work_item_id"])
    submit(conn, store, log, next_body(log, {"type": "stage", "kind": "nope", "rework": False,
                                             "assignee": {"type": "agent", "id": FIX}}))

    nworker.tick()

    assert rows(conn, "next_step_proposed") == []
    assert len(rows(conn, "human_request")) == 1  # 원래 사람 요청은 기존 알림 그대로


# --- internal_request_received ----------------------------------------------------------


def test_accepted_internal_request_notifies_the_recipient_once(conn, store, nsettings, nworker, admin, directory,
                                                               secrets):
    _, _, wid, log = propose(conn, store, nworker, internal(directory))

    accept(conn, store, nsettings, wid, log, admin, secrets=secrets)

    (request,) = internal_requests(conn)
    (row,) = rows(conn, "internal_request_received")
    title = repo.get_work_item(conn, SESSION, wid)["title"]
    assert (row["task_id"], row["dedupe_key"], row["recipient_member_id"], row["channel"], row["session_id"]) == (
        None, f"internal_request_received:{request['request_id']}:shared", directory, "shared", SESSION)
    assert row["content"] == (
        f"[Runloom] 요청 받음 — {title}: {name_of(conn, admin)} · kube_proxy/investigation · 호스트 sysctl 값 확인"
        f" → 박조사\n{PUBLIC}/requests")
    assert json.loads(row["payload_json"]) == {"title": title, "task_url": f"{PUBLIC}/requests", "pr_url": None,
                                               "recipient_member_ids": [directory]}
    # 다시 눌러도(409) 알림은 하나
    with pytest.raises(WorkActionError):
        accept(conn, store, nsettings, wid, log, admin, secrets=secrets)
    assert len(rows(conn, "internal_request_received")) == 1


def test_request_notification_goes_to_the_recipient_only_with_the_first_line(conn, nsettings, admin, directory,
                                                                             secrets):
    wid = new_issue(conn, 5)
    purpose = "첫 줄 " + "가" * 100 + "\n둘째 줄 비밀 아님"
    request_id = internal_request_store.create(conn, SESSION, admin, wid, InternalRequestCreate(
        system_id="kube_proxy", request_kind="investigation", recipient_member_id=directory,
        expected_directory_revision=repo.get_config_revision(conn, SESSION), submission_key="k-1",
        purpose=purpose), now=NOW)["request_id"]
    secrets.delete(NOTIFY_WEBHOOK_URL)
    secrets.write(personal_webhook_name(directory), "https://hooks.example/personal-token")
    secrets.write(personal_webhook_name(admin), "https://hooks.example/admin-token")

    enqueue_request_notification(conn, nsettings, secrets, request_id=request_id, now=NOW)
    enqueue_request_notification(conn, nsettings, secrets, request_id=request_id, now=NOW)

    (row,) = rows(conn, "internal_request_received")
    assert (row["channel"], row["recipient_member_id"], row["dedupe_key"]) == (
        "personal", directory, f"internal_request_received:{request_id}:personal:{directory}")
    first, link = row["content"].split("\n")
    assert first.endswith(" · kube_proxy/investigation · " + purpose.split("\n")[0][:80])
    assert "둘째 줄" not in row["content"] and "→" not in first
    assert link == f"{PUBLIC}/requests"
    assert "token" not in row["content"] and "token" not in row["payload_json"]


def test_request_notification_needs_a_webhook(conn, nsettings, admin, directory, tmp_path):
    wid = new_issue(conn, 5)
    request_id = human_request(conn, admin, wid, directory)

    enqueue_request_notification(conn, nsettings, SecretStore(tmp_path / "empty"), request_id=request_id, now=NOW)

    assert rows(conn, "internal_request_received") == []


# --- ③ 반환 → 결과 뒤 판단 ----------------------------------------------------------------


def test_returned_request_made_by_a_next_step_starts_a_next_step_once(conn, store, nsettings, nworker, make_nworker,
                                                                      admin, directory, secrets):
    task_id, execution_id, wid, log = propose(conn, store, nworker, internal(directory))
    accept(conn, store, nsettings, wid, log, admin, secrets=secrets)
    (request,) = internal_requests(conn)
    returned(conn, request["request_id"], task_id, execution_id)

    report = nworker.tick()

    assert report.next_step_started == 1
    row = repo.next_step_for_cause(conn, request_id=request["request_id"])
    assert (row["cause"], row["cause_execution_id"], row["state"], row["trigger"]) == (
        "request_returned", execution_id, "running", "auto")
    text = ExecutionRequest.model_validate_json(repo.get_execution(conn, row["execution_id"])["request_json"]).request
    assert "## 반환된 사내 요청" in text and "net.netfilter 값은 131072" in text
    # 사람 요청·업무 완료 없음 — 판단은 제안만
    assert requests_of(conn, task_id) == []
    assert repo.get_task(conn, task_id)["finished_at"] is None
    # 다시 돌거나 재시작해도 한 번
    nworker.tick()
    make_nworker().tick()
    assert len([r for r in triage_logs(conn, wid) if r["cause"] == "request_returned"]) == 1


def test_returned_human_request_starts_from_the_open_stage(conn, store, nworker, capable, admin, directory):
    task_id, execution_id, wid = original_request_first(conn, store, nworker, capable)
    request_id = human_request(conn, admin, wid, directory)
    returned(conn, request_id, task_id, execution_id)

    report = nworker.tick()

    assert report.next_step_started == 1
    row = repo.next_step_for_cause(conn, request_id=request_id)
    assert (row["cause"], row["cause_execution_id"], row["task_id"] != task_id) == (
        "request_returned", execution_id, True)


def test_old_return_of_a_human_request_is_not_judged(conn, store, nworker, capable, admin, directory):
    task_id, execution_id, wid = original_request_first(conn, store, nworker, capable)
    request_id = human_request(conn, admin, wid, directory)
    returned(conn, request_id, task_id, execution_id, at="2026-10-06T10:59:59Z")

    report = nworker.tick()

    assert report.next_step_started == 0 and repo.next_step_for_cause(conn, request_id=request_id) is None


@pytest.mark.parametrize("missing", ["capability", "agent"])
def test_returned_human_request_without_a_judge_stays_as_before(conn, store, nworker, capable, admin, directory,
                                                                missing):
    task_id, execution_id, wid = original_request_first(conn, store, nworker, capable)
    before = requests_of(conn, task_id)
    request_id = human_request(conn, admin, wid, directory)
    if missing == "capability":
        repo.record_runner_capabilities(conn, capable["billing"], ["verify_only"])
    else:
        repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=None), NOW)
    conn.commit()
    returned(conn, request_id, task_id, execution_id)

    report = nworker.tick()

    assert (report.next_step_started, report.human_requests) == (0, 0)
    assert repo.next_step_for_cause(conn, request_id=request_id) is None
    assert requests_of(conn, task_id) == before


def test_returned_next_step_request_without_a_judge_asks_a_human_once(conn, store, nsettings, nworker, make_nworker,
                                                                      capable, admin, directory, secrets):
    task_id, execution_id, wid, log = propose(conn, store, nworker, internal(directory))
    accept(conn, store, nsettings, wid, log, admin, secrets=secrets)
    (request,) = internal_requests(conn)
    repo.record_runner_capabilities(conn, capable["billing"], None)
    conn.commit()
    returned(conn, request["request_id"], task_id, execution_id)

    report = nworker.tick()

    assert (report.next_step_started, report.human_requests) == (0, 1)
    assert requests_of(conn, task_id) == [("next_step_human", f"next_step_human:request:{request['request_id']}")]
    (human,) = repo.list_human_requests(conn, task_id)
    assert human["question"] == "사내 요청 결과가 돌아왔습니다 — 다음 단계를 정해 주세요: net.netfilter 값은 131072"
    assert len(rows(conn, "human_request")) == 1
    nworker.tick()
    make_nworker().tick()
    assert len(repo.list_human_requests(conn, task_id)) == 1 and len(rows(conn, "human_request")) == 1
    assert repo.next_step_for_cause(conn, request_id=request["request_id"]) is None
