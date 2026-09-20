"""등록 폴더의 설정 존재 여부와 요약 — 능력 설명 제안의 재료 (ARCHITECTURE "등록·선택·권한").

읽기 전용이며 존재 여부와 짧은 요약만 남긴다. 파일 전체, `.env`, 인증 파일(`.codex/auth.json`, `~/.claude/…` 등),
원격 URL(자격 증명이 섞일 수 있다)은 절대 포함하지 않는다. "설정 발견" 이지 "실제 사용 확인" 이 아니다.
"""

import subprocess
import tomllib
from pathlib import Path

SUMMARY_CHARS = 200
VERIFICATION_LEVEL = "설정 발견"


def _head(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:SUMMARY_CHARS]


def _git(repo_path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _git_summary(repo_path: Path) -> dict | None:
    head = _git(repo_path, "rev-parse", "HEAD")
    if head is None:
        return None
    remotes = _git(repo_path, "remote") or ""  # 이름만. URL 은 `git remote -v` 라 읽지 않는다
    return {"remotes": remotes.split(), "head": head.strip()}


def discover(repo_path: Path) -> dict:
    found: dict = {}
    not_read: list[str] = []

    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo_path / name
        if path.is_file():
            found[name] = _head(path)
        else:
            not_read.append(name)

    # 존재 여부만. 디렉터리 안의 파일(`.codex/auth.json`, `.claude/settings*.json` 등)은 열지 않는다
    found["codex_config"] = (repo_path / ".codex").is_dir() or (repo_path / "codex.toml").is_file()
    found["claude_config"] = (repo_path / ".claude").is_dir() or (repo_path / "CLAUDE.md").is_file()

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            found["pyproject"] = {
                "name": data.get("project", {}).get("name"),
                "pytest_configured": "pytest" in data.get("tool", {}),
            }
        except tomllib.TOMLDecodeError:
            not_read.append("pyproject.toml")
    else:
        not_read.append("pyproject.toml")

    found["tests_dir"] = (repo_path / "tests").is_dir()

    git = _git_summary(repo_path)
    if git is None:
        not_read.append("git")
    else:
        found["git"] = git

    return {"found": found, "not_read": not_read, "verification_level": VERIFICATION_LEVEL}
