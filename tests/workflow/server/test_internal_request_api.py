from fastapi.testclient import TestClient
from workflow.adapters import repo
from .conftest import SESSION, NOW, ADMIN_EMAIL, log_in_member


def setup_request(logged_in_client, conn):
    recipient = log_in_member(TestClient(logged_in_client.app), display_name='API 담당')
    recipient_id = next(m['member_id'] for m in repo.list_members(conn, SESSION) if m['display_name'] == 'API 담당')
    requester_id = repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)['member_id']
    conn.execute('BEGIN IMMEDIATE')
    work_id, _ = repo.create_work_item(conn, SESSION, title='결제 오류', request='원인 확인', kind='bug_fix',
                                     source_type='manual', assignee_type='member', assignee_id=requester_id, now=NOW)
    conn.execute('COMMIT')
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id='billing', request_kind='investigation', recipient_member_id=recipient_id,
                 judgment_member_id=requester_id, agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(entries=[entry], expected_revision=revision)).status_code == 200
    body = dict(system_id='billing', request_kind='investigation', recipient_member_id=recipient_id,
                expected_directory_revision=revision + 1, submission_key='request-1', purpose='에러 원인을 확인해 주세요.')
    return recipient, work_id, body


def test_create_and_recipient_accept_preserve_original_work(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('tasks', 'executions')}
    response = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body)
    assert response.status_code == 200, response.text
    request = response.json()
    assert request['state'] == 'pending' and request['revision'] == 1
    assert recipient.get('/internal-requests').json()['requests'] == [request]
    accepted = recipient.post(f"/internal-requests/{request['request_id']}/accept", json={'expected_revision': 1})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['state'] == 'accepted' and accepted.json()['revision'] == 2
    assert accepted.json()['accepted_at'] is not None
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before
    assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in counts} == counts


def test_replayed_submission_is_same_request_and_changed_payload_conflicts(logged_in_client, conn):
    _, work_id, body = setup_request(logged_in_client, conn)
    url = f'/work-items/{work_id}/internal-requests'
    first = logged_in_client.post(url, json=body).json()
    # 설정 변경 후에도 이미 접수된 같은 요청은 다시 만들지 않는다.
    revision = repo.get_config_revision(conn, SESSION)
    logged_in_client.put('/responsibilities', json=dict(entries=[], expected_revision=revision))
    assert logged_in_client.post(url, json=body).json() == first
    assert logged_in_client.post(url, json={**body, 'purpose': '다른 질문'}).status_code == 409
    assert logged_in_client.get(url).json()['requests'] == [first]


def test_only_recipient_accepts_and_repeated_accept_is_idempotent(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    created = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    url = f"/internal-requests/{created['request_id']}/accept"
    assert logged_in_client.post(url, json={'expected_revision': 1}).status_code == 403  # 관리자도 수신자 아님
    assert recipient.post(url, json={'expected_revision': 9}).status_code == 409
    first = recipient.post(url, json={'expected_revision': 1}).json()
    assert first['revision'] == 2
    assert recipient.post(url, json={'expected_revision': 1}).json() == first
    assert recipient.post(url, json={'expected_revision': 2}).json() == first
    assert recipient.post(url, json={'expected_revision': 9}).status_code == 409


def test_create_revalidates_directory_and_accept_revalidates_members(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    url = f'/work-items/{work_id}/internal-requests'
    assert logged_in_client.post(url, json={**body, 'expected_directory_revision': 1}).status_code == 409
    assert logged_in_client.post(url, json={**body, 'recipient_member_id': 'missing'}).status_code == 422
    created = logged_in_client.post(url, json=body).json()
    conn.execute('UPDATE members SET disabled_at = ? WHERE member_id = ?', (NOW, created['judgment_member_id']))
    response = recipient.post(f"/internal-requests/{created['request_id']}/accept", json={'expected_revision': 1})
    assert response.status_code == 422
    assert recipient.get('/internal-requests').json()['requests'][0]['state'] == 'pending'


def test_workspace_authentication_and_origin_boundaries(logged_in_client, conn):
    _, work_id, body = setup_request(logged_in_client, conn)
    anonymous = TestClient(logged_in_client.app)
    assert anonymous.get('/internal-requests').status_code == 401
    assert anonymous.post(f'/work-items/{work_id}/internal-requests', json=body).status_code == 401
    assert logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body,
                                 headers={'Origin': 'https://foreign.invalid'}).status_code == 403
    repo.create_session(conn, 'other-scope', NOW)
    conn.execute('BEGIN IMMEDIATE')
    other_work, _ = repo.create_work_item(conn, 'other-scope', title='외부', request='외부', kind='bug_fix',
                                         source_type='manual', now=NOW)
    conn.execute('COMMIT')
    assert logged_in_client.get(f'/work-items/{other_work}/internal-requests').status_code == 404
    assert logged_in_client.post(f'/work-items/{other_work}/internal-requests', json=body).status_code == 404
    assert logged_in_client.post('/internal-requests/missing/accept', json={'expected_revision': 1}).status_code == 404


def test_concurrent_submission_and_accept_are_recorded_once(logged_in_client, conn):
    from concurrent.futures import ThreadPoolExecutor

    recipient, work_id, body = setup_request(logged_in_client, conn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    request_id = responses[0].json()['request_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(lambda _: recipient.post(f'/internal-requests/{request_id}/accept', json={'expected_revision': 1}), range(2)))
    assert all(r.status_code == 200 for r in accepted)
    assert accepted[0].json() == accepted[1].json()
    assert accepted[0].json()['revision'] == 2
    assert len(recipient.get('/internal-requests').json()['requests']) == 1


def test_registered_agent_is_rechecked_at_accept(logged_in_client, conn):
    from .conftest import seed_agents

    recipient, work_id, body = setup_request(logged_in_client, conn)
    seed_agents(conn)
    requester = repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)['member_id']
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id=body['system_id'], request_kind=body['request_kind'],
                 recipient_member_id=body['recipient_member_id'], judgment_member_id=requester,
                 agent_id='agent-codex-mac')
    assert logged_in_client.put('/responsibilities', json=dict(entries=[entry], expected_revision=revision)).status_code == 200
    body['expected_directory_revision'] = revision + 1
    created = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    assert created['agent_id'] == 'agent-codex-mac'
    repo.unregister_session_agent(conn, SESSION, 'agent-codex-mac')
    assert recipient.post(f"/internal-requests/{created['request_id']}/accept", json={'expected_revision': 1}).status_code == 422
    fresh = {**body, 'submission_key': 'request-2', 'expected_directory_revision': repo.get_config_revision(conn, SESSION)}
    assert logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=fresh).status_code == 422
    assert recipient.get('/internal-requests').json()['requests'][0]['state'] == 'pending'


def test_inbox_contains_only_requests_for_current_member(logged_in_client, conn):
    _, work_id, body = setup_request(logged_in_client, conn)
    assert logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).status_code == 200
    outsider = log_in_member(TestClient(logged_in_client.app), display_name='다른 동료')
    assert outsider.get('/internal-requests').json()['requests'] == []


def test_not_responsible_and_explicit_reroute_preserve_history(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    reject_url = f"/internal-requests/{old['request_id']}/not-responsible"
    rejection = dict(expected_revision=1, reason='<담당 아님> 결제 플랫폼 담당에게 확인 필요')
    assert logged_in_client.post(reject_url, json=rejection).status_code == 403
    assert recipient.post(reject_url, json={**rejection, 'reason': ' '}).status_code == 422
    rejected = recipient.post(reject_url, json=rejection)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()['rejection_reason'] == rejection['reason']
    assert recipient.post(reject_url, json=rejection).json() == rejected.json()
    assert recipient.post(reject_url, json={**rejection, 'reason': '다른 이유'}).status_code == 409
    assert recipient.post(f"/internal-requests/{old['request_id']}/accept", json={'expected_revision': 2}).status_code == 409
    target_id = old['requester_member_id']
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id='billing', request_kind='investigation', recipient_member_id=target_id,
                 judgment_member_id=target_id, agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(entries=[entry], expected_revision=revision)).status_code == 200
    reroute_url = f"/internal-requests/{old['request_id']}/reroute"
    selection = dict(expected_revision=2, expected_directory_revision=revision + 1, recipient_member_id=target_id)
    assert recipient.post(reroute_url, json=selection).status_code == 403
    assert logged_in_client.post(reroute_url, json={**selection, 'expected_directory_revision': 1}).status_code == 409
    new = logged_in_client.post(reroute_url, json=selection)
    assert new.status_code == 200, new.text
    assert new.json()['request_id'] != old['request_id']
    assert new.json()['state'] == 'pending' and new.json()['revision'] == 1
    assert new.json()['purpose'] == body['purpose']
    assert new.json()['previous_request_id'] == old['request_id']
    assert new.json()['previous_rejection_reason'] == rejection['reason']
    assert rejected.json()['state'] == 'rejected'
    assert logged_in_client.post(reroute_url, json=selection).json() == new.json()
    assert len(logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests']) == 2
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before
    assert conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 0
    assert logged_in_client.post(f"/internal-requests/{new.json()['request_id']}/accept", json={'expected_revision': 1}).status_code == 200


def test_not_responsible_requires_pending_and_current_revision(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    row = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    url = f"/internal-requests/{row['request_id']}/not-responsible"
    assert recipient.post(url, json=dict(expected_revision=9, reason='다른 담당')).status_code == 409
    assert logged_in_client.post(f"/internal-requests/{row['request_id']}/reroute", json=dict(
        expected_revision=1, expected_directory_revision=body['expected_directory_revision'],
        recipient_member_id=body['recipient_member_id'])).status_code == 409
    assert recipient.post(f"/internal-requests/{row['request_id']}/accept", json={'expected_revision': 1}).status_code == 200
    assert recipient.post(url, json=dict(expected_revision=2, reason='다른 담당')).status_code == 409


def test_concurrent_rejection_and_reroute_are_recorded_once(logged_in_client, conn):
    from concurrent.futures import ThreadPoolExecutor

    recipient, work_id, body = setup_request(logged_in_client, conn)
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    reject_url = f"/internal-requests/{old['request_id']}/not-responsible"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recipient.post(reject_url, json=dict(expected_revision=1, reason='다른 담당')), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id=body['system_id'], request_kind=body['request_kind'],
                 recipient_member_id=old['requester_member_id'], judgment_member_id=old['requester_member_id'], agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(expected_revision=revision, entries=[entry])).status_code == 200
    target = dict(expected_revision=2, expected_directory_revision=revision + 1, recipient_member_id=old['requester_member_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: logged_in_client.post(f"/internal-requests/{old['request_id']}/reroute", json=target), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    assert conn.execute('SELECT COUNT(*) FROM internal_request_rejections').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM internal_requests').fetchone()[0] == 2


def test_accept_and_reject_race_has_one_winner(logged_in_client, conn):
    from concurrent.futures import ThreadPoolExecutor

    recipient, work_id, body = setup_request(logged_in_client, conn)
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    base = f"/internal-requests/{old['request_id']}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        accept = pool.submit(recipient.post, base + '/accept', json={'expected_revision': 1})
        reject = pool.submit(recipient.post, base + '/not-responsible', json=dict(expected_revision=1, reason='다른 담당'))
        assert sorted([accept.result().status_code, reject.result().status_code]) == [200, 409]
    row = logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests'][0]
    assert row['revision'] == 2
    assert (row['state'] == 'accepted') != (row['rejected_at'] is not None)


def test_recipient_asks_and_requester_answers_with_history(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    old = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    base = f"/internal-requests/{old['request_id']}"
    assert recipient.post(base + '/accept', json={'expected_revision': 1}).status_code == 200
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    asked = recipient.post(base + '/questions', json=dict(expected_revision=2, submission_key='question-1', text='오류 발생 시각은?'))
    assert asked.status_code == 200, asked.text
    row = asked.json()
    assert row['waiting_for_information'] is True
    question = row['questions'][0]
    assert question['text'] == '오류 발생 시각은?' and question['asked_by_member_id'] == old['recipient_member_id']
    assert question['answer'] is None and question['revision'] == 1
    answer = logged_in_client.post(base + f"/questions/{question['question_id']}/answer", json=dict(expected_revision=1, text='오후 3시'))
    assert answer.status_code == 200, answer.text
    assert answer.json()['waiting_for_information'] is False
    answered = answer.json()['questions'][0]
    assert answered['answer'] == '오후 3시' and answered['answered_by_member_id'] == old['requester_member_id']
    assert answered['revision'] == 2
    assert answered['answered_at'] is not None
    assert answer.json()['revision'] == 2  # 질문·답변 버전과 요청 버전은 별개
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before
    assert conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 0


def test_information_permissions_stale_inputs_replay_and_next_question(logged_in_client, conn):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    row = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    base = f"/internal-requests/{row['request_id']}"
    question = dict(expected_revision=2, submission_key='q-1', text='시각?')
    assert recipient.post(base + '/questions', json=question).status_code == 409
    assert recipient.post(base + '/accept', json={'expected_revision': 1}).status_code == 200
    assert logged_in_client.post(base + '/questions', json=question).status_code == 403
    assert recipient.post(base + '/questions', json={**question, 'text': ' '}).status_code == 422
    assert recipient.post(base + '/questions', json={**question, 'command': 'run'}).status_code == 422
    assert recipient.post(base + '/questions', json={**question, 'expected_revision': 9}).status_code == 409
    first = recipient.post(base + '/questions', json=question).json()
    assert recipient.post(base + '/questions', json=question).json() == first
    assert recipient.post(base + '/questions', json={**question, 'text': '다른 질문'}).status_code == 409
    assert recipient.post(base + '/questions', json={**question, 'submission_key': 'q-2'}).status_code == 409
    qid = first['questions'][0]['question_id']
    answer_url = base + f'/questions/{qid}/answer'
    answer = dict(expected_revision=1, text='15시')
    assert recipient.post(answer_url, json=answer).status_code == 403
    assert logged_in_client.post(answer_url, json={**answer, 'expected_revision': 9}).status_code == 409
    assert logged_in_client.post(answer_url, json={**answer, 'text': ' '}).status_code == 422
    assert logged_in_client.post(base + '/questions/missing/answer', json=answer).status_code == 404
    replied = logged_in_client.post(answer_url, json=answer).json()
    assert logged_in_client.post(answer_url, json=answer).json() == replied
    assert logged_in_client.post(answer_url, json={**answer, 'text': '16시'}).status_code == 409
    assert recipient.post(base + '/questions', json={**question, 'submission_key': 'q-2', 'text': '로그 ID?'}).status_code == 200
    history = logged_in_client.get('/internal-requests').json()['requests'][0]
    assert [q['text'] for q in history['questions']] == ['시각?', '로그 ID?']
    assert history['waiting_for_information'] is True
    assert all('submission_key' not in q for q in history['questions'])
    outsider = log_in_member(TestClient(logged_in_client.app), display_name='질문과 무관한 동료')
    assert outsider.post(answer_url, json=answer).status_code == 403
    assert outsider.get('/internal-requests').json()['requests'] == []


def test_concurrent_information_question_and_answer_are_recorded_once(logged_in_client, conn):
    from concurrent.futures import ThreadPoolExecutor

    recipient, work_id, body = setup_request(logged_in_client, conn)
    row = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    base = f"/internal-requests/{row['request_id']}"
    recipient.post(base + '/accept', json={'expected_revision': 1})
    question = dict(expected_revision=2, submission_key='concurrent', text='시각?')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recipient.post(base + '/questions', json=question), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    qid = results[0].json()['questions'][0]['question_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: logged_in_client.post(base + f'/questions/{qid}/answer', json=dict(expected_revision=1, text='15시')), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    assert len(results[0].json()['questions']) == 1


def _received(conn) -> list:
    return conn.execute("SELECT * FROM notifications WHERE event = 'internal_request_received' ORDER BY rowid").fetchall()


def test_created_and_rerouted_requests_notify_the_recipient_once(logged_in_client, conn):
    """사내 요청이 생기면(생성·재전달) 받는 사람에게 `internal_request_received` 한 번 — 같은 접수 키 재전송은 같은
    요청이라 알림도 하나(phase 22 step 8)."""
    from workflow.adapters.secret_store import NOTIFY_WEBHOOK_URL
    logged_in_client.app.state.secrets.write(NOTIFY_WEBHOOK_URL, 'https://hooks.example/shared-token')
    recipient, work_id, body = setup_request(logged_in_client, conn)
    url = f'/work-items/{work_id}/internal-requests'
    old = logged_in_client.post(url, json=body).json()
    assert logged_in_client.post(url, json=body).json() == old
    (row,) = _received(conn)
    assert (row['dedupe_key'], row['recipient_member_id'], row['task_id']) == (
        f"internal_request_received:{old['request_id']}:shared", body['recipient_member_id'], None)
    assert row['content'].startswith('[Runloom] 요청 받음 — 결제 오류: ')
    assert 'shared-token' not in row['content']
    recipient.post(f"/internal-requests/{old['request_id']}/not-responsible", json=dict(expected_revision=1, reason='팀 다름'))
    target_id = old['requester_member_id']
    revision = repo.get_config_revision(conn, SESSION)
    entry = dict(system_id='billing', request_kind='investigation', recipient_member_id=target_id,
                 judgment_member_id=target_id, agent_id=None)
    assert logged_in_client.put('/responsibilities', json=dict(entries=[entry], expected_revision=revision)).status_code == 200
    reroute_url = f"/internal-requests/{old['request_id']}/reroute"
    selection = dict(expected_revision=2, expected_directory_revision=revision + 1, recipient_member_id=target_id)
    new = logged_in_client.post(reroute_url, json=selection).json()
    assert logged_in_client.post(reroute_url, json=selection).json() == new
    assert [(r['dedupe_key'], r['recipient_member_id']) for r in _received(conn)] == [
        (f"internal_request_received:{old['request_id']}:shared", body['recipient_member_id']),
        (f"internal_request_received:{new['request_id']}:shared", target_id)]
