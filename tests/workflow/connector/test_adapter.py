"""adapter — ExecutionAdapter 경계의 약속과 EchoAdapter (실제 도구를 띄우지 않는다)."""

import hashlib

from workflow.connector.adapter import AdapterOutput, EchoAdapter, make_meta
from workflow.contracts.v1 import ExecutionRequest

from .conftest import BASE_COMMIT, make_request


class Progress:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, message: str, *, runtime_ref: str | None = None) -> None:
        self.calls.append((message, runtime_ref))


def test_make_meta_fills_sha256_and_size():
    meta, data = make_meta("diff", "fix.diff", b"--- a\n+++ b\n", "text/plain")

    assert data == b"--- a\n+++ b\n"
    assert (meta.kind, meta.name, meta.content_type) == ("diff", "fix.diff", "text/plain")
    assert meta.sha256 == hashlib.sha256(data).hexdigest()
    assert meta.size == len(data)


def test_adapter_output_defaults():
    output = AdapterOutput(result=None, failed=("timeout", "초과", True))

    assert output.artifacts == [] and output.runtime_ref == ""


def test_echo_adapter_reports_runtime_ref_first_then_returns_review_result(tmp_path):
    request = make_request()
    handoff_dir = tmp_path / f"{request.task_id}.handoff"
    handoff_dir.mkdir()
    (handoff_dir / "manifest.json").write_text("{}")
    (handoff_dir / "response-after@1.json").write_text("{}")
    progress = Progress()

    output = EchoAdapter().run(request, handoff_dir, progress)

    assert progress.calls[0] == ("EchoAdapter 시작 — 실제 도구를 띄우지 않는다", f"echo:{request.execution_id}")
    assert all(ref is None for _, ref in progress.calls[1:])
    assert output.failed is None and output.runtime_ref == f"echo:{request.execution_id}"
    result = output.result
    assert (result.execution_id, result.task_id, result.outcome) == (request.execution_id, request.task_id, "ready_for_review")
    assert (result.base_commit, result.result_commit) == (BASE_COMMIT, BASE_COMMIT)  # 코드를 바꾸지 않는다
    assert result.artifact_ids == [] and result.verification.log_artifact_id == "verification_log"  # runner 가 채움
    assert result.verification.profile_id == "vp-pytest"
    kinds = [meta.kind for meta, _ in output.artifacts]
    assert kinds == ["diff", "test_log_before", "test_log_after", "verification_log", "report_output"]
    by_kind = {meta.kind: data for meta, data in output.artifacts}
    assert by_kind["test_log_before"].startswith(b"exit_code=1\n")
    assert by_kind["test_log_after"].startswith(b"exit_code=0\n")
    assert b"response-after@1.json" in by_kind["report_output"]
    for meta, data in output.artifacts:
        assert meta.sha256 == hashlib.sha256(data).hexdigest() and meta.size == len(data)


def test_echo_adapter_with_missing_handoff_dir_still_returns(tmp_path):
    output = EchoAdapter().run(make_request(), tmp_path / "없음", Progress())

    assert output.result is not None and "0개" in output.result.summary


def test_echo_adapter_refuses_diagnosis_kind(tmp_path):
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-diagnose-001", "task_id": "diagnose-daily-0920",
        "kind": "diagnosis", "agent_id": "agent-ops-demo", "task_revision": 1, "request": "조사",
        "input_artifact_ids": [], "target": {"run_id": "daily-0920-0900"},
    })

    output = EchoAdapter().run(request, tmp_path, Progress())

    assert output.result is None
    assert output.failed[0] == "unsupported_kind" and output.failed[2] is True
