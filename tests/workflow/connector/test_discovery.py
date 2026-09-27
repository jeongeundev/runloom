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


# --- found.github_repository (phase 11 step 4) ---

TOKEN = "ghs_" + "t" * 36


def _set_remotes(repo, *remotes):
    _git(repo, "remote", "remove", "origin")
    for name, url in remotes:
        _git(repo, "remote", "add", name, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octo/report-demo",
        "https://github.com/octo/report-demo.git",
        "https://github.com/octo/report-demo/",
        "git@github.com:octo/report-demo.git",
        "git@github.com:octo/report-demo",
        "ssh://git@github.com/octo/report-demo",
        "ssh://git@github.com/octo/report-demo.git",
        "https://GitHub.COM/octo/report-demo.git",
    ],
)
def test_discover_reports_github_repository_from_remote_url(repo, url):
    _set_remotes(repo, ("origin", url))

    assert discover(repo)["found"]["github_repository"] == "octo/report-demo"


def test_discover_keeps_owner_name_case_as_written(repo):
    _set_remotes(repo, ("origin", "git@github.com:Octo/Report-Demo.git"))

    assert discover(repo)["found"]["github_repository"] == "Octo/Report-Demo"


def test_discover_strips_credentials_from_github_url(repo):
    _set_remotes(repo, ("origin", f"https://x-access-token:{TOKEN}@github.com/octo/report-demo.git"))

    result = discover(repo)
    text = json.dumps(result, ensure_ascii=False)

    assert result["found"]["github_repository"] == "octo/report-demo"
    assert TOKEN not in text and "x-access-token" not in text
    assert "github.com" not in text and "https://" not in text


def test_discover_prefers_origin_over_other_github_remotes(repo):
    _set_remotes(
        repo,
        ("upstream", "https://github.com/upstream/report-demo.git"),
        ("origin", "git@github.com:octo/report-demo.git"),
    )

    assert discover(repo)["found"]["github_repository"] == "octo/report-demo"


def test_discover_falls_back_to_first_github_remote_without_origin(repo):
    _set_remotes(
        repo,
        ("mirror", "https://gitlab.com/octo/report-demo.git"),
        ("fork", "https://github.com/fork/report-demo.git"),
        ("later", "https://github.com/later/report-demo.git"),
    )

    assert discover(repo)["found"]["github_repository"] == "fork/report-demo"


def test_discover_uses_other_github_remote_when_origin_is_not_github(repo):
    _set_remotes(
        repo,
        ("origin", "https://gitlab.com/octo/report-demo.git"),
        ("gh", "https://github.com/octo/report-demo.git"),
    )

    assert discover(repo)["found"]["github_repository"] == "octo/report-demo"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/octo/report-demo.git",
        "https://github.com.evil.example/octo/report-demo.git",
        "https://notgithub.com/octo/report-demo.git",
        "https://github.com/octo",
        "https://github.com/octo/report-demo/extra",
        "/Users/someone/repos/report-demo",
    ],
)
def test_discover_omits_github_repository_for_non_github_remote(repo, url):
    _set_remotes(repo, ("origin", url))

    assert "github_repository" not in discover(repo)["found"]


def test_discover_omits_github_repository_without_remotes(repo):
    _set_remotes(repo)

    found = discover(repo)["found"]

    assert "github_repository" not in found
    assert found["git"]["remotes"] == []


def test_discover_without_github_remote_leaks_no_url(repo):
    result = discover(repo)  # fixture origin = https://user:pass@example.com/demo.git

    assert "github_repository" not in result["found"]
