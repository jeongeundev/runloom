"""runner — claim → 접수 → 어댑터 선택(등록의 tool) → 인계 자료 → 어댑터 → 업로드 → result_ready|failed 루프
(ARCHITECTURE "상태·재접속·완료").

중앙은 FakeCentral. 어댑터는 `EchoAdapter` 또는 훅을 가진 `StubAdapter`. `Runner` 는 도구 이름 → 어댑터 매핑을 받고
실행 요청의 `target.local_registration_id` 로 찾은 등록의 `tool` 로 하나를 고른다.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, EchoAdapter, make_meta
from workflow.connector.client import Unreachable
from workflow.connector.git_ops import GitError
from workflow.connector.runner import Runner
from workflow.contracts.v1 import (
    CodeChangeResult,
    CodeReviewResult,
    ExecutionRequest,
    ExecutionUsage,
    GenericResult,
    Verification,
)

from .conftest import (
    BASE_COMMIT,
    CONNECTOR_ID,
    NOW,
    REVIEW_SPEC,
    FakeCentral,
    assign_with_generic_handoff,
    assign_with_handoff,
    make_local_request,
    make_request,
    make_review_request,
)


class StubAdapter:
    """훅 `during(progress)` 를 어댑터 실행 도중 호출한다 (중앙 상태 조작·연결 끊김 재현용)."""

    def __init__(self, *, output=None, during=None, raises: Exception | None = None):
        self.calls: list[tuple[ExecutionRequest, Path]] = []
        self.handoff_files: list[dict[str, bytes]] = []  # 실행 시점의 인계 자료 — 종료 뒤엔 디렉터리가 지워진다
        self._output = output
        self._during = during
        self._raises = raises

    def run(self, request, handoff_dir, progress):
        self.calls.append((request, handoff_dir))
        self.handoff_files.append({p.name: p.read_bytes() for p in handoff_dir.iterdir()})
        progress("stub 시작", runtime_ref=f"stub:{request.execution_id}")
        if self._during is not None:
            self._during(progress)
        if self._raises is not None:
            raise self._raises
        return self._output or ok_output(request)


def ok_output(request: ExecutionRequest, extra_artifacts=()) -> AdapterOutput:
    artifacts = [
        make_meta("diff", "fix.diff", b"--- a\n+++ b\n", "text/plain"),
        make_meta("test_log_before", "before.txt", b"exit_code=1\nFAILED\n", "text/plain"),
        make_meta("test_log_after", "after.txt", b"exit_code=0\npassed\n", "text/plain"),
        make_meta("verification_log", "verify.txt", b"exit_code=0\n", "text/plain"),
        make_meta("report_output", "report.txt", "합계    20    5\n".encode(), "text/plain"),
        *extra_artifacts,
    ]
    result = CodeChangeResult(
        contract_version=1, execution_id=request.execution_id, task_id=request.task_id,
        outcome="ready_for_review", summary="stub 수정", base_commit=BASE_COMMIT, result_commit=BASE_COMMIT,
        artifact_ids=[],
        verification=Verification(profile_id="vp-pytest", result_commit=BASE_COMMIT, exit_code=0,
                                  log_artifact_id="verification_log"),
    )
    return AdapterOutput(result=result, artifacts=artifacts, failed=None, runtime_ref="stub:x")


REPO = "demo-report-repo"


def register(state_conn, tmp_path, local_registration_id: str = "local-demo-report", *, tool: str = "stub") -> None:
    state.save_registration(state_conn, {
        "local_registration_id": local_registration_id, "repo_path": str(tmp_path / REPO), "tool": tool,
        "repository_id": REPO, "base_commit": BASE_COMMIT, "verification_profiles": {},
    })


def make_runner(
    client, state_conn, paths, adapter, tmp_path, clock=lambda: NOW, heartbeat_interval: float = 30.0,
) -> Runner:
    """등록 `local-demo-report`(tool `stub`) 하나와 그 도구의 어댑터 하나 — 단일 어댑터 흐름 테스트용."""
    register(state_conn, tmp_path)
    return Runner(
        client, state_conn, paths, {"stub": adapter}, CONNECTOR_ID, clock, tmp_path / "handoff",
        heartbeat_interval=heartbeat_interval,
    )


def event_types(fake: FakeCentral, execution_id: str) -> list[tuple[int, str]]:
    return [(e["seq"], e["type"]) for e in fake.events_of(execution_id)]


# --- 정상 흐름 ------------------------------------------------------------------------


def test_happy_path_sends_events_in_order_and_uploads_result(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    adapter = StubAdapter(during=lambda progress: progress("재현 테스트 작성, 수정 전 실행 실패 확인"))
    runner = make_runner(client, state_conn, paths, adapter, tmp_path)

    runner.tick()

    events = fake.events_of(request.execution_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["type"] for e in events][:2] == ["accepted", "started"]
    assert events[1]["data"] == {"runtime_ref": f"stub:{request.execution_id}"}
    assert [e["type"] for e in events][-1] == "result_ready"
    assert {e["type"] for e in events} == {"accepted", "started", "progress", "result_ready"}
    assert fake.executions[request.execution_id]["status"] == "result_ready"

    uploaded = fake.artifacts_of(request.execution_id)
    assert {"diff", "test_log_before", "test_log_after", "verification_log", "report_output",
            "code_change_result"} <= set(uploaded)
    result = CodeChangeResult.model_validate_json(uploaded["code_change_result"]["data"])
    assert events[-1]["data"]["result_artifact_id"] in fake.artifacts
    assert fake.artifacts[events[-1]["data"]["result_artifact_id"]]["kind"] == "code_change_result"
    assert set(result.artifact_ids) == {
        a_id for a_id, a in fake.artifacts.items()
        if a["execution_id"] == request.execution_id and a["kind"] != "code_change_result"
    }
    assert result.verification.log_artifact_id in fake.artifacts
    assert fake.artifacts[result.verification.log_artifact_id]["kind"] == "verification_log"

    # 로컬 기록: finished, 미전송 없음, 다음 tick 은 새 claim
    row = state.get_execution(state_conn, request.execution_id)
    assert row["phase"] == "finished" and row["finished_at"] is not None
    assert state.pop_pending(state_conn, request.execution_id) == []
    assert state.active_execution(state_conn) is None


def test_handoff_dir_has_manifest_and_attachments_named_by_evidence(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    adapter = StubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    _, handoff_dir = adapter.calls[0]
    assert handoff_dir.name == f"{request.task_id}.handoff"
    files = adapter.handoff_files[0]
    assert sorted(files) == ["diagnosis_result.json", "log-daily-0920@1.txt", "manifest.json", "response-after@1.json"]
    manifest = json.loads(files["manifest.json"])
    assert manifest["source_execution_id"] == "exec-diagnose-001"
    assert json.loads(files["response-after@1.json"])["report_date"] == "2026-09-19"
    assert json.loads(files["diagnosis_result.json"])["outcome"] == "ready_for_handoff"  # inputs 의 diagnosis_result


def test_handoff_dir_is_next_to_registered_repo(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    adapter = StubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()  # 등록 repo_path = tmp_path/demo-report-repo

    assert adapter.calls[0][1] == tmp_path / f"{REPO}-worktrees" / f"{request.task_id}.handoff"


def test_echo_adapter_completes_flow(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, EchoAdapter(), tmp_path).tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    result = CodeChangeResult.model_validate_json(
        fake.artifacts_of(request.execution_id)["code_change_result"]["data"]
    )
    assert result.outcome == "ready_for_review"
    assert "response-after@1.json" in fake.artifacts_of(request.execution_id)["report_output"]["data"].decode()


def test_no_assignment_is_quiet(fake, client, state_conn, paths, tmp_path):
    adapter = StubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert fake.claims == 1 and adapter.calls == []
    assert fake.heartbeats == [
        {"contract_version": 1, "connector_id": CONNECTOR_ID, "current_execution_id": None}
    ]


def test_heartbeat_is_sent_once_per_interval(fake, client, state_conn, paths, tmp_path):
    runner = make_runner(client, state_conn, paths, StubAdapter(), tmp_path)

    runner.tick()
    runner.tick()

    assert len(fake.heartbeats) == 1


def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_heartbeat_continues_while_adapter_runs(fake, client, state_conn, paths, tmp_path):
    """도구가 heartbeat 주기보다 오래 돌면 tick 은 어댑터 안에 묶여 있다. 그동안에도 현재 실행 ID 를 담은 heartbeat 가
    가야 중앙이 Agent 를 offline 으로 보지 않는다 (ARCHITECTURE "연결 생존과 모델 진행 구분")."""
    request = assign_with_handoff(fake)
    adapter = StubAdapter(during=lambda progress: _wait_until(lambda: len(fake.heartbeats) >= 3))
    runner = make_runner(client, state_conn, paths, adapter, tmp_path, heartbeat_interval=0.05)

    runner.tick()

    during_run = fake.heartbeats[1:]  # [0] 은 claim 전 tick 의 것
    assert len(during_run) >= 2
    assert {h["current_execution_id"] for h in during_run} == {request.execution_id}
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_heartbeat_thread_stops_when_adapter_finishes(fake, client, state_conn, paths, tmp_path):
    assign_with_handoff(fake)
    adapter = StubAdapter(during=lambda progress: _wait_until(lambda: len(fake.heartbeats) >= 2))
    runner = make_runner(client, state_conn, paths, adapter, tmp_path, heartbeat_interval=0.05)

    runner.tick()
    sent = len(fake.heartbeats)
    time.sleep(0.2)

    assert len(fake.heartbeats) == sent


def test_heartbeat_failure_during_run_does_not_fail_execution(fake, client, state_conn, paths, tmp_path):
    """실행 중 중앙이 끊겨 heartbeat 가 실패해도 어댑터·결과 전달은 그대로다 — 스레드는 다음 주기에 다시 시도한다."""
    request = assign_with_handoff(fake)

    def during(progress):
        attempts = lambda: sum(1 for r in fake.requests if r.url.path == "/connector/heartbeat")  # noqa: E731
        before = attempts()
        fake.reachable = False
        _wait_until(lambda: attempts() >= before + 2)
        fake.reachable = True
        sent = len(fake.heartbeats)
        _wait_until(lambda: len(fake.heartbeats) > sent)

    runner = make_runner(client, state_conn, paths, StubAdapter(during=during), tmp_path, heartbeat_interval=0.05)

    runner.tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    assert fake.heartbeats[-1]["current_execution_id"] == request.execution_id


# --- 어댑터 선택 — 등록의 tool ---------------------------------------------------------------


def _request_for(local_registration_id: str, execution_id: str, task_id: str) -> ExecutionRequest:
    request = make_request(execution_id=execution_id, task_id=task_id)
    return request.model_copy(update={
        "target": request.target.model_copy(update={"local_registration_id": local_registration_id}),
    })


def test_adapter_is_chosen_by_registered_tool_of_target(fake, client, state_conn, paths, tmp_path):
    register(state_conn, tmp_path, "local-codex", tool="codex")
    register(state_conn, tmp_path, "local-claude", tool="claude")
    codex, claude = StubAdapter(), StubAdapter()
    runner = Runner(client, state_conn, paths, {"codex": codex, "claude": claude}, CONNECTOR_ID, lambda: NOW,
                    tmp_path / "handoff")
    first = assign_with_handoff(fake, _request_for("local-claude", "exec-fix-001", "fix-1"))
    second = assign_with_handoff(fake, _request_for("local-codex", "exec-fix-002", "fix-2"))

    runner.tick()
    runner.tick()

    assert [r.execution_id for r, _ in claude.calls] == [first.execution_id]
    assert [r.execution_id for r, _ in codex.calls] == [second.execution_id]
    assert fake.executions[first.execution_id]["status"] == "result_ready"
    assert fake.executions[second.execution_id]["status"] == "result_ready"


def test_missing_registration_fails_before_handoff_download_and_adapter(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)  # local-demo-report 등록 없음
    adapter = StubAdapter()
    runner = Runner(client, state_conn, paths, {"codex": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    assert adapter.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "registration_missing"
    assert events[-1]["data"]["process_stopped"] is True
    assert "local-demo-report" in events[-1]["data"]["message"]
    assert fake.executions[request.execution_id]["status"] == "failed"
    assert not any("/artifacts/" in str(r.url) for r in fake.requests)  # 인계 자료를 내려받지 않는다
    assert state.active_execution(state_conn) is None


def test_registered_tool_without_adapter_fails_with_adapter_missing(fake, client, state_conn, paths, tmp_path):
    register(state_conn, tmp_path, tool="claude")
    request = assign_with_handoff(fake)
    codex = StubAdapter()
    runner = Runner(client, state_conn, paths, {"codex": codex}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    assert codex.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "adapter_missing"
    assert events[-1]["data"]["process_stopped"] is True
    assert "claude" in events[-1]["data"]["message"] and "codex" in events[-1]["data"]["message"]
    assert fake.executions[request.execution_id]["status"] == "failed"
    assert state.active_execution(state_conn) is None


# --- 재접속·중복 방지 ------------------------------------------------------------------


def test_crash_after_record_claim_resumes_from_accepted_without_second_execution(
    fake, client, state_conn, paths, tmp_path
):
    request = assign_with_handoff(fake)
    # 첫 Runner 가 claim 응답을 받아 로컬 접수 기록까지 남기고 죽었다 (accepted 이벤트 전송 전)
    first = client.claim(CONNECTOR_ID)
    assert first == request
    state.record_claim(state_conn, first, NOW)
    adapter = StubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()  # 새 Runner

    assert [t for _, t in event_types(fake, request.execution_id)][:2] == ["accepted", "started"]
    assert event_types(fake, request.execution_id)[0] == (1, "accepted")
    assert len(fake.executions) == 1 and len(adapter.calls) == 1
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_launching_or_running_on_restart_is_unknown_local_and_not_rerun(
    fake, client, state_conn, paths, tmp_path
):
    request = assign_with_handoff(fake)
    client.claim(CONNECTOR_ID)
    state.record_claim(state_conn, request, NOW)
    state.set_phase(state_conn, request.execution_id, "launching")
    claims_before = fake.claims
    adapter = StubAdapter()
    runner = make_runner(client, state_conn, paths, adapter, tmp_path)

    runner.tick()
    runner.tick()

    assert adapter.calls == []
    assert fake.events_of(request.execution_id) == []  # failed 도 보내지 않는다
    assert fake.claims == claims_before  # 새 배정도 받지 않는다
    row = state.get_execution(state_conn, request.execution_id)
    assert row["phase"] == "launching" and row["unknown_local_at"] == NOW
    assert fake.heartbeats[-1]["current_execution_id"] == request.execution_id


def test_sequence_gap_is_recovered_by_resending_from_expected(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    def during(progress):
        progress("첫 진행")  # seq 3 전송·ack
        fake.forget_events_after(request.execution_id, 1)  # 중앙이 seq 2·3 을 잃음 (복원 등)
        progress("둘째 진행")  # seq 4 → sequence_gap expected 2 → 2 부터 재전송

    make_runner(client, state_conn, paths, StubAdapter(during=during), tmp_path).tick()

    events = event_types(fake, request.execution_id)
    assert [seq for seq, _ in events] == list(range(1, len(events) + 1))
    assert [t for _, t in events][:4] == ["accepted", "started", "progress", "progress"]
    assert events[-1][1] == "result_ready"
    assert state.pop_pending(state_conn, request.execution_id) == []


def test_events_queue_locally_while_unreachable_and_flush_in_order(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    def during(progress):
        fake.reachable = False
        progress("끊긴 동안 진행 1")
        progress("끊긴 동안 진행 2")

    runner = make_runner(client, state_conn, paths, StubAdapter(during=during), tmp_path)
    runner.tick()

    assert [t for _, t in event_types(fake, request.execution_id)] == ["accepted", "started"]
    pending = state.pop_pending(state_conn, request.execution_id)
    assert [(e.seq, e.type) for e in pending] == [(3, "progress"), (4, "progress")]
    assert "code_change_result" not in fake.artifacts_of(request.execution_id)

    fake.reachable = True
    runner.tick()

    events = event_types(fake, request.execution_id)
    assert events[:4] == [(1, "accepted"), (2, "started"), (3, "progress"), (4, "progress")]
    assert events[-1][1] == "result_ready"
    assert fake.executions[request.execution_id]["status"] == "result_ready"
    assert state.pop_pending(state_conn, request.execution_id) == []


def test_unreachable_during_upload_does_not_rerun_adapter(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    def during(progress):
        fake.reachable = False

    adapter = StubAdapter(during=during)
    runner = make_runner(client, state_conn, paths, adapter, tmp_path)
    runner.tick()
    fake.reachable = True
    runner.tick()

    assert len(adapter.calls) == 1
    assert fake.executions[request.execution_id]["status"] == "result_ready"
    assert fake.claims == 1


# --- 실패 ----------------------------------------------------------------------------


def test_handoff_hash_mismatch_fails_before_adapter(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake, tamper=True)
    adapter = StubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert adapter.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "handoff_hash_mismatch"
    assert events[-1]["data"]["process_stopped"] is True
    assert "log-daily-0920@1" in events[-1]["data"]["message"]
    assert fake.executions[request.execution_id]["status"] == "failed"
    assert state.active_execution(state_conn) is None


def test_adapter_failure_is_reported_with_process_stopped_flag(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    stderr = make_meta("codex_stderr", "stderr.txt", b"boom\n", "text/plain")
    output = AdapterOutput(result=None, artifacts=[stderr], failed=("timeout", "20분 초과", True),
                           runtime_ref="stub:x")

    make_runner(client, state_conn, paths, StubAdapter(output=output), tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "started", "failed"]
    assert events[-1]["data"] == {"code": "timeout", "message": "20분 초과", "process_stopped": True}
    assert "codex_stderr" in fake.artifacts_of(request.execution_id)  # 실패 산출물도 보존
    assert "code_change_result" not in fake.artifacts_of(request.execution_id)


def test_adapter_exception_is_failed_without_process_stopped(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, StubAdapter(raises=RuntimeError("어댑터 버그 wfc_" + "z" * 43)), tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert events[-1]["type"] == "failed"
    assert events[-1]["data"]["code"] == "adapter_error"
    assert events[-1]["data"]["process_stopped"] is False
    assert "wfc_z" not in events[-1]["data"]["message"] and "wfc_***" in events[-1]["data"]["message"]


# --- 마스킹 ----------------------------------------------------------------------------


def test_secrets_in_artifacts_are_masked_before_upload_with_warning(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    secret = "wfc_" + "s" * 43
    jsonl = make_meta("codex_jsonl", "codex.jsonl", f'{{"env": "{secret}"}}\n'.encode(), "application/x-ndjson")
    adapter = StubAdapter(output=ok_output(request, extra_artifacts=[jsonl]))

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    uploaded = fake.artifacts_of(request.execution_id)["codex_jsonl"]
    assert secret.encode() not in uploaded["data"]
    assert b"wfc_***" in uploaded["data"]
    assert secret not in json.dumps([a["data"].decode("utf-8", "replace") for a in fake.artifacts.values()])
    warnings = [e for e in fake.events_of(request.execution_id)
                if e["type"] == "progress" and "마스킹" in e["data"]["message"]]
    assert len(warnings) == 1 and "codex.jsonl" in warnings[0]["data"]["message"]
    assert secret not in json.dumps(fake.events_of(request.execution_id))


def test_progress_messages_are_masked(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    secret = "sk-" + "k" * 40

    make_runner(client, state_conn, paths, StubAdapter(during=lambda p: p(f"키 {secret}")), tmp_path).tick()

    messages = [e["data"]["message"] for e in fake.events_of(request.execution_id) if e["type"] == "progress"]
    assert any("sk-***" in m for m in messages) and all(secret not in m for m in messages)


def _register_env(state_conn, tmp_path, env: dict[str, str]) -> None:
    registration = state.get_registration(state_conn, "local-demo-report")
    state.save_registration(state_conn, {**registration, "env": env})


DB_URL = "postgresql://agent:pw-local-5434@localhost:5434/openarchive"


def test_registered_env_values_are_masked_in_artifacts_progress_and_result(fake, client, state_conn, paths, tmp_path):
    """러너 로컬 등록의 `--env` 값(8자 이상)은 산출물·진행 메시지·결과 봉투에서 `<env:이름>` 으로 가린다."""
    request = assign_with_handoff(fake)
    log = make_meta("verification_log", "verify.txt", f"exit_code=1\nconnect {DB_URL} refused\n".encode(), "text/plain")
    output = ok_output(request, extra_artifacts=[log])
    output = AdapterOutput(
        result=output.result.model_copy(update={"summary": f"DB {DB_URL} 로 테스트"}), artifacts=output.artifacts,
        failed=None, runtime_ref="stub:x",
    )
    runner = make_runner(client, state_conn, paths, StubAdapter(output=output, during=lambda p: p(f"DB {DB_URL}")),
                         tmp_path)
    _register_env(state_conn, tmp_path, {"DATABASE_URL": DB_URL, "DEBUG": "1"})

    runner.tick()

    dumped = json.dumps([a["data"].decode("utf-8", "replace") for a in fake.artifacts.values()])
    assert DB_URL not in dumped and "<env:DATABASE_URL>" in dumped
    assert "<env:DEBUG>" not in dumped  # 짧은 값은 가리지 않는다
    events = fake.events_of(request.execution_id)
    assert DB_URL not in json.dumps(events, ensure_ascii=False)
    assert any("<env:DATABASE_URL>" in e["data"].get("message", "") for e in events if e["type"] == "progress")
    assert events[-1]["type"] == "result_ready"


def test_registered_env_values_are_masked_in_failed_message(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    output = AdapterOutput(result=None, artifacts=[], failed=("adapter_error", f"연결 실패 {DB_URL}", True),
                           runtime_ref="stub:x")
    runner = make_runner(client, state_conn, paths, StubAdapter(output=output), tmp_path)
    _register_env(state_conn, tmp_path, {"DATABASE_URL": DB_URL})

    runner.tick()

    failed = fake.events_of(request.execution_id)[-1]
    assert failed["type"] == "failed" and failed["data"]["message"] == "연결 실패 <env:DATABASE_URL>"


# --- 작업 디렉터리 정리 — 결과가 중앙에 닿은 뒤 worktree·인계 디렉터리를 지우고 브랜치·커밋은 남긴다 ----------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_git_repo(tmp_path) -> Path:
    """등록 `repo_path` 자리(`tmp_path/demo-report-repo`)에 실제 Git 저장소 — worktree 정리 규칙 테스트용."""
    repo = tmp_path / REPO
    repo.mkdir()
    (repo / "pkg.py").write_text("X = 1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


class WorktreeAdapter(StubAdapter):
    """`LocalToolAdapter` 처럼 업무 worktree 를 만들고 결과 커밋을 남긴 뒤 output 을 돌려준다."""

    def __init__(self, repo: Path, **kwargs):
        super().__init__(**kwargs)
        self.repo = repo
        self.result_commit: str | None = None

    def run(self, request, handoff_dir, progress):
        worktree = git_ops.ensure_worktree(self.repo, request.task_id, git_ops.head_sha(self.repo))
        (worktree / "pkg.py").write_text("X = 2\n")
        self.result_commit = git_ops.commit_all(worktree, f"fix({request.task_id}): 수정")
        return super().run(request, handoff_dir, progress)


def _workdirs(repo: Path, adapter: StubAdapter, request: ExecutionRequest) -> tuple[Path, Path]:
    return git_ops.worktree_path(repo, request.task_id), adapter.calls[0][1]


def test_workdirs_are_removed_after_result_ready_and_task_branch_remains(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    base = git_ops.head_sha(repo)
    request = assign_with_handoff(fake)
    adapter = WorktreeAdapter(repo)

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert not worktree.exists() and not handoff_dir.exists()
    assert request.task_id not in _git(repo, "worktree", "list")
    # 브랜치와 결과 커밋은 남는다 — 병합은 운영자 확인 뒤 사람이 한다
    assert _git(repo, "rev-parse", f"task/{request.task_id}") == adapter.result_commit
    assert _git(repo, "rev-parse", f"task/{request.task_id}^") == base
    assert _git(repo, "rev-parse", "main") == base and _git(repo, "status", "--porcelain") == ""
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW


def test_keep_workdirs_leaves_worktree_and_handoff_dir(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    adapter = WorktreeAdapter(repo)
    register(state_conn, tmp_path)
    runner = Runner(client, state_conn, paths, {"stub": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff",
                    keep_workdirs=True)

    runner.tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert worktree.exists() and handoff_dir.exists()
    assert _git(worktree, "rev-parse", "HEAD") == adapter.result_commit
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] is None


def test_failed_with_process_stopped_removes_workdirs(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    output = AdapterOutput(result=None, artifacts=[], failed=("timeout", "20분 초과", True), runtime_ref="stub:x")
    adapter = WorktreeAdapter(repo, output=output)

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert fake.events_of(request.execution_id)[-1]["data"]["process_stopped"] is True
    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert not worktree.exists() and not handoff_dir.exists()
    assert _git(repo, "rev-parse", f"task/{request.task_id}") == adapter.result_commit
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW


def test_failed_without_process_stopped_keeps_workdirs(fake, client, state_conn, paths, tmp_path):
    """프로세스 종료를 확인하지 못한 실패 — 살아 있는 프로세스의 cwd 일 수 있으므로 지우지 않는다."""
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    output = AdapterOutput(result=None, artifacts=[], failed=("timeout", "20분 초과", False), runtime_ref="stub:x")
    adapter = WorktreeAdapter(repo, output=output)

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert fake.events_of(request.execution_id)[-1]["data"]["process_stopped"] is False
    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert worktree.exists() and handoff_dir.exists()
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] is None


def test_cleanup_waits_until_result_ready_is_delivered(fake, client, state_conn, paths, tmp_path, monkeypatch):
    """결과 업로드 뒤 result_ready 전송이 끊기면 다음 tick 에 재전송이 확인된 뒤에야 지운다."""
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    adapter = WorktreeAdapter(repo)
    original = client.post_event
    dropped: list[str] = []

    def flaky(event):
        if event.type == "result_ready" and not dropped:
            dropped.append(event.type)
            raise Unreachable("끊김")
        return original(event)

    monkeypatch.setattr(client, "post_event", flaky)
    runner = make_runner(client, state_conn, paths, adapter, tmp_path)
    runner.tick()

    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert "code_change_result" in fake.artifacts_of(request.execution_id)  # 업로드는 끝났지만
    assert fake.executions[request.execution_id]["status"] == "running"  # result_ready 는 아직 중앙에 없다
    assert worktree.exists() and handoff_dir.exists()
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] is None

    runner.tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    assert not worktree.exists() and not handoff_dir.exists()
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW
    assert len(adapter.calls) == 1


def test_cleanup_failure_is_only_logged_after_result_ready(fake, client, state_conn, paths, tmp_path, monkeypatch,
                                                            caplog):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    adapter = WorktreeAdapter(repo)

    def denied(repo_, path):
        raise GitError("worktree remove: Permission denied")

    monkeypatch.setattr(git_ops, "remove_worktree", denied)
    caplog.set_level(logging.WARNING, logger="workflow.connector.runner")

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert events[-1]["type"] == "result_ready"
    assert fake.executions[request.execution_id]["status"] == "result_ready"
    worktree, handoff_dir = _workdirs(repo, adapter, request)
    assert worktree.exists() and not handoff_dir.exists()  # 인계 디렉터리 정리는 worktree 와 독립
    assert "Permission denied" in caplog.text
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] is None
    assert state.active_execution(state_conn) is None


def test_cleanup_passes_quietly_when_worktree_never_existed(fake, client, state_conn, paths, tmp_path, caplog):
    request = assign_with_handoff(fake)  # StubAdapter 는 worktree 를 만들지 않고 등록 repo_path 도 없다
    adapter = StubAdapter()
    caplog.set_level(logging.WARNING, logger="workflow.connector.runner")

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert not adapter.calls[0][1].exists()
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW
    assert caplog.records == []


# --- 사용자 정의 종류 (`LocalTarget`) — 인계 입력 내려받기·어댑터 선택·generic_result 업로드·정리 ----------------


def generic_output(request: ExecutionRequest, outcome: str = "approved", extra_artifacts=()) -> AdapterOutput:
    result = GenericResult(
        contract_version=1, execution_id=request.execution_id, task_id=request.task_id, kind=request.kind,
        outcome=outcome, summary="stub 검토", artifact_ids=[],
    )
    artifacts = [
        make_meta("claude_jsonl", "claude.jsonl", b'{"type":"result"}\n', "application/x-ndjson"),
        make_meta("claude_stderr", "claude-stderr.txt", b"fake claude: done\n", "text/plain"),
        *extra_artifacts,
    ]
    return AdapterOutput(result=result, artifacts=artifacts, failed=None, runtime_ref="stub:x")


class GenericStubAdapter(StubAdapter):
    """`LocalTarget` 요청에 `GenericResult` 를 돌려주는 stub. 인계 디렉터리에 파일을 만들지 않는다."""

    def run(self, request, handoff_dir, progress):
        self._output = self._output or generic_output(request)
        return super().run(request, handoff_dir, progress)


def test_generic_handoff_inputs_are_saved_by_kind_with_extension(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    (called_request, handoff_dir), = adapter.calls
    assert called_request.kind == "review" and called_request.kind_spec == REVIEW_SPEC
    assert handoff_dir == tmp_path / f"{REPO}-worktrees" / f"{request.task_id}.handoff"  # 등록 repo 옆
    files = adapter.handoff_files[0]
    assert sorted(files) == ["code_change_result.json", "diff.patch", "manifest.json", "test_log_after.txt"]
    assert files["diff.patch"].startswith(b"--- a/daily_report/transformer.py")
    assert json.loads(files["code_change_result.json"])["outcome"] == "ready_for_review"
    assert files["test_log_after.txt"].startswith(b"exit_code=0")
    assert json.loads(files["manifest.json"])["source_kind"] == "code_change"
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_generic_handoff_input_hash_mismatch_fails_before_adapter(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake, tamper=True)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert adapter.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "handoff_hash_mismatch"
    assert events[-1]["data"]["process_stopped"] is True
    assert "diff" in events[-1]["data"]["message"]
    assert fake.executions[request.execution_id]["status"] == "failed"


def test_generic_handoff_second_input_of_same_kind_gets_artifact_id_suffix(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake, duplicate_kind=True)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    manifest = json.loads(adapter.handoff_files[0]["manifest.json"])
    second = [i for i in manifest["inputs"] if i["kind"] == "test_log_after"][1]
    expected = f"test_log_after-{second['artifact_id'][:8]}.txt"
    assert sorted(adapter.handoff_files[0]) == sorted([
        "code_change_result.json", "diff.patch", "manifest.json", "test_log_after.txt", expected,
    ])
    assert adapter.handoff_files[0][expected].endswith(b"(2)\n")
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_source_result_outside_inputs_is_saved_as_source_kind_result(fake, client, state_conn, paths, tmp_path):
    """규칙 handoff_kinds 에 선행 결과 kind 가 없어도 선행 결과 봉투는 `{source_kind}_result.{ext}` 로 내려받는다."""
    request = assign_with_generic_handoff(fake, source_result_in_inputs=False)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    files = adapter.handoff_files[0]
    assert sorted(files) == ["code_change_result.json", "diff.patch", "manifest.json", "test_log_after.txt"]
    assert json.loads(files["code_change_result.json"])["summary"] == "(테스트 수정 결과)"
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_local_target_adapter_is_chosen_by_registered_tool(fake, client, state_conn, paths, tmp_path):
    register(state_conn, tmp_path, "local-codex", tool="codex")
    register(state_conn, tmp_path, "local-claude", tool="claude")
    codex, claude = GenericStubAdapter(), GenericStubAdapter()
    runner = Runner(client, state_conn, paths, {"codex": codex, "claude": claude}, CONNECTOR_ID, lambda: NOW,
                    tmp_path / "handoff")
    request = assign_with_generic_handoff(fake, make_local_request(local_registration_id="local-claude"))

    runner.tick()

    assert [r.execution_id for r, _ in claude.calls] == [request.execution_id] and codex.calls == []
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_local_target_missing_registration_fails_before_download(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake)  # local-demo-report 등록 없음
    adapter = GenericStubAdapter()
    runner = Runner(client, state_conn, paths, {"claude": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    assert adapter.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "registration_missing"
    assert not any("/artifacts/" in str(r.url) for r in fake.requests)


def test_finalize_uploads_generic_result_and_sends_result_ready(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "started", "result_ready"]
    uploaded = fake.artifacts_of(request.execution_id)
    assert set(uploaded) == {"claude_jsonl", "claude_stderr", "generic_result"}
    assert "code_change_result" not in uploaded
    assert uploaded["generic_result"]["name"] == "generic_result.json"
    assert uploaded["generic_result"]["content_type"] == "application/json"
    result = GenericResult.model_validate_json(uploaded["generic_result"]["data"])
    assert (result.kind, result.outcome, result.summary) == ("review", "approved", "stub 검토")
    assert (result.execution_id, result.task_id) == (request.execution_id, request.task_id)
    assert sorted(result.artifact_ids) == sorted(
        a_id for a_id, a in fake.artifacts.items()
        if a["execution_id"] == request.execution_id and a["kind"] != "generic_result"
    )
    result_artifact_id = events[-1]["data"]["result_artifact_id"]
    assert fake.artifacts[result_artifact_id]["kind"] == "generic_result"
    assert fake.executions[request.execution_id]["status"] == "result_ready"
    row = state.get_execution(state_conn, request.execution_id)
    assert GenericResult.model_validate_json(row["result_json"]).kind == "review"  # 로컬 기록은 문자열 그대로
    assert state.active_execution(state_conn) is None


def test_echo_adapter_completes_generic_flow(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake)

    make_runner(client, state_conn, paths, EchoAdapter(), tmp_path).tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    result = GenericResult.model_validate_json(fake.artifacts_of(request.execution_id)["generic_result"]["data"])
    assert result.outcome == "approved" and "diff.patch" in result.summary and result.artifact_ids == []


def test_local_target_cleanup_removes_handoff_dir_without_worktree(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    base = git_ops.head_sha(repo)
    request = assign_with_generic_handoff(fake)
    adapter = GenericStubAdapter()

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    handoff_dir = adapter.calls[0][1]
    assert handoff_dir == repo.parent / f"{REPO}-worktrees" / f"{request.task_id}.handoff"
    assert not handoff_dir.exists()
    assert not git_ops.worktree_path(repo, request.task_id).exists()  # worktree 는 만들지 않았다
    assert _git(repo, "worktree", "list").count("\n") == 0 and _git(repo, "branch", "--list", "task/*") == ""
    assert _git(repo, "rev-parse", "HEAD") == base and _git(repo, "status", "--porcelain") == ""
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW


def test_local_target_keep_workdirs_leaves_handoff_dir(fake, client, state_conn, paths, tmp_path):
    make_git_repo(tmp_path)
    request = assign_with_generic_handoff(fake)
    adapter = GenericStubAdapter()
    register(state_conn, tmp_path)
    runner = Runner(client, state_conn, paths, {"stub": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff",
                    keep_workdirs=True)

    runner.tick()

    assert fake.executions[request.execution_id]["status"] == "result_ready"
    assert adapter.calls[0][1].exists()
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] is None


# --- 일반 버그 수정 `bug_fix` ---------------------------------------------------------------------


def bug_fix_request(execution_id: str = "exec-gh-fix-001") -> ExecutionRequest:
    """첫 시도 — 입력 없음. target 은 `CodeChangeTarget` 그대로(등록 local-demo-report 를 빌려 쓴다)."""
    return ExecutionRequest.model_validate({
        **make_request(execution_id=execution_id, task_id="task-gh-41").model_dump(),
        "kind": "bug_fix", "request": "GitHub acme/billing#41 — 할인 쿠폰이 두 번 적용됨", "input_artifact_ids": [],
    })


def bug_fix_output(request: ExecutionRequest) -> AdapterOutput:
    """보고서 없는 일반 버그 결과 — 필수 산출물은 diff·테스트 전후·검증 로그뿐이다."""
    output = ok_output(request)
    output.artifacts = [(meta, data) for meta, data in output.artifacts if meta.kind != "report_output"]
    return output


def test_bug_fix_without_inputs_runs_with_heartbeat_and_uploads_code_change_result(
    fake, client, state_conn, paths, tmp_path,
):
    request = bug_fix_request()
    fake.assign(request)
    adapter = StubAdapter(
        output=bug_fix_output(request), during=lambda progress: _wait_until(lambda: len(fake.heartbeats) >= 3),
    )
    runner = make_runner(client, state_conn, paths, adapter, tmp_path, heartbeat_interval=0.05)

    runner.tick()

    (called, handoff_dir), = adapter.calls
    assert called == request and adapter.handoff_files == [{}]  # 입력이 없어도 빈 인계 디렉터리로 돈다
    assert {h["current_execution_id"] for h in fake.heartbeats[1:]} == {request.execution_id}
    assert [t for _, t in event_types(fake, request.execution_id)][-1] == "result_ready"
    uploaded = fake.artifacts_of(request.execution_id)
    assert set(uploaded) == {"diff", "test_log_before", "test_log_after", "verification_log", "code_change_result"}
    result = CodeChangeResult.model_validate_json(uploaded["code_change_result"]["data"])
    assert fake.artifacts[result.verification.log_artifact_id]["kind"] == "verification_log"
    assert not handoff_dir.exists()  # 종료가 전달된 뒤 정리


def test_claim_declares_supported_builtin_kinds(fake, client, state_conn, paths, tmp_path):
    runner = make_runner(client, state_conn, paths, StubAdapter(), tmp_path)

    runner.tick()

    claim = next(r for r in fake.requests if r.url.path == "/connector/claim")
    assert json.loads(claim.content)["supported_kinds"] == ["code_change", "bug_fix", "code_review"]


# --- 커밋 검토 `code_review` ---------------------------------------------------------------------


REVIEWED = "8e2a4c6f0b1d3e5a7c9f2b4d6e8a0c1f3b5d7e9a"


def review_output(request: ExecutionRequest) -> AdapterOutput:
    """검토 결과 — 산출물은 원시 로그뿐, 봉투는 `CodeReviewResult`."""
    result = CodeReviewResult.model_validate({
        "contract_version": 1, "execution_id": request.execution_id, "task_id": request.task_id,
        "source_execution_id": request.target.source_execution_id, "reviewed_commit": request.target.result_commit,
        "outcome": "changes_requested", "summary": "stub 검토",
        "findings": [{"severity": "blocking", "path": "pkg.py", "line": 1, "message": "경계 누락"}],
        "missing_information": [], "artifact_ids": [],
    })
    artifacts = [make_meta("claude_jsonl", "claude.jsonl", b'{"type":"result"}\n', "application/x-ndjson"),
                 make_meta("claude_stderr", "claude-stderr.txt", b"", "text/plain")]
    return AdapterOutput(result=result, artifacts=artifacts, runtime_ref="stub:review")


def test_code_review_uploads_code_review_result_and_cleans_only_handoff_dir(fake, client, state_conn, paths,
                                                                           tmp_path):
    repo = make_git_repo(tmp_path)
    base = git_ops.head_sha(repo)
    worktree = git_ops.ensure_worktree(repo, "task-gh-41", base)  # 수정 Task 가 남긴 결과 커밋·브랜치
    (worktree / "pkg.py").write_text("X = 2\n")
    result_commit = git_ops.commit_all(worktree, "fix")
    git_ops.remove_worktree(repo, worktree)
    register(state_conn, tmp_path, "local-billing-claude", tool="claude")
    request = make_review_request(base, result_commit)
    request = assign_with_generic_handoff(fake, request)
    adapter = StubAdapter(output=review_output(request))
    runner = Runner(client, state_conn, paths, {"claude": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    (called, handoff_dir), = adapter.calls
    assert called == request
    assert handoff_dir == repo.parent / f"{REPO}-worktrees" / "task-gh-41-review.handoff"
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "started", "result_ready"]
    uploaded = fake.artifacts_of(request.execution_id)
    assert set(uploaded) == {"claude_jsonl", "claude_stderr", "code_review_result"}
    assert uploaded["code_review_result"]["name"] == "code_review_result.json"
    result = CodeReviewResult.model_validate_json(uploaded["code_review_result"]["data"])
    assert (result.outcome, result.reviewed_commit, result.source_execution_id) == (
        "changes_requested", result_commit, "exec-gh-fix-001")
    assert sorted(result.artifact_ids) == sorted(
        a_id for a_id, a in fake.artifacts.items()
        if a["execution_id"] == request.execution_id and a["kind"] != "code_review_result"
    )
    assert fake.artifacts[events[-1]["data"]["result_artifact_id"]]["kind"] == "code_review_result"
    # 정리: 인계 디렉터리만 지운다. 수정 Task 의 브랜치·결과 커밋은 그대로
    assert not handoff_dir.exists()
    assert _git(repo, "rev-parse", "task/task-gh-41") == result_commit
    assert _git(repo, "rev-parse", "HEAD") == base and _git(repo, "status", "--porcelain") == ""
    assert state.get_execution(state_conn, request.execution_id)["cleaned_at"] == NOW


def test_code_review_missing_registration_fails_before_download(fake, client, state_conn, paths, tmp_path):
    request = assign_with_generic_handoff(fake, make_review_request(BASE_COMMIT, REVIEWED))
    adapter = StubAdapter()
    runner = Runner(client, state_conn, paths, {"claude": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    assert adapter.calls == []
    events = fake.events_of(request.execution_id)
    assert [e["type"] for e in events] == ["accepted", "failed"]
    assert events[-1]["data"]["code"] == "registration_missing"


# --- 측정 (phase 9) — started 의 등록 폴더 커밋, 종료 이벤트의 사용량 ---------------------------------------


def _started(fake: FakeCentral, execution_id: str) -> dict:
    return next(e["data"] for e in fake.events_of(execution_id) if e["type"] == "started")


def test_started_carries_registered_folder_head_and_clean_state(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, StubAdapter(), tmp_path).tick()

    assert _started(fake, request.execution_id) == {
        "runtime_ref": f"stub:{request.execution_id}", "folder_commit": _git(repo, "rev-parse", "HEAD"),
        "folder_dirty": False,
    }
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_started_marks_folder_dirty_with_uncommitted_change(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    (repo / "CLAUDE.md").write_text("# 로컬 에이전트 설정 — 아직 커밋 안 함\n")
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, StubAdapter(), tmp_path).tick()

    started = _started(fake, request.execution_id)
    assert started["folder_commit"] == _git(repo, "rev-parse", "HEAD") and started["folder_dirty"] is True


def test_started_reads_registered_folder_not_task_worktree(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    request = assign_with_handoff(fake)
    adapter = WorktreeAdapter(repo)  # worktree 에 결과 커밋을 만든 뒤 started 를 알린다

    make_runner(client, state_conn, paths, adapter, tmp_path).tick()

    started = _started(fake, request.execution_id)
    assert adapter.result_commit != base
    assert (started["folder_commit"], started["folder_dirty"]) == (base, False)


def test_started_omits_folder_fields_when_folder_is_not_git(fake, client, state_conn, paths, tmp_path):
    (tmp_path / REPO).mkdir()  # 등록 폴더는 있지만 Git 저장소가 아니다
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, StubAdapter(), tmp_path).tick()

    assert _started(fake, request.execution_id) == {"runtime_ref": f"stub:{request.execution_id}"}
    assert fake.executions[request.execution_id]["status"] == "result_ready"


def test_started_from_adapter_return_also_carries_folder_fields(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)

    class SilentAdapter:  # progress 로 runtime_ref 를 주지 않는다 — 반환값으로 started 를 보낸다
        def run(self, request, handoff_dir, progress):
            return ok_output(request)

    make_runner(client, state_conn, paths, SilentAdapter(), tmp_path).tick()

    started = _started(fake, request.execution_id)
    assert started == {"runtime_ref": "stub:x", "folder_commit": _git(repo, "rev-parse", "HEAD"),
                       "folder_dirty": False}


def test_result_ready_carries_adapter_usage(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    output = ok_output(request)
    output.usage = ExecutionUsage(cost_usd=0.25, input_tokens=1200, output_tokens=None)

    make_runner(client, state_conn, paths, StubAdapter(output=output), tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert events[-1]["type"] == "result_ready"
    assert events[-1]["data"]["usage"] == {"cost_usd": 0.25, "input_tokens": 1200, "output_tokens": None}


def test_result_ready_without_usage_omits_the_field(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)

    make_runner(client, state_conn, paths, StubAdapter(), tmp_path).tick()

    assert set(fake.events_of(request.execution_id)[-1]["data"]) == {"result_artifact_id"}


def test_failed_carries_adapter_usage(fake, client, state_conn, paths, tmp_path):
    request = assign_with_handoff(fake)
    output = AdapterOutput(result=None, failed=("usage_limit", "한도", True), runtime_ref="stub:x",
                           usage=ExecutionUsage(cost_usd=0.01, input_tokens=429, output_tokens=67))

    make_runner(client, state_conn, paths, StubAdapter(output=output), tmp_path).tick()

    events = fake.events_of(request.execution_id)
    assert events[-1]["type"] == "failed"
    assert events[-1]["data"] == {
        "code": "usage_limit", "message": "한도", "process_stopped": True,
        "usage": {"cost_usd": 0.01, "input_tokens": 429, "output_tokens": 67},
    }


def test_usage_survives_restart_before_result_ready_is_sent(fake, client, state_conn, paths, tmp_path, monkeypatch):
    request = assign_with_handoff(fake)
    output = ok_output(request)
    output.usage = ExecutionUsage(input_tokens=5, output_tokens=6)
    adapter = StubAdapter(output=output)
    runner = make_runner(client, state_conn, paths, adapter, tmp_path)
    original = client.upload_artifact
    calls = {"n": 0}

    def flaky_upload(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:  # 결과 봉투 업로드에서 끊긴다 — 어댑터 산출물 5개는 올라갔다
            raise Unreachable("끊김")
        return original(*args, **kwargs)

    monkeypatch.setattr(client, "upload_artifact", flaky_upload)
    runner.tick()
    assert fake.events_of(request.execution_id)[-1]["type"] == "started"

    Runner(client, state_conn, paths, {"stub": adapter}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff").tick()

    events = fake.events_of(request.execution_id)
    assert len(adapter.calls) == 1 and events[-1]["type"] == "result_ready"
    assert events[-1]["data"]["usage"] == {"cost_usd": None, "input_tokens": 5, "output_tokens": 6}


def test_no_event_carries_registered_folder_path(fake, client, state_conn, paths, tmp_path):
    repo = make_git_repo(tmp_path)
    request = assign_with_handoff(fake)
    output = ok_output(request)
    output.usage = ExecutionUsage(cost_usd=0.1)

    make_runner(client, state_conn, paths, StubAdapter(output=output), tmp_path).tick()

    dumped = json.dumps(fake.events_of(request.execution_id), ensure_ascii=False)
    assert "folder_commit" in dumped and "usage" in dumped
    assert str(repo) not in dumped and str(repo.resolve()) not in dumped and REPO not in dumped


# --- 기준 커밋 보고 (phase 12 step 3, ADR-0018 결정 2) --------------------------------------


class FakeFetch:
    """`git_ops.fetch_origin`·`origin_head` 대역. `heads` 에 없는 저장소는 fetch 가 실패한다."""

    def __init__(self, heads: dict[str, str]):
        self.heads = heads
        self.fetched: list[str] = []

    def fetch_origin(self, repo):
        self.fetched.append(Path(repo).name)
        if Path(repo).name not in self.heads:
            raise GitError("git fetch --quiet origin: 연결 실패")

    def origin_head(self, repo):
        return self.heads.get(Path(repo).name)


def _fetch_runner(monkeypatch, client, state_conn, paths, tmp_path, heads):
    fetch = FakeFetch(heads)
    monkeypatch.setattr(git_ops, "fetch_origin", fetch.fetch_origin)
    monkeypatch.setattr(git_ops, "origin_head", fetch.origin_head)
    for reg_id in ("reg-ok", "reg-broken"):
        state.save_registration(state_conn, {
            "local_registration_id": reg_id, "repo_path": str(tmp_path / reg_id), "tool": "stub",
            "repository_id": REPO, "base_commit": BASE_COMMIT, "verification_profiles": {},
        })
    runner = Runner(client, state_conn, paths, {"stub": StubAdapter()}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")
    return runner, fetch


def test_claim_reports_fetched_origin_heads_and_skips_failed_registrations(
    fake, client, state_conn, paths, tmp_path, monkeypatch, caplog,
):
    runner, fetch = _fetch_runner(monkeypatch, client, state_conn, paths, tmp_path, {"reg-ok": "e" * 40})
    caplog.set_level(logging.INFO, logger="workflow.connector.runner")

    runner.tick()

    assert sorted(fetch.fetched) == ["reg-broken", "reg-ok"]
    assert fake.claim_bodies[-1]["registration_heads"] == {"reg-ok": "e" * 40}
    assert "reg-broken" in caplog.text  # 실패는 로그에만


def test_fetch_is_not_repeated_within_the_interval_but_heads_are_resent(
    fake, client, state_conn, paths, tmp_path, monkeypatch,
):
    runner, fetch = _fetch_runner(monkeypatch, client, state_conn, paths, tmp_path, {"reg-ok": "e" * 40})
    runner.tick()
    fetch.heads["reg-ok"] = "f" * 40

    runner.tick()

    assert len(fetch.fetched) == 2  # 두 번째 tick 은 fetch 하지 않는다
    assert fake.claim_bodies[-1]["registration_heads"] == {"reg-ok": "e" * 40}

    monkeypatch.setattr("workflow.connector.runner.BASE_FETCH_INTERVAL_SECONDS", 0)
    runner.tick()

    assert len(fetch.fetched) == 4
    assert fake.claim_bodies[-1]["registration_heads"] == {"reg-ok": "f" * 40}


def test_claim_without_any_head_omits_the_field(fake, client, state_conn, paths, tmp_path, monkeypatch):
    runner, _ = _fetch_runner(monkeypatch, client, state_conn, paths, tmp_path, {})

    runner.tick()

    assert "registration_heads" not in fake.claim_bodies[-1]


def test_claim_reports_the_real_origin_default_branch_after_upstream_push(fake, client, state_conn, paths, tmp_path):
    def git(cwd, *args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                              check=True, capture_output=True, text=True).stdout.strip()

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "a.txt").write_text("1\n")
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "base")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(tmp_path / "origin.git"))
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(tmp_path / "OpenArchive"))
    (seed / "a.txt").write_text("2\n")
    git(seed, "commit", "-q", "-am", "upstream")
    git(seed, "push", "-q", str(tmp_path / "origin.git"), "main")
    state.save_registration(state_conn, {
        "local_registration_id": "OpenArchive", "repo_path": str(tmp_path / "OpenArchive"), "tool": "stub",
        "repository_id": "jeongeundev/OpenArchive", "base_commit": BASE_COMMIT, "verification_profiles": {},
    })
    runner = Runner(client, state_conn, paths, {"stub": StubAdapter()}, CONNECTOR_ID, lambda: NOW, tmp_path / "handoff")

    runner.tick()

    assert fake.claim_bodies[-1]["registration_heads"] == {"OpenArchive": git(seed, "rev-parse", "HEAD")}
