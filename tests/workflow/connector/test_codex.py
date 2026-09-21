"""codex — 실제 Codex 를 띄우는 어댑터. 여기서는 PATH 앞에 둔 **가짜 `codex`** 스크립트로만 돈다 (실연동은 Step 15).

`make_repo` 는 Step 13 의 `scripts/scaffold_demo_repo.py` 로 데모 저장소를 만든다: `daily_report/transformer.py` 는
수정 전(`items` 만), `python3 -m pytest -q`, `python3 -m daily_report <response.json>`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from workflow.connector import git_ops, state
from workflow.connector.codex import CodexAdapter
from workflow.contracts.v1 import ExecutionRequest

from .conftest import REVIEW_SPEC, make_local_request, make_request

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from scaffold_demo_repo import scaffold

# --- 데모 저장소 (Step 13 스크립트로 생성) --------------------------------------------------


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    """수정 전 데모 저장소. scripts/scaffold_demo_repo.py 가 만드는 것과 같다 (커밋 1개, 태그 report-base)."""
    repo = tmp_path / "demo-report-repo"
    scaffold(repo)
    return repo


# --- 가짜 codex ----------------------------------------------------------------------------

_FAKE_CODEX = '''#!/usr/bin/env python3
"""가짜 codex. MODE 에 따라 -C 디렉터리를 고치고 JSONL 을 stdout 에, 마지막 메시지를 파일에 쓴다."""
import json
import os
import sys
import time

MODE = %(mode)r
args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1]


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


if MODE == "forbidden":
    print("real codex must not run in tests", file=sys.stderr)
    sys.exit(99)

worktree = opt("-C")
last_message = opt("--output-last-message")
prompt = sys.stdin.read()
emit({"type": "thread.started", "thread_id": "fake-thread"})
emit({"type": "argv", "argv": args})
emit({"type": "env", "env": dict(os.environ)})
emit({"type": "prompt", "chars": len(prompt), "head": prompt[:60]})

if MODE.startswith("generic"):
    # 사용자 정의 종류 — 스키마 파일의 enum 을 읽고 {outcome, summary} 만 낸다. cwd 는 인계 디렉터리.
    with open(opt("--output-schema"), encoding="utf-8") as f:
        schema = json.load(f)
    emit({"type": "readonly", "cwd": os.getcwd(), "schema": schema, "prompt_first_line": prompt.splitlines()[0]})
    enum = schema["properties"]["outcome"]["enum"]
    outcome = "rejected" if MODE == "generic_bad_outcome" else enum[0]
    if MODE == "generic_writes":
        with open(os.path.join(worktree, "notes.md"), "w", encoding="utf-8") as f:
            f.write("must not happen")
    with open(last_message, "w", encoding="utf-8") as f:
        json.dump({"outcome": outcome, "summary": "대본 검토: 인계 자료를 읽고 승인"}, f, ensure_ascii=False)
    emit({"type": "turn.completed"})
    print("fake codex: done", file=sys.stderr)
    sys.exit(0)

FIXED = """\\"\\"\\"응답 변환부 — items 또는 data.records 중 정확히 하나를 읽는다.\\"\\"\\"

from dataclasses import dataclass


class TransformError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Row:
    team: str
    completed: int
    pending: int


@dataclass(frozen=True)
class Report:
    report_date: str
    rows: tuple[Row, ...]


def _row(item) -> Row:
    if not isinstance(item, dict) or not isinstance(item.get("team"), str):
        raise TransformError("INVALID_ROW", f"item={item!r}")
    completed, pending = item.get("completed"), item.get("pending")
    for value in (completed, pending):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TransformError("INVALID_ROW", f"item={item!r}")
    return Row(item["team"], completed, pending)


def _records(response: dict) -> list:
    has_items = "items" in response
    data = response.get("data")
    has_records = isinstance(data, dict) and "records" in data
    if has_items and has_records:
        raise TransformError("AMBIGUOUS_RECORDS_FIELD", "both $.items and $.data.records present")
    records = response.get("items") if has_items else (data or {}).get("records")
    if not isinstance(records, list):
        raise TransformError(
            "MISSING_RECORDS_FIELD",
            f"expected_path=$.items|$.data.records observed_root_keys={sorted(response)}",
        )
    return records


def transform(response: dict) -> Report:
    if not isinstance(response.get("report_date"), str):
        raise TransformError("MISSING_REPORT_DATE")
    return Report(response["report_date"], tuple(_row(item) for item in _records(response)))
"""

REPRO = """from daily_report.transformer import transform


def test_data_records_path_is_supported():
    report = transform({"report_date": "2026-09-19", "data": {"records": [
        {"team": "운영", "completed": 12, "pending": 3},
        {"team": "개발", "completed": 8, "pending": 2},
    ]}})
    assert [(r.team, r.completed, r.pending) for r in report.rows] == [("운영", 12, 3), ("개발", 8, 2)]
"""

if MODE == "sleep":
    time.sleep(60)
if MODE == "leak":
    emit({"type": "item", "text": "token wfc_" + "a" * 43 + " and sk-" + "b" * 20})
if MODE in ("full", "leak", "fix_only"):
    with open(os.path.join(worktree, "daily_report", "transformer.py"), "w", encoding="utf-8") as f:
        f.write(FIXED)
if MODE in ("full", "leak"):
    with open(os.path.join(worktree, "tests", "test_repro.py"), "w", encoding="utf-8") as f:
        f.write(REPRO)
if MODE != "no_last_message":
    files = [] if MODE == "noop" else ["daily_report/transformer.py"]
    with open(last_message, "w", encoding="utf-8") as f:
        json.dump({
            "summary": "items 와 data.records 중 하나를 읽도록 변환부를 고쳤다",
            "outcome": "ready_for_review" if MODE != "noop" else "needs_information",
            "files_changed": files,
            "notes": "",
        }, f, ensure_ascii=False)
emit({"type": "turn.completed"})
print("fake codex: done", file=sys.stderr)
'''


def write_fake_codex(bin_dir: Path, mode: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(_FAKE_CODEX % {"mode": mode})
    script.chmod(0o755)
    return script


@pytest.fixture(autouse=True)
def fake_bin(tmp_path, monkeypatch) -> Path:
    """모든 테스트에서 PATH 앞의 `codex` 는 가짜다. 기본은 호출되면 실패하는 forbidden 모드."""
    bin_dir = tmp_path / "fakebin"
    write_fake_codex(bin_dir, "forbidden")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


RESPONSE_AFTER = {
    "report_date": "2026-09-19",
    "data": {"records": [
        {"team": "운영", "completed": 12, "pending": 3},
        {"team": "개발", "completed": 8, "pending": 2},
    ]},
}


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


def register(state_conn, repo: Path, *, profiles: dict | None = None) -> str:
    base = git_ops.head_sha(repo)
    state.save_registration(state_conn, {
        "local_registration_id": "local-demo-report",
        "repo_path": str(repo),
        "tool": "codex",
        "repository_id": "demo-report-repo",
        "base_commit": base,
        "verification_profiles": profiles if profiles is not None else {
            "vp-pytest": [sys.executable, "-m", "pytest", "-q"],
            "vp-report": [sys.executable, "-m", "daily_report", "{response}"],
        },
    })
    return base


def request_for(base_commit: str, **overrides) -> ExecutionRequest:
    request = make_request()
    target = {**request.target.model_dump(), "base_commit": base_commit, **overrides}
    return request.model_copy(update={"target": type(request.target).model_validate(target)})


class Progress:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, message: str, *, runtime_ref: str | None = None) -> None:
        self.calls.append((message, runtime_ref))

    @property
    def runtime_refs(self) -> list[str]:
        return [ref for _, ref in self.calls if ref is not None]


def adapter(state_conn, **kwargs) -> CodexAdapter:
    return CodexAdapter(state_conn, env_base=os.environ, verification_timeout=60, **kwargs)


def by_kind(output) -> dict[str, bytes]:
    return {meta.kind: data for meta, data in output.artifacts}


# --- argv --------------------------------------------------------------------------------


def test_build_argv_is_fixed_and_carries_no_prompt_or_evidence(state_conn, tmp_path):
    worktree = tmp_path / "wt"
    argv = adapter(state_conn).build_argv(worktree, tmp_path / "schema.json", tmp_path / "last.json")

    assert argv == [
        "codex", "exec", "--json", "-C", str(worktree), "--sandbox", "workspace-write",
        "-c", 'approval_policy="never"',
        "--output-schema", str(tmp_path / "schema.json"), "--output-last-message", str(tmp_path / "last.json"),
        "-",
    ]
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv and "--worktree" not in argv
    joined = " ".join(argv)
    assert "response-after" not in joined and "인계" not in joined


# --- 정상 --------------------------------------------------------------------------------


def test_full_run_is_ready_for_review_with_seven_artifacts(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "full")
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
                     "codex_jsonl", "codex_stderr"]
    artifacts = by_kind(output)
    assert artifacts["test_log_before"].decode().splitlines()[0] == "exit_code=1"
    assert artifacts["test_log_after"].decode().splitlines()[0] == "exit_code=0"
    assert artifacts["verification_log"].decode().splitlines()[0] == "exit_code=0"
    report = artifacts["report_output"].decode()
    assert "2026-09-19" in report and "합계    20    5" in report
    assert "transformer.py" in artifacts["diff"].decode() and "test_repro.py" in artifacts["diff"].decode()
    assert b"fake codex: done" in artifacts["codex_stderr"]
    lines = [json.loads(line) for line in artifacts["codex_jsonl"].decode().splitlines()]
    assert lines[0]["type"] == "thread.started" and lines[-1]["type"] == "turn.completed"
    # 검증은 별도 실행: verification 은 result_commit 을 가리키고 exit 0, log 자리표시는 runner 가 채운다
    assert result.verification.profile_id == "vp-pytest"
    assert result.verification.result_commit == result.result_commit
    assert result.verification.exit_code == 0 and result.verification.log_artifact_id == "verification_log"
    # 결과 커밋은 task/<task_id> 브랜치 HEAD, 원본 저장소는 불변
    worktree = repo.parent / "demo-report-repo-worktrees" / request.task_id
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"task/{request.task_id}"
    assert _git(worktree, "rev-parse", "HEAD") == result.result_commit
    assert _git(repo, "rev-parse", "HEAD") == base and _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(repo, "status", "--porcelain") == ""
    assert "records" not in (repo / "daily_report" / "transformer.py").read_text()
    assert not (repo / "tests" / "test_repro.py").exists()
    # 임시 체크아웃은 정리됐고 업무 worktree 만 남는다
    listed = _git(repo, "worktree", "list")
    assert str(worktree) in listed and listed.count("\n") == 1
    # started 는 runtime_ref 로 먼저 알린다
    assert progress.runtime_refs and progress.runtime_refs[0].startswith("pid:")
    assert output.runtime_ref == progress.runtime_refs[0]
    assert any("Codex 종료" in message for message, _ in progress.calls)
    # 마지막 메시지의 요약이 결과 요약에 쓰인다
    assert "items 와 data.records" in result.summary


def test_codex_env_is_allowlisted_without_secrets(state_conn, repo, handoff, fake_bin, monkeypatch):
    write_fake_codex(fake_bin, "full")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "x" * 20)
    monkeypatch.setenv("WORKFLOW_CONNECTOR_HOME", "/tmp/nope")
    monkeypatch.setenv("DIAG_API_TOKEN", "diag")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    lines = [json.loads(line) for line in by_kind(output)["codex_jsonl"].decode().splitlines()]
    env = next(line["env"] for line in lines if line["type"] == "env")
    assert "PATH" in env and "HOME" in env
    assert "OPENAI_API_KEY" not in env and "WORKFLOW_CONNECTOR_HOME" not in env and "DIAG_API_TOKEN" not in env
    assert not any(key.startswith("WORKFLOW_") for key in env)


def test_prompt_goes_to_stdin_not_argv(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "full")
    base = register(state_conn, repo)
    request = request_for(base)

    output = adapter(state_conn).run(request, handoff, Progress())

    lines = [json.loads(line) for line in by_kind(output)["codex_jsonl"].decode().splitlines()]
    argv = next(line["argv"] for line in lines if line["type"] == "argv")
    prompt = next(line for line in lines if line["type"] == "prompt")
    assert argv[-1] == "-" and request.request not in " ".join(argv)
    assert prompt["chars"] > 100


# --- 보류·실패 ------------------------------------------------------------------------------


def test_fix_without_repro_test_is_needs_information_but_preserved(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "fix_only")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert "재현 테스트 없음" in result.summary
    assert result.result_commit is not None and result.result_commit != base  # 작업은 커밋으로 보존
    artifacts = by_kind(output)
    assert artifacts["test_log_before"].decode() == "exit_code=0\n(재현 테스트 없음)\n"
    assert artifacts["test_log_after"].decode().splitlines()[0] == "exit_code=0"
    assert "transformer.py" in artifacts["diff"].decode()


def test_no_change_is_needs_information_without_commit(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "noop")
    base = register(state_conn, repo)
    request = request_for(base)

    output = adapter(state_conn).run(request, handoff, Progress())

    result = output.result
    assert output.failed is None and result.outcome == "needs_information"
    assert result.summary.startswith("변경 없음") and result.result_commit is None and result.verification is None
    assert [meta.kind for meta, _ in output.artifacts] == ["codex_jsonl", "codex_stderr"]
    worktree = repo.parent / "demo-report-repo-worktrees" / request.task_id
    assert _git(worktree, "rev-parse", "HEAD") == base


def test_unreadable_last_message_still_yields_result(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "no_last_message")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.failed is None and output.result.outcome == "needs_information"
    assert "마지막 메시지" in output.result.summary


def test_timeout_terminates_process_and_fails_with_process_stopped(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "sleep")
    base = register(state_conn, repo)
    progress = Progress()

    output = adapter(state_conn, timeout_seconds=1).run(request_for(base), handoff, progress)

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "timeout" and stopped is True and "1" in message
    assert progress.runtime_refs and progress.runtime_refs[0].startswith("pid:")
    kinds = [meta.kind for meta, _ in output.artifacts]
    assert kinds == ["codex_jsonl", "codex_stderr"]  # 부분 출력도 보존
    assert b"thread.started" in by_kind(output)["codex_jsonl"]


def test_secrets_in_codex_stdout_are_masked(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "leak")
    base = register(state_conn, repo)

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    jsonl = by_kind(output)["codex_jsonl"].decode()
    assert "wfc_" + "a" * 43 not in jsonl and "sk-" + "b" * 20 not in jsonl
    assert "wfc_***" in jsonl and "sk-***" in jsonl
    meta = next(meta for meta, _ in output.artifacts if meta.kind == "codex_jsonl")
    assert meta.size == len(jsonl.encode())


def test_retry_reuses_same_worktree_and_branch(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "full")
    base = register(state_conn, repo)
    first = adapter(state_conn).run(request_for(base), handoff, Progress())
    worktree = repo.parent / "demo-report-repo-worktrees" / "fix-daily-0920"

    second = adapter(state_conn).run(
        request_for(first.result.result_commit).model_copy(update={"execution_id": "exec-fix-002"}),
        handoff, Progress(),
    )

    assert second.failed is None and second.result.outcome == "needs_information"  # 같은 내용 → 변경 없음
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "task/fix-daily-0920"
    assert _git(worktree, "rev-parse", "HEAD") == first.result.result_commit
    assert len([p for p in worktree.parent.iterdir() if p.is_dir() and not p.name.endswith(".handoff")]) == 1


def test_missing_registration_fails_before_launching_codex(state_conn, handoff):
    progress = Progress()

    output = adapter(state_conn).run(make_request(), handoff, progress)

    assert output.result is None and output.failed[0] == "registration_missing" and output.failed[2] is True
    assert progress.runtime_refs == [] and output.artifacts == []


def test_missing_base_commit_fails_before_launching_codex(state_conn, repo, handoff):
    register(state_conn, repo)
    progress = Progress()

    output = adapter(state_conn).run(request_for("0" * 40), handoff, progress)

    assert output.failed[0] == "base_commit_missing" and output.failed[2] is True
    assert progress.runtime_refs == []


def test_missing_verification_profile_fails_before_launching_codex(state_conn, repo, handoff):
    base = register(state_conn, repo, profiles={"vp-report": [sys.executable, "-m", "daily_report", "{response}"]})

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.failed[0] == "verification_profile_missing" and "vp-pytest" in output.failed[1]


def test_missing_report_profile_is_recorded_in_report_output(state_conn, repo, handoff, fake_bin):
    write_fake_codex(fake_bin, "full")
    base = register(state_conn, repo, profiles={"vp-pytest": [sys.executable, "-m", "pytest", "-q"]})

    output = adapter(state_conn).run(request_for(base), handoff, Progress())

    assert output.failed is None and output.result.outcome == "ready_for_review"
    report = by_kind(output)["report_output"].decode()
    assert report.startswith("exit_code=") and "vp-report" in report


def test_diagnosis_request_is_unsupported(state_conn, handoff):
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-diagnose-001", "task_id": "diagnose-daily-0920",
        "kind": "diagnosis", "agent_id": "agent-ops-demo", "task_revision": 1, "request": "조사",
        "input_artifact_ids": [], "target": {"run_id": "daily-0920-0900"},
    })

    output = adapter(state_conn).run(request, handoff, Progress())

    assert output.result is None and output.failed[0] == "unsupported_kind"


# --- 사용자 정의 종류 — 읽기 전용 실행 (`--sandbox read-only`, cwd = 인계 디렉터리) ----------------------------


@pytest.fixture
def review_handoff(tmp_path) -> Path:
    handoff = tmp_path / "review-daily-0920.handoff"
    handoff.mkdir()
    (handoff / "manifest.json").write_text('{"source_kind": "code_change"}')
    (handoff / "diff.patch").write_text("--- a\n+++ b\n")
    (handoff / "code_change_result.json").write_text('{"outcome": "ready_for_review"}')
    return handoff


def test_build_readonly_argv_uses_read_only_sandbox_and_handoff_cwd(state_conn, tmp_path):
    handoff = tmp_path / "review.handoff"
    codex = adapter(state_conn)

    argv = codex.build_readonly_argv(handoff, tmp_path / "schema.json", tmp_path / "last.json")

    assert argv == [
        "codex", "exec", "--json", "-C", str(handoff), "--sandbox", "read-only",
        "-c", 'approval_policy="never"',
        "--output-schema", str(tmp_path / "schema.json"), "--output-last-message", str(tmp_path / "last.json"),
        "-",
    ]
    assert "workspace-write" not in argv and "--worktree" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    # 기존 코드 수정 argv 는 그대로
    assert codex.build_argv(handoff, tmp_path / "schema.json", tmp_path / "last.json")[5:7] == [
        "--sandbox", "workspace-write",
    ]


def test_generic_run_is_readonly_in_handoff_dir_with_outcome_schema(state_conn, repo, review_handoff, fake_bin):
    write_fake_codex(fake_bin, "generic")
    register(state_conn, repo)
    request = make_local_request()
    progress = Progress()
    before = sorted(p.name for p in review_handoff.iterdir())

    output = adapter(state_conn).run(request, review_handoff, progress)

    assert output.failed is None, output.failed
    result = output.result
    assert (result.kind, result.outcome) == ("review", "approved")
    assert result.summary == "대본 검토: 인계 자료를 읽고 승인" and result.artifact_ids == []
    assert [meta.kind for meta, _ in output.artifacts] == ["codex_jsonl", "codex_stderr"]
    lines = [json.loads(line) for line in by_kind(output)["codex_jsonl"].decode().splitlines()]
    argv = next(line["argv"] for line in lines if line["type"] == "argv")
    readonly = next(line for line in lines if line["type"] == "readonly")
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("-C") + 1] == str(review_handoff)
    assert Path(readonly["cwd"]).resolve() == review_handoff.resolve()
    assert readonly["schema"]["properties"]["outcome"]["enum"] == REVIEW_SPEC.outcomes
    assert readonly["schema"]["required"] == ["outcome", "summary"]
    assert readonly["prompt_first_line"] == "# 업무 종류: review (검토)"
    assert request.request not in " ".join(argv) and REVIEW_SPEC.instructions not in " ".join(argv)
    # worktree·커밋 없음, 인계 디렉터리 그대로
    assert not (repo.parent / "demo-report-repo-worktrees").exists()
    assert _git(repo, "worktree", "list").count("\n") == 0 and _git(repo, "status", "--porcelain") == ""
    assert sorted(p.name for p in review_handoff.iterdir()) == before
    assert progress.runtime_refs and progress.runtime_refs[0].startswith("pid:")
    assert output.runtime_ref == progress.runtime_refs[0]


def test_generic_env_is_allowlisted_without_secrets(state_conn, repo, review_handoff, fake_bin, monkeypatch):
    write_fake_codex(fake_bin, "generic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "x" * 20)
    monkeypatch.setenv("WORKFLOW_CONNECTOR_TOKEN", "wfc_" + "t" * 43)
    register(state_conn, repo)

    output = adapter(state_conn).run(make_local_request(), review_handoff, Progress())

    lines = [json.loads(line) for line in by_kind(output)["codex_jsonl"].decode().splitlines()]
    env = next(line["env"] for line in lines if line["type"] == "env")
    assert "PATH" in env and "OPENAI_API_KEY" not in env and "WORKFLOW_CONNECTOR_TOKEN" not in env


def test_generic_outcome_outside_spec_is_result_invalid(state_conn, repo, review_handoff, fake_bin):
    write_fake_codex(fake_bin, "generic_bad_outcome")
    register(state_conn, repo)

    output = adapter(state_conn).run(make_local_request(), review_handoff, Progress())

    assert output.result is None
    code, message, stopped = output.failed
    assert code == "result_invalid" and stopped is True and "rejected" in message
    assert [meta.kind for meta, _ in output.artifacts] == ["codex_jsonl", "codex_stderr"]


def test_generic_write_into_handoff_dir_is_readonly_violation(state_conn, repo, review_handoff, fake_bin):
    write_fake_codex(fake_bin, "generic_writes")
    register(state_conn, repo)

    output = adapter(state_conn).run(make_local_request(), review_handoff, Progress())

    assert output.result is None and output.failed[0] == "readonly_violation" and "notes.md" in output.failed[1]


def test_generic_missing_registration_fails_before_launching_codex(state_conn, review_handoff):
    progress = Progress()

    output = adapter(state_conn).run(make_local_request(), review_handoff, progress)

    assert output.failed[0] == "registration_missing" and progress.runtime_refs == [] and output.artifacts == []


@pytest.mark.parametrize("raw, outcome, note", [
    ('{"outcome": "approved", "summary": "좋다"}', "approved", None),
    ('{"outcome": "rejected", "summary": "나쁘다"}', "rejected", "허용되지 않은 outcome 'rejected'"),
    ('{"summary": "outcome 없음"}', "", "허용되지 않은 outcome ''"),
    ("not json", "", "스키마 불일치"),
    (None, "", "파일 없음"),
])
def test_parse_generic_message(state_conn, raw, outcome, note):
    parsed = adapter(state_conn).parse_generic_message(raw, ["approved", "changes_requested"])

    assert parsed.outcome == outcome
    if note is None:
        assert parsed.parse_note is None and parsed.summary == "좋다"
    else:
        assert parsed.parse_note is not None and note in parsed.parse_note
