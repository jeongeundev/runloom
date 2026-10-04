from fastapi.testclient import TestClient
from workflow.adapters import repo
from .conftest import SESSION, log_in_member


def test_directory_forms_save_remove_and_reject_stale(logged_in_client, conn):
    member = repo.list_members(conn, SESSION)[0]['member_id']
    page = logged_in_client.get('/connect?tab=team')
    assert '담당 범위 표' in page.text
    assert 'action="/responsibilities/add"' in page.text
    revision = repo.get_config_revision(conn, SESSION)
    data = dict(system_id='billing', request_kind='investigation', recipient_member_id=member,
                judgment_member_id=member, agent_id='', expected_revision=revision)
    saved = logged_in_client.post('/responsibilities/add', data=data, follow_redirects=False)
    assert saved.status_code == 303
    assert saved.headers['location'] == '/connect?tab=team'
    assert logged_in_client.post('/responsibilities/add', data=data).status_code == 409
    assert 'billing' in logged_in_client.get('/connect?tab=team').text
    removed = logged_in_client.post('/responsibilities/remove', data=dict(position=0, expected_revision=revision + 1))
    assert removed.status_code == 200
    assert logged_in_client.get('/responsibilities').json()['entries'] == []


def test_member_reads_but_cannot_change_directory(client, logged_in_client):
    member = log_in_member(TestClient(client.app))
    page = member.get('/connect?tab=team')
    assert '담당 범위 표' in page.text
    assert 'action="/responsibilities/add"' not in page.text
    assert member.post('/responsibilities/remove', data=dict(position=0, expected_revision=1)).status_code == 403


def test_invalid_form_does_not_modify_directory(logged_in_client, conn):
    member = repo.list_members(conn, SESSION)[0]['member_id']
    revision = repo.get_config_revision(conn, SESSION)
    data = dict(system_id='/tmp/billing', request_kind='investigation', recipient_member_id=member,
                judgment_member_id=member, agent_id='', expected_revision=revision)
    assert logged_in_client.post('/responsibilities/add', data=data).status_code == 422
    data.update(system_id='billing', recipient_member_id='missing')
    assert logged_in_client.post('/responsibilities/add', data=data).status_code == 422
    assert logged_in_client.get('/responsibilities').json() == dict(config_revision=revision, entries=[])
