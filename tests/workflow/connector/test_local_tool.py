"""local_tool — 로컬 도구 공통 흐름 (`LocalToolAdapter`). 실제 도구는 띄우지 않는다.

가짜 하위 클래스 `ScriptedTool` 이 `launch` 를 대본으로 대신한다: worktree 를 고치고(또는 고치지 않고) `ToolRun` 을
돌려주거나 OSError 를 던진다. 공통 흐름의 판정 규칙(변경 없음·재현 테스트 없음 → `needs_information`, 검증은 깨끗한
체크아웃에서, 원시 산출물 kind 는 `raw_kinds`, stdout/stderr 마스킹, 자식 env 에 비밀값 없음)만 검사한다.
Codex 어댑터 자체는 `test_codex.py` 가 가짜 실행 파일로 검사한다.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from workflow.connector import git_ops, state
from workflow.connector.adapter import Progress
from workflow.connector.local_tool import (
    RESULT_SCHEMA,
    LocalToolAdapter,
    ToolResult,
    ToolRun,
    generic_result_schema,
)
from workflow.contracts.v1 import ExecutionRequest, GenericResult

from .conftest import REVIEW_SPEC, make_local_request, make_request

WFC = "wfc_" + "a" * 43
SK = "sk-" + "b" * 20

# check.py — 저장소에 커밋되는 고정 검증 스크립트. 재현 테스트가 있고 소스가 안 고쳐졌으면 1, 그 외 0.
# `_test_before` 가 결과 커밋의 테스트 파일만 기준 코드 위에 놓으면 1(재현 실패), 수정 후 worktree 에서는 0 이어야 한다.
# 재작업 시도용 `tests/test_round2.py` 는 소스에 "round2" 가 있어야 통과한다.
_CHECK = """\
import pathlib, sys
src = pathlib.Path("src.py").read_text()
needs = {"tests/test_repro.py": "fixed", "tests/test_round2.py": "round2"}
broken = [t for t, word in needs.items() if pathlib.Path(t).exists() and word not in src]
print(f"broken={broken}")
sys.exit(1 if broken else 0)
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini-repo"
    repo.mkdir()
    (repo / "src.py").write_text("VALUE = 'original'\n")
    (repo / "check.py").write_text(_CHECK)
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def handoff(tmp_path: Path) -> Path:
    handoff = tmp_path / "fix-daily-0920.handoff"
    handoff.mkdir()
    (handoff / "manifest.json").write_text("{}")
    return handoff


def register(state_conn, repo: Path) -> str:
    base = git_ops.head_sha(repo)
    state.save_registration(state_conn, {
        "local_registration_id": "local-demo-report",
        "repo_path": str(repo),
        "tool": "codex",
        "repository_id": "demo-report-repo",
        "base_commit": base,
        "verification_profiles": {"vp-pytest": [sys.executable, "check.py"]},
    })
    return base


def request_for(base_commit: str):
    request = make_request()
    target = {**request.target.model_dump(), "base_commit": base_commit}
    return request.model_copy(update={"target": type(request.target).model_validate(target)})


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, message: str, *, runtime_ref: str | None = None) -> None:
        self.calls.append((message, runtime_ref))

    @property
    def runtime_refs(self) -> list[str]:
        return [ref for _, ref in self.calls if ref is not None]


def _run(*, stdout: bytes = b"", stderr: bytes = b"", last_message: str | None = None,
         timed_out: bool = False, stopped: bool = True) -> ToolRun:
    return ToolRun(
        pid=4242, started_at="2026-09-20T01:00:00Z", exit_code=None if timed_out else 0,
        stdout=stdout, stderr=stderr, timed_out=timed_out, stopped=stopped, last_message=last_message,
    )


def _message(outcome: str = "ready_for_review", summary: str = "src.py 를 고쳤다") -> str:
    return json.dumps({"summary": summary, "outcome": outcome}, ensure_ascii=False)


class ScriptedTool(LocalToolAdapter):
    """가짜 로컬 도구. `script(cwd)` 가 worktree(또는 읽기 전용 실행의 인계 디렉터리)를 바꾸고 ToolRun 을 돌려준다
    (또는 예외를 던진다). `launch` 와 `launch_readonly` 가 같은 대본을 쓴다."""

    tool_name = "fake"
    raw_kinds = ("claude_jsonl", "claude_stderr")  # codex_* 가 아닌 kind 로 공통 흐름이 raw_kinds 를 따르는지 본다

    def __init__(self, state_conn, script: Callable[[Path], ToolRun], **kwargs):
        super().__init__(state_conn, verification_timeout=60, **kwargs)
        self.script = script
        self.launched_with: list[tuple[Path, str]] = []
        self.launched_readonly_with: list[tuple[Path, str, dict]] = []

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        self.launched_with.append((worktree, prompt_text))
        progress("fake 시작", runtime_ref="pid:4242;start:2026-09-20T01:00:00Z")
        return self.script(worktree)

    def launch_readonly(self, cwd: Path, prompt_text: str, schema: dict, progress: Progress) -> ToolRun:
        self.launched_readonly_with.append((cwd, prompt_text, schema))
        progress("fake 시작", runtime_ref="pid:4242;start:2026-09-20T01:00:00Z")
        return self.script(cwd)

    def parse_last_message(self, raw: str | None) -> ToolResult:
        if raw is None:
            return ToolResult("needs_information", "fake 마지막 메시지를 읽지 못함 (파일 없음)", "파일 없음")
        data = json.loads(raw)
        return ToolResult(data["outcome"], data["summary"], None)

    def parse_generic_message(self, raw: str | None, outcomes) -> ToolResult:
        if raw is None:
            return ToolResult("", "fake 마지막 메시지를 읽지 못함 (파일 없음)", "파일 없음")
        data = json.loads(raw)
        note = None if data["outcome"] in outcomes else f"허용되지 않은 outcome {data['outcome']!r}"
        return ToolResult(data["outcome"], data["summary"], note)


def fix_and_add_test(worktree: Path) -> ToolRun:
    (worktree / "src.py").write_text("VALUE = 'fixed'\n")
    (worktree / "tests" / "test_repro.py").write_text("def test_repro():\n    assert True\n")
    return _run(stdout=b'{"type":"turn.completed"}\n', stderr=b"fake: done\n", last_message=_message())


def fix_only(worktree: Path) -> ToolRun:
    (worktree / "src.py").write_text("VALUE = 'fixed'\n")
    return _run(last_message=_message())


def by_kind(output) -> dict[str, bytes]:
    return {meta.kind: data for meta, data in output.artifacts}


# --- 정상 ---------------------------------------------------------------------------------


def test_full_flow_commits_verifies_in_clean_checkout_and_uses_raw_kinds(state_conn, repo, handoff):
    base = register(state_conn, repo)
    request = request_for(base)
    adapter = ScriptedTool(state_conn, fix_and_add_test)
    progress = Recorder()

    output = adapter.run(request, handoff, progress)

    assert output.failed is None, output.failed
    result = output.result
    assert result.outcome == "ready_for_review" and result.summary == "src.py 를 고쳤다"
    assert result.base_commit == base and result.result_commit != base
    assert [meta.kind for meta, _ in output.artifacts] == [
        "diff", "test_log_before", "test_log_after", "verification_log", "report_output",
        "claude_jsonl", "claude_stderr",
    ]
    names = {meta.kind: meta.name for meta, _ in output.artifacts}
    assert names["claude_jsonl"] == "fake.jsonl" and names["claude_stderr"] == "fake-stderr.txt"
    artifacts = by_kind(output)
    assert artifacts["test_log_before"].decode().splitlines()[0] == "exit_code=1"  # 기준 코드 + 새 테스트 → 재현 실패
    assert artifacts["test_log_after"].decode().splitlines()[0] == "exit_code=0"
    assert artifacts["verification_log"].decode().splitlines()[0] == "exit_code=0"
    assert "vp-report" in artifacts["report_output"].decode()  # 보고서 프로필 미등록 사유가 남는다
    assert "src.py" in artifacts["diff"].decode() and "test_repro.py" in artifacts["diff"].decode()
    assert artifacts["claude_jsonl"] == b'{"type":"turn.completed"}\n' and artifacts["claude_stderr"] == b"fake: done\n"
    assert result.verification.profile_id == "vp-pytest"
    assert result.verification.result_commit == result.result_commit and result.verification.exit_code == 0
    assert result.verification.log_artifact_id == "verification_log" and result.artifact_ids == []
    # launch 는 업무 worktree 에서 한 번, 프롬프트에 요청 원문이 들어간다
    (worktree, prompt_text), = adapter.launched_with
    assert worktree == repo.parent / "mini-repo-worktrees" / request.task_id
    assert request.request in prompt_text
    assert _git(worktree, "rev-parse", "HEAD") == result.result_commit
    assert _git(repo, "rev-parse", "HEAD") == base and _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "worktree", "list").count("\n") == 1  # 임시 체크아웃은 정리됐다
    assert output.runtime_ref == progress.runtime_refs[0] == "pid:4242;start:2026-09-20T01:00:00Z"


# --- 보류 ---------------------------------------------------------------------------------


def test_no_change_is_needs_information_without_commit(state_conn, repo, handoff):
    base = register(state_conn, repo)
    adapter = ScriptedTool(state_conn, lambda _wt: _run(last_message=_message("ready_for_review")))

    output = adapter.run(request_for(base), handoff, Recorder())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert result.summary.startswith("변경 없음") and result.result_commit is None and result.verification is None
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]


def test_fix_without_repro_test_is_needs_information_but_preserved(state_conn, repo, handoff):
    base = register(state_conn, repo)

    output = ScriptedTool(state_conn, fix_only).run(request_for(base), handoff, Recorder())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert "재현 테스트 없음" in result.summary and result.result_commit not in (None, base)
    assert by_kind(output)["test_log_before"].decode() == "exit_code=0\n(재현 테스트 없음)\n"


def test_tool_outcome_needs_information_is_kept_even_with_repro_test(state_conn, repo, handoff):
    base = register(state_conn, repo)

    def script(worktree: Path) -> ToolRun:
        fix_and_add_test(worktree)
        return _run(last_message=_message("needs_information", "인계 자료가 부족하다"))

    output = ScriptedTool(state_conn, script).run(request_for(base), handoff, Recorder())

    assert output.failed is None and output.result.outcome == "needs_information"
    assert output.result.summary == "인계 자료가 부족하다" and output.result.result_commit is not None


def test_unreadable_last_message_uses_parse_result(state_conn, repo, handoff):
    base = register(state_conn, repo)

    def script(worktree: Path) -> ToolRun:
        fix_and_add_test(worktree)
        return _run(last_message=None)

    output = ScriptedTool(state_conn, script).run(request_for(base), handoff, Recorder())

    assert output.failed is None and output.result.outcome == "needs_information"
    assert "마지막 메시지" in output.result.summary


# --- 실패 ---------------------------------------------------------------------------------


def test_timeout_is_failed_with_stop_confirmation_and_partial_output(state_conn, repo, handoff):
    base = register(state_conn, repo)
    adapter = ScriptedTool(
        state_conn, lambda _wt: _run(stdout=b"partial\n", timed_out=True, stopped=True), timeout_seconds=7,
    )
    progress = Recorder()

    output = adapter.run(request_for(base), handoff, progress)

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "timeout" and stopped is True and "7" in message
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]
    assert by_kind(output)["claude_jsonl"] == b"partial\n"
    assert output.runtime_ref == progress.runtime_refs[0]


def test_timeout_without_stop_confirmation_reports_process_stopped_false(state_conn, repo, handoff):
    base = register(state_conn, repo)
    adapter = ScriptedTool(state_conn, lambda _wt: _run(timed_out=True, stopped=False))

    output = adapter.run(request_for(base), handoff, Recorder())

    assert output.failed[0] == "timeout" and output.failed[2] is False


def test_missing_executable_is_tool_unavailable(state_conn, repo, handoff):
    base = register(state_conn, repo)

    def script(_wt: Path) -> ToolRun:
        raise FileNotFoundError(2, "No such file or directory", "fake")

    output = ScriptedTool(state_conn, script).run(request_for(base), handoff, Recorder())

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "fake_unavailable" and stopped is True and "fake" in message
    assert output.artifacts == []


def test_failures_before_launch_do_not_call_launch(state_conn, repo, handoff):
    adapter = ScriptedTool(state_conn, fix_and_add_test)

    missing_registration = adapter.run(make_request(), handoff, Recorder())
    register(state_conn, repo)
    missing_commit = adapter.run(request_for("0" * 40), handoff, Recorder())

    assert missing_registration.failed[0] == "registration_missing"
    assert missing_commit.failed[0] == "base_commit_missing"
    assert adapter.launched_with == []


# --- classify_failure 훅 ----------------------------------------------------------------------


class LimitedTool(ScriptedTool):
    """stderr 에 'limit' 이 있으면 사용량 한도로 판정하는 하위 클래스 — 훅이 공통 흐름을 failed 로 끝내는지 본다."""

    def classify_failure(self, run: ToolRun) -> tuple[str, str] | None:
        return ("usage_limit", "fake 사용량 한도") if b"limit" in run.stderr else None


def test_classify_failure_hook_ends_run_as_failed_and_keeps_raw_logs_only(state_conn, repo, handoff):
    base = register(state_conn, repo)
    request = request_for(base)

    def script(worktree: Path) -> ToolRun:
        fix_and_add_test(worktree)  # 한도 도중 남은 수정 — 커밋·검증하지 않는다
        return _run(stdout=b'{"type":"result"}\n', stderr=b"You've hit your usage limit\n", last_message=_message())

    progress = Recorder()
    output = LimitedTool(state_conn, script).run(request, handoff, progress)

    assert output.result is None
    assert output.failed == ("usage_limit", "fake 사용량 한도", True)
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]
    assert by_kind(output)["claude_stderr"] == b"You've hit your usage limit\n"
    assert output.runtime_ref == progress.runtime_refs[0]
    worktree = repo.parent / "mini-repo-worktrees" / request.task_id
    assert _git(worktree, "rev-parse", "HEAD") == base  # 결과 커밋 없음


def test_classify_failure_uses_process_stop_confirmation(state_conn, repo, handoff):
    base = register(state_conn, repo)
    adapter = LimitedTool(state_conn, lambda _wt: _run(stderr=b"rate limit\n", stopped=False))

    output = adapter.run(request_for(base), handoff, Recorder())

    assert output.failed[0] == "usage_limit" and output.failed[2] is False


def test_classify_failure_default_is_none_and_timeout_wins(state_conn, repo, handoff):
    base = register(state_conn, repo)
    assert ScriptedTool(state_conn, fix_only).classify_failure(_run(stderr=b"rate limit\n")) is None

    output = LimitedTool(state_conn, lambda _wt: _run(stderr=b"limit\n", timed_out=True)).run(
        request_for(base), handoff, Recorder(),
    )

    assert output.failed[0] == "timeout"  # 시간 초과가 먼저다


# --- 결과 스키마 (Codex 는 파일, Claude 는 문자열로 같은 내용을 준다) -------------------------------


def test_result_schema_is_strict_object_with_four_keys():
    assert RESULT_SCHEMA["type"] == "object"
    assert RESULT_SCHEMA["additionalProperties"] is False
    assert sorted(RESULT_SCHEMA["required"]) == ["files_changed", "notes", "outcome", "summary"]
    assert RESULT_SCHEMA["properties"]["outcome"]["enum"] == ["ready_for_review", "needs_information"]
    json.dumps(RESULT_SCHEMA)  # 직렬화 가능


# --- 비밀값 -------------------------------------------------------------------------------


def test_raw_stdout_and_stderr_are_masked(state_conn, repo, handoff):
    base = register(state_conn, repo)
    adapter = ScriptedTool(state_conn, lambda _wt: _run(
        stdout=f"token {WFC}\n".encode(), stderr=f"key {SK}\n".encode(), last_message=_message(),
    ))

    output = adapter.run(request_for(base), handoff, Recorder())

    artifacts = by_kind(output)
    assert WFC not in artifacts["claude_jsonl"].decode() and "wfc_***" in artifacts["claude_jsonl"].decode()
    assert SK not in artifacts["claude_stderr"].decode() and "sk-***" in artifacts["claude_stderr"].decode()
    meta = next(meta for meta, _ in output.artifacts if meta.kind == "claude_jsonl")
    assert meta.size == len(artifacts["claude_jsonl"])


def test_child_env_drops_secrets_and_central_settings(state_conn):
    base_env = {
        **os.environ, "OPENAI_API_KEY": SK, "WORKFLOW_CONNECTOR_HOME": "/x", "DIAG_API_TOKEN": "d",
        "SESSION_SECRET": "s", "OPERATOR_TOKEN": "o",
    }

    env = ScriptedTool(state_conn, fix_only, env_base=base_env).child_env()

    assert "PATH" in env
    assert not {"OPENAI_API_KEY", "DIAG_API_TOKEN", "SESSION_SECRET", "OPERATOR_TOKEN"} & set(env)
    assert not any(key.startswith(("WORKFLOW_", "DIAG_")) for key in env)


def test_verification_profile_runs_with_child_env(state_conn, repo, handoff, monkeypatch):
    """검증 프로필도 비밀값 없는 env 로 돈다 — check.py 가 환경을 stdout 에 찍고 그 로그를 확인한다."""
    (repo / "check.py").write_text("import os, sys\nprint(sorted(os.environ))\nsys.exit(0)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "env probe")
    base = register(state_conn, repo)
    monkeypatch.setenv("OPENAI_API_KEY", SK)

    output = ScriptedTool(state_conn, fix_only, env_base=os.environ).run(request_for(base), handoff, Recorder())

    log = by_kind(output)["test_log_after"].decode()
    assert log.startswith("exit_code=0") and "'PATH'" in log and "OPENAI_API_KEY" not in log


# --- 사용자 정의 종류 — `LocalTarget` 읽기 전용 흐름 (worktree·커밋·검증 없음) ----------------------------------


@pytest.fixture
def review_handoff(tmp_path: Path) -> Path:
    handoff = tmp_path / "review-daily-0920.handoff"
    handoff.mkdir()
    (handoff / "manifest.json").write_text('{"source_kind": "code_change"}')
    (handoff / "diff.patch").write_text("--- a\n+++ b\n")
    (handoff / "code_change_result.json").write_text('{"outcome": "ready_for_review"}')
    return handoff


def _generic_message(outcome: str = "approved", summary: str = "diff 가 repair_request 를 충족한다") -> str:
    return json.dumps({"outcome": outcome, "summary": summary}, ensure_ascii=False)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_generic_flow_returns_generic_result_with_raw_logs_only(state_conn, repo, review_handoff):
    register(state_conn, repo)
    request = make_local_request()
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(
        stdout=b'{"type":"result"}\n', stderr=b"fake: done\n", last_message=_generic_message(),
    ))
    progress = Recorder()
    before = _snapshot(review_handoff)

    output = adapter.run(request, review_handoff, progress)

    assert output.failed is None, output.failed
    result = output.result
    assert isinstance(result, GenericResult)
    assert (result.kind, result.outcome, result.summary) == ("review", "approved", "diff 가 repair_request 를 충족한다")
    assert (result.execution_id, result.task_id, result.artifact_ids) == (request.execution_id, request.task_id, [])
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]
    assert by_kind(output) == {"claude_jsonl": b'{"type":"result"}\n', "claude_stderr": b"fake: done\n"}
    assert output.runtime_ref == progress.runtime_refs[0] == "pid:4242;start:2026-09-20T01:00:00Z"
    # 읽기 전용 실행: 인계 디렉터리에서, 스키마 enum 은 종류의 outcomes, 프롬프트 첫 줄은 종류 표시
    (cwd, prompt_text, schema), = adapter.launched_readonly_with
    assert cwd == review_handoff and adapter.launched_with == []
    assert schema == generic_result_schema(REVIEW_SPEC.outcomes)
    assert schema["properties"]["outcome"]["enum"] == ["approved", "changes_requested", "needs_information"]
    assert prompt_text.splitlines()[0] == "# 업무 종류: review (검토)"
    assert request.request in prompt_text and str(review_handoff / "diff.patch") in prompt_text
    # worktree·커밋·검증 없음 — 저장소와 인계 디렉터리는 그대로
    assert not (repo.parent / "mini-repo-worktrees").exists()
    assert _git(repo, "worktree", "list").count("\n") == 0 and _git(repo, "status", "--porcelain") == ""
    assert _snapshot(review_handoff) == before


def test_generic_flow_without_kind_spec_is_kind_spec_missing(state_conn, repo, review_handoff):
    register(state_conn, repo)
    request = make_local_request().model_copy(update={"kind_spec": None})  # 계약 검증을 우회한 잘못된 요청
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(last_message=_generic_message()))

    output = adapter.run(request, review_handoff, Recorder())

    assert output.result is None and output.failed[0] == "kind_spec_missing" and output.failed[2] is True
    assert "review" in output.failed[1] and adapter.launched_readonly_with == [] and output.artifacts == []


def test_generic_flow_missing_registration_fails_before_launch(state_conn, review_handoff):
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(last_message=_generic_message()))

    output = adapter.run(make_local_request(), review_handoff, Recorder())

    assert output.failed[0] == "registration_missing" and "local-demo-report" in output.failed[1]
    assert adapter.launched_readonly_with == []


def test_generic_flow_creates_missing_handoff_dir(state_conn, repo, tmp_path):
    register(state_conn, repo)
    handoff = tmp_path / "없던.handoff"
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(last_message=_generic_message()))

    output = adapter.run(make_local_request(), handoff, Recorder())

    assert output.failed is None and handoff.is_dir() and adapter.launched_readonly_with[0][0] == handoff
    assert "(인계 자료 없음)" in adapter.launched_readonly_with[0][1]


@pytest.mark.parametrize("last_message, note", [
    (_generic_message("rejected"), "rejected"),
    (_generic_message(""), "''"),
    (None, "파일 없음"),
])
def test_generic_outcome_outside_spec_is_result_invalid_with_raw_logs(state_conn, repo, review_handoff, last_message,
                                                                      note):
    register(state_conn, repo)
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(stdout=b"out\n", stderr=b"err\n", last_message=last_message))
    progress = Recorder()

    output = adapter.run(make_local_request(), review_handoff, progress)

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "result_invalid" and stopped is True and note in message
    assert by_kind(output) == {"claude_jsonl": b"out\n", "claude_stderr": b"err\n"}  # 원시 로그는 남긴다
    assert output.runtime_ref == progress.runtime_refs[0]


def test_generic_flow_that_changes_handoff_files_is_readonly_violation(state_conn, repo, review_handoff):
    register(state_conn, repo)

    def script(cwd: Path) -> ToolRun:
        (cwd / "diff.patch").write_text("--- a\n+++ b\n+tampered\n")
        (cwd / "notes.md").write_text("새 파일")
        return _run(stdout=b"out\n", last_message=_generic_message())

    output = ScriptedTool(state_conn, script).run(make_local_request(), review_handoff, Recorder())

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "readonly_violation" and stopped is True
    assert "diff.patch" in message and "notes.md" in message
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]


def test_generic_flow_that_deletes_a_handoff_file_is_readonly_violation(state_conn, repo, review_handoff):
    register(state_conn, repo)

    def script(cwd: Path) -> ToolRun:
        (cwd / "code_change_result.json").unlink()
        return _run(last_message=_generic_message())

    output = ScriptedTool(state_conn, script).run(make_local_request(), review_handoff, Recorder())

    assert output.failed[0] == "readonly_violation" and "code_change_result.json" in output.failed[1]


def test_generic_flow_timeout_and_classify_failure_keep_their_order(state_conn, repo, review_handoff):
    register(state_conn, repo)
    request = make_local_request()

    timed_out = ScriptedTool(state_conn, lambda _cwd: _run(stdout=b"partial\n", timed_out=True, stopped=False),
                             timeout_seconds=7).run(request, review_handoff, Recorder())
    limited = LimitedTool(state_conn, lambda _cwd: _run(stderr=b"rate limit\n", last_message=_generic_message())).run(
        request, review_handoff, Recorder(),
    )
    limited_but_timed_out = LimitedTool(state_conn, lambda _cwd: _run(stderr=b"limit\n", timed_out=True)).run(
        request, review_handoff, Recorder(),
    )

    assert timed_out.result is None and timed_out.failed[0] == "timeout" and "7" in timed_out.failed[1]
    assert timed_out.failed[2] is False and by_kind(timed_out)["claude_jsonl"] == b"partial\n"
    assert limited.result is None and limited.failed == ("usage_limit", "fake 사용량 한도", True)
    assert [meta.kind for meta, _ in limited.artifacts] == ["claude_jsonl", "claude_stderr"]
    assert limited_but_timed_out.failed[0] == "timeout"  # 시간 초과가 먼저다


def test_generic_flow_missing_executable_is_tool_unavailable(state_conn, repo, review_handoff):
    register(state_conn, repo)

    def script(_cwd: Path) -> ToolRun:
        raise FileNotFoundError(2, "No such file or directory", "fake")

    output = ScriptedTool(state_conn, script).run(make_local_request(), review_handoff, Recorder())

    assert output.result is None and output.failed[0] == "fake_unavailable" and output.artifacts == []


def test_generic_raw_logs_are_masked(state_conn, repo, review_handoff):
    register(state_conn, repo)
    adapter = ScriptedTool(state_conn, lambda _cwd: _run(
        stdout=f"token {WFC}\n".encode(), stderr=f"key {SK}\n".encode(), last_message=_generic_message(),
    ))

    output = adapter.run(make_local_request(), review_handoff, Recorder())

    artifacts = by_kind(output)
    assert WFC not in artifacts["claude_jsonl"].decode() and "wfc_***" in artifacts["claude_jsonl"].decode()
    assert SK not in artifacts["claude_stderr"].decode() and "sk-***" in artifacts["claude_stderr"].decode()


def test_generic_result_schema_is_strict_object_with_outcome_enum():
    schema = generic_result_schema(["approved", "changes_requested"])

    assert schema == {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["approved", "changes_requested"]},
            "summary": {"type": "string"},
        },
        "required": ["outcome", "summary"],
        "additionalProperties": False,
    }
    json.dumps(schema)


def test_diagnosis_request_is_still_unsupported(state_conn, review_handoff):
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-diagnose-001", "task_id": "diagnose-daily-0920",
        "kind": "diagnosis", "agent_id": "agent-ops-demo", "task_revision": 1, "request": "조사",
        "input_artifact_ids": [], "target": {"run_id": "daily-0920-0900"},
    })
    adapter = ScriptedTool(state_conn, fix_only)

    output = adapter.run(request, review_handoff, Recorder())

    assert output.result is None and output.failed[0] == "unsupported_kind"
    assert adapter.launched_with == [] and adapter.launched_readonly_with == []


# --- 일반 버그 수정 `bug_fix` — 등록된 검증 프로필·고정 기준 커밋, 보고서 없음 (ADR-0014 3항) ----------------------

BUG_TASK = "task-gh-41"
BUG_REQUEST = "GitHub acme/billing#41 — 할인 쿠폰이 두 번 적용됨\n\n재현: 같은 쿠폰으로 두 번 결제하면 총액이 음수가 된다."


def register_bug(state_conn, repo: Path) -> str:
    """일반 저장소 등록 — 검증 프로필 `vp-unit`. `vp-report` 도 등록돼 있지만 `bug_fix` 는 쓰지 않아야 한다."""
    base = git_ops.head_sha(repo)
    state.save_registration(state_conn, {
        "local_registration_id": "local-billing",
        "repo_path": str(repo),
        "tool": "codex",
        "repository_id": "acme-billing",
        "base_commit": base,
        "verification_profiles": {
            "vp-unit": [sys.executable, "check.py"],
            "vp-report": [sys.executable, "-c", "print('보고서')"],
        },
    })
    return base


def bug_request(base_commit: str, *, execution_id: str = "exec-gh-fix-001", profile: str = "vp-unit",
                input_artifact_ids: list[str] | None = None) -> ExecutionRequest:
    return ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": execution_id, "task_id": BUG_TASK, "kind": "bug_fix",
        "agent_id": "agent-codex-mac", "task_revision": 1, "request": BUG_REQUEST,
        "input_artifact_ids": input_artifact_ids or [],
        "target": {"local_registration_id": "local-billing", "base_commit": base_commit,
                   "verification_profile_id": profile},
    })


@pytest.fixture
def bug_handoff(tmp_path: Path) -> Path:
    """첫 시도의 인계 디렉터리는 비어 있다. 데모 파일 이름이 있어도 `bug_fix` 는 보고서를 만들지 않는다."""
    handoff = tmp_path / "task-gh-41.handoff"
    handoff.mkdir()
    return handoff


def test_bug_fix_baseline_fails_result_passes_and_no_report(state_conn, repo, bug_handoff):
    base = register_bug(state_conn, repo)
    (bug_handoff / "response-after@1.json").write_text("{}")  # 데모 입력이 우연히 있어도 보고서 경로를 타지 않는다
    request = bug_request(base)
    adapter = ScriptedTool(state_conn, fix_and_add_test)

    output = adapter.run(request, bug_handoff, Recorder())

    assert output.failed is None, output.failed
    result = output.result
    assert result.outcome == "ready_for_review" and result.base_commit == base
    assert _git(repo, "rev-parse", f"{result.result_commit}^") == base  # 결과 커밋은 고정 기준 커밋 바로 위
    assert [meta.kind for meta, _ in output.artifacts] == [
        "diff", "test_log_before", "test_log_after", "verification_log", "claude_jsonl", "claude_stderr",
    ]
    artifacts = by_kind(output)
    assert artifacts["test_log_before"].decode().splitlines()[0] == "exit_code=1"  # 기준 커밋 + 새 테스트 → 재현 실패
    assert artifacts["test_log_after"].decode().splitlines()[0] == "exit_code=0"
    assert artifacts["verification_log"].decode().splitlines()[0] == "exit_code=0"
    assert "src.py" in artifacts["diff"].decode() and "test_repro.py" in artifacts["diff"].decode()
    assert result.verification.profile_id == "vp-unit" and result.verification.result_commit == result.result_commit
    (_worktree, prompt_text), = adapter.launched_with
    assert BUG_REQUEST in prompt_text
    assert "target_component" not in prompt_text and "python3 -m pytest" not in prompt_text  # 데모 규칙 없음


def test_bug_fix_without_new_test_is_needs_information(state_conn, repo, bug_handoff):
    base = register_bug(state_conn, repo)

    output = ScriptedTool(state_conn, fix_only).run(bug_request(base), bug_handoff, Recorder())

    assert output.failed is None and output.result.outcome == "needs_information"
    assert "재현 테스트 없음" in output.result.summary and output.result.result_commit not in (None, base)
    assert "report_output" not in by_kind(output)


def test_bug_fix_without_change_is_needs_information(state_conn, repo, bug_handoff):
    base = register_bug(state_conn, repo)

    output = ScriptedTool(state_conn, lambda _wt: _run(last_message=_message())).run(
        bug_request(base), bug_handoff, Recorder(),
    )

    assert output.failed is None and output.result.outcome == "needs_information"
    assert output.result.summary.startswith("변경 없음") and output.result.result_commit is None


def test_bug_fix_verification_failure_is_kept_with_logs(state_conn, repo, bug_handoff):
    """도구가 ready_for_review 라고 해도 검증 프로필이 실패하면 그 exit code·로그를 그대로 보존한다 (판정은 중앙)."""
    base = register_bug(state_conn, repo)

    def test_only(worktree: Path) -> ToolRun:
        (worktree / "tests" / "test_repro.py").write_text("def test_repro():\n    assert False\n")
        return _run(last_message=_message())

    output = ScriptedTool(state_conn, test_only).run(bug_request(base), bug_handoff, Recorder())

    result = output.result
    assert output.failed is None and result.result_commit is not None
    assert result.verification.exit_code == 1
    artifacts = by_kind(output)
    assert artifacts["verification_log"].decode().startswith("exit_code=1") and "broken=" in artifacts["verification_log"].decode()
    assert artifacts["test_log_after"].decode().startswith("exit_code=1")


def test_bug_fix_unregistered_profile_fails_before_launch(state_conn, repo, bug_handoff):
    base = register_bug(state_conn, repo)
    adapter = ScriptedTool(state_conn, fix_and_add_test)

    output = adapter.run(bug_request(base, profile="vp-from-issue"), bug_handoff, Recorder())

    assert output.failed[0] == "verification_profile_missing" and adapter.launched_with == []


def test_bug_fix_tool_commit_is_commit_mismatch(state_conn, repo, bug_handoff):
    """도구가 규칙을 어기고 직접 커밋하면 worktree HEAD 가 기준 커밋이 아니다 — 결과로 받지 않고 실패로 끝낸다."""
    base = register_bug(state_conn, repo)

    def commits_itself(worktree: Path) -> ToolRun:
        fix_and_add_test(worktree)
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-q", "-m", "tool commit")
        return _run(stdout=b"out\n", last_message=_message())

    output = ScriptedTool(state_conn, commits_itself).run(bug_request(base), bug_handoff, Recorder())

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "commit_mismatch" and stopped is True and base[:12] in message
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]


def test_bug_fix_branch_at_other_commit_is_base_commit_mismatch(state_conn, repo, bug_handoff):
    """이전 시도가 남긴 `task/<id>` 브랜치가 요청의 기준 커밋과 다르면 도구를 띄우지 않는다 — 조용히 다른 코드 위에서
    고치지 않는다."""
    base = register_bug(state_conn, repo)
    first = ScriptedTool(state_conn, fix_and_add_test).run(bug_request(base), bug_handoff, Recorder())
    git_ops.remove_worktree(repo, git_ops.worktree_path(repo, BUG_TASK))
    adapter = ScriptedTool(state_conn, fix_and_add_test)

    output = adapter.run(bug_request(base, execution_id="exec-gh-fix-002"), bug_handoff, Recorder())

    code, message, stopped = output.failed
    assert code == "base_commit_mismatch" and stopped is True
    assert first.result.result_commit[:12] in message and adapter.launched_with == []


def test_bug_fix_leftover_dirty_worktree_is_rejected(state_conn, repo, bug_handoff):
    """중단된 시도(프로세스 종료 미확인이라 worktree 가 남음)의 변경을 다음 시도의 결과로 커밋하지 않는다."""
    base = register_bug(state_conn, repo)
    stopped = ScriptedTool(
        state_conn, lambda wt: (fix_only(wt), _run(stdout=b"partial\n", timed_out=True, stopped=False))[1],
    ).run(bug_request(base), bug_handoff, Recorder())
    assert stopped.failed[0] == "timeout" and stopped.failed[2] is False and stopped.result is None
    worktree = git_ops.worktree_path(repo, BUG_TASK)
    assert _git(worktree, "rev-parse", "HEAD") == base  # 중단된 시도는 커밋을 만들지 않았다
    adapter = ScriptedTool(state_conn, fix_and_add_test)

    output = adapter.run(bug_request(base, execution_id="exec-gh-fix-002"), bug_handoff, Recorder())

    assert output.failed[0] == "worktree_dirty" and output.failed[2] is True and adapter.launched_with == []


def test_bug_fix_rework_uses_previous_result_as_baseline_and_passes_review_findings(state_conn, repo, bug_handoff):
    """재작업 시도: base_commit = 이전 result_commit. 새 테스트만 이전 결과 위에서 실패해야 하고, 이전 검토 지적이
    프롬프트에 들어간다."""
    base = register_bug(state_conn, repo)
    first = ScriptedTool(state_conn, fix_and_add_test).run(bug_request(base), bug_handoff, Recorder())
    previous = first.result.result_commit
    git_ops.remove_worktree(repo, git_ops.worktree_path(repo, BUG_TASK))  # 종료 뒤 runner 가 worktree 를 지운다
    (bug_handoff / "code_review_result.json").write_text(json.dumps({
        "contract_version": 1, "execution_id": "exec-gh-review-001", "task_id": "task-gh-41-review",
        "source_execution_id": "exec-gh-fix-001", "reviewed_commit": previous, "outcome": "changes_requested",
        "summary": "음수 총액 경계가 남아 있다",
        "findings": [{"severity": "blocking", "path": "src.py", "line": 1, "message": "round2 경계를 처리하지 않음"}],
        "missing_information": [], "artifact_ids": [],
    }, ensure_ascii=False))

    def round2(worktree: Path) -> ToolRun:
        (worktree / "src.py").write_text("VALUE = 'fixed round2'\n")
        (worktree / "tests" / "test_round2.py").write_text("def test_round2():\n    assert True\n")
        return _run(last_message=_message(summary="round2 경계 처리"))

    adapter = ScriptedTool(state_conn, round2)
    output = adapter.run(
        bug_request(previous, execution_id="exec-gh-fix-002", input_artifact_ids=["art-prev", "art-review"]),
        bug_handoff, Recorder(),
    )

    assert output.failed is None, output.failed
    result = output.result
    assert result.outcome == "ready_for_review" and result.base_commit == previous
    assert _git(repo, "rev-parse", f"{result.result_commit}^") == previous
    before = by_kind(output)["test_log_before"].decode()
    assert before.startswith("exit_code=1") and "test_round2.py" in before  # 이전 결과 + 새 테스트만 → 재현 실패
    assert "test_repro.py'" not in before.split("broken=")[1]  # 이전 시도의 테스트는 이미 통과
    diff = by_kind(output)["diff"].decode()
    assert "test_round2.py" in diff and "test_repro.py" not in diff
    (_worktree, prompt_text), = adapter.launched_with
    assert "round2 경계를 처리하지 않음" in prompt_text and "src.py:1" in prompt_text
    assert "음수 총액 경계가 남아 있다" in prompt_text


def test_bug_fix_tool_and_profile_env_have_no_github_token(state_conn, repo, bug_handoff):
    """검증 프로필·도구 프로세스 환경에 GitHub 토큰이 없다 — 서버 비밀값이 운영자 Mac 에 있어도 상속하지 않는다."""
    (repo / "check.py").write_text("import os, sys\nprint(sorted(os.environ))\nsys.exit(0)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "env probe")
    base = register_bug(state_conn, repo)
    env_base = {**os.environ, "WORKFLOW_GITHUB_TOKEN": "ghp_" + "x" * 36, "GITHUB_TOKEN": "ghs_y",
                "GH_TOKEN": "gho_z", "WORKFLOW_GITHUB_REPOS": "acme/billing"}
    adapter = ScriptedTool(state_conn, fix_only, env_base=env_base)

    output = adapter.run(bug_request(base), bug_handoff, Recorder())

    log = by_kind(output)["verification_log"].decode()
    assert log.startswith("exit_code=0") and "'PATH'" in log
    for key in ("WORKFLOW_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "WORKFLOW_GITHUB_REPOS"):
        assert key not in log and key not in adapter.child_env()


def test_demo_code_change_still_generates_report(state_conn, repo, handoff):
    """데모 회귀: `code_change` 는 여전히 `vp-report` 로 보고서를 만든다 (응답 fixture 가 인계 자료에 있을 때)."""
    base = git_ops.head_sha(repo)
    state.save_registration(state_conn, {
        "local_registration_id": "local-demo-report", "repo_path": str(repo), "tool": "codex",
        "repository_id": "demo-report-repo", "base_commit": base,
        "verification_profiles": {
            "vp-pytest": [sys.executable, "check.py"],
            "vp-report": [sys.executable, "-c", "import sys; print('보고서 ' + open(sys.argv[1]).read())", "{response}"],
        },
    })
    (handoff / "response-after@1.json").write_text('{"report_date": "2026-09-19"}')

    output = ScriptedTool(state_conn, fix_and_add_test).run(request_for(base), handoff, Recorder())

    assert output.failed is None
    assert by_kind(output)["report_output"].decode().startswith("보고서 {\"report_date\"")
