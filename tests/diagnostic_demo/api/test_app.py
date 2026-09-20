"""진단 API — CONTRACT 1절(접수 202/200/409, 상태 조회), 10절(429), 산출물 소유, 인증. 중앙 워커 호환 e2e."""

import dataclasses
import itertools
import json

import pytest
from fastapi.testclient import TestClient

from diagnostic_demo import db
from diagnostic_demo.api.app import create_app, kst_day_window
from diagnostic_demo.tools.api import TOOL_CONTRACT_VERSION
from diagnostic_demo.worker.fake_script import fixture_script
from diagnostic_demo.worker.model import FakeModelClient
from diagnostic_demo.worker.prompt import PROMPT_VERSION
from diagnostic_demo.worker.runner import process_one
from tests.diagnostic_demo.conftest import EXEC_A, TOKEN, contract_blocks, contract_request, diagnosis_request
from workflow.contracts.v1 import DiagnosisResult, ExecutionRequest, RunStatus
from workflow.domain.verification import LoadedEvidence, TraceEntry, verify_diagnosis

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
NOW = "2026-09-20T00:10:00.000000Z"


@pytest.fixture
def app(diag_settings):
    return create_app(diag_settings)


@pytest.fixture
def client(app):
    return TestClient(app)


def _contract_status_blocks() -> list[dict]:
    return [b for b in contract_blocks() if {"execution_id", "status", "last_event_seq"} <= set(b)]


def _contract_error(code: str) -> dict:
    (block,) = [b for b in contract_blocks() if b.get("code") == code and "message" in b and "field" in b]
    return block


# --- 인증 ---------------------------------------------------------------------------


@pytest.mark.parametrize("method, path", [
    ("POST", "/runs"), ("GET", f"/runs/{EXEC_A}"), ("GET", f"/runs/{EXEC_A}/artifacts/x"),
    ("GET", "/capabilities"), ("GET", "/budget"),
])
def test_every_route_requires_the_api_token(client, method, path):
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}):
        response = client.request(method, path, headers=headers, json=contract_request() if method == "POST" else None)
        assert response.status_code == 401, (path, headers)
        body = response.json()
        assert body["code"] == "unauthenticated" and body["field"] is None and body["details"] is None
        assert TOKEN not in response.text


# --- POST /runs — CONTRACT 1절 ---------------------------------------------------------


def test_new_submission_is_202_with_contract_body(client):
    response = client.post("/runs", json=contract_request(), headers=HEADERS)

    assert response.status_code == 202, response.text
    (expected,) = [b for b in _contract_status_blocks() if b["status"] == "accepted"]
    assert response.json() == expected  # events 없이 CONTRACT 1절 202 본문 그대로


def test_same_request_with_reordered_keys_is_200_current_status(client, diag_conn):
    client.post("/runs", json=contract_request(), headers=HEADERS)
    reordered = dict(reversed(list(contract_request().items())))
    reordered["target"] = {"run_id": "daily-0920-0900"}

    response = client.post("/runs", json=reordered, headers=HEADERS)

    assert response.status_code == 200 and response.json()["status"] == "accepted"
    assert "events" not in response.json()
    assert diag_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    # 진행된 뒤의 재접수도 현재 상태를 돌려준다
    db.append_event(diag_conn, EXEC_A, "started", {"runtime_ref": "diag-run-7f3a"}, NOW)
    response = client.post("/runs", json=contract_request(), headers=HEADERS)
    assert response.status_code == 200 and response.json()["status"] == "running"
    assert response.json()["last_event_seq"] == 2


def test_same_id_different_content_is_409_and_leaves_run_unchanged(client, diag_conn):
    client.post("/runs", json=contract_request(), headers=HEADERS)

    response = client.post("/runs", json=diagnosis_request(run_id="daily-0919-0900"), headers=HEADERS)

    assert response.status_code == 409
    assert response.json() == _contract_error("execution_conflict")
    row = db.get_run(diag_conn, EXEC_A)
    assert json.loads(row["request_json"])["target"] == {"run_id": "daily-0920-0900"}
    assert row["last_event_seq"] == 1


def test_only_diagnosis_kind_is_accepted(client):
    body = {
        **contract_request(), "kind": "code_change", "input_artifact_ids": ["art-handoff-001"],
        "target": {"local_registration_id": "local-demo-report", "verification_profile_id": "vp-pytest",
                   "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"},
    }
    response = client.post("/runs", json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_field" and response.json()["field"] == "kind"


def test_unknown_field_and_contract_version_are_422(client):
    response = client.post("/runs", json={**contract_request(), "extra": 1}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == _contract_error("unknown_field")

    response = client.post("/runs", json={**contract_request(), "contract_version": 2}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == _contract_error("unsupported_contract_version")

    response = client.post("/runs", content=b"{not json", headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["code"] == "invalid_field"


# --- GET /runs/{id} -----------------------------------------------------------------------


def test_status_lookup_returns_events_after_seq_like_contract(client, diag_conn):
    client.post("/runs", json=contract_request(), headers=HEADERS)
    db.append_event(diag_conn, EXEC_A, "started", {"runtime_ref": "diag-run-7f3a"}, "2026-09-20T00:10:01Z")
    db.append_event(diag_conn, EXEC_A, "progress", {"message": "get_run daily-0920-0900 조회 완료"}, "2026-09-20T00:10:04Z")

    response = client.get(f"/runs/{EXEC_A}", params={"after_seq": 1}, headers=HEADERS)

    assert response.status_code == 200
    (expected,) = [b for b in _contract_status_blocks() if b["status"] == "running"]
    assert response.json() == expected
    assert RunStatus.model_validate(response.json()).events[0].seq == 2

    everything = client.get(f"/runs/{EXEC_A}", headers=HEADERS).json()
    assert [e["seq"] for e in everything["events"]] == [1, 2, 3]
    assert client.get(f"/runs/{EXEC_A}", params={"after_seq": 3}, headers=HEADERS).json()["events"] == []
    assert client.get(f"/runs/{EXEC_A}", params={"after_seq": -1}, headers=HEADERS).status_code == 422


def test_unknown_execution_is_404(client):
    response = client.get("/runs/exec-none", headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found", "message": "execution exec-none을 찾을 수 없습니다.", "field": "execution_id", "details": None,
    }


# --- 산출물 -------------------------------------------------------------------------------


def test_artifact_download_is_scoped_to_its_run(client, diag_conn, artifact_store):
    client.post("/runs", json=contract_request(), headers=HEADERS)
    client.post("/runs", json=diagnosis_request("exec-diagnose-002"), headers=HEADERS)
    artifact_id = db.store_artifact(
        diag_conn, artifact_store, EXEC_A, kind="evidence", content_type="text/plain", data=b"line 1\n", now=NOW,
    )

    ok = client.get(f"/runs/{EXEC_A}/artifacts/{artifact_id}", headers=HEADERS)
    assert ok.status_code == 200 and ok.content == b"line 1\n"
    assert ok.headers["content-type"].startswith("text/plain")

    other = client.get(f"/runs/exec-diagnose-002/artifacts/{artifact_id}", headers=HEADERS)
    assert other.status_code == 403 and other.json()["code"] == "forbidden"
    assert client.get(f"/runs/{EXEC_A}/artifacts/art-none", headers=HEADERS).status_code == 403
    assert client.get(f"/runs/exec-none/artifacts/{artifact_id}", headers=HEADERS).status_code == 404


# --- 429 — CONTRACT 10절 ------------------------------------------------------------------


def test_global_daily_limit_is_429_but_resubmission_still_200(diag_settings, diag_conn):
    client = TestClient(create_app(dataclasses.replace(diag_settings, global_daily=1)))
    assert client.post("/runs", json=contract_request(), headers=HEADERS).status_code == 202

    response = client.post("/runs", json=diagnosis_request("exec-diagnose-002"), headers=HEADERS)

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "daily_limit_reached" and body["field"] is None
    assert body["details"]["limit"] == 1
    assert body["details"]["resets_at"].endswith("T00:00:00+09:00")
    assert "1회" in body["message"]
    assert client.post("/runs", json=contract_request(), headers=HEADERS).status_code == 200
    assert diag_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_budget_exhausted_is_429_with_estimate_and_limit(client, diag_conn, diag_settings):
    db.insert_run(diag_conn, ExecutionRequest.model_validate(diagnosis_request("exec-old-1")), NOW)
    db.record_usage(diag_conn, "exec-old-1", "gpt-test", 1, 1, 1, 27.3, NOW)

    response = client.post("/runs", json=contract_request(), headers=HEADERS)

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "budget_exhausted"
    assert body["details"] == {"estimated_usd": 27.3, "limit_usd": diag_settings.budget_usd}
    assert db.get_run(diag_conn, EXEC_A) is None


def test_kst_day_window_bounds_and_reset_time():
    from datetime import UTC, datetime

    start, end, resets_at = kst_day_window(datetime(2026, 9, 20, 20, 30, tzinfo=UTC))  # KST 09-21 05:30
    assert start == "2026-09-20T15:00:00.000000Z" and end == "2026-09-21T15:00:00.000000Z"
    assert resets_at == "2026-09-22T00:00:00+09:00"


# --- 능력·예산 ---------------------------------------------------------------------------


def test_capabilities_and_budget(client, diag_conn, diag_settings):
    capabilities = client.get("/capabilities", headers=HEADERS).json()
    assert capabilities == {
        "contract_version": 1, "role": "operations.diagnose", "workflow_ids": ["daily-report"],
        "tool_contract_version": TOOL_CONTRACT_VERSION, "model_id": diag_settings.model_id,
        "prompt_version": PROMPT_VERSION,
    }
    assert TOOL_CONTRACT_VERSION == "tools-v1"

    client.post("/runs", json=contract_request(), headers=HEADERS)
    db.record_usage(diag_conn, EXEC_A, "gpt-test", 1, 1, 1, 0.25, NOW)
    budget = client.get("/budget", headers=HEADERS).json()
    assert budget == {"estimated_usd": 0.25, "limit_usd": 30.0, "stop_ratio": 0.9, "runs_today": 1}


# --- 중앙 워커 호환 e2e: 접수 → 워커 → 상태·산출물 → 중앙 검증기 ------------------------------


def test_end_to_end_submit_process_fetch_and_verify(client, diag_conn, diag_settings):
    assert client.post("/runs", json=contract_request(), headers=HEADERS).status_code == 202

    assert process_one(
        diag_conn, diag_settings, lambda: FakeModelClient(fixture_script()),
        clock=itertools.count().__next__, now=lambda: NOW,
    )

    status = RunStatus.model_validate(client.get(f"/runs/{EXEC_A}", params={"after_seq": 1}, headers=HEADERS).json())
    assert status.status == "result_ready" and status.error is None
    assert [e.type for e in status.events] == ["started"] + ["progress"] * 8 + ["result_ready"]
    assert status.events[-1].data.result_artifact_id == status.result_artifact_id

    def download(artifact_id: str) -> tuple[bytes, str]:
        response = client.get(f"/runs/{EXEC_A}/artifacts/{artifact_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.content, response.headers["content-type"]

    result_bytes, result_type = download(status.result_artifact_id)
    assert result_type.startswith("application/json")
    result = DiagnosisResult.model_validate_json(result_bytes)
    trace_bytes, _ = download(result.provenance.tool_trace_artifact_id)
    trace = [
        TraceEntry(e["call_id"], e["tool"], e["input"], e["ok"],
                   tuple((r["evidence_id"], r["version"]) for r in e["returned"]))
        for e in json.loads(trace_bytes)["entries"]
    ]
    loaded = {}
    for ref in result.attachments:
        content, content_type = download(ref.artifact_id)
        loaded[(ref.evidence_id, ref.version)] = LoadedEvidence(
            ref.evidence_id, ref.version, content_type.split(";")[0], ref.sha256, content,
        )

    verdict = verify_diagnosis(result, loaded, trace)
    assert verdict.outcome == "passed", [c for c in verdict.checks if not c.passed]
    assert client.get("/budget", headers=HEADERS).json()["runs_today"] == 1
