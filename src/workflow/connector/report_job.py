"""인계 리포트 작업 — claim 이 `ReportJob` 을 주면 그 Agent 로 리포트 JSON 을 써 제출한다 (ADR-0033, ARCHITECTURE
"리포트 작업 실행 — 러너").

- 어댑터는 `job.local_registration_id` 로 찾은 로컬 등록의 `tool` 로 고른다. 등록 없음 `registration_missing`, 그 도구의
  `ReportAdapter` 없음 `adapter_missing`. 작업의 어느 칸도 명령·경로로 쓰지 않는다 — 등록 키·커밋 ID 일 뿐이다.
- original: `checkout_commit` 이 등록 저장소에 있으면 그 커밋의 깨끗한 임시 체크아웃에서(판단과 같은 방식), 로컬 근거
  (커밋 목록·diff stat)를 모아 돌린다. 끝나면 HEAD 가 그대로이고 변경이 없어야 한다(D18) — 아니면 `readonly_violation`.
  체크아웃이 없으면(커밋 null·저장소에 없음) 빈 임시 디렉터리에서 `로컬 근거 없음 — 사유` 로 돌린다.
- tailored: 체크아웃 없이 빈 임시 디렉터리에서, 로컬 근거 없이.
- 결과는 `HandoffReport` 로 검사(`result_invalid`)한 뒤 비밀값을 가리고(`mask_secrets` + 등록 env) 제출한다. 실패는 실패 보고.
  늦은 제출(409)·거부(422)는 로그만 남긴다. 로컬 `state` 에 실행으로 기록하지 않는다 — 이벤트·seq 가 없다.
"""

import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from workflow.connector import git_ops, state
from workflow.connector.adapter import Progress, ReportAdapter, ReportOutput
from workflow.connector.client import CentralClient, CentralError, Unauthenticated
from workflow.connector.git_ops import GitError
from workflow.connector.local_tool import HANDOFF_REPORT_OUTPUT_SCHEMA, _in_clean_checkout
from workflow.connector.masking import mask_secrets
from workflow.connector.prompt import build_report_prompt
from workflow.contracts.v1 import CONTRACT_VERSION, HandoffReport, ReportJob, ReportJobFailure

log = logging.getLogger(__name__)

LOG_MAX_LINES = 50
DIFF_STAT_MAX_LINES = 200
FAILURE_MESSAGE_MAX = 500


class _ReadonlyViolation(Exception):
    pass


def run_report_job(
    client: CentralClient, state_conn, adapters: Mapping[str, object], job: ReportJob, *, handoff_root: Path,
) -> None:
    registration = state.get_registration(state_conn, job.local_registration_id)
    if registration is None:
        _fail(client, job, "registration_missing", f"로컬 등록 {job.local_registration_id} 이 없다", {})
        return
    env = registration["env"]
    adapter = adapters.get(registration["tool"])
    if not isinstance(adapter, ReportAdapter):
        _fail(client, job, "adapter_missing",
              f"등록 {job.local_registration_id} 의 도구 {registration['tool']} 에 리포트 어댑터가 없다", env)
        return

    def progress(message: str, *, runtime_ref: str | None = None) -> None:
        log.info("%s 리포트: %s", job.report_id, mask_secrets(message, env)[0][:FAILURE_MESSAGE_MAX])

    try:
        output = _produce(adapter, job, Path(registration["repo_path"]), env, handoff_root, progress)
    except _ReadonlyViolation as exc:
        output = ReportOutput(None, ("readonly_violation", str(exc)))
    except GitError as exc:
        output = ReportOutput(None, ("checkout_failed", f"체크아웃 실패: {exc}"))
    except Exception as exc:  # 어댑터 예외 — 리포트 작업만 실패시키고 러너는 계속 돈다
        log.exception("%s: 리포트 어댑터 예외", job.report_id)
        output = ReportOutput(None, ("adapter_error", f"{type(exc).__name__}: {exc}"))

    if output.failed is not None:
        _fail(client, job, *output.failed, env)
        return
    try:
        report = HandoffReport.model_validate({
            **output.data, "contract_version": CONTRACT_VERSION, "report_id": job.report_id, "job_type": job.job_type,
        })
    except (ValidationError, TypeError) as exc:
        _fail(client, job, "result_invalid", f"리포트 형식 오류: {_first_error(exc)}", env)
        return
    body = mask_secrets(report.model_dump_json(), env)[0].encode("utf-8")
    _send(lambda: client.submit_report(job.report_id, body), job, "제출")


def _produce(
    adapter: ReportAdapter, job: ReportJob, repo: Path, env: Mapping[str, str], handoff_root: Path,
    progress: Progress,
) -> ReportOutput:
    commit = job.checkout_commit
    if job.job_type == "original" and commit is not None and repo.is_dir() and git_ops.has_commit(repo, commit):
        def inside(checkout: Path) -> ReportOutput:
            prompt = build_report_prompt(job, _local_evidence(checkout, job))
            output = adapter.run_report(checkout, prompt, HANDOFF_REPORT_OUTPUT_SCHEMA, progress, env=env)
            head = git_ops.head_sha(checkout)
            if head != commit:
                raise _ReadonlyViolation(f"리포트 체크아웃의 HEAD 가 {head[:12]} 로 움직였다 (checkout_commit {commit[:12]})")
            if git_ops.is_dirty(checkout):
                raise _ReadonlyViolation("리포트 작업이 체크아웃의 파일을 바꿨다")
            return output

        return _in_clean_checkout(repo, commit, inside)

    local = None
    if job.job_type == "original":
        reason = "결과 커밋 없음" if commit is None else f"커밋 {commit[:12]} 이 등록 저장소에 없음"
        local = f"로컬 근거 없음 — {reason}"
    handoff_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="report-", dir=handoff_root) as tmp:
        cwd = Path(tmp)
        output = adapter.run_report(cwd, build_report_prompt(job, local), HANDOFF_REPORT_OUTPUT_SCHEMA, progress,
                                    env=env)
        if any(cwd.iterdir()):
            raise _ReadonlyViolation("리포트 작업이 작업 디렉터리에 파일을 만들었다")
        return output


def _local_evidence(checkout: Path, job: ReportJob) -> str:
    """체크아웃에서 모은 커밋 목록·diff stat. 기준 커밋이 없거나 저장소에 없으면 결과 커밋 한 줄만."""
    base = job.base_commit if job.base_commit is not None and git_ops.has_commit(checkout, job.base_commit) else None
    lines = ["### 커밋" + (f" ({base[:12]}..{job.checkout_commit[:12]})" if base else ""),
             *git_ops.log_lines(checkout, job.checkout_commit, base=base, limit=LOG_MAX_LINES)]
    if base is not None:
        lines += ["", "### diff stat", *git_ops.diff_stat(checkout, base, job.checkout_commit,
                                                           limit=DIFF_STAT_MAX_LINES)]
    elif job.base_commit is not None:
        lines += ["", f"(기준 커밋 {job.base_commit[:12]} 이 등록 저장소에 없어 diff stat 없음)"]
    return "\n".join(lines)


def _fail(client: CentralClient, job: ReportJob, code: str, message: str, env: Mapping[str, str]) -> None:
    message = mask_secrets(message, env)[0][:FAILURE_MESSAGE_MAX].strip() or code
    log.warning("%s: 리포트 작업 실패 %s — %s", job.report_id, code, message)
    failure = ReportJobFailure(contract_version=CONTRACT_VERSION, failure_code=code, message=message)
    _send(lambda: client.fail_report(job.report_id, failure), job, "실패 보고")


def _send(call, job: ReportJob, what: str) -> None:
    """409(늦은 제출)·422 등 중앙의 판단은 로그만 — 다시 보내도 같다. 401·연결 오류는 tick 이 처리한다."""
    try:
        call()
    except Unauthenticated:
        raise
    except CentralError as exc:
        log.warning("%s: 리포트 %s 거부 — %s", job.report_id, what, exc)


def _first_error(exc: Exception) -> str:
    if not isinstance(exc, ValidationError):
        return str(exc)
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]

