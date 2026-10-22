"""인계 리포트 서버 쪽 — 시작·수집·판정·행동 (ADR-0033, ARCHITECTURE "인계 리포트 — phase 25").

step 4: PR 근거 수집(D22). 중앙이 GitHub 에서 업무에 연결된 PR 의 본문·커밋·리뷰 코멘트를 읽는다(GET 만). 읽기 실패면
PR 근거 전부 없이 사유만 남기고 예외를 내보내지 않는다. 사유·로그에 토큰·헤더·응답 본문을 넣지 않는다.
step 5: 판정 통과한 실행 → 원본 리포트 작업(`queue_original`, 워커의 판정 지점이 직후에 부른다)·시간 초과(`expire_reports`).
대상은 종류 이름이 아니라 판정·판단 단계·Agent 사실(도메인 `report_target`)로, 체크아웃 커밋은 요청 target 의 모양으로 고른다.
step 6: 러너 claim 의 리포트 작업(`lease_job`)·받는 러너 참조(`report_ref`)·제출 판정(`submit_result`·`submit_failure`).
original·tailored 가 같은 길이다. 근거 ID 가 확인되지 않아도 거절하지 않고 표시한다(D20).
step 8: 사람용 보기 묶음(`report_view`·`stage_reports`)·행동(`answer_decision`·`request_tailored`·`regenerate`). 권한은 도메인
판정(보낸 사람·받는 사람은 조회·행동 순간에 `party_facts` 로)이고, API(`report_api`)와 웹(step 9)이 이 함수를 같이 부른다.
`server/worker.py` 를 import 하지 않는다(순환 import).
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import ArtifactMissing, NotFound
from workflow.adapters.github_client import (
    GitHubClient,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
)
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import (
    HANDOFF_REPORT_MAX_BYTES,
    RUNNER_CAPABILITY_HANDOFF_REPORT,
    ArtifactMeta,
    CodeChangeTarget,
    CommitReviewTarget,
    ExecutionRequest,
    HandoffBundle,
    HandoffDecision,
    HandoffReport,
    HandoffReportRef,
    ReportJob,
    ReportJobFailure,
    TriageTarget,
    format_work_key,
)
from workflow.domain.execution_policy import is_triage_kind
from workflow.domain.handoff_report import (
    ArtifactFact,
    EvidenceSet,
    PartyFacts,
    PullFact,
    ReportRequestFacts,
    TailoredRequestFacts,
    TargetFacts,
    VerificationFact,
    build_report_request,
    build_tailored_request,
    can_answer,
    can_regenerate,
    can_tailor,
    check_citations,
    check_evidence,
    report_parties,
    report_target,
    tailor_problem,
)
from workflow.server.auth import LoggedIn
from workflow.server.settings import Settings
from workflow.server.stage_runs import WorkActionError

log = logging.getLogger("workflow.handoff_reports")

PR_EVIDENCE_MAX_PULLS = 3
PR_BODY_MAX = 4000
PR_COMMITS_MAX = 50
PR_COMMIT_LINE_MAX = 200
PR_COMMENTS_MAX = 30
PR_COMMENT_MAX = 1000

# 앞에서부터 처음 맞는 것. 나머지 `GitHubError`(설치 토큰 발급 실패·응답 형식·허용 저장소 밖)는 "응답 형식 오류".
_READ_FAILURES: tuple[tuple[type[GitHubError], str], ...] = (
    (GitHubForbidden, "GitHub 가 거부함(Pull requests 읽기 권한 확인)"),
    (GitHubRateLimited, "GitHub 요청 한도"),
    (GitHubNotFound, "PR 을 찾을 수 없음"),
    (GitHubUnavailable, "GitHub 에 닿지 않음"),
)


@dataclass(frozen=True)
class PullEvidence:
    number: int
    title: str
    url: str
    body: str
    commits: tuple[tuple[str, str], ...]  # (sha, 메시지 첫 줄)
    comments: tuple[tuple[str, str, str], ...]  # (작성자, 위치 표시, 본문)


@dataclass(frozen=True)
class PrEvidence:
    pulls: tuple[PullEvidence, ...] = ()
    error: str | None = None


def _linked_pulls(conn: Connection, work_item_id: str) -> list[tuple[str, str, int]]:
    """업무의 감지 PR + 업무 단계의 Runloom PR(번호 있는 것) — (source_id, 저장소, 번호), 중복 없이 갱신 최근순 상한 개."""
    rows = conn.execute(
        "SELECT source_id, repository_full_name, pr_number, pr_updated_at AS at FROM work_pull_requests"
        " WHERE work_item_id = ?"
        " UNION ALL SELECT p.source_id, p.repository_full_name, p.pr_number, p.updated_at FROM task_pull_requests p"
        " JOIN tasks t ON t.task_id = p.task_id WHERE t.work_item_id = ? AND p.pr_number IS NOT NULL"
        " ORDER BY at DESC",
        (work_item_id, work_item_id),
    ).fetchall()
    pulls: dict[tuple[str, int], tuple[str, str, int]] = {}
    for row in rows:
        key = (row["repository_full_name"].lower(), row["pr_number"])
        if key not in pulls:
            pulls[key] = (row["source_id"], row["repository_full_name"], row["pr_number"])
    return list(pulls.values())[:PR_EVIDENCE_MAX_PULLS]


def _read_pull(client: GitHubClient, repository: str, number: int) -> PullEvidence:
    detail = client.get_pull_detail(repository, number)
    commits = client.list_pull_commits(repository, number)
    comments = client.list_pull_review_comments(repository, number)
    return PullEvidence(
        number=number,
        title=detail.title,
        url=f"https://github.com/{repository}/pull/{number}",  # 링크는 저장소 이름과 번호로 계산한다
        body=detail.body[:PR_BODY_MAX],
        commits=tuple((c.sha, c.message[:PR_COMMIT_LINE_MAX]) for c in commits[:PR_COMMITS_MAX]),
        comments=tuple(
            (c.author_login or "알 수 없음", c.path if c.line is None else f"{c.path}:{c.line}", c.body[:PR_COMMENT_MAX])
            for c in comments[:PR_COMMENTS_MAX]
        ),
    )


def collect_pr_evidence(
    conn: Connection, github_for: Callable[[GitHubSourceConfig], GitHubClient | None], *, work_item_id: str
) -> PrEvidence:
    """행을 만들 때(원본·다시 만들기) 부른다 — 트랜잭션 밖. PR 이 없으면 빈 값, 하나라도 읽기 실패면 PR 근거 없이 사유."""
    linked = _linked_pulls(conn, work_item_id)
    if not linked:
        return PrEvidence()
    work = conn.execute("SELECT session_id FROM work_items WHERE work_item_id = ?", (work_item_id,)).fetchone()
    clients: dict[str, GitHubClient | None] = {}
    pulls: list[PullEvidence] = []
    try:
        for source_id, repository, number in linked:
            if source_id not in clients:
                config = repo.get_github_source(conn, work["session_id"], source_id)
                clients[source_id] = github_for(config) if config is not None else None
            client = clients[source_id]
            if client is None:
                return PrEvidence(error="PR 읽기 실패 — GitHub 연결 없음")
            pulls.append(_read_pull(client, repository, number))
    except GitHubError as exc:
        reason = next((text for kind, text in _READ_FAILURES if isinstance(exc, kind)), "응답 형식 오류")
        log.warning("인계 리포트 PR 근거 읽기 실패 %s: %s", work_item_id, type(exc).__name__)
        return PrEvidence(error=f"PR 읽기 실패 — {reason}")
    return PrEvidence(pulls=tuple(pulls))


# --- 판정 통과 → 원본 리포트 작업 (step 5) ---------------------------------------------------------

_SOURCE_KINDS = {"github": "GitHub 이슈", "jira": "Jira 이슈", "n8n": "n8n 입구", "manual": "직접 등록"}
_TEXT_TYPES = ("application/json",)


def _agent_facts(conn: Connection, agent: Row | None, *, verdict_passed: bool = True, triage_cause: str | None = None,
                 triage_stage: bool = False) -> TargetFacts:
    """Agent·러너 사실(+ 원본이면 판정·판단 단계) — 원본 대상 판정과 맞춤 풀이 Agent 판정이 같이 쓴다."""
    connector = repo.get_connector(conn, agent["connector_id"]) if agent and agent["connector_id"] else None
    capabilities = json.loads(connector["capabilities_json"] or "null") if connector else None
    return TargetFacts(
        verdict_passed=verdict_passed,
        triage_stage=triage_stage,
        triage_cause=triage_cause,
        agent_exists=agent is not None,
        connection_type=agent["connection_type"] if agent else None,
        runner_attached=connector is not None and connector["revoked_at"] is None,
        runner_capable=RUNNER_CAPABILITY_HANDOFF_REPORT in (capabilities or ()),
    )


def _target_facts(conn: Connection, execution: Row) -> tuple[TargetFacts, Row | None]:
    """도메인 대상·불가 판정 재료와 원인 Agent 행."""
    verdict = repo.get_verdict(conn, execution["execution_id"])
    triage = conn.execute("SELECT cause FROM triage_logs WHERE execution_id = ?", (execution["execution_id"],)).fetchone()
    agent = repo.get_agent(conn, execution["agent_id"])
    return _agent_facts(
        conn, agent,
        verdict_passed=verdict is not None and json.loads(verdict["verdict_json"]).get("outcome") == "passed",
        triage_stage=triage is not None, triage_cause=triage["cause"] if triage else None,
    ), agent


def _result_data(conn: Connection, store: ArtifactStore, execution: Row) -> dict:
    """결과 봉투 JSON(코드 수정·검토·범용·판단). 읽지 못하면 빈 dict — 요청문에 '없음' 으로 남는다."""
    try:
        data = json.loads(repo.read_artifact(conn, store, execution["result_artifact_id"]))
    except (ValueError, NotFound, ArtifactMissing):
        return {}
    return data if isinstance(data, dict) else {}


def _commits(request: ExecutionRequest, result: dict, execution: Row) -> tuple[str | None, str | None]:
    """(체크아웃 커밋, base 커밋) — ARCHITECTURE "근거" 의 체크아웃 커밋 규칙을 target 모양으로 고른다."""
    target = request.target
    if isinstance(target, CodeChangeTarget):
        checkout = result.get("result_commit") or target.base_commit
        return checkout, (target.base_commit if target.base_commit != checkout else None)
    if isinstance(target, CommitReviewTarget):
        return target.result_commit, target.base_commit
    if isinstance(target, TriageTarget):
        return target.base_commit, None
    return execution["folder_commit"], None


def _verification(result: dict) -> VerificationFact | None:
    v = result.get("verification")
    if not isinstance(v, dict):
        return None
    try:
        return VerificationFact(profile_id=str(v["profile_id"]), exit_code=int(v["exit_code"]),
                                result_commit=str(v["result_commit"]))
    except (KeyError, TypeError, ValueError):
        return None


def _artifact_fact(conn: Connection, store: ArtifactStore, row: Row, *, with_text: bool) -> ArtifactFact:
    media_type = row["content_type"].split(";")[0].strip().lower()
    text = None
    if with_text and (media_type.startswith("text/") or media_type in _TEXT_TYPES):
        try:
            text = repo.read_artifact(conn, store, row["artifact_id"]).decode("utf-8", errors="replace")
        except (NotFound, ArtifactMissing):
            text = None
    return ArtifactFact(artifact_id=row["artifact_id"], kind=row["kind"], name=row["name"], size=row["size"],
                        media_type=media_type, text=text)


def _input_artifact_ids(conn: Connection, store: ArtifactStore, request: ExecutionRequest) -> list[str]:
    """실행의 입력 산출물 + 입력 인계 묶음이 가리키는 선행 산출물(inputs·attachments·원인 결과)."""
    ids = list(request.input_artifact_ids)
    for artifact_id in request.input_artifact_ids:
        row = repo.get_artifact(conn, artifact_id)
        if row is None or row["kind"] != "handoff_bundle":
            continue
        try:
            bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, store, artifact_id))
        except (ValidationError, ValueError, NotFound, ArtifactMissing):
            continue
        ids += [i.artifact_id for i in bundle.inputs] + [a.artifact_id for a in bundle.attachments]
        ids.append(bundle.source_result_artifact_id)
    return list(dict.fromkeys(ids))


def _original_inputs(conn: Connection, store: ArtifactStore, github_for, execution: Row, task: Row) -> dict:
    """`inputs_json` — 체크아웃 커밋·확인 가능한 근거·PR 근거·요청문. 비밀값·로컬 경로는 넣지 않는다."""
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    result = _result_data(conn, store, execution)
    checkout, base = _commits(request, result, execution)
    work = conn.execute("SELECT * FROM work_items WHERE work_item_id = ?", (task["work_item_id"],)).fetchone()
    pr = collect_pr_evidence(conn, github_for, work_item_id=work["work_item_id"])

    own = repo.artifacts_of(conn, execution["execution_id"])
    own_ids = {a["artifact_id"] for a in own}
    inputs = [row for aid in _input_artifact_ids(conn, store, request)
              if aid not in own_ids and (row := repo.get_artifact(conn, aid)) is not None]
    artifacts = tuple(_artifact_fact(conn, store, a, with_text=True) for a in own) + tuple(
        _artifact_fact(conn, store, a, with_text=False) for a in inputs)
    verification = _verification(result)
    evidence = EvidenceSet(
        artifacts=frozenset(a.artifact_id for a in artifacts),
        commits=frozenset({c for c in (checkout, base) if c} | {sha for p in pr.pulls for sha, _ in p.commits}),
        pulls=frozenset(p.number for p in pr.pulls),
        source_work=bool(work["request"].strip() or work["source_url"]),
        verification=verification is not None or any(a.kind == "verification_log" for a in artifacts),
        claims=frozenset(),
    )
    spec = repo.get_kind(conn, work["session_id"], execution["kind"])
    outcome, summary = result.get("outcome"), result.get("summary")
    text = build_report_request(ReportRequestFacts(
        work_key=format_work_key(work["key_number"]), title=work["title"],
        source_kind=_SOURCE_KINDS.get(work["source_type"], work["source_type"]), source_url=work["source_url"],
        source_text=work["request"], kind_label=spec.label if spec else execution["kind"],
        outcome=outcome if isinstance(outcome, str) else None, summary=summary if isinstance(summary, str) else None,
        verification=verification, artifacts=artifacts,
        pulls=tuple(PullFact(number=p.number, title=p.title, url=p.url, body=p.body, commits=p.commits,
                             comments=p.comments) for p in pr.pulls),
        pr_error=pr.error, evidence=evidence,
    ))
    return {
        "local_registration_id": request.target.local_registration_id,
        "checkout_commit": checkout,
        "base_commit": base,
        "request": text,
        "evidence": {"artifacts": sorted(evidence.artifacts), "commits": sorted(evidence.commits),
                     "pulls": sorted(evidence.pulls), "source_work": evidence.source_work,
                     "verification": evidence.verification, "claims": []},
        "pull_requests": [{"number": p.number, "title": p.title, "url": p.url} for p in pr.pulls],
        "pr_evidence_error": pr.error,
    }


def queue_original(
    conn: Connection, store: ArtifactStore, settings: Settings,
    github_for: Callable[[GitHubSourceConfig], GitHubClient | None], *, execution_id: str, now: str,
) -> Row | None:
    """판정 통과한 실행의 원본 리포트 작업 — 이미 원본 행이 있으면(어느 상태든) 그 행(멱등). 대상 아님이면 None,
    불가 사유면 `unavailable` 행, 그 밖은 PR 근거 수집(트랜잭션 밖)·요청문 조립 뒤 `queued` 행."""
    existing = repo.latest_original_report(conn, execution_id)
    if existing is not None:
        return existing
    return _new_original(conn, store, github_for, execution_id=execution_id, now=now)


def _new_original(conn: Connection, store: ArtifactStore,
                  github_for: Callable[[GitHubSourceConfig], GitHubClient | None], *, execution_id: str,
                  now: str) -> Row | None:
    """새 원본 행 — 대상 판정 → 불가면 `unavailable`, 만들면 PR 근거 수집·요청문 조립 뒤 `queued`(진행 중이면 그 행)."""
    execution = repo.get_execution(conn, execution_id)
    facts, agent = _target_facts(conn, execution)
    target = report_target(facts)
    if target.action == "skip":
        return None
    task = repo.get_task(conn, execution["task_id"])
    keys = {"session_id": task["session_id"], "work_item_id": task["work_item_id"],
            "source_execution_id": execution_id, "source_task_id": task["task_id"], "job_type": "original",
            "agent_id": execution["agent_id"], "connector_id": agent["connector_id"] if agent else None}
    if target.action == "unavailable":
        return repo.record_handoff_report_unavailable(conn, **keys, reason=target.reason, inputs={}, now=now)
    inputs = _original_inputs(conn, store, github_for, execution, task)
    row, _created = repo.create_handoff_report(conn, **keys, inputs=inputs, now=now)
    return row


def _minus_seconds(now: str, seconds: int) -> str:
    """`utc_now` 와 같은 표기(UTC, 마이크로초, `Z`) — repo 의 문자열 비교와 맞는다."""
    moved = (datetime.fromisoformat(now) - timedelta(seconds=seconds)).astimezone(UTC)
    return moved.isoformat(timespec="microseconds").replace("+00:00", "Z")


def expire_reports(conn: Connection, settings: Settings, *, now: str) -> int:
    """`queued`·`leased` 가 만들어진 지 대기 시간 이상이면 `timed_out`(경계 포함). 바뀐 수."""
    wait = settings.handoff_report_wait_seconds
    return repo.expire_handoff_reports(conn, older_than=_minus_seconds(now, wait),
                                       reason=f"{wait // 60}분 안에 리포트가 오지 않음", now=now)


# --- 러너 claim·제출 (step 6) ---------------------------------------------------------------------


class ReportJobError(Exception):
    """제출 경로 오류 — 라우터가 `ApiError` 로 옮긴다."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def lease_job(conn: Connection, connector_id: str, *, now: str) -> ReportJob | None:
    """그 러너에 배정된 가장 이른 `queued` 리포트 작업(original·tailored)을 `leased` 로 바꾸고 `ReportJob` 으로."""
    row = repo.lease_handoff_report(conn, connector_id, now=now)
    if row is None:
        return None
    inputs = json.loads(row["inputs_json"])
    return ReportJob.model_validate({
        "contract_version": 1, "report_id": row["report_id"], "job_type": row["job_type"], "agent_id": row["agent_id"],
        "local_registration_id": inputs["local_registration_id"], "checkout_commit": inputs["checkout_commit"],
        "base_commit": inputs["base_commit"], "request": inputs["request"],
    })


def report_ref(conn: Connection, report: Row) -> HandoffReportRef:
    """`done` 원본 리포트 → 받는 러너에게 붙는 참조(산출물·sha256·그 순간의 결정 답)."""
    artifact = repo.get_artifact(conn, report["artifact_id"])
    decisions = [
        HandoffDecision(item_id=d["item_id"], answer=d["answer"], note=d["note"], answered_at=d["answered_at"],
                        answered_by=repo.get_member(conn, report["session_id"], d["member_id"])["display_name"])
        for d in repo.list_report_decisions(conn, report["report_id"])
    ]
    return HandoffReportRef(report_id=report["report_id"], artifact_id=report["artifact_id"],
                            sha256=artifact["sha256"], decisions=decisions)


def _not_leased(report_id: str) -> ReportJobError:
    return ReportJobError(409, "report_not_leased", f"{report_id}은 지금 받을 수 있는 리포트 작업이 아닙니다.")


def _leased_report(conn: Connection, connector_id: str, report_id: str) -> Row:
    row = repo.get_handoff_report(conn, report_id)
    if row is None:
        raise ReportJobError(404, "not_found", f"리포트 작업 {report_id}을 찾을 수 없습니다.")
    if row["connector_id"] != connector_id:
        raise ReportJobError(403, "forbidden", f"{report_id}은 {connector_id}에 배정되지 않았습니다.")
    if row["status"] != "leased":
        raise _not_leased(report_id)
    return row


def _reject(conn: Connection, row: Row, connector_id: str, reason: str, now: str) -> ReportJobError:
    if not repo.fail_handoff_report(conn, row["report_id"], connector_id=connector_id, reason=reason, now=now):
        return _not_leased(row["report_id"])
    return ReportJobError(422, "report_invalid", reason)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    loc = ".".join(str(part) for part in error["loc"])
    return f"{loc}: {error['msg']}" if loc else error["msg"]


def _evidence_check(report: HandoffReport, row: Row) -> dict:
    evidence = json.loads(row["inputs_json"]).get("evidence") or {}
    if row["job_type"] == "tailored":
        return check_citations(report, frozenset(evidence.get("claims", ()))).to_json()
    return check_evidence(report, EvidenceSet(
        artifacts=frozenset(evidence.get("artifacts", ())), commits=frozenset(evidence.get("commits", ())),
        pulls=frozenset(evidence.get("pulls", ())), source_work=bool(evidence.get("source_work")),
        verification=bool(evidence.get("verification")), claims=frozenset(evidence.get("claims", ())),
    )).to_json()


def submit_result(conn: Connection, store: ArtifactStore, *, connector_id: str, report_id: str, body: bytes,
                  now: str) -> Row:
    """판정 순서(ARCHITECTURE "러너 엔드포인트"): 행·소유·leased → 크기 → 계약 → 작업과 같은 id·종류 → 근거 확인 →
    원인 실행에 산출물 저장 → done. 크기·계약·id 위반은 `failed` 로 바꾸고 422. 원인 결과가 대체됐는지는 보지 않는다(D16)."""
    row = _leased_report(conn, connector_id, report_id)
    if len(body) > HANDOFF_REPORT_MAX_BYTES:
        raise _reject(conn, row, connector_id, "리포트가 너무 큼", now)
    try:
        report = HandoffReport.model_validate_json(body)
    except ValidationError as exc:
        raise _reject(conn, row, connector_id, f"리포트 형식 오류 — {_first_error(exc)}", now) from None
    if (report.report_id, report.job_type) != (row["report_id"], row["job_type"]):
        raise _reject(conn, row, connector_id, "리포트 형식 오류 — report_id·job_type 이 작업과 다름", now)
    evidence_check = _evidence_check(report, row)
    meta = ArtifactMeta(contract_version=1, kind="handoff_report", name="handoff_report.json",
                        content_type="application/json", sha256=hashlib.sha256(body).hexdigest(), size=len(body))
    created, _ = repo.store_artifact(conn, store, execution_id=row["source_execution_id"], session_id=row["session_id"],
                                     meta=meta, data=body, now=now)
    if not repo.finish_handoff_report(conn, report_id, connector_id=connector_id, artifact_id=created.artifact_id,
                                      evidence_check=evidence_check, now=now):
        raise _not_leased(report_id)
    return repo.get_handoff_report(conn, report_id)


def submit_failure(conn: Connection, *, connector_id: str, report_id: str, failure: ReportJobFailure, now: str) -> Row:
    """러너의 실패 보고 → `failed`(`리포트 작성 실패 — {message}`). 자동 재시도 없음(D12)."""
    _leased_report(conn, connector_id, report_id)
    if not repo.fail_handoff_report(conn, report_id, connector_id=connector_id,
                                    reason=f"리포트 작성 실패 — {failure.message}", now=now):
        raise _not_leased(report_id)
    return repo.get_handoff_report(conn, report_id)


# --- 사람용 행동 (step 8 — API·웹이 같이 쓴다, ADR-0031 결정 3) -------------------------------------


def _member_name(conn: Connection, session_id: str, member_id: str | None) -> str | None:
    member = repo.get_member(conn, session_id, member_id) if member_id else None
    return member["display_name"] if member else None


def _next_stage(conn: Connection, source_task: Row) -> Row | None:
    """같은 업무에서 원인 Task 보다 뒤에 만든 판단 아닌 Task 중 가장 최근(같은 시각이면 나중에 넣은 것)."""
    later = conn.execute(
        "SELECT * FROM tasks WHERE work_item_id = ? AND (created_at > ? OR (created_at = ?"
        " AND rowid > (SELECT rowid FROM tasks WHERE task_id = ?))) ORDER BY created_at DESC, rowid DESC",
        (source_task["work_item_id"], source_task["created_at"], source_task["created_at"], source_task["task_id"]),
    ).fetchall()
    for task in later:
        spec = repo.get_kind(conn, task["session_id"], task["kind"])
        if spec is None or not is_triage_kind(spec):
            return task
    return None


def _stage_agent_id(conn: Connection, task: Row) -> str | None:
    """단계의 Agent — 최근 실행의 Agent, 없으면 고른 Agent, 없으면 선택 기록."""
    runs = repo.list_executions(conn, task["task_id"])
    if runs:
        return runs[-1]["agent_id"]
    if task["chosen_agent_id"]:
        return task["chosen_agent_id"]
    selection = repo.get_selection(conn, task["task_id"])
    return selection.selected_agent_id if selection else None


def party_facts(conn: Connection, report: Row) -> PartyFacts:
    """보낸 사람·받는 사람 재료(D7) — 조회·행동 순간에 모은다. 맞춤 풀이 행도 같은 원인 실행이라 원본과 같다."""
    source = repo.get_execution(conn, report["source_execution_id"])
    source_task = repo.get_task(conn, report["source_task_id"])
    next_task = _next_stage(conn, source_task)
    next_agent = _stage_agent_id(conn, next_task) if next_task is not None else None
    work = conn.execute("SELECT assignee_type, assignee_id FROM work_items WHERE work_item_id = ?",
                        (report["work_item_id"],)).fetchone()
    return PartyFacts(
        source_agent_owner=repo.agent_owner_id(conn, source["agent_id"]),
        next_stage_member=None,  # 단계는 멤버가 맡지 않는다 — 단계 담당은 Agent 이고 그 주인이 받는 사람이다
        next_stage_agent_owner=repo.agent_owner_id(conn, next_agent) if next_agent else None,
        next_stage_has_agent=next_agent is not None,
        work_assignee_member=work["assignee_id"] if work["assignee_type"] == "member" else None,
        work_assignee_agent_owner=(repo.agent_owner_id(conn, work["assignee_id"])
                                   if work["assignee_type"] == "agent" else None),
    )


def _forbidden(message: str) -> WorkActionError:
    return WorkActionError(403, "forbidden", message)


def own_report(conn: Connection, session_id: str, report_id: str) -> Row:
    row = repo.get_handoff_report(conn, report_id)
    if row is None or row["session_id"] != session_id:
        raise WorkActionError(404, "not_found", f"리포트 {report_id}을 찾을 수 없습니다.")
    return row


def _done_original(row: Row) -> None:
    if row["job_type"] != "original":
        raise WorkActionError(409, "not_original", "원본 리포트에서만 할 수 있습니다.")
    if row["status"] != "done":
        raise WorkActionError(409, "report_not_done", "아직 완성된 리포트가 아닙니다.")


def _report_json(conn: Connection, store: ArtifactStore, row: Row) -> HandoffReport:
    return HandoffReport.model_validate_json(repo.read_artifact(conn, store, row["artifact_id"]))


def _next_stage_agent(conn: Connection, report: Row) -> str | None:
    next_task = _next_stage(conn, repo.get_task(conn, report["source_task_id"]))
    return _stage_agent_id(conn, next_task) if next_task is not None else None


def tailor_choices(conn: Connection, *, session_id: str, member_id: str, report: Row) -> list[dict]:
    """그 멤버가 주인인 Agent 중 맞춤 풀이를 할 수 있는 것(D8) — 다음 단계 Agent 가 있으면 맨 앞·기본값."""
    default = _next_stage_agent(conn, report)
    choices = [
        {"agent_id": a["agent_id"], "name": a["name"], "default": a["agent_id"] == default}
        for a in repo.list_session_agents(conn, session_id)
        if repo.agent_owner_id(conn, a["agent_id"]) == member_id and tailor_problem(_agent_facts(conn, a)) is None
    ]
    return sorted(choices, key=lambda c: not c["default"])


def _active_original(conn: Connection, report: Row) -> Row | None:
    latest = repo.latest_original_report(conn, report["source_execution_id"])
    return latest if latest is not None and latest["status"] in repo.HANDOFF_REPORT_ACTIVE else None


def report_view(conn: Connection, store: ArtifactStore, *, report: Row, member_id: str, role: str) -> dict:
    """API JSON·웹 템플릿이 같이 쓰는 보기 묶음. 요청문·확인 가능한 근거 집합 원본·토큰은 싣지 않는다."""
    session_id = report["session_id"]
    original = report["job_type"] == "original"
    execution = repo.get_execution(conn, report["source_execution_id"])
    task = repo.get_task(conn, report["source_task_id"])
    spec = repo.get_kind(conn, session_id, task["kind"])
    outcome = _result_data(conn, store, execution).get("outcome")
    agent = repo.get_agent(conn, report["agent_id"]) if report["agent_id"] else None
    inputs = json.loads(report["inputs_json"])
    done = report["status"] == "done"
    parties = report_parties(party_facts(conn, report))
    decisions = [
        {"item_id": d["item_id"], "answer": d["answer"], "note": d["note"],
         "answered_by": _member_name(conn, session_id, d["member_id"]), "answered_at": d["answered_at"]}
        for d in repo.list_report_decisions(conn, report["report_id"])
    ] if original else []
    actions = []
    choices: list[dict] = []
    if original and done and can_answer(parties, member_id=member_id, role=role):
        actions.append("answer")
    if original and done and can_tailor(parties, member_id=member_id):
        choices = tailor_choices(conn, session_id=session_id, member_id=member_id, report=report)
        if choices:
            actions.append("tailor")
    if (original and _active_original(conn, report) is None
            and can_regenerate(parties, member_id=member_id, role=role)):
        actions.append("regenerate")
    latest = repo.latest_original_report(conn, report["source_execution_id"])
    view = {
        "report_id": report["report_id"],
        "job_type": report["job_type"],
        "status": report["status"],
        "reason": report["reason"],
        "stage": {"title": task["title"], "kind": task["kind"], "kind_label": spec.label if spec else task["kind"]},
        "source": {"attempt_no": execution["attempt_no"], "outcome": outcome if isinstance(outcome, str) else None},
        "agent": {"name": agent["name"]} if agent else None,
        "requested_by": _member_name(conn, session_id, report["requested_by_member_id"]),
        "base_report_id": report["base_report_id"],
        "stale": not original and (latest is None or latest["report_id"] != report["base_report_id"]),
        "created_at": report["created_at"],
        "finished_at": report["finished_at"],
        "report": json.loads(repo.read_artifact(conn, store, report["artifact_id"])) if done else None,
        "evidence_check": json.loads(report["evidence_check_json"]) if done else None,
        "pull_requests": inputs.get("pull_requests", []),
        "pr_evidence_error": inputs.get("pr_evidence_error"),
        "decisions": decisions,
        "parties": {"sender": _member_name(conn, session_id, parties.sender_member_id),
                    "recipient": _member_name(conn, session_id, parties.recipient_member_id)},
        "actions": actions,
    }
    if "tailor" in actions:
        view["tailor_choices"] = choices
    return view


def answer_decision(conn: Connection, store: ArtifactStore, *, session_id: str, member: LoggedIn, report_id: str,
                    item_id: str, answer: str, note: str | None, now: str) -> Row:
    """결정 답(D13) — 받는 사람·관리자, 완성된 원본의 `to_confirm` 항목만. 마지막 답이 유효(덮어쓰기)."""
    row = own_report(conn, session_id, report_id)
    if not can_answer(report_parties(party_facts(conn, row)), member_id=member.member_id, role=member.role):
        raise _forbidden("받는 사람이나 관리자만 답할 수 있습니다.")
    _done_original(row)
    if item_id not in {c.id for c in _report_json(conn, store, row).sections.to_confirm}:
        raise WorkActionError(404, "not_found", f"결정 항목 {item_id}을 찾을 수 없습니다.")
    try:
        repo.answer_report_decision(conn, report_id=report_id, item_id=item_id, answer=answer, note=note,
                                    member_id=member.member_id, now=now)
    except ValueError as exc:
        if str(exc) == "note":
            raise WorkActionError(422, "invalid_field", "한 줄 이유를 1~500자로 적어 주세요.", "note") from None
        raise WorkActionError(422, "invalid_field", "answer 는 confirmed·changed 중 하나입니다.", "answer") from None
    return repo.get_handoff_report(conn, report_id)


def request_tailored(conn: Connection, store: ArtifactStore, *, session_id: str, member: LoggedIn, report_id: str,
                     agent_id: str, now: str) -> tuple[Row, bool]:
    """맞춤 풀이(D8) — 받는 사람이 자기 Agent 로. 진행 중이면 그 행(False). 근거는 원본 주장만(`claim:<id>`).
    할 수 없는 Agent(API 연결형·러너 미지원 등)는 주인과 무관하게 422 + 사유 — API 연결형에는 주인이 없다."""
    row = own_report(conn, session_id, report_id)
    _done_original(row)
    if not can_tailor(report_parties(party_facts(conn, row)), member_id=member.member_id):
        raise _forbidden("받는 사람만 맞춤 풀이를 요청할 수 있습니다.")
    agent = repo.get_agent(conn, agent_id)
    if agent is None or not repo.is_session_agent(conn, session_id, agent_id):
        raise _forbidden("내 에이전트만 고를 수 있습니다.")
    problem = tailor_problem(_agent_facts(conn, agent))
    if problem is not None:
        raise WorkActionError(422, "tailor_unavailable", problem)
    if repo.agent_owner_id(conn, agent_id) != member.member_id:
        raise _forbidden("내 에이전트만 고를 수 있습니다.")
    original = _report_json(conn, store, row)
    work = conn.execute("SELECT key_number, title FROM work_items WHERE work_item_id = ?",
                        (row["work_item_id"],)).fetchone()
    next_task = _next_stage(conn, repo.get_task(conn, row["source_task_id"]))
    next_spec = repo.get_kind(conn, session_id, next_task["kind"]) if next_task is not None else None
    text = build_tailored_request(TailoredRequestFacts(
        work_key=format_work_key(work["key_number"]), title=work["title"], recipient_name=member.display_name,
        next_kind_label=next_spec.label if next_spec else None, original=original,
    ))
    inputs = {
        "local_registration_id": agent["local_registration_id"], "checkout_commit": None, "base_commit": None,
        "request": text,
        "evidence": {"artifacts": [], "commits": [], "pulls": [], "source_work": False, "verification": False,
                     "claims": [c.id for c in original.sections.claims()]},
        "pull_requests": [], "pr_evidence_error": None,
    }
    return repo.create_handoff_report(
        conn, session_id=session_id, work_item_id=row["work_item_id"], source_execution_id=row["source_execution_id"],
        source_task_id=row["source_task_id"], job_type="tailored", agent_id=agent_id,
        connector_id=agent["connector_id"], inputs=inputs, now=now, base_report_id=report_id,
        requested_by_member_id=member.member_id)


def regenerate(conn: Connection, store: ArtifactStore, settings: Settings,
               github_for: Callable[[GitHubSourceConfig], GitHubClient | None], *, session_id: str, member: LoggedIn,
               report_id: str, now: str) -> Row:
    """다시 만들기(D12) — 보낸 사람·받는 사람·관리자. 진행 중 원본이 있으면 그 행, 아니면 같은 원인 실행·같은 Agent 로
    새 원본(PR 근거 다시 수집 — 불가 사유면 바로 `unavailable`)."""
    row = own_report(conn, session_id, report_id)
    if row["job_type"] != "original":
        raise WorkActionError(409, "not_original", "원본 리포트에서만 할 수 있습니다.")
    parties = report_parties(party_facts(conn, row))
    if not can_regenerate(parties, member_id=member.member_id, role=member.role):
        raise _forbidden("보낸 사람·받는 사람·관리자만 다시 만들 수 있습니다.")
    active = _active_original(conn, row)
    if active is not None:
        return active
    return _new_original(conn, store, github_for, execution_id=row["source_execution_id"], now=now)


def stage_reports(conn: Connection, store: ArtifactStore, *, work_item_id: str, member_id: str, role: str) -> dict:
    """업무의 리포트 — 단계(원인 Task)마다 최신 원본과 이전 원본(최근순), 맞춤 풀이는 (원본, 요청 멤버)마다 최신."""
    rows = repo.list_work_reports(conn, work_item_id)
    stages: dict[str, list[Row]] = {}
    tailored: dict[tuple[str, str], Row] = {}
    for row in rows:
        if row["job_type"] == "original":
            stages.setdefault(row["source_task_id"], []).append(row)
        else:
            tailored[(row["base_report_id"], row["requested_by_member_id"])] = row
    tasks = {t["task_id"]: t for t in conn.execute(
        "SELECT t.task_id, t.title, t.kind, t.session_id FROM tasks t WHERE t.work_item_id = ?"
        " ORDER BY t.created_at, t.rowid", (work_item_id,))}

    def _view(row: Row) -> dict:
        return report_view(conn, store, report=row, member_id=member_id, role=role)

    result = []
    for task_id, task in tasks.items():
        if task_id not in stages:
            continue
        spec = repo.get_kind(conn, task["session_id"], task["kind"])
        originals = stages[task_id]
        result.append({"title": task["title"], "kind": task["kind"], "kind_label": spec.label if spec else task["kind"],
                       "latest": _view(originals[-1]), "previous": [_view(r) for r in reversed(originals[:-1])]})
    return {"stages": result, "tailored": [_view(r) for r in tailored.values()]}
