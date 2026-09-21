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
