"""담당표 전체 교체는 설정 revision 검사와 한 트랜잭션이다."""
from sqlite3 import Connection
from workflow.adapters import repo
from workflow.contracts.responsibility import Responsibility


class StaleDirectory(Exception):
    pass


def list_entries(conn: Connection, session_id: str) -> list[Responsibility]:
    return [Responsibility.model_validate_json(r['entry_json']) for r in conn.execute(
        'SELECT entry_json FROM responsibilities WHERE session_id = ? ORDER BY position', (session_id,))]


def entry_problem(conn: Connection, session_id: str, entry: Responsibility) -> str | None:
    for member_id in (entry.recipient_member_id, entry.judgment_member_id):
        member = repo.get_member(conn, session_id, member_id)
        if member is None or member['disabled_at'] is not None:
            return '담당자와 판단 담당자는 이 워크스페이스의 활성 멤버여야 합니다.'
    if entry.agent_id is not None and not repo.is_session_agent(conn, session_id, entry.agent_id):
        return '조사 에이전트는 이 워크스페이스에 등록되어야 합니다.'
    return None


def replace_entries(conn: Connection, session_id: str, entries: list[Responsibility], *, expected_revision: int,
                    member_id: str, now: str) -> None:
    conn.execute('BEGIN IMMEDIATE')
    try:
        if repo.get_config_revision(conn, session_id) != expected_revision:
            raise StaleDirectory
        keys = [(e.system_id, e.request_kind, e.recipient_member_id) for e in entries]
        if len(set(keys)) != len(keys):
            raise ValueError('같은 시스템·요청 종류·수신자의 담당 항목이 중복되었습니다.')
        for entry in entries:
            problem = entry_problem(conn, session_id, entry)
            if problem:
                raise ValueError(problem)
        conn.execute('DELETE FROM responsibilities WHERE session_id = ?', (session_id,))
        conn.executemany('INSERT INTO responsibilities (session_id, position, entry_json) VALUES (?, ?, ?)',
                         [(session_id, i, e.model_dump_json()) for i, e in enumerate(entries)])
        repo.bump_config_revision(conn, session_id, area='mapping', action='change',
                                  subject=f'담당 범위 표 {len(entries)}행', member_id=member_id, now=now)
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
