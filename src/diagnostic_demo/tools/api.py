"""모델이 부르는 도구 입구 — ARCHITECTURE "진단 모델과 평가 기준": 모델의 도구 요청 → 서비스에서 인자·권한 검사 → 실제 조회.

`TOOL_SCHEMAS` 는 OpenAI Responses API function calling 형식(`strict: true`, 추가 속성 금지)이다.
모델은 DB·파일 경로를 직접 받지 않으며, 인자는 여기서 검증한 뒤 `FixtureStore` 의 고정된 메서드로만 이어진다.
설명은 도구가 돌려주는 사실 자료를 적고, 진단 결론이나 힌트를 넣지 않는다.
텍스트 자료(`text/plain`)는 모델에 줄 번호가 붙은 줄 목록으로 보낸다 — 모델이 `lines:N-M` 을 원문 안에서 고르게 하기 위한
표시 형식이며, 첨부 원문·sha256·조회 이력은 `FixtureStore` 의 원문 바이트 그대로다.
"""

from collections.abc import Callable
from typing import Any

from diagnostic_demo.tools.store import FixtureStore, ToolResult
from diagnostic_demo.tools.trace import TOOL_CONTRACT_VERSION, ToolTraceRecorder

__all__ = ["TOOL_CONTRACT_VERSION", "TOOL_SCHEMAS", "Tools"]


def _tool(name: str, description: str, properties: dict[str, dict]) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),  # strict 모드는 모든 속성을 required 에 둔다. 선택 인자는 null 허용
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _tool(
        "get_run",
        "보고서 자동화 실행 한 건의 기록을 반환한다: run_id, workflow_id, 시작·종료 시각, 상태, 코드 버전, "
        "HTTP 상태, 단계별 결과, 그 실행이 받은 응답·로그·보고서 자료 참조(evidence_id, version).",
        {"run_id": {"type": "string", "description": "조회할 실행 ID (예: daily-0920-0900)"}},
    ),
    _tool(
        "list_runs",
        "지정한 자동화의 실행 목록을 최신순으로 반환한다. 각 항목은 run_id, started_at, status, code_version 이다. "
        "상세는 get_run 으로 조회한다.",
        {
            "workflow_id": {"type": "string", "description": "자동화 ID (예: daily-report)"},
            "before": {
                "type": ["string", "null"],
                "description": "이 시각(RFC 3339, 시간대 포함) 이전에 시작한 실행만. 없으면 null",
            },
            "status": {
                "type": ["string", "null"],
                "description": "이 상태(succeeded, failed)의 실행만. 없으면 null",
            },
            "limit": {"type": ["integer", "null"], "description": "최대 건수. 없으면 null (기본 10)"},
        },
    ),
    _tool(
        "list_documents",
        "지정한 자동화와 관련된 운영 문서 목록을 반환한다. 각 항목은 evidence_id, version, title, effective_at 이다. "
        "본문은 read_evidence 로 조회한다.",
        {"workflow_id": {"type": "string", "description": "자동화 ID (예: daily-report)"}},
    ),
    _tool(
        "read_evidence",
        "자료 하나의 해당 버전 원문을 반환한다: 실행 기록·응답 JSON·로그 텍스트·보고서 텍스트·운영 문서"
        "({markdown, machine} JSON). 텍스트 자료(content_type text/plain)는 줄 번호가 붙은 줄 목록"
        "(lines: [{line, text}, …], 1부터)과 총 줄 수(line_count)로 돌아오며, 인용은 lines:N-M (1 ≤ N ≤ M ≤ line_count) 이다. "
        "조회 실패는 not_found, access_denied, unavailable 로 구분한다.",
        {
            "evidence_id": {"type": "string", "description": "자료 ID (예: response-after)"},
            "version": {"type": "string", "description": "자료 버전 (예: 1)"},
        },
    ),
]

_SCHEMA_BY_NAME: dict[str, dict] = {schema["name"]: schema for schema in TOOL_SCHEMAS}
_JSON_TYPES: dict[str, type] = {"string": str, "integer": int}


def _validate(name: str, arguments: dict) -> dict[str, Any]:
    """알 수 없는 도구·키, 누락된 필수 인자, 타입 불일치는 ValueError. 선택 인자의 누락은 null 로 본다."""
    schema = _SCHEMA_BY_NAME.get(name)
    if schema is None:
        raise ValueError(f"알 수 없는 도구: {name!r}")
    properties: dict[str, dict] = schema["parameters"]["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"{name}: 허용되지 않는 인자 {sorted(unknown)}")
    validated: dict[str, Any] = {}
    for key, spec in properties.items():
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        value = arguments.get(key)
        if value is None:
            if "null" not in types:
                raise ValueError(f"{name}: 인자 {key} 가 필요합니다")
            validated[key] = None
            continue
        expected = tuple(_JSON_TYPES[t] for t in types if t != "null")
        # bool 은 int 의 하위 타입이지만 JSON integer 가 아니다
        if isinstance(value, bool) or not isinstance(value, expected):
            raise ValueError(f"{name}: 인자 {key} 의 타입이 {types} 가 아닙니다")
        validated[key] = value
    return validated


def _numbered_lines(text: str) -> dict[str, Any]:
    """텍스트 원문을 모델용 줄 목록으로 바꾼다. 줄은 `workflow.domain.evidence_location._resolve_line_range` 와 같은
    규칙으로 센다: `\\n` 으로 나누고 원문이 `\\n` 으로 끝나면 마지막 빈 조각은 줄이 아니다. 각 줄 텍스트는 그대로 둔다."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return {
        "line_count": len(lines),
        "lines": [{"line": number, "text": line} for number, line in enumerate(lines, start=1)],
    }


class Tools:
    def __init__(self, store: FixtureStore, recorder: ToolTraceRecorder) -> None:
        self._store = store
        self._recorder = recorder
        self._dispatch: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "get_run": lambda a: store.get_run(a["run_id"]),
            "list_runs": self._list_runs,
            "list_documents": lambda a: store.list_documents(a["workflow_id"]),
            "read_evidence": lambda a: store.read_evidence(a["evidence_id"], a["version"]),
        }

    def _list_runs(self, a: dict[str, Any]) -> ToolResult:
        limit = a["limit"]
        if limit is not None and limit < 1:
            raise ValueError("list_runs: limit 은 1 이상이어야 합니다")
        return self._store.list_runs(
            a["workflow_id"], a["before"], a["status"], **({} if limit is None else {"limit": limit})
        )

    def call(self, name: str, arguments: dict) -> dict:
        """인자 검증 → store 호출 → 이력 기록 → 모델에 돌려줄 dict."""
        validated = _validate(name, arguments)
        result = self._dispatch[name](validated)
        self._recorder.record(name, validated, result, self._store)
        single = result.returned[0] if len(result.returned) == 1 else None
        content = result.content
        if result.content_type == "text/plain" and isinstance(content, str):
            content = _numbered_lines(content)
        return {
            "ok": result.ok,
            "content": content,
            "content_type": result.content_type,
            "error": result.error.value if result.error is not None else None,
            "evidence_id": single.evidence_id if single is not None else None,
            "version": single.version if single is not None else None,
        }
