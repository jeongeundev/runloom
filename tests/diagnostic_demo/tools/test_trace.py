"""ToolTraceRecorder — 실제로 반환한 근거만 기록하고, 중앙의 TraceEntry 로 읽히는 JSON 을 만든다."""

import json

from diagnostic_demo.tools.store import ToolError
from diagnostic_demo.tools.trace import TOOL_CONTRACT_VERSION, ToolTraceRecorder, TraceRecord
from workflow.domain.verification import TraceEntry


def _as_trace_entry(entry: dict) -> TraceEntry:
    """Step 8 worker._load_trace 와 같은 방식으로 읽는다."""
    return TraceEntry(
        call_id=str(entry["call_id"]),
        tool=str(entry["tool"]),
        input=dict(entry.get("input") or {}),
        ok=bool(entry["ok"]),
        returned=tuple((str(r["evidence_id"]), str(r["version"])) for r in entry.get("returned") or []),
    )


def test_record_success_lists_returned_evidence_with_sha256(store):
    recorder = ToolTraceRecorder()
    result = store.read_evidence("response-after", "1")

    record = recorder.record("read_evidence", {"evidence_id": "response-after", "version": "1"}, result, store)

    assert isinstance(record, TraceRecord)
    assert record.call_id == "call-1"
    assert record.tool == "read_evidence"
    assert record.input == {"evidence_id": "response-after", "version": "1"}
    assert record.ok is True and record.error is None
    assert record.returned == [
        {"evidence_id": "response-after", "version": "1", "sha256": store.sha256_of("response-after", "1")}
    ]


def test_call_ids_are_sequential(store):
    recorder = ToolTraceRecorder()
    recorder.record("get_run", {"run_id": "daily-0920-0900"}, store.get_run("daily-0920-0900"), store)
    recorder.record("list_runs", {"workflow_id": "daily-report"}, store.list_runs("daily-report", None, None), store)
    recorder.record("read_evidence", {"evidence_id": "response-after", "version": "1"},
                    store.read_evidence("response-after", "1"), store)

    assert [e.call_id for e in recorder.entries()] == ["call-1", "call-2", "call-3"]
    assert [e.returned for e in recorder.entries()] == [
        [{"evidence_id": "run-daily-0920-0900", "version": "1", "sha256": store.sha256_of("run-daily-0920-0900", "1")}],
        [],
        [{"evidence_id": "response-after", "version": "1", "sha256": store.sha256_of("response-after", "1")}],
    ]


def test_failed_call_has_no_returned_evidence(store, denied_store):
    recorder = ToolTraceRecorder()

    missing = recorder.record("read_evidence", {"evidence_id": "nope", "version": "1"},
                              store.read_evidence("nope", "1"), store)
    denied = recorder.record("get_run", {"run_id": "daily-0920-0900"},
                             denied_store.get_run("daily-0920-0900"), denied_store)

    assert (missing.ok, missing.error, missing.returned) == (False, ToolError.not_found.value, [])
    assert (denied.ok, denied.error, denied.returned) == (False, ToolError.access_denied.value, [])
    assert recorder.returned_set() == set()


def test_returned_set_is_only_successful_reads(store):
    recorder = ToolTraceRecorder()
    recorder.record("get_run", {"run_id": "daily-0920-0900"}, store.get_run("daily-0920-0900"), store)
    recorder.record("read_evidence", {"evidence_id": "log-daily-0920", "version": "1"},
                    store.read_evidence("log-daily-0920", "1"), store)
    recorder.record("read_evidence", {"evidence_id": "log-daily-0920", "version": "1"},
                    store.read_evidence("log-daily-0920", "1"), store)  # 같은 자료 두 번 읽어도 하나
    recorder.record("read_evidence", {"evidence_id": "nope", "version": "1"},
                    store.read_evidence("nope", "1"), store)

    assert recorder.returned_set() == {("run-daily-0920-0900", "1"), ("log-daily-0920", "1")}


def test_to_json_shape_and_worker_compatibility(store):
    recorder = ToolTraceRecorder()
    recorder.record("get_run", {"run_id": "daily-0920-0900"}, store.get_run("daily-0920-0900"), store)
    recorder.record("read_evidence", {"evidence_id": "nope", "version": "1"},
                    store.read_evidence("nope", "1"), store)

    data = json.loads(recorder.to_json())

    assert data["contract_version"] == 1
    assert data["tool_contract_version"] == TOOL_CONTRACT_VERSION == "tools-v2"
    assert [set(e) for e in data["entries"]] == [
        {"call_id", "tool", "input", "ok", "error", "returned"},
    ] * 2
    assert data["entries"][1] == {
        "call_id": "call-2", "tool": "read_evidence",
        "input": {"evidence_id": "nope", "version": "1"},
        "ok": False, "error": "not_found", "returned": [],
    }
    entries = [_as_trace_entry(e) for e in data["entries"]]
    assert entries[0] == TraceEntry("call-1", "get_run", {"run_id": "daily-0920-0900"}, True,
                                    (("run-daily-0920-0900", "1"),))
    assert entries[1].ok is False and entries[1].returned == ()


def test_entries_returns_copy_and_input_is_not_shared(store):
    recorder = ToolTraceRecorder()
    arguments = {"run_id": "daily-0920-0900"}
    recorder.record("get_run", arguments, store.get_run("daily-0920-0900"), store)
    arguments["run_id"] = "changed"
    recorder.entries().clear()

    assert recorder.entries()[0].input == {"run_id": "daily-0920-0900"}
    assert len(recorder.entries()) == 1
