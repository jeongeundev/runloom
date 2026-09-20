"""결과 조립 — 첨부·해시는 모델 출력이 아니라 FixtureStore 원문에서, 첨부는 읽은 근거 전부. 중앙 검증기와의 통합."""

import hashlib
import itertools
import json

import pytest

from diagnostic_demo import db
from diagnostic_demo.tools.api import Tools
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.assemble import ResultSchemaInvalid, assemble_result
from diagnostic_demo.worker.fake_script import fixture_script, needs_information_script
from diagnostic_demo.worker.loop import Budget, run_diagnosis
from diagnostic_demo.worker.model import DiagnosisDraft, FakeModelClient, ModelTurn
from tests.diagnostic_demo.conftest import EXEC_A, diagnosis_request
from workflow.contracts.v1 import DiagnosisResult, ExecutionRequest
from workflow.domain.verification import LoadedEvidence, TraceEntry, verify_diagnosis

NOW = "2026-09-20T00:10:00.000000Z"
BUDGET = Budget(15, 80_000, 8_000, 300)
PROVENANCE = {"model_id": "fake-model", "prompt_version": "diag-prompt-v1", "tool_contract_version": "tools-v1"}


@pytest.fixture
def request_a(diag_conn) -> ExecutionRequest:
    request = ExecutionRequest.model_validate(diagnosis_request())
    db.insert_run(diag_conn, request, NOW)
    return request


def _diagnose(script, request_a, fixture_store) -> tuple[DiagnosisDraft, ToolTraceRecorder]:
    recorder = ToolTraceRecorder()
    draft, _ = run_diagnosis(
        EXEC_A, request_a, Tools(fixture_store, recorder), FakeModelClient(script), BUDGET,
        itertools.count().__next__, lambda *_: None,
    )
    return draft, recorder


def _verifier_inputs(conn, artifact_store, result: DiagnosisResult):
    """중앙 워커(Step 8)가 하는 일을 테스트에서 재현: 첨부를 내려받아 LoadedEvidence 로, tool_trace 를 TraceEntry 로."""
    loaded = {}
    for ref in result.attachments:
        row = db.get_artifact(conn, result.execution_id, ref.artifact_id)
        content = artifact_store.read(row["store_ref"])
        loaded[(ref.evidence_id, ref.version)] = LoadedEvidence(
            evidence_id=ref.evidence_id, version=ref.version, content_type=row["content_type"],
            sha256=row["sha256"], content=content,
        )
    trace_row = db.get_artifact(conn, result.execution_id, result.provenance.tool_trace_artifact_id)
    entries = json.loads(artifact_store.read(trace_row["store_ref"]))["entries"]
    trace = [
        TraceEntry(
            call_id=e["call_id"], tool=e["tool"], input=e["input"], ok=e["ok"],
            returned=tuple((r["evidence_id"], r["version"]) for r in e["returned"]),
        )
        for e in entries
    ]
    return loaded, trace


def test_assembles_contract_result_from_read_evidence(request_a, fixture_store, artifact_store, diag_conn):
    draft, recorder = _diagnose(fixture_script(), request_a, fixture_store)

    result, artifact_id = assemble_result(
        draft, request_a, recorder, fixture_store, artifact_store, diag_conn, PROVENANCE, now=NOW,
    )

    assert isinstance(result, DiagnosisResult)
    assert (result.execution_id, result.task_id, result.run_id) == (EXEC_A, request_a.task_id, "daily-0920-0900")
    assert result.outcome == "ready_for_handoff" and result.diagnosis == draft.diagnosis
    keys = [(a.evidence_id, a.version) for a in result.attachments]
    assert keys == sorted(keys) and set(keys) == recorder.returned_set() and len(keys) == 7
    for ref in result.attachments:
        assert ref.sha256 == fixture_store.sha256_of(ref.evidence_id, ref.version)
        row = db.get_artifact(diag_conn, EXEC_A, ref.artifact_id)
        assert row["kind"] == "evidence" and row["content_type"] == ref.content_type and row["sha256"] == ref.sha256
        assert artifact_store.read(row["store_ref"]) == fixture_store.raw_bytes(ref.evidence_id, ref.version)
    assert {a.content_type for a in result.attachments} == {"application/json", "text/plain"}

    prov = result.provenance
    assert (prov.model_id, prov.prompt_version, prov.tool_contract_version) == (
        "fake-model", "diag-prompt-v1", "tools-v1",
    )
    trace_row = db.get_artifact(diag_conn, EXEC_A, prov.tool_trace_artifact_id)
    assert trace_row["kind"] == "tool_trace"
    assert json.loads(artifact_store.read(trace_row["store_ref"])) == json.loads(recorder.to_json())

    result_row = db.get_artifact(diag_conn, EXEC_A, artifact_id)
    assert result_row["kind"] == "diagnosis_result" and result_row["content_type"] == "application/json"
    stored = artifact_store.read(result_row["store_ref"])
    assert DiagnosisResult.model_validate_json(stored) == result
    assert result_row["sha256"] == hashlib.sha256(stored).hexdigest()


def test_central_verifier_passes_on_assembled_result(request_a, fixture_store, artifact_store, diag_conn):
    draft, recorder = _diagnose(fixture_script(), request_a, fixture_store)
    result, _ = assemble_result(draft, request_a, recorder, fixture_store, artifact_store, diag_conn, PROVENANCE, now=NOW)

    verdict = verify_diagnosis(result, *_verifier_inputs(diag_conn, artifact_store, result))

    assert verdict.outcome == "passed", [c for c in verdict.checks if not c.passed]


def test_needs_information_result_validates_and_is_undecidable(request_a, fixture_store, artifact_store, diag_conn):
    draft, recorder = _diagnose(needs_information_script(), request_a, fixture_store)
    result, _ = assemble_result(draft, request_a, recorder, fixture_store, artifact_store, diag_conn, PROVENANCE, now=NOW)

    assert result.outcome == "needs_information" and len(result.attachments) == 6
    assert ("upstream-response-change", "1") not in {(a.evidence_id, a.version) for a in result.attachments}
    verdict = verify_diagnosis(result, *_verifier_inputs(diag_conn, artifact_store, result))
    assert verdict.outcome == "undecidable" and all(c.passed for c in verdict.checks)


def test_unread_citation_is_assembled_but_fails_central_verifier(request_a, fixture_store, artifact_store, diag_conn):
    script = fixture_script()
    final = script[-1].draft.model_dump(mode="json")
    final["findings"].append({
        "claim": "런북은 변환기가 items 를 읽는다고 적고 있습니다.",
        "evidence_refs": [{"evidence_id": "daily-report-runbook", "version": "1", "location": "$.machine.reads_path"}],
    })
    script[-1] = ModelTurn([], DiagnosisDraft.model_validate(final), 10, 1, "r")
    draft, recorder = _diagnose(script, request_a, fixture_store)

    result, _ = assemble_result(draft, request_a, recorder, fixture_store, artifact_store, diag_conn, PROVENANCE, now=NOW)

    assert ("daily-report-runbook", "1") not in {(a.evidence_id, a.version) for a in result.attachments}
    verdict = verify_diagnosis(result, *_verifier_inputs(diag_conn, artifact_store, result))
    assert verdict.outcome == "failed"
    (check,) = [c for c in verdict.checks if c.code == "refs_in_attachments"]
    assert not check.passed and "daily-report-runbook@1" in check.detail


def test_contract_violation_preserves_draft_and_raises(request_a, fixture_store, artifact_store, diag_conn):
    script = fixture_script()
    broken = script[-1].draft.model_dump(mode="json")
    broken["diagnosis"] = None  # ready_for_handoff 인데 diagnosis 없음
    script[-1] = ModelTurn([], DiagnosisDraft.model_validate(broken), 10, 1, "r")
    draft, recorder = _diagnose(script, request_a, fixture_store)

    with pytest.raises(ResultSchemaInvalid) as exc:
        assemble_result(draft, request_a, recorder, fixture_store, artifact_store, diag_conn, PROVENANCE, now=NOW)

    row = db.get_artifact(diag_conn, EXEC_A, exc.value.artifact_id)
    assert row["kind"] == "diagnosis_result"
    preserved = json.loads(artifact_store.read(row["store_ref"]))
    assert preserved["outcome"] == "ready_for_handoff" and preserved["diagnosis"] is None
    assert len(preserved["attachments"]) == 7 and "diagnosis" in str(exc.value)
    assert db.get_run(diag_conn, EXEC_A)["result_artifact_id"] is None
