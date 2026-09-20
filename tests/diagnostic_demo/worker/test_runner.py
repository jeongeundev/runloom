"""진단 워커 한 건 처리 — accepted 를 잡아 started → progress → result_ready | failed 이벤트와 usage 를 남긴다."""

import dataclasses
import itertools
import json

import pytest

from diagnostic_demo import db
from diagnostic_demo.worker.fake_script import fixture_script
from diagnostic_demo.worker.model import DiagnosisDraft, DraftInvalid, FakeModelClient, ModelTurn
from diagnostic_demo.worker.runner import process_one
from tests.diagnostic_demo.conftest import EXEC_A, diagnosis_request
from workflow.contracts.v1 import DiagnosisResult, ExecutionRequest

NOW = "2026-09-20T00:10:00.000000Z"


@pytest.fixture
def accepted(diag_conn) -> str:
    db.insert_run(diag_conn, ExecutionRequest.model_validate(diagnosis_request()), NOW)
    return EXEC_A


def _factory(script=None):
    return lambda: FakeModelClient(script if script is not None else fixture_script())


def _run(conn, settings, factory) -> bool:
    return process_one(conn, settings, factory, clock=itertools.count().__next__, now=lambda: NOW)


def test_no_accepted_run_returns_false(diag_conn, diag_settings):
    assert _run(diag_conn, diag_settings, _factory()) is False


def test_processes_fixture_script_to_result_ready(diag_conn, diag_settings, artifact_store, accepted):
    assert _run(diag_conn, diag_settings, _factory()) is True

    run = db.get_run(diag_conn, accepted)
    assert run["status"] == "result_ready" and run["started_at"] == NOW and run["finished_at"] == NOW
    events = db.events_after(diag_conn, accepted, 0)
    assert [e.type for e in events] == ["accepted", "started"] + ["progress"] * 8 + ["result_ready"]
    assert [e.seq for e in events] == list(range(1, 12))
    assert events[-1].data.result_artifact_id == run["result_artifact_id"]
    row = db.get_artifact(diag_conn, accepted, run["result_artifact_id"])
    result = DiagnosisResult.model_validate_json(artifact_store.read(row["store_ref"]))
    assert result.outcome == "ready_for_handoff" and result.provenance.model_id == diag_settings.model_id
    assert result.provenance.prompt_version == "diag-prompt-v3"

    usage = diag_conn.execute("SELECT * FROM usage WHERE execution_id = ?", (accepted,)).fetchone()
    assert usage["calls"] == 9 and usage["model_id"] == diag_settings.model_id
    assert usage["input_tokens"] == sum(t.input_tokens for t in fixture_script())
    assert usage["estimated_usd"] is None  # 단가 미설정 → 추정 불가
    assert _run(diag_conn, diag_settings, _factory()) is False  # 같은 실행을 다시 잡지 않는다


def test_usage_is_priced_when_prices_are_configured(diag_conn, diag_settings, accepted):
    priced = dataclasses.replace(diag_settings, price_input_per_m=1.0, price_output_per_m=10.0)
    _run(diag_conn, priced, _factory())
    usage = diag_conn.execute("SELECT * FROM usage WHERE execution_id = ?", (accepted,)).fetchone()
    expected = usage["input_tokens"] / 1e6 * 1.0 + usage["output_tokens"] / 1e6 * 10.0
    assert usage["estimated_usd"] == pytest.approx(expected)


def test_budget_exceeded_fails_with_process_stopped_and_usage(diag_conn, diag_settings, accepted):
    tight = dataclasses.replace(diag_settings, max_calls=3)
    assert _run(diag_conn, tight, _factory()) is True
    run = db.get_run(diag_conn, accepted)
    assert run["status"] == "failed"
    assert json.loads(run["error_json"])["code"] == "budget_exceeded"
    last = db.events_after(diag_conn, accepted, 0)[-1]
    assert last.type == "failed" and last.data.process_stopped is True and last.data.code == "budget_exceeded"
    usage = diag_conn.execute("SELECT calls FROM usage WHERE execution_id = ?", (accepted,)).fetchone()
    assert usage["calls"] == 3


def test_contract_violation_fails_result_schema_invalid(diag_conn, diag_settings, artifact_store, accepted):
    script = fixture_script()
    broken = script[-1].draft.model_dump(mode="json")
    broken["missing_information"] = [{"code": "evidence_conflict", "description": "x", "evidence_id": None}]
    script[-1] = ModelTurn([], DiagnosisDraft.model_validate(broken), 10, 1, "r")

    _run(diag_conn, diag_settings, _factory(script))

    run = db.get_run(diag_conn, accepted)
    assert run["status"] == "failed" and run["result_artifact_id"] is None
    assert json.loads(run["error_json"])["code"] == "result_schema_invalid"
    kinds = [r["kind"] for r in diag_conn.execute("SELECT kind FROM artifacts WHERE execution_id = ?", (accepted,))]
    assert kinds.count("diagnosis_result") == 1 and kinds.count("evidence") == 7 and "tool_trace" in kinds


def test_model_output_invalid_records_the_rejected_turn_usage(diag_conn, diag_settings, accepted):
    # 계약 거부로 끝난 실행도 그 턴의 호출·토큰을 usage 에 남긴다 — 총액 상한 추정이 한 턴씩 적게 잡히지 않도록
    class RejectsLastTurn:
        def __init__(self) -> None:
            self._fake = FakeModelClient(fixture_script()[:2])  # get_run → list_runs 뒤 세 번째 턴에서 거부

        def start(self, *args):
            return self._fake.start(*args)

        def continue_with_tool_results(self, results):
            if not self._fake.script:
                raise DraftInvalid('{"outcome": 1}', "초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류",
                                   input_tokens=4610, output_tokens=210)
            return self._fake.continue_with_tool_results(results)

    assert _run(diag_conn, diag_settings, RejectsLastTurn) is True

    assert json.loads(db.get_run(diag_conn, accepted)["error_json"])["code"] == "model_output_invalid"
    usage = diag_conn.execute("SELECT * FROM usage WHERE execution_id = ?", (accepted,)).fetchone()
    tool_turns = fixture_script()[:2]
    assert usage["calls"] == 3
    assert usage["input_tokens"] == sum(t.input_tokens for t in tool_turns) + 4610
    assert usage["output_tokens"] == sum(t.output_tokens for t in tool_turns) + 210


def test_model_output_invalid_and_unexpected_errors_fail_without_leaking_keys(diag_conn, diag_settings, accepted):
    empty = ModelTurn([], None, 10, 1, "r")
    _run(diag_conn, diag_settings, _factory([empty]))
    assert json.loads(db.get_run(diag_conn, accepted)["error_json"])["code"] == "model_output_invalid"

    db.insert_run(diag_conn, ExecutionRequest.model_validate(diagnosis_request("exec-diagnose-002")), NOW)

    def exploding():
        raise RuntimeError("auth failed for sk-abcdef123456 at endpoint")

    assert _run(diag_conn, diag_settings, exploding) is True
    run = db.get_run(diag_conn, "exec-diagnose-002")
    error = json.loads(run["error_json"])
    assert run["status"] == "failed" and error["code"] == "internal_error"
    assert "sk-abcdef123456" not in error["message"] and "RuntimeError" in error["message"]
    assert diag_conn.execute("SELECT calls FROM usage WHERE execution_id = ?", ("exec-diagnose-002",)).fetchone()["calls"] == 0
