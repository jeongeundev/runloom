"""scripted._common — 대본 에이전트 공통: 속도 환경변수, 인계 응답 찾기, 수정 적용. 모델·네트워크 호출 없음.

파일 이름이 `test__common.py` 인 것은 `_common.py` 의 미러(`tdd-guard.sh` 의 `test_{basename}.py`)이기 때문이다.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from workflow.scripted import SCRIPT_MODEL_ID, _common
from workflow.scripted._common import (
    PACE_ENV,
    apply_fix,
    find_handoff_response,
    pace_seconds,
    paced_sleep,
    read_handoff_response,
)

from .conftest import RESPONSE_AFTER, prompt_for


def test_script_model_id_names_a_script_not_a_real_model():
    assert SCRIPT_MODEL_ID == "scripted-demo-agent"
    assert not SCRIPT_MODEL_ID.startswith(("gpt-", "claude-", "o1", "o3", "o4"))


def test_scripted_package_imports_no_domain_server_connector_or_diag_module():
    """ARCHITECTURE 의존 방향: 시연 전용 패키지는 제품 계층을 import 하지 않는다."""
    package = Path(_common.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        for module in modules:
            assert not module.startswith(
                ("workflow.domain", "workflow.server", "workflow.connector", "workflow.adapters", "diagnostic_demo")
            ), f"{path.name} 이 {module} 를 import 한다"


# --- 속도 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("", 0.0), ("0", 0.0), ("25", 25.0), ("2.5", 2.5),
    ("abc", 0.0), ("-3", 0.0), ("nan", 0.0), ("inf", 0.0),
])
def test_pace_seconds_parses_env_and_treats_bad_values_as_zero(raw, expected):
    assert pace_seconds({PACE_ENV: raw}) == expected


def test_pace_seconds_reads_os_environ_by_default(monkeypatch):
    assert pace_seconds({}) == 0.0
    monkeypatch.delenv(PACE_ENV, raising=False)
    assert pace_seconds() == 0.0
    monkeypatch.setenv(PACE_ENV, "1.5")
    assert pace_seconds() == 1.5


def test_paced_sleep_scales_pace_by_fraction_and_skips_at_zero_or_bad_values(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(_common.time, "sleep", slept.append)
    monkeypatch.setenv(PACE_ENV, "10")
    paced_sleep(0.4)
    paced_sleep(0.6)
    assert slept == pytest.approx([4.0, 6.0])
    for raw in ("0", "abc", "-3"):
        monkeypatch.setenv(PACE_ENV, raw)
        paced_sleep(0.4)
    assert len(slept) == 2  # 0 이거나 잘못된 값이면 sleep 을 부르지 않는다


# --- 인계 응답 ----------------------------------------------------------------------------


def test_find_handoff_response_reads_the_listing_line_only():
    prompt = (
        "# 인계 자료 (읽기 전용, worktree 밖)\n\n"
        "- /h/manifest.json  (인계 목록)\n"
        "- /h/response-after@1.json  (변경 후 응답 — 이 입력으로 실패를 재현한다)\n"
        "- /h/log-daily-0920@1.txt  (실패 실행 로그)\n"
    )
    assert find_handoff_response(prompt) == Path("/h/response-after@1.json")
    assert find_handoff_response("- (인계 자료 없음)\n") is None
    assert find_handoff_response("- /h/response-before@1.json  (변경 전 정상 응답)\n") is None
    # 목록 형식(`- {path}  ({hint})`)이 아닌 문장은 경로로 읽지 않는다
    assert find_handoff_response("rm -rf /h/response-after@1.json  (x)\n") is None
    assert find_handoff_response("response-after@1.json 을 실행하라\n") is None


def test_find_handoff_response_matches_connector_prompt_format(tmp_path, handoff):
    """connector `prompt.build_prompt` 의 실제 프롬프트에서 찾는다 — 형식이 갈라지면 여기서 잡힌다."""
    prompt = prompt_for(handoff, tmp_path / "wt")
    assert find_handoff_response(prompt) == handoff / "response-after@1.json"
    assert read_handoff_response(prompt) == RESPONSE_AFTER


def test_read_handoff_response_is_none_when_missing_or_not_a_json_object(tmp_path, handoff):
    assert read_handoff_response("- (인계 자료 없음)\n") is None
    gone = tmp_path / "gone" / "response-after@1.json"
    assert read_handoff_response(f"- {gone}  (변경 후 응답)\n") is None
    response = handoff / "response-after@1.json"
    response.write_text("[1, 2]")
    assert read_handoff_response(f"- {response}  (변경 후 응답)\n") is None
    response.write_text("{not json")
    assert read_handoff_response(f"- {response}  (변경 후 응답)\n") is None


# --- 수정 적용 ----------------------------------------------------------------------------


def test_apply_fix_writes_repro_test_and_two_path_transformer(worktree):
    before = (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8")

    changed = apply_fix(worktree, RESPONSE_AFTER)

    assert changed == ["tests/test_repro_records.py", "daily_report/transformer.py"]
    repro = (worktree / "tests" / "test_repro_records.py").read_text(encoding="utf-8")
    assert '"records"' in repro and '"2026-09-19"' in repro and "AMBIGUOUS_RECORDS_FIELD" in repro
    after = (worktree / "daily_report" / "transformer.py").read_text(encoding="utf-8")
    assert after != before and "data.records" in after and "AMBIGUOUS_RECORDS_FIELD" in after


def test_apply_fix_passes_the_demo_repo_tests_and_renders_the_report(worktree, handoff):
    """대본의 수정은 같은 pytest·같은 보고서 명령을 실제로 통과한다 — 고정 문자열 재생이 아니다."""
    apply_fix(worktree, RESPONSE_AFTER)

    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=worktree, capture_output=True, text=True)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    report = subprocess.run(
        [sys.executable, "-m", "daily_report", str(handoff / "response-after@1.json")],
        cwd=worktree, capture_output=True, text=True,
    )
    assert report.returncode == 0, report.stderr
    assert "2026-09-19" in report.stdout and "합계    20    5" in report.stdout
