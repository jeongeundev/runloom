"""저장 계층 예외. HTTP 상태로 옮기는 것은 server 계층(Step 5)의 일이다."""


class AdapterError(Exception):
    pass


class ActiveExecutionExists(AdapterError):
    """같은 Task 에 `released_at IS NULL` 인 Execution 이 이미 있다."""


class DuplicateStartKey(AdapterError):
    """같은 `(task_id, start_key)` 로 이미 실행을 만들었다 — 해제 뒤에도 재사용 불가."""


class EventConflict(AdapterError):
    """같은 seq 에 다른 내용의 이벤트가 이미 저장돼 있다."""

    def __init__(self, seq: int):
        super().__init__(f"seq {seq}에 다른 내용이 이미 저장돼 있습니다")
        self.seq = seq


class SequenceGap(AdapterError):
    def __init__(self, expected_seq: int):
        super().__init__(f"seq {expected_seq}가 먼저 필요합니다")
        self.expected_seq = expected_seq


class InvalidTransition(AdapterError):
    """`event_type` 은 거부한 이벤트 종류(서버 관찰로 막힌 경우 None), `reason` 은 추가 사유."""

    def __init__(
        self, current_status: str, *, event_type: str | None = None, reason: str | None = None
    ):
        super().__init__(f"{current_status} 상태에서는 허용되지 않는 전환입니다")
        self.current_status = current_status
        self.event_type = event_type
        self.reason = reason


class NotFound(AdapterError):
    pass


class Forbidden(AdapterError):
    pass


class HashMismatch(AdapterError):
    """본문의 sha256·크기가 meta 와 다르다."""


class ArtifactMissing(AdapterError):
    """DB 행은 있으나 저장소에 파일이 없다."""


class KindProtected(AdapterError):
    """내장 종류(`builtin`)는 삭제할 수 없다."""


class KindInUse(AdapterError):
    """Task 또는 후속 규칙이 참조하는 종류는 삭제할 수 없다."""


class DuplicateKind(AdapterError):
    """같은 세션에 같은 `kind` 가 이미 등록돼 있다."""


class DuplicateRule(AdapterError):
    """같은 세션에 같은 `(from_kind, to_kind)` 규칙이 이미 등록돼 있다."""


class StaleRequest(AdapterError):
    """사람 요청의 revision 이 `expected_revision` 과 다르거나 이미 응답됐다 (409 `stale_request`)."""

    def __init__(self, request_id: str, current_revision: int):
        super().__init__(f"사람 요청 {request_id} 가 이미 revision {current_revision} 입니다")
        self.current_revision = current_revision


class TriageRunning(AdapterError):
    """그 업무에 이미 도는(`running`) 판단이 있다 — 업무마다 하나(`ux_triage_logs_running`, phase 19)."""


class StaleCriteria(AdapterError):
    """판단 기준의 현재 버전이 `expected_version` 과 다르다 (409 `stale_criteria`)."""

    def __init__(self, current: int):
        super().__init__(f"판단 기준이 이미 v{current} 입니다")
        self.current = current


class AutostartLocked(AdapterError):
    """그 종류의 사람 처리 판단이 기준 건수에 모자라 자동 시작을 켤 수 없다 (409 `triage_autostart_locked`)."""

    def __init__(self, count: int):
        super().__init__(f"판단 기록 {count}건")
        self.count = count


class StaleConfig(AdapterError):
    """소스 설정의 저장된 `config_revision` 이 `expected_revision` 과 다르다 (409 `stale_config`)."""

    def __init__(self, source_id: str, current_revision: int):
        super().__init__(f"source {source_id} revision {current_revision}")
        self.source_id = source_id
        self.current_revision = current_revision


class TaskClosed(AdapterError):
    """마감된 Task 에 새 실행·사람 응답을 붙이려 했다 (409 `task_closed`)."""


class ResponseConflict(AdapterError):
    """같은 `(request_id, response_id)` 에 다른 내용의 응답이 이미 저장돼 있다."""


class RegistrationTaken(AdapterError):
    """같은 `local_registration_id` 의 Agent 를 취소되지 않은 다른 연결 프로그램이 쓰고 있다 (409 `registration_taken`)."""


class EmailTaken(AdapterError):
    """같은 워크스페이스에 같은 이메일의 멤버가 이미 있다 (422 `invalid_field` `email`)."""


class LastAdmin(AdapterError):
    """역할 변경·비활성화로 활성 관리자가 0 이 된다 (409 `last_admin`)."""


class NextStepExists(AdapterError):
    """같은 원인(결과 하나·반환 하나)에 결과 뒤 판단이 이미 있다 — `ux_triage_logs_after_result`·`…_request_returned` (phase 22)."""

    def __init__(self, cause_key: str):
        super().__init__(f"결과 뒤 판단이 이미 있습니다 — {cause_key}")
        self.cause_key = cause_key


class NextStepHandled(AdapterError):
    """결과 뒤 판단 제안이 이미 처리됐다(`accepted`·`dismissed`) (phase 22)."""

    def __init__(self, triage_id: str):
        super().__init__(f"다음 단계 제안 {triage_id} 는 이미 처리됐습니다")
        self.triage_id = triage_id
