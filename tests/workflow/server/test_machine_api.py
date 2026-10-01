"""machine_api.py — 연결 프로그램이 쓰는 중앙 API. CONTRACT 2절(claim)·3절(이벤트·오류표)·4절(산출물)."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import ExecutionRequest, KindSpec
from workflow.server import views
from workflow.server.app import create_app
from workflow.server.auth import SELFHOST_SESSION_ID, utc_now

from .conftest import (
    BASE_COMMIT,
    EXEC_FIX,
    LOCAL_REGISTRATION,
    NOW,
    SESSION,
    TASK_A,
    TASK_B,
    bearer,
    event,
    exchange,
    meta_for,
    request_body,
    seed_agents,
    seed_execution,
    task_row,
)

UNAUTHENTICATED = {
    "code": "unauthenticated", "message": "유효한 연결 토큰이 필요합니다.", "field": None, "details": None,
}


def _post_event(client, headers, execution_id, body):
    return client.post(f"/executions/{execution_id}/events", json=body, headers=headers)


def _upload(client, headers, execution_id, data: bytes, **meta_overrides):
    meta = {**meta_for(data), **meta_overrides}
    return client.post(
        f"/executions/{execution_id}/artifacts",
        data={"meta": json.dumps(meta)},
        files={"file": (meta["name"], data, meta["content_type"])},
        headers=headers,
    )


# --- exchange ---------------------------------------------------------------


def test_exchange_returns_token_once_and_code_is_single_use(client, seeded):
    code = repo.issue_connect_code(seeded, utc_now())
    first = client.post("/connector/exchange", json={"contract_version": 1, "connect_code": code})
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"connector_id", "token"}
    assert body["token"].startswith("wfc_")
    assert repo.authenticate_connector(seeded, body["token"]) == body["connector_id"]

    again = client.post("/connector/exchange", json={"contract_version": 1, "connect_code": code})
    assert again.status_code == 404
    assert again.json()["code"] == "not_found"
    assert again.json()["field"] == "connect_code"

    heartbeat = client.post(
        "/connector/heartbeat",
        json={"contract_version": 1, "connector_id": body["connector_id"], "current_execution_id": None},
        headers=bearer(body["token"]),
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json() == {}


def test_exchange_unknown_code_404(client, seeded):
    response = client.post("/connector/exchange", json={"contract_version": 1, "connect_code": "nope"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_exchange_rejects_unknown_field_and_bad_version(client, seeded):
    response = client.post("/connector/exchange", json={"contract_version": 1, "connect_code": "c", "x": 1})
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"
    response = client.post("/connector/exchange", json={"contract_version": 2, "connect_code": "c"})
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_contract_version"


# --- 인증 (CONTRACT 3절 401) -------------------------------------------------


def test_events_without_token_401(client, exec_fix):
    response = client.post(f"/executions/{exec_fix}/events", json=event(exec_fix, 1, "accepted", {}))
    assert response.status_code == 401
    assert response.json() == UNAUTHENTICATED


def test_events_with_revoked_token_401(client, seeded, connector, exec_fix):
    connector_id, token = connector
    repo.revoke_connector(seeded, connector_id, utc_now())
    response = _post_event(client, bearer(token), exec_fix, event(exec_fix, 1, "accepted", {}))
    assert response.status_code == 401
    assert response.json() == UNAUTHENTICATED


def test_unauthenticated_wins_over_invalid_body(client, exec_fix):
    response = client.post(f"/executions/{exec_fix}/events", json={"contract_version": 2})
    assert response.status_code == 401


# --- claim (CONTRACT 2절) ----------------------------------------------------


def test_claim_204_then_assignment_then_204_after_accepted(client, seeded, connector, headers):
    connector_id, _ = connector
    claim = {"contract_version": 1, "connector_id": connector_id}
    empty = client.post("/connector/claim", json=claim, headers=headers)
    assert empty.status_code == 204
    assert empty.content == b""

    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=connector_id)
    assigned = client.post("/connector/claim", json=claim, headers=headers)
    assert assigned.status_code == 200
    # 저장된 request_json 그대로 — 칸이 없던 요청의 업무 키 두 칸은 기본값으로 싣는다 (CONTRACT 15.2)
    assert assigned.json() == {**request_body(EXEC_FIX, TASK_B), "work_key": None, "branch_seq": 1}

    repeat = client.post("/connector/claim", json=claim, headers=headers)  # 접수 전 재조회는 같은 배정
    assert repeat.status_code == 200
    assert repeat.json()["execution_id"] == EXEC_FIX

    accepted = _post_event(client, headers, EXEC_FIX, event(EXEC_FIX, 1, "accepted", {}))
    assert accepted.status_code == 200
    assert client.post("/connector/claim", json=claim, headers=headers).status_code == 204


def test_claim_records_supported_kinds_declaration(client, seeded, connector, headers):
    """claim 본문의 `supported_kinds` 를 connectors 에 남긴다 — 준비 판정(executor_outdated)의 입력. 생략하면 NULL."""
    connector_id, _ = connector

    def stored():
        return repo.get_connector(seeded, connector_id)["supported_kinds_json"]

    declared = {"contract_version": 1, "connector_id": connector_id, "supported_kinds": ["bug_fix", "code_review"]}
    assert client.post("/connector/claim", json=declared, headers=headers).status_code == 204
    assert json.loads(stored()) == ["bug_fix", "code_review"]

    legacy = {"contract_version": 1, "connector_id": connector_id}
    assert client.post("/connector/claim", json=legacy, headers=headers).status_code == 204
    assert stored() is None


def _seed_verify_only(conn, connector_id: str) -> None:
    body = {**request_body(EXEC_FIX, TASK_B, inputs=["art-previous"]), "verify_only_commit": "c" * 40}
    repo.create_execution(
        conn, execution_id=EXEC_FIX, task_id=TASK_B, attempt_no=1, start_key="reverify:hr-1",
        agent_id="agent-codex-mac", kind="bug_fix", request=ExecutionRequest.model_validate(body),
        assigned_connector_id=connector_id, predecessor_execution_id=None, now=utc_now(),
    )


def test_claim_records_known_runner_capabilities(client, seeded, connector, headers):
    """claim 의 `capabilities` 중 알려진 값만 정렬해 connectors 에 남긴다. 생략하면 NULL(보고 없음)."""
    connector_id, _ = connector

    def stored():
        return repo.get_connector(seeded, connector_id)["capabilities_json"]

    reported = {"contract_version": 1, "connector_id": connector_id, "capabilities": ["someday_feature", "verify_only"]}
    assert client.post("/connector/claim", json=reported, headers=headers).status_code == 204
    assert json.loads(stored()) == ["verify_only"]

    empty = {**reported, "capabilities": []}
    assert client.post("/connector/claim", json=empty, headers=headers).status_code == 204
    assert json.loads(stored()) == []

    legacy = {"contract_version": 1, "connector_id": connector_id}
    assert client.post("/connector/claim", json=legacy, headers=headers).status_code == 204
    assert stored() is None


def test_verify_only_execution_goes_only_to_a_runner_reporting_verify_only(client, seeded, connector, headers):
    connector_id, _ = connector
    _seed_verify_only(seeded, connector_id)
    assert repo.get_execution(seeded, EXEC_FIX)["verify_only"] == 1  # create_execution 이 요청에서 채운다

    legacy = {"contract_version": 1, "connector_id": connector_id}
    assert client.post("/connector/claim", json=legacy, headers=headers).status_code == 204
    other = {**legacy, "capabilities": ["someday_feature"]}
    assert client.post("/connector/claim", json=other, headers=headers).status_code == 204

    assigned = client.post("/connector/claim", json={**legacy, "capabilities": ["verify_only"]}, headers=headers)
    assert assigned.status_code == 200
    assert (assigned.json()["execution_id"], assigned.json()["verify_only_commit"]) == (EXEC_FIX, "c" * 40)


def test_ordinary_execution_keeps_verify_only_zero_and_goes_to_old_runners(client, seeded, connector, headers):
    connector_id, _ = connector
    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=connector_id)
    assert repo.get_execution(seeded, EXEC_FIX)["verify_only"] == 0
    legacy = {"contract_version": 1, "connector_id": connector_id}
    assert client.post("/connector/claim", json=legacy, headers=headers).status_code == 200


def test_claim_connector_id_must_match_token(client, connector, headers):
    response = client.post(
        "/connector/claim", json={"contract_version": 1, "connector_id": "conn-someone-else"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_claim_returns_only_own_assignments(client, seeded, connector):
    mine_id, mine_token = connector
    other_id, other_token = exchange(client, seeded)
    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=other_id)

    mine = client.post(
        "/connector/claim", json={"contract_version": 1, "connector_id": mine_id}, headers=bearer(mine_token)
    )
    assert mine.status_code == 204
    other = client.post(
        "/connector/claim", json={"contract_version": 1, "connector_id": other_id}, headers=bearer(other_token)
    )
    assert other.status_code == 200
    assert other.json()["execution_id"] == EXEC_FIX


def test_claim_registration_heads_update_only_own_agents_base_commit(client, seeded, connector, headers):
    """CONTRACT 14.1 — 보고된 origin 기본 브랜치 커밋이 이 연결 프로그램 Agent 의 `base_commit` 이 된다."""
    mine_id, _ = connector
    other_id, other_token = exchange(client, seeded)
    repo.update_registration(seeded, LOCAL_REGISTRATION, connector_id=mine_id, repository_id="demo-report-repo",
                             base_commit=BASE_COMMIT, verification_profile_ids=[], discovered={}, now=utc_now())
    latest = "7c1d9e2f4a6b8c0d1e3f5a7b9c2d4e6f8a0b1c3d"

    def base() -> str:
        return repo.get_agent(seeded, "agent-codex-mac")["base_commit"]

    stolen = {"contract_version": 1, "connector_id": other_id, "registration_heads": {LOCAL_REGISTRATION: "e" * 40}}
    assert client.post("/connector/claim", json=stolen, headers=bearer(other_token)).status_code == 204
    assert base() == BASE_COMMIT  # 다른 연결 프로그램의 보고는 무시

    body = {"contract_version": 1, "connector_id": mine_id,
            "registration_heads": {LOCAL_REGISTRATION: latest, "local-unknown": "e" * 40}}
    assert client.post("/connector/claim", json=body, headers=headers).status_code == 204
    assert base() == latest

    legacy = {"contract_version": 1, "connector_id": mine_id}  # 칸 없는 구버전 요청 — 이전 값 유지
    assert client.post("/connector/claim", json=legacy, headers=headers).status_code == 204
    assert base() == latest


def test_claim_rejects_bad_registration_head(client, seeded, connector, headers):
    connector_id, _ = connector
    for bad in ("E" * 40, "e" * 39, "main"):
        body = {"contract_version": 1, "connector_id": connector_id, "registration_heads": {LOCAL_REGISTRATION: bad}}
        response = client.post("/connector/claim", json=body, headers=headers)
        assert response.status_code == 422, bad


# --- 이벤트 (CONTRACT 3절) ----------------------------------------------------


def test_event_sequence_accepted_started_progress(client, headers, exec_fix, seeded):
    r1 = _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {}))
    assert r1.status_code == 200
    assert r1.json() == {"execution_id": exec_fix, "last_event_seq": 1, "status": "accepted"}
    r2 = _post_event(client, headers, exec_fix, event(exec_fix, 2, "started", {"runtime_ref": "pid:48213"}))
    assert r2.json() == {"execution_id": exec_fix, "last_event_seq": 2, "status": "running"}
    r3 = _post_event(client, headers, exec_fix, event(exec_fix, 3, "progress", {"message": "재현 중"}))
    assert r3.json() == {"execution_id": exec_fix, "last_event_seq": 3, "status": "running"}

    rows = repo.list_events(seeded, exec_fix)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert {r["actor"] for r in rows} == {f"connector:{repo.get_execution(seeded, exec_fix)['assigned_connector_id']}"}


def test_duplicate_same_seq_same_content_200_without_reapply(client, headers, running):
    body = event(running, 3, "progress", {"message": "재현 테스트 작성, 수정 전 실행 실패 확인"})
    response = _post_event(client, headers, running, body)
    assert response.status_code == 200
    assert response.json() == {"execution_id": running, "last_event_seq": 3, "status": "running"}


def test_same_seq_different_content_409_event_conflict(client, headers, running):
    body = event(running, 3, "progress", {"message": "다른 메시지"})
    response = _post_event(client, headers, running, body)
    assert response.status_code == 409
    assert response.json() == {
        "code": "event_conflict",
        "message": "seq 3은 다른 내용으로 이미 저장되었습니다.",
        "field": "seq",
        "details": None,
    }


def test_sequence_gap_409_with_expected_seq(client, headers, running):
    body = event(running, 5, "progress", {"message": "순번 건너뜀"})
    response = _post_event(client, headers, running, body)
    assert response.status_code == 409
    assert response.json() == {
        "code": "sequence_gap",
        "message": "seq 4가 먼저 필요합니다.",
        "field": "seq",
        "details": {"expected_seq": 4},
    }


def test_started_after_result_ready_409_invalid_transition(client, headers, running):
    data = b"diff --git a/x b/x\n"
    uploaded = _upload(client, headers, running, data)
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["artifact_id"]
    ready = _post_event(client, headers, running, event(running, 4, "result_ready", {"result_artifact_id": artifact_id}))
    assert ready.status_code == 200
    assert ready.json()["status"] == "result_ready"

    late = _post_event(client, headers, running, event(running, 5, "started", {"runtime_ref": "pid:2"}))
    assert late.status_code == 409
    assert late.json() == {
        "code": "invalid_transition",
        "message": "result_ready 상태에서는 started를 받을 수 없습니다.",
        "field": "type",
        "details": {"current_status": "result_ready"},
    }
    same_again = _post_event(client, headers, running, event(running, 4, "result_ready", {"result_artifact_id": artifact_id}))
    assert same_again.status_code == 200  # 이미 반영한 과거 이벤트의 동일 재전송만 허용


def test_progress_when_not_running_409_invalid_transition(client, headers, exec_fix):
    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    response = _post_event(client, headers, exec_fix, event(exec_fix, 2, "progress", {"message": "x"}))
    assert response.status_code == 409
    assert response.json() == {
        "code": "invalid_transition",
        "message": "accepted 상태에서는 progress를 받을 수 없습니다.",
        "field": "type",
        "details": {"current_status": "accepted"},
    }


def test_result_ready_without_artifact_409(client, headers, running):
    response = _post_event(client, headers, running, event(running, 4, "result_ready", {"result_artifact_id": "art-none"}))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "invalid_transition"
    assert body["details"] == {"reason": "result_artifact_missing"}
    assert body["field"] == "type"


def test_failed_event_records_code_and_process_stopped(client, headers, running, seeded):
    body = event(running, 4, "failed", {"code": "timeout", "message": "Codex 실행이 20분을 초과해 종료했습니다.", "process_stopped": True})
    response = _post_event(client, headers, running, body)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    row = repo.get_execution(seeded, running)
    assert (row["failed_code"], row["process_stopped"]) == ("timeout", 1)


# --- 단계 상태 재계산 (ARCHITECTURE "단계 상태 재계산 (step 4)") -----------------------------


def _stage_status(conn, task_id: str) -> tuple[str, str]:
    row = repo.get_task(conn, task_id)
    return row["status"], row["status_reason"]


def _panel_status(client, conn, task_id: str) -> tuple[str, str]:
    summary = views.task_summary(conn, repo.get_task(conn, task_id), now=utc_now(), settings=client.app.state.settings)
    return summary["status"].label, summary["status"].reason


def test_cycle_stage_status_follows_runner_events(client, headers, exec_fix, seeded):
    """재현: 순환 종류(bug_fix) 단계가 실행 running 뒤에도 `실행 요청됨 · 접수 대기` 로 남던 결함."""
    repo.update_task_status(seeded, TASK_A, "실행 요청됨", "접수 대기", now=NOW)

    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    assert _stage_status(seeded, TASK_A) == ("실행 요청됨", "접수 확인")

    assert _post_event(client, headers, exec_fix, event(exec_fix, 2, "started", {"runtime_ref": "pid:1"})).status_code == 200
    assert _stage_status(seeded, TASK_A) == ("실행 중", "시작 확인")
    assert _panel_status(client, seeded, TASK_A) == ("실행 중", "시작 확인")

    assert _post_event(client, headers, exec_fix, event(exec_fix, 3, "progress", {"message": "재현 중"})).status_code == 200
    assert _stage_status(seeded, TASK_A) == ("실행 중", "재현 중")
    assert _panel_status(client, seeded, TASK_A) == ("실행 중", "재현 중")


def test_resent_event_leaves_stage_status_and_events_unchanged(client, headers, running, seeded):
    before = (_stage_status(seeded, TASK_A), len(repo.list_task_events(seeded, TASK_A)))
    body = event(running, 3, "progress", {"message": "재현 테스트 작성, 수정 전 실행 실패 확인"})
    assert _post_event(client, headers, running, body).status_code == 200
    assert _post_event(client, headers, running, body).status_code == 200
    assert (_stage_status(seeded, TASK_A), len(repo.list_task_events(seeded, TASK_A))) == before
    assert before[0] == ("실행 중", "재현 테스트 작성, 수정 전 실행 실패 확인")


def test_late_event_does_not_reopen_finished_stage(client, headers, exec_fix, seeded):
    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    repo.update_task_status(seeded, TASK_A, "실패", "검토 거절", finished_at=NOW, now=NOW)
    events_before = len(repo.list_task_events(seeded, TASK_A))

    assert _post_event(client, headers, exec_fix, event(exec_fix, 2, "started", {"runtime_ref": "pid:1"})).status_code == 200
    assert _stage_status(seeded, TASK_A) == ("실패", "검토 거절")
    assert len(repo.list_task_events(seeded, TASK_A)) == events_before


def test_failed_event_leaves_stage_and_work_open_for_the_worker(client, headers, running, seeded):
    """실행 실패 마감(단계 `실패`·`stage_failed` 요청)은 워커가 한 트랜잭션으로 쓴다. 이벤트 경로가 먼저 `실패` 를 저장하면
    요청 없이 모든 단계가 닫혀 업무가 `종료`(끝 상태)로 굳는다 — e2e `test_real_repo` test_07 이 잡은 회귀(phase 17 step 11)."""
    before = _stage_status(seeded, TASK_A)
    body = event(running, 4, "failed", {"code": "commit_mismatch", "message": "도구가 직접 커밋", "process_stopped": True})
    assert _post_event(client, headers, running, body).status_code == 200

    assert _stage_status(seeded, TASK_A) == before
    work = repo.work_item_of_task(seeded, TASK_A)
    assert work["status"] not in ("종료", "완료") and work["closed_at"] is None


def test_non_cycle_stage_status_stays_live(client, headers, connector, seeded):
    """비순환(사용자 정의) 종류는 화면이 지금처럼 실시간 판정이고, 저장값도 같은 판정으로 맞춰진다."""
    spec = KindSpec(
        kind="classify", label="분류", capability_code="code.fix", scope_key="repository_id",
        input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="분류하세요.", builtin=False,
    )
    repo.insert_kind(seeded, SESSION, spec, NOW)
    repo.insert_work_item_task(seeded, {**task_row("task-triage-1"), "kind": "classify", "criteria": []}, NOW)
    request = {**request_body("exec-triage-001", "task-triage-1", "classify"),
               "target": {"local_registration_id": LOCAL_REGISTRATION}, "kind_spec": spec.model_dump()}
    repo.create_execution(
        seeded, execution_id="exec-triage-001", task_id="task-triage-1", attempt_no=1, start_key="auto:task-triage-1:r1",
        agent_id="agent-codex-mac", kind="classify", request=ExecutionRequest.model_validate(request),
        assigned_connector_id=connector[0], predecessor_execution_id=None, now=NOW,
    )
    for body in (event("exec-triage-001", 1, "accepted", {}),
                 event("exec-triage-001", 2, "started", {"runtime_ref": "pid:1"})):
        assert _post_event(client, headers, "exec-triage-001", body).status_code == 200
    assert _panel_status(client, seeded, "task-triage-1") == ("실행 중", "시작 확인")
    assert _stage_status(seeded, "task-triage-1") == ("실행 중", "시작 확인")


def test_events_for_other_connectors_execution_403(client, seeded, connector, exec_fix):
    other_id, other_token = exchange(client, seeded)
    response = _post_event(client, bearer(other_token), exec_fix, event(exec_fix, 1, "accepted", {}))
    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden",
        "message": f"{exec_fix}은 {other_id}에 배정되지 않았습니다.",
        "field": None,
        "details": None,
    }


def test_events_for_unassigned_execution_403(client, seeded, connector, headers):
    seed_execution(seeded, "exec-unassigned-001", TASK_A, connector_id=None)
    response = _post_event(client, headers, "exec-unassigned-001", event("exec-unassigned-001", 1, "accepted", {}))
    assert response.status_code == 403


def test_events_unknown_execution_404(client, headers):
    response = _post_event(client, headers, "exec-none", event("exec-none", 1, "accepted", {}))
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "execution exec-none을 찾을 수 없습니다.",
        "field": "execution_id",
        "details": None,
    }


def test_events_unsupported_contract_version_422(client, headers, exec_fix):
    body = {**event(exec_fix, 1, "accepted", {}), "contract_version": 2}
    response = _post_event(client, headers, exec_fix, body)
    assert response.status_code == 422
    assert response.json() == {
        "code": "unsupported_contract_version",
        "message": "contract_version 2는 지원하지 않습니다.",
        "field": "contract_version",
        "details": None,
    }


def test_events_unknown_field_422(client, headers, exec_fix):
    body = {**event(exec_fix, 1, "accepted", {}), "extra": "x"}
    response = _post_event(client, headers, exec_fix, body)
    assert response.status_code == 422
    assert response.json() == {
        "code": "unknown_field",
        "message": "필드 extra는 허용되지 않습니다.",
        "field": "extra",
        "details": None,
    }


def test_events_execution_id_mismatch_422(client, headers, exec_fix):
    response = _post_event(client, headers, exec_fix, event("exec-other", 1, "accepted", {}))
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"
    assert response.json()["field"] == "execution_id"


# --- 산출물 (CONTRACT 4절) ----------------------------------------------------


def test_upload_201_then_200_then_result_ready(client, headers, running, seeded, store):
    data = b"diff --git a/report.py b/report.py\n"
    meta = meta_for(data)
    first = _upload(client, headers, running, data)
    assert first.status_code == 201
    body = first.json()
    assert body["kind"] == "diff"
    assert body["sha256"] == meta["sha256"]
    assert body["size"] == len(data)
    assert set(body) == {"artifact_id", "kind", "sha256", "size"}
    assert store.exists(meta["sha256"])

    second = _upload(client, headers, running, data)
    assert second.status_code == 200
    assert second.json() == body

    ready = _post_event(client, headers, running, event(running, 4, "result_ready", {"result_artifact_id": body["artifact_id"]}))
    assert ready.status_code == 200
    assert repo.get_execution(seeded, running)["result_artifact_id"] == body["artifact_id"]


def test_upload_hash_mismatch_422(client, headers, running):
    data = b"real bytes"
    response = _upload(client, headers, running, data, sha256=hashlib.sha256(b"other").hexdigest())
    assert response.status_code == 422
    assert response.json()["code"] == "hash_mismatch"
    response = _upload(client, headers, running, data, size=len(data) + 1)
    assert response.status_code == 422
    assert response.json()["code"] == "hash_mismatch"


def test_upload_rejects_bad_meta(client, headers, running):
    data = b"x"
    response = _upload(client, headers, running, data, extra="no")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field"
    response = _upload(client, headers, running, data, kind="not-a-kind")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"
    response = client.post(
        f"/executions/{running}/artifacts",
        data={"meta": "{broken"},
        files={"file": ("a", data, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"


def test_upload_to_other_connectors_or_unknown_execution(client, seeded, connector, exec_fix):
    _, other_token = exchange(client, seeded)
    assert _upload(client, bearer(other_token), exec_fix, b"x").status_code == 403
    assert _upload(client, bearer(other_token), "exec-none", b"x").status_code == 404
    assert _upload(client, {}, exec_fix, b"x").status_code == 401


def test_download_allows_inputs_manifest_attachments_and_own_outputs(client, seeded, store, connector):
    """B 실행은 입력 handoff bundle, 그 manifest 의 선행 결과·inputs·첨부, 자기 산출물만 내려받는다 (CONTRACT 4절)."""
    connector_id, token = connector
    headers = bearer(token)
    seed_execution(seeded, "exec-a", TASK_A)
    evidence = b'{"report_date": "2026-09-19", "data": {"records": []}}'
    ev, _ = repo.store_artifact(
        seeded, store, execution_id="exec-a", session_id=SESSION,
        meta=_meta_model(evidence, "evidence", "response-after.json"), data=evidence, now=utc_now(),
    )
    result = b'{"outcome": "ready_for_review"}'
    res, _ = repo.store_artifact(
        seeded, store, execution_id="exec-a", session_id=SESSION,
        meta=_meta_model(result, "code_change_result", "code_change_result.json"), data=result, now=utc_now(),
    )
    trace_bytes = b"[]"
    trace, _ = repo.store_artifact(
        seeded, store, execution_id="exec-a", session_id=SESSION,
        meta=_meta_model(trace_bytes, "tool_trace", "trace.json"), data=trace_bytes, now=utc_now(),
    )
    manifest = json.dumps({
        "contract_version": 1,
        "source_execution_id": "exec-a",
        "source_kind": "bug_fix",
        "source_result_artifact_id": res.artifact_id,
        "inputs": [],
        "attachments": [{
            "evidence_id": "response-after", "version": "1", "content_type": "application/json",
            "artifact_id": ev.artifact_id, "sha256": ev.sha256,
        }],
    }).encode()
    bundle, _ = repo.store_artifact(
        seeded, store, execution_id="exec-a", session_id=SESSION,
        meta=_meta_model(manifest, "handoff_bundle", "manifest.json"), data=manifest, now=utc_now(),
    )
    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=connector_id, inputs=[bundle.artifact_id], predecessor="exec-a")
    own = b"diff"
    mine = _upload(client, headers, EXEC_FIX, own).json()

    got_bundle = client.get(f"/executions/{EXEC_FIX}/artifacts/{bundle.artifact_id}", headers=headers)
    assert got_bundle.status_code == 200
    assert got_bundle.content == manifest
    assert got_bundle.headers["content-type"].startswith("application/json")

    got_evidence = client.get(f"/executions/{EXEC_FIX}/artifacts/{ev.artifact_id}", headers=headers)
    assert got_evidence.status_code == 200
    assert got_evidence.content == evidence

    got_mine = client.get(f"/executions/{EXEC_FIX}/artifacts/{mine['artifact_id']}", headers=headers)
    assert got_mine.status_code == 200
    assert got_mine.content == own
    assert got_mine.headers["content-type"].startswith("text/plain")

    got_result = client.get(f"/executions/{EXEC_FIX}/artifacts/{res.artifact_id}", headers=headers)  # source_result_artifact_id
    assert got_result.status_code == 200

    denied = client.get(f"/executions/{EXEC_FIX}/artifacts/{trace.artifact_id}", headers=headers)  # 나열되지 않은 A 산출물
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"
    assert client.get(f"/executions/{EXEC_FIX}/artifacts/art-none", headers=headers).status_code == 403
    assert client.get(f"/executions/exec-none/artifacts/{bundle.artifact_id}", headers=headers).status_code == 404
    _, other_token = exchange(client, seeded)
    assert client.get(f"/executions/{EXEC_FIX}/artifacts/{bundle.artifact_id}", headers=bearer(other_token)).status_code == 403


def _meta_model(data: bytes, kind: str, name: str):
    from workflow.contracts.v1 import ArtifactMeta

    return ArtifactMeta.model_validate(meta_for(data, kind, name, "application/json"))


# --- heartbeat ---------------------------------------------------------------


def test_heartbeat_touches_connector_and_marks_its_agents_online(client, seeded, connector, headers):
    connector_id, _ = connector
    seed_agents(seeded, with_claude=True)  # 이 러너에 묶이지 않은 agent-claude-mac (connection_state unknown)
    repo.update_registration(
        seeded, LOCAL_REGISTRATION, connector_id=connector_id, repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={}, now="2026-09-19T00:00:00Z",
    )
    repo.set_agent_connection(seeded, "agent-codex-mac", "offline", "2026-09-19T00:00:00Z")

    response = client.post(
        "/connector/heartbeat",
        json={"contract_version": 1, "connector_id": connector_id, "current_execution_id": "exec-fix-001"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {}
    agent = repo.get_agent(seeded, "agent-codex-mac")
    assert agent["connection_state"] == "online"
    assert agent["last_seen_at"] > "2026-09-19T00:00:00Z"
    assert repo.get_agent(seeded, "agent-claude-mac")["connection_state"] == "unknown"  # 다른 에이전트는 그대로
    row = seeded.execute("SELECT * FROM connectors WHERE connector_id = ?", (connector_id,)).fetchone()
    assert row["current_execution_id"] == "exec-fix-001"
    assert row["last_seen_at"] is not None


def _heartbeat(client, headers, connector_id, current):
    return client.post(
        "/connector/heartbeat",
        json={"contract_version": 1, "connector_id": connector_id, "current_execution_id": current},
        headers=headers,
    )


def test_heartbeat_says_when_the_reported_execution_is_closed_centrally(client, seeded, connector, headers, running):
    """러너가 재시작 뒤 붙잡고 있는 실행을 중앙이 이미 마감했으면 알려 준다 — 러너가 내려놓고 새 업무를 받게(실연동 1)."""
    connector_id, _ = connector
    assert _heartbeat(client, headers, connector_id, running).json() == {}  # 아직 진행 중

    repo.fail_execution(seeded, running, code="operator_closed", message="운영자 종료", now="2026-09-20T02:00:00Z")
    assert _heartbeat(client, headers, connector_id, running).json() == {"current_execution_closed": True}

    repo.release_execution(seeded, running, "2026-09-20T02:00:00Z")
    assert _heartbeat(client, headers, connector_id, running).json() == {"current_execution_closed": True}


def test_heartbeat_does_not_speak_about_other_or_unknown_executions(client, seeded, connector, exec_fix):
    other_id, other_token = exchange(client, seeded)
    repo.fail_execution(seeded, exec_fix, code="operator_closed", message="운영자 종료", now="2026-09-20T02:00:00Z")
    assert _heartbeat(client, bearer(other_token), other_id, exec_fix).json() == {}  # 다른 러너에 배정된 실행
    assert _heartbeat(client, bearer(other_token), other_id, "exec-none").json() == {}
    assert _heartbeat(client, bearer(other_token), other_id, None).json() == {}


def test_heartbeat_connector_id_must_match_token(client, connector, headers):
    response = client.post(
        "/connector/heartbeat",
        json={"contract_version": 1, "connector_id": "conn-other", "current_execution_id": None},
        headers=headers,
    )
    assert response.status_code == 403


# --- registrations -----------------------------------------------------------


def _registration(connector_id: str, **overrides) -> dict:
    body = {
        "contract_version": 1,
        "connector_id": connector_id,
        "local_registration_id": LOCAL_REGISTRATION,
        "tool": "codex",
        "repository_id": "demo-report-repo",
        "base_commit": BASE_COMMIT,
        "verification_profile_ids": ["vp-pytest"],
        "discovered": {"agents_md": {"found": True, "path_hint": "AGENTS.md"}, "codex_version": "0.155.1"},
    }
    body.update(overrides)
    return body


def test_registration_updates_preregistered_agent(client, seeded, connector, headers):
    connector_id, _ = connector
    response = client.post("/connector/registrations", json=_registration(connector_id), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"agent_id": "agent-codex-mac", "created": False}
    agent = repo.get_agent(seeded, "agent-codex-mac")
    assert agent["connector_id"] == connector_id
    assert agent["repository_id"] == "demo-report-repo"
    assert agent["base_commit"] == BASE_COMMIT
    assert json.loads(agent["verification_profile_ids_json"]) == ["vp-pytest"]
    assert json.loads(agent["discovered_json"])["codex_version"] == "0.155.1"
    assert agent["connection_state"] == "online"
    assert agent["last_seen_at"] is not None
    assert agent["capabilities_json"]  # 운영자가 등록한 능력은 그대로
    assert repo.agents_for_connector(seeded, connector_id)[0]["agent_id"] == "agent-codex-mac"


def test_registration_discovered_size_limit_422(client, connector, headers):
    connector_id, _ = connector
    big = {"blob": "x" * 70_000}
    response = client.post("/connector/registrations", json=_registration(connector_id, discovered=big), headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"
    assert response.json()["field"] == "discovered"


def test_registration_accepts_claude_tool(client, seeded, connector, headers):
    """`tool` 은 `codex`·`claude` 둘 다 같은 경로다. 중앙은 값을 저장하지 않으며(어댑터 선택은
    연결 프로그램의 로컬 등록 `state.sqlite` 의 `tool` 로만 정한다) 분기도 없다."""
    connector_id, _ = connector
    response = client.post("/connector/registrations", json=_registration(connector_id, tool="claude"), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"agent_id": "agent-codex-mac", "created": False}
    agent = repo.get_agent(seeded, "agent-codex-mac")
    assert agent["connector_id"] == connector_id
    assert agent["connection_state"] == "online"


def test_registration_rejects_other_tool_and_mismatched_connector(client, connector, headers):
    connector_id, _ = connector
    response = client.post("/connector/registrations", json=_registration(connector_id, tool="gemini"), headers=headers)
    assert response.status_code == 422
    assert response.json()["field"] == "tool"
    response = client.post("/connector/registrations", json=_registration("conn-other"), headers=headers)
    assert response.status_code == 403


def test_registration_rejects_short_commit(client, connector, headers):
    connector_id, _ = connector
    response = client.post("/connector/registrations", json=_registration(connector_id, base_commit="3f9c2e1"), headers=headers)
    assert response.status_code == 422
    assert response.json()["field"] == "base_commit"



# --- 러너 등록이 Agent 를 만든다 (phase 12 step 1, ADR-0018 결정 1 · CONTRACT 14.3·14.4) ---

OPEN_ARCHIVE = {
    "local_registration_id": "OpenArchive",
    "agent_name": "OpenArchive",
    "tool": "claude",
    "repository_id": "jeongeundev/OpenArchive",
    "verification_profile_ids": ["vp-check"],
    "discovered": {"found": {"github_repository": "jeongeundev/OpenArchive"}, "not_read": []},
}


@pytest.fixture
def selfhost(settings, conn):
    """같은 DB 를 여는 클라이언트와 그 클라이언트로 교환한 연결 프로그램."""
    client = TestClient(create_app(settings))
    connector_id, token = exchange(client, conn)
    return client, connector_id, bearer(token)


def test_selfhost_registration_creates_agent_for_unknown_name(selfhost, conn):
    client, connector_id, headers = selfhost

    response = client.post("/connector/registrations", json=_registration(connector_id, **OPEN_ARCHIVE),
                           headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True and set(body) == {"agent_id", "created"}
    agent = repo.get_agent(conn, body["agent_id"])
    assert agent["name"] == "OpenArchive"
    assert json.loads(agent["capabilities_json"]) == [
        {"code": "code.fix", "scope": {"repository_id": "jeongeundev/OpenArchive"}},
        {"code": "code.review", "scope": {"repository_id": "jeongeundev/OpenArchive"}},
    ]
    assert (agent["connector_id"], agent["repository_id"], agent["base_commit"]) == (
        connector_id, "jeongeundev/OpenArchive", BASE_COMMIT)
    assert json.loads(agent["verification_profile_ids_json"]) == ["vp-check"]
    assert json.loads(agent["discovered_json"])["found"]["github_repository"] == "jeongeundev/OpenArchive"
    assert agent["connection_state"] == "online"
    assert repo.is_session_agent(conn, SELFHOST_SESSION_ID, body["agent_id"])


def test_selfhost_registration_twice_is_idempotent(selfhost, conn):
    client, connector_id, headers = selfhost
    first = client.post("/connector/registrations", json=_registration(connector_id, **OPEN_ARCHIVE), headers=headers)

    second = client.post("/connector/registrations",
                         json=_registration(connector_id, **{**OPEN_ARCHIVE, "base_commit": "a" * 40}), headers=headers)

    assert second.status_code == 200
    assert second.json() == {"agent_id": first.json()["agent_id"], "created": False}
    assert [a["agent_id"] for a in repo.list_agents(conn) if a["local_registration_id"] == "OpenArchive"] == [
        first.json()["agent_id"]]
    assert repo.get_agent(conn, first.json()["agent_id"])["base_commit"] == "a" * 40


def test_selfhost_registration_of_preregistered_agent_keeps_existing_behavior(selfhost, seeded):
    client, connector_id, headers = selfhost
    before = len(repo.list_agents(seeded))

    response = client.post("/connector/registrations", json=_registration(connector_id), headers=headers)

    assert response.json() == {"agent_id": "agent-codex-mac", "created": False}
    assert len(repo.list_agents(seeded)) == before


def test_selfhost_registration_name_used_by_another_connector_409(selfhost, conn):
    client, connector_id, headers = selfhost
    first = client.post("/connector/registrations", json=_registration(connector_id, **OPEN_ARCHIVE), headers=headers)
    other_id, other_token = exchange(client, conn)

    response = client.post("/connector/registrations", json=_registration(other_id, **OPEN_ARCHIVE),
                           headers=bearer(other_token))

    assert response.status_code == 409
    assert response.json()["code"] == "registration_taken"
    assert response.json()["field"] == "local_registration_id"
    assert repo.get_agent(conn, first.json()["agent_id"])["connector_id"] == connector_id


def test_registration_agent_name_is_limited_to_100_chars(selfhost):
    client, connector_id, headers = selfhost

    response = client.post("/connector/registrations",
                           json=_registration(connector_id, **{**OPEN_ARCHIVE, "agent_name": "x" * 101}),
                           headers=headers)

    assert response.status_code == 422
    assert response.json()["field"] == "agent_name"


# --- 측정 칸 (phase 9 step 4, ADR-0015) ------------------------------------------

FOLDER_COMMIT = "b" * 40
MEASURE_COLUMNS = ("folder_commit", "folder_dirty", "cost_usd", "input_tokens", "output_tokens")


def _measure(conn, execution_id):
    row = repo.get_execution(conn, execution_id)
    return tuple(row[c] for c in MEASURE_COLUMNS)


def test_events_with_measure_fields_store_folder_commit_and_usage(client, headers, exec_fix, seeded):
    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    started = event(exec_fix, 2, "started", {"runtime_ref": "pid:1", "folder_commit": FOLDER_COMMIT, "folder_dirty": True})
    assert _post_event(client, headers, exec_fix, started).status_code == 200
    assert _measure(seeded, exec_fix) == (FOLDER_COMMIT, 1, None, None, None)
    artifact_id = _upload(client, headers, exec_fix, b"diff --git a/x b/x\n").json()["artifact_id"]
    usage = {"cost_usd": 0.25, "input_tokens": 1200, "output_tokens": None}
    ready = event(exec_fix, 3, "result_ready", {"result_artifact_id": artifact_id, "usage": usage})
    assert _post_event(client, headers, exec_fix, ready).status_code == 200
    assert _measure(seeded, exec_fix) == (FOLDER_COMMIT, 1, 0.25, 1200, None)
    # 같은 seq 재전송은 값을 바꾸지 않는다
    assert _post_event(client, headers, exec_fix, ready).status_code == 200
    assert _measure(seeded, exec_fix) == (FOLDER_COMMIT, 1, 0.25, 1200, None)


def _result_ready_data(conn, execution_id) -> dict:
    row = conn.execute(
        "SELECT data_json FROM execution_events WHERE execution_id = ? AND type = 'result_ready'", (execution_id,)
    ).fetchone()
    return json.loads(row["data_json"])


@pytest.mark.parametrize("pushed", [True, False])
def test_result_ready_branch_pushed_is_stored(client, headers, exec_fix, seeded, pushed):
    """ADR-0018 결정 4: 러너의 push 결과는 실행의 result_ready 기록에 남는다 (executions 칸은 스키마 v8, step 6)."""
    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    assert _post_event(client, headers, exec_fix, event(exec_fix, 2, "started", {"runtime_ref": "pid:1"})).status_code == 200
    artifact_id = _upload(client, headers, exec_fix, b"diff --git a/x b/x\n").json()["artifact_id"]
    ready = event(exec_fix, 3, "result_ready", {"result_artifact_id": artifact_id, "branch_pushed": pushed})

    assert _post_event(client, headers, exec_fix, ready).status_code == 200
    assert _post_event(client, headers, exec_fix, ready).status_code == 200  # 같은 seq 재전송

    assert _result_ready_data(seeded, exec_fix)["branch_pushed"] is pushed
    assert repo.get_execution(seeded, exec_fix)["status"] == "result_ready"


def test_result_ready_without_branch_pushed_from_old_runner_is_accepted(client, headers, exec_fix, seeded):
    assert _post_event(client, headers, exec_fix, event(exec_fix, 1, "accepted", {})).status_code == 200
    assert _post_event(client, headers, exec_fix, event(exec_fix, 2, "started", {"runtime_ref": "pid:1"})).status_code == 200
    artifact_id = _upload(client, headers, exec_fix, b"diff --git a/x b/x\n").json()["artifact_id"]

    ready = event(exec_fix, 3, "result_ready", {"result_artifact_id": artifact_id})
    assert _post_event(client, headers, exec_fix, ready).status_code == 200

    assert "branch_pushed" not in _result_ready_data(seeded, exec_fix)


def test_events_without_measure_fields_keep_null(client, headers, running, seeded):
    body = event(running, 4, "failed", {"code": "timeout", "message": "시간 초과", "process_stopped": True})
    assert _post_event(client, headers, running, body).status_code == 200
    assert _measure(seeded, running) == (None, None, None, None, None)


def test_failed_event_with_usage_stores_known_values_only(client, headers, running, seeded):
    body = event(running, 4, "failed", {"code": "timeout", "message": "시간 초과", "process_stopped": True,
                                        "usage": {"input_tokens": 10, "output_tokens": 3}})
    assert _post_event(client, headers, running, body).status_code == 200
    assert _measure(seeded, running) == (None, None, None, 10, 3)
