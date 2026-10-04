"""담당표는 수신자 후보를 제공하며 업무 배정이나 실행을 하지 않는다."""

from workflow.adapters import repo
from .conftest import SESSION


def test_empty_directory_requires_explicit_selection(logged_in_client):
    response = logged_in_client.get('/responsibilities/candidates', params={'system_id': 'billing', 'request_kind': 'investigation'})
    assert response.status_code == 200
    assert response.json()['candidates'] == []
    assert response.json()['selection_required'] is True
    assert logged_in_client.get('/responsibilities').json()['entries'] == []


def test_directory_round_trip_and_invalid_recipient_is_atomic(logged_in_client, conn):
    member = repo.list_members(conn, SESSION)[0]['member_id']
    entry = {'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': member,
             'judgment_member_id': member, 'agent_id': None}
    before = repo.get_config_revision(conn, SESSION)
    response = logged_in_client.put('/responsibilities', json={'entries': [entry], 'expected_revision': before})
    assert response.status_code == 200, response.text
    assert response.json()['entries'] == [entry]
    assert response.json()['config_revision'] == before + 1
    bad = {**entry, 'recipient_member_id': 'missing'}
    assert logged_in_client.put('/responsibilities', json={'entries': [bad], 'expected_revision': before + 1}).status_code == 422
    assert logged_in_client.get('/responsibilities').json() == response.json()


def test_multiple_candidates_require_choice_and_revalidate_disabled_member(logged_in_client, conn):
    member = repo.list_members(conn, SESSION)[0]['member_id']
    other = repo.add_member(conn, SESSION, display_name='다른 담당', role='member', now='2026-10-03T00:00:00Z')
    entries = [{'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': m,
                'judgment_member_id': member, 'agent_id': None} for m in (member, other)]
    revision = repo.get_config_revision(conn, SESSION)
    saved = logged_in_client.put('/responsibilities', json={'entries': entries, 'expected_revision': revision})
    assert saved.status_code == 200
    query = {'system_id': 'billing', 'request_kind': 'investigation'}
    result = logged_in_client.get('/responsibilities/candidates', params=query).json()
    assert len(result['candidates']) == 2 and result['selection_required']
    choice = {**query, 'recipient_member_id': other, 'expected_revision': revision + 1}
    selected = logged_in_client.post('/responsibilities/select', json=choice)
    assert selected.status_code == 200, selected.text
    assert selected.json()['entry'] == entries[1]
    assert selected.json()['execution_started'] is False
    conn.execute('UPDATE members SET disabled_at = ? WHERE member_id = ?', ('2026-10-03T00:00:00Z', other))
    assert logged_in_client.post('/responsibilities/select', json=choice).status_code == 422
    result = logged_in_client.get('/responsibilities/candidates', params=query).json()
    assert result['candidates'][1]['selectable'] is False
    assert logged_in_client.post('/responsibilities/select', json={**choice, 'expected_revision': revision}).status_code == 409


def test_unregistered_agent_and_duplicate_rows_are_rejected(logged_in_client, conn):
    member = repo.list_members(conn, SESSION)[0]['member_id']
    entry = {'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': member,
             'judgment_member_id': member, 'agent_id': None}
    revision = repo.get_config_revision(conn, SESSION)
    for entries in ([{**entry, 'agent_id': 'missing'}], [entry, entry]):
        assert logged_in_client.put('/responsibilities', json={'entries': entries, 'expected_revision': revision}).status_code == 422
    assert logged_in_client.get('/responsibilities').json()['entries'] == []


def test_only_admin_can_edit_and_anonymous_cannot_read(client, logged_in_client, conn):
    from fastapi.testclient import TestClient
    from .conftest import log_in_member

    anonymous = TestClient(client.app)
    assert anonymous.get('/responsibilities').status_code == 401
    assert anonymous.get('/responsibilities/candidates', params={'system_id': 'billing', 'request_kind': 'investigation'}).status_code == 401
    member_client = log_in_member(TestClient(client.app))
    assert member_client.get('/responsibilities').status_code == 200
    revision = repo.get_config_revision(conn, SESSION)
    assert member_client.put('/responsibilities', json={'entries': [], 'expected_revision': revision}).status_code == 403


def test_selection_does_not_assign_work_or_execute_and_revoked_agent_is_blocked(logged_in_client, conn):
    from .conftest import seed_agents

    seed_agents(conn)
    member = repo.list_members(conn, SESSION)[0]['member_id']
    entry = {'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': member,
             'judgment_member_id': member, 'agent_id': 'agent-codex-mac'}
    revision = repo.get_config_revision(conn, SESSION)
    assert logged_in_client.put('/responsibilities', json={'entries': [entry], 'expected_revision': revision}).status_code == 200
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in ('work_items', 'tasks', 'executions')}
    choice = {'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': member,
              'expected_revision': revision + 1}
    assert logged_in_client.post('/responsibilities/select', json=choice).status_code == 200
    assert {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in before} == before
    repo.unregister_session_agent(conn, SESSION, 'agent-codex-mac')
    assert logged_in_client.post('/responsibilities/select', json=choice).status_code == 422
    assert logged_in_client.get('/responsibilities/candidates', params={'system_id': 'billing', 'request_kind': 'investigation'}).json()['candidates'][0]['selectable'] is False


def test_other_workspace_recipient_and_missing_choice_are_rejected(logged_in_client, conn):
    from .conftest import NOW

    repo.create_session(conn, 'other-scope', NOW)
    other = repo.add_member(conn, 'other-scope', display_name='외부 담당', now=NOW)
    revision = repo.get_config_revision(conn, SESSION)
    entry = {'system_id': 'billing', 'request_kind': 'investigation', 'recipient_member_id': other,
             'judgment_member_id': other, 'agent_id': None}
    assert logged_in_client.put('/responsibilities', json={'entries': [entry], 'expected_revision': revision}).status_code == 422
    assert logged_in_client.post('/responsibilities/select', json={'system_id': 'billing', 'request_kind': 'investigation',
                                'recipient_member_id': other, 'expected_revision': revision}).status_code == 422
