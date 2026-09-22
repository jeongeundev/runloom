"""체인이 "사람 차례"인가 — callback 시점 판정 (ADR-0010).

상태 라벨과 선행 상태만 본다. 이유 문구·시각·DB 는 판정에 쓰지 않는다.
"""

import pytest

from workflow.domain.settlement import BLOCKED_BY_HUMAN, LIVE_LABELS, NodeState, chain_settled
from workflow.domain.status import USER_STATUS_LABELS


def test_label_constants_are_user_status_labels():
    assert LIVE_LABELS == ("실행 요청됨", "실행 중")
    assert BLOCKED_BY_HUMAN == ("확인 필요", "실패")
    assert set(LIVE_LABELS) <= set(USER_STATUS_LABELS)
    assert set(BLOCKED_BY_HUMAN) <= set(USER_STATUS_LABELS)


@pytest.mark.parametrize(
    ("scene", "nodes", "expected"),
    [
        ("빈 체인", [], False),
        ("첫 업무 후보 없음", [("확인 필요", None)], True),
        ("A 완료 → B 검토 대기 (주 경로)", [("완료", None), ("확인 필요", "완료")], True),
        ("A 완료 → B 실행 중", [("완료", None), ("실행 중", "완료")], False),
        ("A 완료 → B 실행 요청됨", [("완료", None), ("실행 요청됨", "완료")], False),
        ("A 판정 불가 → B 선행 대기", [("확인 필요", None), ("대기", "확인 필요")], True),
        ("A 실패 → B 선행 대기", [("실패", None), ("대기", "실패")], True),
        ("A 완료 → B 자동 실행 대기·연결 끊김", [("완료", None), ("대기", "완료")], False),
        ("A 실행 중 → B 선행 대기", [("실행 중", None), ("대기", "실행 중")], False),
        ("시작 전 (실행 가능)", [("실행 가능", None)], True),
        ("단독 진단 완료", [("완료", None)], True),
        ("첫 업무 대기 (연결 끊김)", [("대기", None)], False),
        (
            "A→B→C, C 실행 중",
            [("완료", None), ("확인 필요", "완료"), ("실행 중", "확인 필요")],
            False,
        ),
        (
            "A→B→C, C 검토 대기",
            [("완료", None), ("확인 필요", "완료"), ("확인 필요", "확인 필요")],
            True,
        ),
    ],
)
def test_chain_settled(scene, nodes, expected):
    states = [NodeState(status=s, predecessor_status=p) for s, p in nodes]

    assert chain_settled(states) is expected, scene


def test_node_state_is_frozen():
    node = NodeState(status="대기", predecessor_status=None)
    with pytest.raises(AttributeError):
        node.status = "완료"  # type: ignore[misc]
