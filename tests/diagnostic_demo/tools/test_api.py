"""Tools.call — 모델이 부르는 유일한 입구. 인자 검증 → store → recorder. TOOL_SCHEMAS 는 OpenAI function calling 형식."""

import hashlib
import json

import pytest

from diagnostic_demo.tools.api import TOOL_CONTRACT_VERSION, TOOL_SCHEMAS, Tools
from diagnostic_demo.tools.store import FixtureStore
from diagnostic_demo.tools.trace import ToolTraceRecorder
from workflow.domain.evidence_location import resolve_location

TOOL_NAMES = {"get_run", "list_runs", "list_documents", "read_evidence"}


@pytest.fixture
def recorder() -> ToolTraceRecorder:
    return ToolTraceRecorder()


@pytest.fixture
def tools(store, recorder) -> Tools:
    return Tools(store, recorder)


# --- 스키마 ------------------------------------------------------------------


def test_tool_contract_version():
    assert TOOL_CONTRACT_VERSION == "tools-v2"


def test_schemas_are_strict_function_tools():
    assert {s["name"] for s in TOOL_SCHEMAS} == TOOL_NAMES
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        assert schema["strict"] is True
        assert schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        # strict 모드: 모든 속성이 required, 선택 인자는 null 허용 타입
        assert set(params["required"]) == set(params["properties"])


def test_schema_inputs_follow_prd_table():
    by_name = {s["name"]: s["parameters"]["properties"] for s in TOOL_SCHEMAS}
    assert set(by_name["get_run"]) == {"run_id"}
    assert set(by_name["list_runs"]) == {"workflow_id", "before", "status", "limit"}
    assert set(by_name["list_documents"]) == {"workflow_id"}
    assert set(by_name["read_evidence"]) == {"evidence_id", "version"}
    for optional in ("before", "status", "limit"):
        assert "null" in by_name["list_runs"][optional]["type"]


def test_schema_descriptions_state_facts_not_conclusions():
    text = " ".join(s["description"] for s in TOOL_SCHEMAS)
    for word in ("원인", "data.records", "형식 변경"):
        assert word not in text


def test_read_evidence_description_states_numbered_lines_and_line_range():
    description = next(s["description"] for s in TOOL_SCHEMAS if s["name"] == "read_evidence")
    assert "줄 번호" in description and "line_count" in description and "lines:N-M" in description


# --- 인자 검증 -------------------------------------------------------------------


def test_unknown_tool_is_rejected(tools, recorder):
    with pytest.raises(ValueError):
        tools.call("delete_run", {"run_id": "daily-0920-0900"})
    assert recorder.entries() == []


def test_unknown_argument_is_rejected(tools, recorder):
    with pytest.raises(ValueError):
        tools.call("get_run", {"run_id": "daily-0920-0900", "path": "/etc/passwd"})
    assert recorder.entries() == []


def test_missing_required_argument_is_rejected(tools):
    with pytest.raises(ValueError):
        tools.call("read_evidence", {"evidence_id": "response-after"})


def test_wrong_type_is_rejected(tools):
    with pytest.raises(ValueError):
        tools.call("get_run", {"run_id": 42})
    with pytest.raises(ValueError):
        tools.call("list_runs", {"workflow_id": "daily-report", "before": None, "status": None, "limit": "10"})
    with pytest.raises(ValueError):
        tools.call("list_runs", {"workflow_id": "daily-report", "before": None, "status": None, "limit": 0})


# --- 호출 ---------------------------------------------------------------------


def test_get_run_call_returns_model_facing_dict_and_records(tools, recorder, store):
    out = tools.call("get_run", {"run_id": "daily-0920-0900"})

    assert set(out) == {"ok", "content", "content_type", "error", "evidence_id", "version"}
    assert out["ok"] is True and out["error"] is None
    assert out["content_type"] == "application/json"
    assert out["content"]["run_id"] == "daily-0920-0900"
    assert (out["evidence_id"], out["version"]) == ("run-daily-0920-0900", "1")
    (entry,) = recorder.entries()
    assert entry.tool == "get_run" and entry.ok
    assert entry.returned[0]["sha256"] == store.sha256_of("run-daily-0920-0900", "1")


def test_read_evidence_not_found_is_returned_to_model_not_raised(tools, recorder):
    out = tools.call("read_evidence", {"evidence_id": "nope", "version": "1"})

    assert out == {"ok": False, "content": None, "content_type": None, "error": "not_found",
                   "evidence_id": None, "version": None}
    (entry,) = recorder.entries()
    assert entry.ok is False and entry.error == "not_found" and entry.returned == []


def test_access_denied_is_an_error_not_empty_content(denied_store, recorder):
    tools = Tools(denied_store, recorder)
    out = tools.call("list_documents", {"workflow_id": "daily-report"})
    assert out["ok"] is False and out["error"] == "access_denied" and out["content"] is None


def test_list_runs_call_accepts_nulls_and_omitted_optionals(tools):
    full = tools.call("list_runs", {"workflow_id": "daily-report", "before": None, "status": None, "limit": None})
    omitted = tools.call("list_runs", {"workflow_id": "daily-report"})
    assert full["content"] == omitted["content"]
    assert [r["run_id"] for r in full["content"]] == ["daily-0920-0900", "daily-0919-0900"]
    assert (full["evidence_id"], full["version"]) == (None, None)

    filtered = tools.call("list_runs", {"workflow_id": "daily-report", "before": "2026-09-20T09:00:00+09:00",
                                        "status": "succeeded", "limit": 5})
    assert [r["run_id"] for r in filtered["content"]] == ["daily-0919-0900"]


def test_list_documents_call(tools):
    out = tools.call("list_documents", {"workflow_id": "daily-report"})
    assert out["ok"] and [d["evidence_id"] for d in out["content"]] == [
        "daily-report-runbook", "upstream-response-change", "daily-report-contract",
    ]


def test_read_evidence_text_call_returns_numbered_lines_and_line_count(tools, store):
    out = tools.call("read_evidence", {"evidence_id": "log-daily-0920", "version": "1"})

    assert out["ok"] and out["content_type"] == "text/plain"
    assert (out["evidence_id"], out["version"]) == ("log-daily-0920", "1")
    assert set(out["content"]) == {"line_count", "lines"}
    assert out["content"]["line_count"] == 4
    assert [line["line"] for line in out["content"]["lines"]] == [1, 2, 3, 4]
    raw_lines = store.raw_bytes("log-daily-0920", "1").decode("utf-8").splitlines()
    assert [line["text"] for line in out["content"]["lines"]] == raw_lines  # 원문 그대로, trim·정규화 없음
    assert out["content"]["lines"][0]["text"].startswith("2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900")


@pytest.mark.parametrize("raw", [b"one\ntwo\nthree\n", b"one\ntwo\nthree"], ids=["trailing-newline", "no-trailing-newline"])
def test_text_line_numbering_follows_verifier_rule(fixture_root, recorder, raw):
    """끝 개행 뒤의 빈 조각은 줄이 아니다 — 모델이 본 번호와 검증기(`_resolve_line_range`)가 세는 번호가 같다."""
    key = ("log-daily-0920", "1")
    tools = Tools(FixtureStore(fixture_root, frozenset({"daily-report"}), replaced={key: raw}), recorder)

    out = tools.call("read_evidence", {"evidence_id": key[0], "version": key[1]})

    assert out["content"]["line_count"] == 3
    assert out["content"]["lines"] == [
        {"line": 1, "text": "one"}, {"line": 2, "text": "two"}, {"line": 3, "text": "three"},
    ]
    resolved = resolve_location(raw, "text/plain", "lines:1-3")
    assert resolved is not None and resolved.value == [line["text"] for line in out["content"]["lines"]]
    assert resolve_location(raw, "text/plain", "lines:1-4") is None


def test_text_line_with_leading_and_trailing_spaces_is_kept_verbatim(fixture_root, recorder):
    key = ("log-daily-0920", "1")
    raw = b"  a  \n\n\tb\n"
    tools = Tools(FixtureStore(fixture_root, frozenset({"daily-report"}), replaced={key: raw}), recorder)

    out = tools.call("read_evidence", {"evidence_id": key[0], "version": key[1]})

    assert [line["text"] for line in out["content"]["lines"]] == ["  a  ", "", "\tb"]
    assert out["content"]["line_count"] == 3


def test_read_evidence_json_content_is_unchanged(tools, store):
    out = tools.call("read_evidence", {"evidence_id": "response-after", "version": "1"})
    assert out["ok"] and out["content_type"] == "application/json"
    assert out["content"] == json.loads(store.raw_bytes("response-after", "1"))


def test_text_line_numbers_do_not_change_trace_sha256(tools, recorder, store):
    tools.call("read_evidence", {"evidence_id": "log-daily-0920", "version": "1"})

    (entry,) = recorder.entries()
    assert entry.returned[0]["sha256"] == hashlib.sha256(store.raw_bytes("log-daily-0920", "1")).hexdigest()
    assert entry.returned[0]["sha256"] == store.sha256_of("log-daily-0920", "1")


def test_every_call_is_recorded_in_order(tools, recorder):
    tools.call("get_run", {"run_id": "daily-0920-0900"})
    tools.call("list_runs", {"workflow_id": "daily-report"})
    tools.call("read_evidence", {"evidence_id": "response-after", "version": "1"})

    assert [(e.call_id, e.tool) for e in recorder.entries()] == [
        ("call-1", "get_run"), ("call-2", "list_runs"), ("call-3", "read_evidence"),
    ]
    assert recorder.returned_set() == {("run-daily-0920-0900", "1"), ("response-after", "1")}
