"""결과 조립 — ARCHITECTURE "진단 모델과 평가 기준": 첨부 본문·버전·해시는 진단 서비스가 실제 조회 기록에서 조립한다.

- `attachments` 는 조회 이력이 실제로 반환한 근거 전부다 (모델이 인용했든 안 했든). 원문·해시는 `FixtureStore` 에서 읽는다.
  중앙 검증기의 `attachments_in_trace`(첨부 ⊆ 읽음)·`refs_in_attachments`(인용 ⊆ 첨부)는 여기서 판정하지 않는다.
- 계약 위반 초안은 `ResultSchemaInvalid` 로 알리되 조립한 본문을 산출물로 보존한다 (수신 증거).
"""

import json
from sqlite3 import Connection

from pydantic import ValidationError

from diagnostic_demo import db
from diagnostic_demo.artifact_store import ArtifactStore
from diagnostic_demo.tools.store import FixtureStore
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.model import DiagnosisDraft
from workflow.contracts.v1 import CONTRACT_VERSION, DiagnosisResult, ExecutionRequest


class ResultSchemaInvalid(Exception):
    """조립한 결과가 계약 v1 `DiagnosisResult` 가 아니다. 본문은 `artifact_id` 로 보존했다."""

    def __init__(self, artifact_id: str, message: str):
        super().__init__(message)
        self.artifact_id = artifact_id


def assemble_result(
    draft: DiagnosisDraft,
    request: ExecutionRequest,
    recorder: ToolTraceRecorder,
    store: FixtureStore,
    artifact_store: ArtifactStore,
    conn: Connection,
    provenance_base: dict,
    *,
    now: str,
) -> tuple[DiagnosisResult, str]:
    """(결과, result_artifact_id). 첨부·조회 이력·결과를 실행 소유 산출물로 저장한다."""
    execution_id = request.execution_id

    def save(kind: str, content_type: str, data: bytes) -> str:
        return db.store_artifact(
            conn, artifact_store, execution_id, kind=kind, content_type=content_type, data=data, now=now
        )

    attachments = []
    for evidence_id, version in sorted(recorder.returned_set()):
        raw = store.raw_bytes(evidence_id, version)
        content_type = store.content_type_of(evidence_id, version) or "application/octet-stream"
        artifact_id = save("evidence", content_type, raw)
        attachments.append({
            "evidence_id": evidence_id,
            "version": version,
            "content_type": content_type,
            "artifact_id": artifact_id,
            "sha256": store.sha256_of(evidence_id, version),
        })
    trace_artifact_id = save("tool_trace", "application/json", recorder.to_json())

    data = {
        "contract_version": CONTRACT_VERSION,
        "execution_id": execution_id,
        "task_id": request.task_id,
        "run_id": request.target.run_id,
        **draft.model_dump(mode="json"),
        "attachments": attachments,
        "provenance": {**provenance_base, "tool_trace_artifact_id": trace_artifact_id},
    }
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        result = DiagnosisResult.model_validate(data)
    except ValidationError as exc:
        preserved = save("diagnosis_result", "application/json", encoded)
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
        raise ResultSchemaInvalid(preserved, f"결과가 계약 v1 DiagnosisResult 가 아닙니다: {detail}") from exc
    return result, save("diagnosis_result", "application/json", encoded)
