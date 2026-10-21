"""업무 키(`RUN-12`)로 이 워크스페이스의 업무 찾기 — 웹 화면과 사람용 API 가 함께 쓴다(ADR-0032)."""

import re
from sqlite3 import Connection, Row

from workflow.adapters import repo
from workflow.contracts.v1 import WORK_KEY_PREFIX

WORK_KEY = re.compile(rf"{WORK_KEY_PREFIX}-([1-9][0-9]{{0,8}})")


def work_by_key(conn: Connection, session_id: str, key: str) -> Row | None:
    """형식이 틀리거나 없는 업무·다른 워크스페이스 업무는 None — 예외 없음."""
    match = WORK_KEY.fullmatch(key)
    return repo.get_work_item_by_key(conn, session_id, int(match.group(1))) if match else None
