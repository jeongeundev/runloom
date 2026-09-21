"""로컬 도구 공통 흐름 — 도구(Codex·Claude)가 worktree 를 고친 뒤의 판정·보존은 도구와 무관하다.

`LocalToolAdapter.run` 이 하는 일 (ARCHITECTURE "코드 수정 결과", "Codex와 worktree"):
- 로컬 등록·검증 프로필·기준 커밋 확인 → worktree 준비 → `launch` → 원시 stdout/stderr 보존(마스킹)
- 변경 유무 확인 → 결과 커밋 → `test_log_before`(결과 커밋의 새 테스트만 기준 커밋의 깨끗한 체크아웃에 넣고 실행)
  → `test_log_after`(worktree) → `verification_log`·`report_output`(결과 커밋의 깨끗한 체크아웃) → diff → outcome
- 도구의 말을 믿지 않는다: 변경 없음·재현 테스트 없음이면 `needs_information`, 검증은 별도 체크아웃에서 다시 실행한다.

하위 클래스는 `tool_name`·`raw_kinds` 와 `launch`(프로세스를 띄우고 `ToolRun` 을 돌려준다)·`parse_last_message`
(도구의 마지막 구조화 메시지를 `ToolResult` 로), 그리고 읽기 전용 실행의 `launch_readonly`·`parse_generic_message` 만
구현한다. 도구 출력에서 실패 사유(사용량 한도 등)를 읽어야 하면 `classify_failure` 를 덮어쓴다 — 기본은 None(판정 없음)이다.

내장이 아닌 종류(`LocalTarget`, ADR-0009)는 `_run_generic` 이 **읽기 전용**으로 돌린다: worktree·커밋·검증 프로필 없이
인계 디렉터리(handoff dir)에서 `launch_readonly` → 원시 로그 보존 → 인계 파일 변경 확인(`readonly_violation`) →
`{outcome, summary}` 를 읽어 `GenericResult`. `outcome ∉ kind_spec.outcomes` 면 `result_invalid` 로 실패한다.

셸 명령은 로컬 등록의 검증 프로필에서만 온다. 요청·인계 자료·모델 출력에서 명령·경로를 받아 실행하지 않는다.
도구·검증 프로세스의 환경은 `child_env`(= `masking.codex_env` 허용 목록)뿐이다 — 연결 토큰·API 키를 상속하지 않는다.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, Progress, make_meta
from workflow.connector.git_ops import GitError
from workflow.connector.masking import codex_env, mask_secrets
from workflow.connector.prompt import build_generic_prompt, build_prompt
from workflow.contracts.v1 import (
    CONTRACT_VERSION,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionRequest,
    GenericResult,
    LocalTarget,
    Verification,
)

log = logging.getLogger(__name__)

REPORT_PROFILE_ID = "vp-report"
RESPONSE_PLACEHOLDER = "{response}"
RESPONSE_FILE = "response-after@1.json"
NO_REPRO_TEST_LOG = "exit_code=0\n(재현 테스트 없음)\n"
KILL_GRACE_SECONDS = 5

# 도구의 마지막 메시지 스키마. Codex 는 파일(`--output-schema`)로, Claude 는 문자열(`--json-schema`)로 같은 내용을 준다.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "무엇을 어떻게 고쳤는지 한 단락"},
        "outcome": {"type": "string", "enum": ["ready_for_review", "needs_information"]},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string", "description": "남은 사항·확인이 필요한 점. 없으면 빈 문자열"},
    },
    "required": ["summary", "outcome", "files_changed", "notes"],
    "additionalProperties": False,
}


def outcome_note(outcome: str, outcomes: Sequence[str]) -> str:
    """`parse_generic_message` 가 허용 목록 밖 outcome 에 남기는 사유 — 실패 메시지(`result_invalid`)가 된다."""
    return f"허용되지 않은 outcome {outcome!r} — 허용: {', '.join(outcomes)}"


def generic_result_schema(outcomes: Sequence[str]) -> dict:
    """사용자 정의 종류의 마지막 메시지 스키마 — `outcome` 은 `kind_spec.outcomes` 중 하나, `summary` 는 문자열."""
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": list(outcomes)},
            "summary": {"type": "string"},
        },
        "required": ["outcome", "summary"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ToolRun:
    """도구 프로세스 한 번의 원시 결과. 어댑터가 커밋·검증·판정을 하기 전의 재료다."""

    pid: int
    started_at: str
    exit_code: int | None
    stdout: bytes  # 원문 (마스킹 전)
    stderr: bytes
    timed_out: bool
    stopped: bool
    last_message: str | None  # 도구가 낸 마지막 구조화 메시지 원문(JSON 문자열) 또는 None


@dataclass(frozen=True)
class ToolResult:
    """`parse_last_message`·`parse_generic_message` 의 결과 — 도구의 주장이다. 코드 수정은 공통 흐름이 변경·재현 테스트
    유무로 다시 판정하고(outcome 은 `ready_for_review`|`needs_information`), 사용자 정의 종류는 `outcome ∈ kind_spec.outcomes`
    를 확인한다."""

    outcome: str
    summary: str
    parse_note: str | None  # 마지막 메시지를 못 읽었거나 outcome 이 허용 목록 밖일 때 사유


class LocalToolAdapter:
    """로컬 도구 공통 흐름. 하위 클래스는 `tool_name`·`raw_kinds`·`launch`·`parse_last_message` 와 읽기 전용 실행의
    `launch_readonly`·`parse_generic_message` 만 구현한다."""

    tool_name: str  # "codex" | "claude" — 실패 코드 `{tool}_unavailable` 와 로그 이름에 쓴다
    raw_kinds: tuple[str, str]  # ("codex_jsonl", "codex_stderr") 처럼 원시 stdout/stderr 산출물 kind

    def __init__(
        self,
        state_conn,
        *,
        timeout_seconds: int = 1200,
        env_base: Mapping[str, str] = os.environ,
        verification_timeout: int = 300,
    ):
        self._conn = state_conn
        self._timeout = timeout_seconds
        self._env_base = env_base
        self._verification_timeout = verification_timeout

    # --- 하위 클래스가 구현 ---------------------------------------------------------------------

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        """도구 프로세스를 worktree 에서 띄우고 끝날 때까지 기다린다. 시작 직후 `progress(..., runtime_ref=...)` 한 번.
        실행 파일이 없으면 OSError 를 그대로 던진다 (프로세스가 시작되지 않았다)."""
        raise NotImplementedError

    def parse_last_message(self, raw: str | None) -> ToolResult:
        raise NotImplementedError

    def launch_readonly(self, cwd: Path, prompt_text: str, schema: dict, progress: Progress) -> ToolRun:
        """읽기 전용으로 도구를 `cwd`(인계 디렉터리)에서 띄운다. `schema` 는 마지막 메시지의 JSON Schema
        (`generic_result_schema`). 시작 직후 `progress(..., runtime_ref=...)` 한 번. 실행 파일이 없으면 OSError."""
        raise NotImplementedError

    def parse_generic_message(self, raw: str | None, outcomes: Sequence[str]) -> ToolResult:
        """마지막 메시지에서 `{outcome, summary}` 를 읽는다. `outcome ∉ outcomes` 면 `parse_note` 에 사유를 적고 outcome 은
        그대로 둔다 — 공통 흐름이 `result_invalid` 로 실패시킨다. 못 읽으면 outcome 은 빈 문자열."""
        raise NotImplementedError

    def classify_failure(self, run: ToolRun) -> tuple[str, str] | None:
        """도구 출력에서 실패 사유를 읽는 훅 — `(code, message)` 면 공통 흐름이 그 자리에서 `failed` 로 끝낸다
        (`process_stopped` 는 `run.stopped`). 시간 초과는 이 훅보다 먼저 판정한다. 기본은 None (Codex)."""
        return None

    # --- 공통 ------------------------------------------------------------------------------------

    def child_env(self) -> dict[str, str]:
        """도구·검증 프로세스에 넘기는 환경. 허용 목록만 남기고 연결 토큰·API 키·중앙 설정을 제거한다."""
        return codex_env(self._env_base)

    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        target = request.target
        if isinstance(target, LocalTarget):
            return self._run_generic(request, handoff_dir, progress)
        if not isinstance(target, CodeChangeTarget):
            return _failed("unsupported_kind", f"{type(self).__name__} 는 {request.kind} 를 처리하지 않는다")
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
            run = self.launch(worktree, build_prompt(request, handoff_dir, worktree), progress)
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)
        if run.timed_out:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts,
                failed=("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.", run.stopped),
                runtime_ref=runtime_ref,
            )
        failure = self.classify_failure(run)
        if failure is not None:
            code, message = failure
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
            )

        parsed = self.parse_last_message(run.last_message)
        summary = parsed.summary
        base_commit = target.base_commit
        if not git_ops.is_dirty(worktree):
            result = self._result(request, "needs_information", f"변경 없음: {summary}", None, None)
            return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref)

        result_commit = git_ops.commit_all(
            worktree, f"fix({request.task_id}): {_first_line(summary, self.tool_name)[:60]}"
        )
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
        else:
            outcome = parsed.outcome
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
            *raw_artifacts,
        ]
        return AdapterOutput(result=result, artifacts=artifacts, runtime_ref=runtime_ref)

    # --- 사용자 정의 종류 — 읽기 전용 -----------------------------------------------------------------

    def _run_generic(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        """`LocalTarget` 실행. 등록 확인 → 인계 디렉터리에서 `launch_readonly` → 원시 로그 → 시간 초과·`classify_failure`
        → 인계 파일 변경 확인 → `{outcome, summary}` → `GenericResult`. worktree·커밋·검증 프로필은 없다."""
        target = request.target
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        spec = request.kind_spec
        if spec is None:
            return _failed("kind_spec_missing", f"{request.kind} 요청에 kind_spec 이 없다")
        handoff_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot(handoff_dir)

        try:
            run = self.launch_readonly(
                handoff_dir, build_generic_prompt(request, handoff_dir), generic_result_schema(spec.outcomes), progress,
            )
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)

        def failed(code: str, message: str) -> AdapterOutput:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
            )

        if run.timed_out:
            return failed("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.")
        failure = self.classify_failure(run)
        if failure is not None:
            return failed(*failure)
        changed = _changed_files(before, _snapshot(handoff_dir))
        if changed:
            return failed("readonly_violation", f"읽기 전용 실행이 인계 디렉터리의 파일을 바꿨다: {', '.join(changed)}")

        parsed = self.parse_generic_message(run.last_message, spec.outcomes)
        if parsed.outcome not in spec.outcomes:
            return failed("result_invalid", parsed.parse_note or f"허용되지 않은 outcome {parsed.outcome!r}")
        result = GenericResult(
            contract_version=CONTRACT_VERSION, execution_id=request.execution_id, task_id=request.task_id,
            kind=request.kind, outcome=parsed.outcome, summary=parsed.summary, artifact_ids=[],
        )
        return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref)

    def _raw_artifacts(self, run: ToolRun) -> list[tuple]:
        """도구의 원시 stdout/stderr — `raw_kinds` 의 kind 로, 마스킹해서 보존한다."""
        stdout_kind, stderr_kind = self.raw_kinds
        return [
            make_meta(stdout_kind, f"{self.tool_name}.jsonl", _masked(run.stdout), "application/x-ndjson"),
            make_meta(stderr_kind, f"{self.tool_name}-stderr.txt", _masked(run.stderr), "text/plain"),
        ]

    # --- 검증 프로필 ----------------------------------------------------------------------------

    def _run_argv(self, argv: list[str], cwd: Path) -> tuple[int, str, str]:
        """등록된 인자 배열을 그대로 실행. (exit_code, stdout, stderr). 시간 초과는 124, 실행 불가는 127."""
        try:
            proc = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, errors="replace",
                timeout=self._verification_timeout, env=self.child_env(),
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


def communicate_or_stop(
    proc: subprocess.Popen, input_bytes: bytes, timeout: int,
) -> tuple[bytes, bytes, bool, bool]:
    """stdin 을 쓰고 끝까지 기다린다. 시간 초과면 terminate → `KILL_GRACE_SECONDS` → kill.
    `(stdout, stderr, timed_out, stopped)` — stopped 는 프로세스 종료를 실제로 확인했는지다."""
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input_bytes, timeout=timeout)
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
                log.error("pid=%s 를 종료하지 못했다", proc.pid)
        stdout, stderr = proc.communicate()
    return stdout, stderr, timed_out, proc.poll() is not None


def _failed(code: str, message: str) -> AdapterOutput:
    """도구를 띄우기 전의 실패 — 프로세스가 없으므로 process_stopped=True."""
    return AdapterOutput(result=None, failed=(code, message, True))


def _snapshot(root: Path) -> dict[str, str]:
    """인계 디렉터리 안 파일의 상대 경로 → sha256. 읽기 전용 실행 전후를 비교하는 데 쓴다."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """추가·삭제·수정된 파일 이름 (정렬)."""
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def _masked(data: bytes) -> bytes:
    text, _ = mask_secrets(data.decode("utf-8", errors="replace"))
    return text.encode("utf-8")


def _log_text(exit_code: int, stdout: str, stderr: str) -> str:
    return f"exit_code={exit_code}\n{stdout}{stderr}"


def _first_line(text: str, tool_name: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else f"{tool_name} 수정"


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
