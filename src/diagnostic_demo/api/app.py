"""진단 데모 API — CONTRACT 1절(`POST /runs`, `GET /runs/{id}`), 산출물 다운로드, `/capabilities`, `/budget`.

중앙 워커만 호출한다 (`Authorization: Bearer <DIAG_API_TOKEN>`, ARCHITECTURE 인증 절). 진단 자체는 워커
(`diagnostic_demo.worker`)가 같은 DB 를 보고 처리한다 — 접수와 시작을 구분한다 (ARCHITECTURE "진단 API" 행).
- 같은 execution_id 재접수: 의미 필드가 같으면 200 현재 상태, 다르면 409. 기존 접수를 덮어쓰지 않는다.
- 하루 전체 건수·누적 비용 상한은 접수 시점에 429 (CONTRACT 10절). 재접수(멱등)는 상한과 무관하다.
- 모듈 변수 `app` 은 `python3 -m uvicorn diagnostic_demo.api.app:app` 진입점이며 import 시 환경변수를 읽는다.
  테스트는 `DIAG_SKIP_APP=1` 로 건너뛰고 `create_app(settings)` 를 직접 쓴다 (tests/conftest.py).
"""

import hmac
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from diagnostic_demo import db
from diagnostic_demo.artifact_store import ArtifactStore
from diagnostic_demo.pricing import total_estimated_usd
from diagnostic_demo.settings import Settings, load_settings
from diagnostic_demo.tools.api import TOOL_CONTRACT_VERSION
from diagnostic_demo.worker.prompt import PROMPT_VERSION
from workflow.contracts.v1 import CONTRACT_VERSION, ErrorBody, ExecutionRequest

KST = ZoneInfo("Asia/Seoul")


# --- 오류 ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, field: str | None = None, details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.body = ErrorBody(code=code, message=message, field=field, details=details)

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, content=self.body.model_dump(mode="json"))


def _josa(value: object, with_batchim: str, without_batchim: str) -> str:
    """숫자 뒤 조사: 영·일·삼·육·칠·팔은 받침이 있다. 숫자가 아니면 받침 없는 쪽을 쓴다."""
    text = str(value)
    return with_batchim if text and text[-1] in "013678" else without_batchim


def _validation_error(errors: list[dict[str, Any]]) -> ApiError:
    err = errors[0] if errors else {}
    loc = [p for p in err.get("loc", ()) if p != "body"]
    kind = err.get("type")
    if kind == "extra_forbidden":
        name = str(loc[-1]) if loc else ""
        return ApiError(422, "unknown_field", f"필드 {name}는 허용되지 않습니다.", field=name)
    if loc and loc[-1] == "contract_version":
        value = err.get("input")
        return ApiError(
            422, "unsupported_contract_version",
            f"contract_version {value}{_josa(value, '은', '는')} 지원하지 않습니다.", field="contract_version",
        )
    if kind == "json_invalid":
        return ApiError(422, "invalid_field", "본문이 올바른 JSON이 아닙니다.")
    field = ".".join(str(p) for p in loc) or None
    return ApiError(422, "invalid_field", f"필드 {field}: {err.get('msg', '올바르지 않습니다')}", field=field)


# --- 의존성 ---------------------------------------------------------------------------


def require_token(request: Request) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    expected: str = request.app.state.settings.api_token
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), expected.encode()):
        raise ApiError(401, "unauthenticated", "유효한 진단 API 토큰이 필요합니다.")


def get_conn(request: Request) -> Iterator[Connection]:
    conn = request.app.state.conn_factory()
    try:
        yield conn
    finally:
        conn.close()


def kst_day_window(now: datetime) -> tuple[str, str, str]:
    """(오늘 KST 자정 UTC, 내일 KST 자정 UTC, 다음 리셋 시각 KST 표기). DB 의 accepted_at 형식과 같은 문자열."""
    start_local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)

    def utc(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    return utc(start_local), utc(end_local), end_local.isoformat()


def _first_diff(old: Any, new: Any, path: str = "") -> str | None:
    """두 요청에서 값이 다른 첫 필드 경로 (예: target.run_id). 키 순서는 무시한다."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            diff = _first_diff(old.get(key), new.get(key), f"{path}.{key}" if path else key)
            if diff is not None:
                return diff
        return None
    return path if old != new else None


def _json(model: BaseModel, status: int = 200, **dump: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content=model.model_dump(mode="json", **dump))


# --- 라우트 ---------------------------------------------------------------------------

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/runs")
def submit_run(body: ExecutionRequest, request: Request, conn: Connection = Depends(get_conn)) -> JSONResponse:
    if body.kind != "diagnosis":
        raise ApiError(422, "invalid_field", "진단 API는 kind diagnosis만 접수합니다.", field="kind")
    settings: Settings = request.app.state.settings
    execution_id = body.execution_id

    existing = db.get_run(conn, execution_id)
    if existing is None:
        now = db.utc_now()
        start, end, resets_at = kst_day_window(datetime.fromisoformat(now))
        if db.count_runs_accepted_between(conn, start, end) >= settings.global_daily:
            raise ApiError(
                429, "daily_limit_reached",
                f"오늘 진단 API의 실행 한도({settings.global_daily}회)에 도달했습니다.",
                details={"limit": settings.global_daily, "resets_at": resets_at},
            )
        estimated = total_estimated_usd(conn)
        if estimated >= settings.budget_usd * settings.budget_stop_ratio:
            raise ApiError(
                429, "budget_exhausted",
                f"모델 호출 총액 상한에 도달했습니다 (추정 US${estimated:.2f} / 상한 US${settings.budget_usd:g}).",
                details={"estimated_usd": round(estimated, 2), "limit_usd": settings.budget_usd},
            )
        try:
            db.insert_run(conn, body, now)
        except sqlite3.IntegrityError:
            existing = db.get_run(conn, execution_id)  # 같은 ID 가 동시에 접수됨 — 아래 비교로 넘어간다
        else:
            return _json(db.run_status(conn, execution_id, after_seq=None), 202, exclude={"events"})

    if existing["request_hash"] != db.request_hash(body):
        raise ApiError(
            409, "execution_conflict",
            f"execution_id {execution_id}{_josa(execution_id, '은', '는')} 다른 내용으로 이미 접수되었습니다.",
            field=_first_diff(
                ExecutionRequest.model_validate_json(existing["request_json"]).model_dump(mode="json"),
                body.model_dump(mode="json"),
            ),
        )
    return _json(db.run_status(conn, execution_id, after_seq=None), 200, exclude={"events"})


@router.get("/runs/{execution_id}")
def get_run(
    execution_id: str, after_seq: int = Query(0, ge=0), conn: Connection = Depends(get_conn)
) -> JSONResponse:
    status = db.run_status(conn, execution_id, after_seq=after_seq)
    if status is None:
        raise ApiError(404, "not_found", f"execution {execution_id}을 찾을 수 없습니다.", field="execution_id")
    return _json(status)


@router.get("/runs/{execution_id}/artifacts/{artifact_id}")
def download_artifact(
    execution_id: str, artifact_id: str, request: Request, conn: Connection = Depends(get_conn)
) -> Response:
    if db.get_run(conn, execution_id) is None:
        raise ApiError(404, "not_found", f"execution {execution_id}을 찾을 수 없습니다.", field="execution_id")
    row = db.get_artifact(conn, execution_id, artifact_id)
    if row is None:
        raise ApiError(403, "forbidden", f"{artifact_id}은 {execution_id}의 산출물이 아닙니다.")
    store: ArtifactStore = request.app.state.store
    return Response(content=store.read(row["store_ref"]), media_type=row["content_type"])


@router.get("/capabilities")
def capabilities(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    return {
        "contract_version": CONTRACT_VERSION,
        "role": "operations.diagnose",
        "workflow_ids": sorted(settings.allowed_workflow_ids),
        "tool_contract_version": TOOL_CONTRACT_VERSION,
        "model_id": settings.model_id,
        "prompt_version": PROMPT_VERSION,
    }


@router.get("/budget")
def budget(request: Request, conn: Connection = Depends(get_conn)) -> dict:
    settings: Settings = request.app.state.settings
    start, end, _ = kst_day_window(datetime.now(UTC))
    return {
        "estimated_usd": total_estimated_usd(conn),
        "limit_usd": settings.budget_usd,
        "stop_ratio": settings.budget_stop_ratio,
        "runs_today": db.count_runs_accepted_between(conn, start, end),
    }


# --- 앱 ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    try:
        db.init_schema(conn)
    finally:
        conn.close()

    app = FastAPI(title="workflow diagnostic demo")
    app.state.settings = settings
    app.state.conn_factory = lambda: db.connect(settings.db_path)  # 요청마다 새 연결
    app.state.store = ArtifactStore(settings.artifact_dir)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _validation_error(list(exc.errors())).response()

    app.include_router(router)
    return app


app = None if os.environ.get("DIAG_SKIP_APP") == "1" else create_app()
