import json
from workflow.adapters import repo
from .conftest import SESSION
from .test_internal_request_api import setup_request


def test_send_from_work_panel_and_accept_from_inbox(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    panel = logged_in_client.get(f'/work/{key}/panel')
    assert f'action="/work/{key}/internal-requests"' in panel.text
    selection = json.dumps(dict(system_id=body['system_id'], request_kind=body['request_kind'],
                                recipient_member_id=body['recipient_member_id']))
    response = logged_in_client.post(f'/work/{key}/internal-requests', data=dict(
        selection=selection, expected_directory_revision=body['expected_directory_revision'],
        submission_key=body['submission_key'], purpose=body['purpose']), follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers['location'] == f'/tasks?open={key}'
    request = logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests'][0]
    inbox = recipient.get('/requests')
    assert inbox.status_code == 200 and body['purpose'] in inbox.text
    action = f"/requests/{request['request_id']}/accept"
    assert f'action="{action}"' in inbox.text
    assert f'action="{action}"' not in logged_in_client.get('/requests').text
    accepted = recipient.post(action, data={'expected_revision': 1}, follow_redirects=False)
    assert accepted.status_code == 303 and accepted.headers['location'] == '/requests'
    assert '수락됨' in recipient.get('/requests').text
    assert '수락됨' in logged_in_client.get(f'/work/{key}/panel').text
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before


def test_request_form_rejects_invalid_selection_and_escapes_context(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    data = dict(selection='[]', purpose=body['purpose'], submission_key='form-1',
                expected_directory_revision=body['expected_directory_revision'])
    assert logged_in_client.post(f'/work/{key}/internal-requests', data=data).status_code == 422
    assert logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests'] == []
    created = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json={**body, 'purpose': '<script>alert(1)</script>'}).json()
    page = recipient.get('/requests')
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page.text
    assert '<script>alert(1)</script>' not in page.text
    assert logged_in_client.post(f"/requests/{created['request_id']}/accept", data={'expected_revision': 1}).status_code == 403


def test_investigation_forms_and_returned_result_are_visible_in_original_work(logged_in_client, conn, store, settings):
    from workflow.server.auth import utc_now
    from workflow.server.worker import Worker
    from .test_internal_investigation import investigation_request
    from .test_worker import seed_generic_result

    recipient, work_id, request_id = investigation_request(logged_in_client, conn)
    page = recipient.get('/requests')
    action = f'/requests/{request_id}/investigation'
    assert f'action="{action}"' in page.text
    assert f'action="{action}"' not in logged_in_client.get('/requests').text
    response = recipient.post(action, data=dict(expected_revision=2, investigation_selection=json.dumps(
        dict(kind='investigate', scope_value='billing'))), follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/requests'
    request = recipient.get('/internal-requests').json()['requests'][0]
    task_id = request['investigation_task_id']
    execution_id = repo.list_executions(conn, task_id)[0]['execution_id']
    assert f'href="/tasks/{task_id}"' in recipient.get('/requests').text
    result_id = seed_generic_result(conn, store, execution_id, utc_now(), task_id=task_id,
                                   kind='investigate', outcome='reported', summary='공유 결과 <근거>')
    Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now).tick()
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    return_action = f'/requests/{request_id}/return'
    assert f'action="{return_action}"' in recipient.get('/requests').text
    response = recipient.post(return_action, data=dict(expected_revision=3, execution_id=execution_id), follow_redirects=False)
    assert response.status_code == 303
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    original = logged_in_client.get(f'/work/{key}/panel')
    assert '공유 결과 &lt;근거&gt;' in original.text
    assert f'/tasks/{task_id}/artifacts/{result_id}' in original.text


def test_not_responsible_and_reroute_forms(logged_in_client, conn):
    from .test_internal_request_api import setup_request
    from workflow.adapters import repo
    from .conftest import SESSION

    recipient, work_id, body = setup_request(logged_in_client, conn)
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    assert f'/requests/{old["request_id"]}/not-responsible' in recipient.get('/requests').text
    assert recipient.post(f'/requests/{old["request_id"]}/not-responsible', data=dict(
        expected_revision=1, reason='<팀 다름>'), follow_redirects=False).status_code == 303
    html = logged_in_client.get('/requests').text
    assert '&lt;팀 다름&gt;' in html and '담당 아님' in html
    assert f'/requests/{old["request_id"]}/accept' not in recipient.get('/requests').text
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id=body['system_id'], request_kind=body['request_kind'],
                 recipient_member_id=old['requester_member_id'], judgment_member_id=old['requester_member_id'], agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(expected_revision=revision, entries=[entry])).status_code == 200
    assert logged_in_client.post(f'/requests/{old["request_id"]}/reroute', data=dict(
        expected_revision=2, expected_directory_revision=revision + 1,
        recipient_member_id=old['requester_member_id']), follow_redirects=False).status_code == 303
    assert '이전 요청' in logged_in_client.get('/requests').text


def test_information_forms_show_waiting_history_and_escape_text(logged_in_client, conn):
    from .test_internal_request_api import setup_request

    recipient, work_id, body = setup_request(logged_in_client, conn)
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    base = f"/internal-requests/{old['request_id']}"
    recipient.post(base + '/accept', json={'expected_revision': 1})
    assert f"/requests/{old['request_id']}/questions" in recipient.get('/requests').text
    asked = recipient.post(f"/requests/{old['request_id']}/questions", data=dict(
        expected_revision=2, submission_key='web-question', text='<발생 시각?>'), follow_redirects=False)
    assert asked.status_code == 303, asked.text
    row = logged_in_client.get('/internal-requests').json()['requests'][0]
    question_id = row['questions'][0]['question_id']
    html = logged_in_client.get('/requests').text
    assert '정보 답변 대기' in html and '&lt;발생 시각?&gt;' in html
    assert f'/questions/{question_id}/answer' in html
    assert f'/questions/{question_id}/answer' not in recipient.get('/requests').text
    answer = logged_in_client.post(f"/requests/{old['request_id']}/questions/{question_id}/answer", data=dict(
        expected_revision=1, text='<15시>'), follow_redirects=False)
    assert answer.status_code == 303, answer.text
    html = recipient.get('/requests').text
    assert '&lt;15시&gt;' in html and '정보 답변 대기' not in html


def test_judgment_forms_show_basis_and_designated_response(logged_in_client, conn, store, settings):
    from .test_internal_investigation import reviewed_investigation

    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    assert f'/requests/{request_id}/judgments' in recipient.get('/requests').text
    requested = recipient.post(f'/requests/{request_id}/judgments', data=dict(expected_revision=3,
        execution_id=execution_id, submission_key='web-judgment', issue='<반환 판단>'), follow_redirects=False)
    assert requested.status_code == 303, requested.text
    row = logged_in_client.get('/internal-requests').json()['requests'][0]
    judgment_id = row['judgments'][0]['judgment_id']
    html = logged_in_client.get('/requests').text
    assert '판단 응답 대기' in html and '&lt;반환 판단&gt;' in html and '검토할 조사 결과' in html
    assert f'/judgments/{judgment_id}/respond' in html
    html = recipient.get('/requests').text
    assert f'/judgments/{judgment_id}/respond' not in html
    assert f'/requests/{request_id}/return' not in html
    responded = logged_in_client.post(f'/requests/{request_id}/judgments/{judgment_id}/respond', data=dict(
        expected_revision=1, decision='approve', reason='<근거 확인>'), follow_redirects=False)
    assert responded.status_code == 303, responded.text
    html = recipient.get('/requests').text
    assert '&lt;근거 확인&gt;' in html and '판단 승인' in html
    assert f'/requests/{request_id}/return' in html
    assert recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200
    assert '현재 결과·정보와 다른 시점의 판단 기록' not in recipient.get('/requests').text


def test_requester_resumption_form_and_escaped_record(logged_in_client, conn, store, settings):
    from .test_internal_investigation import reviewed_investigation

    recipient, work_id, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    path = f'/requests/{request_id}/resume'
    assert path not in logged_in_client.get('/requests').text
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution_id))
    assert path in logged_in_client.get('/requests').text
    assert path not in recipient.get('/requests').text
    result = logged_in_client.post(path, data=dict(expected_revision=4, text='<재개 내용>'), follow_redirects=False)
    assert result.status_code == 303, result.text
    html = logged_in_client.get('/requests').text
    assert '업무 재개 기록됨' in html and '&lt;재개 내용&gt;' in html
    assert path not in html
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    panel = logged_in_client.get(f'/work/{key}/panel')
    assert '업무 재개 기록' in panel.text and '&lt;재개 내용&gt;' in panel.text


def test_web_send_and_reroute_notify_the_recipient_once(logged_in_client, conn):
    """웹 폼으로 만든 요청·재전달도 받는 사람에게 `internal_request_received` 한 번(phase 22 step 8)."""
    from workflow.adapters.secret_store import NOTIFY_WEBHOOK_URL
    from .test_internal_request_api import _received

    logged_in_client.app.state.secrets.write(NOTIFY_WEBHOOK_URL, 'https://hooks.example/shared-token')
    recipient, work_id, body = setup_request(logged_in_client, conn)
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    selection = json.dumps(dict(system_id=body['system_id'], request_kind=body['request_kind'],
                                recipient_member_id=body['recipient_member_id']))
    data = dict(selection=selection, expected_directory_revision=body['expected_directory_revision'],
                submission_key=body['submission_key'], purpose='<b>원인</b> 확인\n둘째 줄')
    for _ in range(2):
        assert logged_in_client.post(f'/work/{key}/internal-requests', data=data, follow_redirects=False).status_code == 303
    (old,) = logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests']
    (row,) = _received(conn)
    assert row['dedupe_key'] == f"internal_request_received:{old['request_id']}:shared"
    assert row['content'].split('\n')[0].endswith(' · billing/investigation · <b>원인</b> 확인 → API 담당')
    recipient.post(f"/requests/{old['request_id']}/not-responsible", data=dict(expected_revision=1, reason='팀 다름'))
    target_id = old['requester_member_id']
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id='billing', request_kind='investigation', recipient_member_id=target_id,
                 judgment_member_id=target_id, agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(expected_revision=revision, entries=[entry])).status_code == 200
    assert logged_in_client.post(f"/requests/{old['request_id']}/reroute", data=dict(
        expected_revision=2, expected_directory_revision=revision + 1, recipient_member_id=target_id),
        follow_redirects=False).status_code == 303
    new = [r for r in logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests']
           if r['request_id'] != old['request_id']][0]
    assert [r['dedupe_key'] for r in _received(conn)] == [
        f"internal_request_received:{old['request_id']}:shared", f"internal_request_received:{new['request_id']}:shared"]
