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
from workflow.connector.local_tool import RESULT_SCHEMA, LocalToolAdapter, ToolResult, ToolRun

from .conftest import make_request

WFC = "wfc_" + "a" * 43
SK = "sk-" + "b" * 20

# check.py — 저장소에 커밋되는 고정 검증 스크립트. 재현 테스트가 있고 소스가 안 고쳐졌으면 1, 그 외 0.
# `_test_before` 가 결과 커밋의 테스트 파일만 기준 코드 위에 놓으면 1(재현 실패), 수정 후 worktree 에서는 0 이어야 한다.
_CHECK = """\
import pathlib, sys
repro = pathlib.Path("tests/test_repro.py").exists()
fixed = "fixed" in pathlib.Path("src.py").read_text()
print(f"repro={repro} fixed={fixed}")
sys.exit(1 if repro and not fixed else 0)
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
    """가짜 로컬 도구. `script(worktree)` 가 worktree 를 바꾸고 ToolRun 을 돌려준다 (또는 예외를 던진다)."""

    tool_name = "fake"
    raw_kinds = ("claude_jsonl", "claude_stderr")  # codex_* 가 아닌 kind 로 공통 흐름이 raw_kinds 를 따르는지 본다

    def __init__(self, state_conn, script: Callable[[Path], ToolRun], **kwargs):
        super().__init__(state_conn, verification_timeout=60, **kwargs)
        self.script = script
        self.launched_with: list[tuple[Path, str]] = []

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        self.launched_with.append((worktree, prompt_text))
        progress("fake 시작", runtime_ref="pid:4242;start:2026-09-20T01:00:00Z")
        return self.script(worktree)

    def parse_last_message(self, raw: str | None) -> ToolResult:
        if raw is None:
            return ToolResult("needs_information", "fake 마지막 메시지를 읽지 못함 (파일 없음)", "파일 없음")
        data = json.loads(raw)
        return ToolResult(data["outcome"], data["summary"], None)


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
