"""계약 v1 — 중앙 API·진단 API·연결 프로그램이 공유하는 유일한 Pydantic 모델.

필드·조건의 원본은 docs/ARCHITECTURE.md "계약 v1", 완전한 예시는 docs/CONTRACT.md.
도메인 판단(자동 선택·상태 전이)·DB·HTTP 는 여기 넣지 않는다.

공통 규칙:
- 알 수 없는 필드 거부, 자동 변환 없음 (`extra="forbid"`, `strict=True`).
- 시각은 `str` 로 받아 시간대 있는 RFC 3339 만 허용한다. `datetime` 을 쓰지 않는다 —
  FastAPI 는 JSON 을 dict 로 푼 뒤 python 모드로 검증하므로 strict 에서 str → datetime 이 거부된다.
- ID 는 비어 있지 않은 불투명 문자열. 경로로 해석하지 않는다.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = 1

# CONTRACT.md 4절의 산출물 kind 13종
ARTIFACT_KINDS: tuple[str, ...] = (
    "handoff_bundle",
    "diagnosis_result",
    "tool_trace",
    "evidence",
    "codex_jsonl",
    "codex_stderr",
    "diff",
    "test_log_before",
    "test_log_after",
    "verification_log",
    "report_output",
    "code_change_result",
    "review_comment",
)

_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
# location 문법 — ARCHITECTURE "진단 결과와 근거". `$.a.b[0].c` 객체 경로(배열 인덱스 `[N]` 은 0부터,
# 앞자리 0 없음) 또는 `lines:N-M` 줄 범위. 와일드카드·필터·음수 인덱스는 없다.
# 이 문자열 하나가 JSON Schema `pattern`(모델 생성 시점 강제)과 검증기·도메인 해석기의 유일한 문법이다.
# pydantic-core 의 Rust regex 와 Python re 둘 다에서 돌아야 하므로 lookaround·backreference 를 쓰지 않는다.
_KEY = r"[A-Za-z_][A-Za-z0-9_]*"
_INDEX = r"\[(?:0|[1-9][0-9]*)\]"
OBJECT_PATH_PATTERN = rf"\$(?:\.{_KEY}(?:{_INDEX})*)+"
LINE_RANGE_PATTERN = r"lines:[1-9][0-9]*-[1-9][0-9]*"
LOCATION_PATTERN = rf"^(?:{OBJECT_PATH_PATTERN}|{LINE_RANGE_PATTERN})$"
_LOCATION = re.compile(LOCATION_PATTERN)


def parse_rfc3339_aware(value: str) -> str:
    """시간대가 있는 RFC 3339 문자열인지 검증하고 원문을 그대로 돌려준다."""
    if not _RFC3339.match(value):
        raise ValueError("시간대가 있는 RFC 3339 시각이어야 합니다")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("시간대가 있는 RFC 3339 시각이어야 합니다")
    return value


def _validate_location(value: str) -> str:
    """문법은 `LOCATION_PATTERN`, 여기서는 그 위에 `lines:N-M` 의 M ≥ N 만 더 본다."""
    if not _LOCATION.match(value):
        raise ValueError("location 은 `$.a.b[0]` 객체 경로 또는 `lines:N-M` (M ≥ N) 이어야 합니다")
    if value.startswith("lines:"):
        start, end = (int(n) for n in value[len("lines:") :].split("-"))
        if start > end:
            raise ValueError("lines:N-M 은 M ≥ N 이어야 합니다")
    return value


ContractVersion = Literal[1]
NonEmptyStr = Annotated[str, Field(min_length=1)]
Rfc3339 = Annotated[str, AfterValidator(parse_rfc3339_aware)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Location = Annotated[str, Field(pattern=LOCATION_PATTERN), AfterValidator(_validate_location)]
ArtifactKind = Literal[*ARTIFACT_KINDS]
ExecutionStatus = Literal["queued", "accepted", "running", "result_ready", "failed", "unknown"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- 오류 ------------------------------------------------------------------


class ErrorBody(_Contract):
    code: str
    message: str
    field: str | None
    details: dict[str, Any] | None


# --- 실행 요청과 접수 (CONTRACT 1·2·8절) -----------------------------------


class DiagnosisTarget(_Contract):
    run_id: NonEmptyStr


class CodeChangeTarget(_Contract):
    local_registration_id: NonEmptyStr
    base_commit: CommitSha
    verification_profile_id: NonEmptyStr


class ExecutionRequest(_Contract):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    kind: Literal["diagnosis", "code_change"]
    agent_id: NonEmptyStr
    task_revision: int = Field(ge=1)
    request: NonEmptyStr
    input_artifact_ids: list[NonEmptyStr]
    target: DiagnosisTarget | CodeChangeTarget

    @model_validator(mode="after")
    def _check_kind_target(self) -> "ExecutionRequest":
        if len(set(self.input_artifact_ids)) != len(self.input_artifact_ids):
            raise ValueError("input_artifact_ids 에 중복이 있습니다")
        if self.kind == "diagnosis" and not isinstance(self.target, DiagnosisTarget):
            raise ValueError("kind diagnosis 의 target 은 run_id 만 가집니다")
        if self.kind == "code_change":
            if not isinstance(self.target, CodeChangeTarget):
                raise ValueError("kind code_change 의 target 은 local_registration_id 를 가집니다")
            if not self.input_artifact_ids:
                raise ValueError("code_change 는 input_artifact_ids 가 비어 있으면 안 됩니다")
        return self


class ClaimRequest(_Contract):
    contract_version: ContractVersion
    connector_id: NonEmptyStr


class HeartbeatRequest(_Contract):
    contract_version: ContractVersion
    connector_id: NonEmptyStr
    current_execution_id: NonEmptyStr | None


# --- 실행 이벤트 (CONTRACT 3절) --------------------------------------------


class AcceptedData(_Contract):
    pass


class StartedData(_Contract):
    runtime_ref: NonEmptyStr


class ProgressData(_Contract):
    message: str


class ResultReadyData(_Contract):
    result_artifact_id: NonEmptyStr


class FailedData(_Contract):
    code: str
    message: str
    process_stopped: bool


_EVENT_DATA: dict[str, type[_Contract]] = {
    "accepted": AcceptedData,
    "started": StartedData,
    "progress": ProgressData,
    "result_ready": ResultReadyData,
    "failed": FailedData,
}


class ExecutionEvent(_Contract):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    seq: int = Field(ge=1)
    occurred_at: Rfc3339
    type: Literal["accepted", "started", "progress", "result_ready", "failed"]
    data: AcceptedData | StartedData | ProgressData | ResultReadyData | FailedData

    @model_validator(mode="after")
    def _check_data_matches_type(self) -> "ExecutionEvent":
        expected = _EVENT_DATA[self.type]
        if type(self.data) is not expected:
            raise ValueError(f"type {self.type} 의 data 는 {expected.__name__} 형태여야 합니다")
        return self


class EventAck(_Contract):
    execution_id: NonEmptyStr
    last_event_seq: int = Field(ge=0)
    status: ExecutionStatus


class RunStatus(_Contract):
    """진단 API `POST/GET /runs` 응답. 경로 이름이 runs 일 뿐 내용은 Execution 상태다."""

    execution_id: NonEmptyStr
    status: Literal["accepted", "running", "result_ready", "failed"]
    last_event_seq: int = Field(ge=0)
    result_artifact_id: NonEmptyStr | None
    error: ErrorBody | None
    events: list[ExecutionEvent] = []


# --- 산출물 (CONTRACT 4절) -------------------------------------------------


class ArtifactMeta(_Contract):
    contract_version: ContractVersion
    kind: ArtifactKind
    name: NonEmptyStr
    content_type: NonEmptyStr
    sha256: Sha256
    size: int = Field(ge=0)


class ArtifactCreated(_Contract):
    artifact_id: NonEmptyStr
    kind: ArtifactKind
    sha256: Sha256
    size: int = Field(ge=0)


class AttachmentRef(_Contract):
    evidence_id: NonEmptyStr
    version: NonEmptyStr
    content_type: NonEmptyStr
    artifact_id: NonEmptyStr
    sha256: Sha256


class HandoffBundle(_Contract):
    contract_version: ContractVersion
    source_execution_id: NonEmptyStr
    diagnosis_result_artifact_id: NonEmptyStr
    attachments: list[AttachmentRef]

    @model_validator(mode="after")
    def _check_unique_evidence(self) -> "HandoffBundle":
        pairs = [(a.evidence_id, a.version) for a in self.attachments]
        if len(set(pairs)) != len(pairs):
            raise ValueError("attachments 에 같은 evidence_id@version 이 중복됩니다")
        return self


# --- 진단 결과 (CONTRACT 5·6절) --------------------------------------------


class EvidenceVersion(_Contract):
    evidence_id: NonEmptyStr
    version: NonEmptyStr


class EvidenceRef(_Contract):
    evidence_id: NonEmptyStr
    version: NonEmptyStr
    location: Location


class Finding(_Contract):
    claim: str
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class Diagnosis(_Contract):
    code: Literal["response_path_changed"]
    baseline_run_id: NonEmptyStr
    failed_run_id: NonEmptyStr
    old_path: str
    new_path: str
    change_document: EvidenceVersion
    report_contract: EvidenceVersion

    @model_validator(mode="after")
    def _check_paths(self) -> "Diagnosis":
        if not (self.old_path.startswith("$.") and self.new_path.startswith("$.")):
            raise ValueError("old_path·new_path 는 `$.` 로 시작해야 합니다")
        if self.old_path == self.new_path:
            raise ValueError("old_path 와 new_path 가 같습니다")
        return self


class RepairRequest(_Contract):
    target_component: NonEmptyStr
    change: str
    preserve: str
    checks: list[str] = Field(min_length=1)


class MissingInformation(_Contract):
    code: Literal["evidence_unavailable", "evidence_conflict", "unsupported_diagnosis"]
    description: str
    evidence_id: NonEmptyStr | None


class Provenance(_Contract):
    model_id: NonEmptyStr
    prompt_version: NonEmptyStr
    tool_contract_version: NonEmptyStr
    tool_trace_artifact_id: NonEmptyStr


class DiagnosisResult(_Contract):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    run_id: NonEmptyStr
    outcome: Literal["ready_for_handoff", "needs_information"]
    summary: str
    findings: list[Finding]
    diagnosis: Diagnosis | None
    repair_request: RepairRequest | None
    missing_information: list[MissingInformation]
    attachments: list[AttachmentRef]
    provenance: Provenance

    @model_validator(mode="after")
    def _check_outcome(self) -> "DiagnosisResult":
        if self.outcome == "ready_for_handoff":
            if self.diagnosis is None or self.repair_request is None:
                raise ValueError("ready_for_handoff 는 diagnosis·repair_request 가 있어야 합니다")
            if not self.findings:
                raise ValueError("ready_for_handoff 는 findings 가 비어 있으면 안 됩니다")
            if self.missing_information:
                raise ValueError("ready_for_handoff 는 missing_information 이 빈 배열이어야 합니다")
        else:
            if self.diagnosis is not None or self.repair_request is not None:
                raise ValueError("needs_information 은 diagnosis·repair_request 가 null 이어야 합니다")
            if not self.missing_information:
                raise ValueError("needs_information 은 missing_information 이 하나 이상이어야 합니다")
        if self.diagnosis is not None and self.diagnosis.failed_run_id != self.run_id:
            raise ValueError("diagnosis.failed_run_id 는 run_id 와 같아야 합니다")
        return self


# --- 코드 수정 결과 (CONTRACT 7절) -----------------------------------------


class Verification(_Contract):
    profile_id: NonEmptyStr
    result_commit: CommitSha
    exit_code: int
    log_artifact_id: NonEmptyStr


class CodeChangeResult(_Contract):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    outcome: Literal["ready_for_review", "needs_information"]
    summary: str
    base_commit: CommitSha
    result_commit: CommitSha | None
    artifact_ids: list[NonEmptyStr]
    verification: Verification | None

    @model_validator(mode="after")
    def _check_outcome(self) -> "CodeChangeResult":
        if self.outcome == "ready_for_review":
            if self.result_commit is None or self.verification is None:
                raise ValueError("ready_for_review 는 result_commit·verification 이 있어야 합니다")
            if self.verification.result_commit != self.result_commit:
                raise ValueError("verification.result_commit 은 result_commit 과 같아야 합니다")
        return self


# --- 검토 (CONTRACT 8절) ---------------------------------------------------


class ReviewComment(_Contract):
    contract_version: ContractVersion
    task_id: NonEmptyStr
    reviewed_execution_id: NonEmptyStr
    decision: Literal["request_changes"]
    comment: str
    created_at: Rfc3339


# --- 능력과 자동 선택 기록 (CONTRACT 9절) ----------------------------------

_CAPABILITY_SCOPE_KEYS: dict[str, frozenset[str]] = {
    "operations.diagnose": frozenset({"workflow_id"}),
    "code.modify": frozenset({"repository_id"}),
}


class Capability(_Contract):
    code: Literal["operations.diagnose", "code.modify"]
    scope: dict[str, str]

    @model_validator(mode="after")
    def _check_scope_keys(self) -> "Capability":
        expected = _CAPABILITY_SCOPE_KEYS[self.code]
        if set(self.scope) != expected:
            raise ValueError(f"{self.code} 의 scope 키는 {sorted(expected)} 여야 합니다")
        return self


class SelectionRecord(_Contract):
    task_id: NonEmptyStr
    mode: Literal["auto", "manual"]
    required_capability: Capability
    candidate_count: int = Field(ge=0)
    selected_agent_id: NonEmptyStr | None
    matched: Capability | None
    status: Literal["selected", "needs_selection"]
    reason: str

    @model_validator(mode="after")
    def _check_status(self) -> "SelectionRecord":
        if self.status == "selected":
            if self.selected_agent_id is None or self.matched is None:
                raise ValueError("selected 는 selected_agent_id·matched 가 있어야 합니다")
            if self.candidate_count != 1:
                raise ValueError("selected 는 candidate_count 가 1 이어야 합니다")
        elif self.selected_agent_id is not None or self.matched is not None:
            raise ValueError("needs_selection 은 selected_agent_id·matched 가 null 이어야 합니다")
        return self
