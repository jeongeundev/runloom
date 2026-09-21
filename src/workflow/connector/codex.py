"""Codex CLI 어댑터 — `codex exec` 를 업무 worktree 에서 띄우고 마지막 메시지를 읽는다 (ADR-0001, ARCHITECTURE "Codex와 worktree").

프로세스를 띄우는 부분(`build_argv`·`launch`)과 `--output-last-message` 파일을 읽는 부분(`parse_last_message`)만 여기 있다.
변경 확인 → 결과 커밋 → 수정 전 재현 테스트 → 수정 후 테스트 → 깨끗한 체크아웃에서 검증·보고서 → diff → outcome 판정은
도구와 무관하므로 `local_tool.LocalToolAdapter` 가 한다. Codex 의 말을 믿지 않는 규칙도 거기 있다.
Codex 프로세스 환경은 `child_env`(= `masking.codex_env` 허용 목록)뿐이다.

사용자 정의 종류(`LocalTarget`)는 `launch_readonly` 가 같은 구조에서 `--sandbox read-only` 로 인계 디렉터리에서 띄우고
(`build_readonly_argv`), `parse_generic_message` 가 `{outcome, summary}` 만 읽는다. 인자 배열은 여전히 고정이다.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.connector import state
from workflow.connector.adapter import Progress
from workflow.connector.local_tool import (
    RESULT_SCHEMA,
    LocalToolAdapter,
    ToolResult,
    ToolRun,
    communicate_or_stop,
    outcome_note,
)

READONLY_SANDBOX = "read-only"


class CodexLastMessage(BaseModel):
    """`--output-last-message` 파일. 스키마(`local_tool.RESULT_SCHEMA`)를 따르지 않아도 읽을 수 있는 만큼 읽는다."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    outcome: Literal["ready_for_review", "needs_information"] = "needs_information"
    files_changed: list[str] = []
    notes: str = ""


class CodexGenericMessage(BaseModel):
    """사용자 정의 종류의 `--output-last-message` 파일 — `outcome` 은 등록된 식별자라 Literal 이 아니다.
    허용 목록 확인은 `parse_generic_message` 가 한다."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    outcome: str = ""


class CodexAdapter(LocalToolAdapter):
    tool_name = "codex"
    raw_kinds = ("codex_jsonl", "codex_stderr")

    def __init__(
        self,
        state_conn,
        *,
        codex_bin: str = "codex",
        timeout_seconds: int = 1200,
        sandbox: str = "workspace-write",
        approval_policy: str = "never",
        env_base: Mapping[str, str] = os.environ,
        verification_timeout: int = 300,
    ):
        super().__init__(
            state_conn, timeout_seconds=timeout_seconds, env_base=env_base, verification_timeout=verification_timeout,
        )
        self._codex_bin = codex_bin
        self._sandbox = sandbox
        self._approval_policy = approval_policy

    def build_argv(
        self, worktree: Path, schema_path: Path, last_message_path: Path, *, sandbox: str | None = None,
    ) -> list[str]:
        """고정 인자만. 프롬프트는 stdin(`-`)으로 주고, 사용자·근거 문자열을 인자에 넣지 않는다.
        `sandbox` 는 어댑터 설정값(기본 `workspace-write`) 또는 읽기 전용 실행의 `read-only` 뿐이다."""
        return [
            self._codex_bin, "exec", "--json", "-C", str(worktree),
            "--sandbox", self._sandbox if sandbox is None else sandbox,
            "-c", f'approval_policy="{self._approval_policy}"',
            "--output-schema", str(schema_path),
            "--output-last-message", str(last_message_path),
            "-",
        ]

    def build_readonly_argv(self, cwd: Path, schema_path: Path, last_message_path: Path) -> list[str]:
        """사용자 정의 종류 — `--sandbox read-only`, `-C` 는 인계 디렉터리. 그 외는 `build_argv` 와 같다."""
        return self.build_argv(cwd, schema_path, last_message_path, sandbox=READONLY_SANDBOX)

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        return self._launch(worktree, prompt_text, RESULT_SCHEMA, progress, readonly=False)

    def launch_readonly(self, cwd: Path, prompt_text: str, schema: dict, progress: Progress) -> ToolRun:
        return self._launch(cwd, prompt_text, schema, progress, readonly=True)

    def _launch(self, cwd: Path, prompt_text: str, schema: dict, progress: Progress, *, readonly: bool) -> ToolRun:
        """스키마 파일은 인계 디렉터리·worktree 밖 임시 디렉터리에 둔다 — 도구가 보는 작업 위치를 더럽히지 않는다."""
        with tempfile.TemporaryDirectory(prefix="workflow-codex-") as tmp:
            schema_path = Path(tmp) / "codex_result_schema.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2))
            last_message_path = Path(tmp) / "last_message.json"
            build = self.build_readonly_argv if readonly else self.build_argv
            argv = build(cwd, schema_path, last_message_path)
            started_at = state.utc_now()
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.child_env(),
            )
            progress(f"Codex 실행 시작 pid={proc.pid}", runtime_ref=f"pid:{proc.pid};start:{started_at}")
            stdout, stderr, timed_out, stopped = communicate_or_stop(proc, prompt_text.encode("utf-8"), self._timeout)
            progress(f"Codex 종료 exit={proc.returncode}{' (시간 초과)' if timed_out else ''}")
            last_message = last_message_path.read_text(encoding="utf-8") if last_message_path.is_file() else None
            return ToolRun(
                pid=proc.pid, started_at=started_at, exit_code=proc.returncode,
                stdout=stdout, stderr=stderr, timed_out=timed_out, stopped=stopped, last_message=last_message,
            )

    def parse_last_message(self, raw: str | None) -> ToolResult:
        """파일이 없거나 스키마와 다르면 `needs_information` 으로 두고 사유를 요약에 남긴다. 실행은 계속된다."""
        if raw is None:
            note = "파일 없음"
        else:
            try:
                last = CodexLastMessage.model_validate_json(raw)
            except ValidationError as exc:
                note = f"스키마 불일치: {exc.error_count()}건"
            else:
                return ToolResult(last.outcome, last.summary or "(요약 없음)", None)
        return ToolResult("needs_information", f"Codex 마지막 메시지를 읽지 못함 ({note})", note)

    def parse_generic_message(self, raw: str | None, outcomes: Sequence[str]) -> ToolResult:
        """같은 파일에서 `{outcome, summary}` 만 읽는다. `outcome ∉ outcomes` 면 그대로 두고 사유를 남긴다 —
        공통 흐름이 `result_invalid` 로 끝낸다."""
        if raw is None:
            note = "파일 없음"
        else:
            try:
                last = CodexGenericMessage.model_validate_json(raw)
            except ValidationError as exc:
                note = f"스키마 불일치: {exc.error_count()}건"
            else:
                if last.outcome in outcomes:
                    return ToolResult(last.outcome, last.summary or "(요약 없음)", None)
                return ToolResult(last.outcome, last.summary, outcome_note(last.outcome, outcomes))
        return ToolResult("", f"Codex 마지막 메시지를 읽지 못함 ({note})", note)
