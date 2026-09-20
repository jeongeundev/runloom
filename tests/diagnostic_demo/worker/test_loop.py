"""모델 호출 루프 — 모델 턴 → 도구 호출(검증은 Tools) → 결과 반환 → 반복. 상한 초과는 BudgetExceeded."""

import itertools

import pytest

from diagnostic_demo.tools.api import Tools
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.fake_script import fixture_script
from diagnostic_demo.worker.loop import Budget, BudgetExceeded, Usage, run_diagnosis
from diagnostic_demo.worker.model import DiagnosisDraft, DraftInvalid, FakeModelClient, ModelTurn, ToolCall
from tests.diagnostic_demo.conftest import EXEC_A, diagnosis_request
from tests.diagnostic_demo.worker.test_model import draft_dict
from workflow.contracts.v1 import ExecutionRequest

BUDGET = Budget(max_calls=15, max_input_tokens=80_000, max_output_tokens=8_000, timeout_seconds=300)


@pytest.fixture
def request_a() -> ExecutionRequest:
    return ExecutionRequest.model_validate(diagnosis_request())


@pytest.fixture
def recorder() -> ToolTraceRecorder:
    return ToolTraceRecorder()


@pytest.fixture
def tools(fixture_store, recorder) -> Tools:
    return Tools(fixture_store, recorder)


@pytest.fixture
def emitted() -> list[tuple[str, dict]]:
    return []


def _emit(emitted):
    return lambda type_, data: emitted.append((type_, data))


def tool_turn(name: str, arguments: dict, n: int = 1) -> ModelTurn:
    return ModelTurn([ToolCall(f"c{n}", name, arguments)], None, 500, 20, f"r{n}")


def draft_turn(draft: dict | None = None) -> ModelTurn:
    return ModelTurn([], DiagnosisDraft.model_validate(draft or draft_dict()), 500, 200, "r-final")


def test_fixture_script_produces_draft_trace_and_progress(request_a, tools, recorder, emitted):
    model = FakeModelClient(fixture_script())

    draft, usage = run_diagnosis(EXEC_A, request_a, tools, model, BUDGET, itertools.count().__next__, _emit(emitted))

    assert draft.outcome == "ready_for_handoff"
    assert emitted[0][0] == "started" and isinstance(emitted[0][1]["runtime_ref"], str)
    progress = [data["message"] for type_, data in emitted[1:] if type_ == "progress"]
    assert len(progress) == 8 and len(emitted) == 9
    assert progress[0] == "get_run daily-0920-0900 조회 완료"
    assert progress[1] == "list_runs daily-report 조회 완료"
    assert progress[2] == "read_evidence run-daily-0919-0900@1 조회 완료"
    assert usage.calls == 9
    assert usage.input_tokens == sum(t.input_tokens for t in fixture_script())
    assert usage.output_tokens == sum(t.output_tokens for t in fixture_script())
    assert len(recorder.entries()) == 8 and len(recorder.returned_set()) == 7
    # 모델은 Tools.call 의 dict 를 그대로 받는다
    assert model.received[0][0][1]["ok"] is True and model.received[0][0][1]["evidence_id"] == "run-daily-0920-0900"
    assert model.started[1].startswith("{") and "daily-0920-0900" in model.started[1]


def test_more_than_max_calls_raises_budget_exceeded(request_a, tools, emitted):
    script = [tool_turn("get_run", {"run_id": "daily-0920-0900"}, n) for n in range(1, 17)] + [draft_turn()]
    model = FakeModelClient(script)
    usage = Usage()

    with pytest.raises(BudgetExceeded) as exc:
        run_diagnosis(EXEC_A, request_a, tools, model, BUDGET, itertools.count().__next__, _emit(emitted), usage)

    assert exc.value.code == "budget_exceeded"
    assert usage.calls == 15 and len(model.script) == 2  # 16번째 호출은 하지 않았다


def test_token_ceiling_raises_budget_exceeded(request_a, tools, emitted):
    budget = Budget(max_calls=15, max_input_tokens=1200, max_output_tokens=8_000, timeout_seconds=300)
    script = [tool_turn("get_run", {"run_id": "daily-0920-0900"}, n) for n in range(1, 4)] + [draft_turn()]
    usage = Usage()
    with pytest.raises(BudgetExceeded) as exc:
        run_diagnosis(EXEC_A, request_a, tools, FakeModelClient(script), budget, itertools.count().__next__,
                      _emit(emitted), usage)
    assert exc.value.code == "budget_exceeded" and usage.calls == 3 and usage.input_tokens == 1500


def test_elapsed_time_raises_timeout(request_a, tools, emitted):
    budget = Budget(max_calls=15, max_input_tokens=80_000, max_output_tokens=8_000, timeout_seconds=250)
    script = [tool_turn("get_run", {"run_id": "daily-0920-0900"}, n) for n in range(1, 6)] + [draft_turn()]
    clock = itertools.count(0, 100).__next__  # 시작 0, 호출 전 확인마다 100초씩 흐른다
    usage = Usage()
    with pytest.raises(BudgetExceeded) as exc:
        run_diagnosis(EXEC_A, request_a, tools, FakeModelClient(script), budget, clock, _emit(emitted), usage)
    # 100·200초에는 호출하고 300초에는 상한을 넘은 것을 확인해 호출하지 않는다
    assert exc.value.code == "timeout" and usage.calls == 2


def test_invalid_tool_arguments_are_returned_to_model_and_loop_continues(request_a, tools, recorder, emitted):
    script = [
        ModelTurn([ToolCall("c1", "get_run", {"path": "/etc/passwd"}), ToolCall("c2", "delete_run", {})], None, 10, 1, "r1"),
        draft_turn(),
    ]
    model = FakeModelClient(script)

    draft, usage = run_diagnosis(EXEC_A, request_a, tools, model, BUDGET, itertools.count().__next__, _emit(emitted))

    assert draft is not None and usage.calls == 2
    (results,) = model.received
    assert [cid for cid, _ in results] == ["c1", "c2"]
    for _, out in results:
        assert out["ok"] is False and out["error"] == "invalid_arguments" and out["content"] is None
        assert "message" in out
    assert recorder.entries() == []  # 검증에 걸린 호출은 조회 이력이 아니다
    assert [d["message"] for t, d in emitted if t == "progress"] == ["get_run 인자 오류", "delete_run 인자 오류"]


def test_tool_failure_is_a_progress_message_not_an_exception(request_a, tools, emitted):
    script = [tool_turn("read_evidence", {"evidence_id": "nope", "version": "1"}), draft_turn()]
    run_diagnosis(EXEC_A, request_a, tools, FakeModelClient(script), BUDGET, itertools.count().__next__, _emit(emitted))
    assert emitted[1] == ("progress", {"message": "read_evidence nope@1 조회 실패 (not_found)"})


def test_draft_citing_unread_evidence_is_returned_as_is(request_a, tools, recorder, emitted):
    # 판정은 중앙 검증기가 한다. 서비스는 모델에 두 번째 기회를 주지 않고 사실만 기록한다
    draft, usage = run_diagnosis(EXEC_A, request_a, tools, FakeModelClient([draft_turn()]), BUDGET,
                                 itertools.count().__next__, _emit(emitted))
    assert draft.findings and recorder.returned_set() == set() and usage.calls == 1


def test_turn_without_tool_calls_or_draft_is_draft_invalid(request_a, tools, emitted):
    empty = ModelTurn([], None, 10, 1, "r1")
    usage = Usage()
    with pytest.raises(DraftInvalid):
        run_diagnosis(EXEC_A, request_a, tools, FakeModelClient([empty]), BUDGET, itertools.count().__next__,
                      _emit(emitted), usage)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 10, 1)


class _RejectsLastTurn:
    """대본이 끝난 다음 턴에서 DraftInvalid 를 던지는 가짜 클라이언트 — OpenAIModelClient._turn 의 계약 거부를 흉내 낸다."""

    def __init__(self, script: list[ModelTurn], exc: DraftInvalid) -> None:
        self._fake = FakeModelClient(script)
        self._exc = exc

    def start(self, system, user, tools, schema):
        return self._fake.start(system, user, tools, schema)

    def continue_with_tool_results(self, results):
        if not self._fake.script:
            raise self._exc
        return self._fake.continue_with_tool_results(results)


def test_draft_invalid_turn_is_added_to_usage_and_reraised_as_is(request_a, tools, emitted):
    # 계약 거부 턴의 호출 1회·토큰이 usage 에 들어간다 (DIAG_EVAL 이 적은 결함). 두 번째 기회는 없고 상한 판정도 하지 않는다
    script = [tool_turn("get_run", {"run_id": "daily-0920-0900"}, n) for n in range(1, 3)]
    rejected = DraftInvalid('{"outcome": 1}', "초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류",
                            input_tokens=4610, output_tokens=210)
    tight = Budget(max_calls=15, max_input_tokens=1200, max_output_tokens=8_000, timeout_seconds=300)
    usage = Usage()

    with pytest.raises(DraftInvalid) as exc:
        run_diagnosis(EXEC_A, request_a, tools, _RejectsLastTurn(script, rejected), tight,
                      itertools.count().__next__, _emit(emitted), usage)

    assert exc.value is rejected  # 누적 1000 + 4610 > 1200 이어도 BudgetExceeded 로 바꾸지 않는다
    assert usage.calls == 3
    assert usage.input_tokens == 500 * 2 + 4610 and usage.output_tokens == 20 * 2 + 210
