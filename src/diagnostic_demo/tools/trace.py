"""조회 이력(`tool_trace`) — PRD "조회 계층은 호출 ID·입력·성공 여부·실제로 반환한 근거 ID와 버전을 기록한다".

모델이 작성한 인용 목록과 별도로 보존해, 읽지 않은 자료를 인용·첨부했는지 중앙 검증기가 확인한다.
`to_json()` 의 entries 는 중앙의 `workflow.domain.verification.TraceEntry` 필드(`call_id`, `tool`, `input`,
`ok`, `returned`)와 대응한다.
"""

import json
from dataclasses import asdict, dataclass

from diagnostic_demo.tools.store import EvidenceKey, FixtureStore, ToolResult
from workflow.contracts.v1 import CONTRACT_VERSION

# tools-v2: 텍스트 자료를 줄 번호가 붙은 줄 목록과 line_count 로 돌려준다 (Step 1). 조회 이력·첨부 해시는 원문 바이트 기준으로 불변.
TOOL_CONTRACT_VERSION = "tools-v2"


@dataclass
class TraceRecord:
    """진단 서비스가 기록한 조회 한 건. `returned` 항목은 {evidence_id, version, sha256}."""

    call_id: str
    tool: str
    input: dict
    ok: bool
    error: str | None
    returned: list[dict]


class ToolTraceRecorder:
    def __init__(self) -> None:
        self._entries: list[TraceRecord] = []

    def record(self, tool: str, input: dict, result: ToolResult, store: FixtureStore) -> TraceRecord:
        """호출 결과를 기록한다. 실패 호출은 자료를 반환하지 않은 것이므로 `returned` 가 비어 있다."""
        returned = [
            {
                "evidence_id": ev.evidence_id,
                "version": ev.version,
                "sha256": store.sha256_of(ev.evidence_id, ev.version),
            }
            for ev in (result.returned if result.ok else ())
        ]
        record = TraceRecord(
            call_id=f"call-{len(self._entries) + 1}",
            tool=tool,
            input=dict(input),
            ok=result.ok,
            error=result.error.value if result.error is not None else None,
            returned=returned,
        )
        self._entries.append(record)
        return record

    def entries(self) -> list[TraceRecord]:
        return list(self._entries)

    def to_json(self) -> bytes:
        payload = {
            "contract_version": CONTRACT_VERSION,
            "tool_contract_version": TOOL_CONTRACT_VERSION,
            "entries": [asdict(entry) for entry in self._entries],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    def returned_set(self) -> set[EvidenceKey]:
        return {
            (item["evidence_id"], item["version"])
            for entry in self._entries
            for item in entry.returned
        }
