"""시작하기 체크리스트 — ARCHITECTURE "업무 화면 — phase 16" 시작하기. 순수 함수(DB·HTTP 없음).

완료 사실은 `repo.start_facts` 가 DB 에서 모아 넘긴다. 상태: done(완료) / next(필수 중 첫 미완료) / todo(그 밖 필수 미완료) /
optional(선택 미완료). 선택 항목은 필수 완료 판정(`required_done`)에 들어가지 않는다.
"""

from dataclasses import dataclass
from typing import Literal

from workflow.domain import team


@dataclass(frozen=True)
class StartFacts:
    has_source: bool  # 활성 GitHub 소스 또는 취소되지 않은 입구 토큰
    has_runner: bool  # 취소되지 않은 연결 프로그램
    invited: bool  # 활성 멤버 2명 이상 또는 발급된 초대
    delegated: bool  # 실행이 한 번이라도 있는 업무


@dataclass(frozen=True)
class StartItem:
    key: str
    label: str
    state: Literal["done", "next", "todo", "optional"]
    required: bool
    href: str
    action: str  # 이 항목을 하는 데 필요한 동작(domain/team.py)


# (키, 이름, 필수, 버튼 주소, 필요 동작, 완료 사실 칸)
START_ITEMS = (
    ("source", "가져올 곳 연결", True, "/repos", team.MANAGE_CONNECTIONS, "has_source"),
    ("runner", "러너 붙이기", True, "/repos", team.ATTACH_RUNNER, "has_runner"),
    ("invite", "팀원 초대", False, "/team", team.MANAGE_TEAM, "invited"),
    ("delegate", "첫 업무 맡기기", True, "/tasks?q=unassigned", team.DELEGATE, "delegated"),
)


def start_items(facts: StartFacts) -> tuple[StartItem, ...]:
    items = []
    next_given = False
    for key, label, required, href, action, fact in START_ITEMS:
        if getattr(facts, fact):
            state = "done"
        elif not required:
            state = "optional"
        elif not next_given:
            state, next_given = "next", True
        else:
            state = "todo"
        items.append(StartItem(key, label, state, required, href, action))
    return tuple(items)


def required_done(facts: StartFacts) -> bool:
    return all(getattr(facts, fact) for _, _, required, _, _, fact in START_ITEMS if required)
