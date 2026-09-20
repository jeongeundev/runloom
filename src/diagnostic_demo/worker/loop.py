"""모델 호출 루프 — ARCHITECTURE "진단 모델과 평가 기준" 처리 순서와 "모델 호출 예산" 상한.

모델 턴 → 도구 호출(인자·권한 검사는 `Tools.call` 안) → 결과 반환 → 반복. 초안이 오면 끝난다.
- 호출 수·누적 토큰·경과 시간이 상한을 넘으면 `BudgetExceeded`(code `budget_exceeded` | `timeout`).
- 초안이 읽지 않은 근거를 인용해도 모델에 두 번째 기회를 주지 않고 그대로 돌려준다. 판정은 중앙 검증기가 한다.
- 도구 인자 오류는 예외가 아니라 오류 dict 로 모델에 돌려주고 계속한다. 그 호출은 조회 이력에 남지 않는다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from diagnostic_demo.tools.api import TOOL_SCHEMAS, Tools
from diagnostic_demo.worker.model import DiagnosisDraft, DraftInvalid, ModelClient, ModelTurn, draft_json_schema
from diagnostic_demo.worker.prompt import SYSTEM_PROMPT, user_message
from workflow.contracts.v1 import ExecutionRequest


@dataclass(frozen=True)
class Budget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float


class BudgetExceeded(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # "budget_exceeded" | "timeout"


@dataclass
class Usage:
    """실행 하나의 누적 모델 사용량. 루프가 갱신하며 상한 초과로 중단돼도 그때까지의 값이 남는다."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def _label(name: str, arguments: dict) -> str:
    if name == "read_evidence":
        return f"{arguments.get('evidence_id')}@{arguments.get('version')}"
    if name == "get_run":
        return str(arguments.get("run_id"))
    return str(arguments.get("workflow_id"))


def run_diagnosis(
    execution_id: str,
    request: ExecutionRequest,
    tools: Tools,
    model: ModelClient,
    budget: Budget,
    clock: Callable[[], float],
    emit: Callable[[str, dict], None],
    usage: Usage | None = None,
) -> tuple[DiagnosisDraft, Usage]:
    """`emit(type, data)` 로 started·progress 이벤트를 낸다. result_ready·failed 는 호출자(runner)가 낸다."""
    usage = usage if usage is not None else Usage()
    started_at = clock()
    emit("started", {"runtime_ref": f"diag-run:{execution_id}"})

    def call_model(turn_fn: Callable[[], ModelTurn]) -> ModelTurn:
        if usage.calls >= budget.max_calls:
            raise BudgetExceeded("budget_exceeded", f"모델 호출 {budget.max_calls}회 상한에 도달했습니다.")
        if clock() - started_at > budget.timeout_seconds:
            raise BudgetExceeded("timeout", f"진단이 {budget.timeout_seconds:g}초를 초과했습니다.")
        try:
            turn = turn_fn()
        except DraftInvalid as exc:
            # 거부된 턴도 호출·토큰을 썼다. 이미 실패한 실행이므로 상한 판정 없이 사용량만 더하고 그대로 던진다
            usage.calls += 1
            usage.input_tokens += exc.input_tokens
            usage.output_tokens += exc.output_tokens
            raise
        usage.calls += 1
        usage.input_tokens += turn.input_tokens
        usage.output_tokens += turn.output_tokens
        if usage.input_tokens > budget.max_input_tokens or usage.output_tokens > budget.max_output_tokens:
            raise BudgetExceeded(
                "budget_exceeded",
                f"누적 토큰(입력 {usage.input_tokens}, 출력 {usage.output_tokens})이 상한"
                f"(입력 {budget.max_input_tokens}, 출력 {budget.max_output_tokens})을 넘었습니다.",
            )
        return turn

    schema = draft_json_schema()
    turn = call_model(lambda: model.start(SYSTEM_PROMPT, user_message(request), TOOL_SCHEMAS, schema))
    while True:
        if not turn.tool_calls:
            if turn.draft is None:
                raise DraftInvalid("", "모델이 도구 호출도 초안도 내지 않았습니다")
            return turn.draft, usage
        results: list[tuple[str, dict]] = []
        for call in turn.tool_calls:
            try:
                output = tools.call(call.name, call.arguments)
            except ValueError as exc:
                output = {
                    "ok": False, "content": None, "content_type": None, "error": "invalid_arguments",
                    "message": str(exc), "evidence_id": None, "version": None,
                }
                emit("progress", {"message": f"{call.name} 인자 오류"})
            else:
                label = _label(call.name, call.arguments)
                emit("progress", {
                    "message": f"{call.name} {label} 조회 완료" if output["ok"]
                    else f"{call.name} {label} 조회 실패 ({output['error']})",
                })
            results.append((call.call_id, output))
        turn = call_model(lambda: model.continue_with_tool_results(results))
