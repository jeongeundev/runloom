"""discovery — 등록 폴더의 설정 존재 여부·요약만 읽는다. 파일 전체·.env·인증 파일 내용은 절대 포함하지 않는다."""

import json
import subprocess

import pytest

from workflow.connector.discovery import discover

SECRET = "sk-" + "e" * 40


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# 데모 저장소\n\n" + "x" * 500, encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "daily-report-demo"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    (repo / "tests").mkdir()
    (repo / ".codex").mkdir()
    (repo / ".codex" / "auth.json").write_text(json.dumps({"token": SECRET}))
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text(json.dumps({"apiKey": SECRET}))
    (repo / ".env").write_text(f"OPENAI_API_KEY={SECRET}\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", "https://user:pass@example.com/demo.git")
    return repo


def test_discover_finds_agents_md_summary_and_config_presence(repo):
    found = discover(repo)["found"]

    assert found["AGENTS.md"].startswith("# 데모 저장소")
    assert len(found["AGENTS.md"]) <= 200
    assert found["codex_config"] is True
    assert found["claude_config"] is True
    assert found["pyproject"] == {"name": "daily-report-demo", "pytest_configured": True}
    assert found["tests_dir"] is True
    assert found["git"]["remotes"] == ["origin"]
    assert len(found["git"]["head"]) == 40


def test_discover_never_includes_secrets_or_remote_urls(repo):
    text = json.dumps(discover(repo), ensure_ascii=False)

    assert SECRET not in text
    assert "example.com" not in text and "pass" not in text
    assert "x" * 201 not in text  # 파일 전체가 아니라 첫 200자


def test_discover_lists_what_was_not_read(repo):
    result = discover(repo)

    assert "CLAUDE.md" in result["not_read"]
    assert result["verification_level"] == "설정 발견"


def test_discover_on_plain_folder(tmp_path):
    result = discover(tmp_path)

    assert result["found"] == {"codex_config": False, "claude_config": False, "tests_dir": False}
    assert {"AGENTS.md", "CLAUDE.md", "pyproject.toml", "git"} <= set(result["not_read"])


def test_discover_claude_config_from_claude_md_alone(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# 지침\n", encoding="utf-8")

    found = discover(tmp_path)["found"]

    assert found["claude_config"] is True and found["codex_config"] is False
    assert found["CLAUDE.md"] == "# 지침\n"
