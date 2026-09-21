"""scripted.codex — `codex exec --json -C <worktree> … --output-last-message <file> -` 인자 형식으로 대본을 재생한다.

argv 는 실제 `CodexAdapter.build_argv` 로 만들고 마지막 메시지는 `CodexAdapter.parse_last_message` 로 읽는다 —
어댑터는 바뀌지 않고 대본이 그 형식을 따른다. 실제 codex 는 호출하지 않는다.
"""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from workflow.connector.codex import CodexAdapter
from workflow.scripted import SCRIPT_MODEL_ID, _common
from workflow.scripted import codex as scripted_codex
from workflow.scripted._common import PACE_ENV

from .conftest import prompt_for


def argv_for(worktree: Path, tmp_path: Path) -> tuple[list[str], Path]:
    schema = tmp_path / "codex_result_schema.json"
    schema.write_text("{}")
    last_message = tmp_path / "last_message.json"
    return CodexAdapter(None).build_argv(worktree, schema, last_message), last_message


def run_main(monkeypatch, capsys, argv: list[str], prompt: str) -> tuple[int, list[dict], str, float]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(prompt))
    started = time.perf_counter()
    code = scripted_codex.main(argv)
    elapsed = time.perf_counter() - started
    out, err = capsys.readouterr()
    return code, [json.loads(line) for line in out.splitlines()], err, elapsed


def test_full_run_fixes_worktree_and_writes_jsonl_and_last_message(worktree, handoff, tmp_path, monkeypatch, capsys):
    argv, last_message = argv_for(worktree, tmp_path)
    before = (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8")

    code, lines, err, elapsed = run_main(monkeypatch, capsys, argv, prompt_for(handoff, worktree))

    assert code == 0 and elapsed < 1.0
    assert [line["type"] for line in lines] == ["thread.started", "item", "turn.completed"]
    assert lines[0]["thread_id"] == "scripted-codex" and lines[0]["model"] == SCRIPT_MODEL_ID
    assert (worktree / "tests" / "test_repro_records.py").is_file()
    assert (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8") != before
    parsed = CodexAdapter(None).parse_last_message(last_message.read_text(encoding="utf-8"))
    assert parsed.outcome == "ready_for_review" and parsed.parse_note is None
    assert "items 와 data.records" in parsed.summary
    last = json.loads(last_message.read_text(encoding="utf-8"))
    assert last["files_changed"] == ["tests/test_repro_records.py", "daily_report/transformer.py"]
    assert "scripted codex" in err


def test_missing_handoff_response_changes_nothing_and_is_needs_information(worktree, tmp_path, monkeypatch, capsys):
    argv, last_message = argv_for(worktree, tmp_path)
    empty = tmp_path / "empty.handoff"
    empty.mkdir()
    snapshot = {p: p.read_text(encoding="utf-8") for p in worktree.rglob("*.py")}

    code, lines, _, _ = run_main(monkeypatch, capsys, argv, prompt_for(empty, worktree))

    assert code == 0
    assert [line["type"] for line in lines] == ["thread.started", "item", "turn.completed"]
    assert {p: p.read_text(encoding="utf-8") for p in worktree.rglob("*.py")} == snapshot
    parsed = CodexAdapter(None).parse_last_message(last_message.read_text(encoding="utf-8"))
    assert parsed.outcome == "needs_information" and parsed.parse_note is None
    assert json.loads(last_message.read_text(encoding="utf-8"))["files_changed"] == []


@pytest.mark.parametrize("raw", ["0", "abc", "-3"])
def test_zero_or_bad_pace_never_sleeps(worktree, handoff, tmp_path, monkeypatch, capsys, raw):
    monkeypatch.setenv(PACE_ENV, raw)
    monkeypatch.setattr(_common.time, "sleep", lambda s: pytest.fail(f"sleep({s}) 호출"))
    argv, _ = argv_for(worktree, tmp_path)

    code, _, _, elapsed = run_main(monkeypatch, capsys, argv, prompt_for(handoff, worktree))

    assert code == 0 and elapsed < 1.0


def test_output_never_names_a_real_model(worktree, handoff, tmp_path, monkeypatch, capsys):
    argv, last_message = argv_for(worktree, tmp_path)

    _, lines, err, _ = run_main(monkeypatch, capsys, argv, prompt_for(handoff, worktree))

    text = json.dumps(lines) + last_message.read_text(encoding="utf-8") + err
    assert "gpt-" not in text and "claude-" not in text and SCRIPT_MODEL_ID in text


def test_runs_as_module_with_prompt_on_stdin_and_honours_dash_c(worktree, handoff, tmp_path):
    argv, last_message = argv_for(worktree, tmp_path)

    completed = subprocess.run(
        [sys.executable, "-m", "workflow.scripted.codex", *argv[1:]], input=prompt_for(handoff, worktree),
        capture_output=True, text=True, env={**os.environ, PACE_ENV: "0"}, cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [line["type"] for line in lines] == ["thread.started", "item", "turn.completed"]
    assert last_message.is_file() and (worktree / "tests" / "test_repro_records.py").is_file()


def test_without_output_option_exits_nonzero_without_reading_stdin(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "workflow.scripted.codex", "--help"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=tmp_path,
    )
    assert completed.returncode != 0
    assert "--output-last-message" in completed.stderr
