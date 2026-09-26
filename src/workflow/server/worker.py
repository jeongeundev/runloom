"""중앙 워커 — ARCHITECTURE "실행 조정 워커". `python3 -m workflow.server.worker`.

DB 를 기준으로 상태를 전진시킨다: GitHub 수집 → 연결 상태 → 서버 관찰(unknown·heartbeat) → 진단 전달 → 진단 폴링 →
A 판정 → 코드 수정 결과 확인 → 커밋 검토 결과 확인 → 범용 결과 판정 → 업무 순환 → 후속 스캔 → 실패 반영 → callback 전달 →
원본 이슈 반영. 각 단계는 자기 트랜잭션(repo 함수)으로 끝나고,
HTTP(진단 API 호출·다운로드·callback POST)는 트랜잭션 밖에서 한다. 후속 스캔이 판정들 뒤에 오므로 같은 tick 에 판정이 나면 바로 잇는다.

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
- GitHub 수집(ADR-0014)은 GitHub 클라이언트가 있을 때만(토큰이 설정됐을 때) 켜진 소스마다 `GITHUB_SYNC_INTERVAL_SECONDS`
  간격으로 `github_sync.sync_source` 를 부른다. rate limit 이면 그 소스는 알려준 시간만큼 쉰다. 수집은 Task 만 만들고
  착수하지 않는다.
- GitHub 업무 순환(ADR-0014 결정 5·6·10)은 `domain.execution_policy` 의 `cycle` 종류(`bug_fix`·`code_review`)만 다룬다.
  한 결과마다 판정 → `decide_followup` → 저장(후속 Task·사람 요청, repo 트랜잭션) → 준비 판정 → 착수 순이고, 그 뒤
  아직 실행이 없는 Task 를 준비 판정으로 착수한다. 이미 한 일은 DB(`start_key`·`followup_links`·사람 요청 `cause_key`)
  에서 다시 계산하므로 재시작·같은 결과 재처리에도 한 번만 일어난다. 트랜잭션 중 HTTP·도구를 기다리지 않고 GitHub 에
  쓰지 않는다(원본 반영은 tick 마지막 단계). 이 종류들은 기존 후속 스캔(`_spawn_successors`)을 타지 않는다.
  운영자가 정해야 풀리는 대기(담당자 여럿·위임 밖·입력 없음)는 revision 마다 사람 요청 한 건으로 남긴다. 운영자 응답이
  결과를 기다리던 시도 뒤의 revision 을 만들면 그 시도를 해제하고 새 revision 의 `auto_start_key` 로 다시 착수한다 —
  응답만으로는 실행하지 않고 언제나 준비 판정을 다시 거친다(step 11).
- callback 은 체인이 사람 차례(`chain_settled`)가 되면 1회 보낸다 (ADR-0010). tick 의 마지막 단계라 A 판정 → B 착수가
  같은 tick 에 일어나면 그 사이에 보내지 않는다. 실패는 attempts·next_at 으로 물러나 재시도하고 5회 뒤 멈춘다.
- 원본 이슈 반영(ADR-0014 결정 8)은 GitHub 클라이언트가 있을 때 맨 끝에 `github_delivery` 로 Task 당 댓글 하나를
  만들거나 고친다. 워커 판정·착수와 외부 반영은 분리돼 있다 — 반영 실패·불확실은 `source_deliveries` 에만 남는다.
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
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row
from typing import Any

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.callback_client import CallbackClient, CallbackFailed, HttpCallbackClient
from workflow.adapters.db import connect, init_schema
from workflow.adapters.diag_client import (
    DiagClient,
    DiagRejected,
    DiagUnavailable,
    HttpDiagClient,
)
from workflow.adapters.github_client import GitHubClient, HttpGitHubClient
from workflow.adapters.errors import (
    ActiveExecutionExists,
    AdapterError,
    ArtifactMissing,
    DuplicateStartKey,
    NotFound,
    TaskClosed,
)
from workflow.contracts.v1 import (
    ArtifactMeta,
    AttachmentRef,
    CallbackGate,
    CallbackTask,
    ChainCallback,
    CodeChangeResult,
    CodeReviewResult,
    CommitReviewTarget,
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
from workflow.domain.callback_policy import host_allowed
from workflow.domain.completion import criteria_template, merge_criteria
from workflow.domain.execution_policy import ExecutionPolicy, policy_for
from workflow.domain.report_expectation import (
    ExpectedReport,
    expected_report,
    report_text_matches,
)
from workflow.domain.settlement import NodeState, chain_settled
from workflow.domain.start_key import auto_start_key
from workflow.domain.status import UserStatus, user_status
from workflow.domain.succession import continue_reason, may_continue
from workflow.domain.task_followup import FollowupContext, FollowupDecision, FollowupTaskSpec, ReviewFacts, decide_followup
from workflow.domain.task_readiness import TaskReadiness
from workflow.domain.verification import (
    Check,
    LoadedEvidence,
    TraceEntry,
    verify_diagnosis,
)
from workflow.server import github_delivery, github_sync, task_cycle, views
from workflow.server.auth import utc_now
from workflow.server.settings import Settings, load_settings

log = logging.getLogger("workflow.worker")

DIAG_ACTOR = "diag"
EXPECTED_REPORT_EVIDENCE = ("expected-report", "1")
_EXIT_CODE_LINE = re.compile(r"^exit_code=(-?\d+)\s*$")
# callback 재시도 (ADR-0010): n 회째 실패 뒤 30·2^(n-1) 초 (30·60·120·240), 5회 실패 후 중단
CALLBACK_MAX_ATTEMPTS = 5
CALLBACK_BACKOFF_SECONDS = 30
# GitHub 목록 폴링 간격(소스마다). 변화 없으면 ETag 304 라 primary 한도를 쓰지 않는다
GITHUB_SYNC_INTERVAL_SECONDS = 60


@dataclass
class TickReport:
    """워커 한 바퀴의 처리 건수 요약. 로그·테스트용이며 상태가 아니다."""

    sources_synced: int = 0  # 이번 바퀴에 수집한 GitHub 소스
    issues_created: int = 0  # 수집이 새로 만든 Task
    sync_errors: int = 0  # GitHub 호출 실패(다음 간격에 같은 커서로 다시)
    agents_offline: int = 0
    observations: int = 0
    submitted: int = 0
    events_applied: int = 0
    verdicts: int = 0
    successors_created: int = 0
    inputs_prepared: int = 0
    results_checked: int = 0
    generic_checked: int = 0  # 사용자 정의 종류의 결과 판정 (outcome ∈ KindSpec.outcomes)
    reviews_checked: int = 0  # 커밋 검토 결과 판정 (CodeReviewResult)
    tasks_started: int = 0  # 준비 판정을 통과해 착수한 업무 순환 Task
    tasks_resumed: int = 0  # 사람 응답 뒤 준비 판정을 다시 통과해 이어 착수한 Task
    followup_tasks_created: int = 0  # 결과에서 새로 만든 후속 Task
    followups_started: int = 0  # 결과에서 이어진 실행(검토 연결·재작업)
    human_requests: int = 0  # 새로 만든 사람 요청
    failures_reflected: int = 0
    retries: int = 0  # DiagUnavailable — 다음 tick 에 같은 실행 ID 로 재시도
    callbacks_sent: int = 0  # 사람 차례가 된 체인에 ChainCallback 전송 (체인당 1회)
    callbacks_failed: int = 0  # 전송 실패·허용 목록 밖 — attempts 로 기록
    deliveries_queued: int = 0  # 원본 이슈 댓글 본문의 새 revision
    deliveries_sent: int = 0  # 댓글 생성·수정 성공(응답을 잃은 POST 를 marker 로 찾은 것 포함)
    deliveries_failed: int = 0  # 반영 실패(403·404·삭제된 댓글) — Task 상태와 따로

    def any(self) -> bool:
        return any(v for v in asdict(self).values())


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _age_seconds(now: str, then: str | None) -> float | None:
    return None if not then else (_parse(now) - _parse(then)).total_seconds()


def _plus_seconds(now: str, seconds: int) -> str:
    """`now` 뒤 `seconds` 초 — `utc_now` 와 같은 표기(UTC, 마이크로초, `Z`)라 repo 의 문자열 비교와 맞는다."""
    moved = (_parse(now) + timedelta(seconds=seconds)).astimezone(UTC)
    return moved.isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def _result_envelope(conn: Connection, store: ArtifactStore, execution: Row) -> tuple[str | None, str | None]:
    """결과 봉투(진단·코드 수정·범용)의 최상위 `outcome`·`summary` 문자열 — callback 본문용.
    이 실행의 산출물이 아니거나 읽지 못하면 (None, None). `_result_outcome`(후속 착수 판단)과 별개다."""
    content = _read_owned(conn, store, execution["execution_id"], execution["result_artifact_id"])
    try:
        data = json.loads(content) if content is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, None
    outcome, summary = data.get("outcome"), data.get("summary")
    return (outcome if isinstance(outcome, str) else None, summary if isinstance(summary, str) else None)


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


def _operator_closed(task: Row) -> bool:
    """운영자 종료·실패로 마감된 Task — 새 후속을 만들지 않는다."""
    return task["finished_at"] is not None and task["status"] == "실패"


# 후속을 보류한 결정이 Task 에 남길 상태(`FollowupDecision.hold_code` → 상태 문구). task_closed 는 이미 마감이라 없다
_HOLD_LABELS = {"source_closed": "대기", "stale_review": "확인 필요"}


# --- 워커 -------------------------------------------------------------------------------


class Worker:
    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        store: ArtifactStore,
        diag: DiagClient,
        callbacks: CallbackClient,
        settings: Settings,
        clock: Callable[[], str],
        github: GitHubClient | None = None,
    ):
        self._github = github
        self._github_next_at: dict[str, str] = {}  # source_id → 다음 수집 시각(메모리 — 재시작하면 바로 한 번 부른다)
        self._manual_task_id: str | None = None  # 직접 실행(`start_manually`) 중인 Task — 이 Task 만 `manual_mode` 를 뺀다
        self._conn_factory = conn_factory
        self._store = store
        self._diag = diag
        self._callbacks = callbacks
        self._settings = settings
        self._clock = clock

    def tick(self) -> TickReport:
        """한 바퀴. 단계 순서가 곧 의존 순서다 (판정은 폴링 뒤, 후속 스캔은 판정 셋 뒤 — 같은 tick 에 판정이 나면 바로 잇는다.
        callback 전달은 맨 뒤 — 후속 착수·실패 반영까지 끝난 상태로 사람 차례를 판정한다)."""
        report = TickReport()
        conn = self._conn_factory()
        try:
            self._sync_github(conn, report)
            self._mark_offline(conn, report)
            self._observe(conn, report)
            self._submit_diagnoses(conn, report)
            self._poll_diagnoses(conn, report)
            self._judge_diagnoses(conn, report)
            self._check_code_results(conn, report)
            self._check_review_results(conn, report)
            self._check_generic_results(conn, report)
            self._advance_cycle(conn, report)
            self._spawn_successors(conn, report)
            self._reflect_failures(conn, report)
            self._deliver_callbacks(conn, report)
            self._deliver_github(conn, report)
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

    # --- GitHub 수집 ---------------------------------------------------------------------

    def _sync_github(self, conn: Connection, report: TickReport) -> None:
        if self._github is None:
            return
        now = self._clock()
        for session_id in repo.github_source_sessions(conn):
            for config in repo.list_github_sources(conn, session_id):
                due = self._github_next_at.get(config.source_id)
                if not config.enabled or (due is not None and _parse(now) < _parse(due)):
                    continue
                result = github_sync.sync_source(conn, self._github, config.source_id, now)
                report.sources_synced += 1
                report.issues_created += len(result.created)
                if result.error is not None:
                    report.sync_errors += 1
                    log.warning("GitHub 수집 실패 %s: %s", config.source_id, result.error)
                if result.merge_error is not None:
                    log.warning("GitHub 병합 PR 조회 실패 %s: %s", config.source_id, result.merge_error)
                wait = max(GITHUB_SYNC_INTERVAL_SECONDS, result.retry_after_seconds or 0)
                self._github_next_at[config.source_id] = _plus_seconds(now, wait)

    # --- Task 상태 ---------------------------------------------------------------------

    def _write_status(self, conn: Connection, task: Row, status: str, reason: str) -> bool:
        if task["finished_at"] is not None or (task["status"], task["status_reason"]) == (status, reason):
            return False
        repo.update_task_status(conn, task["task_id"], status, reason, now=self._clock())
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
        """`CodeChangeResult` 를 내는 종류(데모 `code_change`·일반 `bug_fix`). 검사 항목은 실행 정책 표가 정한다."""
        for execution in repo.results_awaiting_verdict(conn):
            policy = policy_for(execution["kind"])
            if policy.verifier not in ("report_code_change", "code_change"):
                continue
            checks = self._code_result_checks(conn, execution, policy)
            failed = [c for c in checks if not c["passed"]]
            required = next((c for c in failed if c["code"] == "required_artifacts"), None)
            reason = "검토 대기" if not failed else (required["detail"] if required else " · ".join(c["detail"] for c in failed))
            repo.record_verdict(
                conn, task_id=execution["task_id"], execution_id=execution["execution_id"],
                verdict={"outcome": "passed" if not failed else "failed", "checks": checks},
                status="확인 필요", reason=reason, finish=False, now=self._clock(),
            )
            report.results_checked += 1

    def _code_result_checks(self, conn: Connection, execution: Row, policy: ExecutionPolicy) -> list[dict[str, Any]]:
        """CONTRACT 7절·13.5 정상 제출 조건. 데모(`report_code_change`)는 기대 보고서와 대조하고, 일반 버그 수정
        (`code_change`)은 보고서 대신 결과의 요청 ID·기준 커밋이 요청과 같은지 본다. 어느 쪽이든 판정만으로 완료하지 않는다."""
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
        if policy.verifier == "code_change":
            checks.extend(self._request_match_checks(execution, result))
            if result.outcome == "needs_information":
                # 수정·검증 없이 정보를 요청한 정상 제출 — 산출물 검사를 하지 않고 사람 요청(`fix_needs_information`)으로 잇는다
                return checks

        missing = [k for k in policy.required_artifacts if k not in latest]
        checks.append(_check_dict(
            "required_artifacts", not missing,
            f"필수 산출물 누락: {', '.join(missing)}" if missing else f"필수 산출물 있음: {', '.join(policy.required_artifacts)}",
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

        if policy.verifier != "report_code_change":
            return checks
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

    def _request_match_checks(self, execution: Row, result: CodeChangeResult) -> list[dict[str, Any]]:
        """결과가 이 실행·Task 의 것이고 요청이 고정한 기준 커밋에서 시작했는가 (ARCHITECTURE 데모/일반 비교표)."""
        expected_ids = (execution["execution_id"], execution["task_id"])
        actual_ids = (result.execution_id, result.task_id)
        base = ExecutionRequest.model_validate_json(execution["request_json"]).target.base_commit
        return [
            _check_dict("result_ids_match", actual_ids == expected_ids,
                        "execution_id·task_id 가 요청과 일치" if actual_ids == expected_ids
                        else f"결과의 execution_id·task_id {actual_ids} ≠ 요청 {expected_ids}"),
            _check_dict("commit_matches", result.base_commit == base,
                        f"기준 커밋 {base[:7]} 에서 시작" if result.base_commit == base
                        else f"결과의 기준 커밋 {result.base_commit[:7]} ≠ 요청 {base[:7]}"),
        ]

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

    # --- 6b. 커밋 검토 결과 확인 ------------------------------------------------------------

    def _check_review_results(self, conn: Connection, report: TickReport) -> None:
        """`CodeReviewResult` 를 내는 종류. 판정은 결과가 요청의 검토 대상(수정 실행·결과 커밋)을 가리키는지만 본다 —
        최신 수정 결과인지(`stale_review`)와 다음 할 일은 업무 순환 단계의 `decide_followup` 이 정한다."""
        for execution in repo.results_awaiting_verdict(conn):
            if policy_for(execution["kind"]).verifier != "commit_review":
                continue
            checks = self._review_result_checks(conn, execution)
            failed = [c for c in checks if not c["passed"]]
            repo.record_verdict(
                conn, task_id=execution["task_id"], execution_id=execution["execution_id"],
                verdict={"outcome": "passed" if not failed else "failed", "checks": checks},
                status="확인 필요", reason="판정 통과 — 후속 결정" if not failed else " · ".join(c["detail"] for c in failed),
                finish=False, now=self._clock(),
            )
            report.reviews_checked += 1

    def _review_result_checks(self, conn: Connection, execution: Row) -> list[dict[str, Any]]:
        try:
            result = CodeReviewResult.model_validate_json(
                repo.read_artifact(conn, self._store, execution["result_artifact_id"])
            )
        except (ValidationError, ValueError, NotFound, ArtifactMissing) as exc:
            return [_check_dict("result_parsed", False, f"검토 결과를 계약 v1 로 읽을 수 없음: {type(exc).__name__}")]
        target = ExecutionRequest.model_validate_json(execution["request_json"]).target
        assert isinstance(target, CommitReviewTarget)  # 계약: code_review 요청의 target
        expected_ids = (execution["execution_id"], execution["task_id"])
        actual_ids = (result.execution_id, result.task_id)
        return [
            _check_dict("result_parsed", True, f"outcome={result.outcome}"),
            _check_dict("result_ids_match", actual_ids == expected_ids,
                        "execution_id·task_id 가 요청과 일치" if actual_ids == expected_ids
                        else f"결과의 execution_id·task_id {actual_ids} ≠ 요청 {expected_ids}"),
            _check_dict("source_matches", result.source_execution_id == target.source_execution_id,
                        f"검토 대상 수정 실행 {target.source_execution_id}"
                        if result.source_execution_id == target.source_execution_id
                        else f"결과의 수정 실행 {result.source_execution_id} ≠ 요청 {target.source_execution_id}"),
            _check_dict("commit_matches", result.reviewed_commit == target.result_commit,
                        f"검토 커밋 {target.result_commit[:7]}" if result.reviewed_commit == target.result_commit
                        else f"검토한 커밋 {result.reviewed_commit[:7]} ≠ 요청 {target.result_commit[:7]}"),
        ]

    # --- 7. 범용 결과 판정 (사용자 정의 종류) ------------------------------------------------

    def _check_generic_results(self, conn: Connection, report: TickReport) -> None:
        """`result_ready` 이고 판정 없는 실행 중 내장이 아닌 종류. `GenericResult` 봉투를 검사해 판정을 기록한다.
        중앙은 봉투와 `outcome ∈ kind_spec.outcomes` 만 보고, 완료는 어느 쪽이든 사람이 한다 (ADR-0009)."""
        for execution in repo.results_awaiting_verdict(conn):
            if policy_for(execution["kind"]).verifier != "generic":
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

    # --- 7b. GitHub 업무 순환 (ADR-0014) ---------------------------------------------------

    def start_manually(self, conn: Connection, task_id: str) -> bool:
        """직접 실행(웹의 `실행`) — 이 Task 와 그 선행·후속의 업무 순환 단계를 tick 과 같은 규칙으로 돌리되 이 Task 의
        준비 판정에서만 `manual_mode` 를 뺀다. start_key·대상·입력은 자동 착수와 같아 같은 원인은 한 번만 실행된다.
        반환은 이 Task 에 새 실행이 생겼는가."""
        task = repo.get_task(conn, task_id)
        before = {e["execution_id"] for e in repo.list_executions(conn, task_id)}
        scope = {task_id, *(t["task_id"] for t in repo.successors_of(conn, task_id))}
        if task["predecessor_task_id"]:
            scope.add(task["predecessor_task_id"])
        self._manual_task_id = task_id
        try:
            self._cycle_followups(conn, TickReport(), only=scope)
            self._start_ready_tasks(conn, TickReport(), only={task_id})
        finally:
            self._manual_task_id = None
        return any(e["execution_id"] not in before for e in repo.list_executions(conn, task_id))

    def _manual_override(self, task: Row) -> dict[str, Any]:
        return {"run_mode": "auto"} if task["task_id"] == self._manual_task_id else {}

    def _advance_cycle(self, conn: Connection, report: TickReport) -> None:
        """판정된 결과 → `decide_followup` → 저장(후속 Task·사람 요청) → 준비 판정 → 착수, 그다음 아직 실행이 없는 Task 의
        준비 판정 → 착수. 결과에서 잇는 일을 먼저 해 재작업이 같은 저장소의 새 업무보다 앞선다."""
        self._cycle_followups(conn, report)
        self._start_ready_tasks(conn, report)

    def _cycle_followups(self, conn: Connection, report: TickReport, only: set[str] | None = None) -> None:
        for row in repo.executions_by(conn, statuses=("result_ready",)):
            if not policy_for(row["kind"]).cycle or (only is not None and row["task_id"] not in only):
                continue
            execution = repo.get_execution(conn, row["execution_id"])
            if execution["released_at"] is not None:
                continue  # 이번 바퀴의 앞선 결정이 해제한 시도(재작업·검토 재연결)
            verdict_row = repo.get_verdict(conn, execution["execution_id"])
            task = repo.get_task(conn, execution["task_id"])
            if verdict_row is None or task["finished_at"] is not None:
                continue
            context = self._followup_context(conn, execution, task, json.loads(verdict_row["verdict_json"])["outcome"])
            self._apply_followup(conn, execution, task, context, decide_followup(context), report)

    def _followup_context(self, conn: Connection, execution: Row, task: Row, verdict: str) -> FollowupContext:
        """결정 재료를 DB 에서 다시 계산한다 — 기존 후속은 명시적 원인 참조(선행 Task·원인 실행)로만 찾고, 이미 처리한 원인은
        만들어진 실행의 `start_key` 와 사람 요청의 `cause_key` 다."""
        request = ExecutionRequest.model_validate_json(execution["request_json"])
        rules = [rule for _, rule in repo.list_rules(conn, task["session_id"])]
        issue, _ = task_cycle.origin_source(conn, task)
        common = {
            "session_id": task["session_id"], "task_id": task["task_id"], "kind": task["kind"],
            "execution_id": execution["execution_id"],
            "outcome": _result_outcome(conn, self._store, execution) or "",
            "verdict": "passed" if verdict == "passed" else "failed",
            "rules": rules, "rules_revision": repo.get_config_revision(conn, task["session_id"]),
            "source_state": issue["state"] if issue is not None else None,
        }
        if isinstance(request.target, CommitReviewTarget):
            fix_task = repo.get_task(conn, repo.get_execution(conn, request.target.source_execution_id)["task_id"])
            fix_executions = repo.list_executions(conn, fix_task["task_id"])
            latest = self._latest_fix_result(conn, fix_executions)
            return FollowupContext(
                **common,
                result_commit=None,
                review=ReviewFacts(
                    fix_task_id=fix_task["task_id"],
                    source_execution_id=request.target.source_execution_id,
                    reviewed_commit=request.target.result_commit,  # 판정 통과면 결과의 reviewed_commit 과 같다
                    latest_fix_execution_id=latest[0] if latest else "",
                    latest_fix_commit=latest[1] if latest else "",
                    rounds_used=sum(1 for e in fix_executions if e["start_key"].startswith("rework:")),
                    max_rework_rounds=task_cycle.max_rework_rounds(conn, fix_task),
                ),
                handled_cause_keys=frozenset(
                    [e["start_key"] for e in fix_executions] + self._request_keys(conn, task, fix_task)
                ),
                task_closed=_operator_closed(task) or _operator_closed(fix_task),
            )
        to_kinds = {rule.to_kind for rule in rules if rule.from_kind == task["kind"]}
        successors = [t for t in repo.successors_of(conn, task["task_id"]) if t["kind"] in to_kinds]
        return FollowupContext(
            **common,
            result_commit=self._result_commit(conn, execution),
            existing_followup_task_id=next((t["task_id"] for t in successors if t["finished_at"] is None), None),
            handled_cause_keys=frozenset(
                [e["start_key"] for t in successors for e in repo.list_executions(conn, t["task_id"])]
                + self._request_keys(conn, task)
            ),
            task_closed=_operator_closed(task),
        )

    def _request_keys(self, conn: Connection, *tasks: Row) -> list[str]:
        return [r["cause_key"] for t in tasks for r in repo.list_human_requests(conn, t["task_id"])]

    def _result_commit(self, conn: Connection, execution: Row) -> str | None:
        try:
            return CodeChangeResult.model_validate_json(
                repo.read_artifact(conn, self._store, execution["result_artifact_id"])
            ).result_commit
        except (ValidationError, ValueError, NotFound, ArtifactMissing, TypeError):
            return None

    def _latest_fix_result(self, conn: Connection, fix_executions: list[Row]) -> tuple[str, str] | None:
        """수정 Task 의 가장 최근 판정 통과 결과 (execution_id, result_commit)."""
        for execution in reversed(fix_executions):
            verdict = repo.get_verdict(conn, execution["execution_id"])
            if verdict is None or json.loads(verdict["verdict_json"]).get("outcome") != "passed":
                continue
            commit = self._result_commit(conn, execution)
            if commit is not None:
                return execution["execution_id"], commit
        return None

    def _apply_followup(
        self, conn: Connection, execution: Row, task: Row, context: FollowupContext, decision: FollowupDecision,
        report: TickReport,
    ) -> None:
        now = self._clock()
        if decision.action == "create_task":
            created = self._create_followup_task(conn, task, decision.create, now)
            if created is None:
                return
            followup_id, fresh = created
            report.followup_tasks_created += int(fresh)
            started = self._start_review(conn, repo.get_task(conn, followup_id), execution, decision.cause_key, report)
            report.followups_started += int(started)
        elif decision.action == "link_existing":
            started = self._start_review(
                conn, repo.get_task(conn, decision.target_task_id), execution, decision.cause_key, report,
            )
            report.followups_started += int(started)
        elif decision.action == "rework":
            fix_task = repo.get_task(conn, decision.target_task_id)
            active = repo.active_execution(conn, fix_task["task_id"])
            started = self._start_fix(
                conn, fix_task, start_key=decision.cause_key, base_commit=decision.base_commit,
                inputs=[repo.get_execution(conn, e)["result_artifact_id"] for e in decision.input_execution_ids],
                release_execution_id=active["execution_id"] if active is not None else None,
                rework_requested=True, rework_rounds_used=context.review.rounds_used,
            )
            if started:
                report.followups_started += 1
                self._write_status(conn, task, "대기", "수정 요청 — 재작업 결과 대기")
        elif decision.action == "request_human":
            target = repo.get_task(conn, decision.target_task_id)
            _, fresh = repo.create_human_request_once(
                conn, target["task_id"], decision.request_code, decision.reason, decision.cause_key, now,
            )
            report.human_requests += int(fresh)
            self._write_status(conn, target, "확인 필요", decision.reason)
        elif decision.hold_code in _HOLD_LABELS:
            self._write_status(conn, task, _HOLD_LABELS[decision.hold_code], decision.reason)
        elif decision.hold_code is None and context.review is not None and context.outcome == "approved":
            # 검토 종결일 뿐이다 — 수정 Task 는 사람(병합·이슈 종료) 차례로 남고 잠금도 유지한다
            repo.finish_task(conn, task_id=task["task_id"], execution_id=execution["execution_id"],
                             status="완료", reason="검토 승인", now=now)
            self._write_status(conn, repo.get_task(conn, context.review.fix_task_id), "확인 필요", decision.reason)

    def _create_followup_task(
        self, conn: Connection, predecessor: Row, spec: FollowupTaskSpec, now: str
    ) -> tuple[str, bool] | None:
        """원인 실행 하나에 후속 Task 하나(`followup_links`). 요구 능력은 후속 종류의 능력 + 선행의 같은 범위 값,
        실행 Agent 는 소스 설정의 검토 Agent(원본이 없으면 자동 선택), 실행 방식은 그 시점 소스 설정."""
        kind_spec = repo.get_kind(conn, spec.session_id, spec.kind)
        if kind_spec is None:
            log.warning("후속 종류 %s 가 세션 %s 등록부에 없음", spec.kind, spec.session_id)
            return None
        _, config = task_cycle.origin_source(conn, predecessor)
        scope = json.loads(predecessor["required_capability_json"])["scope"]
        if kind_spec.scope_key in scope:
            scope = {kind_spec.scope_key: scope[kind_spec.scope_key]}
        chosen = config.review_agent_id if config is not None else None
        row = {
            "task_id": f"task-{secrets.token_hex(6)}",
            "session_id": spec.session_id,
            "title": f"{kind_spec.label}: {predecessor['title']}",
            "request": f"{kind_spec.label} — 선행 업무 「{predecessor['title']}」의 판정 통과 결과",
            "kind": spec.kind,
            "required_capability": {"code": kind_spec.capability_code, "scope": scope},
            "selection_mode": "manual" if chosen else "auto",
            "chosen_agent_id": chosen,
            "run_mode": config.run_mode if config is not None else predecessor["run_mode"],
            "completion_mode": "review",
            "criteria": [c.__dict__ for c in merge_criteria(criteria_template(kind_spec), [])],
            "predecessor_task_id": spec.predecessor_task_id,
            "revision": 1,
            "target": {},
            "status": "대기",
            "status_reason": "준비 판정 대기",
        }
        return repo.create_followup_once(conn, spec, row, now)

    def _start_review(
        self, conn: Connection, review_task: Row, fix_execution: Row, start_key: str, report: TickReport
    ) -> bool:
        """수정 결과 커밋을 검토 Task 의 새 실행으로 고정한다. 진행 중인 검토는 끊지 않고 끝나기를 기다린다."""
        active = repo.active_execution(conn, review_task["task_id"])
        if active is not None and active["status"] != "result_ready":
            return False
        readiness = task_cycle.evaluate(
            conn, review_task, now=self._clock(), settings=self._settings,
            pair_agent_id=fix_execution["agent_id"],
            result_dependency_task_id=fix_execution["task_id"], result_dependency_ready=True,
            **self._manual_override(review_task),
        )
        if not readiness.ready:
            self._write_blocked(conn, review_task, readiness, report)
            return False
        agent = repo.get_agent(conn, readiness.agent_id)
        fix_result = CodeChangeResult.model_validate_json(
            repo.read_artifact(conn, self._store, fix_execution["result_artifact_id"])
        )
        rule = repo.get_rule(conn, review_task["session_id"], fix_execution["kind"], review_task["kind"])
        inputs = [assemble_handoff(conn, self._store, fix_execution, review_task, rule, self._clock())] if rule else []
        return self._create_cycle_execution(
            conn, review_task, agent,
            target={
                "local_registration_id": agent["local_registration_id"],
                "source_execution_id": fix_execution["execution_id"],
                "base_commit": fix_result.base_commit,
                "result_commit": fix_result.result_commit,
            },
            inputs=inputs, start_key=start_key, predecessor_execution_id=fix_execution["execution_id"],
            release_execution_id=active["execution_id"] if active is not None else None,
        )

    def _start_ready_tasks(self, conn: Connection, report: TickReport, only: set[str] | None = None) -> None:
        """실행이 없는 업무 순환 Task 를 하나씩 따로 평가한다 — 한 Task 의 대기가 다른 Task 를 막지 않는다.
        수정 결과에서 시작하는 종류(검토)는 `_cycle_followups` 가 착수한다. 결과를 기다리던 시도는 사람 응답이 있을 때만
        `_resume` 이 잇는다."""
        for task in repo.list_tasks(conn, None):
            policy = policy_for(task["kind"])
            if task["finished_at"] is not None or not policy.cycle or (only is not None and task["task_id"] not in only):
                continue
            active = repo.active_execution(conn, task["task_id"])
            if active is not None:
                if self._resume(conn, task, active, policy, report):
                    report.tasks_resumed += 1
                continue
            if policy.starts_from_result:
                continue
            if self._start_fix(conn, task, start_key=auto_start_key(task["task_id"], task["revision"]), report=report):
                report.tasks_started += 1

    def _resume(self, conn: Connection, task: Row, active: Row, policy: ExecutionPolicy, report: TickReport) -> bool:
        """결과를 기다리던 시도(`result_ready`)에 대해 물은 요청에 운영자가 답해 새 revision 이 생겼으면 그 시도를 해제하고
        새 revision 의 `auto_start_key` 로 다시 착수한다(준비 판정을 다시 거친다). 준비 판정 대기 요청(`ready:`)은 실행 전의
        일이라 결과를 기다리는 시도를 다시 돌리지 않는다. 수정은 이전 입력 + 이전 결과를, 검토는 같은 수정 결과를 다시 본다."""
        if active["status"] != "result_ready":
            return False
        previous = ExecutionRequest.model_validate_json(active["request_json"])
        answered = [
            r for r in repo.list_human_responses(conn, task["task_id"])
            if r["task_revision"] > previous.task_revision and r["asked_revision"] >= previous.task_revision
            and not r["cause_key"].startswith(task_cycle.READINESS_REQUEST_PREFIX)
        ]
        if not answered:
            return False
        start_key = auto_start_key(task["task_id"], task["revision"])
        if policy.starts_from_result:
            fix_execution = repo.get_execution(conn, previous.target.source_execution_id)
            return self._start_review(conn, task, fix_execution, start_key, report)
        inputs = [*previous.input_artifact_ids, active["result_artifact_id"]]
        return self._start_fix(
            conn, task, start_key=start_key, inputs=list(dict.fromkeys(i for i in inputs if i)),
            release_execution_id=active["execution_id"], report=report,
        )

    def _start_fix(
        self, conn: Connection, task: Row, *, start_key: str, base_commit: str | None = None,
        inputs: list[str] | None = None, release_execution_id: str | None = None, report: TickReport | None = None,
        **readiness_overrides: Any,
    ) -> bool:
        """준비 판정을 통과하면 담당 Agent 의 등록값 + 소스의 검증 프로필로 target 을 고정해 실행을 만든다. 기준 커밋은
        주어진 값(재작업: 검토한 결과 커밋), 없으면 이 Task 의 마지막 결과 커밋(남은 task 브랜치), 없으면 등록 보고값."""
        now = self._clock()
        readiness = task_cycle.evaluate(
            conn, task, now=now, settings=self._settings, **readiness_overrides, **self._manual_override(task),
        )
        if not readiness.ready:
            self._write_blocked(conn, task, readiness, report)
            return False
        agent = repo.get_agent(conn, readiness.agent_id)
        _, config = task_cycle.origin_source(conn, task)
        profiles = json.loads(agent["verification_profile_ids_json"])
        previous = [e for e in repo.list_executions(conn, task["task_id"]) if e["result_artifact_id"] is not None]
        latest_commit = next((c for c in map(lambda e: self._result_commit(conn, e), reversed(previous)) if c), None)
        return self._create_cycle_execution(
            conn, task, agent,
            target={
                "local_registration_id": agent["local_registration_id"],
                "base_commit": base_commit or latest_commit or agent["base_commit"],
                "verification_profile_id": config.fix_verification_profile_id if config is not None
                else (profiles[0] if profiles else None),
            },
            inputs=inputs or [], start_key=start_key, predecessor_execution_id=None,
            release_execution_id=release_execution_id,
        )

    def _create_cycle_execution(
        self, conn: Connection, task: Row, agent: Row, *, target: dict[str, Any], inputs: list[str], start_key: str,
        predecessor_execution_id: str | None, release_execution_id: str | None,
    ) -> bool:
        """요청을 고정하고 기존 `create_execution`(start_key 유일·활성 잠금)으로 만든다. 이전 시도 해제도 같은 트랜잭션."""
        task_id = task["task_id"]
        spec = repo.get_kind(conn, task["session_id"], task["kind"])
        attempts = repo.list_executions(conn, task_id)
        try:
            request = ExecutionRequest.model_validate({
                "contract_version": 1,
                "execution_id": f"exec-{secrets.token_hex(8)}",
                "task_id": task_id,
                "kind": task["kind"],
                "agent_id": agent["agent_id"],
                "task_revision": task["revision"],
                "request": task_cycle.request_text(conn, task),
                "input_artifact_ids": inputs,
                "target": target,
                "kind_spec": spec.model_dump() if spec is not None else None,
            })
        except ValidationError as exc:
            log.warning("업무 %s 의 실행 요청을 만들 수 없음: %s", task_id, exc)
            self._write_status(conn, task, "대기", "실행 요청을 만들 수 없음 — 연결 프로그램의 등록 보고(기준 커밋·검증 프로필) 대기")
            return False
        try:
            repo.create_execution(
                conn,
                execution_id=request.execution_id, task_id=task_id,
                attempt_no=attempts[-1]["attempt_no"] + 1 if attempts else 1,
                start_key=start_key, agent_id=agent["agent_id"], kind=task["kind"], request=request,
                assigned_connector_id=agent["connector_id"], predecessor_execution_id=predecessor_execution_id,
                now=self._clock(), release_execution_id=release_execution_id, ready=True,
            )
        except (DuplicateStartKey, ActiveExecutionExists) as exc:
            log.info("업무 %s 는 이미 이 원인으로 실행을 만들었음: %s", task_id, exc)
            return False
        except TaskClosed:
            log.info("업무 %s 는 착수 직전에 마감됨", task_id)
            return False
        self._refresh_task(conn, task_id)  # → 실행 요청됨 · 접수 대기
        return True

    def _write_blocked(
        self, conn: Connection, task: Row, readiness: TaskReadiness, report: TickReport | None = None
    ) -> None:
        """대기 사유를 모두 이유 문구로. 직접 실행 모드만 남았으면 `실행 가능`(사람이 누르면 된다). 대기 코드 목록이
        직전 기록과 다를 때만 `blocked` 이벤트를 남긴다(ADR-0015). 운영자가 정해야 하는 사유는 이 revision 에 한 번 사람
        요청으로 남긴다 — 이미 다른 요청을 기다리는 중(`decision_pending`)이면 더 묻지 않는다."""
        codes = {b.code for b in readiness.blockers}
        if codes == {"manual_mode"}:
            self._write_status(conn, task, "실행 가능", "직접 실행 모드")
        else:
            self._write_status(conn, task, "대기", " · ".join(b.reason for b in readiness.blockers))
        if task["finished_at"] is None:
            blockers = sorted({(b.code, b.actor) for b in readiness.blockers})
            repo.record_blocked(conn, task["task_id"], [{"code": c, "actor": a} for c, a in blockers], now=self._clock())
        if "decision_pending" in codes:
            return
        for blocker in readiness.blockers:
            if blocker.code not in task_cycle.READINESS_REQUEST_CODES:
                continue
            _, fresh = repo.create_human_request_once(
                conn, task["task_id"], blocker.code, blocker.reason,
                task_cycle.readiness_cause_key(blocker.code, task["revision"]), self._clock(),
            )
            if report is not None:
                report.human_requests += int(fresh)

    # --- 8. 후속 스캔 -------------------------------------------------------------------

    def _spawn_successors(self, conn: Connection, report: TickReport) -> None:
        """선행 실행이 `result_ready` + 판정 `passed` + 결과 `outcome` ∈ 규칙 `on_outcomes` 면 규칙 `handoff_kinds` 로 입력을
        고정하고 실행을 만든다 (ADR-0009 (3)). 선행 Task 의 `완료` 를 기다리지 않는다. 규칙·outcome 이 맞지 않으면 이유를 남긴다."""
        now = self._clock()
        for task in repo.tasks_with_ready_predecessor(conn):
            task_id = task["task_id"]
            if policy_for(task["kind"]).cycle:
                continue  # 업무 순환 종류는 `_advance_cycle` 이 결과에서 잇는다
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

    # --- 10. callback 전달 ---------------------------------------------------------------

    def _deliver_callbacks(self, conn: Connection, report: TickReport) -> None:
        """callback_url 이 있고 아직 안 보낸 체인 중 `chain_settled` 인 것에 ChainCallback 을 1회 POST 한다 (ADR-0010).
        HTTP 는 트랜잭션 밖. 실패는 attempts·next_at 으로 물러나 재시도하고 CALLBACK_MAX_ATTEMPTS 뒤 멈춘다."""
        now = self._clock()
        for chain in repo.chains_awaiting_callback(conn, now, max_attempts=CALLBACK_MAX_ATTEMPTS):
            chain_id = chain["chain_id"]
            tasks = repo.tasks_of_chain(conn, chain_id)
            states = {t["task_id"]: self._task_state(conn, t, now) for t in tasks}
            nodes = [
                NodeState(states[t["task_id"]].label, self._predecessor_label(conn, t, states, now)) for t in tasks
            ]
            if not chain_settled(nodes):
                continue
            url = chain["callback_url"]
            if not host_allowed(url, self._settings.callback_hosts):
                # 접수 뒤 허용 목록이 바뀐 경우 — 보내지 않고 기록만 (화면의 callback_last_error)
                repo.record_callback_attempt(conn, chain_id, ok=False, error="허용 목록 밖", now=now, next_at=None)
                report.callbacks_failed += 1
                continue
            payload = self._chain_callback(conn, chain, tasks, states, now)
            try:
                self._callbacks.post(url, payload.model_dump(mode="json"))
            except CallbackFailed as exc:
                attempts = chain["callback_attempts"] + 1
                repo.record_callback_attempt(
                    conn, chain_id, ok=False, error=str(exc), now=now,
                    next_at=_plus_seconds(now, CALLBACK_BACKOFF_SECONDS * 2 ** (attempts - 1)),
                )
                report.callbacks_failed += 1
                log.warning("callback 전송 실패 chain %s (%s회): %s", chain_id, attempts, exc)
                continue
            repo.record_callback_attempt(conn, chain_id, ok=True, error=None, now=now, next_at=None)
            report.callbacks_sent += 1

    # --- 11. 원본 이슈 반영 -------------------------------------------------------------

    def _deliver_github(self, conn: Connection, report: TickReport) -> None:
        """원본 이슈 댓글 outbox (ADR-0014 결정 8). 반영 실패는 기록만 하고 Task·실행을 바꾸지 않는다."""
        if self._github is None:
            return
        now = self._clock()
        report.deliveries_queued += github_delivery.queue_source_updates(
            conn, self._store, self._settings.public_url, now
        )
        result = github_delivery.deliver_source_updates(conn, self._github, now)
        report.deliveries_sent += result.created + result.updated + result.reconciled
        report.deliveries_failed += result.failed
        if result.rate_limited:
            log.warning("GitHub 반영 rate limit — 다음 시각까지 물러남")

    def _task_state(self, conn: Connection, task: Row, now: str) -> UserStatus:
        """체인 화면과 같은 판정 — 마감된 Task 는 저장 상태, 아니면 지금 실행·연결 상태로 판정 (`views.status_of`)."""
        return views.status_of(task, views.build_task_view(conn, task, now=now, settings=self._settings))

    def _predecessor_label(self, conn: Connection, task: Row, states: dict[str, UserStatus], now: str) -> str | None:
        predecessor_id = task["predecessor_task_id"]
        if predecessor_id is None:
            return None
        if predecessor_id in states:
            return states[predecessor_id].label
        predecessor = repo.get_task(conn, predecessor_id)  # 체인 밖 선행
        return None if predecessor is None else self._task_state(conn, predecessor, now).label

    def _chain_callback(
        self, conn: Connection, chain: Row, tasks: list[Row], states: dict[str, UserStatus], now: str
    ) -> ChainCallback:
        """CONTRACT 12절 ChainCallback. human_gate 는 체인 화면(`views.chain_summary`)의 값 그대로, outcome·summary 는
        결과 산출물이 있는 최신 시도의 봉투에서 읽는다 (없으면 null). 모델을 부르지 않는다."""
        public_url = self._settings.public_url
        gate = views.chain_summary(conn, chain, now=now, settings=self._settings)["human_gate"]
        entries: list[CallbackTask] = []
        for task in tasks:
            task_id = task["task_id"]
            state = states[task_id]
            with_result = [e for e in repo.list_executions(conn, task_id) if e["result_artifact_id"] is not None]
            outcome, summary = _result_envelope(conn, self._store, with_result[-1]) if with_result else (None, None)
            entries.append(CallbackTask(
                task_id=task_id, key=task["source_ref"] or "", kind=task["kind"], title=task["title"],
                status=state.label, status_reason=state.reason, outcome=outcome, summary=summary,
                task_url=f"{public_url}/tasks/{task_id}" if public_url else None,
            ))
        return ChainCallback(
            contract_version=1,
            chain_id=chain["chain_id"],
            title=chain["title"],
            source="n8n",
            chain_url=f"{public_url}/chains/{chain['chain_id']}" if public_url else None,
            settled_at=now,
            human_gate=CallbackGate(label=gate["label"], status_label=gate["status_label"], reason=gate["reason"]),
            tasks=entries,
        )


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
        callbacks=HttpCallbackClient(),
        settings=settings,
        clock=utc_now,
        # 토큰이 없으면 GitHub 수집만 꺼진다(ADR-0014 결정 1)
        github=HttpGitHubClient(settings.github_token, settings.github_repos) if settings.github_token else None,
    )
    log.info("중앙 워커 시작 — 복구 스캔 %s", asdict(worker.tick()))
    worker.run_forever(3.0)


if __name__ == "__main__":
    main()
