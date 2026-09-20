"""Codex CLI 어댑터 — `codex exec` 를 업무 worktree 에서 띄우고 결과를 보존한다 (ADR-0001, ARCHITECTURE "Codex와 worktree").

Codex 의 말을 믿지 않는다. 이 어댑터가 직접 확인·기록하는 것:
- worktree 준비 → 프롬프트를 stdin 으로 → JSONL·stderr 수집(마스킹) → 변경 유무 확인 → 결과 커밋
- `test_log_before`: 결과 커밋의 새 테스트 파일만 기준 커밋의 깨끗한 체크아웃에 넣고 검증 프로필 실행
- `test_log_after`: worktree 에서 검증 프로필 실행
- `verification_log`·`report_output`: 결과 커밋의 깨끗한 체크아웃에서 검증 프로필과 보고서 프로필(`vp-report`) 실행.
  검사한 코드와 보존한 커밋이 같음을 보장한다.
셸 명령은 로컬 등록의 검증 프로필에서만 온다. 요청·인계 자료·모델 출력에서 명령·경로를 받아 실행하지 않는다.
Codex 프로세스 환경은 `masking.codex_env` 허용 목록뿐이다.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, Progress, make_meta
from workflow.connector.git_ops import GitError
from workflow.connector.masking import codex_env, mask_secrets
from workflow.connector.prompt import CODEX_RESULT_SCHEMA, build_prompt
from workflow.contracts.v1 import (
    CONTRACT_VERSION,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionRequest,
    Verification,
)

log = logging.getLogger(__name__)

REPORT_PROFILE_ID = "vp-report"
RESPONSE_PLACEHOLDER = "{response}"
RESPONSE_FILE = "response-after@1.json"
NO_REPRO_TEST_LOG = "exit_code=0\n(재현 테스트 없음)\n"
KILL_GRACE_SECONDS = 5


@dataclass(frozen=True)
class CodexRun:
    """Codex 프로세스 한 번의 원시 결과."""

    exit_code: int
    jsonl: bytes
    stderr: bytes
    last_message: str | None
    started_at: str
    ended_at: str
    pid: int
    stopped: bool
    timed_out: bool


class CodexLastMessage(BaseModel):
    """`--output-last-message` 파일. 스키마(`prompt.CODEX_RESULT_SCHEMA`)를 따르지 않아도 읽을 수 있는 만큼 읽는다."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    outcome: Literal["ready_for_review", "needs_information"] = "needs_information"
    files_changed: list[str] = []
    notes: str = ""


def _masked(data: bytes) -> bytes:
    text, _ = mask_secrets(data.decode("utf-8", errors="replace"))
    return text.encode("utf-8")


def _log_text(exit_code: int, stdout: str, stderr: str) -> str:
    return f"exit_code={exit_code}\n{stdout}{stderr}"


def _parse_last_message(raw: str | None) -> tuple[CodexLastMessage, str | None]:
    """(파싱 결과, 실패 사유). 실패해도 계속 진행하고 사유를 요약에 남긴다."""
    if raw is None:
        return CodexLastMessage(), "파일 없음"
    try:
        return CodexLastMessage.model_validate_json(raw), None
    except ValidationError as exc:
        return CodexLastMessage(), f"스키마 불일치: {exc.error_count()}건"


class CodexAdapter:
    def __init__(
        self,
        state_conn,
        *,
        codex_bin: str = "codex",
        timeout_seconds: int = 1200,
        sandbox: str = "workspace-write",
        approval_policy: str = "never",
        env_base: Mapping[str, str] = os.environ,
        verification_timeout: int = 300,
    ):
        self._conn = state_conn
        self._codex_bin = codex_bin
        self._timeout = timeout_seconds
        self._sandbox = sandbox
        self._approval_policy = approval_policy
        self._env_base = env_base
        self._verification_timeout = verification_timeout

    def build_argv(self, worktree: Path, schema_path: Path, last_message_path: Path) -> list[str]:
        """고정 인자만. 프롬프트는 stdin(`-`)으로 주고, 사용자·근거 문자열을 인자에 넣지 않는다."""
        return [
            self._codex_bin, "exec", "--json", "-C", str(worktree),
            "--sandbox", self._sandbox,
            "-c", f'approval_policy="{self._approval_policy}"',
            "--output-schema", str(schema_path),
            "--output-last-message", str(last_message_path),
            "-",
        ]

    # --- 실행 ---------------------------------------------------------------------------------

    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        target = request.target
        if not isinstance(target, CodeChangeTarget):
            return _failed("unsupported_kind", f"CodexAdapter 는 {request.kind} 를 처리하지 않는다")
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        profiles: dict[str, list[str]] = registration["verification_profiles"]
        profile = profiles.get(target.verification_profile_id)
        if profile is None:
            return _failed(
                "verification_profile_missing",
                f"검증 프로필 {target.verification_profile_id} 이 등록 {target.local_registration_id} 에 없다",
            )
        repo = Path(registration["repo_path"])
        try:
            worktree = git_ops.ensure_worktree(repo, request.task_id, target.base_commit)
        except GitError as exc:
            return _failed("base_commit_missing", str(exc))

        try:
            run = self._launch(worktree, build_prompt(request, handoff_dir, worktree), progress)
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed("codex_unavailable", f"{self._codex_bin}: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        codex_artifacts = [
            make_meta("codex_jsonl", "codex.jsonl", _masked(run.jsonl), "application/x-ndjson"),
            make_meta("codex_stderr", "codex-stderr.txt", _masked(run.stderr), "text/plain"),
        ]
        if run.timed_out:
            return AdapterOutput(
                result=None, artifacts=codex_artifacts,
                failed=("timeout", f"Codex 실행이 {self._timeout}초를 초과해 종료했습니다.", run.stopped),
                runtime_ref=runtime_ref,
            )

        last, parse_note = _parse_last_message(run.last_message)
        summary = f"Codex 마지막 메시지를 읽지 못함 ({parse_note})" if parse_note else (last.summary or "(요약 없음)")
        base_commit = target.base_commit
        if not git_ops.is_dirty(worktree):
            result = self._result(request, "needs_information", f"변경 없음: {summary}", None, None)
            return AdapterOutput(result=result, artifacts=codex_artifacts, runtime_ref=runtime_ref)

        result_commit = git_ops.commit_all(worktree, f"fix({request.task_id}): {_first_line(summary)[:60]}")
        progress(f"결과 커밋 {result_commit[:12]} (task/{request.task_id})")

        test_files = git_ops.changed_test_files(repo, base_commit, result_commit)
        before = self._test_before(repo, worktree, base_commit, test_files, profile)
        progress(f"수정 전 재현 테스트 {before.splitlines()[0]} (테스트 파일 {len(test_files)}개)")
        after_code, after_out, after_err = self._run_argv(profile, worktree)
        progress(f"수정 후 테스트 exit_code={after_code}")

        def in_result_checkout(dest: Path) -> tuple[tuple[int, str, str], str]:
            return self._run_argv(profile, dest), self._report(dest, profiles.get(REPORT_PROFILE_ID), handoff_dir)

        (verify_code, verify_out, verify_err), report = _in_clean_checkout(repo, result_commit, in_result_checkout)
        progress(f"검증 프로필 {target.verification_profile_id} exit_code={verify_code} @ {result_commit[:12]}")
        diff = git_ops.diff_text(repo, base_commit, result_commit)

        if not test_files:
            outcome, summary = "needs_information", f"재현 테스트 없음: {summary}"
        elif parse_note or last.outcome == "needs_information":
            outcome = "needs_information"
        else:
            outcome = "ready_for_review"
        verification = Verification(
            profile_id=target.verification_profile_id, result_commit=result_commit, exit_code=verify_code,
            log_artifact_id="verification_log",
        )
        result = self._result(request, outcome, summary, result_commit, verification)
        artifacts = [
            make_meta("diff", "fix.diff", diff.encode(), "text/x-diff"),
            make_meta("test_log_before", "pytest-before.txt", before.encode(), "text/plain"),
            make_meta("test_log_after", "pytest-after.txt", _log_text(after_code, after_out, after_err).encode(),
                      "text/plain"),
            make_meta("verification_log", "verification.txt",
                      _log_text(verify_code, verify_out, verify_err).encode(), "text/plain"),
            make_meta("report_output", "report.txt", report.encode(), "text/plain"),
            *codex_artifacts,
        ]
        return AdapterOutput(result=result, artifacts=artifacts, runtime_ref=runtime_ref)

    def _launch(self, worktree: Path, prompt_text: str, progress: Progress) -> CodexRun:
        with tempfile.TemporaryDirectory(prefix="workflow-codex-") as tmp:
            schema_path = Path(tmp) / "codex_result_schema.json"
            schema_path.write_text(json.dumps(CODEX_RESULT_SCHEMA, ensure_ascii=False, indent=2))
            last_message_path = Path(tmp) / "last_message.json"
            argv = self.build_argv(worktree, schema_path, last_message_path)
            started_at = state.utc_now()
            proc = subprocess.Popen(
                argv, cwd=worktree, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=codex_env(self._env_base),
            )
            progress(f"Codex 실행 시작 pid={proc.pid}", runtime_ref=f"pid:{proc.pid};start:{started_at}")
            timed_out = False
            try:
                stdout, stderr = proc.communicate(prompt_text.encode("utf-8"), timeout=self._timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=KILL_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        log.error("Codex pid=%s 를 종료하지 못했다", proc.pid)
                stdout, stderr = proc.communicate()
            ended_at = state.utc_now()
            stopped = proc.poll() is not None
            progress(f"Codex 종료 exit={proc.returncode}{' (시간 초과)' if timed_out else ''}")
            last_message = last_message_path.read_text(encoding="utf-8") if last_message_path.is_file() else None
            return CodexRun(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                jsonl=stdout, stderr=stderr, last_message=last_message,
                started_at=started_at, ended_at=ended_at, pid=proc.pid, stopped=stopped, timed_out=timed_out,
            )

    # --- 검증 프로필 ----------------------------------------------------------------------------

    def _run_argv(self, argv: list[str], cwd: Path) -> tuple[int, str, str]:
        """등록된 인자 배열을 그대로 실행. (exit_code, stdout, stderr). 시간 초과는 124, 실행 불가는 127."""
        try:
            proc = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, errors="replace",
                timeout=self._verification_timeout, env=codex_env(self._env_base),
            )
        except subprocess.TimeoutExpired as exc:
            return 124, _text(exc.stdout), f"(검증 시간 초과 {self._verification_timeout}초)\n{_text(exc.stderr)}"
        except OSError as exc:
            return 127, "", f"(실행 불가) {exc}\n"
        return proc.returncode, proc.stdout, proc.stderr

    def _test_before(
        self, repo: Path, worktree: Path, base_commit: str, test_files: list[str], profile: list[str]
    ) -> str:
        if not test_files:
            return NO_REPRO_TEST_LOG

        def inside(dest: Path) -> str:
            for rel in test_files:  # git 이 보고한 저장소 상대 경로 — 결과 커밋의 테스트만 기준 코드 위에 놓는다
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(worktree / rel, dest / rel)
            return _log_text(*self._run_argv(profile, dest))

        return _in_clean_checkout(repo, base_commit, inside)

    def _report(self, checkout: Path, report_profile: list[str] | None, handoff_dir: Path) -> str:
        if report_profile is None:
            return f"exit_code=2\n(검증 프로필 {REPORT_PROFILE_ID} 이 등록되지 않아 보고서를 생성하지 못함)\n"
        response = handoff_dir / RESPONSE_FILE
        if not response.is_file():
            return f"exit_code=2\n(인계 자료에 {RESPONSE_FILE} 이 없어 보고서를 생성하지 못함)\n"
        argv = [str(response) if arg == RESPONSE_PLACEHOLDER else arg for arg in report_profile]
        code, stdout, stderr = self._run_argv(argv, checkout)
        return stdout if code == 0 else _log_text(code, stdout, stderr)

    @staticmethod
    def _result(
        request: ExecutionRequest, outcome: str, summary: str, result_commit: str | None,
        verification: Verification | None,
    ) -> CodeChangeResult:
        return CodeChangeResult(
            contract_version=CONTRACT_VERSION, execution_id=request.execution_id, task_id=request.task_id,
            outcome=outcome, summary=summary, base_commit=request.target.base_commit,
            result_commit=result_commit, artifact_ids=[], verification=verification,
        )


def _failed(code: str, message: str) -> AdapterOutput:
    """Codex 를 띄우기 전의 실패 — 프로세스가 없으므로 process_stopped=True."""
    return AdapterOutput(result=None, failed=(code, message, True))


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else "Codex 수정"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _in_clean_checkout[T](repo: Path, commit: str, fn: Callable[[Path], T]) -> T:
    """`commit` 의 깨끗한 임시 체크아웃에서 fn 을 실행하고 반드시 정리한다. 업무 worktree 는 건드리지 않는다."""
    with tempfile.TemporaryDirectory(prefix="workflow-checkout-") as tmp:
        dest = Path(tmp) / "checkout"
        git_ops.export_checkout(repo, commit, dest)
        try:
            return fn(dest)
        finally:
            try:
                git_ops.remove_worktree(repo, dest)
            except GitError as exc:
                log.warning("임시 체크아웃 정리 실패 (%s): %s", dest, exc)
