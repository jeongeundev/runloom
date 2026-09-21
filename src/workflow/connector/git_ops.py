"""Git worktree·커밋·diff — 연결 프로그램이 결과 보존을 관리한다 (ARCHITECTURE "Codex와 worktree").

- 업무별 worktree 는 저장소 옆 `<repo>-worktrees/<task_id>/`, 브랜치 `task/<task_id>`. 재시도는 같은 worktree.
- 원본 저장소의 작업 트리·기준 브랜치는 건드리지 않는다. 결과는 작업 브랜치의 로컬 커밋으로만 남긴다.
- 모든 명령은 고정 인자 배열이다. task_id·커밋 ID 는 불투명 문자열이며 경로로 해석하지 않는다.
"""

import re
import subprocess
from pathlib import Path

DEFAULT_AUTHOR = "workflow-connector <connector@localhost>"
_TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class GitError(Exception):
    pass


def _git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git {' '.join(args)}: {exc.stderr.strip() or exc.stdout.strip()}") from exc
    except OSError as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    return result.stdout


def _safe(name: str) -> str:
    # 파일 이름·브랜치 이름으로만 쓴다. `..` 와 앞뒤 `.` 은 git ref 규칙에 걸리므로 함께 치운다
    name = re.sub(r"\.{2,}", "_", _SAFE_NAME.sub("_", name)).strip(".")
    return name or "_"


def head_sha(repo: Path) -> str:
    return _git(["rev-parse", "HEAD"], repo).strip()


def worktree_path(repo: Path, task_id: str) -> Path:
    return repo.parent / f"{repo.name}-worktrees" / _safe(task_id)


def ensure_worktree(repo: Path, task_id: str, base_commit: str) -> Path:
    """없으면 `task/<task_id>` 브랜치로 base_commit 에서 만든다. 있으면 그 브랜치인지 확인하고 그대로 쓴다."""
    path = worktree_path(repo, task_id)
    branch = f"task/{_safe(task_id)}"
    if path.exists():
        current = _git(["rev-parse", "--abbrev-ref", "HEAD"], path).strip()
        if current != branch:
            raise GitError(f"{path} 는 브랜치 {current} 의 worktree 다 (기대: {branch})")
        return path
    try:
        _git(["cat-file", "-e", f"{base_commit}^{{commit}}"], repo)
    except GitError as exc:
        raise GitError(f"base_commit {base_commit} 이 저장소에 없다") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, capture_output=True,
    ).returncode == 0
    if branch_exists:  # worktree 디렉터리만 지워진 경우 — 브랜치를 이어 쓴다
        _git(["worktree", "add", str(path), branch], repo)
    else:
        _git(["worktree", "add", "-b", branch, str(path), base_commit], repo)
    return path


def is_dirty(worktree: Path) -> bool:
    return _git(["status", "--porcelain", "--untracked-files=all"], worktree).strip() != ""


def commit_all(worktree: Path, message: str, author: str = DEFAULT_AUTHOR) -> str:
    """추적·미추적 변경을 모두 커밋하고 커밋 ID 를 돌려준다. 변경이 없으면 GitError."""
    _git(["add", "-A"], worktree)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree, capture_output=True)
    if staged.returncode == 0:
        raise GitError("nothing to commit")
    name, _, email = author.rpartition(" <")
    _git([
        "-c", f"user.name={name}", "-c", f"user.email={email.rstrip('>')}",
        "commit", "-q", "--author", author, "-m", message,
    ], worktree)
    return head_sha(worktree)


def diff_text(repo: Path, base: str, head: str) -> str:
    return _git(["diff", base, head], repo)


def changed_test_files(repo: Path, base: str, head: str) -> list[str]:
    """base→head 에서 추가·수정된 `tests/` 아래 파일과 `test_*.py`."""
    names = _git(["diff", "--name-only", "--diff-filter=AM", base, head], repo).splitlines()
    return sorted(n for n in names if n.startswith("tests/") or _TEST_FILE.search(n))


def export_checkout(repo: Path, commit: str, dest: Path) -> None:
    """검증용 깨끗한 체크아웃. 호출자가 `remove_worktree` 로 정리한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "--detach", str(dest), commit], repo)


def remove_worktree(repo: Path, path: Path) -> None:
    _git(["worktree", "remove", "--force", str(path)], repo)


def prune_worktrees(repo: Path) -> None:
    """디렉터리가 사라진 worktree 의 관리 항목을 지운다 (`git worktree prune`). 브랜치는 건드리지 않는다."""
    _git(["worktree", "prune"], repo)
