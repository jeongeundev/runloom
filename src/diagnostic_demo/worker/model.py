"""모델 클라이언트 — ARCHITECTURE "진단 모델과 평가 기준": 조사 요청·도구 정의 전달 → 도구 요청 → 결과 반환 → 구조화 진단.

- `DiagnosisDraft` 는 모델이 쓰는 부분만이다. `attachments`·`provenance`·`contract_version`·`execution_id` 는 서비스가
  조회 이력에서 채운다 (`worker/assemble.py`). 봉투 규칙(outcome 별 필수 필드)은 `DiagnosisResult` 가 검증한다.
- `OpenAIModelClient` 는 Responses API 를 `previous_response_id` 로 잇는다. SDK 객체는 생성자로 주입받아 테스트에서
  가짜로 바꾼다. 이 모듈은 API 키를 읽지 않는다 — 키는 `__main__` 이 `openai.OpenAI(api_key=…)` 에만 넘긴다.
- `FakeModelClient` 는 대본을 순서대로 돌려준다. 테스트·로컬 e2e 전용이며 `DIAG_MODEL=fake` 로만 쓰인다.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.contracts.v1 import Diagnosis, Finding, MissingInformation, RepairRequest


class DiagnosisDraft(BaseModel):
    """모델이 작성하는 결과 초안. 하위 모델은 계약 v1 과 같다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["ready_for_handoff", "needs_information"]
    summary: str
    findings: list[Finding]
    diagnosis: Diagnosis | None
    repair_request: RepairRequest | None
    missing_information: list[MissingInformation]


# OpenAI structured outputs(strict) 가 받지 않는 키워드. 실호출 검증은 Step 17.
_UNSUPPORTED_KEYWORDS = frozenset({"title", "default", "minLength", "maxLength"})
_NAME_MAPS = frozenset({"properties", "$defs"})


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        if key in _NAME_MAPS:
            out[key] = {name: _strict(sub) for name, sub in value.items()}  # 값의 키는 이름이지 키워드가 아니다
        elif key == "const":
            out["enum"] = [value]
        else:
            out[key] = _strict(value)
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


def draft_json_schema() -> dict:
    """`DiagnosisDraft` 의 JSON Schema 를 strict 모드 규칙(모든 객체 additionalProperties=false, 모든 속성 required)에 맞춘 것."""
    return _strict(DiagnosisDraft.model_json_schema())


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelTurn:
    tool_calls: list[ToolCall]
    draft: DiagnosisDraft | None
    input_tokens: int
    output_tokens: int
    raw_id: str | None


class DraftInvalid(Exception):
    """모델의 최종 출력이 `DiagnosisDraft` 가 아니다 (거부·빈 출력·형식 위반). 원문은 `raw` 에 보존한다."""

    def __init__(self, raw: str, message: str):
        super().__init__(message)
        self.raw = raw


class ModelClient(Protocol):
    def start(self, system: str, user: str, tools: list[dict], schema: dict) -> ModelTurn: ...

    def continue_with_tool_results(self, results: Sequence[tuple[str, dict]]) -> ModelTurn: ...


class OpenAIModelClient:
    def __init__(self, client: Any, model_id: str):
        self._client = client  # openai.OpenAI 인스턴스 (테스트에서는 가짜)
        self._model_id = model_id
        self._system = ""
        self._tools: list[dict] = []
        self._text: dict = {}
        self._previous_response_id: str | None = None

    def __repr__(self) -> str:
        return f"OpenAIModelClient(model_id={self._model_id!r})"

    def start(self, system: str, user: str, tools: list[dict], schema: dict) -> ModelTurn:
        self._system = system
        self._tools = tools
        self._text = {
            "format": {"type": "json_schema", "name": "diagnosis_draft", "schema": schema, "strict": True}
        }
        self._previous_response_id = None
        return self._turn(self._create([{"role": "user", "content": user}]))

    def continue_with_tool_results(self, results: Sequence[tuple[str, dict]]) -> ModelTurn:
        items = [
            {"type": "function_call_output", "call_id": call_id, "output": json.dumps(output, ensure_ascii=False)}
            for call_id, output in results
        ]
        return self._turn(self._create(items))

    def _create(self, input_items: list[dict]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "instructions": self._system,
            "input": input_items,
            "tools": self._tools,
            "text": self._text,
        }
        if self._previous_response_id is not None:
            kwargs["previous_response_id"] = self._previous_response_id
        return self._client.responses.create(**kwargs)

    def _turn(self, response: Any) -> ModelTurn:
        self._previous_response_id = response.id
        tool_calls: list[ToolCall] = []
        texts: list[str] = []
        refusal: str | None = None
        for item in response.output:
            kind = getattr(item, "type", None)
            if kind == "function_call":
                # strict 도구는 인자가 스키마에 맞는 JSON 이다. 그래도 깨졌으면 빈 인자로 두어 Tools.call 의 검증이 거부하게 한다
                try:
                    arguments = json.loads(item.arguments)
                except ValueError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append(ToolCall(item.call_id, item.name, arguments))
            elif kind == "message":
                for part in item.content:
                    if part.type == "output_text":
                        texts.append(part.text)
                    elif part.type == "refusal":
                        refusal = part.refusal
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        draft: DiagnosisDraft | None = None
        if not tool_calls:
            if refusal is not None:
                raise DraftInvalid(refusal, "모델이 응답을 거부했습니다")
            text = "".join(texts).strip()
            if not text:
                raise DraftInvalid("", "모델이 도구 호출도 초안도 내지 않았습니다")
            try:
                draft = DiagnosisDraft.model_validate_json(text)
            except ValidationError as exc:
                raise DraftInvalid(text, f"초안이 DiagnosisDraft 형식이 아닙니다: {exc.error_count()}개 오류") from exc
        return ModelTurn(tool_calls, draft, input_tokens, output_tokens, response.id)


@dataclass
class FakeModelClient:
    """대본(`ModelTurn` 목록)을 순서대로 돌려준다. 도구 결과는 `received` 에 남겨 테스트가 확인한다."""

    script: list[ModelTurn]
    received: list[list[tuple[str, dict]]] = field(default_factory=list)
    started: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        self.script = list(self.script)

    def start(self, system: str, user: str, tools: list[dict], schema: dict) -> ModelTurn:
        self.started = (system, user)
        return self._next()

    def continue_with_tool_results(self, results: Sequence[tuple[str, dict]]) -> ModelTurn:
        self.received.append(list(results))
        return self._next()

    def _next(self) -> ModelTurn:
        if not self.script:
            raise RuntimeError("fake 대본이 끝났습니다")
        return self.script.pop(0)
