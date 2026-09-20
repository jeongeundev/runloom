"""Claude Code CLI 어댑터 — `claude -p` 를 업무 worktree 에서 띄우고 결과 JSON 을 읽는다 (두 번째 로컬 도구).

프로세스를 띄우는 부분(`build_argv`·`launch`)과 `--output-format json` 의 stdout 한 덩어리를 읽는 부분
(`parse_last_message`·`classify_failure`)만 여기 있다. 변경 확인 → 결과 커밋 → 재현 테스트 → 검증 → outcome 판정은
`local_tool.LocalToolAdapter` 가 한다.

- 인자는 고정이다. 프롬프트는 stdin, 작업 디렉터리는 `cwd` 로만 준다. 승인 우회 권한 모드는 쓰지 않는다
  (ARCHITECTURE "승인·샌드박스 우회 정책을 제품 런타임에 복사하지 않는다").
- 환경은 공통 허용 목록(`child_env`)에 부모의 `CLAUDE_*`·`ANTHROPIC_*` 만 더한다 (`tool_env`) — 운영자 Mac 의
  로그인·설정 재사용. `OPENAI_*`·`WORKFLOW_*`·`DIAG_*`·연결 토큰은 상속하지 않는다. 검증 프로필은 `child_env` 로 돈다.
- 결과 JSON 의 키는 2026-09-20 `claude 2.1.278` 로 한 번 실행해 확인했다: `type=result`, `subtype`, `is_error`,
  `result`(문자열), `structured_output`(`--json-schema` 결과 객체), `usage`, `api_error_status`, `session_id` 등.
"""

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.connector import state
from workflow.connector.adapter import Progress
from workflow.connector.local_tool import (
    RESULT_SCHEMA,
    LocalToolAdapter,
    ToolResult,
    ToolRun,
    communicate_or_stop,
)

# 고정 허용 도구. 셸은 등록된 검증 명령과 같은 접두 두 개, git 은 읽기만.
ALLOWED_TOOLS = (
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(python3 -m pytest*)", "Bash(python3 -m daily_report*)", "Bash(git diff*)", "Bash(git status*)",
)

# 사용량 한도 문구. `429` 는 상태·오류 문맥에서만 본다 — usage 의 토큰 수 같은 숫자를 오인하지 않는다.
USAGE_LIMIT_PATTERN = re.compile(
    r"rate[ _-]?limit|usage[ _-]?limit|hit your (?:\w+ )?limit|(?:status|http|error)\W{0,10}429\b",
    re.IGNORECASE,
)


class ClaudeStructuredOutput(BaseModel):
    """`structured_output` — `local_tool.RESULT_SCHEMA` 를 따르지 않아도 읽을 수 있는 만큼 읽는다."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    outcome: Literal["ready_for_review", "needs_information"] = "needs_information"
    files_changed: list[str] = []
    notes: str = ""


class ClaudeResult(BaseModel):
    """`claude -p --output-format json` 의 stdout 한 덩어리 중 어댑터가 읽는 키."""

    model_config = ConfigDict(extra="ignore")

    type: str = "result"
    subtype: str = ""
    is_error: bool = False
    result: Any = None
    structured_output: dict[str, Any] | None = None
    api_error_status: int | None = None


class ClaudeAdapter(LocalToolAdapter):
    tool_name = "claude"
    raw_kinds = ("claude_jsonl", "claude_stderr")

    def __init__(
        self,
        state_conn,
        *,
        claude_bin: str = "claude",
        timeout_seconds: int = 1200,
        permission_mode: str = "acceptEdits",
        env_base: Mapping[str, str] = os.environ,
        verification_timeout: int = 300,
    ):
        super().__init__(
            state_conn, timeout_seconds=timeout_seconds, env_base=env_base, verification_timeout=verification_timeout,
        )
        self._claude_bin = claude_bin
        self._permission_mode = permission_mode

    def build_argv(self, worktree: Path, schema_json: str) -> list[str]:
        """고정 인자만. `worktree` 는 `cwd` 로만 쓰고 인자에 넣지 않는다 (`-C`·`--add-dir` 없음). 프롬프트는 stdin."""
        return [
            self._claude_bin, "-p", "--output-format", "json", "--no-session-persistence",
            "--permission-mode", self._permission_mode,
            "--allowedTools", *ALLOWED_TOOLS,
            "--json-schema", schema_json,
        ]

    def tool_env(self) -> dict[str, str]:
        """Claude 프로세스 환경: 공통 허용 목록 + 부모의 `CLAUDE_*`·`ANTHROPIC_*`. 검증 프로필에는 쓰지 않는다."""
        env = self.child_env()
        env.update({k: v for k, v in self._env_base.items() if k.startswith(("CLAUDE_", "ANTHROPIC_"))})
        return env

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        argv = self.build_argv(worktree, json.dumps(RESULT_SCHEMA, ensure_ascii=False))
        started_at = state.utc_now()
        proc = subprocess.Popen(
            argv, cwd=worktree, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.tool_env(),
        )
        progress(f"Claude 실행 시작 pid={proc.pid}", runtime_ref=f"pid:{proc.pid};start:{started_at}")
        stdout, stderr, timed_out, stopped = communicate_or_stop(proc, prompt_text.encode("utf-8"), self._timeout)
        progress(f"Claude 종료 exit={proc.returncode}{' (시간 초과)' if timed_out else ''}")
        return ToolRun(
            pid=proc.pid, started_at=started_at, exit_code=proc.returncode,
            stdout=stdout, stderr=stderr, timed_out=timed_out, stopped=stopped,
            last_message=_result_envelope(stdout),
        )

    def parse_last_message(self, raw: str | None) -> ToolResult:
        """결과 JSON 이 없거나 `is_error` 이거나 구조화 출력이 없으면 `needs_information` + 사유. 실행은 계속된다."""
        if raw is None:
            note = "결과 JSON 없음"
        else:
            try:
                envelope = ClaudeResult.model_validate_json(raw)
            except ValidationError as exc:
                note = f"결과 형식 불일치: {exc.error_count()}건"
            else:
                if envelope.is_error:
                    note = f"is_error: {_head(envelope.result)}"
                elif envelope.structured_output is None:
                    note = "구조화 출력 없음"
                else:
                    try:
                        last = ClaudeStructuredOutput.model_validate(envelope.structured_output)
                    except ValidationError as exc:
                        note = f"구조화 출력 스키마 불일치: {exc.error_count()}건"
                    else:
                        return ToolResult(last.outcome, last.summary or "(요약 없음)", None)
        return ToolResult("needs_information", f"Claude 마지막 메시지를 읽지 못함 ({note})", note)

    def classify_failure(self, run: ToolRun) -> tuple[str, str] | None:
        """사용량 한도(구독 창·API 429)면 `usage_limit`. 구조화 출력이 있는 정상 결과는 본문에 무엇이 있든 한도가 아니다."""
        envelope = _parse_envelope(run.last_message)
        if envelope is not None and not envelope.is_error and envelope.structured_output is not None:
            return None
        if envelope is not None and envelope.api_error_status == 429:
            return "usage_limit", f"Claude 사용량 한도 (api_error_status=429): {_head(envelope.result)}"
        # 사람이 읽는 오류 문장(result) → stderr → stdout 원문 순으로 본다
        sources = [_head(envelope.result)] if envelope is not None else []
        sources += [run.stderr.decode("utf-8", errors="replace"), run.stdout.decode("utf-8", errors="replace")]
        for text in sources:
            match = USAGE_LIMIT_PATTERN.search(text)
            if match is not None:
                return "usage_limit", f"Claude 사용량 한도: {_around(text, match)}"
        return None


def _result_envelope(stdout: bytes) -> str | None:
    """stdout 에서 `type=result` 객체를 찾아 JSON 문자열로. `--output-format json` 은 한 덩어리지만 줄 단위 출력도 받는다."""
    text = stdout.decode("utf-8", errors="replace").strip()
    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return json.dumps(obj, ensure_ascii=False)
    return None


def _parse_envelope(raw: str | None) -> ClaudeResult | None:
    if raw is None:
        return None
    try:
        return ClaudeResult.model_validate_json(raw)
    except ValidationError:
        return None


def _around(text: str, match: re.Match, before: int = 60, after: int = 100) -> str:
    """일치 지점 앞뒤만 — 한도 문구가 메시지에 반드시 들어간다."""
    return _head(text[max(0, match.start() - before):match.end() + after], before + after + 40)


def _head(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[:limit] + "…"
