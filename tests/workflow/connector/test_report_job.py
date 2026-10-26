"""report_job — 러너의 인계 리포트 작업 (ARCHITECTURE "리포트 작업 실행 — 러너", D3·D18·D19).

중앙은 FakeCentral. 어댑터는 `run_report` 만 가진 `ReportStub` — 실제 도구를 띄우지 않는다. original 은 실제 Git 저장소의
결과 커밋을 깨끗한 읽기 전용 체크아웃으로 열고, tailored 는 빈 임시 디렉터리에서 돈다. 둘 다 끝나면 지운다.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from workflow.connector import git_ops, state
from workflow.connector.adapter import ReportOutput
from workflow.connector.local_tool import HANDOFF_REPORT_OUTPUT_SCHEMA
from workflow.connector.report_job import run_report_job
from workflow.contracts.v1 import HandoffReport

from .conftest import REPORT_ID, REPORT_REQUEST, make_report_job

WFC = "wfc_" + "a" * 43
ENV_SECRET = "registered-secret-value"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "billing"
    repo.mkdir()
    (repo / "coupon.py").write_text("APPLIED = set()\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit(repo: Path, text: str, message: str) -> str:
    (repo / "coupon.py").write_text(text)
    _git(repo, "commit", "-q", "-am", message)
    return git_ops.head_sha(repo)


def register(state_conn, repo: Path, *, tool: str = "stub") -> None:
    state.save_registration(state_conn, {
        "local_registration_id": "local-demo-report", "repo_path": str(repo), "tool": tool,
        "repository_id": "billing", "base_commit": git_ops.head_sha(repo) if repo.is_dir() else "0" * 40,
        "verification_profiles": {}, "env": {"MY_TOKEN": ENV_SECRET},
    })


def report_data(evidence: list[str] | None = None, text: str = "쿠폰 재적용을 막았다") -> dict:
    claim = {"id": "change-1", "text": text, "why": None, "evidence": evidence or ["artifact:art-diff-001"]}
    return {
        "sections": {"changes": [claim], "decisions": [], "open_questions": [], "to_confirm": [], "constraints": [],
                     "remaining_work": []},
        "diagram": None,
    }


class ReportStub:
    """`ReportAdapter` — 호출 시점의 cwd 상태를 기록하고 `during(cwd)` 로 쓰기를 흉내 낸다."""

    def __init__(self, output: ReportOutput | None = None, during: Callable[[Path], None] | None = None,
                 raises: Exception | None = None):
        self.calls: list[dict] = []
        self._output = output or ReportOutput(data=report_data(), failed=None)
        self._during = during
        self._raises = raises

    def run_report(self, cwd: Path, prompt_text: str, schema: dict, progress, *, env=None) -> ReportOutput:
        head = _git(cwd, "rev-parse", "HEAD") if (cwd / ".git").exists() else None
        self.calls.append({"cwd": cwd, "prompt": prompt_text, "schema": schema, "env": env, "head": head,
                           "files": sorted(p.name for p in cwd.iterdir())})
        progress("stub 시작", runtime_ref="stub:report")
        if self._during is not None:
            self._during(cwd)
        if self._raises is not None:
            raise self._raises
        return self._output


def run(client, state_conn, adapter, job, tmp_path, **adapters) -> None:
    run_report_job(client, state_conn, {"stub": adapter, **adapters}, job, handoff_root=tmp_path / "handoff")


def _worktrees(repo: Path) -> list[str]:
    return [line for line in _git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


# --- original -----------------------------------------------------------------------------------


def test_original_runs_in_clean_checkout_with_local_evidence_and_submits_report(
    fake, client, state_conn, repo, tmp_path,
):
    base = git_ops.head_sha(repo)
    result = _commit(repo, "APPLIED = set()\nUSED = True\n", "fix: 쿠폰 재적용 막음")
    _commit(repo, "OTHER = 1\n", "chore: 결과 뒤 다른 작업")  # 등록 폴더 HEAD 는 결과 커밋이 아니다
    register(state_conn, repo)
    adapter = ReportStub()

    run(client, state_conn, adapter, make_report_job(result, base), tmp_path)

    call = adapter.calls[0]
    assert call["head"] == result and call["cwd"] != repo  # 결과 커밋의 깨끗한 체크아웃
    assert call["schema"] is HANDOFF_REPORT_OUTPUT_SCHEMA
    assert call["env"] == {"MY_TOKEN": ENV_SECRET}
    assert call["prompt"].startswith(REPORT_REQUEST)
    assert "## 로컬 근거 (연결 프로그램이 모음)" in call["prompt"]
    assert f"{result}\tfix: 쿠폰 재적용 막음" in call["prompt"]
    assert "결과 뒤 다른 작업" not in call["prompt"]
    assert "coupon.py" in call["prompt"]  # diff stat
    report = HandoffReport.model_validate_json(fake.report_results[REPORT_ID])
    assert (report.report_id, report.job_type, report.contract_version) == (REPORT_ID, "original", 1)
    assert report.sections.changes[0].evidence == ["artifact:art-diff-001"]
    assert not call["cwd"].exists() and len(_worktrees(repo)) == 1  # 체크아웃 정리
    assert fake.report_failures == {}


def test_original_without_base_logs_only_checkout_commit(fake, client, state_conn, repo, tmp_path):
    result = _commit(repo, "USED = True\n", "fix: 하나")
    register(state_conn, repo)
    adapter = ReportStub()

    run(client, state_conn, adapter, make_report_job(result), tmp_path)

    prompt = adapter.calls[0]["prompt"]
    assert f"{result}\tfix: 하나" in prompt and "\tbase" not in prompt
    assert REPORT_ID in fake.report_results


def test_original_with_commit_missing_from_repo_runs_in_empty_dir_without_local_evidence(
    fake, client, state_conn, repo, tmp_path,
):
    register(state_conn, repo)
    adapter = ReportStub()

    run(client, state_conn, adapter, make_report_job("e" * 40), tmp_path)

    call = adapter.calls[0]
    assert call["head"] is None and call["files"] == []
    assert "로컬 근거 없음 — " in call["prompt"]
    assert not call["cwd"].exists()
    assert REPORT_ID in fake.report_results


@pytest.mark.parametrize("write", [
    lambda cwd: (cwd / "coupon.py").write_text("CHANGED = 1\n"),
    lambda cwd: (cwd / "new.txt").write_text("x\n"),
    lambda cwd: _git(cwd, "commit", "-q", "--allow-empty", "-m", "몰래 커밋"),
], ids=["modified", "untracked", "commit"])
def test_original_checkout_change_is_readonly_violation_and_not_submitted(
    fake, client, state_conn, repo, tmp_path, write,
):
    result = _commit(repo, "USED = True\n", "fix: 하나")
    register(state_conn, repo)
    adapter = ReportStub(during=write)

    run(client, state_conn, adapter, make_report_job(result), tmp_path)

    assert fake.report_results == {}
    assert fake.report_failures[REPORT_ID]["failure_code"] == "readonly_violation"
    assert not adapter.calls[0]["cwd"].exists() and len(_worktrees(repo)) == 1
    assert git_ops.head_sha(repo) == result  # 원본 저장소는 그대로


# --- tailored ------------------------------------------------------------------------------------


def test_tailored_runs_without_checkout_or_local_evidence(fake, client, state_conn, repo, tmp_path):
    register(state_conn, repo)
    adapter = ReportStub(ReportOutput(data=report_data(["claim:change-1"]), failed=None))
    job = make_report_job(job_type="tailored", request="# 맞춤 풀이: RUN-12\n\n원본 리포트 JSON")

    run(client, state_conn, adapter, job, tmp_path)

    call = adapter.calls[0]
    assert call["head"] is None and call["files"] == [] and "## 로컬 근거" not in call["prompt"]
    assert not call["cwd"].exists()
    report = HandoffReport.model_validate_json(fake.report_results[REPORT_ID])
    assert report.job_type == "tailored"


def test_empty_dir_write_is_readonly_violation(fake, client, state_conn, repo, tmp_path):
    register(state_conn, repo)
    adapter = ReportStub(during=lambda cwd: (cwd / "notes.md").write_text("x"))

    run(client, state_conn, adapter, make_report_job(job_type="tailored"), tmp_path)

    assert fake.report_failures[REPORT_ID]["failure_code"] == "readonly_violation"
    assert fake.report_results == {}


# --- 실패 보고 ------------------------------------------------------------------------------------


def test_adapter_failure_is_reported_with_its_code(fake, client, state_conn, repo, tmp_path):
    result = _commit(repo, "USED = True\n", "fix: 하나")
    register(state_conn, repo)
    adapter = ReportStub(ReportOutput(data=None, failed=("timeout", f"20분 초과 {WFC}")))

    run(client, state_conn, adapter, make_report_job(result), tmp_path)

    failure = fake.report_failures[REPORT_ID]
    assert failure["failure_code"] == "timeout"
    assert WFC not in failure["message"] and "wfc_***" in failure["message"]
    assert fake.report_results == {} and len(_worktrees(repo)) == 1


def test_adapter_exception_is_adapter_error(fake, client, state_conn, repo, tmp_path):
    register(state_conn, repo)

    run(client, state_conn, ReportStub(raises=RuntimeError("boom")), make_report_job(job_type="tailored"),
        tmp_path)

    assert fake.report_failures[REPORT_ID]["failure_code"] == "adapter_error"
    assert "boom" in fake.report_failures[REPORT_ID]["message"]


def test_contract_violation_is_result_invalid(fake, client, state_conn, repo, tmp_path):
    register(state_conn, repo)
    bad = report_data()
    bad["sections"]["changes"] = []  # changes 는 1개 이상
    adapter = ReportStub(ReportOutput(data=bad, failed=None))

    run(client, state_conn, adapter, make_report_job(job_type="tailored"), tmp_path)

    assert fake.report_failures[REPORT_ID]["failure_code"] == "result_invalid"
    assert fake.report_results == {}


def test_missing_registration_and_adapter_are_reported_before_running(fake, client, state_conn, repo, tmp_path):
    adapter = ReportStub()

    run(client, state_conn, adapter, make_report_job(job_type="tailored"), tmp_path)
    missing_registration = fake.report_failures.pop(REPORT_ID)
    register(state_conn, repo, tool="codex")
    run(client, state_conn, adapter, make_report_job(job_type="tailored"), tmp_path, codex=object())

    assert missing_registration["failure_code"] == "registration_missing"
    assert fake.report_failures[REPORT_ID]["failure_code"] == "adapter_missing"  # run_report 없는 어댑터
    assert adapter.calls == []


# --- 비밀값·늦은 제출 ------------------------------------------------------------------------------


def test_secrets_and_registered_env_values_are_masked_before_submit(fake, client, state_conn, repo, tmp_path):
    register(state_conn, repo)
    adapter = ReportStub(ReportOutput(data=report_data(text=f"토큰 {WFC} 와 {ENV_SECRET}"), failed=None))

    run(client, state_conn, adapter, make_report_job(job_type="tailored"), tmp_path)

    body = fake.report_results[REPORT_ID].decode()
    assert WFC not in body and ENV_SECRET not in body
    assert "wfc_***" in body and "<env:MY_TOKEN>" in body
    HandoffReport.model_validate_json(body)  # 마스킹 뒤에도 계약에 맞는다


def test_late_submit_409_is_logged_not_raised(fake, client, state_conn, repo, tmp_path, caplog):
    register(state_conn, repo)
    fake.report_reject = (409, "report_not_leased")

    run(client, state_conn, ReportStub(), make_report_job(job_type="tailored"), tmp_path)

    assert "report_not_leased" in caplog.text
    assert fake.report_failures == {}
