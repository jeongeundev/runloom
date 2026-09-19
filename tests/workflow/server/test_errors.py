"""errors.py — ApiError·검증 오류·어댑터 예외를 CONTRACT 오류 본문으로 옮긴다."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from workflow.adapters.errors import (
    ActiveExecutionExists,
    AdapterError,
    ArtifactMissing,
    DuplicateStartKey,
    EventConflict,
    Forbidden,
    HashMismatch,
    InvalidTransition,
    NotFound,
    SequenceGap,
)
from workflow.contracts.v1 import ContractVersion
from workflow.server.errors import ApiError, adapter_error_to_api, install_error_handlers


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract_version: ContractVersion
    name: str
    target: _Nested


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/echo")
    def _echo(body: _Body):
        return {"ok": True}

    @app.get("/api-error")
    def _api_error():
        raise ApiError(429, "daily_limit_reached", "오늘 한도에 도달했습니다.", details={"limit": 10})

    @app.get("/adapter-error")
    def _adapter_error():
        raise NotFound("execution exec-none")

    @app.get("/pydantic-error")
    def _pydantic_error():
        _Body.model_validate_json('{"contract_version": 1, "name": "n", "target": {"run_id": "r", "extra": 1}}')

    return TestClient(app)


def test_api_error_becomes_error_body(client):
    response = client.get("/api-error")
    assert response.status_code == 429
    assert response.json() == {
        "code": "daily_limit_reached",
        "message": "오늘 한도에 도달했습니다.",
        "field": None,
        "details": {"limit": 10},
    }


def test_unknown_field_422(client):
    response = client.post("/echo", json={"contract_version": 1, "name": "n", "target": {"run_id": "r"}, "extra": 1})
    assert response.status_code == 422
    assert response.json() == {
        "code": "unknown_field", "message": "필드 extra는 허용되지 않습니다.", "field": "extra", "details": None,
    }


def test_unsupported_contract_version_422(client):
    response = client.post("/echo", json={"contract_version": 2, "name": "n", "target": {"run_id": "r"}})
    assert response.status_code == 422
    assert response.json() == {
        "code": "unsupported_contract_version",
        "message": "contract_version 2는 지원하지 않습니다.",
        "field": "contract_version",
        "details": None,
    }


def test_invalid_field_uses_dotted_path_without_body_prefix(client):
    response = client.post("/echo", json={"contract_version": 1, "name": "n", "target": {"run_id": 5}})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_field"
    assert body["field"] == "target.run_id"
    assert body["details"] is None


def test_missing_field_is_invalid_field(client):
    response = client.post("/echo", json={"contract_version": 1, "target": {"run_id": "r"}})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"
    assert response.json()["field"] == "name"


def test_malformed_json_is_invalid_field(client):
    response = client.post("/echo", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field"


def test_pydantic_validation_error_raised_in_handler_is_converted(client):
    response = client.get("/pydantic-error")
    assert response.status_code == 422
    assert response.json() == {
        "code": "unknown_field", "message": "필드 extra는 허용되지 않습니다.", "field": "extra", "details": None,
    }


def test_adapter_error_raised_in_handler_is_converted(client):
    response = client.get("/adapter-error")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "exc, status, code, field, details",
    [
        (NotFound("execution exec-none"), 404, "not_found", None, None),
        (Forbidden("exec-fix-001은 conn-mac-01에 배정되지 않았습니다."), 403, "forbidden", None, None),
        (EventConflict(3), 409, "event_conflict", "seq", None),
        (SequenceGap(4), 409, "sequence_gap", "seq", {"expected_seq": 4}),
        (InvalidTransition("result_ready", event_type="started"), 409, "invalid_transition", "type",
         {"current_status": "result_ready"}),
        (InvalidTransition("running", event_type="result_ready", reason="result_artifact_missing"), 409,
         "invalid_transition", "type", {"reason": "result_artifact_missing"}),
        (HashMismatch("pytest-before.txt"), 422, "hash_mismatch", "sha256", None),
        (ActiveExecutionExists("fix-daily-0920"), 409, "execution_conflict", None, None),
        (DuplicateStartKey("fix-daily-0920/k"), 409, "execution_conflict", None, None),
        (ArtifactMissing("art-1"), 500, "artifact_missing", None, None),
        (AdapterError("?"), 500, "internal_error", None, None),
    ],
)
def test_adapter_error_mapping(exc, status, code, field, details):
    api = adapter_error_to_api(exc)
    assert (api.status, api.code, api.field, api.details) == (status, code, field, details)


def test_adapter_error_messages_follow_contract_table():
    assert adapter_error_to_api(EventConflict(3)).message == "seq 3은 다른 내용으로 이미 저장되었습니다."
    assert adapter_error_to_api(SequenceGap(4)).message == "seq 4가 먼저 필요합니다."
    assert adapter_error_to_api(SequenceGap(3)).message == "seq 3이 먼저 필요합니다."
    assert adapter_error_to_api(EventConflict(2)).message == "seq 2는 다른 내용으로 이미 저장되었습니다."
    assert (
        adapter_error_to_api(InvalidTransition("result_ready", event_type="started")).message
        == "result_ready 상태에서는 started를 받을 수 없습니다."
    )
    assert adapter_error_to_api(NotFound("execution exec-none")).message == "execution exec-none을 찾을 수 없습니다."
    assert adapter_error_to_api(Forbidden("메시지")).message == "메시지"


def test_error_body_never_contains_secret_like_values():
    """ApiError 는 고정 문구만 담는다. 토큰이 메시지에 섞이지 않는지 대표 사례로 확인."""
    api = ApiError(401, "unauthenticated", "유효한 연결 토큰이 필요합니다.")
    assert "wfc_" not in api.message
    assert api.body() == {
        "code": "unauthenticated", "message": "유효한 연결 토큰이 필요합니다.", "field": None, "details": None,
    }
