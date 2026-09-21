"""scripted.claude — `claude -p --output-format json … --json-schema <schema>` 인자 형식, 프롬프트 stdin, 작업 위치 cwd.

stdout 의 `type=result` 봉투 한 덩어리를 실제 `ClaudeAdapter`(`_result_envelope` → `parse_last_message`)가 읽어
`ready_for_review` 를 내야 한다. 실제 claude 는 호출하지 않는다.
"""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.workflow.connector.conftest import REVIEW_SPEC
from workflow.connector.claude import ClaudeAdapter, _result_envelope
from workflow.connector.local_tool import RESULT_SCHEMA, ToolRun, generic_result_schema
from workflow.scripted import SCRIPT_MODEL_ID, _common
from workflow.scripted import claude as scripted_claude
from workflow.scripted._common import PACE_ENV

from .conftest import generic_prompt_for, prompt_for


def argv_for(worktree: Path) -> list[str]:
    return ClaudeAdapter(None).build_argv(worktree, json.dumps(RESULT_SCHEMA, ensure_ascii=False))


def run_main(monkeypatch, capsys, worktree: Path, prompt: str) -> tuple[int, str, str, float]:
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(sys, "stdin", io.StringIO(prompt))
    started = time.perf_counter()
    code = scripted_claude.main(argv_for(worktree))
    elapsed = time.perf_counter() - started
    out, err = capsys.readouterr()
    return code, out, err, elapsed


def test_full_run_fixes_cwd_worktree_and_prints_one_result_envelope(worktree, handoff, monkeypatch, capsys):
    before = (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8")

    code, out, err, elapsed = run_main(monkeypatch, capsys, worktree, prompt_for(handoff, worktree))

    assert code == 0 and elapsed < 1.0
    assert out.count("\n") == 1  # 봉투 한 덩어리
    raw = _result_envelope(out.encode("utf-8"))
    assert raw is not None
    envelope = json.loads(raw)
    assert envelope["type"] == "result" and envelope["subtype"] == "success" and envelope["is_error"] is False
    assert envelope["session_id"] == "scripted-claude"
    assert envelope["usage"] == {"input_tokens": 0, "output_tokens": 0}
    structured = envelope["structured_output"]
    assert structured["outcome"] == "ready_for_review" and structured["notes"] == "scripted"
    assert structured["files_changed"] == ["tests/test_repro_records.py", "daily_report/transformer.py"]
    assert envelope["result"] == structured["summary"]
    assert (worktree / "tests" / "test_repro_records.py").is_file()
    assert (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8") != before
    assert "scripted claude" in err

    adapter = ClaudeAdapter(None)
    parsed = adapter.parse_last_message(raw)
    assert parsed.outcome == "ready_for_review" and parsed.parse_note is None
    assert "items 와 data.records" in parsed.summary
    run = ToolRun(pid=1, started_at="2026-09-21T00:00:00Z", exit_code=0, stdout=out.encode(), stderr=err.encode(),
                  timed_out=False, stopped=True, last_message=raw)
    assert adapter.classify_failure(run) is None


def test_missing_handoff_response_changes_nothing_and_is_needs_information(worktree, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty.handoff"
    empty.mkdir()
    snapshot = {p: p.read_text(encoding="utf-8") for p in worktree.rglob("*.py")}

    code, out, _, _ = run_main(monkeypatch, capsys, worktree, prompt_for(empty, worktree))

    assert code == 0
    assert {p: p.read_text(encoding="utf-8") for p in worktree.rglob("*.py")} == snapshot
    raw = _result_envelope(out.encode("utf-8"))
    envelope = json.loads(raw)
    assert envelope["is_error"] is False and envelope["structured_output"]["files_changed"] == []
    parsed = ClaudeAdapter(None).parse_last_message(raw)
    assert parsed.outcome == "needs_information" and parsed.parse_note is None


@pytest.mark.parametrize("raw", ["0", "abc", "-3"])
def test_zero_or_bad_pace_never_sleeps(worktree, handoff, monkeypatch, capsys, raw):
    monkeypatch.setenv(PACE_ENV, raw)
    monkeypatch.setattr(_common.time, "sleep", lambda s: pytest.fail(f"sleep({s}) 호출"))

    code, _, _, elapsed = run_main(monkeypatch, capsys, worktree, prompt_for(handoff, worktree))

    assert code == 0 and elapsed < 1.0


def test_output_never_names_a_real_model(worktree, handoff, monkeypatch, capsys):
    _, out, err, _ = run_main(monkeypatch, capsys, worktree, prompt_for(handoff, worktree))

    assert "gpt-" not in out + err and "claude-" not in out + err and SCRIPT_MODEL_ID in out


def test_runs_as_module_in_cwd_with_prompt_on_stdin(worktree, handoff):
    completed = subprocess.run(
        [sys.executable, "-m", "workflow.scripted.claude", *argv_for(worktree)[1:]],
        input=prompt_for(handoff, worktree), capture_output=True, text=True,
        env={**os.environ, PACE_ENV: "0"}, cwd=worktree,
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["type"] == "result" and envelope["structured_output"]["outcome"] == "ready_for_review"
    assert (worktree / "tests" / "test_repro_records.py").is_file()


def test_without_print_flag_exits_nonzero_without_reading_stdin(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "workflow.scripted.claude", "--help"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=tmp_path,
    )
    assert completed.returncode != 0
    assert "-p" in completed.stderr


# --- 사용자 정의 종류 — `build_readonly_argv`(--allowedTools Read Glob Grep, --json-schema) 를 그대로 받는다 ------


def readonly_argv_for(handoff: Path, outcomes: list[str] = REVIEW_SPEC.outcomes) -> list[str]:
    return ClaudeAdapter(None).build_readonly_argv(handoff, json.dumps(generic_result_schema(outcomes), ensure_ascii=False))


def run_generic(monkeypatch, capsys, handoff: Path, argv: list[str]) -> tuple[int, str, str, float]:
    monkeypatch.chdir(handoff)
    monkeypatch.setattr(sys, "stdin", io.StringIO(generic_prompt_for(handoff)))
    started = time.perf_counter()
    code = scripted_claude.main(argv)
    elapsed = time.perf_counter() - started
    out, err = capsys.readouterr()
    return code, out, err, elapsed


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_generic_kind_answers_first_outcome_from_schema_and_writes_nothing(review_handoff, monkeypatch, capsys):
    before = _snapshot(review_handoff)

    code, out, err, elapsed = run_generic(monkeypatch, capsys, review_handoff, readonly_argv_for(review_handoff))

    assert code == 0 and elapsed < 1.0
    assert out.count("\n") == 1  # 봉투 한 덩어리
    assert _snapshot(review_handoff) == before  # 읽기 전용 — cwd(인계 디렉터리)에 파일을 만들거나 고치지 않는다
    raw = _result_envelope(out.encode("utf-8"))
    assert raw is not None
    envelope = json.loads(raw)
    assert envelope["type"] == "result" and envelope["subtype"] == "success" and envelope["is_error"] is False
    assert envelope["session_id"] == "scripted-claude" and envelope["model"] == SCRIPT_MODEL_ID
    structured = envelope["structured_output"]
    assert set(structured) == {"outcome", "summary"}  # 스키마(additionalProperties: false)와 같은 두 키
    assert structured["outcome"] == REVIEW_SPEC.outcomes[0] == "approved"
    assert "diff.patch" in structured["summary"] and "code_change_result.json" in structured["summary"]
    assert str(review_handoff) not in structured["summary"]  # 파일 이름만
    assert envelope["result"] == structured["summary"]
    assert "scripted claude: approved" in err

    adapter = ClaudeAdapter(None)
    parsed = adapter.parse_generic_message(raw, REVIEW_SPEC.outcomes)
    assert parsed.outcome == "approved" and parsed.parse_note is None and parsed.summary == structured["summary"]
    run = ToolRun(pid=1, started_at="2026-09-21T00:00:00Z", exit_code=0, stdout=out.encode(), stderr=err.encode(),
                  timed_out=False, stopped=True, last_message=raw)
    assert adapter.classify_failure(run) is None


def test_generic_kind_follows_the_schema_enum_not_a_fixed_word(review_handoff, monkeypatch, capsys):
    code, out, _, _ = run_generic(monkeypatch, capsys, review_handoff, readonly_argv_for(review_handoff, ["looks_fine", "rework"]))

    assert code == 0
    assert json.loads(out)["structured_output"]["outcome"] == "looks_fine"


def test_generic_kind_runs_as_module_in_handoff_cwd_and_leaves_it_untouched(review_handoff):
    before = _snapshot(review_handoff)

    completed = subprocess.run(
        [sys.executable, "-m", "workflow.scripted.claude", *readonly_argv_for(review_handoff)[1:]],
        input=generic_prompt_for(review_handoff), capture_output=True, text=True,
        env={**os.environ, PACE_ENV: "0"}, cwd=review_handoff,
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["type"] == "result" and envelope["structured_output"]["outcome"] == "approved"
    assert _snapshot(review_handoff) == before
