"""실행 도구 경계 — 연결 프로그램이 실제 도구(Codex)를 띄우는 자리. 첫 구현은 Step 12 의 `CodexAdapter`.

runner 와의 약속:
- `progress(message, *, runtime_ref=...)` 의 첫 `runtime_ref` 가 `started` 이벤트가 된다. 프로세스를 띄운 직후
  한 번 넘긴다. 그 전의 메시지는 중앙에 보내지 않는다 (running 이 아니면 progress 를 받지 않는다).
- `AdapterOutput.result` 의 `artifact_ids` 와 `verification.log_artifact_id` 는 업로드 뒤에야 정해지므로
  어댑터는 비워 두고(`[]`, kind 이름 `"verification_log"`), runner 가 중앙 ID 로 채운다.
- 산출물의 `sha256`·`size` 는 마스킹 뒤 runner 가 다시 계산한다.
- 요청·인계 자료에서 셸 명령·경로를 받아 실행하지 않는다. 검증 명령은 로컬 등록값에서만 온다.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from workflow.contracts.v1 import (
    ArtifactMeta,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionRequest,
    Verification,
)

Progress = Callable[..., None]  # progress(message: str, *, runtime_ref: str | None = None)


@dataclass
class AdapterOutput:
    result: CodeChangeResult | None  # None 이면 실패
    artifacts: list[tuple[ArtifactMeta, bytes]] = field(default_factory=list)
    failed: tuple[str, str, bool] | None = None  # (code, message, process_stopped)
    runtime_ref: str = ""


class ExecutionAdapter(Protocol):
    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput: ...


def make_meta(kind: str, name: str, data: bytes, content_type: str) -> tuple[ArtifactMeta, bytes]:
    meta = ArtifactMeta(
        contract_version=1, kind=kind, name=name, content_type=content_type,
        sha256=hashlib.sha256(data).hexdigest(), size=len(data),
    )
    return meta, data


class EchoAdapter:
    """테스트·e2e 용. 인계 자료 목록을 읽고 고정된 산출물을 돌려준다. 실제 도구를 띄우지 않고 코드를 바꾸지 않는다."""

    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        runtime_ref = f"echo:{request.execution_id}"
        progress("EchoAdapter 시작 — 실제 도구를 띄우지 않는다", runtime_ref=runtime_ref)
        if not isinstance(request.target, CodeChangeTarget):
            return AdapterOutput(
                result=None, failed=("unsupported_kind", f"EchoAdapter 는 {request.kind} 를 처리하지 않는다", True),
                runtime_ref=runtime_ref,
            )
        names = sorted(p.name for p in handoff_dir.iterdir()) if handoff_dir.is_dir() else []
        progress(f"인계 자료 {len(names)}개 확인")
        listing = ("\n".join(names) + "\n").encode()
        base = request.target.base_commit
        artifacts = [
            make_meta("diff", "echo.diff", "# EchoAdapter: 변경 없음\n".encode(), "text/plain"),
            make_meta("test_log_before", "pytest-before.txt",
                      "exit_code=1\n(EchoAdapter) 재현 테스트 실패 흉내\n".encode(), "text/plain"),
            make_meta("test_log_after", "pytest-after.txt", b"exit_code=0\n(EchoAdapter)\n", "text/plain"),
            make_meta("verification_log", "verification.txt", b"exit_code=0\n(EchoAdapter)\n", "text/plain"),
            make_meta("report_output", "report.txt", b"(EchoAdapter) handoff files:\n" + listing, "text/plain"),
        ]
        result = CodeChangeResult(
            contract_version=1,
            execution_id=request.execution_id,
            task_id=request.task_id,
            outcome="ready_for_review",
            summary=f"EchoAdapter: 인계 자료 {len(names)}개를 읽었고 코드를 바꾸지 않았다.",
            base_commit=base,
            result_commit=base,
            artifact_ids=[],
            verification=Verification(
                profile_id=request.target.verification_profile_id, result_commit=base, exit_code=0,
                log_artifact_id="verification_log",
            ),
        )
        return AdapterOutput(result=result, artifacts=artifacts, failed=None, runtime_ref=runtime_ref)
