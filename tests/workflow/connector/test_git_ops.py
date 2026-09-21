"""git_ops — 업무별 worktree, 결과 커밋, diff. 원본 저장소의 작업 트리·기준 브랜치는 건드리지 않는다."""

import shutil
import subprocess

import pytest

from workflow.connector import git_ops
from workflow.connector.git_ops import GitError


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "demo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pkg.py").write_text("X = 1\n")
    (repo / "tests" / "test_pkg.py").write_text("def test_x():\n    assert True\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_head_sha_returns_full_commit(repo):
    sha = git_ops.head_sha(repo)

    assert sha == _git(repo, "rev-parse", "HEAD") and len(sha) == 40


def test_ensure_worktree_creates_branch_next_to_repo(repo):
    base = git_ops.head_sha(repo)

    path = git_ops.ensure_worktree(repo, "fix-daily-0920", base)

    assert path == repo.parent / "demo-worktrees" / "fix-daily-0920"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/fix-daily-0920"
    assert _git(path, "rev-parse", "HEAD") == base
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # 원본은 기준 브랜치 그대로


def test_ensure_worktree_is_reused_on_retry(repo):
    base = git_ops.head_sha(repo)
    first = git_ops.ensure_worktree(repo, "fix-daily-0920", base)
    (first / "pkg.py").write_text("X = 2\n")
    result = git_ops.commit_all(first, "fix: 1차")

    second = git_ops.ensure_worktree(repo, "fix-daily-0920", result)

    assert second == first
    assert _git(second, "rev-parse", "--abbrev-ref", "HEAD") == "task/fix-daily-0920"
    assert _git(second, "rev-parse", "HEAD") == result


def test_ensure_worktree_rejects_unknown_base_commit(repo):
    with pytest.raises(GitError):
        git_ops.ensure_worktree(repo, "fix-x", "0" * 40)
    assert not (repo.parent / "demo-worktrees" / "fix-x").exists()


def test_ensure_worktree_refuses_path_with_other_branch(repo):
    base = git_ops.head_sha(repo)
    path = repo.parent / "demo-worktrees" / "fix-other"
    path.parent.mkdir(parents=True)
    _git(repo, "worktree", "add", "-q", "-b", "someone-else", str(path), base)

    with pytest.raises(GitError):
        git_ops.ensure_worktree(repo, "fix-other", base)


def test_task_id_is_not_interpreted_as_path(repo):
    base = git_ops.head_sha(repo)

    path = git_ops.ensure_worktree(repo, "../escape", base)

    assert path.parent == repo.parent / "demo-worktrees"
    assert ".." not in path.name


def test_is_dirty_sees_untracked_and_modified(repo):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "fix-a", base)

    assert git_ops.is_dirty(path) is False
    (path / "tests" / "test_new.py").write_text("def test_n():\n    assert True\n")
    assert git_ops.is_dirty(path) is True


def test_commit_all_commits_everything_and_leaves_origin_untouched(repo):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "fix-a", base)
    (path / "pkg.py").write_text("X = 2\n")
    (path / "tests" / "test_new.py").write_text("def test_n():\n    assert True\n")

    sha = git_ops.commit_all(path, "fix(fix-a): 수정")

    assert sha == _git(path, "rev-parse", "HEAD") and sha != base
    assert git_ops.is_dirty(path) is False
    assert _git(path, "show", "-s", "--format=%an <%ae>", sha) == "workflow-connector <connector@localhost>"
    assert _git(repo, "rev-parse", "HEAD") == base
    assert (repo / "pkg.py").read_text() == "X = 1\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_commit_all_without_changes_raises(repo):
    path = git_ops.ensure_worktree(repo, "fix-a", git_ops.head_sha(repo))

    with pytest.raises(GitError, match="nothing to commit"):
        git_ops.commit_all(path, "empty")


def test_diff_text_and_changed_test_files(repo):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "fix-a", base)
    (path / "pkg.py").write_text("X = 2\n")
    (path / "tests" / "test_repro.py").write_text("def test_r():\n    assert True\n")
    (path / "tests" / "test_pkg.py").write_text("def test_x():\n    assert 1\n")
    (path / "test_top.py").write_text("def test_t():\n    assert True\n")
    (path / "notes.txt").write_text("x\n")
    head = git_ops.commit_all(path, "fix")

    diff = git_ops.diff_text(repo, base, head)
    tests = git_ops.changed_test_files(repo, base, head)

    assert "pkg.py" in diff and "-X = 1" in diff and "+X = 2" in diff
    assert tests == ["test_top.py", "tests/test_pkg.py", "tests/test_repro.py"]


def test_export_checkout_gives_clean_copy_at_commit_and_remove(repo, tmp_path):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "fix-a", base)
    (path / "pkg.py").write_text("X = 2\n")
    head = git_ops.commit_all(path, "fix")
    dest = tmp_path / "export" / "at-base"

    git_ops.export_checkout(repo, base, dest)
    try:
        assert (dest / "pkg.py").read_text() == "X = 1\n"
        assert _git(dest, "rev-parse", "HEAD") == base
    finally:
        git_ops.remove_worktree(repo, dest)

    assert not dest.exists()
    assert _git(path, "rev-parse", "HEAD") == head  # 업무 worktree 는 남는다
    assert "at-base" not in _git(repo, "worktree", "list")


def test_prune_worktrees_drops_entries_whose_directory_is_gone(repo):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "fix-a", base)
    shutil.rmtree(path)  # 디렉터리만 사라지고 관리 항목은 남은 상태
    assert "fix-a" in _git(repo, "worktree", "list")

    git_ops.prune_worktrees(repo)

    assert "fix-a" not in _git(repo, "worktree", "list")
    assert _git(repo, "rev-parse", "task/fix-a") == base  # 브랜치는 남는다


def test_prune_worktrees_is_quiet_when_nothing_is_stale(repo):
    path = git_ops.ensure_worktree(repo, "fix-a", git_ops.head_sha(repo))

    git_ops.prune_worktrees(repo)

    assert path.exists() and "fix-a" in _git(repo, "worktree", "list")
