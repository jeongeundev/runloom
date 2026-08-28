"""verify.sh Stop 훅 동작 테스트."""
import json
import subprocess
from pathlib import Path

import pytest

VERIFY = Path(__file__).resolve().parent / "hooks" / "verify.sh"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def _mark_verified(repo):
    """직전 커밋까지 검증을 통과한 상태로 만든다 (verify.sh 의 마커)."""
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    (repo / ".git" / "harness-verified").write_text(sha)


@pytest.fixture
def repo(tmp_path):
    """커밋 1개를 가진 깨끗한 git 저장소."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("# t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def run_verify(repo, *, stop_hook_active=False):
    payload = json.dumps({"stop_hook_active": stop_hook_active})
    return subprocess.run(
        ["bash", str(VERIFY)], cwd=repo, input=payload,
        capture_output=True, text=True, timeout=120,
    )


class TestVerifyGate:
    def test_no_changes_exits_zero(self, repo):
        """대화만 한 경우 — 변경이 없으면 검증을 돌리지 않는다."""
        assert run_verify(repo).returncode == 0

    def test_only_docs_changed_exits_zero(self, repo):
        """문서만 고친 경우도 검증 대상이 아니다."""
        (repo / "docs").mkdir()
        (repo / "docs" / "PRD.md").write_text("# PRD")
        assert run_verify(repo).returncode == 0

    def test_stop_hook_active_skips(self, repo):
        """stop 훅으로 재개된 턴은 재검증하지 않는다 (무한 루프 방지)."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        assert run_verify(repo, stop_hook_active=True).returncode == 0


class TestTddInvariant:
    def test_source_without_test_fails(self, repo):
        """Bash 리다이렉션으로 만들었든, 테스트 없는 소스 파일은 잡힌다."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        r = run_verify(repo)
        assert r.returncode == 2
        assert "foo.ts" in r.stderr

    def test_source_with_test_passes(self, repo):
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        (repo / "src" / "foo.test.ts").write_text("test('a', () => {})")
        assert run_verify(repo).returncode == 0

    def test_committed_source_without_test_also_fails(self, repo):
        """커밋된 변경도 HEAD 기준이 아니라 작업 트리 기준으로 본다."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        _git(repo, "add", "-A")
        r = run_verify(repo)
        assert r.returncode == 2


class TestToolchainGate:
    """툴체인이 '실제로 돌았는지'를 실패하는 스크립트로 증명한다."""

    @pytest.fixture
    def repo_with_failing_toolchain(self, repo):
        (repo / "package.json").write_text(json.dumps({
            "name": "t", "scripts": {"test": "exit 1"}
        }))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add package.json")
        _mark_verified(repo)   # 여기까지는 검증된 상태로 둔다
        return repo

    def test_docs_change_does_not_run_toolchain(self, repo_with_failing_toolchain):
        """문서만 바뀌면 반드시 실패하는 npm test 조차 돌지 않아야 한다."""
        repo = repo_with_failing_toolchain
        (repo / "docs").mkdir()
        (repo / "docs" / "PRD.md").write_text("# PRD")
        assert run_verify(repo).returncode == 0

    def test_source_change_runs_toolchain(self, repo_with_failing_toolchain):
        """소스가 바뀌면 툴체인이 돌고, 실패가 전달된다."""
        repo = repo_with_failing_toolchain
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        (repo / "src" / "foo.test.ts").write_text("test('a', () => {})")
        r = run_verify(repo)
        assert r.returncode == 2
        assert "npm run test" in r.stderr


class TestCommittedWorkStillVerified:
    """세션이 커밋하고 턴을 끝내도 검증은 건너뛰지 않는다.

    execute.py 프리앰블 규칙 6이 세션에게 커밋을 지시하므로,
    '작업 트리가 더러운가' 만으로 게이트를 걸면 검증이 통째로 빠진다.
    """

    def test_committed_source_without_test_is_caught(self, repo):
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: foo")
        assert subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                              capture_output=True, text=True).stdout == ""
        r = run_verify(repo)
        assert r.returncode == 2
        assert "foo.ts" in r.stderr

    def test_pure_conversation_after_verify_skips(self, repo):
        """한 번 검증을 통과한 뒤, 아무 변경 없는 턴은 건너뛴다."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        (repo / "src" / "foo.test.ts").write_text("test('a', () => {})")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: foo")
        assert run_verify(repo).returncode == 0      # 1회차 — 실제 검증
        assert run_verify(repo).returncode == 0      # 2회차 — 대화만, 건너뜀

    def test_failed_verification_repeats_until_fixed(self, repo):
        """검증 실패 시 마커를 갱신하지 않으므로, 고칠 때까지 계속 잡힌다."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: foo")
        assert run_verify(repo).returncode == 2
        assert run_verify(repo).returncode == 2   # 안 고쳤으므로 여전히 실패
        (repo / "src" / "foo.test.ts").write_text("test('a', () => {})")
        assert run_verify(repo).returncode == 0   # 고치면 통과
        assert run_verify(repo).returncode == 0   # 이후 대화 턴은 건너뜀
