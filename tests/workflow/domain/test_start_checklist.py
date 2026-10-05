"""시작하기 체크리스트 (phase 16 step 7, ARCHITECTURE "업무 화면 — phase 16" 시작하기).

항목 4개 — 가져올 곳·러너·팀원 초대(선택)·첫 업무 맡기기. 상태는 완료·다음(필수 중 첫 미완료)·할 일·선택.
선택 항목은 필수 완료 판정에 들어가지 않는다.
"""

from dataclasses import replace

from workflow.domain import team
from workflow.domain.start_checklist import START_ITEMS, StartFacts, required_done, start_items

EMPTY = StartFacts(has_source=False, has_runner=False, invited=False, delegated=False)
ALL = StartFacts(has_source=True, has_runner=True, invited=True, delegated=True)


def states(facts: StartFacts) -> dict[str, str]:
    return {item.key: item.state for item in start_items(facts)}


def test_items_are_four_in_order_with_labels_buttons_and_actions():
    items = start_items(EMPTY)
    assert [(i.key, i.label, i.required, i.href, i.action) for i in items] == [
        ("source", "가져올 곳 연결", True, "/repos", team.MANAGE_CONNECTIONS),
        ("runner", "러너 붙이기", True, "/repos", team.ATTACH_RUNNER),
        ("invite", "팀원 초대", False, "/team", team.MANAGE_TEAM),
        ("delegate", "첫 업무 맡기기", True, "/tasks?q=unassigned", team.DELEGATE),
    ]
    assert len(START_ITEMS) == 4


def test_empty_workspace_first_item_is_next():
    assert states(EMPTY) == {"source": "next", "runner": "todo", "invite": "optional", "delegate": "todo"}
    assert not required_done(EMPTY)


def test_next_moves_as_steps_are_done():
    one = replace(EMPTY, has_source=True)
    assert states(one) == {"source": "done", "runner": "next", "invite": "optional", "delegate": "todo"}
    two = replace(one, has_runner=True)
    assert states(two) == {"source": "done", "runner": "done", "invite": "optional", "delegate": "next"}
    assert states(ALL) == {"source": "done", "runner": "done", "invite": "done", "delegate": "done"}


def test_next_is_first_unfinished_required_even_out_of_order():
    facts = replace(EMPTY, has_runner=True, delegated=True)
    assert states(facts) == {"source": "next", "runner": "done", "invite": "optional", "delegate": "done"}


def test_optional_item_is_never_next_and_not_part_of_required_done():
    facts = replace(ALL, invited=False)
    assert states(facts)["invite"] == "optional"
    assert required_done(facts)
    assert not required_done(replace(EMPTY, invited=True))
    assert states(replace(EMPTY, invited=True))["invite"] == "done"


def test_required_done_needs_all_three_required():
    for missing in ("has_source", "has_runner", "delegated"):
        assert not required_done(replace(ALL, **{missing: False}))
    assert required_done(ALL)
