"""중앙 워커 — ARCHITECTURE "계약 수용 기준" 표를 한 행씩. 연결 프로그램 대신 테스트가 이벤트·산출물을 DB 에 직접 올린다.

시계는 고정 문자열을 돌려주는 `Clock`. 규칙 기반 후속(`_spawn_successors`)·범용 결과 판정·callback 은 워크스페이스에
등록한 사용자 정의 종류 `triage` → `review`(`repo.insert_kind`·`insert_rule`) 위에서 본다 — 내장 `bug_fix`·`code_review`
는 업무 순환(`_advance_cycle`)이 잇고, 그 흐름은 `test_task_cycle.py` 가 본다. 여기서 `bug_fix` 는 코드 결과 확인
(`_check_code_results`)과 인계 묶음 조립만 본다. 진단 데모 경로는 `main` 전용이다 (ADR-0019).
"""

import dataclasses
import inspect
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from workflow.adapters import repo
from workflow.adapters.callback_client import CallbackFailed
from workflow.adapters.db import connect
from workflow.adapters.github_client import GitHubRateLimited, IssuePage
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import (
    BUILTIN_RULES,
    ArtifactMeta,
    ChainCallback,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
    KindSpec,
    LocalTarget,
    SelectionRecord,
    SuccessorRule,
)
from workflow.server.auth import ensure_workspace
from workflow.server.worker import GITHUB_SYNC_INTERVAL_SECONDS, TickReport, Worker, assemble_handoff

from .conftest import (
    BASE_COMMIT,
    LOCAL_REGISTRATION,
    REPOSITORY,
    SESSION,
    TASK_A,
    TASK_B,
    bearer,
    code_change_result,
    event,
    exchange,
    log_in,
    meta_for,
)

BUILTIN_RULE = BUILTIN_RULES[0]  # bug_fix --[ready_for_review]--> code_review
CODE_KINDS = ("diff", "test_log_before", "test_log_after", "verification_log")

# 사용자 정의 종류 `triage`(Codex) → `review`(Claude) 와 규칙 triage → review (CONTRACT 11절). R 은 T 의 후속.
TASK_T = "triage-daily-0920"
TASK_R = "triage-review-0920"
EXEC_T = "exec-triage-001"
LOCAL_REVIEW = "local-demo-report-claude"
CAP_T = {"code": "triage", "scope": {"repository_id": REPOSITORY}}
CAP_R = {"code": "review", "scope": {"repository_id": REPOSITORY}}
TRIAGE_KIND = KindSpec(
    kind="triage", label="분류", capability_code="triage", scope_key="repository_id",
    input_kinds=[], output_kind="generic_result", outcomes=["ready_for_handoff", "needs_information"],
    instructions="보고서 변환 실패 원인을 분류하고 수정 방향을 diff 초안으로 남기세요. 저장소를 수정하지 마세요.",
    builtin=False,
)
REVIEW_KIND = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "generic_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"],
    instructions="인계 디렉터리의 diff 와 generic_result 를 읽고 검토 의견을 내세요. 저장소를 수정하지 마세요.",
    builtin=False,
)
REVIEW_RULE = SuccessorRule(
    from_kind="triage", on_outcomes=["ready_for_handoff"], to_kind="review",
    handoff_kinds=["generic_result", "diff"],
)
TRIAGE_SUMMARY = "report_transformer 가 data.records 를 읽지 못해 합계가 비었습니다. 수정 방향을 diff 로 남겼습니다."
REVIEW_SUMMARY = "diff 는 최소 변경이며 재현 테스트가 무력화되지 않았습니다."

# phase 7: n8n 입구로 들어온 체인 (ADR-0010). key 는 CONTRACT 12절, callback 은 허용 목록 localhost:5678 안.
CHAIN_ID = "chain-n8n-0920"
CALLBACK_URL = "http://localhost:5678/webhook-waiting/1234"
KEY_T, KEY_R = "triage-report", "review-triage"


# --- 시계·callback 흉내 -------------------------------------------------------------------


class Clock:
    def __init__(self, now: str = "2026-09-20T00:00:00Z"):
        self.now = now

    def __call__(self) -> str:
        return self.now

    def advance(self, seconds: int) -> None:
        moved = datetime.fromisoformat(self.now) + timedelta(seconds=seconds)
        self.now = moved.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FakeCallbackClient:
    """워커 → n8n callback 을 기록한다. `fail` 이면 기록한 뒤 `CallbackFailed("HTTP 503")` — 시도 횟수는 `posts` 로 센다."""

    def __init__(self, fail: bool = False):
        self.posts: list[tuple[str, dict]] = []
        self.fail = fail

    def post(self, url: str, payload: dict) -> None:
        self.posts.append((url, payload))
        if self.fail:
            raise CallbackFailed("HTTP 503")


# --- fixture ------------------------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_worker(app, settings, store, clock):
    """`app` 이 스키마를 만든 뒤에 워커를 연다. 워커는 자기 conn_factory 로 tick 마다 연결을 연다."""

    def _make(overrides=None, callbacks=None) -> Worker:
        used = settings if overrides is None else overrides
        return Worker(lambda: connect(used.db_path), store, callbacks or FakeCallbackClient(), used, clock)

    return _make


@pytest.fixture
def worker(make_worker) -> Worker:
    return make_worker()


def _selection(task_id: str, agent_id: str, capability: dict) -> SelectionRecord:
    return SelectionRecord.model_validate({
        "task_id": task_id, "mode": "auto", "required_capability": capability, "candidate_count": 1,
        "selected_agent_id": agent_id, "matched": capability, "status": "selected",
        "reason": f"{capability['code']} 일치 후보 1개",
    })


def _user_task(task_id: str, spec: KindSpec, capability: dict, registration: str, *, title: str,
               predecessor: str | None = None, run_mode: str = "auto", status=("대기", "선행 대기"),
               link: dict | None = None) -> dict:
    """웹 직접 등록·n8n 입구가 만드는 사용자 정의 종류 Task 모양 — 대상은 로컬 등록 하나."""
    return {
        "task_id": task_id, "session_id": SESSION, "title": title,
        "request": "보고서 변환 실패를 살펴봐 주세요.", "kind": spec.kind,
        "required_capability": capability, "selection_mode": "auto", "chosen_agent_id": None,
        "run_mode": run_mode, "completion_mode": "review", "criteria": [],
        "predecessor_task_id": predecessor, "revision": 1,
        "target": {"local_registration_id": registration},
        "status": status[0], "status_reason": status[1],
        **(link or {}),
    }


def seed_user_flow(conn, client, clock, *, rule=REVIEW_RULE, r_run_mode="auto", chain=None) -> tuple[str, str]:
    """워크스페이스·러너 모양 Agent 2개(Codex=triage, Claude=review)·종류 triage·review·(기본) 규칙 triage → review·
    T(triage)→R(review)·선택 기록·T queued 실행(연결 프로그램 배정). 연결 프로그램은 두 등록을 보고한 온라인 상태.
    `rule=None` 이면 규칙 없이 종류만. `chain`(`insert_chain` 행)을 주면 시작된 체인의 노드(`source_ref` KEY_T·KEY_R)다.
    반환은 (connector_id, token)."""
    now = clock()
    ensure_workspace(conn, now)
    for agent_id, name, registration, capability in (
        ("agent-codex-mac", "개인 Codex", LOCAL_REGISTRATION, CAP_T),
        ("agent-claude-mac", "Claude Code", LOCAL_REVIEW, CAP_R),
    ):
        repo.upsert_agent(conn, {
            "agent_id": agent_id, "name": name, "owner_scope": "personal", "connection_type": "local",
            "local_registration_id": registration, "capabilities": [capability], "connection_state": "unknown",
        })
        repo.register_session_agent(conn, SESSION, agent_id, now)
    repo.insert_kind(conn, SESSION, TRIAGE_KIND, now)
    repo.insert_kind(conn, SESSION, REVIEW_KIND, now)
    if rule is not None:
        repo.insert_rule(conn, SESSION, rule, now)
    if chain is not None:
        repo.insert_chain(conn, chain, now)
        repo.mark_chain_started(conn, chain["chain_id"], now)  # 입구 API 는 접수 즉시 첫 업무를 시작한다
    link_t = {"chain_id": chain["chain_id"], "source_ref": KEY_T} if chain else None
    link_r = {"chain_id": chain["chain_id"], "source_ref": KEY_R} if chain else None
    repo.insert_work_item_task(conn, _user_task(
        TASK_T, TRIAGE_KIND, CAP_T, LOCAL_REGISTRATION, title="보고서 변환 실패 분류",
        status=("실행 요청됨", "접수 대기"), link=link_t,
    ), now)
    repo.insert_work_item_task(conn, _user_task(
        TASK_R, REVIEW_KIND, CAP_R, LOCAL_REVIEW, title="분류 결과 검토", predecessor=TASK_T,
        run_mode=r_run_mode, link=link_r,
    ), now)
    repo.save_selection(conn, _selection(TASK_T, "agent-codex-mac", CAP_T))
    repo.save_selection(conn, _selection(TASK_R, "agent-claude-mac", CAP_R))
    connector_id, token = exchange(client, conn)
    for registration in (LOCAL_REGISTRATION, LOCAL_REVIEW):
        repo.update_registration(
            conn, registration, connector_id=connector_id, repository_id=REPOSITORY,
            base_commit=BASE_COMMIT, verification_profile_ids=[], discovered={}, now=now,
        )
    repo.touch_connector(conn, connector_id, now, None)
    request = ExecutionRequest(
        contract_version=1, execution_id=EXEC_T, task_id=TASK_T, kind="triage", agent_id="agent-codex-mac",
        task_revision=1, request="보고서 변환 실패를 살펴봐 주세요.", input_artifact_ids=[],
        target=LocalTarget(local_registration_id=LOCAL_REGISTRATION), kind_spec=TRIAGE_KIND,
    )
    repo.create_execution(
        conn, execution_id=EXEC_T, task_id=TASK_T, attempt_no=1, start_key=f"auto:{TASK_T}:r1",
        agent_id="agent-codex-mac", kind="triage", request=request, assigned_connector_id=connector_id,
        predecessor_execution_id=None, now=now,
    )
    return connector_id, token


@pytest.fixture
def flow(conn, client, clock) -> tuple[str, str]:
    return seed_user_flow(conn, client, clock)


def n8n_chain(callback_url=CALLBACK_URL) -> dict:
    """입구 API 가 만든 체인 행 (`source` n8n). `callback_url=None` 이면 출구 없음. `items` 는 InboundItem 원문 모양 —
    체인 화면(`views._composition_reasons`)이 이것으로 구성 이유를 다시 만든다."""
    return {
        "chain_id": CHAIN_ID, "session_id": SESSION, "title": "보고서 변환 실패 분류 → 검토",
        "source": "n8n", "callback_url": callback_url,
        "items": [
            {"key": KEY_T, "title": "보고서 변환 실패 분류", "body": "분류",
             "labels": ["kind:triage", f"repository_id:{REPOSITORY}"], "blocked_by": []},
            {"key": KEY_R, "title": "분류 결과 검토", "body": "검토",
             "labels": ["kind:review", f"repository_id:{REPOSITORY}"], "blocked_by": [KEY_T]},
        ],
    }


@pytest.fixture
def n8n_settings(settings):
    """callback 허용 목록에 localhost:5678 이 있고 public_url 은 비어 있다 (chain_url·task_url null)."""
    return dataclasses.replace(settings, callback_hosts=("localhost:5678",))


def _task(conn, task_id: str):
    return repo.get_task(conn, task_id)


def _status(conn, task_id: str) -> tuple[str, str]:
    row = _task(conn, task_id)
    return row["status"], row["status_reason"]


def _executions(conn, task_id: str):
    return repo.list_executions(conn, task_id)


def _verdict(conn, execution_id: str) -> dict | None:
    row = repo.get_verdict(conn, execution_id)
    return json.loads(row["verdict_json"]) if row else None


def _append(conn, execution_id: str, seq: int, type_: str, data: dict, now: str, actor="connector:x"):
    repo.append_event(
        conn, execution_id, ExecutionEvent.model_validate(event(execution_id, seq, type_, data)),
        actor=actor, now=now,
    )


def _store(conn, store, execution_id: str, kind: str, data: bytes, now: str, content_type="text/plain") -> str:
    created, _ = repo.store_artifact(
        conn, store, execution_id=execution_id, session_id=SESSION,
        meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=f"{kind}.txt", content_type=content_type)),
        data=data, now=now,
    )
    return created.artifact_id


def seed_generic_result(
    conn, store, execution_id: str, now: str, *, task_id: str, kind: str, outcome: str,
    summary: str = TRIAGE_SUMMARY, with_diff: bool = False, raw: bytes | None = None, started: bool = False,
) -> str:
    """실행을 (accepted → started →) (원시 로그 [+ diff] + generic_result) → result_ready 로. CONTRACT 11.3 형태.
    `started` 면 이미 실행 중이라 accepted·started 를 건너뛴다. `raw` 를 주면 결과 산출물 본문을 그 바이트로 둔다
    (봉투 파싱 실패). 반환은 결과 산출물 ID."""
    if not started:
        _append(conn, execution_id, 1, "accepted", {}, now)
        _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:2"}, now)
    ids = [_store(conn, store, execution_id, "claude_jsonl", b'{"type":"result"}\n', now)]
    if with_diff:
        ids.append(_store(conn, store, execution_id, "diff", b"--- a/report.py\n+++ b/report.py\n-items\n+records\n", now))
    body = {
        "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": kind,
        "outcome": outcome, "summary": summary, "artifact_ids": ids,
    }
    result_id = _store(
        conn, store, execution_id, "generic_result",
        raw if raw is not None else json.dumps(body, ensure_ascii=False).encode(), now,
        content_type="application/json",
    )
    _append(conn, execution_id, 3, "result_ready", {"result_artifact_id": result_id}, now)
    return result_id


def finish_triage(conn, store, clock, *, outcome="ready_for_handoff", raw=None, started=False) -> str:
    """T 실행(EXEC_T)의 결과 제출 — diff 초안과 generic_result. 반환은 결과 산출물 ID."""
    return seed_generic_result(conn, store, EXEC_T, clock(), task_id=TASK_T, kind="triage", outcome=outcome,
                               with_diff=True, raw=raw, started=started)


def spawn_review(worker: Worker, conn, store, clock) -> str:
    """T 결과 → 판정 → R 착수까지 한 tick. 반환은 R 실행 ID."""
    finish_triage(conn, store, clock)
    worker.tick()
    return _executions(conn, TASK_R)[0]["execution_id"]


def finish_review(conn, store, clock, execution_id: str, *, outcome="approved", kind="review") -> str:
    return seed_generic_result(conn, store, execution_id, clock(), task_id=TASK_R, kind=kind, outcome=outcome,
                               summary=REVIEW_SUMMARY)


# --- 규칙 기반 후속: T 결과 + 판정 passed + outcome 일치 → R --------------------------------------


def test_triage_result_is_judged_and_spawns_review_in_the_same_tick(flow, worker, conn, store, client, clock):
    """tick 순서: 범용 결과 판정 → 후속 스캔. T 는 사람 검토 전(확인 필요)인데 R 이 착수한다 (ADR-0009 (3))."""
    connector_id, token = flow
    result_id = finish_triage(conn, store, clock)

    report = worker.tick()

    assert (report.generic_checked, report.results_checked, report.successors_created) == (1, 0, 1)
    assert _verdict(conn, EXEC_T)["outcome"] == "passed"
    assert _status(conn, TASK_T) == ("확인 필요", "검토 대기")
    assert _task(conn, TASK_T)["finished_at"] is None
    assert repo.get_execution(conn, EXEC_T)["released_at"] is None

    # R: 인계 묶음을 입력으로 고정한 queued 실행 하나
    executions = _executions(conn, TASK_R)
    assert len(executions) == 1
    r = executions[0]
    assert (r["status"], r["kind"], r["attempt_no"]) == ("queued", "review", 1)
    assert r["assigned_connector_id"] == connector_id
    assert r["predecessor_execution_id"] == EXEC_T
    assert r["start_key"] == f"auto:{TASK_R}:r1"
    assert _status(conn, TASK_R) == ("실행 요청됨", "접수 대기")
    # 후속 실행을 만든 뒤 R 의 업무 상태를 다시 계산해 기록했다(ADR-0020)
    work = repo.work_item_of_task(conn, TASK_R)
    assert (work["status"], work["status_reason"]) == ("에이전트 작업 중", "검토 실행 중")

    # 사용자 정의 종류의 요청: kind_spec 은 등록부 값, target 은 LocalTarget 하나 (CONTRACT 11.5)
    request = ExecutionRequest.model_validate_json(r["request_json"])
    assert request.kind == "review" and request.kind_spec == REVIEW_KIND
    assert request.agent_id == "agent-claude-mac"
    assert request.target == LocalTarget(local_registration_id=LOCAL_REVIEW)
    assert len(request.input_artifact_ids) == 1
    bundle_row = repo.get_artifact(conn, request.input_artifact_ids[0])
    assert (bundle_row["kind"], bundle_row["execution_id"], bundle_row["session_id"]) == ("handoff_bundle", EXEC_T, SESSION)
    bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, store, bundle_row["artifact_id"]))
    assert bundle.source_execution_id == EXEC_T and bundle.source_kind == "triage"
    assert bundle.source_result_artifact_id == result_id
    assert bundle.attachments == []
    # 규칙 handoff [generic_result, diff] — 원시 로그(claude_jsonl)는 넘기지 않는다
    by_kind = {a["kind"]: a for a in repo.artifacts_of(conn, EXEC_T)}
    assert sorted((i.kind, i.artifact_id, i.sha256) for i in bundle.inputs) == sorted(
        (k, by_kind[k]["artifact_id"], by_kind[k]["sha256"]) for k in ("generic_result", "diff")
    )

    # 연결 프로그램이 claim API 로 그 실행을 받을 수 있다
    response = client.post(
        "/connector/claim", json={"contract_version": 1, "connector_id": connector_id},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["execution_id"] == r["execution_id"]
    assert response.json()["input_artifact_ids"] == request.input_artifact_ids

    # 다음 tick 에 실행이 늘지 않는다
    assert worker.tick().successors_created == 0
    assert len(_executions(conn, TASK_R)) == 1


def test_bundle_is_assembled_once_and_reused(flow, conn, store, clock):
    finish_triage(conn, store, clock)
    t = repo.get_execution(conn, EXEC_T)
    task_r = _task(conn, TASK_R)

    first = assemble_handoff(conn, store, t, task_r, REVIEW_RULE, clock())
    second = assemble_handoff(conn, store, t, task_r, REVIEW_RULE, clock())

    assert first == second
    bundles = [x for x in repo.artifacts_of(conn, EXEC_T) if x["kind"] == "handoff_bundle"]
    assert len(bundles) == 1


def test_bundle_for_bug_fix_collects_rule_kinds_without_attachments(exec_fix, conn, store, clock):
    """내장 규칙 bug_fix → code_review (handoff code_change_result·diff·test_log_after·verification_log):
    수정 산출물 4개가 inputs 에, 근거 첨부는 없다. test_log_before 는 규칙 밖이라 넘기지 않는다."""
    seed_code_result(conn, store, exec_fix, clock())
    fix_row = repo.get_execution(conn, exec_fix)
    task_b = _task(conn, TASK_B)

    bundle_id = assemble_handoff(conn, store, fix_row, task_b, BUILTIN_RULE, clock())

    bundle_row = repo.get_artifact(conn, bundle_id)
    assert (bundle_row["kind"], bundle_row["execution_id"], bundle_row["session_id"]) == ("handoff_bundle", exec_fix, SESSION)
    bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, store, bundle_id))
    assert bundle.source_execution_id == exec_fix and bundle.source_kind == "bug_fix"
    assert bundle.source_result_artifact_id == fix_row["result_artifact_id"]
    assert bundle.attachments == []
    by_kind = {a["kind"]: a for a in repo.artifacts_of(conn, exec_fix)}
    assert sorted((i.kind, i.artifact_id, i.sha256, i.content_type) for i in bundle.inputs) == sorted(
        (k, by_kind[k]["artifact_id"], by_kind[k]["sha256"], by_kind[k]["content_type"])
        for k in ("code_change_result", "diff", "test_log_after", "verification_log")
    )
    assert assemble_handoff(conn, store, fix_row, task_b, BUILTIN_RULE, clock()) == bundle_id


def test_review_waits_while_triage_has_no_verdict(flow, worker, conn, store, clock):
    """선행 결과가 있어도 판정이 없으면 착수하지 않는다 — 판정 단계보다 먼저 스캔이 돌아도 같다."""
    finish_triage(conn, store, clock)

    report = TickReport()
    worker._spawn_successors(conn, report)  # tick 의 범용 판정을 건너뛰고 스캔만

    assert report.successors_created == 0 and _executions(conn, TASK_R) == []
    assert _status(conn, TASK_R) == ("대기", "선행 대기")


def test_review_needs_attention_when_triage_outcome_not_in_rule(flow, worker, conn, store, clock):
    """판정은 통과했지만 outcome 이 규칙 on_outcomes 밖 → 확인 필요 + 이유. 기본 규칙을 추측하지 않는다."""
    finish_triage(conn, store, clock, outcome="needs_information")

    report = worker.tick()

    assert report.generic_checked == 1 and report.successors_created == 0
    assert _verdict(conn, EXEC_T)["outcome"] == "passed"
    assert _executions(conn, TASK_R) == []
    assert _status(conn, TASK_R) == ("확인 필요", "선행 outcome needs_information 은 규칙 대상 아님 — 확인 필요")
    assert worker.tick().successors_created == 0


def test_review_waits_with_reason_when_no_rule_registered(conn, client, clock, worker, store):
    """종류는 등록됐지만 triage → review 규칙이 없다 → 대기 + '후속 규칙 없음'."""
    seed_user_flow(conn, client, clock, rule=None)
    finish_triage(conn, store, clock)

    report = worker.tick()

    assert report.generic_checked == 1 and report.successors_created == 0
    assert _executions(conn, TASK_R) == []
    assert _status(conn, TASK_R) == ("대기", "후속 규칙 없음: triage → review — 규칙을 등록하거나 직접 실행")


def test_failed_triage_verdict_does_not_spawn_review(flow, worker, conn, store, clock):
    """판정 실패(outcome 이 종류 목록 밖)인 선행은 사람이 본다 — 후속을 착수하지 않는다."""
    finish_triage(conn, store, clock, outcome="merged")

    report = worker.tick()

    assert _verdict(conn, EXEC_T)["outcome"] == "failed"
    assert report.successors_created == 0 and _executions(conn, TASK_R) == []
    assert _status(conn, TASK_R) == ("대기", "선행 대기")


def test_closed_triage_does_not_spawn_review(flow, worker, conn, store, clock):
    """사람이 선행을 종료(close)하면 후속을 새로 착수하지 않는다."""
    finish_triage(conn, store, clock)
    repo.record_verdict(
        conn, task_id=TASK_T, execution_id=EXEC_T, verdict={"outcome": "passed", "checks": []},
        status="확인 필요", reason="검토 대기", finish=False, now=clock(),
    )
    repo.update_task_status(conn, TASK_T, "실패", "검토 거절", finished_at=clock(), review_decision="close", now=clock())
    repo.release_execution(conn, EXEC_T, clock())

    report = worker.tick()

    assert report.successors_created == 0 and _executions(conn, TASK_R) == []
    assert _status(conn, TASK_R) == ("대기", "선행 대기")


def test_restart_after_triage_result_creates_review_once(flow, make_worker, conn, store, clock):
    repo.set_agent_connection(conn, "agent-claude-mac", "offline", clock())
    finish_triage(conn, store, clock)
    report = make_worker().tick()

    assert _verdict(conn, EXEC_T)["outcome"] == "passed"
    assert report.successors_created == 0 and _executions(conn, TASK_R) == []
    # 인계 묶음 전이라 화면 판정은 선행(T, 확인 필요) 기준 — 진단 자동 완료가 없어져 '연결 끊김' 이 아니다
    assert _status(conn, TASK_R) == ("대기", "선행 대기")

    repo.set_agent_connection(conn, "agent-claude-mac", "online", clock())
    fresh = make_worker()  # 재시작: 새 인스턴스의 첫 tick 이 후속 스캔으로 이어 간다
    assert fresh.tick().successors_created == 1
    assert make_worker().tick().successors_created == 0
    make_worker().tick()

    assert len(_executions(conn, TASK_R)) == 1
    assert _status(conn, TASK_R) == ("실행 요청됨", "접수 대기")


def test_offline_connector_holds_review_until_online(flow, worker, conn, store, clock, settings):
    finish_triage(conn, store, clock)
    clock.advance(settings.limits.heartbeat_offline_seconds + 1)  # heartbeat 끊김

    report = worker.tick()

    assert report.agents_offline == 2
    assert repo.get_agent(conn, "agent-codex-mac")["connection_state"] == "offline"
    assert repo.get_agent(conn, "agent-claude-mac")["connection_state"] == "offline"
    assert _verdict(conn, EXEC_T)["outcome"] == "passed"
    assert _executions(conn, TASK_R) == []
    # 인계 묶음 전이라 화면 판정은 선행(T, 확인 필요) 기준 — 진단 자동 완료가 없어져 '연결 끊김' 이 아니다
    assert _status(conn, TASK_R) == ("대기", "선행 대기")

    repo.set_agent_connection(conn, "agent-claude-mac", "online", clock())
    assert worker.tick().successors_created == 1
    assert _status(conn, TASK_R) == ("실행 요청됨", "접수 대기")


def test_manual_review_gets_bundle_without_execution_and_is_runnable(conn, client, clock, make_worker, store):
    """직접 실행 후속은 입력(인계 묶음)만 준비하고 사용자 조작 전에는 실행을 만들지 않는다."""
    seed_user_flow(conn, client, clock, r_run_mode="manual")
    worker = make_worker()
    finish_triage(conn, store, clock)

    report = worker.tick()

    assert report.inputs_prepared == 1 and report.successors_created == 0
    assert _executions(conn, TASK_R) == []
    bundles = [x for x in repo.artifacts_of(conn, EXEC_T) if x["kind"] == "handoff_bundle"]
    assert len(bundles) == 1
    assert worker.tick().inputs_prepared == 0
    # 선행 T 는 사람 검토 전(확인 필요)이지만 결과·판정·인계 묶음이 준비됐으므로 직접 실행 후속은 '실행 가능' 이다
    # (ADR-0009 (3) — views.predecessor_handoff 가 선행 조건을 푼다)
    assert _status(conn, TASK_R) == ("실행 가능", "agent-claude-mac 선택됨")

    # 사용자의 직접 실행이 그 인계 묶음을 입력으로 쓴다
    log_in(client)
    response = client.post(f"/tasks/{TASK_R}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    r = _executions(conn, TASK_R)[0]
    assert ExecutionRequest.model_validate_json(r["request_json"]).input_artifact_ids == [bundles[0]["artifact_id"]]
    assert r["predecessor_execution_id"] == EXEC_T


# --- 사용자 정의 종류의 결과 판정 — outcome ∈ KindSpec.outcomes 만, 완료는 사람 ---------------------


def test_generic_result_in_spec_passes_and_waits_for_review(flow, worker, conn, store, clock):
    r = spawn_review(worker, conn, store, clock)
    finish_review(conn, store, clock, r, outcome="changes_requested")

    report = worker.tick()

    assert (report.generic_checked, report.results_checked) == (1, 0)
    verdict = _verdict(conn, r)
    assert verdict["outcome"] == "passed"
    assert [c["code"] for c in verdict["checks"]] == ["envelope_valid", "ids_match", "outcome_in_spec"]
    assert all(c["passed"] for c in verdict["checks"])
    assert _status(conn, TASK_R) == ("확인 필요", "검토 대기")
    task_r = _task(conn, TASK_R)
    assert task_r["finished_at"] is None and repo.get_execution(conn, r)["released_at"] is None
    assert worker.tick().generic_checked == 0  # 한 번만 판정


def test_generic_result_outcome_outside_spec_fails(flow, worker, conn, store, clock):
    r = spawn_review(worker, conn, store, clock)
    finish_review(conn, store, clock, r, outcome="merged")

    worker.tick()

    verdict = _verdict(conn, r)
    assert verdict["outcome"] == "failed"
    failed = [c for c in verdict["checks"] if not c["passed"]]
    assert [c["code"] for c in failed] == ["outcome_in_spec"]
    assert "허용되지 않은 outcome" in failed[0]["detail"] and "merged" in failed[0]["detail"]
    assert _status(conn, TASK_R) == ("확인 필요", failed[0]["detail"])
    assert _task(conn, TASK_R)["finished_at"] is None


def test_generic_result_kind_mismatch_fails(flow, worker, conn, store, clock):
    r = spawn_review(worker, conn, store, clock)
    finish_review(conn, store, clock, r, kind="audit")

    worker.tick()

    verdict = _verdict(conn, r)
    assert verdict["outcome"] == "failed"
    assert [c["code"] for c in verdict["checks"]] == ["envelope_valid", "ids_match"]
    assert not next(c for c in verdict["checks"] if c["code"] == "ids_match")["passed"]
    assert _status(conn, TASK_R)[0] == "확인 필요"


def test_broken_generic_result_is_preserved_and_needs_attention(flow, worker, conn, store, clock):
    finish_triage(conn, store, clock, raw=b"not json {")

    worker.tick()

    t = repo.get_execution(conn, EXEC_T)
    assert t["status"] == "result_ready"
    assert repo.read_artifact(conn, store, t["result_artifact_id"]) == b"not json {"
    verdict = _verdict(conn, EXEC_T)
    assert verdict["outcome"] == "failed" and [c["code"] for c in verdict["checks"]] == ["envelope_valid"]
    assert _status(conn, TASK_T) == ("확인 필요", verdict["checks"][0]["detail"])
    assert _executions(conn, TASK_R) == []


def test_generic_check_ignores_builtin_kinds(exec_fix, worker, conn, store, clock):
    """bug_fix 결과는 코드 결과 확인이 판정한다 — 범용 판정은 내장 종류를 건드리지 않는다."""
    seed_code_result(conn, store, exec_fix, clock())

    report = worker.tick()

    assert (report.results_checked, report.generic_checked) == (1, 0)
    assert [c["code"] for c in _verdict(conn, exec_fix)["checks"]][0] == "result_parsed"


# --- bug_fix 결과 확인: 요청 일치·필수 산출물·수정 전 실패·검증 프로필 -----------------------------------


def seed_code_result(
    conn, store, execution_id: str, now: str, *, kinds=CODE_KINDS, before_exit=1, verification_exit=0,
    outcome="ready_for_review", result_execution_id: str | None = None, raw: bytes | None = None,
) -> str:
    """bug_fix 실행을 accepted → started → (산출물) → result_ready 로. CONTRACT 13.5 정상 제출 형태.
    `result_execution_id` 는 결과 봉투의 execution_id(요청과 다르게), `raw` 는 결과 본문 바이트(파싱 실패). 반환은 결과 ID."""
    _append(conn, execution_id, 1, "accepted", {}, now)
    _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:1"}, now)
    contents = {
        "diff": "--- a/report.py\n+++ b/report.py\n@@ -1 +1,2 @@\n-items\n+items\n+records\n",
        "test_log_before": f"exit_code={before_exit}\nFAILED tests/test_transform.py::test_records\n",
        "test_log_after": "exit_code=0\n3 passed\n",
        "verification_log": f"exit_code={verification_exit}\n3 passed\n",
    }
    for kind in kinds:
        _store(conn, store, execution_id, kind, contents[kind].encode(), now)
    body = code_change_result(result_execution_id or execution_id, TASK_A)
    body["outcome"] = outcome
    body["verification"]["exit_code"] = verification_exit
    result_id = _store(
        conn, store, execution_id, "code_change_result",
        raw if raw is not None else json.dumps(body, ensure_ascii=False).encode(), now, content_type="application/json",
    )
    _append(conn, execution_id, 3, "result_ready", {"result_artifact_id": result_id}, now)
    return result_id


def _check_code(worker: Worker, conn) -> TickReport:
    """코드 결과 확인 단계만 — 뒤이은 업무 순환(사람 요청·검토 착수)이 상태를 바꾸기 전의 판정·상태를 본다."""
    report = TickReport()
    worker._check_code_results(conn, report)
    return report


def test_fix_result_passes_and_keeps_the_lock_for_review(exec_fix, worker, conn, store, clock):
    seed_code_result(conn, store, exec_fix, clock())

    assert _check_code(worker, conn).results_checked == 1

    assert _status(conn, TASK_A) == ("확인 필요", "검토 대기")
    assert _task(conn, TASK_A)["finished_at"] is None and repo.get_execution(conn, exec_fix)["released_at"] is None
    verdict = _verdict(conn, exec_fix)
    assert verdict["outcome"] == "passed" and all(c["passed"] for c in verdict["checks"])
    assert [c["code"] for c in verdict["checks"]] == [
        "result_parsed", "result_ids_match", "commit_matches", "required_artifacts", "test_before_failed",
        "verification_passed",
    ]
    assert _check_code(worker, conn).results_checked == 0  # 한 번만 판정


def test_fix_result_missing_artifacts_needs_attention(exec_fix, worker, conn, store, clock):
    seed_code_result(conn, store, exec_fix, clock(), kinds=("diff",))

    _check_code(worker, conn)

    status, reason = _status(conn, TASK_A)
    assert status == "확인 필요"
    assert reason == "필수 산출물 누락: test_log_before, test_log_after, verification_log"
    verdict = _verdict(conn, exec_fix)
    assert verdict["outcome"] == "failed"
    assert not next(c for c in verdict["checks"] if c["code"] == "required_artifacts")["passed"]
    assert _task(conn, TASK_A)["finished_at"] is None


def test_fix_result_with_passing_before_test_or_failed_verification_needs_attention(
    exec_fix, worker, conn, store, clock
):
    seed_code_result(conn, store, exec_fix, clock(), before_exit=0, verification_exit=2)

    _check_code(worker, conn)

    status, reason = _status(conn, TASK_A)
    assert status == "확인 필요"
    assert "수정 전 테스트가 실패하지 않음" in reason and "vp-pytest exit 2" in reason


def test_broken_fix_result_is_preserved_and_needs_attention(exec_fix, worker, conn, store, clock):
    seed_code_result(conn, store, exec_fix, clock(), raw=b"not json {")

    _check_code(worker, conn)

    fix_row = repo.get_execution(conn, exec_fix)
    assert repo.read_artifact(conn, store, fix_row["result_artifact_id"]) == b"not json {"
    verdict = _verdict(conn, exec_fix)
    assert verdict["outcome"] == "failed" and [c["code"] for c in verdict["checks"]] == ["result_parsed"]
    assert _status(conn, TASK_A) == ("확인 필요", verdict["checks"][0]["detail"])


def test_fix_result_ids_must_match_the_execution(exec_fix, worker, conn, store, clock):
    seed_code_result(conn, store, exec_fix, clock(), result_execution_id="exec-other")

    _check_code(worker, conn)

    verdict = _verdict(conn, exec_fix)
    assert verdict["outcome"] == "failed"
    assert [c["code"] for c in verdict["checks"] if not c["passed"]] == ["result_ids_match"]
    assert _status(conn, TASK_A)[0] == "확인 필요"


def test_fix_needing_information_skips_artifact_checks(exec_fix, worker, conn, store, clock):
    """정보 요청 제출은 수정·검증이 없으므로 산출물 검사를 하지 않는다 — 산출물이 없어도 판정 통과."""
    seed_code_result(conn, store, exec_fix, clock(), kinds=(), outcome="needs_information")

    _check_code(worker, conn)

    verdict = _verdict(conn, exec_fix)
    assert verdict["outcome"] == "passed"
    assert [c["code"] for c in verdict["checks"]] == ["result_parsed", "result_ids_match", "commit_matches"]


# --- 실패 반영·관찰 ------------------------------------------------------------------------


def test_replayed_triage_result_after_review_failed_adds_no_execution(flow, worker, conn, store, clock):
    r = spawn_review(worker, conn, store, clock)
    _append(conn, r, 1, "accepted", {}, clock())
    _append(conn, r, 2, "failed", {"code": "timeout", "message": "Claude 실행이 20분을 초과해 종료했습니다.",
                                   "process_stopped": True}, clock())

    report = worker.tick()

    assert report.failures_reflected == 1
    task_r = _task(conn, TASK_R)
    assert (task_r["status"], task_r["status_reason"]) == ("실패", "timeout · Claude 실행이 20분을 초과해 종료했습니다.")
    assert task_r["finished_at"] == clock()
    assert repo.get_execution(conn, r)["released_at"] == clock()

    for _ in range(3):
        report = worker.tick()  # T 결과·판정은 그대로 남아 있다 — start_key 로 R 추가 실행 없음
        assert report.successors_created == 0 and report.failures_reflected == 0

    assert len(_executions(conn, TASK_R)) == 1


def test_failed_without_process_confirmation_needs_attention(flow, worker, conn, clock):
    _append(conn, EXEC_T, 1, "accepted", {}, clock())
    _append(conn, EXEC_T, 2, "failed", {"code": "timeout", "message": "종료 확인 실패", "process_stopped": False}, clock())

    worker.tick()

    task_t = _task(conn, TASK_T)
    assert (task_t["status"], task_t["status_reason"]) == ("확인 필요", "종료 미확인 — 재실행하지 않음")
    assert task_t["finished_at"] is None and repo.get_execution(conn, EXEC_T)["released_at"] is None
    assert _executions(conn, TASK_R) == []


def test_accepted_without_start_becomes_unknown_keeps_lock_and_recovers(flow, worker, conn, clock, settings):
    _append(conn, EXEC_T, 1, "accepted", {}, clock())
    clock.advance(settings.limits.unknown_after_seconds + 1)

    report = worker.tick()

    assert report.observations == 1
    assert repo.get_execution(conn, EXEC_T)["status"] == "unknown"
    observations = conn.execute("SELECT kind FROM execution_observations WHERE execution_id = ?", (EXEC_T,)).fetchall()
    assert [o["kind"] for o in observations] == ["unknown_no_start"]
    assert _status(conn, TASK_T) == ("확인 필요", "시작 여부 불명 — 재실행하지 않음")
    assert _task(conn, TASK_T)["finished_at"] is None
    assert worker.tick().observations == 0  # 한 번만 기록
    assert len(_executions(conn, TASK_T)) == 1

    # 기존 실행 주체의 재전송(started)으로 같은 실행을 복원. 새 프로세스를 만들지 않는다
    _append(conn, EXEC_T, 2, "started", {"runtime_ref": "pid:9"}, clock())
    worker.tick()
    assert repo.get_execution(conn, EXEC_T)["status"] == "running"
    assert len(_executions(conn, TASK_T)) == 1


def test_heartbeat_loss_is_observed_once_and_never_restarts(flow, worker, conn, clock, settings):
    _append(conn, EXEC_T, 1, "accepted", {}, clock())
    _append(conn, EXEC_T, 2, "started", {"runtime_ref": "pid:1"}, clock())
    clock.advance(settings.limits.heartbeat_offline_seconds + 1)

    report = worker.tick()

    assert report.observations == 1
    assert repo.get_execution(conn, EXEC_T)["status"] == "running"
    worker.tick()
    rows = conn.execute("SELECT kind FROM execution_observations WHERE execution_id = ?", (EXEC_T,)).fetchall()
    assert [r["kind"] for r in rows] == ["heartbeat_lost"]
    assert len(_executions(conn, TASK_T)) == 1


# --- phase 7: callback — 체인이 사람 차례(chain_settled)가 되면 1회 POST (ADR-0010) --------------------------


def _chain_row(conn):
    return repo.get_chain(conn, CHAIN_ID)


def _at(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _chain_flow_to_review_result(worker, conn, store, clock) -> str:
    """T 결과 → R 착수 → R 결과(approved) 제출까지. R 판정 tick 은 하지 않는다. 반환은 R 실행 ID."""
    r = spawn_review(worker, conn, store, clock)
    finish_review(conn, store, clock, r)
    return r


def test_callback_is_sent_once_when_chain_becomes_human_turn(conn, client, clock, make_worker, store, n8n_settings):
    """(a) T 실행 중 0 → (b) T 판정·R 착수 tick 0 (R 실행 요청됨) → (c) R 결과·판정 tick 1회 → (d) 이후·승인 뒤에도 1회."""
    _, token = seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient()
    worker = make_worker(n8n_settings, callbacks)

    for body in (event(EXEC_T, 1, "accepted", {}), event(EXEC_T, 2, "started", {"runtime_ref": "pid:1"})):
        response = client.post(f"/executions/{EXEC_T}/events", json=body, headers=bearer(token))
        assert response.status_code == 200, response.text
    report = worker.tick()  # T 실행 중 (저장 상태는 화면이 갱신한다 — 사람 차례 판정은 실행 상태로 한다)
    assert repo.get_execution(conn, EXEC_T)["status"] == "running"
    assert (report.callbacks_sent, report.callbacks_failed, callbacks.posts) == (0, 0, [])

    finish_triage(conn, store, clock, started=True)
    report = worker.tick()  # T 결과 → 판정 → R 착수 — 같은 tick 의 마지막 단계는 R 이 실행 요청됨이라 보내지 않는다
    assert report.successors_created == 1 and _status(conn, TASK_R) == ("실행 요청됨", "접수 대기")
    assert (report.callbacks_sent, callbacks.posts) == (0, [])

    r = _executions(conn, TASK_R)[0]["execution_id"]
    finish_review(conn, store, clock, r)
    report = worker.tick()  # R 결과 판정 → 확인 필요 · 검토 대기 → 사람 차례

    assert (report.generic_checked, report.callbacks_sent, report.callbacks_failed) == (1, 1, 0)
    assert report.any()
    assert len(callbacks.posts) == 1
    url, payload = callbacks.posts[0]
    assert url == CALLBACK_URL
    body = ChainCallback.model_validate(payload)
    assert (body.contract_version, body.chain_id, body.source, body.settled_at) == (1, CHAIN_ID, "n8n", clock())
    assert body.title == "보고서 변환 실패 분류 → 검토"
    assert body.chain_url is None
    assert (body.human_gate.label, body.human_gate.status_label, body.human_gate.reason) == (
        "검토 승인 (사람) · 병합은 운영자 확인", "확인 필요", "검토 대기"
    )
    task_t, task_r = body.tasks
    assert (task_t.task_id, task_t.key, task_t.kind, task_t.title) == (TASK_T, KEY_T, "triage", "보고서 변환 실패 분류")
    assert (task_t.status, task_t.status_reason) == ("확인 필요", "검토 대기")
    assert (task_t.outcome, task_t.summary) == ("ready_for_handoff", TRIAGE_SUMMARY)
    assert (task_r.task_id, task_r.key, task_r.kind, task_r.title) == (TASK_R, KEY_R, "review", "분류 결과 검토")
    assert (task_r.status, task_r.status_reason) == ("확인 필요", "검토 대기")
    assert (task_r.outcome, task_r.summary) == ("approved", REVIEW_SUMMARY)
    assert task_t.task_url is None and task_r.task_url is None
    chain = _chain_row(conn)
    assert chain["callback_sent_at"] == clock()
    assert (chain["callback_attempts"], chain["callback_next_at"], chain["callback_last_error"]) == (0, None, None)

    for _ in range(2):
        assert worker.tick().callbacks_sent == 0
    repo.update_task_status(conn, TASK_R, "완료", "검토 승인", finished_at=clock(), review_decision="approve", now=clock())
    repo.release_execution(conn, r, clock())
    clock.advance(600)
    report = worker.tick()
    assert (report.callbacks_sent, report.callbacks_failed) == (0, 0)
    assert len(callbacks.posts) == 1
    assert _chain_row(conn)["callback_sent_at"] != clock()


def test_failed_callback_backs_off_and_stops_after_five_attempts(conn, client, clock, make_worker, store, n8n_settings):
    """(e) 실패 → attempts 1·next_at now+30s·last_error. 29초 뒤 재시도 없음, 31초 뒤 재시도 → 60초. 5회 뒤 중단."""
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient(fail=True)
    worker = make_worker(n8n_settings, callbacks)
    _chain_flow_to_review_result(worker, conn, store, clock)

    report = worker.tick()

    assert (report.callbacks_sent, report.callbacks_failed) == (0, 1)
    assert len(callbacks.posts) == 1
    chain = _chain_row(conn)
    assert chain["callback_sent_at"] is None
    assert (chain["callback_attempts"], chain["callback_last_error"]) == (1, "HTTP 503")
    assert _at(chain["callback_next_at"]) == _at(clock()) + timedelta(seconds=30)

    clock.advance(29)
    assert worker.tick().callbacks_failed == 0 and len(callbacks.posts) == 1

    clock.advance(2)
    assert worker.tick().callbacks_failed == 1 and len(callbacks.posts) == 2
    chain = _chain_row(conn)
    assert chain["callback_attempts"] == 2
    assert _at(chain["callback_next_at"]) == _at(clock()) + timedelta(seconds=60)

    for attempts, backoff in ((3, 120), (4, 240), (5, 480)):
        clock.advance(backoff // 2 + 1)  # 직전 대기(backoff/2)를 넘긴다
        assert worker.tick().callbacks_failed == 1
        chain = _chain_row(conn)
        assert chain["callback_attempts"] == attempts
        assert _at(chain["callback_next_at"]) == _at(clock()) + timedelta(seconds=backoff)
    assert len(callbacks.posts) == 5

    clock.advance(3600)
    for _ in range(3):
        assert worker.tick().callbacks_failed == 0
    assert len(callbacks.posts) == 5
    chain = _chain_row(conn)
    assert chain["callback_sent_at"] is None and chain["callback_last_error"] == "HTTP 503"


def test_callback_succeeds_on_retry_and_clears_error(conn, client, clock, make_worker, store, n8n_settings):
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient(fail=True)
    worker = make_worker(n8n_settings, callbacks)
    _chain_flow_to_review_result(worker, conn, store, clock)
    worker.tick()
    assert _chain_row(conn)["callback_attempts"] == 1

    callbacks.fail = False
    clock.advance(31)
    report = worker.tick()

    assert (report.callbacks_sent, report.callbacks_failed) == (1, 0)
    chain = _chain_row(conn)
    assert chain["callback_sent_at"] == clock()
    assert (chain["callback_attempts"], chain["callback_last_error"]) == (1, None)
    assert len(callbacks.posts) == 2
    assert ChainCallback.model_validate(callbacks.posts[1][1]).settled_at == clock()
    assert worker.tick().callbacks_sent == 0 and len(callbacks.posts) == 2


def test_callback_when_successor_is_blocked_by_outcome_outside_rule(conn, client, clock, make_worker, store, n8n_settings):
    """T 가 needs_information 으로 판정 통과 → 규칙 밖 outcome 이라 R 은 착수하지 않는다 → 사람 차례.
    워커는 R 에 `확인 필요` + 규칙 이유를 저장하지만, callback 의 status 는 체인 화면과 같은 지금 판정(`views.status_of`)이라
    R 은 `대기 · 선행 대기`(선행 T 가 `확인 필요`)로 실린다 — 화면이 보이는 값과 같다."""
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient()
    worker = make_worker(n8n_settings, callbacks)
    finish_triage(conn, store, clock, outcome="needs_information")

    report = worker.tick()

    assert (report.generic_checked, report.successors_created, report.callbacks_sent) == (1, 0, 1)
    assert _status(conn, TASK_R) == ("확인 필요", "선행 outcome needs_information 은 규칙 대상 아님 — 확인 필요")
    body = ChainCallback.model_validate(callbacks.posts[0][1])
    assert [t.key for t in body.tasks] == [KEY_T, KEY_R]
    assert (body.tasks[0].status, body.tasks[0].status_reason, body.tasks[0].outcome) == (
        "확인 필요", "검토 대기", "needs_information"
    )
    assert (body.tasks[1].status, body.tasks[1].status_reason) == ("대기", "선행 대기")
    assert (body.tasks[1].outcome, body.tasks[1].summary) == (None, None)  # 결과 없음
    assert (body.human_gate.status_label, body.human_gate.reason) == ("대기", "선행 대기")  # 체인 화면의 사람 단계와 같은 값
    assert worker.tick().callbacks_sent == 0 and len(callbacks.posts) == 1


def test_callback_when_first_task_fails_verdict_and_successor_waits(conn, client, clock, make_worker, store, n8n_settings):
    """T 판정 실패(outcome 이 종류 목록 밖) → T `확인 필요`, R `대기 · 선행 대기` — 선행이 사람에게 막혔으니 사람 차례다."""
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient()
    worker = make_worker(n8n_settings, callbacks)
    finish_triage(conn, store, clock, outcome="merged")

    report = worker.tick()

    assert (report.generic_checked, report.successors_created, report.callbacks_sent) == (1, 0, 1)
    body = ChainCallback.model_validate(callbacks.posts[0][1])
    assert (body.tasks[0].status, body.tasks[0].outcome) == ("확인 필요", "merged")
    assert (body.tasks[1].status, body.tasks[1].status_reason, body.tasks[1].outcome) == ("대기", "선행 대기", None)
    assert worker.tick().callbacks_sent == 0


def test_chain_without_callback_url_sends_nothing(conn, client, clock, make_worker, store, n8n_settings):
    """(g) 직접 등록처럼 callback_url 이 없는 체인은 사람 차례가 돼도 아무것도 보내지 않는다."""
    seed_user_flow(conn, client, clock, chain=n8n_chain(callback_url=None))
    callbacks = FakeCallbackClient()
    worker = make_worker(n8n_settings, callbacks)
    _chain_flow_to_review_result(worker, conn, store, clock)

    report = worker.tick()

    assert _status(conn, TASK_R) == ("확인 필요", "검토 대기")
    assert (report.callbacks_sent, report.callbacks_failed, callbacks.posts) == (0, 0, [])
    chain = _chain_row(conn)
    assert (chain["callback_attempts"], chain["callback_sent_at"]) == (0, None)


def test_empty_allow_list_records_failure_without_posting(conn, client, clock, make_worker, store, settings):
    """(h) 허용 목록이 비면(접수 뒤 설정이 바뀐 경우) 보내지 않고 실패로 기록한다. 5회 뒤 멈춘다."""
    assert settings.callback_hosts == ()
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient()
    worker = make_worker(settings, callbacks)
    _chain_flow_to_review_result(worker, conn, store, clock)

    report = worker.tick()

    assert (report.callbacks_sent, report.callbacks_failed, callbacks.posts) == (0, 1, [])
    chain = _chain_row(conn)
    assert (chain["callback_attempts"], chain["callback_last_error"], chain["callback_next_at"]) == (1, "허용 목록 밖", None)
    assert chain["callback_sent_at"] is None
    for _ in range(4):
        assert worker.tick().callbacks_failed == 1
    assert _chain_row(conn)["callback_attempts"] == 5
    assert worker.tick().callbacks_failed == 0
    assert _chain_row(conn)["callback_attempts"] == 5 and callbacks.posts == []


def test_public_url_fills_chain_and_task_urls(conn, client, clock, make_worker, store, n8n_settings):
    """(i) WORKFLOW_PUBLIC_URL 이 있으면 chain_url·task_url 이 그 앞에 붙는다."""
    seed_user_flow(conn, client, clock, chain=n8n_chain())
    callbacks = FakeCallbackClient()
    worker = make_worker(dataclasses.replace(n8n_settings, public_url="http://127.0.0.1:8000"), callbacks)
    _chain_flow_to_review_result(worker, conn, store, clock)

    worker.tick()

    body = ChainCallback.model_validate(callbacks.posts[0][1])
    assert body.chain_url == f"http://127.0.0.1:8000/chains/{CHAIN_ID}"
    assert [t.task_url for t in body.tasks] == [
        f"http://127.0.0.1:8000/tasks/{TASK_T}", f"http://127.0.0.1:8000/tasks/{TASK_R}",
    ]


def test_callback_stage_runs_after_successor_scan_and_failure_reflection():
    """(b) 의 근거 — 후속 스캔·실패 반영 뒤다. 순서가 바뀌면 T 판정 → R 착수 사이에 보낼 수 있다.
    그 뒤는 외부 반영(초안 PR — phase 12, GitHub 원본 이슈 댓글 — step 12, 알림 웹훅 — phase 12 step 7)뿐이다.
    알림은 맨 뒤 — 같은 tick 에 쌓인 사람 요청·PR 열림·실패를 바로 보낸다."""
    calls = re.findall(r"self\.(_\w+)\(conn, report\)", inspect.getsource(Worker.tick))
    assert calls[-6:] == [
        "_spawn_successors", "_reflect_failures", "_deliver_callbacks", "_deliver_pull_requests", "_deliver_github",
        "_deliver_notifications",
    ]


# --- TickReport·run_forever -------------------------------------------------------------


def test_tick_report_defaults_to_zero():
    assert all(v == 0 for v in dataclasses.asdict(TickReport()).values())
    assert not TickReport().any() and TickReport(callbacks_sent=1).any() and TickReport(callbacks_failed=1).any()


def test_empty_db_tick_is_noop(worker):
    assert worker.tick() == TickReport()


def test_run_forever_sleeps_between_ticks(worker, monkeypatch):
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("workflow.server.worker.time.sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.run_forever(0.5)
    assert slept == [0.5]


# --- GitHub 수집 (phase 8 step 7) ---------------------------------------------------------


class _CountingGitHub:
    """list_issues 호출 수만 센다. 빈 마지막 페이지를 돌려주거나 `failures` 를 하나씩 던진다."""

    def __init__(self, failures=()):
        self.repos: list[str] = []
        self.failures = list(failures)

    def list_issues(self, repo_name, cursor):
        self.repos.append(repo_name)
        if self.failures:
            raise self.failures.pop(0)
        return IssuePage(issues=(), next_cursor=None, etag=None, not_modified=False, skipped_pull_requests=0)

    def get_issue(self, repo_name, number):
        raise AssertionError("선택 이슈 없음")


def _github_source(source_id: str, repository: str, *, enabled: bool = True) -> GitHubSourceConfig:
    return GitHubSourceConfig(
        source_id=source_id, repository_full_name=repository, workflow_repository_id="billing",
        label_filter=["bug"], selected_issue_numbers=[], start_at="2026-09-01T00:00:00Z",
        fix_verification_profile_id="vp-pytest", review_agent_id="agent-review", run_mode="auto",
        max_rework_rounds=1, enabled=enabled, config_revision=1,
    )


def test_worker_polls_enabled_github_sources_at_interval_and_waits_on_rate_limit(
    app, settings, store, clock, conn
):
    repo.create_session(conn, "sess-gh", clock.now)
    repo.save_github_source(conn, "sess-gh", _github_source("ghs-00000001", "acme/billing"), clock.now)
    repo.save_github_source(conn, "sess-gh", _github_source("ghs-00000002", "acme/lib", enabled=False), clock.now)
    gh = _CountingGitHub(failures=[GitHubRateLimited("GET /repos/acme/billing/issues: HTTP 429", 300, None)])
    worker = Worker(lambda: connect(settings.db_path), store, FakeCallbackClient(), settings, clock, github=gh)

    report = worker.tick()
    assert gh.repos == ["acme/billing"]  # 멈춘 소스는 부르지 않는다
    assert (report.sources_synced, report.sync_errors) == (1, 1)

    clock.advance(GITHUB_SYNC_INTERVAL_SECONDS)
    worker.tick()
    assert len(gh.repos) == 1  # rate limit 대기(300초) 중

    clock.advance(300)
    report = worker.tick()
    assert len(gh.repos) == 2 and report.sync_errors == 0

    worker.tick()
    assert len(gh.repos) == 2  # 간격 안에서는 다시 부르지 않는다
    clock.advance(GITHUB_SYNC_INTERVAL_SECONDS)
    worker.tick()
    assert len(gh.repos) == 3


def test_worker_without_github_client_does_not_sync(worker, conn, clock):
    repo.create_session(conn, "sess-gh", clock.now)
    repo.save_github_source(conn, "sess-gh", _github_source("ghs-00000001", "acme/billing"), clock.now)
    assert worker.tick().sources_synced == 0


def test_worker_syncs_each_source_with_its_own_client_and_skips_unconnected(app, settings, store, clock, conn):
    """소스별 자격(ADR-0017): App 설치·PAT·환경변수 중 소스에 맞는 클라이언트. 자격이 없는 소스는 수집하지 않는다."""
    repo.create_session(conn, "sess-gh", clock.now)
    repo.save_github_source(conn, "sess-gh", _github_source("ghs-00000001", "acme/billing"), clock.now)
    repo.save_github_source(conn, "sess-gh", _github_source("ghs-00000002", "acme/lib"), clock.now)
    billing = _CountingGitHub()
    asked: list[str] = []

    def github_for(config):
        asked.append(config.source_id)
        return billing if config.repository_full_name == "acme/billing" else None

    worker = Worker(lambda: connect(settings.db_path), store, FakeCallbackClient(), settings, clock,
                    github_for=github_for)
    report = worker.tick()
    assert billing.repos == ["acme/billing"]
    assert (report.sources_synced, report.sync_errors) == (1, 0)
    assert {"ghs-00000001", "ghs-00000002"} <= set(asked)


def test_configure_logging_silences_http_request_lines():
    """httpx 는 요청 줄을 URL 그대로 INFO 로 남긴다 — callback URL 의 `?signature=…` 가 워커 로그에 새지 않게."""
    import logging

    from workflow.server.worker import configure_logging

    root = logging.getLogger()
    loggers = [root, logging.getLogger("httpx"), logging.getLogger("httpcore")]
    saved = [lg.level for lg in loggers]
    saved_handlers = root.handlers[:]
    root.handlers.clear()  # `python3 -m workflow.server.worker` 처럼 핸들러 없는 루트에서 시작
    try:
        configure_logging()
        for name in ("httpx", "httpcore"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO)
            assert logging.getLogger(name).isEnabledFor(logging.WARNING)
        assert logging.getLogger("workflow.worker").isEnabledFor(logging.INFO)
    finally:
        root.handlers[:] = saved_handlers
        for lg, level in zip(loggers, saved, strict=True):
            lg.setLevel(level)
