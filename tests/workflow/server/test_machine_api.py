"""machine_api.py — 연결 프로그램이 쓰는 중앙 API. CONTRACT 2절(claim)·3절(이벤트·오류표)·4절(산출물)."""

import hashlib
import json

from workflow.adapters import repo
from workflow.server.auth import utc_now

from .conftest import (
    BASE_COMMIT,
    EXEC_FIX,
    LOCAL_REGISTRATION,
    TASK_A,
    TASK_B,
    bearer,
    event,
    exchange,
    meta_for,
    request_body,
    seed_execution,
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
    assert assigned.json() == request_body(EXEC_FIX, TASK_B)  # 저장된 request_json 그대로

    repeat = client.post("/connector/claim", json=claim, headers=headers)  # 접수 전 재조회는 같은 배정
    assert repeat.status_code == 200
    assert repeat.json()["execution_id"] == EXEC_FIX

    accepted = _post_event(client, headers, EXEC_FIX, event(EXEC_FIX, 1, "accepted", {}))
    assert accepted.status_code == 200
    assert client.post("/connector/claim", json=claim, headers=headers).status_code == 204


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
    seed_execution(seeded, "exec-diagnose-001", TASK_A, kind="diagnosis", connector_id=None, inputs=())
    response = _post_event(client, headers, "exec-diagnose-001", event("exec-diagnose-001", 1, "accepted", {}))
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
    """B 실행은 입력 handoff bundle, 그 manifest 의 첨부, 자기 산출물만 내려받는다 (CONTRACT 4절)."""
    connector_id, token = connector
    headers = bearer(token)
    seed_execution(seeded, "exec-diag", TASK_A, kind="diagnosis", inputs=())
    evidence = b'{"report_date": "2026-09-19", "data": {"records": []}}'
    ev, _ = repo.store_artifact(
        seeded, store, execution_id="exec-diag", session_id="sess-1",
        meta=_meta_model(evidence, "evidence", "response-after.json"), data=evidence, now=utc_now(),
    )
    result = b'{"outcome": "ready_for_handoff"}'
    res, _ = repo.store_artifact(
        seeded, store, execution_id="exec-diag", session_id="sess-1",
        meta=_meta_model(result, "diagnosis_result", "diagnosis.json"), data=result, now=utc_now(),
    )
    manifest = json.dumps({
        "contract_version": 1,
        "source_execution_id": "exec-diag",
        "source_kind": "diagnosis",
        "source_result_artifact_id": res.artifact_id,
        "inputs": [],
        "attachments": [{
            "evidence_id": "response-after", "version": "1", "content_type": "application/json",
            "artifact_id": ev.artifact_id, "sha256": ev.sha256,
        }],
    }).encode()
    bundle, _ = repo.store_artifact(
        seeded, store, execution_id="exec-diag", session_id="sess-1",
        meta=_meta_model(manifest, "handoff_bundle", "manifest.json"), data=manifest, now=utc_now(),
    )
    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=connector_id, inputs=[bundle.artifact_id], predecessor="exec-diag")
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

    denied = client.get(f"/executions/{EXEC_FIX}/artifacts/{res.artifact_id}", headers=headers)  # 나열되지 않은 A 산출물
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
    assert repo.get_agent(seeded, "agent-ops-demo")["connection_state"] == "online"  # 다른 에이전트는 그대로
    row = seeded.execute("SELECT * FROM connectors WHERE connector_id = ?", (connector_id,)).fetchone()
    assert row["current_execution_id"] == "exec-fix-001"
    assert row["last_seen_at"] is not None


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
    assert response.json() == {"agent_id": "agent-codex-mac"}
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


def test_registration_unknown_local_registration_404(client, connector, headers):
    connector_id, _ = connector
    response = client.post(
        "/connector/registrations", json=_registration(connector_id, local_registration_id="local-none"), headers=headers
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert response.json()["field"] == "local_registration_id"


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
    assert response.json() == {"agent_id": "agent-codex-mac"}
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
