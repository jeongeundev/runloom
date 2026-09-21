"""중앙 워커 — ARCHITECTURE "실행 조정 워커". `python3 -m workflow.server.worker`.

DB 를 기준으로 상태를 전진시킨다: 연결 상태 → 서버 관찰(unknown·heartbeat) → 진단 전달 → 진단 폴링 →
A 판정 → B 결과 확인 → 범용 결과 판정 → 후속 스캔 → 실패 반영. 각 단계는 자기 트랜잭션(repo 함수)으로 끝나고,
HTTP(진단 API 호출·다운로드)는 트랜잭션 밖에서 한다. 후속 스캔이 판정들 뒤에 오므로 같은 tick 에 판정이 나면 바로 잇는다.

- 모델을 호출하지 않는다 (ADR-0004). A 완료는 `verify_diagnosis` 의 `passed` 로만 결정한다.
- 후속 착수 조건은 선행 결과 + 판정 `passed` + 결과 `outcome` ∈ 등록된 규칙 `on_outcomes` 다 (ADR-0009). 선행 Task 의
  `완료`(사람 승인)를 기다리지 않고, 규칙에 없는 결과는 착수하지 않고 이유를 남긴다. 인계 묶음은 규칙 `handoff_kinds` 로 모은다.
- 자동 재시도는 같은 execution_id 의 재전송(`DiagUnavailable`)뿐이다. 새 실행을 만드는 곳은 후속 스캔 하나이고,
  `start_key` 유일성이 재시작·이벤트 중복에도 실행을 한 번으로 묶는다.
- 시간 초과·heartbeat 상실은 `unknown`·관찰 기록으로 두고 재실행하지 않는다.
- 진단 API 는 이벤트를 push 하지 않으므로 워커가 `status()` 로 받은 이벤트를 actor `diag` 로 대신 저장한다.
  결과 안의 진단 API 쪽 산출물 ID 는 중앙에 저장한 산출물 ID 로 치환해 화면·인계가 중앙 ID 만 보게 한다.
- Task 의 저장 상태는 `domain.status.user_status` 와 같은 문구로 쓴다. `실패`(종료 확인)·`완료`(자동 판정)만
  마감(`finished_at`)하고 잠금을 해제한다. `확인 필요`·`unknown`·검토 대기는 잠금을 유지한다.
"""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from sqlite3 import Connection, Row
from typing import Any

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect, init_schema
from workflow.adapters.diag_client import (
    DiagClient,
    DiagRejected,
    DiagUnavailable,
    HttpDiagClient,
)
from workflow.adapters.errors import (
    ActiveExecutionExists,
    AdapterError,
    ArtifactMissing,
    DuplicateStartKey,
    NotFound,
)
from workflow.contracts.v1 import (
    BUILTIN_KIND_NAMES,
    ArtifactMeta,
    AttachmentRef,
    CodeChangeResult,
    DiagnosisResult,
    ExecutionEvent,
    ExecutionRequest,
    GenericResult,
    HandoffBundle,
    InputRef,
    ResultReadyData,
    RunStatus,
    SuccessorRule,
)
from workflow.domain.report_expectation import (
    ExpectedReport,
    expected_report,
    report_text_matches,
)
from workflow.domain.start_key import auto_start_key
from workflow.domain.status import user_status
from workflow.domain.succession import continue_reason, may_continue
from workflow.domain.verification import (
    Check,
    LoadedEvidence,
    TraceEntry,
    verify_diagnosis,
)
from workflow.server import views
from workflow.server.auth import utc_now
from workflow.server.settings import Settings, load_settings

log = logging.getLogger("workflow.worker")

DIAG_ACTOR = "diag"
EXPECTED_REPORT_EVIDENCE = ("expected-report", "1")
# CONTRACT 7절 정상 제출의 필수 산출물
REQUIRED_CODE_ARTIFACTS = ("diff", "test_log_before", "test_log_after", "report_output", "verification_log")
_EXIT_CODE_LINE = re.compile(r"^exit_code=(-?\d+)\s*$")


@dataclass
class TickReport:
    """워커 한 바퀴의 처리 건수 요약. 로그·테스트용이며 상태가 아니다."""

    agents_offline: int = 0
    observations: int = 0
    submitted: int = 0
    events_applied: int = 0
    verdicts: int = 0
    successors_created: int = 0
    inputs_prepared: int = 0
    results_checked: int = 0
    generic_checked: int = 0  # 사용자 정의 종류의 결과 판정 (outcome ∈ KindSpec.outcomes)
    failures_reflected: int = 0
    retries: int = 0  # DiagUnavailable — 다음 tick 에 같은 실행 ID 로 재시도

    def any(self) -> bool:
        return any(v for v in asdict(self).values())


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _age_seconds(now: str, then: str | None) -> float | None:
    return None if not then else (_parse(now) - _parse(then)).total_seconds()


def _check_dict(code: str, passed: bool, detail: str) -> dict[str, Any]:
    return asdict(Check(code, passed, detail))


def _failed_verdict(code: str, detail: str, *checks: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": "failed", "checks": [*checks, _check_dict(code, False, detail)]}


def _store(
    conn: Connection,
    store: ArtifactStore,
    *,
    execution_id: str,
    session_id: str,
    kind: str,
    name: str,
    content_type: str,
    data: bytes,
    now: str,
) -> str:
    """해시는 실제 바이트에서 계산한다. 같은 (실행, kind, 해시) 는 기존 산출물 ID 를 돌려준다 (재시작 안전)."""
    created, _ = repo.store_artifact(
        conn, store,
        execution_id=execution_id, session_id=session_id,
        meta=ArtifactMeta(
            contract_version=1, kind=kind, name=name, content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(), size=len(data),
        ),
        data=data, now=now,
    )
    return created.artifact_id


def _read_owned(conn: Connection, store: ArtifactStore, execution_id: str, artifact_id: str) -> bytes | None:
    """이 실행이 만든 산출물만 읽는다. 결과 봉투가 다른 실행의 ID 를 가리켜도 따라가지 않는다."""
    row = repo.get_artifact(conn, artifact_id)
    if row is None or row["execution_id"] != execution_id:
        return None
    try:
        return store.read(row["store_ref"])
    except FileNotFoundError:
        return None


# --- 인계 묶음 ---------------------------------------------------------------------------


def _expected_from_result(
    conn: Connection, store: ArtifactStore, execution_id: str, result: DiagnosisResult
) -> ExpectedReport | None:
    """실패 실행 기록의 `response_ref` 응답과 보고서 계약의 `supported_paths` 로 기대 보고서를 계산한다.
    diagnosis 가 없거나(정보 필요) 첨부가 깨졌으면 None — B 결과 확인에서 '기대 보고서 없음' 으로 남는다."""
    diagnosis = result.diagnosis
    if diagnosis is None:
        return None
    by_key = {(a.evidence_id, a.version): a for a in result.attachments}

    def read_json(key: tuple[str, str]) -> Any:
        ref = by_key.get(key)
        content = _read_owned(conn, store, execution_id, ref.artifact_id) if ref else None
        return None if content is None else json.loads(content)

    try:
        record = read_json((f"run-{diagnosis.failed_run_id}", "1"))
        response_ref = record["response_ref"]
        response = read_json((response_ref["evidence_id"], response_ref["version"]))
        contract = read_json((diagnosis.report_contract.evidence_id, diagnosis.report_contract.version))
        supported = contract["machine"]["supported_paths"]
        if not isinstance(response, dict) or not isinstance(supported, list):
            return None
        return expected_report(response, [p for p in supported if isinstance(p, str)])
    except (TypeError, KeyError, ValueError):
        return None


def _result_outcome(conn: Connection, store: ArtifactStore, execution: Row) -> str | None:
    """결과 산출물 JSON 의 최상위 `outcome` 문자열 — 세 결과 봉투(진단·코드 수정·범용) 모두 가진다. 읽지 못하면 None."""
    try:
        data = json.loads(repo.read_artifact(conn, store, execution["result_artifact_id"]))
    except (ValueError, NotFound, ArtifactMissing):
        return None
    outcome = data.get("outcome") if isinstance(data, dict) else None
    return outcome if isinstance(outcome, str) else None


def _diagnosis_attachments(
    conn: Connection, store: ArtifactStore, source_execution: Row, successor_task: Row, now: str
) -> list[AttachmentRef]:
    """진단 결과의 근거 원문 참조(이미 중앙 ID) + 계산한 `expected_report.json` 을 `evidence` 산출물로 저장해 덧붙인다.
    결과를 읽을 수 없으면(사람이 승인한 깨진 결과) 빈 목록 — 근거 없이 결과 참조만 넘긴다."""
    execution_id = source_execution["execution_id"]
    try:
        result = DiagnosisResult.model_validate_json(
            repo.read_artifact(conn, store, source_execution["result_artifact_id"])
        )
    except (ValidationError, ValueError, NotFound, ArtifactMissing):
        return []
    attachments = list(result.attachments)
    expected = _expected_from_result(conn, store, execution_id, result)
    if expected is not None:
        data = json.dumps(expected.to_dict(), ensure_ascii=False, indent=2).encode()
        artifact_id = _store(
            conn, store, execution_id=execution_id, session_id=successor_task["session_id"],
            kind="evidence", name="expected_report.json", content_type="application/json",
            data=data, now=now,
        )
        attachments.append(AttachmentRef(
            evidence_id=EXPECTED_REPORT_EVIDENCE[0], version=EXPECTED_REPORT_EVIDENCE[1],
            content_type="application/json", artifact_id=artifact_id,
            sha256=hashlib.sha256(data).hexdigest(),
        ))
    return attachments


def assemble_handoff(
    conn: Connection, store: ArtifactStore, source_execution: Row, successor_task: Row, rule: SuccessorRule, now: str
) -> str:
    """규칙 `handoff_kinds` 로 선행 실행의 산출물을 모아 `handoff_bundle` manifest 를 후속 세션 소유 산출물로 저장한다
    (execution_id 는 선행 실행). 근거 첨부는 진단 결과에서만 채워지고, 첨부에 이미 있는 산출물은 `inputs` 에 다시 넣지 않는다.
    이미 있으면 그 ID 를 돌려준다."""
    execution_id = source_execution["execution_id"]
    existing = [a for a in repo.artifacts_of(conn, execution_id) if a["kind"] == "handoff_bundle"]
    if existing:
        return existing[-1]["artifact_id"]

    source_kind = source_execution["kind"]
    attachments = (
        _diagnosis_attachments(conn, store, source_execution, successor_task, now)
        if source_kind == "diagnosis" else []
    )
    attached = {a.artifact_id for a in attachments}
    inputs = [
        InputRef(kind=row["kind"], artifact_id=row["artifact_id"], sha256=row["sha256"], content_type=row["content_type"])
        for row in repo.artifacts_of_kinds(conn, execution_id, rule.handoff_kinds)
        if row["artifact_id"] not in attached and row["kind"] != "handoff_bundle"
    ]
    bundle = HandoffBundle(
        contract_version=1,
        source_execution_id=execution_id,
        source_kind=source_kind,
        source_result_artifact_id=source_execution["result_artifact_id"],
        inputs=inputs,
        attachments=attachments,
    )
    return _store(
        conn, store, execution_id=execution_id, session_id=successor_task["session_id"],
        kind="handoff_bundle", name="handoff.json", content_type="application/json",
        data=bundle.model_dump_json(indent=2).encode(), now=now,
    )


# --- 워커 -------------------------------------------------------------------------------


class Worker:
    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        store: ArtifactStore,
        diag: DiagClient,
        settings: Settings,
        clock: Callable[[], str],
    ):
        self._conn_factory = conn_factory
        self._store = store
        self._diag = diag
        self._settings = settings
        self._clock = clock

    def tick(self) -> TickReport:
        """한 바퀴. 단계 순서가 곧 의존 순서다 (판정은 폴링 뒤, 후속 스캔은 판정 셋 뒤 — 같은 tick 에 판정이 나면 바로 잇는다)."""
        report = TickReport()
        conn = self._conn_factory()
        try:
            self._mark_offline(conn, report)
            self._observe(conn, report)
            self._submit_diagnoses(conn, report)
            self._poll_diagnoses(conn, report)
            self._judge_diagnoses(conn, report)
            self._check_code_results(conn, report)
            self._check_generic_results(conn, report)
            self._spawn_successors(conn, report)
            self._reflect_failures(conn, report)
        finally:
            conn.close()
        return report

    def run_forever(self, interval_seconds: float = 3.0) -> None:
        while True:
            try:
                report = self.tick()
                if report.any():
                    log.info("tick %s", asdict(report))
            except Exception:  # 한 바퀴의 예외로 워커를 죽이지 않는다. systemd 재시작보다 다음 바퀴가 빠르다
                log.exception("tick 실패 — 다음 바퀴에 재시도")
            time.sleep(interval_seconds)

    # --- Task 상태 ---------------------------------------------------------------------

    def _write_status(self, conn: Connection, task: Row, status: str, reason: str) -> bool:
        if task["finished_at"] is not None or (task["status"], task["status_reason"]) == (status, reason):
            return False
        repo.update_task_status(conn, task["task_id"], status, reason)
        return True

    def _refresh_task(self, conn: Connection, task_id: str) -> bool:
        """저장 상태를 `user_status` 와 같게 맞춘다 (웹의 `_refresh_status` 와 같은 규칙)."""
        task = repo.get_task(conn, task_id)
        if task is None or task["finished_at"] is not None:
            return False
        status = user_status(views.build_task_view(conn, task, now=self._clock(), settings=self._settings))
        return self._write_status(conn, task, status.label, status.reason)

    # --- 1. 연결 상태 -------------------------------------------------------------------

    def _mark_offline(self, conn: Connection, report: TickReport) -> None:
        now = self._clock()
        for agent in repo.list_agents(conn):
            if agent["connection_type"] != "local" or agent["connection_state"] != "online":
                continue
            if not views.agent_online(agent, now=now, settings=self._settings):
                repo.set_agent_connection(conn, agent["agent_id"], "offline", agent["last_seen_at"])
                report.agents_offline += 1

    # --- 2. 서버 관찰 -------------------------------------------------------------------

    def _observe(self, conn: Connection, report: TickReport) -> None:
        now = self._clock()
        limits = self._settings.limits
        for execution in repo.executions_by(conn, statuses=("accepted",)):
            age = _age_seconds(now, execution["accepted_at"])
            if age is not None and age >= limits.unknown_after_seconds:
                repo.mark_unknown(
                    conn, execution["execution_id"], "unknown_no_start",
                    f"accepted 후 {int(age)}초 동안 started 없음", now,
                )
                report.observations += 1
        for execution in repo.executions_by(conn, statuses=("running",)):
            if execution["assigned_connector_id"] is None:
                continue  # 진단 API 실행은 heartbeat 가 없다 — 연결 프로그램이 맡은 실행(종류 무관)만 본다
            connector = repo.get_connector(conn, execution["assigned_connector_id"])
            last_seen = connector["last_seen_at"] if connector else None
            age = _age_seconds(now, last_seen)
            if age is not None and age <= limits.heartbeat_offline_seconds:
                continue
            already = [
                o for o in repo.observations_of(conn, execution["execution_id"])
                if o["kind"] == "heartbeat_lost"
                and (last_seen is None or _parse(o["observed_at"]) >= _parse(last_seen))
            ]
            if already:
                continue  # 이 끊김은 이미 기록했다. 상태는 유지하고 재실행하지 않는다
            repo.record_observation(
                conn, execution["execution_id"], "heartbeat_lost",
                f"연결 프로그램 heartbeat 미수신 (마지막 확인 {last_seen or '없음'})", now,
            )
            report.observations += 1

    # --- 3·4. 진단 전달·폴링 ------------------------------------------------------------

    def _fail(self, conn: Connection, execution_id: str, code: str, message: str) -> None:
        try:
            repo.fail_execution(conn, execution_id, code=code, message=message, now=self._clock())
        except AdapterError:
            log.warning("실행 %s 를 failed 로 확정하지 못함 (이미 최종 상태)", execution_id)

    def _submit_diagnoses(self, conn: Connection, report: TickReport) -> None:
        for execution in repo.executions_by(conn, statuses=("queued",), kind="diagnosis"):
            execution_id = execution["execution_id"]
            request = ExecutionRequest.model_validate_json(execution["request_json"])
            try:
                status = self._diag.submit(request)
            except DiagUnavailable as exc:
                log.warning("진단 API 접수 불가 %s: %s — 다음 tick 재시도", execution_id, exc)
                report.retries += 1
                continue
            except DiagRejected as exc:
                log.warning("진단 API 접수 거부 %s: %s", execution_id, exc)
                self._fail(conn, execution_id, exc.body.code, exc.body.message)
                continue
            report.submitted += 1
            report.events_applied += self._apply_status(conn, execution, status, report)
            if repo.get_execution(conn, execution_id)["last_event_seq"] == 0:
                # 202(새 접수)·200(같은 요청 재접수) 본문에는 이벤트가 없다 (CONTRACT 1절). 2xx 는 접수됐다는 뜻이므로
                # 접수 사실을 seq 1 로 워커가 대신 기록하고, 나머지는 폴링(after_seq=1)이 가져온다
                accepted = ExecutionEvent(
                    contract_version=1, execution_id=execution_id, seq=1, occurred_at=self._clock(),
                    type="accepted", data={},
                )
                repo.append_event(conn, execution_id, accepted, actor=DIAG_ACTOR, now=self._clock())
                report.events_applied += 1
            self._refresh_task(conn, execution["task_id"])

    def _poll_diagnoses(self, conn: Connection, report: TickReport) -> None:
        for execution in repo.executions_by(conn, statuses=("accepted", "running", "unknown"), kind="diagnosis"):
            execution_id = execution["execution_id"]
            try:
                status = self._diag.status(execution_id, after_seq=execution["last_event_seq"])
            except DiagUnavailable as exc:
                log.warning("진단 API 조회 불가 %s: %s — 다음 tick 재시도", execution_id, exc)
                report.retries += 1
                continue
            except DiagRejected as exc:
                log.warning("진단 API 조회 거부 %s: %s", execution_id, exc)
                self._fail(conn, execution_id, exc.body.code, exc.body.message)
                continue
            applied = self._apply_status(conn, execution, status, report)
            report.events_applied += applied
            if applied:
                self._refresh_task(conn, execution["task_id"])

    def _apply_status(self, conn: Connection, execution: Row, status: RunStatus, report: TickReport) -> int:
        """진단 API 가 돌려준 이벤트를 seq 순으로 반영한다. 하나라도 막히면(다운로드 불가·거부·전이 오류) 거기서 멈춘다."""
        applied = 0
        for event in sorted(status.events, key=lambda e: e.seq):
            if event.seq <= repo.get_execution(conn, execution["execution_id"])["last_event_seq"]:
                continue
            if not self._apply_event(conn, execution, event, report):
                break
            applied += 1
        return applied

    def _apply_event(self, conn: Connection, execution: Row, event: ExecutionEvent, report: TickReport) -> bool:
        execution_id = execution["execution_id"]
        if event.execution_id != execution_id:
            log.warning("진단 API 이벤트의 execution_id 불일치 %s ≠ %s", event.execution_id, execution_id)
            return False
        if event.type == "result_ready":
            central_id = self._ingest_result(conn, execution, event.data.result_artifact_id, report)
            if central_id is None:
                return False
            event = event.model_copy(update={"data": ResultReadyData(result_artifact_id=central_id)})
        try:
            repo.append_event(conn, execution_id, event, actor=DIAG_ACTOR, now=self._clock())
        except AdapterError as exc:
            log.warning("진단 이벤트 반영 실패 %s seq %s: %s", execution_id, event.seq, exc)
            return False
        return True

    def _ingest_result(
        self, conn: Connection, execution: Row, diag_result_id: str, report: TickReport
    ) -> str | None:
        """결과·조회 이력·첨부를 내려받아(트랜잭션 밖) 중앙 산출물로 저장하고, 결과 안의 진단 API 쪽 ID 를 중앙 ID 로
        치환한 결과의 artifact_id 를 돌려준다. 다운로드 불가면 None (다음 tick 재시도), 첨부 상한 초과·거부면
        실행을 failed 로 확정하고 None."""
        execution_id = execution["execution_id"]
        session_id = repo.get_task(conn, execution["task_id"])["session_id"]
        now = self._clock()

        def download(artifact_id: str) -> tuple[bytes, str] | None:
            try:
                return self._diag.download(execution_id, artifact_id)
            except DiagUnavailable as exc:
                log.warning("진단 산출물 다운로드 불가 %s/%s: %s — 다음 tick 재시도", execution_id, artifact_id, exc)
                report.retries += 1
                return None
            except DiagRejected as exc:
                log.warning("진단 산출물 다운로드 거부 %s/%s: %s", execution_id, artifact_id, exc)
                self._fail(conn, execution_id, exc.body.code, exc.body.message)
                return None

        fetched = download(diag_result_id)
        if fetched is None:
            return None
        result_bytes, result_type = fetched
        try:
            data = json.loads(result_bytes)
            DiagnosisResult.model_validate(data)
        except (ValueError, ValidationError):
            # 계약이 깨진 결과도 수신 증거로 원문 그대로 보존한다. 판정 단계가 확인 필요로 둔다
            return _store(
                conn, self._store, execution_id=execution_id, session_id=session_id,
                kind="diagnosis_result", name="diagnosis_result.json",
                content_type=result_type or "application/json", data=result_bytes, now=now,
            )

        fetched = download(data["provenance"]["tool_trace_artifact_id"])
        if fetched is None:
            return None
        trace_bytes, trace_type = fetched
        downloads: list[tuple[dict[str, Any], bytes]] = []
        total = 0
        for ref in data["attachments"]:
            fetched = download(ref["artifact_id"])
            if fetched is None:
                return None
            content, _ = fetched
            total += len(content)
            downloads.append((ref, content))
        limit = self._settings.limits.attachments_max_bytes
        if total > limit:
            self._fail(
                conn, execution_id, "attachments_too_large",
                f"첨부 총 크기 {total} bytes 가 상한 {limit} bytes 를 넘습니다.",
            )
            return None

        data["provenance"]["tool_trace_artifact_id"] = _store(
            conn, self._store, execution_id=execution_id, session_id=session_id,
            kind="tool_trace", name="tool_trace.json",
            content_type=trace_type or "application/json", data=trace_bytes, now=now,
        )
        for ref, content in downloads:
            ref["artifact_id"] = _store(
                conn, self._store, execution_id=execution_id, session_id=session_id,
                kind="evidence", name=f"{ref['evidence_id']}@{ref['version']}",
                content_type=ref["content_type"], data=content, now=now,
            )
        return _store(
            conn, self._store, execution_id=execution_id, session_id=session_id,
            kind="diagnosis_result", name="diagnosis_result.json", content_type="application/json",
            data=json.dumps(data, ensure_ascii=False).encode(), now=now,
        )

    # --- 5. A 판정 --------------------------------------------------------------------

    def _judge_diagnoses(self, conn: Connection, report: TickReport) -> None:
        for execution in repo.results_awaiting_verdict(conn, "diagnosis"):
            task = repo.get_task(conn, execution["task_id"])
            verdict = self._diagnosis_verdict(conn, execution)
            checks = verdict["checks"]
            passed = verdict["outcome"] == "passed"
            if task["completion_mode"] == "auto" and passed:
                status, reason, finish = "완료", f"판정 근거: {sum(1 for c in checks if c['passed'])}/{len(checks)}", True
            elif task["completion_mode"] == "review":
                status, reason, finish = "확인 필요", "검토 대기", False
            else:
                status, reason, finish = "확인 필요", views.summarize_verdict(verdict)[1], False
            repo.record_verdict(
                conn, task_id=task["task_id"], execution_id=execution["execution_id"], verdict=verdict,
                status=status, reason=reason, finish=finish, now=self._clock(),
            )
            report.verdicts += 1

    def _diagnosis_verdict(self, conn: Connection, execution: Row) -> dict[str, Any]:
        """결과 파싱·ID 일치·조회 이력 파싱은 워커가, 근거 검증은 `verify_diagnosis` 가 한다."""
        execution_id = execution["execution_id"]
        try:
            result = DiagnosisResult.model_validate_json(repo.read_artifact(conn, self._store, execution["result_artifact_id"]))
        except (ValidationError, ValueError, NotFound, ArtifactMissing) as exc:
            return _failed_verdict("result_parsed", f"진단 결과를 계약 v1 로 읽을 수 없음: {type(exc).__name__}")
        parsed = _check_dict("result_parsed", True, f"outcome={result.outcome}")

        request = ExecutionRequest.model_validate_json(execution["request_json"])
        expected_ids = (execution_id, execution["task_id"], request.target.run_id)
        actual_ids = (result.execution_id, result.task_id, result.run_id)
        if actual_ids != expected_ids:
            return _failed_verdict(
                "result_ids_match", f"결과의 execution_id·task_id·run_id {actual_ids} ≠ 요청 {expected_ids}", parsed
            )
        ids = _check_dict("result_ids_match", True, "execution_id·task_id·run_id 가 요청과 일치")

        trace = self._load_trace(conn, execution_id, result.provenance.tool_trace_artifact_id)
        if trace is None:
            return _failed_verdict("trace_parsed", "조회 이력(tool_trace)을 읽을 수 없음", parsed, ids)

        loaded: dict[tuple[str, str], LoadedEvidence] = {}
        for ref in result.attachments:
            content = _read_owned(conn, self._store, execution_id, ref.artifact_id)
            if content is None:
                continue  # attachments_loaded 검사가 '내려받지 않음' 으로 잡는다
            loaded[(ref.evidence_id, ref.version)] = LoadedEvidence(
                evidence_id=ref.evidence_id, version=ref.version, content_type=ref.content_type,
                sha256=hashlib.sha256(content).hexdigest(), content=content,
            )
        verdict = verify_diagnosis(result, loaded, trace)
        return {"outcome": verdict.outcome, "checks": [parsed, ids, *(asdict(c) for c in verdict.checks)]}

    def _load_trace(self, conn: Connection, execution_id: str, artifact_id: str) -> list[TraceEntry] | None:
        """Step 9 `ToolTraceRecorder.to_json` 형식: {"entries": [{call_id, tool, input, ok, error, returned: [{evidence_id, version, sha256}]}]}."""
        content = _read_owned(conn, self._store, execution_id, artifact_id)
        if content is None:
            return None
        try:
            entries = json.loads(content)["entries"]
            return [
                TraceEntry(
                    call_id=str(e["call_id"]), tool=str(e["tool"]), input=dict(e.get("input") or {}),
                    ok=bool(e["ok"]),
                    returned=tuple((str(r["evidence_id"]), str(r["version"])) for r in e.get("returned") or []),
                )
                for e in entries
            ]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    # --- 6. B 결과 확인 -----------------------------------------------------------------

    def _check_code_results(self, conn: Connection, report: TickReport) -> None:
        for execution in repo.results_awaiting_verdict(conn, "code_change"):
            checks = self._code_result_checks(conn, execution)
            failed = [c for c in checks if not c["passed"]]
            required = next((c for c in failed if c["code"] == "required_artifacts"), None)
            reason = "검토 대기" if not failed else (required["detail"] if required else " · ".join(c["detail"] for c in failed))
            repo.record_verdict(
                conn, task_id=execution["task_id"], execution_id=execution["execution_id"],
                verdict={"outcome": "passed" if not failed else "failed", "checks": checks},
                status="확인 필요", reason=reason, finish=False, now=self._clock(),
            )
            report.results_checked += 1

    def _code_result_checks(self, conn: Connection, execution: Row) -> list[dict[str, Any]]:
        """CONTRACT 7절 정상 제출 조건. 어느 쪽이든 사람 검토 전 완료하지 않으므로 결과는 이유 문구와 판정 기록에만 쓴다."""
        execution_id = execution["execution_id"]
        latest: dict[str, Row] = {a["kind"]: a for a in repo.artifacts_of(conn, execution_id)}

        def text_of(kind: str) -> str | None:
            artifact = latest.get(kind)
            content = _read_owned(conn, self._store, execution_id, artifact["artifact_id"]) if artifact else None
            return None if content is None else content.decode("utf-8", errors="replace")

        try:
            result = CodeChangeResult.model_validate_json(repo.read_artifact(conn, self._store, execution["result_artifact_id"]))
        except (ValidationError, ValueError, NotFound, ArtifactMissing) as exc:
            return [_check_dict("result_parsed", False, f"코드 수정 결과를 계약 v1 로 읽을 수 없음: {type(exc).__name__}")]
        checks = [_check_dict("result_parsed", True, f"outcome={result.outcome}")]

        missing = [k for k in REQUIRED_CODE_ARTIFACTS if k not in latest]
        checks.append(_check_dict(
            "required_artifacts", not missing,
            f"필수 산출물 누락: {', '.join(missing)}" if missing else "diff·테스트 전후·보고서·검증 로그 있음",
        ))

        before = text_of("test_log_before")
        match = _EXIT_CODE_LINE.match(before.splitlines()[0]) if before and before.splitlines() else None
        if match is None:
            checks.append(_check_dict("test_before_failed", False, "test_log_before 첫 줄에 exit_code= 가 없음"))
        elif int(match.group(1)) == 0:
            checks.append(_check_dict("test_before_failed", False, "수정 전 테스트가 실패하지 않음 (exit_code=0)"))
        else:
            checks.append(_check_dict("test_before_failed", True, f"수정 전 exit_code={match.group(1)}"))

        verification = result.verification
        if verification is None:
            checks.append(_check_dict("verification_passed", False, "verification 없음 (보류 제출)"))
        elif verification.exit_code != 0:
            checks.append(_check_dict("verification_passed", False, f"검증 프로필 {verification.profile_id} exit {verification.exit_code}"))
        else:
            checks.append(_check_dict("verification_passed", True, f"{verification.profile_id} exit 0 @ {verification.result_commit[:7]}"))

        expected = self._expected_report(conn, execution)
        report_text = text_of("report_output")
        if expected is None:
            checks.append(_check_dict("report_matches", False, "기대 보고서 없음 (인계 묶음에 expected-report 없음)"))
        elif report_text is None:
            checks.append(_check_dict("report_matches", False, "report_output 없음"))
        elif report_text_matches(report_text, expected):
            checks.append(_check_dict(
                "report_matches", True,
                f"{expected.report_date} · 합계 {expected.total_completed}·{expected.total_pending} 일치",
            ))
        else:
            checks.append(_check_dict("report_matches", False, "보고서 수치 불일치"))
        return checks

    def _expected_report(self, conn: Connection, execution: Row) -> ExpectedReport | None:
        """B 입력의 `handoff_bundle` 에서 `expected-report@1` 첨부를 읽는다."""
        request = ExecutionRequest.model_validate_json(execution["request_json"])
        for input_id in request.input_artifact_ids:
            row = repo.get_artifact(conn, input_id)
            if row is None or row["kind"] != "handoff_bundle":
                continue
            try:
                bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, self._store, input_id))
                ref = next(a for a in bundle.attachments if (a.evidence_id, a.version) == EXPECTED_REPORT_EVIDENCE)
                content = _read_owned(conn, self._store, bundle.source_execution_id, ref.artifact_id)
                return None if content is None else ExpectedReport.from_dict(json.loads(content))
            except (ValidationError, ValueError, KeyError, TypeError, StopIteration, NotFound, ArtifactMissing):
                continue
        return None

    # --- 7. 범용 결과 판정 (사용자 정의 종류) ------------------------------------------------

    def _check_generic_results(self, conn: Connection, report: TickReport) -> None:
        """`result_ready` 이고 판정 없는 실행 중 내장이 아닌 종류. `GenericResult` 봉투를 검사해 판정을 기록한다.
        중앙은 봉투와 `outcome ∈ kind_spec.outcomes` 만 보고, 완료는 어느 쪽이든 사람이 한다 (ADR-0009)."""
        for execution in repo.results_awaiting_verdict(conn):
            if execution["kind"] in BUILTIN_KIND_NAMES:
                continue
            checks = self._generic_result_checks(conn, execution)
            failed = [c for c in checks if not c["passed"]]
            reason = "검토 대기" if not failed else " · ".join(c["detail"] for c in failed)
            repo.record_verdict(
                conn, task_id=execution["task_id"], execution_id=execution["execution_id"],
                verdict={"outcome": "passed" if not failed else "failed", "checks": checks},
                status="확인 필요", reason=reason, finish=False, now=self._clock(),
            )
            report.generic_checked += 1

    def _generic_result_checks(self, conn: Connection, execution: Row) -> list[dict[str, Any]]:
        """envelope_valid(파싱) → ids_match(execution_id·task_id·kind) → outcome_in_spec(요청에 고정된 kind_spec.outcomes)."""
        try:
            result = GenericResult.model_validate_json(
                repo.read_artifact(conn, self._store, execution["result_artifact_id"])
            )
        except (ValidationError, ValueError, NotFound, ArtifactMissing) as exc:
            return [_check_dict("envelope_valid", False, f"결과를 계약 v1 GenericResult 로 읽을 수 없음: {type(exc).__name__}")]
        checks = [_check_dict("envelope_valid", True, f"outcome={result.outcome}")]

        expected_ids = (execution["execution_id"], execution["task_id"], execution["kind"])
        actual_ids = (result.execution_id, result.task_id, result.kind)
        if actual_ids != expected_ids:
            checks.append(_check_dict("ids_match", False, f"결과의 execution_id·task_id·kind {actual_ids} ≠ 요청 {expected_ids}"))
            return checks
        checks.append(_check_dict("ids_match", True, "execution_id·task_id·kind 가 요청과 일치"))

        spec = ExecutionRequest.model_validate_json(execution["request_json"]).kind_spec
        if spec is None:
            checks.append(_check_dict("outcome_in_spec", False, "요청에 kind_spec 이 없어 허용 outcome 을 알 수 없음"))
        elif result.outcome not in spec.outcomes:
            checks.append(_check_dict(
                "outcome_in_spec", False, f"허용되지 않은 outcome {result.outcome} — 허용: {', '.join(spec.outcomes)}",
            ))
        else:
            checks.append(_check_dict("outcome_in_spec", True, f"outcome {result.outcome} 이 허용 목록 안"))
        return checks

    # --- 8. 후속 스캔 -------------------------------------------------------------------

    def _spawn_successors(self, conn: Connection, report: TickReport) -> None:
        """선행 실행이 `result_ready` + 판정 `passed` + 결과 `outcome` ∈ 규칙 `on_outcomes` 면 규칙 `handoff_kinds` 로 입력을
        고정하고 실행을 만든다 (ADR-0009 (3)). 선행 Task 의 `완료` 를 기다리지 않는다. 규칙·outcome 이 맞지 않으면 이유를 남긴다."""
        now = self._clock()
        for task in repo.tasks_with_ready_predecessor(conn):
            task_id = task["task_id"]
            if repo.active_execution(conn, task_id) is not None:
                continue
            selection = repo.get_selection(conn, task_id)
            if selection is None or selection.status != "selected":
                continue  # 확인 필요(후보 0·N) — 사용자 선택을 기다린다
            source = repo.predecessor_ready_execution(conn, task["predecessor_task_id"])
            if source is None:
                self._refresh_task(conn, task_id)
                continue
            rule = repo.get_rule(conn, task["session_id"], source["kind"], task["kind"])
            if rule is None:
                self._write_status(
                    conn, task, "대기",
                    f"후속 규칙 없음: {source['kind']} → {task['kind']} — 규칙을 등록하거나 직접 실행",
                )
                continue
            verdict = repo.get_verdict(conn, source["execution_id"])
            if verdict is None or json.loads(verdict["verdict_json"]).get("outcome") != "passed":
                self._refresh_task(conn, task_id)  # 판정 실패·보류인 선행은 사람이 본다
                continue
            outcome = _result_outcome(conn, self._store, source)
            if outcome is None:
                self._refresh_task(conn, task_id)
                continue
            if not may_continue(rule, outcome):
                self._write_status(conn, task, "확인 필요", continue_reason(rule, outcome))
                continue
            agent = repo.get_agent(conn, selection.selected_agent_id)
            if agent is None or agent["connection_type"] == "api":
                # 진단 API 후속은 이번에도 웹 경로에서만 시작한다 (상한·usage 기록이 거기 있다 — ADR-0009 한계)
                self._refresh_task(conn, task_id)
                continue
            if task["run_mode"] == "manual":
                before = repo.artifacts_of(conn, source["execution_id"])
                assemble_handoff(conn, self._store, source, task, rule, now)
                if len(repo.artifacts_of(conn, source["execution_id"])) > len(before):
                    report.inputs_prepared += 1
                self._refresh_task(conn, task_id)  # → 실행 가능. 사용자 조작 전에는 실행을 만들지 않는다
                continue
            if not views.agent_online(agent, now=now, settings=self._settings):
                self._refresh_task(conn, task_id)  # → 대기 · 연결 끊김, 마지막 확인 …
                continue
            spec = repo.get_kind(conn, task["session_id"], task["kind"])
            if spec is None:
                log.warning("후속 업무 %s 의 종류 %s 가 등록부에 없음", task_id, task["kind"])
                self._refresh_task(conn, task_id)
                continue
            bundle_id = assemble_handoff(conn, self._store, source, task, rule, now)
            try:
                request = ExecutionRequest.model_validate({
                    "contract_version": 1,
                    "execution_id": f"exec-{secrets.token_hex(8)}",
                    "task_id": task_id,
                    "kind": task["kind"],
                    "agent_id": agent["agent_id"],
                    "task_revision": task["revision"],
                    "request": task["request"],
                    "input_artifact_ids": [bundle_id],
                    "target": json.loads(task["target_json"]),
                    "kind_spec": spec.model_dump(),
                })
            except ValidationError as exc:
                log.warning("후속 업무 %s 의 실행 요청을 만들 수 없음: %s", task_id, exc)
                self._refresh_task(conn, task_id)
                continue
            attempts = repo.list_executions(conn, task_id)
            try:
                repo.create_execution(
                    conn,
                    execution_id=request.execution_id, task_id=task_id,
                    attempt_no=attempts[-1]["attempt_no"] + 1 if attempts else 1,
                    start_key=auto_start_key(task_id, task["revision"]),
                    agent_id=agent["agent_id"], kind=task["kind"], request=request,
                    assigned_connector_id=agent["connector_id"],
                    predecessor_execution_id=source["execution_id"],
                    now=now,
                )
            except (DuplicateStartKey, ActiveExecutionExists) as exc:
                log.info("후속 업무 %s 는 이미 자동 실행을 만들었음: %s", task_id, exc)
                self._refresh_task(conn, task_id)
                continue
            report.successors_created += 1
            self._refresh_task(conn, task_id)  # → 실행 요청됨 · 접수 대기

    # --- 9. 실패 반영 -------------------------------------------------------------------

    def _reflect_failures(self, conn: Connection, report: TickReport) -> None:
        for execution in repo.executions_by(conn, statuses=("failed", "unknown")):
            task = repo.get_task(conn, execution["task_id"])
            if task is None or task["finished_at"] is not None:
                continue
            if execution["status"] == "failed" and execution["process_stopped"]:
                # 프로세스 종료를 확인한 실패 — 마감(실패)하고 잠금을 푼다. 재실행은 새 업무·명시적 재시도만
                repo.finish_task(
                    conn, task_id=task["task_id"], execution_id=execution["execution_id"], status="실패",
                    reason=f"{execution['failed_code']} · {execution['failed_message']}", now=self._clock(),
                )
                report.failures_reflected += 1
                continue
            reason = (
                "종료 미확인 — 재실행하지 않음" if execution["status"] == "failed"
                else "시작 여부 불명 — 재실행하지 않음"
            )
            if self._write_status(conn, task, "확인 필요", reason):
                report.failures_reflected += 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
    finally:
        conn.close()
    worker = Worker(
        conn_factory=lambda: connect(settings.db_path),
        store=ArtifactStore(settings.artifact_dir),
        diag=HttpDiagClient(settings.diag_api_url, settings.diag_api_token),
        settings=settings,
        clock=utc_now,
    )
    log.info("중앙 워커 시작 — 복구 스캔 %s", asdict(worker.tick()))
    worker.run_forever(3.0)


if __name__ == "__main__":
    main()
