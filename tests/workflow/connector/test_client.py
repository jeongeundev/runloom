"""client — 연결 프로그램 → 중앙 기계 API (CONTRACT 2·3·4절). 요청 형식과 오류 대응을 FakeCentral 로 검사한다."""

import hashlib
import json

import httpx
import pytest

from workflow.connector.client import (
    CentralClient,
    CentralError,
    EventConflictError,
    SequenceGapError,
    Unauthenticated,
    Unreachable,
)
from workflow.contracts.v1 import ArtifactMeta, EventAck, ExecutionEvent
from workflow.server.machine_api import RegistrationRequest

from .conftest import CONNECTOR_ID, NOW, TOKEN, FakeCentral, assign_with_handoff, make_request


def _event(execution_id: str, seq: int, type_: str, data: dict) -> ExecutionEvent:
    return ExecutionEvent.model_validate({
        "contract_version": 1, "execution_id": execution_id, "seq": seq,
        "occurred_at": NOW, "type": type_, "data": data,
    })


def _last(fake: FakeCentral) -> httpx.Request:
    return fake.requests[-1]


# --- exchange ---------------------------------------------------------------------


def test_exchange_sends_code_without_auth_and_returns_credentials(fake):
    fake.connect_codes["code-1"] = CONNECTOR_ID
    client = CentralClient("http://central.test", None, transport=fake.transport())

    assert client.exchange("code-1") == (CONNECTOR_ID, TOKEN)

    request = _last(fake)
    assert (request.method, request.url.path) == ("POST", "/connector/exchange")
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {"contract_version": 1, "connect_code": "code-1"}


def test_exchange_unknown_code_is_central_error_404(fake):
    client = CentralClient("http://central.test", None, transport=fake.transport())

    with pytest.raises(CentralError) as info:
        client.exchange("없는 코드")
    assert (info.value.status, info.value.body.code) == (404, "not_found")


# --- claim · heartbeat · registrations ----------------------------------------------


def test_claim_sends_bearer_and_body_and_returns_request(fake, client):
    request = make_request()
    fake.assign(request)

    assert client.claim(CONNECTOR_ID) == request

    sent = _last(fake)
    assert (sent.method, sent.url.path) == ("POST", "/connector/claim")
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(sent.content) == {"contract_version": 1, "connector_id": CONNECTOR_ID}


def test_claim_204_is_none(fake, client):
    assert client.claim(CONNECTOR_ID) is None


def test_claim_401_when_token_revoked(fake):
    client = CentralClient("http://central.test", "wfc_revoked", transport=fake.transport())

    with pytest.raises(Unauthenticated):
        client.claim(CONNECTOR_ID)


def test_heartbeat_body(fake, client):
    client.heartbeat(CONNECTOR_ID, "exec-fix-001")
    client.heartbeat(CONNECTOR_ID, None)

    assert fake.heartbeats == [
        {"contract_version": 1, "connector_id": CONNECTOR_ID, "current_execution_id": "exec-fix-001"},
        {"contract_version": 1, "connector_id": CONNECTOR_ID, "current_execution_id": None},
    ]


def test_report_registration_matches_server_model_and_omits_local_commands(fake, client):
    client.report_registration(CONNECTOR_ID, {
        "local_registration_id": "local-demo-report",
        "tool": "codex",
        "repository_id": "demo-report-repo",
        "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
        "verification_profile_ids": ["vp-pytest"],
        "discovered": {"found": {"tests_dir": True}, "not_read": [], "verification_level": "설정 발견"},
    })

    body = fake.registrations[0]
    RegistrationRequest.model_validate(body)  # 서버 모델(extra=forbid) 그대로 통과
    assert body["connector_id"] == CONNECTOR_ID
    assert body["verification_profile_ids"] == ["vp-pytest"]
    assert "repo_path" not in json.dumps(body) and "pytest -q" not in json.dumps(body)


# --- events ----------------------------------------------------------------------------


def test_post_event_returns_ack(fake, client):
    request = make_request()
    fake.assign(request)

    ack = client.post_event(_event(request.execution_id, 1, "accepted", {}))

    assert ack == EventAck(execution_id=request.execution_id, last_event_seq=1, status="accepted")
    sent = _last(fake)
    assert sent.url.path == f"/executions/{request.execution_id}/events"
    assert json.loads(sent.content)["contract_version"] == 1


def test_post_event_sequence_gap_carries_expected_seq(fake, client):
    request = make_request()
    fake.assign(request)
    client.post_event(_event(request.execution_id, 1, "accepted", {}))

    with pytest.raises(SequenceGapError) as info:
        client.post_event(_event(request.execution_id, 3, "progress", {"message": "x"}))
    assert info.value.expected_seq == 2
    assert info.value.status == 409


def test_post_event_conflict(fake, client):
    request = make_request()
    fake.assign(request)
    client.post_event(_event(request.execution_id, 1, "accepted", {}))

    with pytest.raises(EventConflictError):
        client.post_event(_event(request.execution_id, 1, "started", {"runtime_ref": "r"}))


def test_post_event_same_content_again_is_ack_not_error(fake, client):
    request = make_request()
    fake.assign(request)
    event = _event(request.execution_id, 1, "accepted", {})
    client.post_event(event)

    assert client.post_event(event).last_event_seq == 1


def test_post_event_other_409_is_central_error(fake, client):
    request = make_request()
    fake.assign(request)
    client.post_event(_event(request.execution_id, 1, "accepted", {}))

    with pytest.raises(CentralError) as info:
        client.post_event(_event(request.execution_id, 2, "progress", {"message": "아직 running 아님"}))
    assert info.value.body.code == "invalid_transition"
    assert not isinstance(info.value, (SequenceGapError, EventConflictError))


# --- artifacts -------------------------------------------------------------------------


def test_download_artifact_returns_bytes_and_content_type(fake, client):
    request = assign_with_handoff(fake)
    bundle_id = request.input_artifact_ids[0]

    data, content_type = client.download_artifact(request.execution_id, bundle_id)

    assert json.loads(data)["source_execution_id"] == "exec-diagnose-001"
    assert content_type == "application/json"


def test_download_forbidden_is_central_error_403(fake, client):
    request = make_request()
    fake.assign(request)
    other = fake.add_artifact("exec-other", "diff", b"x")

    with pytest.raises(CentralError) as info:
        client.download_artifact(request.execution_id, other)
    assert info.value.status == 403


def test_upload_artifact_sends_multipart_meta_and_file(fake, client):
    request = make_request()
    fake.assign(request)
    data = b"exit_code=1\n..."
    meta = ArtifactMeta(contract_version=1, kind="test_log_before", name="pytest-before.txt",
                        content_type="text/plain", sha256=hashlib.sha256(data).hexdigest(), size=len(data))

    created = client.upload_artifact(request.execution_id, meta, data)

    assert (created.kind, created.sha256, created.size) == ("test_log_before", meta.sha256, len(data))
    assert fake.artifacts[created.artifact_id]["data"] == data
    sent = _last(fake)
    assert sent.headers["content-type"].startswith("multipart/form-data")
    # 재업로드는 같은 ID
    assert client.upload_artifact(request.execution_id, meta, data).artifact_id == created.artifact_id


def test_upload_hash_mismatch_is_central_error_422(fake, client):
    request = make_request()
    fake.assign(request)
    meta = ArtifactMeta(contract_version=1, kind="diff", name="a.diff", content_type="text/plain",
                        sha256="0" * 64, size=1)

    with pytest.raises(CentralError) as info:
        client.upload_artifact(request.execution_id, meta, b"x")
    assert (info.value.status, info.value.body.code) == (422, "hash_mismatch")


# --- 연결 오류 -------------------------------------------------------------------------


def test_connection_error_is_unreachable(fake, client):
    fake.reachable = False

    with pytest.raises(Unreachable):
        client.claim(CONNECTOR_ID)


def test_5xx_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = CentralClient("http://central.test", TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(Unreachable):
        client.heartbeat(CONNECTOR_ID, None)


def test_error_messages_do_not_contain_token(fake):
    client = CentralClient("http://central.test", TOKEN, transport=fake.transport())
    fake.reachable = False
    with pytest.raises(Unreachable) as info:
        client.claim(CONNECTOR_ID)
    assert TOKEN not in str(info.value) and TOKEN not in repr(client)
