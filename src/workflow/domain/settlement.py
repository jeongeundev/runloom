"""체인이 "사람 차례"인가 — callback 시점 판정 (ADR-0010). 시각·DB·HTTP 를 보지 않는다.

호출자(워커 tick 의 마지막 단계)가 체인의 각 Task 를 `NodeState` 로 스냅샷해 넘긴다. 판정은 상태 라벨
(`USER_STATUS_LABELS`)과 선행 Task 의 상태 라벨만 본다 — 이유 문구는 화면용이라 쓰지 않는다.
화면 폴링 규칙(`server/views._LIVE_LABELS`)과 같은 관찰이다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

LIVE_LABELS = ("실행 요청됨", "실행 중")  # 워커·실행 주체가 진행 중
BLOCKED_BY_HUMAN = ("확인 필요", "실패")  # 이 상태의 선행은 사람이 봐야 풀린다


@dataclass(frozen=True)
class NodeState:
    status: str  # USER_STATUS_LABELS 중 하나
    predecessor_status: str | None  # 선행 Task 의 사용자 상태. 선행이 없으면 None


def chain_settled(nodes: Sequence[NodeState]) -> bool:
    """(a) LIVE_LABELS 인 업무가 없고 (b) `대기` 인 업무는 모두 predecessor_status ∈ BLOCKED_BY_HUMAN 이면 참. 빈 목록은 거짓.

    `대기` 의 선행이 `확인 필요`/`실패` 면 워커가 더 할 수 없다 — 판정 불가·규칙 밖 outcome·실패한 선행은
    사람이 본다 (`worker._spawn_successors` 가 그렇게 둔다). 선행이 `완료`·`실행 중`·`대기`·None 인 `대기` 는
    자동 실행 대기·연결 끊김·선행 진행 중이라 아직 워커 몫이다. `실행 가능`·`확인 필요`·`완료`·`실패` 는
    사람 조작 전엔 바뀌지 않는다.
    """
    if not nodes:
        return False
    for node in nodes:
        if node.status in LIVE_LABELS:
            return False
        if node.status == "대기" and node.predecessor_status not in BLOCKED_BY_HUMAN:
            return False
    return True
