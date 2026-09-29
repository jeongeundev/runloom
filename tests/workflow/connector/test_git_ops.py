"""git_ops — 업무별 worktree, 결과 커밋, diff. 원본 저장소의 작업 트리·기준 브랜치는 건드리지 않는다."""

import os
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


def test_has_commit_is_true_only_for_commits_in_the_repo(repo):
    head = git_ops.head_sha(repo)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    assert git_ops.has_commit(repo, head) is True
    assert git_ops.has_commit(repo, "0" * 40) is False
    assert git_ops.has_commit(repo, tree) is False  # 커밋이 아닌 객체


def test_is_ancestor_follows_history_direction(repo):
    base = git_ops.head_sha(repo)
    path = git_ops.ensure_worktree(repo, "task-a", base)
    (path / "pkg.py").write_text("X = 2\n")
    result = git_ops.commit_all(path, "fix")

    assert git_ops.is_ancestor(repo, base, result) is True
    assert git_ops.is_ancestor(repo, result, base) is False


# --- 기준 커밋 추적 (phase 12 step 3, ADR-0018 결정 2) ------------------------------------


@pytest.fixture
def origin_clone(repo, tmp_path):
    """(bare origin, 그 클론). 클론은 `refs/remotes/origin/HEAD` 를 가진다."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(bare), str(clone))
    return bare, clone


def _push_new_commit(bare, tmp_path) -> str:
    """다른 곳에서 개발해 origin 기본 브랜치에 push 한 상황."""
    other = tmp_path / "elsewhere"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "pkg.py").write_text("X = 3\n")
    _git(other, "commit", "-q", "-am", "upstream")
    _git(other, "push", "-q", "origin", "main")
    return _git(other, "rev-parse", "HEAD")


def test_fetch_origin_then_origin_head_returns_the_new_default_branch_commit(origin_clone, tmp_path):
    bare, clone = origin_clone
    local_head = git_ops.head_sha(clone)
    pushed = _push_new_commit(bare, tmp_path)
    assert git_ops.origin_head(clone) == local_head  # fetch 전에는 옛 값

    git_ops.fetch_origin(clone)

    assert git_ops.origin_head(clone) == pushed
    assert git_ops.head_sha(clone) == local_head  # 작업 트리·로컬 브랜치는 그대로
    assert git_ops.has_commit(clone, pushed)
    path = git_ops.ensure_worktree(clone, "task-up", pushed)  # fetch 로 받은 커밋에서 worktree 를 만든다
    assert _git(path, "rev-parse", "HEAD") == pushed


def test_origin_head_sets_missing_symbolic_ref_from_the_remote(repo, origin_clone, tmp_path):
    bare, _ = origin_clone
    _git(repo, "remote", "add", "origin", str(bare))
    git_ops.fetch_origin(repo)
    _git(repo, "remote", "set-head", "origin", "--delete")  # 원격을 나중에 붙인 저장소(git 버전에 따라)는 origin/HEAD 가 없다
    assert subprocess.run(["git", "symbolic-ref", "-q", "refs/remotes/origin/HEAD"], cwd=repo).returncode != 0

    assert git_ops.origin_head(repo) == _git(bare, "rev-parse", "main")


def test_origin_head_without_origin_is_none(repo):
    assert git_ops.origin_head(repo) is None


def test_fetch_origin_failures_raise_git_error(repo, tmp_path):
    with pytest.raises(GitError):
        git_ops.fetch_origin(repo)  # origin 없음
    with pytest.raises(GitError):
        git_ops.fetch_origin(tmp_path / "없는 폴더")
    _git(repo, "remote", "add", "origin", str(tmp_path / "없는-origin.git"))
    with pytest.raises(GitError):
        git_ops.fetch_origin(repo)


# --- 작업 복사본 준비물: 원본 폴더 링크 (ADR-0018 결정 3, phase 12 step 4) ----------------------------------------


@pytest.fixture
def prepared_repo(repo):
    """원본 폴더에만 있는 설치물 — `.gitignore` 의 `node_modules/`(끝 `/`)는 심볼릭 링크에 맞지 않는다."""
    (repo / ".gitignore").write_text("node_modules/\n.venv/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    (repo / "backend" / ".venv" / "bin").mkdir(parents=True)
    (repo / "backend" / ".venv" / "bin" / "x").write_text("venv\n")
    (repo / "frontend" / "node_modules").mkdir(parents=True)
    (repo / "frontend" / "node_modules" / "y").write_text("module\n")
    return repo


def test_link_prepared_paths_links_origin_folders_and_keeps_them_out_of_git(prepared_repo):
    worktree = git_ops.ensure_worktree(prepared_repo, "t1", git_ops.head_sha(prepared_repo))

    linked = git_ops.link_prepared_paths(prepared_repo, worktree, ["backend/.venv", "frontend/node_modules"])

    assert linked == ["backend/.venv", "frontend/node_modules"]
    for rel in linked:
        assert (worktree / rel).is_symlink()
        assert (worktree / rel).resolve() == (prepared_repo / rel).resolve()
    assert (worktree / "backend/.venv/bin/x").read_text() == "venv\n"
    assert git_ops.is_dirty(worktree) is False
    (worktree / "pkg.py").write_text("X = 2\n")
    commit = git_ops.commit_all(worktree, "fix")
    assert _git(worktree, "show", "--name-only", "--format=", commit).splitlines() == ["pkg.py"]
    assert (prepared_repo / "frontend/node_modules/y").read_text() == "module\n"  # 원본은 그대로


def test_link_prepared_paths_writes_exclude_rule_once(prepared_repo):
    base = git_ops.head_sha(prepared_repo)
    first = git_ops.ensure_worktree(prepared_repo, "t1", base)
    second = git_ops.ensure_worktree(prepared_repo, "t2", base)

    git_ops.link_prepared_paths(prepared_repo, first, ["frontend/node_modules"])
    git_ops.link_prepared_paths(prepared_repo, second, ["frontend/node_modules"])
    again = git_ops.link_prepared_paths(prepared_repo, first, ["frontend/node_modules"])  # 이어 쓰기 — 이미 있다

    exclude = (prepared_repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count("/frontend/node_modules") == 1
    assert again == []
    assert git_ops.is_dirty(second) is False


def test_link_prepared_paths_skips_missing_origin_and_tracked_paths(prepared_repo, caplog):
    worktree = git_ops.ensure_worktree(prepared_repo, "t1", git_ops.head_sha(prepared_repo))

    with caplog.at_level("INFO", logger="workflow.connector.git_ops"):
        linked = git_ops.link_prepared_paths(prepared_repo, worktree, ["pkg.py", "tests", "absent/dir"])

    assert linked == []
    assert not (worktree / "pkg.py").is_symlink() and (worktree / "pkg.py").read_text() == "X = 1\n"
    assert not (worktree / "tests").is_symlink()
    assert not os.path.lexists(worktree / "absent")
    assert git_ops.is_dirty(worktree) is False
    text = caplog.text
    assert "pkg.py" in text and "tests" in text and "absent/dir" in text


def test_copy_prepared_paths_copies_real_directories_and_keeps_them_out_of_git(prepared_repo):
    """Next(Turbopack)는 작업 복사본 밖을 가리키는 `node_modules` 링크를 거부한다 — 그런 설치물은 복사한다."""
    worktree = git_ops.ensure_worktree(prepared_repo, "t1", git_ops.head_sha(prepared_repo))

    copied = git_ops.copy_prepared_paths(prepared_repo, worktree, ["frontend/node_modules", "absent/dir"])

    assert copied == ["frontend/node_modules"]
    dest = worktree / "frontend/node_modules"
    assert dest.is_dir() and not dest.is_symlink()
    assert (dest / "y").read_text() == "module\n"
    assert not os.path.lexists(worktree / "absent")
    assert git_ops.is_dirty(worktree) is False
    (dest / "y").write_text("changed\n")  # 복사본을 고쳐도 원본은 그대로
    assert (prepared_repo / "frontend/node_modules/y").read_text() == "module\n"
    assert git_ops.copy_prepared_paths(prepared_repo, worktree, ["frontend/node_modules"]) == []  # 이어 쓰기


def test_copy_prepared_paths_does_not_overwrite_tracked_paths(prepared_repo):
    worktree = git_ops.ensure_worktree(prepared_repo, "t1", git_ops.head_sha(prepared_repo))

    assert git_ops.copy_prepared_paths(prepared_repo, worktree, ["pkg.py"]) == []
    assert (worktree / "pkg.py").read_text() == "X = 1\n"
    assert git_ops.is_dirty(worktree) is False


# --- 결과 브랜치 push (ADR-0018 결정 4, phase 12 step 5) ------------------------------------------------


def _fix_commit(clone, task_id: str, text: str) -> str:
    worktree = git_ops.ensure_worktree(clone, task_id, git_ops.head_sha(clone))
    (worktree / "pkg.py").write_text(text)
    return git_ops.commit_all(worktree, f"fix({task_id})")


def test_push_task_branch_creates_then_fast_forwards_remote_branch(origin_clone):
    bare, clone = origin_clone
    main_before = _git(bare, "rev-parse", "main")
    first = _fix_commit(clone, "task-abc", "X = 2\n")

    assert git_ops.push_task_branch(clone, "task-abc") is True
    assert _git(bare, "rev-parse", "refs/heads/task/task-abc") == first

    second = _fix_commit(clone, "task-abc", "X = 3\n")  # 재작업 — 같은 브랜치를 앞으로
    assert git_ops.push_task_branch(clone, "task-abc") is True
    assert _git(bare, "rev-parse", "refs/heads/task/task-abc") == second
    assert _git(bare, "rev-parse", "main") == main_before  # 기본 브랜치는 그대로


def test_push_task_branch_uses_fixed_args_without_force(origin_clone, monkeypatch):
    _, clone = origin_clone
    _fix_commit(clone, "task/../x y", "X = 2\n")
    calls: list[list[str]] = []
    original = subprocess.run

    def recording(args, *a, **kw):
        calls.append(list(args))
        return original(args, *a, **kw)

    monkeypatch.setattr(git_ops.subprocess, "run", recording)

    assert git_ops.push_task_branch(clone, "task/../x y") is True

    push = [c for c in calls if c[:2] == ["git", "push"]]
    branch = f"refs/heads/task/{git_ops._safe('task/../x y')}"  # ensure_worktree 와 같은 이름
    assert push == [["git", "push", "--quiet", "origin", f"{branch}:{branch}"]]
    assert not any(arg in ("-f", "--force", "--force-with-lease") or arg.startswith("+") for arg in push[0])


def test_push_task_branch_rejected_when_remote_diverged(origin_clone):
    bare, clone = origin_clone
    _fix_commit(clone, "task-div", "X = 2\n")
    assert git_ops.push_task_branch(clone, "task-div") is True
    other = git_ops.worktree_path(clone, "task-div")
    _git(other, "reset", "-q", "--hard", "HEAD^")
    (other / "pkg.py").write_text("X = 9\n")
    git_ops.commit_all(other, "갈라진 커밋")

    assert git_ops.push_task_branch(clone, "task-div") is False  # force 없음 — 원격은 그대로


def test_push_task_branch_unwritable_origin_is_false(origin_clone):
    bare, clone = origin_clone
    _fix_commit(clone, "task-ro", "X = 2\n")
    modes = {p: p.stat().st_mode for p in [bare, *bare.rglob("*")]}
    for p in modes:
        p.chmod(modes[p] & ~0o222)
    try:
        assert git_ops.push_task_branch(clone, "task-ro") is False
    finally:
        for p, mode in modes.items():
            p.chmod(mode)
    assert subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/task/task-ro"], cwd=bare).returncode != 0


def test_push_task_branch_failure_log_hides_remote_credentials(repo, caplog):
    _fix_commit(repo, "task-cred", "X = 2\n")
    _git(repo, "remote", "add", "origin", "https://user:s3cr3t-token@127.0.0.1:9/o/r.git")

    with caplog.at_level("INFO", logger="workflow.connector.git_ops"):
        assert git_ops.push_task_branch(repo, "task-cred") is False

    assert caplog.records
    assert "s3cr3t-token" not in caplog.text and "127.0.0.1:9" not in caplog.text


def test_push_task_branch_without_origin_is_false(repo):
    _fix_commit(repo, "task-none", "X = 2\n")
    assert git_ops.push_task_branch(repo, "task-none") is False


def test_has_origin(repo, origin_clone):
    _, clone = origin_clone
    assert git_ops.has_origin(clone) is True
    assert git_ops.has_origin(repo) is False


# --- 업무 키 브랜치 (phase 14 step 7) ------------------------------------------------------------


def test_ensure_worktree_with_work_key_uses_runloom_branch(repo):
    base = git_ops.head_sha(repo)

    path = git_ops.ensure_worktree(repo, "task-3", base, work_key="RUN-3")

    assert path == git_ops.worktree_path(repo, "task-3")  # 폴더는 여전히 단계(Task)마다
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "runloom/RUN-3"
    assert git_ops.ensure_worktree(repo, "task-3", base, work_key="RUN-3") == path  # 재작업은 같은 브랜치


def test_retried_stage_starts_a_numbered_branch_from_the_base_commit(repo):
    base = git_ops.head_sha(repo)
    first = git_ops.ensure_worktree(repo, "task-3", base, work_key="RUN-3")
    (first / "pkg.py").write_text("X = 2\n")
    old = git_ops.commit_all(first, "fix: 1차")

    second = git_ops.ensure_worktree(repo, "task-3-retry", base, work_key="RUN-3", branch_seq=2)

    assert _git(second, "rev-parse", "--abbrev-ref", "HEAD") == "runloom/RUN-3-2"
    assert _git(second, "rev-parse", "HEAD") == base
    assert _git(repo, "rev-parse", "runloom/RUN-3") == old  # 옛 브랜치는 그대로


@pytest.mark.parametrize("key,seq", [("run/../x", 1), ("RUN-3 ; rm", 1), ("RUN-3", 0), (None, 2)])
def test_ensure_worktree_refuses_bad_work_key_before_touching_git(repo, key, seq):
    base = git_ops.head_sha(repo)
    with pytest.raises(ValueError):
        git_ops.ensure_worktree(repo, "task-bad", base, work_key=key, branch_seq=seq)
    assert not git_ops.worktree_path(repo, "task-bad").exists()
    assert _git(repo, "branch", "--list") == "* main"


def test_push_task_branch_with_work_key_pushes_runloom_branch(origin_clone):
    bare, clone = origin_clone
    worktree = git_ops.ensure_worktree(clone, "task-3", git_ops.head_sha(clone), work_key="RUN-3")
    (worktree / "pkg.py").write_text("X = 2\n")
    commit = git_ops.commit_all(worktree, "fix(task-3)")

    assert git_ops.push_task_branch(clone, "task-3", work_key="RUN-3") is True
    assert _git(bare, "rev-parse", "refs/heads/runloom/RUN-3") == commit
    assert subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/task/task-3"],
                          cwd=bare).returncode != 0


def test_push_task_branch_refuses_bad_work_key(origin_clone):
    _, clone = origin_clone
    with pytest.raises(ValueError):
        git_ops.push_task_branch(clone, "task-3", work_key="main")
