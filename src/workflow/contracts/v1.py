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
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

CONTRACT_VERSION = 1

# CONTRACT.md 4절의 산출물 kind 18종
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
    "claude_jsonl",
    "claude_stderr",
    "generic_result",
    "code_review_result",
    "triage_result",
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


# 업무 종류·능력 코드·scope 키·outcome 식별자 — ARCHITECTURE "업무 종류와 후속 규칙". 경로·파일명·SQL 로 흘러가므로
# 소문자 식별자만 허용한다.
KIND_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"
CAPABILITY_CODE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"

ContractVersion = Literal[1]
NonEmptyStr = Annotated[str, Field(min_length=1)]
KindId = Annotated[str, Field(pattern=KIND_PATTERN)]
CapabilityCode = Annotated[str, Field(pattern=CAPABILITY_CODE_PATTERN)]
Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
Outcome = Identifier
Rfc3339 = Annotated[str, AfterValidator(parse_rfc3339_aware)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Location = Annotated[str, Field(pattern=LOCATION_PATTERN), AfterValidator(_validate_location)]

# 업무 키·결과 브랜치 — ARCHITECTURE "업무와 단계 — phase 14". 키는 서버가 번호로 만든 값이고, 러너는 이 패턴을 통과한
# 값만 브랜치 이름에 넣는다(`/`·`..`·공백이 들어갈 수 없다).
WORK_KEY_PREFIX = "RUN"
WORK_KEY_PATTERN = r"^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,8}$"
WorkKey = Annotated[str, Field(pattern=WORK_KEY_PATTERN)]


def format_work_key(n: int) -> str:
    return f"{WORK_KEY_PREFIX}-{n}"


def result_branch(task_id: str, work_key: str | None, branch_seq: int = 1) -> str:
    """결과 브랜치 이름의 유일한 규칙 — 러너(worktree·push)와 서버(PR head·원본 댓글)가 함께 쓴다. 키가 있으면
    `runloom/<키>`(순번 2 이상이면 `-<순번>`), 없으면 옛 `task/<task_id>`. 계약 밖 값은 이름이 되기 전에 ValueError."""
    if branch_seq < 1 or (work_key is None and branch_seq != 1):
        raise ValueError(f"branch_seq {branch_seq} 는 업무 키가 있을 때만 1 이상")
    if work_key is None:
        return f"task/{task_id}"
    if not re.fullmatch(WORK_KEY_PATTERN, work_key):
        raise ValueError(f"업무 키 {work_key!r} 가 패턴 {WORK_KEY_PATTERN} 밖")
    return f"runloom/{work_key}" if branch_seq == 1 else f"runloom/{work_key}-{branch_seq}"
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


class CommitReviewTarget(_Contract):
    """`code_review` 전용 — 검토할 수정 실행과 그 결과 커밋을 고정한다 (CONTRACT 13.3)."""

    local_registration_id: NonEmptyStr
    source_execution_id: NonEmptyStr
    base_commit: CommitSha
    result_commit: CommitSha

    @model_validator(mode="after")
    def _check_commits(self) -> "CommitReviewTarget":
        if self.base_commit == self.result_commit:
            raise ValueError("result_commit 이 base_commit 과 같습니다 — 검토할 변경이 없습니다")
        return self


class LocalTarget(_Contract):
    """내장이 아닌 종류를 로컬 도구가 읽기 전용으로 수행할 때의 target. worktree·커밋·검증 프로필이 없다."""

    local_registration_id: NonEmptyStr


class TriageTarget(_Contract):
    """판단(결과 형태 `triage_result`) 전용 — 판단 Agent 의 로컬 등록과 읽기 전용으로 꺼낼 기본 브랜치 끝 (CONTRACT 17.1)."""

    local_registration_id: NonEmptyStr
    base_commit: CommitSha


# --- 업무 종류와 후속 규칙 (CONTRACT 11절) ---------------------------------

# 셀프호스트 전용(ADR-0019) — 진단 데모의 `diagnosis`·`code_change` 는 `main` 에만 있다
BUILTIN_KIND_NAMES: tuple[str, ...] = ("bug_fix", "code_review", "triage")


class KindSpec(_Contract):
    """업무 종류의 봉투. 내용은 정의하지 않는다 — 중앙은 입력 kind·출력 kind·outcome 목록만 본다."""

    kind: KindId
    label: NonEmptyStr
    capability_code: CapabilityCode
    scope_key: Identifier
    input_kinds: list[ArtifactKind]
    output_kind: Literal[
        "diagnosis_result", "code_change_result", "code_review_result", "generic_result", "triage_result"
    ]
    outcomes: list[Outcome] = Field(min_length=1)
    instructions: str
    builtin: bool

    @model_validator(mode="after")
    def _check_builtin(self) -> "KindSpec":
        if len(set(self.input_kinds)) != len(self.input_kinds):
            raise ValueError("input_kinds 에 중복이 있습니다")
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("outcomes 에 중복이 있습니다")
        if self.builtin and self.kind not in BUILTIN_KIND_NAMES:
            raise ValueError(f"내장 종류는 {list(BUILTIN_KIND_NAMES)} 뿐입니다")
        if not self.builtin and self.kind in BUILTIN_KIND_NAMES:
            raise ValueError(f"{self.kind} 은 내장 종류 이름입니다")
        if not self.builtin and self.output_kind != "generic_result":
            raise ValueError("사용자 정의 종류의 output_kind 는 generic_result 여야 합니다")
        return self


BUILTIN_KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        kind="bug_fix", label="버그 수정", capability_code="code.fix", scope_key="repository_id",
        input_kinds=[], output_kind="code_change_result",
        outcomes=["ready_for_review", "needs_information"], instructions="", builtin=True,
    ),
    KindSpec(
        kind="code_review", label="커밋 검토", capability_code="code.review", scope_key="repository_id",
        input_kinds=["code_change_result"], output_kind="code_review_result",
        outcomes=["approved", "changes_requested", "needs_information"], instructions="", builtin=True,
    ),
    KindSpec(  # 판단 — outcomes 는 진행 여부 세 값 (ADR-0025 결정 1)
        kind="triage", label="판단", capability_code="code.triage", scope_key="repository_id",
        input_kinds=[], output_kind="triage_result",
        outcomes=["ready", "needs_check", "unsuitable"], instructions="", builtin=True,
    ),
)


class SuccessorRule(_Contract):
    """선행 결과의 outcome 이 `on_outcomes` 에 있으면 `handoff_kinds` 산출물을 넘겨 `to_kind` 를 시작한다.
    `on_outcomes ⊆ from_kind.outcomes`·`handoff_kinds ⊇ to_kind.input_kinds` 는 서버가 등록부로 검사한다.
    `placement` 는 후속 Task 를 둘 곳 — `same_work` 원인 Task 업무의 다음 단계, `new_work` 새 업무(ADR-0020)."""

    from_kind: KindId
    on_outcomes: list[Outcome] = Field(min_length=1)
    to_kind: KindId
    handoff_kinds: list[ArtifactKind]
    placement: Literal["same_work", "new_work"] = "same_work"

    @model_validator(mode="after")
    def _check_rule(self) -> "SuccessorRule":
        if self.from_kind == self.to_kind:
            raise ValueError("from_kind 와 to_kind 가 같습니다")
        if len(set(self.on_outcomes)) != len(self.on_outcomes):
            raise ValueError("on_outcomes 에 중복이 있습니다")
        if len(set(self.handoff_kinds)) != len(self.handoff_kinds):
            raise ValueError("handoff_kinds 에 중복이 있습니다")
        if "handoff_bundle" in self.handoff_kinds:
            raise ValueError("handoff_kinds 에 handoff_bundle 은 넣을 수 없습니다 — 묶음 자체입니다")
        return self


BUILTIN_RULES: tuple[SuccessorRule, ...] = (
    SuccessorRule(
        from_kind="bug_fix", on_outcomes=["ready_for_review"], to_kind="code_review",
        handoff_kinds=["code_change_result", "diff", "test_log_after", "verification_log"],
    ),
)


class _OmitUnknownMeasure(_Contract):
    """측정 칸(phase 9)이 null 이면 직렬화에서 뺀다 — 구버전 서버는 null 이라도 모르는 칸을 422 로 거부하고,
    저장된 이벤트와의 중복 비교(`repo._event_content`)도 기존 모양 그대로여야 한다."""

    _MEASURE_FIELDS: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _omit_unknown_measure(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for name in self._MEASURE_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data


class ExecutionRequest(_OmitUnknownMeasure):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    kind: KindId
    agent_id: NonEmptyStr
    task_revision: int = Field(ge=1)
    request: NonEmptyStr
    input_artifact_ids: list[NonEmptyStr]
    target: DiagnosisTarget | CodeChangeTarget | CommitReviewTarget | LocalTarget | TriageTarget
    kind_spec: KindSpec | None = None
    # 결과 브랜치 `result_branch(task_id, work_key, branch_seq)` 의 두 칸 (CONTRACT 15.2·15.3). 없으면 옛 `task/<task_id>`
    work_key: WorkKey | None = None
    branch_seq: int = Field(default=1, ge=1)
    # 검증만 다시 (CONTRACT 16.1): 에이전트 없이 이 커밋을 다시 검증한다. null 이면 직렬화에서 빠진다 — 옛 러너가 받는다
    verify_only_commit: CommitSha | None = None

    _MEASURE_FIELDS: ClassVar[tuple[str, ...]] = ("verify_only_commit",)

    @model_validator(mode="after")
    def _check_kind_target(self) -> "ExecutionRequest":
        if len(set(self.input_artifact_ids)) != len(self.input_artifact_ids):
            raise ValueError("input_artifact_ids 에 중복이 있습니다")
        if self.verify_only_commit is not None:
            if not isinstance(self.target, CodeChangeTarget):
                raise ValueError("verify_only_commit 은 target 이 CodeChangeTarget 일 때만 씁니다")
            if not self.input_artifact_ids:
                raise ValueError("verify_only_commit 은 input_artifact_ids 가 비어 있으면 안 됩니다")
            if self.verify_only_commit == self.target.base_commit:
                raise ValueError("verify_only_commit 은 target.base_commit 과 달라야 합니다")
        if self.work_key is None and self.branch_seq != 1:
            raise ValueError("branch_seq 는 work_key 가 있을 때만 1 이 아닐 수 있습니다")
        if self.kind_spec is not None and self.kind_spec.kind != self.kind:
            raise ValueError("kind_spec.kind 는 kind 와 같아야 합니다")
        # 판단은 결과 형태로 target 모양이 정해진다 — 봉투를 늘 싣는다 (CONTRACT 17.1)
        if self.kind_spec is not None and self.kind_spec.output_kind == "triage_result":
            if not isinstance(self.target, TriageTarget):
                raise ValueError("결과 형태 triage_result 의 target 은 local_registration_id·base_commit 입니다")
            if self.input_artifact_ids:
                raise ValueError("판단 요청은 input_artifact_ids 가 빈 배열이어야 합니다")
            return self
        if self.kind == "triage":
            raise ValueError("kind triage 는 kind_spec 이 있어야 합니다")
        # `diagnosis`·`code_change` 는 `main` 의 진단 데모 요청(kind_spec 없음) 모양으로만 남는다 (ADR-0019).
        # kind_spec 이 있으면 그 이름의 사용자 정의 종류다 — 아래 else 로 간다
        if self.kind == "diagnosis" and self.kind_spec is None:
            if not isinstance(self.target, DiagnosisTarget):
                raise ValueError("kind diagnosis 의 target 은 run_id 만 가집니다")
        elif self.kind == "code_change" and self.kind_spec is None:
            if not isinstance(self.target, CodeChangeTarget):
                raise ValueError("kind code_change 의 target 은 local_registration_id 를 가집니다")
            if not self.input_artifact_ids:
                raise ValueError("code_change 는 input_artifact_ids 가 비어 있으면 안 됩니다")
        elif self.kind == "bug_fix":
            # 진단 인계가 없다 — 첫 시도는 입력이 비어도 된다. 재작업은 이전 결과·검토 결과가 붙는다.
            if not isinstance(self.target, CodeChangeTarget):
                raise ValueError("kind bug_fix 의 target 은 local_registration_id·base_commit·verification_profile_id 입니다")
        elif self.kind == "code_review":
            if not isinstance(self.target, CommitReviewTarget):
                raise ValueError("kind code_review 의 target 은 source_execution_id·result_commit 을 가집니다")
            if not self.input_artifact_ids:
                raise ValueError("code_review 는 input_artifact_ids 가 비어 있으면 안 됩니다")
        else:
            # 내장이 아닌 종류. kind_spec.builtin 은 KindSpec 검증(내장 이름만 builtin)과 위의 kind 일치로 이미 False 다.
            if not isinstance(self.target, LocalTarget):
                raise ValueError("내장이 아닌 kind 의 target 은 local_registration_id 하나입니다")
            if self.kind_spec is None:
                raise ValueError("내장이 아닌 kind 는 kind_spec 이 있어야 합니다")
        return self


RunnerCapability = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
RUNNER_CAPABILITY_VERIFY_ONLY = "verify_only"
RUNNER_CAPABILITIES: tuple[str, ...] = (RUNNER_CAPABILITY_VERIFY_ONLY,)


class ClaimRequest(_OmitUnknownMeasure):
    """`supported_kinds` 가 null(생략)이면 구버전 연결 프로그램 — 서버는 사용자 정의 종류만 배정한다
    (ADR-0014 결정 4, 옛 내장 `code_change` 는 ADR-0019 로 없어졌다). `registration_heads` 는 `local_registration_id` → fetch 뒤 `origin` 기본 브랜치 커밋 —
    서버가 이 연결 프로그램 Agent 의 `base_commit` 을 갱신한다. null(생략)이면 보고 없음 (ADR-0018 결정 2)."""

    contract_version: ContractVersion
    connector_id: NonEmptyStr
    supported_kinds: list[KindId] | None = None
    registration_heads: Annotated[dict[NonEmptyStr, CommitSha], Field(max_length=50)] | None = None
    # 러너가 할 수 있는 선택 동작 (CONTRACT 16.2). null 이면 보고 없음(옛 러너) — 직렬화에서 빠진다
    capabilities: Annotated[list[RunnerCapability], Field(max_length=20)] | None = None

    _MEASURE_FIELDS: ClassVar[tuple[str, ...]] = ("capabilities",)

    @model_validator(mode="after")
    def _check_unique_kinds(self) -> "ClaimRequest":
        if self.supported_kinds is not None and len(set(self.supported_kinds)) != len(self.supported_kinds):
            raise ValueError("supported_kinds 에 중복이 있습니다")
        if self.capabilities is not None and len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities 에 중복이 있습니다")
        return self


class HeartbeatRequest(_Contract):
    contract_version: ContractVersion
    connector_id: NonEmptyStr
    current_execution_id: NonEmptyStr | None


# --- 실행 이벤트 (CONTRACT 3절) --------------------------------------------


class AcceptedData(_Contract):
    pass


class ExecutionUsage(_Contract):
    """도구가 보고한 실행 사용량 (CONTRACT 3.1절, ADR-0015). 모르는 값은 null 이며 0 이 아니다."""

    # strict 에서도 float 칸은 JSON 정수(`0`)를 받는다 — bool 은 거부된다.
    cost_usd: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class StartedData(_OmitUnknownMeasure):
    _MEASURE_FIELDS = ("folder_commit", "folder_dirty")

    runtime_ref: NonEmptyStr
    # 러너 로컬 등록 폴더(worktree 아님)의 HEAD·미커밋 변경 여부. 못 읽으면 null.
    folder_commit: CommitSha | None = None
    folder_dirty: bool | None = None

    @model_validator(mode="after")
    def _check_dirty_needs_commit(self) -> "StartedData":
        if self.folder_dirty is not None and self.folder_commit is None:
            raise ValueError("folder_dirty 는 folder_commit 과 함께만 보낼 수 있습니다")
        return self


class ProgressData(_Contract):
    message: str


class ResultReadyData(_OmitUnknownMeasure):
    _MEASURE_FIELDS = ("usage", "branch_pushed")

    result_artifact_id: NonEmptyStr
    usage: ExecutionUsage | None = None
    # 러너가 결과 브랜치(`result_branch`)를 origin 에 push 한 결과 (ADR-0018 결정 4). 생략 = 시도 안 함·구버전.
    branch_pushed: bool | None = None


class FailedData(_OmitUnknownMeasure):
    _MEASURE_FIELDS = ("usage",)

    code: str
    message: str
    process_stopped: bool
    usage: ExecutionUsage | None = None


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


class InputRef(_Contract):
    """인계 묶음의 입력 항목 — 규칙 `handoff_kinds` 로 모은 선행 실행의 산출물."""

    kind: ArtifactKind
    artifact_id: NonEmptyStr
    sha256: Sha256
    content_type: NonEmptyStr


class HandoffBundle(_Contract):
    contract_version: ContractVersion
    source_execution_id: NonEmptyStr
    source_kind: KindId
    source_result_artifact_id: NonEmptyStr
    inputs: list[InputRef]
    attachments: list[AttachmentRef]

    @model_validator(mode="after")
    def _check_unique(self) -> "HandoffBundle":
        input_ids = [i.artifact_id for i in self.inputs]
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("inputs 에 같은 artifact_id 가 중복됩니다")
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


# --- 범용 결과 (CONTRACT 11절) ---------------------------------------------


class GenericResult(_Contract):
    """내장이 아닌 종류의 결과 봉투. 중앙은 `outcome ∈ KindSpec.outcomes` 만 판정하고 완료는 사람이 한다."""

    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    kind: KindId
    outcome: Outcome
    summary: str
    artifact_ids: list[NonEmptyStr]

    @model_validator(mode="after")
    def _check_unique_artifacts(self) -> "GenericResult":
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("artifact_ids 에 중복이 있습니다")
        return self


# --- 커밋 검토 결과 (CONTRACT 13.4) -------------------------------------------


class ReviewFinding(_Contract):
    """`path` 는 표시용 문자열이다 — 파일 경로로 열거나 실행하지 않는다."""

    severity: Literal["blocking", "non_blocking"]
    path: str | None
    line: Annotated[int, Field(ge=1)] | None
    message: NonEmptyStr

    @model_validator(mode="after")
    def _check_line(self) -> "ReviewFinding":
        if self.line is not None and self.path is None:
            raise ValueError("line 은 path 가 있을 때만 씁니다")
        return self


class CodeReviewResult(_Contract):
    """`code_review` 의 결과 봉투. `reviewed_commit` 이 target 의 `result_commit`·최신 수정 결과인지는 중앙이 본다.
    `approved` 는 검토 완료일 뿐 이슈 종료·병합 권한이 아니다."""

    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    source_execution_id: NonEmptyStr
    reviewed_commit: CommitSha
    outcome: Literal["approved", "changes_requested", "needs_information"]
    summary: str
    findings: list[ReviewFinding]
    missing_information: list[NonEmptyStr]
    artifact_ids: list[NonEmptyStr]

    @model_validator(mode="after")
    def _check_outcome(self) -> "CodeReviewResult":
        blocking = [f for f in self.findings if f.severity == "blocking"]
        if self.outcome == "changes_requested" and not blocking:
            raise ValueError("changes_requested 는 blocking 지적이 1개 이상이어야 합니다")
        if self.outcome == "approved" and blocking:
            raise ValueError("approved 는 blocking 지적이 없어야 합니다")
        if self.outcome == "needs_information":
            if not self.missing_information:
                raise ValueError("needs_information 은 missing_information 이 하나 이상이어야 합니다")
        elif self.missing_information:
            raise ValueError(f"{self.outcome} 는 missing_information 이 빈 배열이어야 합니다")
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("artifact_ids 에 중복이 있습니다")
        return self


# --- 판단 결과·후보 (CONTRACT 17.2·17.3, ADR-0025) ------------------------------

TRIAGE_PROCEED: tuple[str, ...] = ("ready", "needs_check", "unsuitable")
TRIAGE_CRITERIA: tuple[str, ...] = ("clarity", "verifiability", "scope", "risk", "permission", "history", "dependency")
TRIAGE_CANDIDATES_MAX = 30
_TriageNote = Annotated[str, Field(min_length=1, max_length=200)]


class TriageReason(_Contract):
    criterion: Literal[*TRIAGE_CRITERIA]
    note: _TriageNote  # 표시만 한다 — 명령·경로로 쓰지 않는다


class TriageAssignee(_Contract):
    type: Literal["member", "agent"]
    id: NonEmptyStr


class TriageResult(_Contract):
    """판단의 결과 봉투 — **제안**이다. 후보 목록 안인지·`inspected_commit == target.base_commit` 인지는 중앙이 본다."""

    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    inspected_commit: CommitSha
    proceed: Literal[*TRIAGE_PROCEED]
    confidence: Annotated[float, Field(ge=0, le=1)]
    proposed_kind: KindId | None
    assignee: TriageAssignee | None
    predecessors: Annotated[list[WorkKey], Field(max_length=5)]
    reasons: Annotated[list[TriageReason], Field(min_length=1, max_length=7)]
    missing_information: Annotated[list[_TriageNote], Field(max_length=10)]

    @model_validator(mode="after")
    def _check_proceed(self) -> "TriageResult":
        if len(set(self.predecessors)) != len(self.predecessors):
            raise ValueError("predecessors 에 중복이 있습니다")
        criteria = [r.criterion for r in self.reasons]
        if len(set(criteria)) != len(criteria):
            raise ValueError("reasons 의 criterion 에 중복이 있습니다")
        if self.proceed == "ready":
            if self.proposed_kind is None or self.assignee is None:
                raise ValueError("ready 는 proposed_kind·assignee 가 있어야 합니다")
            if self.missing_information:
                raise ValueError("ready 는 missing_information 이 빈 배열이어야 합니다")
        elif self.proceed == "needs_check" and not self.missing_information:
            raise ValueError("needs_check 는 missing_information 이 하나 이상이어야 합니다")
        return self


class TriageKindCandidate(_Contract):
    kind: KindId
    label: NonEmptyStr


class TriageMemberCandidate(_Contract):
    member_id: NonEmptyStr
    display_name: NonEmptyStr
    open_work: int = Field(ge=0)


class TriageAgentCandidate(_Contract):
    agent_id: NonEmptyStr
    name: NonEmptyStr
    owner_name: str | None
    online: bool
    open_work: int = Field(ge=0)
    kinds: list[KindId] = Field(min_length=1)  # 이 저장소 범위로 맡을 수 있는 후보 종류


class TriagePredecessorCandidate(_Contract):
    work_key: WorkKey
    title: NonEmptyStr
    status: NonEmptyStr  # 업무 상태


def _check_unique(values: list[str], name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} 에 중복이 있습니다")


class TriageCandidates(_Contract):
    """판단을 시작할 때 고정한 후보 목록 — 요청문에 글로 넣고 판단 로그에 저장한다. 러너 payload 가 아니다."""

    current_kind: KindId
    kinds: list[TriageKindCandidate] = Field(min_length=1)
    members: list[TriageMemberCandidate]
    agents: list[TriageAgentCandidate] = Field(max_length=TRIAGE_CANDIDATES_MAX)
    predecessors: list[TriagePredecessorCandidate] = Field(max_length=TRIAGE_CANDIDATES_MAX)

    @model_validator(mode="after")
    def _check_ids(self) -> "TriageCandidates":
        _check_unique([k.kind for k in self.kinds], "kinds")
        _check_unique([m.member_id for m in self.members], "members")
        _check_unique([a.agent_id for a in self.agents], "agents")
        _check_unique([p.work_key for p in self.predecessors], "predecessors")
        if self.current_kind not in {k.kind for k in self.kinds}:
            raise ValueError("kinds 에 current_kind 가 있어야 합니다")
        for agent in self.agents:
            _check_unique(agent.kinds, f"agents[{agent.agent_id}].kinds")
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


class Capability(_Contract):
    """계약은 형태만 본다 — `code` 가 어느 종류의 `capability_code` 이고 scope 키가 그 종류의 `scope_key` 인지는
    서버가 등록부(`KindSpec`)로 검사한다."""

    code: CapabilityCode
    scope: dict[Identifier, NonEmptyStr] = Field(min_length=1, max_length=1)


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


# --- n8n 입구·callback (CONTRACT 12절) ---------------------------------------

_CALLBACK_URL_MAX_LENGTH = 2048


def _validate_callback_url(value: str) -> str:
    """http/https 만, 공백 없음, 2048자 이하. 정규화하지 않는다 — n8n 이 준 resume URL 그대로 저장·전송한다."""
    if not value.startswith(("http://", "https://")):
        raise ValueError("callback_url 은 http:// 또는 https:// 로 시작해야 합니다")
    if any(ch.isspace() for ch in value):
        raise ValueError("callback_url 에 공백이 있습니다")
    if len(value) > _CALLBACK_URL_MAX_LENGTH:
        raise ValueError(f"callback_url 은 {_CALLBACK_URL_MAX_LENGTH}자 이하여야 합니다")
    return value


CallbackUrl = Annotated[str, AfterValidator(_validate_callback_url)]


class InboundItem(_Contract):
    """입구 본문의 항목 — `domain/task_sources.Issue` 와 같은 모양(`url` 없음). 라벨 규칙으로만 종류를 정한다."""

    key: NonEmptyStr
    title: NonEmptyStr
    body: str  # Task.request 가 된다 — 명령·경로로 해석하지 않는다
    labels: list[str]
    blocked_by: list[str]


class InboundChainRequest(_Contract):
    contract_version: ContractVersion
    items: list[InboundItem]  # 1~10개
    callback_url: CallbackUrl | None = None

    @model_validator(mode="after")
    def _check_items(self) -> "InboundChainRequest":
        if not self.items:
            raise ValueError("items 는 1개 이상이어야 합니다")
        if len(self.items) > 10:
            raise ValueError(f"items 는 10개 이하여야 합니다 (현재 {len(self.items)}개)")
        keys: set[str] = set()
        for item in self.items:
            if item.key in keys:
                raise ValueError(f"key {item.key} 가 요청 안에서 중복입니다")
            keys.add(item.key)
        # adapters/task_sources._check_references 와 같은 규칙 — 자기 참조 금지, 같은 요청의 key 만
        for item in self.items:
            for dep in item.blocked_by:
                if dep == item.key:
                    raise ValueError(f"항목 {item.key} 가 자기 자신을 blocked_by 로 가리킵니다")
                if dep not in keys:
                    raise ValueError(f"blocked_by 의 {dep} 가 같은 요청의 key 가 아닙니다")
        return self


class InboundTaskRef(_Contract):
    task_id: NonEmptyStr
    key: NonEmptyStr
    kind: KindId
    status: NonEmptyStr  # USER_STATUS_LABELS 의 문구


class InboundSkipped(_Contract):
    key: NonEmptyStr
    reason: NonEmptyStr


class InboundChainResponse(_Contract):
    contract_version: ContractVersion
    chain_id: NonEmptyStr
    chain_url: str | None
    started: bool
    start_error: ErrorBody | None
    tasks: list[InboundTaskRef]
    skipped: list[InboundSkipped]


class CallbackGate(_Contract):
    label: NonEmptyStr
    status_label: NonEmptyStr
    reason: str


class CallbackTask(_Contract):
    task_id: NonEmptyStr
    key: str  # source_ref. 단독 Task 도 key 가 있다
    kind: KindId
    title: NonEmptyStr
    status: NonEmptyStr
    status_reason: str
    outcome: str | None  # 최신 결과 봉투의 outcome — 결과가 없으면 null
    summary: str | None
    task_url: str | None


class ChainCallback(_Contract):
    """체인이 사람 차례(`chain_settled`)가 될 때 워커가 `callback_url` 로 보내는 본문. 체인당 1회."""

    contract_version: ContractVersion
    chain_id: NonEmptyStr
    title: NonEmptyStr
    source: Literal["n8n"]
    chain_url: str | None
    settled_at: Rfc3339
    human_gate: CallbackGate
    tasks: list[CallbackTask] = Field(min_length=1)
