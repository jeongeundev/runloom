"""claude — Claude Code CLI 를 띄우는 어댑터. 여기서는 PATH 앞에 둔 **가짜 `claude`** 스크립트로만 돈다 (실연동은 step 5).

가짜는 `claude -p --output-format json` 처럼 stdout 에 결과 JSON 한 덩어리를 쓴다. 키는 2026-09-20 `claude 2.1.278` 로
한 번 실행해 확인한 형태(`type=result`, `subtype`, `is_error`, `result`, `structured_output`, `usage`, `api_error_status` …)를
따르고, 받은 argv·env·cwd·프롬프트를 그 안의 `_fake` 키에 남긴다. 어댑터는 모르는 키를 무시하므로 테스트는 보존된
`claude_jsonl` 산출물에서 이를 읽는다. 데모 저장소는 test_codex 와 같은 `scripts/scaffold_demo_repo.py`,
수정 대본(고친 변환부·재현 테스트)은 대본 에이전트(`workflow.scripted._common`)의 상수를 재사용한다.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from workflow.connector import git_ops, state
from workflow.connector.claude import ALLOWED_TOOLS, ClaudeAdapter
from workflow.connector.local_tool import RESULT_SCHEMA, ToolRun
from workflow.scripted._common import FIXED_TRANSFORMER, REPRO_TEST

from .test_codex import RESPONSE_AFTER, Progress, _git, by_kind, make_repo, request_for

WFC = "wfc_" + "a" * 43
SK = "sk-" + "b" * 20

# --- 가짜 claude ---------------------------------------------------------------------------

_FAKE_CLAUDE = '''#!/usr/bin/env python3
"""가짜 claude. MODE 에 따라 cwd(worktree)를 고치고 `--output-format json` 결과 한 덩어리를 stdout 에 쓴다."""
import json
import os
import sys
import time
from pathlib import Path

MODE = %(mode)r
HERE = Path(__file__).resolve().parent
args = sys.argv[1:]

if MODE == "forbidden":
    print("real claude must not run in tests", file=sys.stderr)
    sys.exit(99)

prompt = sys.stdin.read()
worktree = Path.cwd()
fake = {"argv": args, "env": dict(os.environ), "cwd": str(worktree),
        "prompt_chars": len(prompt), "prompt_head": prompt[:60]}

if MODE == "sleep":
    time.sleep(60)
if MODE in ("full", "leak", "fix_only", "is_error", "no_structured"):
    (worktree / "daily_report" / "transformer.py").write_text((HERE / "fixed_transformer.py").read_text())
if MODE in ("full", "leak", "is_error", "no_structured"):
    (worktree / "tests" / "test_repro.py").write_text((HERE / "repro_test.py").read_text())

if MODE == "rate_limit_429":
    print('API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"..."}}', file=sys.stderr)
    sys.exit(1)  # stdout 에 결과 JSON 없음


def result(**fields):
    # usage 의 429 는 일부러 넣었다 — 정상 결과의 숫자를 한도 문구로 오인하면 안 된다
    envelope = {
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "session_id": "fake-session",
        "usage": {"input_tokens": 429, "output_tokens": 67}, "total_cost_usd": 0.01, "api_error_status": None,
        "result": "", "structured_output": None, "_fake": fake,
    }
    envelope.update(fields)
    print(json.dumps(envelope, ensure_ascii=False))


structured = {
    "summary": "items 와 data.records 중 하나를 읽도록 변환부를 고쳤다",
    "outcome": "needs_information" if MODE == "noop" else "ready_for_review",
    "files_changed": [] if MODE == "noop" else ["daily_report/transformer.py"],
    "notes": "",
}
if MODE == "is_error":
    result(subtype="error_during_execution", is_error=True, result="Error: tool execution failed")
elif MODE == "no_structured":
    result(result="작업을 마쳤다 (JSON 없이)")
elif MODE == "usage_limit":
    result(is_error=True, result="You've hit your usage limit. Your limit will reset at 3pm (Asia/Seoul).")
elif MODE == "leak":
    print("stderr token " + %(wfc)r, file=sys.stderr)
    result(result="stdout key " + %(sk)r, structured_output=structured)
else:
    result(result=json.dumps(structured, ensure_ascii=False), structured_output=structured)
print("fake claude: done", file=sys.stderr)
'''


def write_fake_claude(bin_dir: Path, mode: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "fixed_transformer.py").write_text(FIXED_TRANSFORMER)
    (bin_dir / "repro_test.py").write_text(
        REPRO_TEST % {"response": json.dumps(RESPONSE_AFTER, ensure_ascii=False, indent=4)}
    )
    script = bin_dir / "claude"
    script.write_text(_FAKE_CLAUDE % {"mode": mode, "wfc": WFC, "sk": SK})
    script.chmod(0o755)
    return script


@pytest.fixture(autouse=True)
def fake_bin(tmp_path, monkeypatch) -> Path:
    """모든 테스트에서 PATH 앞의 `claude` 는 가짜다. 기본은 호출되면 실패하는 forbidden 모드."""
    bin_dir = tmp_path / "fakebin"
    write_fake_claude(bin_dir, "forbidden")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def handoff(tmp_path) -> Path:
    handoff = tmp_path / "fix-daily-0920.handoff"
    handoff.mkdir()
    (handoff / "manifest.json").write_text("{}")
    (handoff / "response-after@1.json").write_text(json.dumps(RESPONSE_AFTER, ensure_ascii=False))
    (handoff / "log-daily-0920@1.txt").write_text("ERROR code=MISSING_RECORDS_FIELD\n")
    return handoff


@pytest.fixture
def repo(tmp_path) -> Path:
    return make_repo(tmp_path)


def register(state_conn, repo: Path) -> str:
    base = git_ops.head_sha(repo)
    state.save_registration(state_conn, {
        "local_registration_id": "local-demo-report",
        "repo_path": str(repo),
        "tool": "claude",
        "repository_id": "demo-report-repo",
        "base_commit": base,
        "verification_profiles": {
            "vp-pytest": [sys.executable, "-m", "pytest", "-q"],
            "vp-report": [sys.executable, "-m", "daily_report", "{response}"],
        },
    })
    return base


def adapter(state_conn, **kwargs) -> ClaudeAdapter:
    return ClaudeAdapter(state_conn, env_base=os.environ, verification_timeout=60, **kwargs)


def envelope_of(output) -> dict:
    """보존된 stdout(claude_jsonl)의 결과 JSON."""
    return json.loads(by_kind(output)["claude_jsonl"].decode())


# --- argv ---------------------------------------------------------------------------------


def test_build_argv_is_fixed_and_carries_no_prompt_or_paths(state_conn, tmp_path):
    worktree = tmp_path / "wt"
    schema_json = json.dumps(RESULT_SCHEMA, ensure_ascii=False)

    argv = adapter(state_conn).build_argv(worktree, schema_json)

    assert argv == [
        "claude", "-p", "--output-format", "json", "--no-session-persistence",
        "--permission-mode", "acceptEdits",
        "--allowedTools", *ALLOWED_TOOLS,
        "--json-schema", schema_json,
    ]
    assert ALLOWED_TOOLS == (
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(python3 -m pytest*)", "Bash(python3 -m daily_report*)", "Bash(git diff*)", "Bash(git status*)",
    )
    joined = " ".join(argv)
    assert "bypassPermissions" not in joined and "dangerously" not in joined
    assert str(worktree) not in joined and "-C" not in argv and "--add-dir" not in argv
    assert "response-after" not in joined and "인계" not in joined


# --- 정상 ---------------------------------------------------------------------------------


def test_full_run_is_ready_for_review_with_seven_artifacts(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "full")
    base = register(state_conn, repo)
    request = request_for(base)
    progress = Progress()

    output = adapter(state_conn).run(request, handoff, progress)

    assert output.failed is None, output.failed
    result = output.result
    assert result.outcome == "ready_for_review"
    assert result.base_commit == base and result.result_commit != base
    assert result.artifact_ids == []  # runner 가 채운다
    kinds = [meta.kind for meta, _ in output.artifacts]
    assert kinds == ["diff", "test_log_before", "test_log_after", "verification_log", "report_output",
                     "claude_jsonl", "claude_stderr"]
    names = {meta.kind: meta.name for meta, _ in output.artifacts}
    assert names["claude_jsonl"] == "claude.jsonl" and names["claude_stderr"] == "claude-stderr.txt"
    artifacts = by_kind(output)
    assert artifacts["test_log_before"].decode().splitlines()[0] == "exit_code=1"
    assert artifacts["test_log_after"].decode().splitlines()[0] == "exit_code=0"
    assert artifacts["verification_log"].decode().splitlines()[0] == "exit_code=0"
    report = artifacts["report_output"].decode()
    assert "2026-09-19" in report and "합계    20    5" in report
    assert "transformer.py" in artifacts["diff"].decode() and "test_repro.py" in artifacts["diff"].decode()
    assert b"fake claude: done" in artifacts["claude_stderr"]
    # 검증은 별도 실행: verification 은 result_commit 을 가리키고 exit 0
    assert result.verification.profile_id == "vp-pytest"
    assert result.verification.result_commit == result.result_commit
    assert result.verification.exit_code == 0 and result.verification.log_artifact_id == "verification_log"
    # 결과 커밋은 task/<task_id> 브랜치 HEAD, 원본 저장소는 불변
    worktree = repo.parent / "demo-report-repo-worktrees" / request.task_id
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"task/{request.task_id}"
    assert _git(worktree, "rev-parse", "HEAD") == result.result_commit
    assert _git(repo, "rev-parse", "HEAD") == base and _git(repo, "status", "--porcelain") == ""
    # started 는 runtime_ref 로 먼저 알린다
    assert progress.runtime_refs and progress.runtime_refs[0].startswith("pid:")
    assert output.runtime_ref == progress.runtime_refs[0]
    assert any("Claude 종료" in message for message, _ in progress.calls)
    assert "items 와 data.records" in result.summary


def test_claude_runs_in_worktree_with_fixed_argv_and_prompt_on_stdin(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "full")
    base = register(state_conn, repo)
    request = request_for(base)

    output = adapter(state_conn).run(request, handoff, Progress())

    fake = envelope_of(output)["_fake"]
    worktree = repo.parent / "demo-report-repo-worktrees" / request.task_id
    assert Path(fake["cwd"]).resolve() == worktree.resolve()
    assert fake["argv"] == adapter(state_conn).build_argv(worktree, json.dumps(RESULT_SCHEMA, ensure_ascii=False))[1:]
    assert request.request not in " ".join(fake["argv"]) and str(handoff) not in " ".join(fake["argv"])
    assert fake["prompt_chars"] > 100 and fake["prompt_head"].startswith("# 업무")


def test_successful_result_with_429_in_usage_is_not_a_usage_limit(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "full")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert b"429" in by_kind(output)["claude_jsonl"]
    assert output.failed is None and output.result.outcome == "ready_for_review"


# --- 보류 ---------------------------------------------------------------------------------


def test_is_error_result_is_needs_information_with_parse_note(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "is_error")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.failed is None
    result = output.result
    assert result.outcome == "needs_information"
    assert "is_error" in result.summary and "tool execution failed" in result.summary
    assert result.result_commit is not None and result.result_commit != base  # 작업은 커밋으로 보존


def test_missing_structured_output_is_needs_information(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "no_structured")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.failed is None and output.result.outcome == "needs_information"
    assert "구조화 출력 없음" in output.result.summary


def test_no_change_is_needs_information_without_commit(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "noop")
    base = register(state_conn, repo)
    request = request_for(base)

    output = adapter(state_conn).run(request, handoff, Progress())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert result.summary.startswith("변경 없음") and result.result_commit is None
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]


def test_fix_without_repro_test_is_needs_information_but_preserved(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "fix_only")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert "재현 테스트 없음" in result.summary and result.result_commit not in (None, base)


# --- 실패 ---------------------------------------------------------------------------------


def test_usage_limit_in_result_is_failed_usage_limit(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "usage_limit")
    base = register(state_conn, repo)
    progress = Progress()

    output = adapter(state_conn).run(request_for(base), handoff, progress)

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "usage_limit" and stopped is True and "limit" in message
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]
    assert output.runtime_ref == progress.runtime_refs[0]


def test_rate_limit_on_stderr_without_result_json_is_failed_usage_limit(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "rate_limit_429")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.result is None and output.failed[0] == "usage_limit" and output.failed[2] is True
    assert "429" in output.failed[1] or "rate_limit" in output.failed[1]
    assert b"rate_limit_error" in by_kind(output)["claude_stderr"]


def test_timeout_terminates_process_and_fails_with_process_stopped(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "sleep")
    base = register(state_conn, repo)
    progress = Progress()

    output = adapter(state_conn, timeout_seconds=1).run(request_for(base), handoff, progress)

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "timeout" and stopped is True and "1" in message
    assert progress.runtime_refs and progress.runtime_refs[0].startswith("pid:")
    assert [meta.kind for meta, _ in output.artifacts] == ["claude_jsonl", "claude_stderr"]


def test_missing_executable_is_claude_unavailable(state_conn, repo, handoff):
    base = register(state_conn, repo)

    output = adapter(state_conn, claude_bin="/nonexistent/claude").run(request_for(base), handoff, Progress())

    assert output.result is None and output.failed[0] == "claude_unavailable" and output.failed[2] is True


# --- 환경·비밀값 ----------------------------------------------------------------------------


def test_child_env_keeps_claude_and_anthropic_vars_but_drops_secrets(state_conn, repo, handoff, fake_bin,
                                                                   monkeypatch):
    write_fake_claude(fake_bin, "full")
    monkeypatch.setenv("OPENAI_API_KEY", SK)
    monkeypatch.setenv("WORKFLOW_CONNECTOR_HOME", "/tmp/nope")
    monkeypatch.setenv("WORKFLOW_CONNECTOR_TOKEN", WFC)
    monkeypatch.setenv("DIAG_API_TOKEN", "diag")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/claude-cfg")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    env = envelope_of(output)["_fake"]["env"]
    assert "PATH" in env and "HOME" in env
    assert env["ANTHROPIC_MODEL"] == "claude-x" and env["CLAUDE_CONFIG_DIR"] == "/tmp/claude-cfg"
    assert "OPENAI_API_KEY" not in env and "DIAG_API_TOKEN" not in env
    assert not any(key.startswith(("WORKFLOW_", "DIAG_", "OPENAI_")) for key in env)


def test_tool_env_is_only_for_the_claude_process_not_verification(state_conn):
    base_env = {**os.environ, "ANTHROPIC_API_KEY": SK, "OPENAI_API_KEY": SK, "WORKFLOW_CONNECTOR_HOME": "/x"}
    claude = ClaudeAdapter(state_conn, env_base=base_env)

    assert claude.tool_env()["ANTHROPIC_API_KEY"] == SK
    assert "ANTHROPIC_API_KEY" not in claude.child_env()  # 검증 프로필(pytest)에는 넘기지 않는다
    assert not {"OPENAI_API_KEY", "WORKFLOW_CONNECTOR_HOME"} & set(claude.tool_env())


def test_secrets_in_claude_stdout_and_stderr_are_masked(state_conn, repo, handoff, fake_bin):
    write_fake_claude(fake_bin, "leak")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    artifacts = by_kind(output)
    stdout, stderr = artifacts["claude_jsonl"].decode(), artifacts["claude_stderr"].decode()
    assert SK not in stdout and "sk-***" in stdout
    assert WFC not in stderr and "wfc_***" in stderr
    meta = next(meta for meta, _ in output.artifacts if meta.kind == "claude_jsonl")
    assert meta.size == len(stdout.encode())


# --- parse_last_message 단위 -----------------------------------------------------------------


def _envelope(**fields) -> str:
    base = {"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": None,
            "api_error_status": None}
    return json.dumps({**base, **fields}, ensure_ascii=False)


@pytest.mark.parametrize("raw, note", [
    (None, "결과 JSON 없음"),
    ("not json", "결과 형식 불일치"),
    (_envelope(is_error=True, result="Error: boom"), "is_error: Error: boom"),
    (_envelope(result="text only"), "구조화 출력 없음"),
    (_envelope(structured_output={"outcome": "done"}), "구조화 출력 스키마 불일치"),
])
def test_parse_last_message_needs_information_with_note(state_conn, raw, note):
    parsed = adapter(state_conn).parse_last_message(raw)

    assert parsed.outcome == "needs_information"
    assert parsed.parse_note is not None and note in parsed.parse_note
    assert "Claude 마지막 메시지" in parsed.summary and note in parsed.summary


def test_parse_last_message_reads_structured_output(state_conn):
    raw = _envelope(structured_output={
        "summary": "고쳤다", "outcome": "ready_for_review", "files_changed": ["a.py"], "notes": "",
    })

    parsed = adapter(state_conn).parse_last_message(raw)

    assert parsed.outcome == "ready_for_review" and parsed.summary == "고쳤다" and parsed.parse_note is None


def test_classify_failure_is_none_for_a_successful_result(state_conn):
    run = ToolRun(
        pid=1, started_at="2026-09-20T01:00:00Z", exit_code=0,
        stdout=b'{"usage": {"input_tokens": 429}}', stderr=b"", timed_out=False, stopped=True,
        last_message=_envelope(structured_output={"summary": "s", "outcome": "ready_for_review",
                                                  "files_changed": [], "notes": ""}),
    )

    assert adapter(state_conn).classify_failure(run) is None
