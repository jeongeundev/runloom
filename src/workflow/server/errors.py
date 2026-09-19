"""API 오류 — CONTRACT 공통 규칙의 오류 본문(`code`, `message`, `field`, `details`) 과 HTTP 상태.

문구는 CONTRACT 3절 오류표를 그대로 쓴다. 메시지에 요청 헤더·토큰·요청 객체를 넣지 않는다.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from workflow.adapters.errors import (
    ActiveExecutionExists,
    AdapterError,
    ArtifactMissing,
    DuplicateStartKey,
    EventConflict,
    Forbidden,
    HashMismatch,
    InvalidTransition,
    NotFound,
    SequenceGap,
)
from workflow.contracts.v1 import ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.details = details

    def body(self) -> dict[str, Any]:
        return ErrorBody(
            code=self.code, message=self.message, field=self.field, details=self.details
        ).model_dump(mode="json")

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, content=self.body())


def _josa(value: object, with_batchim: str, without_batchim: str) -> str:
    """숫자 뒤 조사: 영·일·삼·육·칠·팔은 받침이 있다. 숫자가 아니면 받침 없는 쪽을 쓴다."""
    text = str(value)
    return with_batchim if text and text[-1] in "013678" else without_batchim


def validation_error_to_api(errors: list[dict[str, Any]]) -> ApiError:
    """pydantic/FastAPI 검증 오류 목록의 첫 항목을 422 로 옮긴다. `loc` 의 `body` 접두사는 뺀다."""
    err = errors[0] if errors else {}
    loc = list(err.get("loc", ()))
    if loc and loc[0] == "body":
        loc = loc[1:]
    kind = err.get("type")
    if kind == "extra_forbidden":
        name = str(loc[-1]) if loc else ""
        return ApiError(422, "unknown_field", f"필드 {name}는 허용되지 않습니다.", field=name)
    if loc and loc[-1] == "contract_version":
        value = err.get("input")
        return ApiError(
            422,
            "unsupported_contract_version",
            f"contract_version {value}{_josa(value, '은', '는')} 지원하지 않습니다.",
            field="contract_version",
        )
    if kind == "json_invalid":
        return ApiError(422, "invalid_field", "본문이 올바른 JSON이 아닙니다.")
    field = ".".join(str(p) for p in loc) or None
    return ApiError(422, "invalid_field", f"필드 {field}: {err.get('msg', '올바르지 않습니다')}", field=field)


def adapter_error_to_api(exc: AdapterError) -> ApiError:
    if isinstance(exc, NotFound):
        return ApiError(404, "not_found", f"{exc}을 찾을 수 없습니다.")
    if isinstance(exc, Forbidden):
        return ApiError(403, "forbidden", str(exc))
    if isinstance(exc, EventConflict):
        return ApiError(
            409,
            "event_conflict",
            f"seq {exc.seq}{_josa(exc.seq, '은', '는')} 다른 내용으로 이미 저장되었습니다.",
            field="seq",
        )
    if isinstance(exc, SequenceGap):
        n = exc.expected_seq
        return ApiError(
            409,
            "sequence_gap",
            f"seq {n}{_josa(n, '이', '가')} 먼저 필요합니다.",
            field="seq",
            details={"expected_seq": n},
        )
    if isinstance(exc, InvalidTransition):
        if exc.reason is not None:
            return ApiError(
                409,
                "invalid_transition",
                f"{exc.event_type} 전에 참조한 산출물이 업로드·해시 확인되어야 합니다.",
                field="type",
                details={"reason": exc.reason},
            )
        if exc.event_type is None:
            message = f"{exc.current_status} 상태에서는 허용되지 않는 전환입니다."
        else:
            message = f"{exc.current_status} 상태에서는 {exc.event_type}를 받을 수 없습니다."
        return ApiError(
            409, "invalid_transition", message, field="type",
            details={"current_status": exc.current_status},
        )
    if isinstance(exc, HashMismatch):
        return ApiError(422, "hash_mismatch", "본문 해시가 meta.sha256과 다릅니다.", field="sha256")
    if isinstance(exc, (ActiveExecutionExists, DuplicateStartKey)):
        return ApiError(409, "execution_conflict", f"이미 실행이 있습니다: {exc}")
    if isinstance(exc, ArtifactMissing):
        return ApiError(500, "artifact_missing", "산출물 파일이 저장소에 없습니다.")
    return ApiError(500, "internal_error", "저장 계층 오류입니다.")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return validation_error_to_api(list(exc.errors())).response()

    @app.exception_handler(ValidationError)
    async def _pydantic_validation(request: Request, exc: ValidationError) -> JSONResponse:
        return validation_error_to_api(list(exc.errors())).response()

    @app.exception_handler(AdapterError)
    async def _adapter(request: Request, exc: AdapterError) -> JSONResponse:
        return adapter_error_to_api(exc).response()
