"""Jira 이슈 가져오기 규칙 — JQL·받기·업무 칸·준비 판정 입력·세 순간·전환 고르기 (ADR-0024 결정 4~9·11·12,
ARCHITECTURE "Jira 소스 — phase 18").

DB·HTTP 를 보지 않는다. 수집(`server/jira_sync`)이 스냅숏과 설정을 넘기고 저장은 repo 가 한다.

- JQL 에는 프로젝트 **id**(숫자)와 커서(epoch ms 숫자)만 넣는다. 사용자 문자열이 꼭 들어가야 하면 `jql_string` 으로
  따옴표·역슬래시를 이스케이프한다. 이슈 유형 거르기는 JQL 이 아니라 받은 뒤 `accept` 가 한다.
- 원본 열림/닫힘은 상태 범주다 — `done` 이면 `closed`. Jira 상태는 완료 판정 근거가 아니다(계약 v1).
- 종류·우선순위는 매핑 표(`field_mappings`, `jira`)가 라벨 + 이슈 유형 이름 + 우선순위 이름으로 정한다. 종류 이름으로
  분기하지 않는다.
- 이슈 제목·본문은 업무 제목·요청 재료일 뿐 명령·경로·URL 로 해석하지 않는다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.jira import JIRA_MOMENTS as JIRA_MOMENTS  # 계약 상수를 이 모듈 이름으로도 쓴다
from workflow.contracts.jira import (
    JiraIssueSnapshot,
    JiraProjectConfig,
    JiraTransition,
    normalize_site_url,
)
from workflow.contracts.v1 import KindSpec
from workflow.domain.completion import criteria_template, merge_criteria
from workflow.domain.field_mapping import MappingRow, map_value
from workflow.domain.form_sections import extract_form
from workflow.domain.issue_intake import IntakeFacts

_NUMERIC_ID = re.compile(r"[1-9][0-9]*")
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# 업무 상태(`to`) → 순간. 그 밖(`새로 들어옴`·`대기`·`내 차례`·`종료`)은 옮기지 않는다.
MOMENT_OF_STATUS = {
    "에이전트 작업 중": "start",
    "직접 작업 중": "start",
    "PR · 검토": "review",
    "완료": "done",
}
# 순간 → 프로젝트 설정 칸(= `jira_projects` 칸 이름). repo 가 SQL 칸 이름을 이 고정 사전에서만 고른다.
MOMENT_COLUMNS = {"start": "status_on_start", "review": "status_on_review", "done": "status_on_done"}


# --- JQL ---

def jql_string(value: str) -> str:
    """JQL 문자열 리터럴 — 큰따옴표로 감싸고 `\\`·`"`·줄바꿈을 이스케이프한다."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def search_jql(project_id: str, cursor_ms: int | None) -> str:
    if not isinstance(project_id, str) or not _NUMERIC_ID.fullmatch(project_id) or not project_id.isascii():
        raise ValueError("project_id 는 숫자 id 여야 합니다")
    if cursor_ms is None:
        return f"project = {project_id} AND statusCategory != Done ORDER BY updated ASC, key ASC"
    if not isinstance(cursor_ms, int) or isinstance(cursor_ms, bool) or cursor_ms < 0:
        raise ValueError("cursor_ms 는 0 이상의 정수여야 합니다")
    return f"project = {project_id} AND updated >= {cursor_ms} ORDER BY updated ASC, key ASC"


# --- 커서 ---

def _ms(value: str) -> int:
    return (datetime.fromisoformat(value.replace("Z", "+00:00")) - _EPOCH) // timedelta(milliseconds=1)


def updated_ms(snapshot: JiraIssueSnapshot) -> int:
    return _ms(snapshot.updated)


def next_cursor_ms(snapshots: Sequence[JiraIssueSnapshot], previous: int | None) -> int | None:
    """본 이슈의 가장 늦은 `updated`(포함 경계 — 다음 바퀴에 다시 받은 이슈는 digest 로 흡수). 뒤로 가지 않는다."""
    values = [updated_ms(s) for s in snapshots]
    if previous is not None:
        values.append(previous)
    return max(values, default=None)


def initial_cursor_ms(start_mode: Literal["from_now", "all_open"], start_at: str) -> int | None:
    """프로젝트를 추가할 때의 커서 — `from_now` 는 그 시각, `all_open` 은 없음(열린 업무 전부)."""
    return _ms(start_at) if start_mode == "from_now" else None


# --- 받기·업무 칸 ---

def accept(project: JiraProjectConfig, snapshot: JiraIssueSnapshot) -> bool:
    """처음 보는 이슈를 받을지. 이미 받은 이슈의 변경은 범위와 무관하게 수집기가 반영한다."""
    if snapshot.project_id != project.project_id:
        return False
    if project.issue_types and snapshot.issue_type.casefold() not in {t.casefold() for t in project.issue_types}:
        return False
    if snapshot.status_category == "done":
        return False
    return project.start_mode == "all_open" or _ms(snapshot.created) >= _ms(project.start_at)


def issue_state(snapshot: JiraIssueSnapshot) -> Literal["open", "closed"]:
    return "closed" if snapshot.status_category == "done" else "open"


def mapping_values(snapshot: JiraIssueSnapshot) -> tuple[str, ...]:
    """매핑 표 입력값 — 라벨 + 이슈 유형 이름 + 우선순위 이름(있으면)."""
    return (*snapshot.labels, snapshot.issue_type, *((snapshot.priority,) if snapshot.priority else ()))


def issue_kind(rows: Sequence[MappingRow], snapshot: JiraIssueSnapshot) -> str | None:
    """None 이면 그 이슈를 가져오지 않는다 — 기본 종류는 매핑 행(`*`)으로만 둔다."""
    return map_value(rows, "jira", "kind", mapping_values(snapshot))


def issue_priority(rows: Sequence[MappingRow], snapshot: JiraIssueSnapshot) -> str:
    return map_value(rows, "jira", "priority", mapping_values(snapshot)) or "normal"


def task_input(snapshot: JiraIssueSnapshot) -> tuple[str, str]:
    """(제목, 요청). 이 둘이 바뀌면 새 Task revision 이다."""
    return snapshot.summary, snapshot.description_text.strip()


def issue_url(site_url: str, key: str) -> str:
    if not _ISSUE_KEY.fullmatch(key):
        raise ValueError("이슈 키 형식이 아닙니다")
    return f"{normalize_site_url(site_url)}/browse/{key}"


@dataclass(frozen=True)
class JiraWorkFields:
    """스냅숏이 채우는 업무 칸."""

    title: str
    request: str
    form: dict  # `work_items.form_json` 모양
    mapping_values: tuple[str, ...]
    source_item_id: str
    source_key: str
    source_url: str
    source_state: str  # Jira 상태 이름
    state: Literal["open", "closed"]


def work_fields(snapshot: JiraIssueSnapshot, *, site_url: str) -> JiraWorkFields:
    title, request = task_input(snapshot)
    return JiraWorkFields(
        title=title,
        request=request,
        form=extract_form(request, origin="jira_description").to_json(),
        mapping_values=mapping_values(snapshot),
        source_item_id=snapshot.issue_id,
        source_key=snapshot.key,
        source_url=issue_url(site_url, snapshot.key),
        source_state=snapshot.status_name,
        state=issue_state(snapshot),
    )


def snapshot_to_task_spec(
    project: JiraProjectConfig,
    run: GitHubSourceConfig,
    snapshot: JiraIssueSnapshot,
    *,
    kind: KindSpec,
    session_id: str,
    task_id: str,
) -> dict:
    """`repo.insert_task` 와 같은 dict(`issue_intake.snapshot_to_task_spec` 과 같은 모양). 실행 설정은 연결 저장소(`run`)의
    것 — 요구 능력 범위는 로컬 저장소 id, 없으면 저장소 이름(준비 판정이 매칭 값으로 바꿔 본다)."""
    title, request = task_input(snapshot)
    criteria = merge_criteria(criteria_template(kind), [])
    return {
        "task_id": task_id,
        "session_id": session_id,
        "title": title,
        "request": request,
        "kind": kind.kind,
        "required_capability": {
            "code": kind.capability_code,
            "scope": {kind.scope_key: run.workflow_repository_id or run.repository_full_name},
        },
        "selection_mode": "auto",
        "chosen_agent_id": None,
        "run_mode": run.run_mode,
        "completion_mode": "review",
        "criteria": [c.__dict__ for c in criteria],
        "predecessor_task_id": None,
        "revision": 1,
        "target": {},
        "status": "대기",
        "status_reason": "준비 판정 대기",
        "chain_id": None,
        "source_ref": snapshot.key,
    }


# --- 원본 조회·준비 판정 입력 ---

def run_config(config: GitHubSourceConfig) -> GitHubSourceConfig:
    """연결 저장소 설정을 Jira 업무의 실행 설정으로 — 지시는 [맡기기]로만 받으므로 `all_open`, 라벨 지시 없음."""
    return config.model_copy(update={"intake": "all_open", "trigger_label": None})


def intake_facts(
    *,
    request: str,
    run_mode: Literal["auto", "manual"],
    state: Literal["open", "closed"],
    delegated_by: Literal["operator", "followup"] | None,
    max_rework_rounds: int | None,
) -> IntakeFacts:
    """Jira 행이 붙은 단계(= 수정 단계)의 사실. `assignee_ids=()` 라 수정 매칭이고, 지시 전이면 착수하지 않는다.
    운영자 지시는 직접 지시라 `manual` 이어도 자동 착수처럼 본다(GitHub `intake_facts` 와 같다)."""
    return IntakeFacts(
        assignee_ids=(),
        bindings={},
        request_text=request,
        request_required=True,
        run_mode="auto" if delegated_by == "operator" else run_mode,
        max_rework_rounds=max_rework_rounds,
        source_state=state,
        delegated=delegated_by is not None,
    )


# --- 세 순간·전환 ---

def moment_for(status: str) -> str | None:
    """업무 상태가 새로 들어간 값(`to`) → 순간. 값이 바뀔 때만 부른다(`set_work_status`)."""
    return MOMENT_OF_STATUS.get(status)


def moment_target(project: JiraProjectConfig, moment: str) -> str | None:
    """그 순간에 옮길 Jira 상태 이름. 비어 있으면 그 순간은 옮기지 않는다."""
    return getattr(project, MOMENT_COLUMNS[moment])


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def already_in(current_name: str, target_name: str) -> bool:
    return _same_name(current_name, target_name)


def choose_transition(transitions: Sequence[JiraTransition], target_name: str) -> str | None:
    """도착 상태 이름이 목표와 같은(대소문자·앞뒤 공백 무시) 첫 전환의 id. 없으면 None."""
    return next((t.transition_id for t in transitions if _same_name(t.to_name, target_name)), None)


# --- 후속 이슈 등록 (ADR-0024 결정 13) ---

FOLLOWUP_LABEL = "runloom"
JIRA_LINK_TYPE = "Relates"
FOLLOWUP_SUMMARY_MAX = 255
FOLLOWUP_REQUEST_MAX = 2000
_FOLLOWUP_LABEL = re.compile(r"runloom-RUN-([1-9][0-9]*)")


def followup_label(key_number: int) -> str:
    """후속 이슈를 업무 RUN-n 과 잇는 라벨 — 고정 형식이라 JQL 문자열에 넣어도 안전하다."""
    if not isinstance(key_number, int) or isinstance(key_number, bool) or key_number < 1:
        raise ValueError("key_number 는 1 이상의 정수여야 합니다")
    return f"runloom-RUN-{key_number}"


def followup_labels(key_number: int) -> tuple[str, str]:
    return FOLLOWUP_LABEL, followup_label(key_number)


def followup_key_number(labels: Sequence[str]) -> int | None:
    """라벨 `runloom-RUN-<n>` 의 n(처음 것). 없으면 None — 가져오기의 후속 조정 판정."""
    for label in labels:
        match = _FOLLOWUP_LABEL.fullmatch(label)
        if match:
            return int(match.group(1))
    return None


def followup_summary(title: str) -> str:
    return title[:FOLLOWUP_SUMMARY_MAX]


def followup_description(*, work_key: str, cause_key: str, request: str, work_url: str | None) -> str:
    """후속 이슈 본문(Markdown — `adf.markdown_to_adf` 로 바꿔 보낸다). 요청은 앞 2000자, 공개 주소가 없으면 그 줄 없음."""
    parts = [f"Runloom 후속 업무 {work_key} — {cause_key} 의 결과로 생겼습니다."]
    if body := request.strip()[:FOLLOWUP_REQUEST_MAX]:
        parts.append(body)
    if work_url:
        parts.append(f"Runloom 업무: {work_url}")
    return "\n\n".join(parts)
