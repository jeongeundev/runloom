"""로컬 도구 공통 흐름 — 도구(Codex·Claude)가 worktree 를 고친 뒤의 판정·보존은 도구와 무관하다.

`LocalToolAdapter.run` 이 하는 일 (ARCHITECTURE "코드 수정 결과", "Codex와 worktree"):
- 로컬 등록·검증 프로필·기준 커밋 확인 → worktree 준비 → `launch` → 원시 stdout/stderr 보존(마스킹)
- 변경 유무 확인 → 결과 커밋 → `test_log_before`(결과 커밋의 새 테스트만 기준 커밋의 깨끗한 체크아웃에 넣고 실행)
  → `test_log_after`(worktree) → `verification_log`(결과 커밋의 깨끗한 체크아웃) → diff → outcome
- 도구의 말을 믿지 않는다: 변경 없음·재현 테스트 없음이면 `needs_information`, 검증은 별도 체크아웃에서 다시 실행한다.

버그 수정(`bug_fix`, ADR-0014)은 등록된 검증 프로필 하나로 판정 재료를 남기고 기준 커밋을 고정한다: 도구를 띄우기 전
worktree HEAD 가 `base_commit` 이 아니면 `base_commit_mismatch`, 이전 시도의 미커밋 변경이 남아 있으면 `worktree_dirty`,
도구가 직접 커밋해 HEAD 가 움직였으면 `commit_mismatch` 로 실패한다. 재작업 시도는 `base_commit` = 이전 `result_commit`
이라 남아 있는 결과 브랜치(`runloom/<업무 키>`, 키 없는 옛 요청은 `task/<id>`) 위에서 그대로 이어진다. 요청의 업무 키가
계약 패턴 밖이면 git 을 부르기 전에 `invalid_work_key` 로 실패한다.

하위 클래스는 `tool_name`·`raw_kinds` 와 `launch`(프로세스를 띄우고 `ToolRun` 을 돌려준다)·`parse_last_message`
(도구의 마지막 구조화 메시지를 `ToolResult` 로), 그리고 읽기 전용 실행의 `launch_readonly`·`parse_generic_message`·
`read_structured_message`(마지막 구조화 메시지를 객체 그대로) 만 구현한다. 도구 출력에서 실패 사유(사용량 한도 등)를
읽어야 하면 `classify_failure` 를, 도구가 보고한 비용·토큰을 읽을 수 있으면 `read_usage` 를 덮어쓴다 — 둘 다 기본은
None(판정 없음·모름)이다. 사용량은 도구를 띄운 뒤의 모든 결과·실패에 싣고, 시간 초과면 싣지 않는다(ADR-0015).

내장이 아닌 종류(`LocalTarget`, ADR-0009)는 `_run_generic` 이 **읽기 전용**으로 돌린다: worktree·커밋·검증 프로필 없이
인계 디렉터리(handoff dir)에서 `launch_readonly` → 원시 로그 보존 → 인계 파일 변경 확인(`readonly_violation`) →
`{outcome, summary}` 를 읽어 `GenericResult`. `outcome ∉ kind_spec.outcomes` 면 `result_invalid` 로 실패한다.

내장 `code_review`(`CommitReviewTarget`, ADR-0014 3항)는 `_run_commit_review` 가 같은 로컬 등록 저장소에서 결과 커밋의
깨끗한 임시 체크아웃을 만들어 읽기 전용으로 돌린다 — 인계 디렉터리만 읽는 `_run_generic` 이 아니다. 착수 전 확인: 등록
(`registration_missing`) → 두 커밋이 그 저장소에 있음(`commit_missing`, 다른 기기·클론의 커밋은 전송하지 않는다) →
`base_commit` 이 `result_commit` 의 조상(`commit_mismatch`) → 인계된 수정 결과 봉투의 실행·커밋이 target 과 같음
(`source_mismatch`). 그 뒤 체크아웃에서 `launch_readonly`(스키마 `REVIEW_RESULT_SCHEMA`) → 원시 로그 → 시간 초과·
`classify_failure` → 체크아웃 HEAD·파일과 인계 파일이 그대로인지(`readonly_violation`) → `read_structured_message` 를
`CodeReviewResult` 로(`result_invalid`). `reviewed_commit` 은 도구의 말이 아니라 검토 뒤 확인한 체크아웃 HEAD 다.
체크아웃은 끝나면 지우고 원본 저장소·수정 브랜치는 건드리지 않는다.

판단(`TriageTarget` — 결과 형태 `triage_result`, ARCHITECTURE "판단 — phase 19" 러너)은 `_run_triage` 가 등록 저장소에서
중앙이 고정한 `base_commit`(기본 브랜치 끝)의 깨끗한 임시 체크아웃을 만들어 읽기 전용으로 돌린다 — 등록 폴더의 브랜치·
커밋 안 된 변경은 보이지 않는다. 착수 전 확인: 등록(`registration_missing`) → 커밋이 그 저장소에 있음(`commit_missing`,
fetch 하지 않는다). 체크아웃에 준비물 링크·복사는 없다. 그 뒤 `launch_readonly`(스키마 `TRIAGE_OUTPUT_SCHEMA`) → 원시 로그
→ 시간 초과·`classify_failure` → 체크아웃 HEAD·파일과 인계 파일이 그대로인지(`readonly_violation`) → `read_structured_message`
에 실행·업무 ID 와 체크아웃 HEAD(`inspected_commit`)를 더해 `TriageResult` 로(`result_invalid`). 후보 안의 값인지는 중앙이 본다.
결과 뒤 판단(`TriageTarget.mode = next_step`, ADR-0027)은 같은 경로에서 스키마만 `NEXT_STEP_OUTPUT_SCHEMA` 이고, 접수 판단 칸
(`proposed_kind`·`assignee`·`predecessors`)은 비워 `next_action` 을 싣는다.

검증만 다시(`verify_only_commit`, ADR-0023)는 `_run_verify_only` 가 도구를 띄우지 않고 그 커밋의 깨끗한 체크아웃에서 등록된
검증 프로필만 다시 돌린다: 등록·프로필 확인 → 인계된 이전 결과 봉투가 같은 커밋인지(`source_mismatch`) → 커밋이 등록
폴더에 있는지(없으면 origin fetch 뒤 다시, 그래도 없으면 `result_commit_missing`) → 수정 전 재현 로그·검증 로그·diff →
`CodeChangeResult`. worktree·브랜치를 만들지 않는다.

셸 명령은 로컬 등록의 검증 프로필에서만 온다. 요청·인계 자료·모델 출력에서 명령·경로를 받아 실행하지 않는다.
도구·검증 프로세스의 환경은 `child_env`(= `masking.codex_env` 허용 목록 + 이 실행 로컬 등록의 `env`)뿐이다 — 연결 토큰·API
키를 상속하지 않는다. 등록 `PYTHONPATH` 의 상대 항목은 그 프로세스의 cwd(worktree·임시 체크아웃) 기준 절대 경로로 푼다
(`resolve_pythonpath`) — 하위 프로세스가 cwd 를 바꿔도 호스트의 편집 설치를 잡지 않는다. 코드 수정은 worktree 와 검증용 깨끗한 체크아웃에 등록의 `links`(원본 폴더 설치물)를 링크로, `copies` 를 복사로 건다
(ADR-0018 결정 3). 등록 `env` 값 가림은 업로드 전 러너가 한다.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, Progress, make_meta
from workflow.connector.git_ops import GitError
from workflow.connector.masking import codex_env, mask_secrets, registered_env
from workflow.connector.prompt import (
    build_bug_fix_prompt,
    build_generic_prompt,
    build_review_prompt,
    build_triage_prompt,
)
from workflow.contracts.v1 import (
    CONTRACT_VERSION,
    TRIAGE_CRITERIA,
    TRIAGE_MODE_NEXT_STEP,
    TRIAGE_PROCEED,
    CodeChangeResult,
    CodeChangeTarget,
    CodeReviewResult,
    CommitReviewTarget,
    ExecutionRequest,
    ExecutionUsage,
    GenericResult,
    LocalTarget,
    TriageResult,
    TriageTarget,
    Verification,
    result_branch,
)

log = logging.getLogger(__name__)

NO_REPRO_TEST_LOG = "exit_code=0\n(재현 테스트 없음)\n"
KILL_GRACE_SECONDS = 5

# 도구의 마지막 메시지 스키마. Codex 는 파일(`--output-schema`)로, Claude 는 문자열(`--json-schema`)로 같은 내용을 준다.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "무엇을 어떻게 고쳤는지 한 단락"},
        "outcome": {"type": "string", "enum": ["ready_for_review", "needs_information"]},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string", "description": "남은 사항·확인이 필요한 점. 없으면 빈 문자열"},
    },
    "required": ["summary", "outcome", "files_changed", "notes"],
    "additionalProperties": False,
}

# 커밋 검토의 마지막 메시지 스키마 — `CodeReviewResult` 중 도구가 채우는 네 필드. 나머지(실행·커밋·산출물 ID)는 연결 프로그램이
# 채운다. strict 출력(Codex `--output-schema`)을 위해 모든 키가 필수이고 null 은 타입 배열로 적는다.
REVIEW_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["approved", "changes_requested", "needs_information"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["blocking", "non_blocking"]},
                    "path": {"type": ["string", "null"]},
                    "line": {"type": ["integer", "null"]},
                    "message": {"type": "string"},
                },
                "required": ["severity", "path", "line", "message"],
                "additionalProperties": False,
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["outcome", "summary", "findings", "missing_information"],
    "additionalProperties": False,
}

# 판단의 담당 후보 객체·근거 — 접수 판단과 결과 뒤 판단 스키마가 같이 쓴다.
_TRIAGE_ASSIGNEE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["member", "agent"]},
        "id": {"type": "string"},
    },
    "required": ["type", "id"],
    "additionalProperties": False,
}
_TRIAGE_REASONS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "criterion": {"type": "string", "enum": list(TRIAGE_CRITERIA)},
            "note": {"type": "string"},
        },
        "required": ["criterion", "note"],
        "additionalProperties": False,
    },
}

# 판단의 마지막 메시지 스키마 — `TriageResult` 중 모델이 채우는 칸만. 실행·업무 ID·`inspected_commit`(체크아웃 HEAD)·계약
# 버전은 연결 프로그램이 채운다. 개수·길이·확신도 범위·proceed 별 규칙은 `TriageResult` 검증이 본다(어긋나면 `result_invalid`).
TRIAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "proceed": {"type": "string", "enum": list(TRIAGE_PROCEED)},
        "confidence": {"type": "number", "description": "0~1"},
        "proposed_kind": {"type": ["string", "null"], "description": "후보 종류 중 하나"},
        "assignee": {"anyOf": [_TRIAGE_ASSIGNEE_SCHEMA, {"type": "null"}]},
        "predecessors": {"type": "array", "items": {"type": "string"}, "description": "선행 후보의 업무 키, 최대 5개"},
        "reasons": _TRIAGE_REASONS_SCHEMA,
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["proceed", "confidence", "proposed_kind", "assignee", "predecessors", "reasons",
                 "missing_information"],
    "additionalProperties": False,
}


def _next_action_schema(action_type: str, properties: dict) -> dict:
    """`next_action` 한 모양 — `type` 은 값 하나의 enum, 칸은 모두 required(Codex strict 호환)."""
    properties = {"type": {"type": "string", "enum": [action_type]}, **properties}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


# 결과 뒤 판단(`TriageTarget.mode = next_step`)의 마지막 메시지 스키마 — 모델이 채우는 칸만. `next_action` 은 네 모양의
# `anyOf`(Claude `--json-schema`·Codex `--output-schema` 둘 다 받는다). `proposed_kind`·`assignee`·`predecessors` 는
# 연결 프로그램이 비우고, 개수·길이·확신도 범위·proceed 별 규칙은 `TriageResult` 검증이 본다.
NEXT_STEP_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "proceed": TRIAGE_OUTPUT_SCHEMA["properties"]["proceed"],
        "confidence": TRIAGE_OUTPUT_SCHEMA["properties"]["confidence"],
        "next_action": {
            "anyOf": [
                _next_action_schema("stage", {
                    "kind": {"type": "string", "description": "다음 단계 후보 종류 중 하나"},
                    "assignee": _TRIAGE_ASSIGNEE_SCHEMA,
                    "rework": {"type": "boolean"},
                }),
                _next_action_schema("new_work", {
                    "kind": {"type": "string", "description": "새 업무 후보 종류 중 하나"},
                    "title": {"type": "string"},
                    "assignee": _TRIAGE_ASSIGNEE_SCHEMA,
                }),
                _next_action_schema("internal_request", {
                    "system_id": {"type": "string"},
                    "request_kind": {"type": "string"},
                    "recipient_member_id": {"type": "string"},
                    "purpose": {"type": "string"},
                }),
                _next_action_schema("human", {"question": {"type": "string"}}),
            ],
        },
        "reasons": _TRIAGE_REASONS_SCHEMA,
        "missing_information": TRIAGE_OUTPUT_SCHEMA["properties"]["missing_information"],
    },
    "required": ["proceed", "confidence", "next_action", "reasons", "missing_information"],
    "additionalProperties": False,
}

def resolve_pythonpath(env: Mapping[str, str], cwd: Path) -> dict[str, str]:
    """`PYTHONPATH` 의 상대 항목을 `cwd` 기준 절대 경로로 바꾼 사본. 빈 항목·`~` 로 시작하는 항목·절대 경로는 그대로,
    심볼릭 링크는 따라가지 않는다(파일 시스템을 읽지 않는다). 다른 변수는 건드리지 않는다."""
    resolved = dict(env)
    if "PYTHONPATH" in resolved:
        resolved["PYTHONPATH"] = os.pathsep.join(
            item if not item or item.startswith("~") or os.path.isabs(item)
            else os.path.normpath(os.path.join(cwd, item))
            for item in resolved["PYTHONPATH"].split(os.pathsep)
        )
    return resolved


def outcome_note(outcome: str, outcomes: Sequence[str]) -> str:
    """`parse_generic_message` 가 허용 목록 밖 outcome 에 남기는 사유 — 실패 메시지(`result_invalid`)가 된다."""
    return f"허용되지 않은 outcome {outcome!r} — 허용: {', '.join(outcomes)}"


def generic_result_schema(outcomes: Sequence[str]) -> dict:
    """사용자 정의 종류의 마지막 메시지 스키마 — `outcome` 은 `kind_spec.outcomes` 중 하나, `summary` 는 문자열."""
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": list(outcomes)},
            "summary": {"type": "string"},
        },
        "required": ["outcome", "summary"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ToolRun:
    """도구 프로세스 한 번의 원시 결과. 어댑터가 커밋·검증·판정을 하기 전의 재료다."""

    pid: int
    started_at: str
    exit_code: int | None
    stdout: bytes  # 원문 (마스킹 전)
    stderr: bytes
    timed_out: bool
    stopped: bool
    last_message: str | None  # 도구가 낸 마지막 구조화 메시지 원문(JSON 문자열) 또는 None


@dataclass(frozen=True)
class ToolResult:
    """`parse_last_message`·`parse_generic_message` 의 결과 — 도구의 주장이다. 코드 수정은 공통 흐름이 변경·재현 테스트
    유무로 다시 판정하고(outcome 은 `ready_for_review`|`needs_information`), 사용자 정의 종류는 `outcome ∈ kind_spec.outcomes`
    를 확인한다."""

    outcome: str
    summary: str
    parse_note: str | None  # 마지막 메시지를 못 읽었거나 outcome 이 허용 목록 밖일 때 사유


class LocalToolAdapter:
    """로컬 도구 공통 흐름. 하위 클래스는 `tool_name`·`raw_kinds`·`launch`·`parse_last_message` 와 읽기 전용 실행의
    `launch_readonly`·`parse_generic_message`·`read_structured_message` 만 구현한다."""

    tool_name: str  # "codex" | "claude" — 실패 코드 `{tool}_unavailable` 와 로그 이름에 쓴다
    raw_kinds: tuple[str, str]  # ("codex_jsonl", "codex_stderr") 처럼 원시 stdout/stderr 산출물 kind

    def __init__(
        self,
        state_conn,
        *,
        timeout_seconds: int = 1200,
        env_base: Mapping[str, str] = os.environ,
        verification_timeout: int = 300,
    ):
        self._conn = state_conn
        self._timeout = timeout_seconds
        self._env_base = env_base
        self._verification_timeout = verification_timeout
        self._registered_env: dict[str, str] = {}  # 지금 실행 중인 요청의 로컬 등록 env — `run` 이 매번 정한다

    # --- 하위 클래스가 구현 ---------------------------------------------------------------------

    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun:
        """도구 프로세스를 worktree 에서 띄우고 끝날 때까지 기다린다. 시작 직후 `progress(..., runtime_ref=...)` 한 번.
        실행 파일이 없으면 OSError 를 그대로 던진다 (프로세스가 시작되지 않았다)."""
        raise NotImplementedError

    def parse_last_message(self, raw: str | None) -> ToolResult:
        raise NotImplementedError

    def launch_readonly(self, cwd: Path, prompt_text: str, schema: dict, progress: Progress) -> ToolRun:
        """읽기 전용으로 도구를 `cwd`(인계 디렉터리)에서 띄운다. `schema` 는 마지막 메시지의 JSON Schema
        (`generic_result_schema`). 시작 직후 `progress(..., runtime_ref=...)` 한 번. 실행 파일이 없으면 OSError."""
        raise NotImplementedError

    def parse_generic_message(self, raw: str | None, outcomes: Sequence[str]) -> ToolResult:
        """마지막 메시지에서 `{outcome, summary}` 를 읽는다. `outcome ∉ outcomes` 면 `parse_note` 에 사유를 적고 outcome 은
        그대로 둔다 — 공통 흐름이 `result_invalid` 로 실패시킨다. 못 읽으면 outcome 은 빈 문자열."""
        raise NotImplementedError

    def read_structured_message(self, raw: str | None) -> tuple[dict | None, str | None]:
        """마지막 구조화 메시지를 객체 그대로 꺼낸다 (커밋 검토). 못 꺼내면 `(None, 사유)`. 형식 검사는 공통 흐름이
        `CodeReviewResult` 로 한다."""
        raise NotImplementedError

    def classify_failure(self, run: ToolRun) -> tuple[str, str] | None:
        """도구 출력에서 실패 사유를 읽는 훅 — `(code, message)` 면 공통 흐름이 그 자리에서 `failed` 로 끝낸다
        (`process_stopped` 는 `run.stopped`). 시간 초과는 이 훅보다 먼저 판정한다. 기본은 None (Codex)."""
        return None

    def read_usage(self, run: ToolRun) -> ExecutionUsage | None:
        """도구 출력에서 비용·토큰을 읽는 훅. 모르는 칸은 null, 전부 모르면 None. 기본은 None."""
        return None

    # --- 공통 ------------------------------------------------------------------------------------

    def child_env(self, cwd: Path | None = None) -> dict[str, str]:
        """도구·검증 프로세스에 넘기는 환경. 허용 목록만 남기고 연결 토큰·API 키·중앙 설정을 제거한 뒤, 지금 실행의
        로컬 등록 env 를 더한다 — `registered_env` 가 허용 목록·예약 이름을 빼므로 허용 목록 값을 덮지 못한다.
        `cwd` 를 주면 등록 `PYTHONPATH` 의 상대 항목을 그 기준으로 푼다."""
        env = {**codex_env(self._env_base), **registered_env(self._registered_env)}
        return env if cwd is None else resolve_pythonpath(env, cwd)

    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        target = request.target
        registration = (
            state.get_registration(self._conn, target.local_registration_id)
            if isinstance(target, (LocalTarget, CommitReviewTarget, CodeChangeTarget, TriageTarget)) else None
        )
        self._registered_env = registration["env"] if registration else {}
        if isinstance(target, LocalTarget):
            return self._run_generic(request, handoff_dir, progress)
        if isinstance(target, CommitReviewTarget):
            return self._run_commit_review(request, handoff_dir, progress)
        if isinstance(target, TriageTarget):  # 계약이 결과 형태 `triage_result` 에만 이 target 을 허용한다
            return self._run_triage(request, handoff_dir, progress)
        if not isinstance(target, CodeChangeTarget):
            return _failed("unsupported_kind", f"{type(self).__name__} 는 {request.kind} 를 처리하지 않는다")
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        profiles: dict[str, list[str]] = registration["verification_profiles"]
        profile = profiles.get(target.verification_profile_id)
        if profile is None:
            return _failed(
                "verification_profile_missing",
                f"검증 프로필 {target.verification_profile_id} 이 등록 {target.local_registration_id} 에 없다",
            )
        repo = Path(registration["repo_path"])
        prepared = Prepared(registration["links"], registration.get("copies", []))
        if request.verify_only_commit is not None:
            return self._run_verify_only(request, handoff_dir, progress, repo, profile, prepared)
        branch = {"work_key": request.work_key, "branch_seq": request.branch_seq}
        try:
            worktree = git_ops.ensure_worktree(repo, request.task_id, target.base_commit, **branch)
        except ValueError as exc:  # 계약 밖 업무 키 — 브랜치 이름으로 쓰지 않는다
            return _failed("invalid_work_key", str(exc))
        except GitError as exc:
            return _failed("base_commit_missing", str(exc))
        prepared.apply(repo, worktree)  # 이어 쓰는 worktree 는 이미 있는 링크·복사본을 건너뛴다
        head = git_ops.head_sha(worktree)
        if head != target.base_commit:
            return _failed(
                "base_commit_mismatch",
                f"worktree {worktree.name} 의 HEAD {head[:12]} 가 요청 base_commit {target.base_commit[:12]} 이 아니다",
            )
        if git_ops.is_dirty(worktree):
            return _failed("worktree_dirty", f"worktree {worktree.name} 에 이전 시도의 미커밋 변경이 남아 있다")

        try:
            run = self.launch(worktree, build_bug_fix_prompt(request, handoff_dir, worktree), progress)
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)
        if run.timed_out:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts,
                failed=("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.", run.stopped),
                runtime_ref=runtime_ref,
            )
        usage = self.read_usage(run)
        failure = self.classify_failure(run)
        if failure is not None:
            code, message = failure
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
                usage=usage,
            )

        base_commit = target.base_commit
        if (head := git_ops.head_sha(worktree)) != base_commit:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, runtime_ref=runtime_ref, usage=usage,
                failed=("commit_mismatch",
                        f"{self.tool_name} 가 직접 커밋해 HEAD 가 {head[:12]} 로 움직였다 (base_commit {base_commit[:12]})",
                        run.stopped),
            )

        parsed = self.parse_last_message(run.last_message)
        summary = parsed.summary
        if not git_ops.is_dirty(worktree):
            result = self._result(request, "needs_information", f"변경 없음: {summary}", None, None)
            return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref, usage=usage)

        result_commit = git_ops.commit_all(
            worktree, f"fix({request.task_id}): {_first_line(summary, self.tool_name)[:60]}"
        )
        progress(f"결과 커밋 {result_commit[:12]} ({result_branch(request.task_id, **branch)})")

        test_files = git_ops.changed_test_files(repo, base_commit, result_commit)
        before = self._test_before(repo, worktree, base_commit, test_files, profile, prepared)
        progress(f"수정 전 재현 테스트 {before.splitlines()[0]} (테스트 파일 {len(test_files)}개)")
        after_code, after_out, after_err = self._run_argv(profile, worktree)
        progress(f"수정 후 테스트 exit_code={after_code}")

        verify_code, verify_out, verify_err = _in_clean_checkout(
            repo, result_commit, lambda dest: self._run_argv(profile, dest), prepared,
        )
        progress(f"검증 프로필 {target.verification_profile_id} exit_code={verify_code} @ {result_commit[:12]}")
        diff = git_ops.diff_text(repo, base_commit, result_commit)

        if not test_files:
            outcome, summary = "needs_information", f"재현 테스트 없음: {summary}"
        else:
            outcome = parsed.outcome
        verification = Verification(
            profile_id=target.verification_profile_id, result_commit=result_commit, exit_code=verify_code,
            log_artifact_id="verification_log",
        )
        result = self._result(request, outcome, summary, result_commit, verification)
        artifacts = [
            make_meta("diff", "fix.diff", diff.encode(), "text/x-diff"),
            make_meta("test_log_before", "pytest-before.txt", before.encode(), "text/plain"),
            make_meta("test_log_after", "pytest-after.txt", _log_text(after_code, after_out, after_err).encode(),
                      "text/plain"),
            make_meta("verification_log", "verification.txt",
                      _log_text(verify_code, verify_out, verify_err).encode(), "text/plain"),
            *raw_artifacts,
        ]
        return AdapterOutput(result=result, artifacts=artifacts, runtime_ref=runtime_ref, usage=usage)

    # --- 검증만 다시 — 도구 없이 결과 커밋의 깨끗한 체크아웃 --------------------------------------------------

    def _run_verify_only(
        self, request: ExecutionRequest, handoff_dir: Path, progress: Progress, repo: Path, profile: list[str],
        prepared: "Prepared",
    ) -> AdapterOutput:
        """`verify_only_commit` 실행. 이전 결과 봉투 확인(`source_mismatch`) → 커밋 확인(`result_commit_missing`) →
        결과 커밋의 깨끗한 체크아웃에서 수정 전 재현 로그와 검증 프로필 한 번 → `CodeChangeResult`. 도구를 띄우지 않는다."""
        target = request.target
        commit = request.verify_only_commit
        source = _verify_only_source(handoff_dir, commit, target.base_commit)
        if source is None:
            return _failed(
                "source_mismatch",
                f"인계 자료에 결과 커밋 {commit[:12]}(기준 {target.base_commit[:12]}) 의 수정 결과 봉투가 없다",
            )
        if not _has_commits_after_fetch(repo, (commit, target.base_commit)):
            return _failed(
                "result_commit_missing",
                f"결과 커밋 {commit[:12]} 또는 기준 커밋 {target.base_commit[:12]} 이 등록 "
                f"{target.local_registration_id} 의 저장소에 없다 (origin fetch 뒤에도)",
            )
        runtime_ref = f"verify-only:{request.execution_id}"
        progress(f"검증만 다시 @ {commit[:12]}", runtime_ref=runtime_ref)

        test_files = git_ops.changed_test_files(repo, target.base_commit, commit)

        def inside(dest: Path) -> tuple[str, tuple[int, str, str]]:
            before = self._test_before(repo, dest, target.base_commit, test_files, profile, prepared)
            return before, self._run_argv(profile, dest)

        try:
            before, (verify_code, verify_out, verify_err) = _in_clean_checkout(repo, commit, inside, prepared)
        except GitError as exc:
            return _failed("checkout_failed", f"결과 커밋 {commit[:12]} 체크아웃 실패: {exc}")
        progress(f"수정 전 재현 테스트 {before.splitlines()[0]} (테스트 파일 {len(test_files)}개)")
        progress(f"검증 프로필 {target.verification_profile_id} exit_code={verify_code} @ {commit[:12]}")
        diff = git_ops.diff_text(repo, target.base_commit, commit)

        outcome = source.outcome if test_files else "needs_information"
        verification = Verification(
            profile_id=target.verification_profile_id, result_commit=commit, exit_code=verify_code,
            log_artifact_id="verification_log",
        )
        result = self._result(request, outcome, f"검증만 다시 — {source.summary}", commit, verification)
        verify_log = _log_text(verify_code, verify_out, verify_err).encode()
        artifacts = [
            make_meta("diff", "fix.diff", diff.encode(), "text/x-diff"),
            make_meta("test_log_before", "pytest-before.txt", before.encode(), "text/plain"),
            make_meta("test_log_after", "pytest-after.txt", verify_log, "text/plain"),
            make_meta("verification_log", "verification.txt", verify_log, "text/plain"),
        ]
        return AdapterOutput(result=result, artifacts=artifacts, runtime_ref=runtime_ref)

    # --- 사용자 정의 종류 — 읽기 전용 -----------------------------------------------------------------

    def _run_generic(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        """`LocalTarget` 실행. 등록 확인 → 인계 디렉터리에서 `launch_readonly` → 원시 로그 → 시간 초과·`classify_failure`
        → 인계 파일 변경 확인 → `{outcome, summary}` → `GenericResult`. worktree·커밋·검증 프로필은 없다."""
        target = request.target
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        spec = request.kind_spec
        if spec is None:
            return _failed("kind_spec_missing", f"{request.kind} 요청에 kind_spec 이 없다")
        handoff_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot(handoff_dir)

        try:
            run = self.launch_readonly(
                handoff_dir, build_generic_prompt(request, handoff_dir), generic_result_schema(spec.outcomes), progress,
            )
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)

        usage = None if run.timed_out else self.read_usage(run)

        def failed(code: str, message: str) -> AdapterOutput:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
                usage=usage,
            )

        if run.timed_out:
            return failed("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.")
        failure = self.classify_failure(run)
        if failure is not None:
            return failed(*failure)
        changed = _changed_files(before, _snapshot(handoff_dir))
        if changed:
            return failed("readonly_violation", f"읽기 전용 실행이 인계 디렉터리의 파일을 바꿨다: {', '.join(changed)}")

        parsed = self.parse_generic_message(run.last_message, spec.outcomes)
        if parsed.outcome not in spec.outcomes:
            return failed("result_invalid", parsed.parse_note or f"허용되지 않은 outcome {parsed.outcome!r}")
        result = GenericResult(
            contract_version=CONTRACT_VERSION, execution_id=request.execution_id, task_id=request.task_id,
            kind=request.kind, outcome=parsed.outcome, summary=parsed.summary, artifact_ids=[],
        )
        return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref, usage=usage)

    # --- 내장 `code_review` — 결과 커밋의 읽기 전용 체크아웃 ---------------------------------------------

    def _run_commit_review(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        """`CommitReviewTarget` 실행. 착수 전 확인 → 결과 커밋의 깨끗한 체크아웃에서 `launch_readonly` → 원시 로그 →
        시간 초과·`classify_failure` → 체크아웃·인계 파일 불변 확인 → `CodeReviewResult`. 체크아웃은 반드시 지운다."""
        target = request.target
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        repo = Path(registration["repo_path"])
        for label, commit in (("result_commit", target.result_commit), ("base_commit", target.base_commit)):
            if not git_ops.has_commit(repo, commit):
                return _failed(
                    "commit_missing",
                    f"{label} {commit[:12]} 이 등록 {target.local_registration_id} 의 저장소에 없다 — "
                    "다른 기기·저장소의 커밋은 전송하지 않는다",
                )
        if not git_ops.is_ancestor(repo, target.base_commit, target.result_commit):
            return _failed(
                "commit_mismatch",
                f"base_commit {target.base_commit[:12]} 이 result_commit {target.result_commit[:12]} 의 조상이 아니다",
            )
        source = _source_result(handoff_dir, target.source_execution_id)
        if source is None:
            return _failed("source_mismatch", f"인계 자료에 수정 실행 {target.source_execution_id} 의 결과 봉투가 없다")
        if (source.base_commit, source.result_commit) != (target.base_commit, target.result_commit):
            return _failed(
                "source_mismatch",
                f"수정 실행 {target.source_execution_id} 의 커밋 {source.base_commit[:12]}..{(source.result_commit or '없음')[:12]}"
                f" 이 검토 대상 {target.base_commit[:12]}..{target.result_commit[:12]} 과 다르다",
            )
        diff = git_ops.diff_text(repo, target.base_commit, target.result_commit)
        handoff_before = _snapshot(handoff_dir)

        def review(checkout: Path) -> tuple[ToolRun, str, bool]:
            prompt_text = build_review_prompt(request, handoff_dir, checkout, diff, source)
            run = self.launch_readonly(checkout, prompt_text, REVIEW_RESULT_SCHEMA, progress)
            return run, git_ops.head_sha(checkout), git_ops.is_dirty(checkout)

        try:
            run, head, dirty = _in_clean_checkout(repo, target.result_commit, review)
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        except GitError as exc:
            return _failed("checkout_failed", f"결과 커밋 {target.result_commit[:12]} 체크아웃 실패: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)

        usage = None if run.timed_out else self.read_usage(run)

        def failed(code: str, message: str) -> AdapterOutput:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
                usage=usage,
            )

        if run.timed_out:
            return failed("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.")
        failure = self.classify_failure(run)
        if failure is not None:
            return failed(*failure)
        if head != target.result_commit:
            return failed("readonly_violation",
                          f"검토 체크아웃의 HEAD 가 {head[:12]} 로 움직였다 (result_commit {target.result_commit[:12]})")
        if dirty:
            return failed("readonly_violation", "읽기 전용 검토가 검토 체크아웃의 파일을 바꿨다")
        changed = _changed_files(handoff_before, _snapshot(handoff_dir))
        if changed:
            return failed("readonly_violation", f"읽기 전용 검토가 인계 디렉터리의 파일을 바꿨다: {', '.join(changed)}")

        data, note = self.read_structured_message(run.last_message)
        if data is None:
            return failed("result_invalid", f"{self.tool_name} 검토 결과를 읽지 못함 ({note})")
        try:
            result = CodeReviewResult.model_validate({
                "contract_version": CONTRACT_VERSION, "execution_id": request.execution_id, "task_id": request.task_id,
                "source_execution_id": target.source_execution_id, "reviewed_commit": head,
                **{key: data.get(key) for key in REVIEW_RESULT_SCHEMA["required"]}, "artifact_ids": [],
            })
        except ValidationError as exc:
            return failed("result_invalid", f"검토 결과 형식 오류: {_first_error(exc)}")
        return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref, usage=usage)

    # --- 판단 — 기본 브랜치 끝의 읽기 전용 체크아웃 ----------------------------------------------------

    def _run_triage(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput:
        """`TriageTarget` 실행. 착수 전 확인 → `base_commit` 의 깨끗한 체크아웃에서 `launch_readonly` → 원시 로그 →
        시간 초과·`classify_failure` → 체크아웃·인계 파일 불변 확인 → `TriageResult`. 체크아웃은 반드시 지운다.
        `mode = next_step`(결과 뒤 판단)이면 스키마만 `NEXT_STEP_OUTPUT_SCHEMA` 이고 접수 판단 칸은 비운다."""
        target = request.target
        registration = state.get_registration(self._conn, target.local_registration_id)
        if registration is None:
            return _failed("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
        repo = Path(registration["repo_path"])
        if not git_ops.has_commit(repo, target.base_commit):
            return _failed(
                "commit_missing",
                f"base_commit {target.base_commit[:12]} 이 등록 {target.local_registration_id} 의 저장소에 없다",
            )
        handoff_before = _snapshot(handoff_dir) if handoff_dir.is_dir() else {}
        next_step = target.mode == TRIAGE_MODE_NEXT_STEP
        schema = NEXT_STEP_OUTPUT_SCHEMA if next_step else TRIAGE_OUTPUT_SCHEMA

        def triage(checkout: Path) -> tuple[ToolRun, str, bool]:
            run = self.launch_readonly(checkout, build_triage_prompt(request, checkout), schema, progress)
            return run, git_ops.head_sha(checkout), git_ops.is_dirty(checkout)

        try:
            run, head, dirty = _in_clean_checkout(repo, target.base_commit, triage)
        except OSError as exc:  # 실행 파일 없음 등 — 프로세스가 시작되지 않았다
            return _failed(f"{self.tool_name}_unavailable", f"{self.tool_name} 을 시작하지 못함: {exc}")
        except GitError as exc:
            return _failed("checkout_failed", f"base_commit {target.base_commit[:12]} 체크아웃 실패: {exc}")
        runtime_ref = f"pid:{run.pid};start:{run.started_at}"
        raw_artifacts = self._raw_artifacts(run)

        usage = None if run.timed_out else self.read_usage(run)

        def failed(code: str, message: str) -> AdapterOutput:
            return AdapterOutput(
                result=None, artifacts=raw_artifacts, failed=(code, message, run.stopped), runtime_ref=runtime_ref,
                usage=usage,
            )

        if run.timed_out:
            return failed("timeout", f"{self.tool_name} 실행이 {self._timeout}초를 초과해 종료했습니다.")
        failure = self.classify_failure(run)
        if failure is not None:
            return failed(*failure)
        if head != target.base_commit:
            return failed("readonly_violation",
                          f"판단 체크아웃의 HEAD 가 {head[:12]} 로 움직였다 (base_commit {target.base_commit[:12]})")
        if dirty:
            return failed("readonly_violation", "읽기 전용 판단이 판단 체크아웃의 파일을 바꿨다")
        changed = _changed_files(handoff_before, _snapshot(handoff_dir) if handoff_dir.is_dir() else {})
        if changed:
            return failed("readonly_violation", f"읽기 전용 판단이 인계 디렉터리의 파일을 바꿨다: {', '.join(changed)}")

        data, note = self.read_structured_message(run.last_message)
        if data is None:
            return failed("result_invalid", f"{self.tool_name} 판단 결과를 읽지 못함 ({note})")
        try:
            result = TriageResult.model_validate({
                **{key: data.get(key) for key in schema["required"]},
                **({"proposed_kind": None, "assignee": None, "predecessors": []} if next_step else {}),
                "contract_version": CONTRACT_VERSION, "execution_id": request.execution_id,
                "task_id": request.task_id, "inspected_commit": head,
            })
        except ValidationError as exc:
            return failed("result_invalid", f"판단 결과 형식 오류: {_first_error(exc)}")
        return AdapterOutput(result=result, artifacts=raw_artifacts, runtime_ref=runtime_ref, usage=usage)

    def _raw_artifacts(self, run: ToolRun) -> list[tuple]:
        """도구의 원시 stdout/stderr — `raw_kinds` 의 kind 로, 마스킹해서 보존한다."""
        stdout_kind, stderr_kind = self.raw_kinds
        return [
            make_meta(stdout_kind, f"{self.tool_name}.jsonl", _masked(run.stdout), "application/x-ndjson"),
            make_meta(stderr_kind, f"{self.tool_name}-stderr.txt", _masked(run.stderr), "text/plain"),
        ]

    # --- 검증 프로필 ----------------------------------------------------------------------------

    def _run_argv(self, argv: list[str], cwd: Path) -> tuple[int, str, str]:
        """등록된 인자 배열을 그대로 실행. (exit_code, stdout, stderr). 시간 초과는 124, 실행 불가는 127."""
        try:
            proc = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, errors="replace",
                timeout=self._verification_timeout, env=self.child_env(cwd),
            )
        except subprocess.TimeoutExpired as exc:
            return 124, _text(exc.stdout), f"(검증 시간 초과 {self._verification_timeout}초)\n{_text(exc.stderr)}"
        except OSError as exc:
            return 127, "", f"(실행 불가) {exc}\n"
        return proc.returncode, proc.stdout, proc.stderr

    def _test_before(
        self, repo: Path, worktree: Path, base_commit: str, test_files: list[str], profile: list[str],
        prepared: "Prepared",
    ) -> str:
        if not test_files:
            return NO_REPRO_TEST_LOG

        def inside(dest: Path) -> str:
            # git 이 보고한 저장소 상대 경로 — 결과 커밋의 테스트만 기준 코드 위에 놓는다. `worktree` 는 결과 커밋의
            # 파일이 있는 곳(업무 worktree 또는 검증만 다시의 결과 커밋 체크아웃)이다.
            for rel in test_files:
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(worktree / rel, dest / rel)
            return _log_text(*self._run_argv(profile, dest))

        return _in_clean_checkout(repo, base_commit, inside, prepared)

    @staticmethod
    def _result(
        request: ExecutionRequest, outcome: str, summary: str, result_commit: str | None,
        verification: Verification | None,
    ) -> CodeChangeResult:
        return CodeChangeResult(
            contract_version=CONTRACT_VERSION, execution_id=request.execution_id, task_id=request.task_id,
            outcome=outcome, summary=summary, base_commit=request.target.base_commit,
            result_commit=result_commit, artifact_ids=[], verification=verification,
        )


def usage_from(cost_usd: object, input_tokens: object, output_tokens: object) -> ExecutionUsage | None:
    """도구가 보고한 값에서 형식이 맞는 칸만 — 비용은 0 이상의 수, 토큰은 0 이상의 정수(bool 제외). 나머지는 null(모름),
    전부 모르면 None. 모르는 값을 0 으로 채우지 않는다."""
    def number(value: object, types: tuple[type, ...]) -> float | int | None:
        return value if isinstance(value, types) and not isinstance(value, bool) and value >= 0 else None

    usage = ExecutionUsage(
        cost_usd=number(cost_usd, (int, float)), input_tokens=number(input_tokens, (int,)),
        output_tokens=number(output_tokens, (int,)),
    )
    return None if usage == ExecutionUsage() else usage


def communicate_or_stop(
    proc: subprocess.Popen, input_bytes: bytes, timeout: int,
) -> tuple[bytes, bytes, bool, bool]:
    """stdin 을 쓰고 끝까지 기다린다. 시간 초과면 terminate → `KILL_GRACE_SECONDS` → kill.
    `(stdout, stderr, timed_out, stopped)` — stopped 는 프로세스 종료를 실제로 확인했는지다."""
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                log.error("pid=%s 를 종료하지 못했다", proc.pid)
        stdout, stderr = proc.communicate()
    return stdout, stderr, timed_out, proc.poll() is not None


def _failed(code: str, message: str) -> AdapterOutput:
    """도구를 띄우기 전의 실패 — 프로세스가 없으므로 process_stopped=True."""
    return AdapterOutput(result=None, failed=(code, message, True))


def _snapshot(root: Path) -> dict[str, str]:
    """인계 디렉터리 안 파일의 상대 경로 → sha256. 읽기 전용 실행 전후를 비교하는 데 쓴다."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _source_result(handoff_dir: Path, execution_id: str) -> CodeChangeResult | None:
    """인계 디렉터리의 JSON 중 `execution_id` 의 `CodeChangeResult` (이름 순 첫 번째). 못 읽는 파일은 건너뛴다."""
    for path in sorted(handoff_dir.glob("*.json")) if handoff_dir.is_dir() else []:
        try:
            result = CodeChangeResult.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError):
            continue
        if result.execution_id == execution_id:
            return result
    return None


def _verify_only_source(handoff_dir: Path, commit: str, base_commit: str) -> CodeChangeResult | None:
    """인계 디렉터리의 수정 결과 봉투 중 `result_commit == commit` 이고 `base_commit` 이 같은 것 (이름 순 첫 번째)."""
    for path in sorted(handoff_dir.glob("*.json")) if handoff_dir.is_dir() else []:
        try:
            result = CodeChangeResult.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError):
            continue
        if (result.result_commit, result.base_commit) == (commit, base_commit):
            return result
    return None


def _has_commits_after_fetch(repo: Path, commits: Sequence[str]) -> bool:
    """커밋이 모두 등록 폴더에 있는가. 없으면 origin 을 한 번 fetch 하고 다시 본다 — fetch 실패는 없음과 같다."""
    if all(git_ops.has_commit(repo, commit) for commit in commits):
        return True
    try:
        git_ops.fetch_origin(repo)
    except GitError:
        log.info("검증만 다시: %s 의 origin fetch 실패", repo.name)
        return False
    return all(git_ops.has_commit(repo, commit) for commit in commits)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """추가·삭제·수정된 파일 이름 (정렬)."""
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def _masked(data: bytes) -> bytes:
    text, _ = mask_secrets(data.decode("utf-8", errors="replace"))
    return text.encode("utf-8")


def _log_text(exit_code: int, stdout: str, stderr: str) -> str:
    return f"exit_code={exit_code}\n{stdout}{stderr}"


def _first_line(text: str, tool_name: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else f"{tool_name} 수정"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


@dataclass(frozen=True)
class Prepared:
    """등록의 작업 복사본 준비물(ADR-0018 결정 3) — 원본 폴더 설치물의 링크와 복사본."""

    links: Sequence[str] = ()
    copies: Sequence[str] = ()

    def apply(self, repo: Path, checkout: Path) -> None:
        git_ops.link_prepared_paths(repo, checkout, list(self.links))
        git_ops.copy_prepared_paths(repo, checkout, list(self.copies))


def _in_clean_checkout[T](repo: Path, commit: str, fn: Callable[[Path], T], prepared: Prepared = Prepared()) -> T:
    """`commit` 의 깨끗한 임시 체크아웃에서 fn 을 실행하고 반드시 정리한다. 업무 worktree 는 건드리지 않는다.
    `prepared` 는 검증이 원본 폴더 설치물을 쓰도록 체크아웃에 거는 링크·복사본이다 — 정리(`worktree remove`)는
    링크만 지우고 원본은 건드리지 않는다."""
    with tempfile.TemporaryDirectory(prefix="workflow-checkout-") as tmp:
        dest = Path(tmp) / "checkout"
        git_ops.export_checkout(repo, commit, dest)
        try:
            prepared.apply(repo, dest)
            return fn(dest)
        finally:
            try:
                git_ops.remove_worktree(repo, dest)
            except GitError as exc:
                log.warning("임시 체크아웃 정리 실패 (%s): %s", dest, exc)
