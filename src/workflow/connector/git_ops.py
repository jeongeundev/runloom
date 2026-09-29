"""Git worktree·커밋·diff — 연결 프로그램이 결과 보존을 관리한다 (ARCHITECTURE "Codex와 worktree").

- 단계(Task)별 worktree 는 저장소 옆 `<repo>-worktrees/<task_id>/`. 브랜치는 `contracts/v1.result_branch` — 업무 키가
  있으면 `runloom/<키>`(다시 맡긴 단계는 `-<순번>`), 없으면 `task/<task_id>`. 재시도는 같은 worktree·같은 브랜치.
- 원본 저장소의 작업 트리·기준 브랜치는 건드리지 않는다. 결과는 작업 브랜치의 로컬 커밋으로만 남긴다.
- 모든 명령은 고정 인자 배열이다. task_id·커밋 ID 는 불투명 문자열이며 경로로 해석하지 않는다.
- 기준 커밋 추적(ADR-0018 결정 2): `fetch_origin`·`origin_head` 만 네트워크에 닿는다. 원격 이름 `origin`·`refs/remotes/origin/HEAD`
  는 코드 상수이고, 자격 입력 프롬프트 없이(`GIT_TERMINAL_PROMPT=0`) 제한 시간 안에 끝낸다.
- 작업 복사본 준비물(ADR-0018 결정 3): `link_prepared_paths` 가 등록의 `links` 를 원본 폴더로 향하는 심볼릭 링크로 걸고
  저장소 공용 `info/exclude` 에 넣는다. 링크를 거부하는 도구(Next Turbopack 은 작업 복사본 밖을 가리키는 `node_modules`
  링크를 거부한다)용 `copies` 는 `copy_prepared_paths` 가 복사한다(macOS 는 APFS 복제 `cp -Rc`). 원본 폴더의 파일은 읽기만 한다.
- 결과 브랜치 push(ADR-0018 결정 4): `push_task_branch` 는 결과 브랜치만 같은 이름으로 origin 에 보낸다. force 없음,
  사용자 로컬 git 자격·훅 그대로. 실패 로그에서 원격 URL 은 가린다(자격이 URL 에 들어 있을 수 있다).
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from workflow.contracts.v1 import result_branch

DEFAULT_AUTHOR = "workflow-connector <connector@localhost>"
GIT_NETWORK_TIMEOUT_SECONDS = 120
_ORIGIN_HEAD = "refs/remotes/origin/HEAD"
_TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_GITIGNORE_SPECIAL = re.compile(r"([\\*?\[])")
_REMOTE_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+|\S+@\S+:\S*")

log = logging.getLogger(__name__)


class GitError(Exception):
    pass


def _git(args: list[str], cwd: Path, *, network: bool = False) -> str:
    extra = {"env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"}, "timeout": GIT_NETWORK_TIMEOUT_SECONDS} if network else {}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, **extra)
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git {' '.join(args)}: {exc.stderr.strip() or exc.stdout.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)}: {GIT_NETWORK_TIMEOUT_SECONDS}초 안에 끝나지 않음") from exc
    except OSError as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    return result.stdout


def _safe(name: str) -> str:
    # 파일 이름·브랜치 이름으로만 쓴다. `..` 와 앞뒤 `.` 은 git ref 규칙에 걸리므로 함께 치운다
    name = re.sub(r"\.{2,}", "_", _SAFE_NAME.sub("_", name)).strip(".")
    return name or "_"


def head_sha(repo: Path) -> str:
    return _git(["rev-parse", "HEAD"], repo).strip()


def has_commit(repo: Path, commit: str) -> bool:
    """`commit` 이 이 저장소에 있는 커밋 객체인가. 다른 기기·클론의 커밋은 가져오지 않는다 (fetch 없음).
    `fetch_origin` 으로 받은 원격 추적 커밋도 같은 객체 저장소에 있으므로 찾는다."""
    return subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, capture_output=True,
    ).returncode == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, capture_output=True,
    ).returncode == 0


def fetch_origin(repo: Path) -> None:
    """`git fetch --quiet origin`. origin 없음·연결 실패·시간 초과는 GitError. 작업 트리·로컬 브랜치는 건드리지 않는다."""
    _git(["fetch", "--quiet", "origin"], repo, network=True)


def origin_head(repo: Path) -> str | None:
    """`refs/remotes/origin/HEAD` 가 가리키는 커밋 — 마지막 fetch 기준 origin 기본 브랜치 최신. 심볼릭 ref 가 없으면
    (원격을 나중에 붙인 저장소) `git remote set-head origin --auto` 를 한 번 한다. origin 없음·실패면 None."""
    try:
        _git(["remote", "get-url", "origin"], repo)
        if subprocess.run(
            ["git", "symbolic-ref", "--quiet", _ORIGIN_HEAD], cwd=repo, capture_output=True,
        ).returncode != 0:
            _git(["remote", "set-head", "origin", "--auto"], repo, network=True)
        return _git(["rev-parse", "--verify", f"{_ORIGIN_HEAD}^{{commit}}"], repo).strip()
    except GitError:
        return None


def has_origin(repo: Path) -> bool:
    return subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True).returncode == 0


def push_task_branch(repo: Path, task_id: str, *, work_key: str | None = None, branch_seq: int = 1) -> bool:
    """결과 브랜치를 origin 의 같은 이름 브랜치로 push 한다 (fast-forward 만). 거부·자격 없음·네트워크·시간 초과는
    False 와 로그 — 호출자는 실행 결과를 바꾸지 않는다. 대상 ref 가 `refs/heads/task/…`·`refs/heads/runloom/…` 로 고정이라
    기본 브랜치에 닿지 않는다. 계약 밖 키는 ValueError."""
    ref = f"refs/heads/{_task_branch(task_id, work_key, branch_seq)}"
    try:
        _git(["push", "--quiet", "origin", f"{ref}:{ref}"], repo, network=True)
    except GitError as exc:
        log.info("task 브랜치 push 실패 (%s): %s", ref, _REMOTE_URL.sub("<원격>", str(exc)))
        return False
    return True


def _task_branch(task_id: str, work_key: str | None = None, branch_seq: int = 1) -> str:
    return result_branch(_safe(task_id), work_key, branch_seq)


def worktree_path(repo: Path, task_id: str) -> Path:
    return repo.parent / f"{repo.name}-worktrees" / _safe(task_id)


def ensure_worktree(
    repo: Path, task_id: str, base_commit: str, *, work_key: str | None = None, branch_seq: int = 1,
) -> Path:
    """없으면 결과 브랜치로 base_commit 에서 만든다. 있으면 그 브랜치인지 확인하고 그대로 쓴다. 계약 밖 키는 git 을
    부르기 전에 ValueError."""
    branch = _task_branch(task_id, work_key, branch_seq)
    path = worktree_path(repo, task_id)
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


def link_prepared_paths(repo: Path, checkout: Path, links: list[str]) -> list[str]:
    """`checkout/<p>` → `repo/<p>` 심볼릭 링크를 걸고 건 경로를 돌려준다. 원본에 대상이 없거나 체크아웃에 이미 그 경로가
    있으면(추적 파일·이전 시도의 링크) 건너뛴다. 경로마다 `info/exclude` 에 `/<p>` 를 한 번 넣는다 — `.gitignore` 의
    `node_modules/` 처럼 끝이 `/` 인 규칙은 링크(디렉터리가 아님)에 맞지 않아 `commit_all`·`is_dirty` 에 잡히기 때문이다.
    `links` 는 등록 때(cli) 검사한 상대 경로다."""
    if not links:
        return []
    _exclude_paths(repo, links)
    linked = []
    for rel in links:
        source, dest = repo / rel, checkout / rel
        if os.path.lexists(dest):
            if not (dest.is_symlink() and os.readlink(dest) == str(source)):
                log.info("링크 %s 건너뜀 — 작업 복사본에 이미 있다 (추적 파일은 덮지 않는다)", rel)
            continue
        if not source.exists():
            log.info("링크 %s 건너뜀 — 원본 폴더에 없다", rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, dest)
        linked.append(rel)
    return linked


def copy_prepared_paths(repo: Path, checkout: Path, copies: list[str]) -> list[str]:
    """`repo/<p>` 를 `checkout/<p>` 로 복사하고 복사한 경로를 돌려준다. 건너뛰기·`info/exclude` 규칙은
    `link_prepared_paths` 와 같다. macOS 는 APFS 복제(`cp -Rc` — 파일 내용을 복사하지 않아 수 초), 그 밖은 일반 복사."""
    if not copies:
        return []
    _exclude_paths(repo, copies)
    copied = []
    for rel in copies:
        source, dest = repo / rel, checkout / rel
        if os.path.lexists(dest):
            log.info("복사 %s 건너뜀 — 작업 복사본에 이미 있다 (추적 파일은 덮지 않는다)", rel)
            continue
        if not source.exists():
            log.info("복사 %s 건너뜀 — 원본 폴더에 없다", rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["cp", "-Rc", str(source), str(dest)], check=True, capture_output=True)
        elif source.is_dir():
            shutil.copytree(source, dest, symlinks=True)
        else:
            shutil.copy2(source, dest)
        copied.append(rel)
    return copied


def _exclude_paths(repo: Path, paths: list[str]) -> None:
    """경로마다 `info/exclude` 에 `/<p>` 를 한 번 넣는다(저장소 공용 — 모든 worktree 에 적용)."""
    exclude = repo / _git(["rev-parse", "--git-path", "info/exclude"], repo).strip()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.is_file() else ""
    rules = text.splitlines()
    new_rules = [rule for rule in dict.fromkeys(_exclude_rule(p) for p in paths) if rule not in rules]
    if new_rules:
        with exclude.open("a") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + "".join(f"{r}\n" for r in new_rules))


def _exclude_rule(rel: str) -> str:
    """저장소 뿌리에 고정한 문자 그대로의 규칙 — glob 특수 문자는 `\\` 로 막는다."""
    return "/" + _GITIGNORE_SPECIAL.sub(r"\\\1", rel)


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
