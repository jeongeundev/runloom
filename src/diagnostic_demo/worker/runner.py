"""진단 워커의 한 건 처리 — `accepted` 실행을 잡아 모델 루프 → 결과 조립 → 이벤트·usage 기록.

- 잠금은 `db.claim_accepted` 의 `started_at` 기록이다. 잡은 뒤 죽으면 `accepted` 로 남고 다시 잡지 않는다 —
  중앙이 시작 불명(`unknown`)으로 판정한다 (ARCHITECTURE: 재실행하지 않음).
- 실패는 종류별 code 로 `failed` 이벤트(`process_stopped: true` — 워커 프로세스 안의 루프라 종료를 확인한다).
  다른 모델로 조용히 대체하지 않는다.
- 예외 메시지에 `sk-` 접두사 토큰이 섞여도 이벤트·DB 에 남기지 않는다 (ARCHITECTURE 로그·산출물의 비밀정보).
"""

import re
import time
from collections.abc import Callable
from sqlite3 import Connection

from diagnostic_demo import db
from diagnostic_demo.artifact_store import ArtifactStore
from diagnostic_demo.pricing import estimate_usd
from diagnostic_demo.settings import Settings
from diagnostic_demo.tools.api import TOOL_CONTRACT_VERSION, Tools
from diagnostic_demo.tools.store import FixtureStore
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.assemble import ResultSchemaInvalid, assemble_result
from diagnostic_demo.worker.loop import Budget, BudgetExceeded, Usage, run_diagnosis
from diagnostic_demo.worker.model import DraftInvalid, ModelClient
from diagnostic_demo.worker.prompt import PROMPT_VERSION
from workflow.contracts.v1 import ExecutionRequest

_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]+")


def _safe(message: str) -> str:
    return _SECRET.sub("sk-***", message)[:500]


def process_one(
    conn: Connection,
    settings: Settings,
    model_factory: Callable[[], ModelClient],
    *,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], str] = db.utc_now,
) -> bool:
    """접수된 실행 하나를 끝까지 처리한다. 잡을 실행이 없으면 False."""
    row = db.claim_accepted(conn, now())
    if row is None:
        return False
    execution_id = row["execution_id"]
    request = ExecutionRequest.model_validate_json(row["request_json"])

    def emit(type_: str, data: dict) -> None:
        db.append_event(conn, execution_id, type_, data, now())

    def fail(code: str, message: str) -> None:
        emit("failed", {"code": code, "message": _safe(message), "process_stopped": True})

    store = FixtureStore(settings.fixtures_dir, settings.allowed_workflow_ids)
    recorder = ToolTraceRecorder()
    budget = Budget(
        max_calls=settings.max_calls,
        max_input_tokens=settings.max_input_tokens,
        max_output_tokens=settings.max_output_tokens,
        timeout_seconds=settings.timeout_seconds,
    )
    provenance = {
        "model_id": settings.model_id,
        "prompt_version": PROMPT_VERSION,
        "tool_contract_version": TOOL_CONTRACT_VERSION,
    }
    usage = Usage()
    try:
        draft, _ = run_diagnosis(
            execution_id, request, Tools(store, recorder), model_factory(), budget, clock, emit, usage
        )
        _, artifact_id = assemble_result(
            draft, request, recorder, store, ArtifactStore(settings.artifact_dir), conn, provenance, now=now()
        )
        emit("result_ready", {"result_artifact_id": artifact_id})
    except BudgetExceeded as exc:
        fail(exc.code, str(exc))
    except ResultSchemaInvalid as exc:
        fail("result_schema_invalid", str(exc))
    except DraftInvalid as exc:
        fail("model_output_invalid", str(exc))
    except Exception as exc:  # 모델·SDK·저장 오류. 실패로 기록하고 다음 실행으로 넘어간다
        fail("internal_error", f"{type(exc).__name__}: {exc}")
    finally:
        estimated = (
            estimate_usd(usage.input_tokens, usage.output_tokens,
                         settings.price_input_per_m, settings.price_output_per_m)
            if settings.pricing_configured else None
        )
        db.record_usage(
            conn, execution_id, settings.model_id, usage.input_tokens, usage.output_tokens, usage.calls,
            estimated, now(),
        )
    return True
