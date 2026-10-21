"""업무 키 조회 공용 함수 — 형식이 틀리거나 없거나 다른 워크스페이스면 None(예외 없음)."""

from workflow.adapters import repo
from workflow.server.work_lookup import WORK_KEY, work_by_key

from .conftest import NOW, SESSION


def _work(conn, session_id):
    conn.execute("BEGIN IMMEDIATE")
    work_id, number = repo.create_work_item(conn, session_id, title="결제 오류", request="원인 확인", kind="bug_fix",
                                            source_type="manual", now=NOW)
    conn.execute("COMMIT")
    return work_id, number


def test_work_by_key_finds_own_work(logged_in_client, conn):
    work_id, number = _work(conn, SESSION)
    assert work_by_key(conn, SESSION, f"RUN-{number}")["work_item_id"] == work_id


def test_work_by_key_returns_none_for_bad_format_missing_and_other_workspace(logged_in_client, conn):
    work_id, number = _work(conn, SESSION)
    repo.create_session(conn, "other-scope", NOW)
    _work(conn, "other-scope")
    _, other_number = _work(conn, "other-scope")
    for key in (work_id, "run-1", "RUN-0", "RUN-01", f"RUN-{number} ", "RUN-1234567890", "OPS-1", "",
                f"RUN-{other_number}"):
        assert work_by_key(conn, SESSION, key) is None, key


def test_work_key_pattern_is_the_web_pattern():
    from workflow.server import web
    assert web.WORK_KEY is WORK_KEY
    assert WORK_KEY.fullmatch("RUN-12") and not WORK_KEY.fullmatch("RUN-0")
