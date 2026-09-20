"""모델 클라이언트 — OpenAI Responses API 응답 파싱은 가짜 응답 객체로만 검사한다. 네트워크 호출 없음."""

import json
import re
import time
from types import SimpleNamespace

import pytest

from diagnostic_demo.tools.api import TOOL_SCHEMAS
from diagnostic_demo.worker import model as model_module
from diagnostic_demo.worker.model import (
    DiagnosisDraft,
    DraftInvalid,
    FakeModelClient,
    ModelTurn,
    OpenAIModelClient,
    ToolCall,
    draft_json_schema,
)
from tests.diagnostic_demo.conftest import contract_results
from workflow.contracts.v1 import LOCATION_PATTERN

DRAFT_KEYS = ("outcome", "summary", "findings", "diagnosis", "repair_request", "missing_information")


def draft_dict(result: dict | None = None) -> dict:
    source = result if result is not None else contract_results()[0]
    return {k: source[k] for k in DRAFT_KEYS}


# --- 가짜 OpenAI SDK -------------------------------------------------------------


class FakeResponses:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


class FakeOpenAI:
    def __init__(self, replies: list) -> None:
        self.responses = FakeResponses(replies)


def function_call(call_id: str, name: str, arguments) -> SimpleNamespace:
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=text)


def message(text: str | None = None, refusal: str | None = None) -> SimpleNamespace:
    content = []
    if text is not None:
        content.append(SimpleNamespace(type="output_text", text=text))
    if refusal is not None:
        content.append(SimpleNamespace(type="refusal", refusal=refusal))
    return SimpleNamespace(type="message", content=content)


def response(rid: str, output: list, input_tokens: int = 100, output_tokens: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid, output=output, usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    )


# --- DiagnosisDraft ----------------------------------------------------------------


def test_draft_is_the_model_part_of_the_result():
    draft = DiagnosisDraft.model_validate(draft_dict())
    assert draft.outcome == "ready_for_handoff" and draft.diagnosis.code == "response_path_changed"
    with pytest.raises(ValueError):
        DiagnosisDraft.model_validate({**draft_dict(), "attachments": []})
    with pytest.raises(ValueError):
        DiagnosisDraft.model_validate({**draft_dict(), "provenance": {}})


def test_draft_does_not_enforce_outcome_shape_here():
    # 봉투 검증(ready_for_handoff 인데 diagnosis 없음 등)은 assemble 의 DiagnosisResult 가 한다
    lax = {**draft_dict(), "diagnosis": None}
    assert DiagnosisDraft.model_validate(lax).diagnosis is None


def _walk(node, seen: list) -> None:
    if isinstance(node, dict):
        seen.append(node)
        for value in node.values():
            _walk(value, seen)
    elif isinstance(node, list):
        for item in node:
            _walk(item, seen)


def test_draft_json_schema_is_openai_strict_compatible():
    schema = draft_json_schema()
    nodes: list[dict] = []
    _walk(schema, nodes)
    objects = [n for n in nodes if n.get("type") == "object"]
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    for node in nodes:
        for banned in ("minLength", "maxLength", "title", "default"):
            assert banned not in node
    assert set(schema["properties"]) == set(DRAFT_KEYS)
    assert "$defs" in schema


def test_draft_json_schema_keeps_the_contract_location_pattern():
    # Step 0 의 LOCATION_PATTERN 이 strict 스키마에 그대로 남아 모델 생성 시점에 문법을 강제한다 (_strict 가 pattern 을 지우지 않는다)
    location = draft_json_schema()["$defs"]["EvidenceRef"]["properties"]["location"]
    assert location["type"] == "string" and location["pattern"] == LOCATION_PATTERN
    pattern = re.compile(location["pattern"])
    for ok in ("$.stages[1].status", "lines:1-4", "$.stages[1]", "$.code_version", "$.machine.effective_at"):
        assert pattern.match(ok), ok
    for bad in ("$.items[*]", "$", "$.keys()", "$.stages[01]", "lines:2", "lines:0-3", "$.a."):
        assert not pattern.match(bad), bad


# --- OpenAIModelClient -------------------------------------------------------------


def test_start_sends_instructions_tools_and_strict_schema():
    sdk = FakeOpenAI([response("resp_1", [function_call("call_1", "get_run", {"run_id": "daily-0920-0900"})])])
    client = OpenAIModelClient(sdk, "gpt-test")

    turn = client.start("SYSTEM", "USER", TOOL_SCHEMAS, draft_json_schema())

    (call,) = sdk.responses.calls
    assert call["model"] == "gpt-test" and call["instructions"] == "SYSTEM"
    assert call["input"] == [{"role": "user", "content": "USER"}]
    assert call["tools"] is TOOL_SCHEMAS
    assert call["text"] == {"format": {
        "type": "json_schema", "name": "diagnosis_draft", "schema": draft_json_schema(), "strict": True,
    }}
    assert call.get("previous_response_id") is None
    assert turn == ModelTurn(
        tool_calls=[ToolCall("call_1", "get_run", {"run_id": "daily-0920-0900"})],
        draft=None, input_tokens=100, output_tokens=10, raw_id="resp_1",
    )


def test_continue_chains_previous_response_id_and_function_call_outputs():
    sdk = FakeOpenAI([
        response("resp_1", [
            function_call("call_1", "get_run", {"run_id": "daily-0920-0900"}),
            function_call("call_2", "list_documents", {"workflow_id": "daily-report"}),
        ]),
        response("resp_2", [message(json.dumps(draft_dict(), ensure_ascii=False))], 300, 50),
    ])
    client = OpenAIModelClient(sdk, "gpt-test")
    first = client.start("S", "U", TOOL_SCHEMAS, draft_json_schema())
    assert [c.name for c in first.tool_calls] == ["get_run", "list_documents"]

    second = client.continue_with_tool_results([("call_1", {"ok": True}), ("call_2", {"ok": False, "error": "x"})])

    call = sdk.responses.calls[1]
    assert call["previous_response_id"] == "resp_1"
    assert call["input"] == [
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true}'},
        {"type": "function_call_output", "call_id": "call_2", "output": '{"ok": false, "error": "x"}'},
    ]
    assert call["instructions"] == "S" and call["tools"] is TOOL_SCHEMAS and call["text"]["format"]["strict"] is True
    assert second.tool_calls == [] and second.draft == DiagnosisDraft.model_validate(draft_dict())
    assert (second.input_tokens, second.output_tokens, second.raw_id) == (300, 50, "resp_2")


def test_refusal_or_non_json_or_empty_output_is_draft_invalid():
    for output in ([message(refusal="거부")], [message(text="not json")], [], [message(text='{"outcome": 1}')]):
        sdk = FakeOpenAI([response("r", output)])
        with pytest.raises(DraftInvalid):
            OpenAIModelClient(sdk, "gpt-test").start("S", "U", TOOL_SCHEMAS, draft_json_schema())


def test_malformed_tool_arguments_become_empty_dict_for_tool_validation():
    sdk = FakeOpenAI([response("r", [function_call("call_1", "get_run", "{not json")])])
    turn = OpenAIModelClient(sdk, "gpt-test").start("S", "U", TOOL_SCHEMAS, draft_json_schema())
    assert turn.tool_calls == [ToolCall("call_1", "get_run", {})]


def test_missing_usage_counts_zero():
    reply = SimpleNamespace(id="r", output=[function_call("c", "get_run", {"run_id": "x"})], usage=None)
    turn = OpenAIModelClient(FakeOpenAI([reply]), "gpt-test").start("S", "U", TOOL_SCHEMAS, draft_json_schema())
    assert (turn.input_tokens, turn.output_tokens) == (0, 0)


def test_draft_invalid_carries_the_rejected_turn_usage():
    # 거부·빈 출력·형식 위반 턴도 호출 1회와 토큰을 썼다. 루프가 usage 에 더할 수 있도록 예외가 응답 usage 를 갖는다
    for output in ([message(refusal="거부")], [message(text="not json")], [], [message(text='{"outcome": 1}')]):
        sdk = FakeOpenAI([response("r", output, 4610, 210)])
        with pytest.raises(DraftInvalid) as exc:
            OpenAIModelClient(sdk, "gpt-test").start("S", "U", TOOL_SCHEMAS, draft_json_schema())
        assert (exc.value.input_tokens, exc.value.output_tokens) == (4610, 210)
    bare = DraftInvalid("", "x")
    assert (bare.input_tokens, bare.output_tokens) == (0, 0)


def test_openai_client_does_not_expose_the_sdk_or_key_in_repr():
    sdk = FakeOpenAI([])
    sdk.api_key = "sk-secret"
    assert "sk-secret" not in repr(OpenAIModelClient(sdk, "gpt-test"))


# --- FakeModelClient ---------------------------------------------------------------


def test_fake_client_replays_script_in_order_and_records_results():
    script = [
        ModelTurn([ToolCall("c1", "get_run", {"run_id": "daily-0920-0900"})], None, 10, 1, "f1"),
        ModelTurn([], DiagnosisDraft.model_validate(draft_dict()), 20, 2, "f2"),
    ]
    fake = FakeModelClient(script)
    assert fake.start("S", "U", TOOL_SCHEMAS, draft_json_schema()) is script[0]
    assert fake.continue_with_tool_results([("c1", {"ok": True})]) is script[1]
    assert fake.received == [[("c1", {"ok": True})]]
    with pytest.raises(RuntimeError):
        fake.continue_with_tool_results([])


def test_fake_client_turn_seconds_delays_each_turn():
    """공개 데모용 속도 — 턴마다 `turn_seconds` 를 기다린 뒤 대본을 꺼낸다 (`DIAG_FAKE_TURN_SECONDS`)."""
    script = [
        ModelTurn([ToolCall("c1", "get_run", {"run_id": "daily-0920-0900"})], None, 10, 1, "f1"),
        ModelTurn([], DiagnosisDraft.model_validate(draft_dict()), 20, 2, "f2"),
    ]
    fake = FakeModelClient(script, turn_seconds=0.01)

    started = time.perf_counter()
    fake.start("S", "U", TOOL_SCHEMAS, draft_json_schema())
    fake.continue_with_tool_results([("c1", {"ok": True})])

    assert time.perf_counter() - started >= 0.02


def test_fake_client_default_turn_seconds_is_zero_and_never_sleeps(monkeypatch):
    monkeypatch.setattr(model_module.time, "sleep", lambda s: pytest.fail(f"sleep({s}) 호출"))
    fake = FakeModelClient([ModelTurn([], DiagnosisDraft.model_validate(draft_dict()), 1, 1, "f")])

    assert fake.turn_seconds == 0.0
    assert fake.start("S", "U", TOOL_SCHEMAS, draft_json_schema()).draft is not None
