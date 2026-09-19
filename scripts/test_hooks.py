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

    def test_research_probe_script_is_exempt(self, repo):
        """docs/research/ 아래의 일회성 프로브 스크립트는 TDD 대상이 아니다."""
        (repo / "docs" / "research" / "probe").mkdir(parents=True)
        (repo / "docs" / "research" / "probe" / "probe.py").write_text("print(1)")
        assert run_verify(repo).returncode == 0

    def test_archived_script_is_exempt(self, repo):
        """docs/archive/ 아래로 보관된 스크립트도 TDD 대상이 아니다."""
        d = repo / "docs" / "archive" / "2026-09-16-x" / "research" / "probe"
        d.mkdir(parents=True)
        (d / "probe.py").write_text("print(1)")
        assert run_verify(repo).returncode == 0

    def test_python_source_with_mirrored_tests_dir_passes(self, repo):
        """tests/ 가 src/ 구조를 따라간 배치도 인정한다: src/a/b/x.py → tests/a/b/test_x.py."""
        (repo / "src" / "pkg" / "sub").mkdir(parents=True)
        (repo / "src" / "pkg" / "sub" / "mod.py").write_text("x = 1\n")
        (repo / "tests" / "pkg" / "sub").mkdir(parents=True)
        (repo / "tests" / "pkg" / "sub" / "test_mod.py").write_text(
            "def test_x():\n    assert True\n")
        assert run_verify(repo).returncode == 0

    def test_python_main_entrypoint_is_exempt(self, repo):
        """`python3 -m pkg` 진입점 __main__.py 는 __init__.py 와 같이 TDD 대상이 아니다."""
        (repo / "src" / "pkg").mkdir(parents=True)
        (repo / "src" / "pkg" / "__init__.py").write_text("")
        (repo / "src" / "pkg" / "__main__.py").write_text("print('entry')\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_dummy.py").write_text("def test_x():\n    assert True\n")
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

    @pytest.mark.skipif(
        subprocess.run(["python3", "-m", "ruff", "--version"], capture_output=True).returncode != 0,
        reason="ruff 미설치")
    def test_python_lint_failure_is_reported(self, repo):
        """pytest 는 통과해도 ruff 가 실패하면 검증 실패로 전달한다."""
        (repo / "src").mkdir()
        (repo / "src" / "bad.py").write_text("value = undefined_name\n")
        (repo / "src" / "test_bad.py").write_text("def test_ok():\n    assert True\n")
        r = run_verify(repo)
        assert r.returncode == 2
        assert "ruff" in r.stderr

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


# ---------------------------------------------------------------------------
# Codex 어댑터 — 전역 ~/.codex/hooks.json 이 저장소의 scripts/hooks/codex-*.sh 를 찾아 실행한다.
# 판정 규칙은 tdd-guard.sh / verify.sh 에 위임하고, 여기서는 입출력 변환만 검증한다.
# ---------------------------------------------------------------------------

HOOKS = VERIFY.parent


def run_hook(name, repo, payload):
    return subprocess.run(
        ["bash", str(HOOKS / name)], cwd=repo, input=json.dumps(payload),
        capture_output=True, text=True, timeout=120,
    )


class TestCodexBlockDangerous:
    def test_denies_rm_rf(self, repo):
        r = run_hook("codex-block-dangerous.sh", repo, {"tool_input": {"command": "rm -rf build"}})
        assert r.returncode == 0
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_handles_argv_array(self, repo):
        r = run_hook("codex-block-dangerous.sh", repo, {"tool_input": {"command": ["git", "push", "--force"]}})
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_passes_safe_command(self, repo):
        r = run_hook("codex-block-dangerous.sh", repo, {"tool_input": {"command": "git push origin main"}})
        assert r.returncode == 0 and r.stdout == ""

    def test_passes_non_bash_tool(self, repo):
        """전역 훅은 matcher 없이 모든 도구에 걸린다 — command 가 없으면 통과."""
        r = run_hook("codex-block-dangerous.sh", repo, {"tool_input": {"file_path": "src/a.ts"}})
        assert r.returncode == 0 and r.stdout == ""


class TestCodexTddGuard:
    PATCH_ADD = "*** Begin Patch\n*** Add File: src/foo.ts\n+export const a = 1\n*** End Patch"

    def test_denies_apply_patch_without_test(self, repo):
        (repo / "src").mkdir()
        r = run_hook("codex-tdd-guard.sh", repo, {"tool_input": {"command": self.PATCH_ADD}})
        assert r.returncode == 0
        out = json.loads(r.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "foo" in out["permissionDecisionReason"]

    def test_allows_apply_patch_when_test_exists(self, repo):
        (repo / "src").mkdir()
        (repo / "src" / "foo.test.ts").write_text("test('a', () => {})")
        r = run_hook("codex-tdd-guard.sh", repo, {"tool_input": {"command": self.PATCH_ADD}})
        assert r.returncode == 0 and r.stdout == ""

    def test_ignores_delete(self, repo):
        patch = "*** Begin Patch\n*** Delete File: src/foo.ts\n*** End Patch"
        r = run_hook("codex-tdd-guard.sh", repo, {"tool_input": {"command": patch}})
        assert r.returncode == 0 and r.stdout == ""

    def test_accepts_file_path_directly(self, repo):
        (repo / "src").mkdir()
        r = run_hook("codex-tdd-guard.sh", repo, {"tool_input": {"file_path": str(repo / "src" / "bar.ts")}})
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_passes_non_edit_tool(self, repo):
        r = run_hook("codex-tdd-guard.sh", repo, {"tool_input": {"command": "ls"}})
        assert r.returncode == 0 and r.stdout == ""


class TestCodexVerifyGate:
    def test_blocks_with_verify_message(self, repo):
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        r = run_hook("codex-verify-gate.sh", repo, {"stop_hook_active": False})
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["decision"] == "block"
        assert "foo.ts" in out["reason"]

    def test_allows_when_nothing_to_verify(self, repo):
        r = run_hook("codex-verify-gate.sh", repo, {"stop_hook_active": False})
        assert r.returncode == 0 and r.stdout == ""

    def test_stop_hook_active_passes_through(self, repo):
        """재개된 턴은 verify.sh 가 스킵한다 — 어댑터도 막지 않아야 한다."""
        (repo / "src").mkdir()
        (repo / "src" / "foo.ts").write_text("export const a = 1")
        r = run_hook("codex-verify-gate.sh", repo, {"stop_hook_active": True})
        assert r.returncode == 0 and r.stdout == ""
