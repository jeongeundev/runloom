"""scaffold_demo_repo.py — B 가 수정할 데모 저장소를 이 트리 밖에 생성하는 스크립트.

생성된 저장소는 수정 전 기준(`items` 만 지원)이어야 하고, PRD 의 `response-before` 로는 보고서를,
`response-after` 로는 `MISSING_RECORDS_FIELD` 를 내야 한다. 보고서 형식은 Step 9 fixture `report-0918` 과 같다.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import scaffold_demo_repo as sdr

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "diagnostic_demo" / "fixtures" / "evidence"

RESPONSE_BEFORE = json.loads((FIXTURES / "response-before" / "1.json").read_text())
RESPONSE_AFTER = json.loads((FIXTURES / "response-after" / "1.json").read_text())
REPORT_0918 = (FIXTURES / "report-0918" / "1.txt").read_text()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def run_report(repo: Path, response: dict | None, tmp_path: Path, name: str = "response.json"):
    path = tmp_path / name
    if response is not None:
        path.write_text(json.dumps(response, ensure_ascii=False))
    return subprocess.run(
        [sys.executable, "-m", "daily_report", str(path)], cwd=repo, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("demo") / "demo-report-repo"
    sha = sdr.scaffold(path)
    assert len(sha) == 40
    return path


# --- git ------------------------------------------------------------------------------


def test_scaffold_creates_git_repo_with_one_commit_and_tag(repo):
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(repo, "rev-list", "--count", "HEAD") == "1"
    assert git(repo, "tag") == "report-base"
    assert git(repo, "rev-parse", "report-base") == git(repo, "rev-parse", "HEAD")
    assert git(repo, "status", "--porcelain") == ""
    assert "report-base" in git(repo, "log", "-1", "--format=%s")


def test_scaffold_returns_head_sha(tmp_path):
    path = tmp_path / "r"
    sha = sdr.scaffold(path)
    assert sha == git(path, "rev-parse", "HEAD")


def test_scaffold_refuses_existing_path_unless_forced(tmp_path):
    path = tmp_path / "r"
    sdr.scaffold(path)
    (path / "marker").write_text("x")

    with pytest.raises(FileExistsError):
        sdr.scaffold(path)
    assert (path / "marker").exists()

    second = sdr.scaffold(path, force=True)
    assert not (path / "marker").exists()
    assert git(path, "rev-list", "--count", "HEAD") == "1"
    assert second == git(path, "rev-parse", "HEAD")


# --- 구조 -----------------------------------------------------------------------------


def test_layout_matches_step_spec(repo):
    expected = {
        "README.md", "AGENTS.md", "docs/contract.md", "pyproject.toml", ".gitignore",
        "daily_report/__init__.py", "daily_report/transformer.py", "daily_report/report.py",
        "daily_report/__main__.py", "tests/__init__.py", "tests/test_transformer.py", "tests/test_report.py",
    }
    assert set(git(repo, "ls-files").splitlines()) == expected


def test_readme_and_agents_say_it_is_fictional(repo):
    for name in ("README.md", "AGENTS.md"):
        text = (repo / name).read_text()
        assert "가상" in text and "실제 서비스가 아니다" in text
    agents = (repo / "AGENTS.md").read_text()
    assert "python3 -m pytest -q" in agents and "docs/contract.md" in agents and "커밋" in agents


def test_contract_doc_states_base_code_does_not_implement_it(repo):
    text = (repo / "docs" / "contract.md").read_text()
    assert "data.records" in text and "items" in text
    assert "수정 전 코드는 이 계약을 아직 구현하지 않았다" in text


def test_base_transformer_only_reads_items(repo):
    source = (repo / "daily_report" / "transformer.py").read_text()
    assert "records" not in source  # data.records 지원은 B 가 추가한다
    tests = (repo / "tests" / "test_transformer.py").read_text()
    assert "records" not in tests


def test_no_real_company_names_or_urls(repo):
    for path in git(repo, "ls-files").splitlines():
        text = (repo / path).read_text()
        assert "http://" not in text and "https://" not in text, path


# --- 동작 -----------------------------------------------------------------------------


def test_generated_repo_pytest_passes(repo):
    result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_response_before_renders_prd_report_exactly(repo, tmp_path):
    result = run_report(repo, RESPONSE_BEFORE, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == REPORT_0918
    assert "합계    20    5" in result.stdout and "2026-09-18" in result.stdout


def test_response_after_fails_with_missing_records_field(repo, tmp_path):
    result = run_report(repo, RESPONSE_AFTER, tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("ERROR stage=transform component=report_transformer code=MISSING_RECORDS_FIELD")
    assert "expected_path=$.items" in lines[0]


def test_empty_items_renders_zero_totals_without_rows(repo, tmp_path):
    result = run_report(repo, {"report_date": "2026-09-18", "items": []}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "일일 업무 보고서 — 2026-09-18\n\n팀      완료  미완료\n합계    0     0\n"


def test_missing_report_date_fails(repo, tmp_path):
    result = run_report(repo, {"items": []}, tmp_path)
    assert result.returncode == 1
    assert "code=MISSING_REPORT_DATE" in result.stderr


def test_invalid_row_fails(repo, tmp_path):
    result = run_report(repo, {"report_date": "2026-09-18", "items": [{"team": "운영", "completed": -1}]}, tmp_path)
    assert result.returncode == 1
    assert "code=INVALID_ROW" in result.stderr


def test_missing_file_and_bad_json_exit_2(repo, tmp_path):
    missing = run_report(repo, None, tmp_path, "missing.json")
    assert missing.returncode == 2
    (tmp_path / "bad.json").write_text("{")
    bad = run_report(repo, None, tmp_path, "bad.json")
    assert bad.returncode == 2


def test_changed_count_changes_totals(repo, tmp_path):
    response = json.loads(json.dumps(RESPONSE_BEFORE))
    response["items"][0]["completed"] = 13
    result = run_report(repo, response, tmp_path)
    assert result.returncode == 0
    assert "운영    13    3" in result.stdout and "합계    21    5" in result.stdout


# --- main -----------------------------------------------------------------------------


def test_main_prints_base_commit_and_respects_force(tmp_path, capsys):
    path = tmp_path / "out"
    assert sdr.main([str(path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == f"base_commit={git(path, 'rev-parse', 'HEAD')}"

    assert sdr.main([str(path)]) != 0
    assert sdr.main([str(path), "--force"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == f"base_commit={git(path, 'rev-parse', 'HEAD')}"


def test_default_path_is_sibling_of_this_repo():
    assert sdr.default_path() == ROOT.parent / "demo-report-repo"
