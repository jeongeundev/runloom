"""실행 도구 경계 — 연결 프로그램이 실제 도구(Codex)를 띄우는 자리. 첫 구현은 Step 12 의 `CodexAdapter`.

runner 와의 약속:
- `progress(message, *, runtime_ref=...)` 의 첫 `runtime_ref` 가 `started` 이벤트가 된다. 프로세스를 띄운 직후
  한 번 넘긴다. 그 전의 메시지는 중앙에 보내지 않는다 (running 이 아니면 progress 를 받지 않는다).
- `AdapterOutput.result` 의 `artifact_ids` 와 `verification.log_artifact_id` 는 업로드 뒤에야 정해지므로
  어댑터는 비워 두고(`[]`, kind 이름 `"verification_log"`), runner 가 중앙 ID 로 채운다.
- 산출물의 `sha256`·`size` 는 마스킹 뒤 runner 가 다시 계산한다.
- 요청·인계 자료에서 셸 명령·경로를 받아 실행하지 않는다. 검증 명령은 로컬 등록값에서만 온다.
- `AdapterOutput.result` 는 코드 수정(`CodeChangeTarget` — `bug_fix`)이면 `CodeChangeResult`, 커밋 검토
  (`CommitReviewTarget` — `code_review`)면 `CodeReviewResult`, 판단(`TriageTarget` — 결과 형태 `triage_result`)이면
  `TriageResult`, 내장이 아닌 종류(`LocalTarget`)면 `GenericResult`. 처리하지 못하는 target 은 `unsupported_kind` 로 끝낸다.
- `AdapterOutput.usage` 는 도구가 보고한 비용·토큰(ADR-0015). 모르면 None 이며 0 으로 채우지 않는다. runner 가
  `result_ready`·`failed` 에 싣는다.

인계 리포트 작업(ADR-0033)은 `ExecutionRequest` 가 아니므로 `run` 을 쓰지 않는다 — `ReportAdapter.run_report` 가 읽기 전용으로
도구를 `cwd`(결과 커밋 체크아웃 또는 빈 임시 디렉터리)에서 띄워 마지막 구조화 메시지를 객체로 돌려준다. 계약 검사·마스킹·
제출은 `report_job` 이 한다.
"""

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from workflow.contracts.v1 import (
    ArtifactMeta,
    CodeChangeResult,
    CodeChangeTarget,
    CodeReviewResult,
    ExecutionRequest,
    ExecutionUsage,
    GenericResult,
    LocalTarget,
    TriageResult,
    Verification,
)

Progress = Callable[..., None]  # progress(message: str, *, runtime_ref: str | None = None)

# claim 때 중앙에 선언하는 내장 종류 — 이 연결 프로그램의 어댑터가 처리한다 (`ClaimRequest.supported_kinds`, ADR-0014 4항).
# 사용자 정의 종류는 선언과 무관하게 `LocalTarget` 으로 돈다.
SUPPORTED_BUILTIN_KINDS = ("bug_fix", "code_review", "triage")


@dataclass
class AdapterOutput:
    result: CodeChangeResult | CodeReviewResult | GenericResult | TriageResult | None  # None 이면 실패
    artifacts: list[tuple[ArtifactMeta, bytes]] = field(default_factory=list)
    failed: tuple[str, str, bool] | None = None  # (code, message, process_stopped)
    runtime_ref: str = ""
    usage: ExecutionUsage | None = None  # 도구가 보고한 사용량. None = 모름


class ExecutionAdapter(Protocol):
    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput: ...


@dataclass(frozen=True)
class ReportOutput:
    data: dict | None  # 스키마 `{sections, diagram}` 객체 — None 이면 실패
    failed: tuple[str, str] | None = None  # (failure_code, message)


@runtime_checkable
class ReportAdapter(Protocol):
    def run_report(
        self, cwd: Path, prompt_text: str, schema: dict, progress: Progress, *, env: Mapping[str, str] | None = None,
    ) -> ReportOutput: ...


def make_meta(kind: str, name: str, data: bytes, content_type: str) -> tuple[ArtifactMeta, bytes]:
    meta = ArtifactMeta(
        contract_version=1, kind=kind, name=name, content_type=content_type,
        sha256=hashlib.sha256(data).hexdigest(), size=len(data),
    )
    return meta, data


class EchoAdapter:
    """테스트·e2e 용. 인계 자료 목록을 읽고 고정된 산출물을 돌려준다. 실제 도구를 띄우지 않고 코드를 바꾸지 않는다.
    내장이 아닌 종류(`LocalTarget`)는 인계 자료 목록을 요약에 담은 `GenericResult`(outcome 은 `kind_spec.outcomes[0]`)."""

    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        runtime_ref = f"echo:{request.execution_id}"
        progress("EchoAdapter 시작 — 실제 도구를 띄우지 않는다", runtime_ref=runtime_ref)
        if isinstance(request.target, LocalTarget):
            return self._run_generic(request, handoff_dir, progress, runtime_ref)
        if not isinstance(request.target, CodeChangeTarget):
            return AdapterOutput(
                result=None, failed=("unsupported_kind", f"EchoAdapter 는 {request.kind} 를 처리하지 않는다", True),
                runtime_ref=runtime_ref,
            )
        names = _listing(handoff_dir)
        progress(f"인계 자료 {len(names)}개 확인")
        base = request.target.base_commit
        artifacts = [
            make_meta("diff", "echo.diff", "# EchoAdapter: 변경 없음\n".encode(), "text/plain"),
            make_meta("test_log_before", "pytest-before.txt",
                      "exit_code=1\n(EchoAdapter) 재현 테스트 실패 흉내\n".encode(), "text/plain"),
            make_meta("test_log_after", "pytest-after.txt", b"exit_code=0\n(EchoAdapter)\n", "text/plain"),
            make_meta("verification_log", "verification.txt", b"exit_code=0\n(EchoAdapter)\n", "text/plain"),
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

    @staticmethod
    def _run_generic(request: ExecutionRequest, handoff_dir: Path, progress: Progress, runtime_ref: str) -> AdapterOutput:
        spec = request.kind_spec
        if spec is None:
            return AdapterOutput(
                result=None, failed=("kind_spec_missing", f"{request.kind} 요청에 kind_spec 이 없다", True),
                runtime_ref=runtime_ref,
            )
        names = _listing(handoff_dir)
        progress(f"인계 자료 {len(names)}개 확인")
        result = GenericResult(
            contract_version=1, execution_id=request.execution_id, task_id=request.task_id, kind=request.kind,
            outcome=spec.outcomes[0],
            summary=f"EchoAdapter: 인계 자료 {len(names)}개를 읽었고 파일을 만들지 않았다 — {', '.join(names) or '없음'}",
            artifact_ids=[],
        )
        return AdapterOutput(result=result, artifacts=[], failed=None, runtime_ref=runtime_ref)


    def run_report(
        self, cwd: Path, prompt_text: str, schema: dict, progress: Progress, *, env: Mapping[str, str] | None = None,
    ) -> ReportOutput:
        """요청문의 근거 ID 하나씩(원본은 `artifact:`·`commit:`, 맞춤 풀이는 `claim:`)을 붙인 고정 리포트."""
        progress("EchoAdapter 리포트 — 실제 도구를 띄우지 않는다", runtime_ref="echo:report")
        if prompt_text.startswith("# 맞춤 풀이"):
            evidence = _first_ids(prompt_text, (_CLAIM_ID,))
        else:
            evidence = _first_ids(prompt_text, (_ARTIFACT_ID, _COMMIT_ID))
        claim = {"id": "change-1", "text": "EchoAdapter: 요청문을 읽었고 파일을 바꾸지 않았다.", "why": None,
                 "evidence": evidence}
        sections = {"changes": [claim], "decisions": [], "open_questions": [], "to_confirm": [], "constraints": [],
                    "remaining_work": []}
        return ReportOutput(data={"sections": sections, "diagram": None})


_ARTIFACT_ID = re.compile(r"artifact:[A-Za-z0-9_-]+")
_COMMIT_ID = re.compile(r"commit:[0-9a-f]{7,40}\b")
_CLAIM_ID = re.compile(r"claim:[a-z][a-z0-9-]{0,23}\b")


def _first_ids(text: str, patterns: tuple[re.Pattern, ...]) -> list[str]:
    return [match.group(0) for pattern in patterns if (match := pattern.search(text))]


def _listing(handoff_dir: Path) -> list[str]:
    return sorted(p.name for p in handoff_dir.iterdir()) if handoff_dir.is_dir() else []
