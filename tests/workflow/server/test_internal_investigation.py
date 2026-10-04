import json
from workflow.adapters import repo
from workflow.contracts.v1 import KindSpec
from workflow.server.auth import utc_now
from workflow.server.worker import Worker
from .conftest import SESSION, exchange
from .test_internal_request_api import setup_request
from .test_worker import seed_generic_result

KIND = KindSpec(kind='investigate', label='오류 조사', capability_code='investigate', scope_key='system_id',
                input_kinds=[], output_kind='generic_result', outcomes=['reported', 'needs_information'],
                instructions='공유된 합성 기록만 읽고 원인과 불확실성을 보고하세요.', builtin=False)


def investigation_request(logged_in_client, conn, *, judgment_member_id=None):
    recipient, work_id, body = setup_request(logged_in_client, conn)
    repo.insert_kind(conn, SESSION, KIND, utc_now())
    connector_id, _ = exchange(logged_in_client, conn)
    repo.upsert_agent(conn, dict(agent_id='investigator', name='조사 에이전트', owner_scope='personal',
                                connection_type='local', connector_id=connector_id, local_registration_id='registered-local',
                                capabilities=[{'code': 'investigate', 'scope': {'system_id': 'billing'}}],
                                connection_state='online', last_seen_at=utc_now()))
    repo.register_session_agent(conn, SESSION, 'investigator', utc_now())
    revision = repo.get_config_revision(conn, SESSION)
    requester_id = repo.get_work_item(conn, SESSION, work_id)['assignee_id']
    entry = dict(system_id='billing', request_kind='investigation', recipient_member_id=body['recipient_member_id'],
                 judgment_member_id=judgment_member_id or requester_id, agent_id='investigator')
    assert logged_in_client.put('/responsibilities', json=dict(entries=[entry], expected_revision=revision)).status_code == 200
    body['expected_directory_revision'] = revision + 1
    created = logged_in_client.post(f'/work-items/{work_id}/internal-requests', json=body).json()
    assert recipient.post(f"/internal-requests/{created['request_id']}/accept", json={'expected_revision': 1}).status_code == 200
    return recipient, work_id, created['request_id']


def test_accepted_request_executes_registered_generic_kind_and_returns_reviewed_result(logged_in_client, conn, store, settings):
    recipient, work_id, request_id = investigation_request(logged_in_client, conn)
    repo.refresh_work_status(conn, work_id, now=utc_now())
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    start = recipient.post(f'/internal-requests/{request_id}/investigation', json=dict(expected_revision=2, kind='investigate', scope_value='billing'))
    assert start.status_code == 200, start.text
    task_id = start.json()['investigation_task_id']
    assert start.json()['revision'] == 3
    task = repo.get_task(conn, task_id)
    assert task['work_item_id'] != work_id
    execution = repo.list_executions(conn, task_id)[0]
    envelope = json.loads(execution['request_json'])
    assert envelope['target'] == {'local_registration_id': 'registered-local'}
    assert envelope['request'].endswith('에러 원인을 확인해 주세요.')
    result_id = seed_generic_result(conn, store, execution['execution_id'], utc_now(), task_id=task_id,
                                    kind='investigate', outcome='reported', summary='합성 기록에서 타임아웃을 확인했습니다.')
    worker = Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now)
    assert worker.tick().generic_checked == 1
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    returned = recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution['execution_id']))
    assert returned.status_code == 200, returned.text
    assert returned.json()['returned_artifact_id'] == result_id
    assert returned.json()['returned_summary'] == '합성 기록에서 타임아웃을 확인했습니다.'
    assert returned.json()['revision'] == 4
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before
    assert logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests'][0]['returned_artifact_id'] == result_id


def test_waiting_investigation_rechecks_capability_before_worker_execution(logged_in_client, conn, store, settings):
    recipient, _, request_id = investigation_request(logged_in_client, conn)
    conn.execute("UPDATE agents SET last_seen_at = '2000-01-01T00:00:00Z' WHERE agent_id = 'investigator'")
    started = recipient.post(f'/internal-requests/{request_id}/investigation', json=dict(expected_revision=2, kind='investigate', scope_value='billing'))
    assert started.status_code == 200
    task_id = started.json()['investigation_task_id']
    assert repo.list_executions(conn, task_id) == []
    conn.execute("UPDATE agents SET last_seen_at = ?, capabilities_json = '[]' WHERE agent_id = 'investigator'", (utc_now(),))
    worker = Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now)
    worker.tick()
    assert repo.list_executions(conn, task_id) == []


def test_only_recipient_starts_and_kind_scope_are_validated_atomically(logged_in_client, conn):
    recipient, _, request_id = investigation_request(logged_in_client, conn)
    url = f'/internal-requests/{request_id}/investigation'
    body = dict(expected_revision=2, kind='investigate', scope_value='billing')
    count = conn.execute('SELECT COUNT(*) FROM work_items').fetchone()[0]
    assert logged_in_client.post(url, json=body).status_code == 403
    assert recipient.post(url, json={**body, 'kind': 'bug_fix'}).status_code == 422
    assert recipient.post(url, json={**body, 'scope_value': 'other-system'}).status_code == 422
    assert recipient.post(url, json={**body, 'expected_revision': 1}).status_code == 409
    assert conn.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == count
    assert conn.execute('SELECT COUNT(*) FROM internal_request_investigations').fetchone()[0] == 0


def reviewed_investigation(logged_in_client, conn, store, settings, *, outcome='reported', judgment_member_id=None):
    recipient, work_id, request_id = investigation_request(logged_in_client, conn, judgment_member_id=judgment_member_id)
    start = recipient.post(f'/internal-requests/{request_id}/investigation', json=dict(expected_revision=2, kind='investigate', scope_value='billing')).json()
    task_id = start['investigation_task_id']
    execution = repo.list_executions(conn, task_id)[0]
    seed_generic_result(conn, store, execution['execution_id'], utc_now(), task_id=task_id,
                        kind='investigate', outcome=outcome, summary='검토할 조사 결과')
    Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now).tick()
    return recipient, work_id, request_id, task_id, execution['execution_id']


def test_result_return_requires_review_valid_verdict_current_execution_and_recipient(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    url = f'/internal-requests/{request_id}/return'
    body = dict(expected_revision=3, execution_id=execution_id)
    assert recipient.post(url, json=body).status_code == 409
    assert logged_in_client.post(url, json=body).status_code == 403
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    assert recipient.post(url, json={**body, 'execution_id': 'other-execution'}).status_code == 409
    assert recipient.post(url, json={**body, 'expected_revision': 2}).status_code == 409
    first = recipient.post(url, json=body)
    assert first.status_code == 200
    assert recipient.post(url, json=body).json() == first.json()
    assert recipient.post(url, json={**body, 'expected_revision': 4}).json() == first.json()
    assert recipient.post(url, json={**body, 'expected_revision': 9}).status_code == 409


def test_failed_contract_verdict_cannot_be_returned_even_if_task_approved(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings, outcome='not_registered')
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    response = recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution_id))
    assert response.status_code == 409 and response.json()['code'] == 'result_not_valid'


def test_concurrent_start_and_return_do_not_duplicate_tasks_or_records(logged_in_client, conn, store, settings):
    from concurrent.futures import ThreadPoolExecutor

    recipient, _, request_id = investigation_request(logged_in_client, conn)
    body = dict(expected_revision=2, kind='investigate', scope_value='billing')
    url = f'/internal-requests/{request_id}/investigation'
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: recipient.post(url, json=body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    task_id = responses[0].json()['investigation_task_id']
    assert len(repo.list_executions(conn, task_id)) == 1
    assert conn.execute('SELECT COUNT(*) FROM internal_request_investigations').fetchone()[0] == 1
    execution_id = repo.list_executions(conn, task_id)[0]['execution_id']
    seed_generic_result(conn, store, execution_id, utc_now(), task_id=task_id, kind='investigate', outcome='reported')
    Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now).tick()
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        returned = list(pool.map(lambda _: recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution_id)), range(2)))
    assert all(r.status_code == 200 for r in returned)
    assert returned[0].json() == returned[1].json()
    assert returned[0].json()['revision'] == 4


def test_owner_approval_is_required_before_investigation_execution(logged_in_client, conn):
    recipient, _, request_id = investigation_request(logged_in_client, conn)
    # 러너는 다른 멤버 소유이며 요청을 받은 사람이 사용 승인을 대신할 수 없다.
    owner = repo.get_work_item(conn, SESSION, repo.get_work_item_by_key(conn, SESSION, 1)['work_item_id'])['assignee_id']
    connector_id = repo.get_agent(conn, 'investigator')['connector_id']
    conn.execute('UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?', (owner, connector_id))
    conn.execute("UPDATE agents SET delegation_policy = 'owner_approval' WHERE agent_id = 'investigator'")
    response = recipient.post(f'/internal-requests/{request_id}/investigation', json=dict(expected_revision=2, kind='investigate', scope_value='billing'))
    assert response.status_code == 200, response.text
    task_id = response.json()['investigation_task_id']
    assert repo.list_executions(conn, task_id) == []
    assert repo.list_owner_approvals(conn, task_id)


def test_review_retry_rechecks_fixed_investigation_agent(logged_in_client, conn, store, settings):
    recipient, _, _, task_id, _ = reviewed_investigation(logged_in_client, conn, store, settings)
    conn.execute("UPDATE agents SET capabilities_json = '[]' WHERE agent_id = 'investigator'")
    response = recipient.post(f'/tasks/{task_id}/review', data={'decision': 'request_changes', 'comment': '다시 확인'})
    assert response.status_code == 409
    assert len(repo.list_executions(conn, task_id)) == 1
    assert repo.active_execution(conn, task_id) is not None


def test_unanswered_information_blocks_start_and_answer_enters_execution_context(logged_in_client, conn):
    recipient, _, request_id = investigation_request(logged_in_client, conn)
    base = f'/internal-requests/{request_id}'
    asked = recipient.post(base + '/questions', json=dict(expected_revision=2, submission_key='context', text='발생 시각?'))
    assert asked.status_code == 200
    task_count = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    selection = dict(expected_revision=2, kind='investigate', scope_value='billing')
    blocked = recipient.post(base + '/investigation', json=selection)
    assert blocked.status_code == 409, blocked.text
    assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == task_count
    question_id = asked.json()['questions'][0]['question_id']
    assert logged_in_client.post(base + f'/questions/{question_id}/answer', json=dict(expected_revision=1, text='15:00')).status_code == 200
    started = recipient.post(base + '/investigation', json=selection)
    assert started.status_code == 200, started.text
    execution = repo.list_executions(conn, started.json()['investigation_task_id'])[0]
    context = json.loads(execution['request_json'])['request']
    assert '발생 시각?' in context and '15:00' in context


def test_information_after_execution_does_not_mutate_execution_and_blocks_return(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    before = dict(repo.get_execution(conn, execution_id))
    base = f'/internal-requests/{request_id}'
    asked = recipient.post(base + '/questions', json=dict(expected_revision=3, submission_key='later', text='추가 정보?'))
    assert asked.status_code == 200
    result = recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id))
    assert result.status_code == 409, result.text
    assert dict(repo.get_execution(conn, execution_id)) == before
    question_id = asked.json()['questions'][0]['question_id']
    assert logged_in_client.post(base + f'/questions/{question_id}/answer', json=dict(expected_revision=1, text='추가 기록')).status_code == 200
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200
    assert recipient.post(base + '/questions', json=dict(expected_revision=4, submission_key='closed', text='새 질문')).status_code == 409


def test_pending_runner_waits_for_information_and_uses_answer(logged_in_client, conn, store, settings):
    recipient, _, request_id = investigation_request(logged_in_client, conn)
    conn.execute("UPDATE agents SET last_seen_at = '2000-01-01T00:00:00Z' WHERE agent_id = 'investigator'")
    base = f'/internal-requests/{request_id}'
    started = recipient.post(base + '/investigation', json=dict(expected_revision=2, kind='investigate', scope_value='billing')).json()
    task_id = started['investigation_task_id']
    asked = recipient.post(base + '/questions', json=dict(expected_revision=3, submission_key='offline', text='고객 요청 ID?')).json()
    conn.execute('UPDATE agents SET last_seen_at = ? WHERE agent_id = ?', (utc_now(), 'investigator'))
    worker = Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now)
    worker.tick()
    assert repo.list_executions(conn, task_id) == []
    question_id = asked['questions'][0]['question_id']
    assert logged_in_client.post(base + f'/questions/{question_id}/answer', json=dict(expected_revision=1, text='synthetic-123')).status_code == 200
    worker.tick()
    execution = repo.list_executions(conn, task_id)[0]
    assert 'synthetic-123' in json.loads(execution['request_json'])['request']


def test_pending_question_blocks_review_retry_without_releasing_result(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    assert recipient.post(f'/internal-requests/{request_id}/questions', json=dict(
        expected_revision=3, submission_key='review-wait', text='추가 로그?')).status_code == 200
    response = recipient.post(f'/tasks/{task_id}/review', data={'decision': 'request_changes', 'comment': '추가 정보 반영'})
    assert response.status_code == 409, response.text
    assert repo.active_execution(conn, task_id)['execution_id'] == execution_id
    assert len(repo.list_executions(conn, task_id)) == 1


def test_designated_judgment_gates_return_and_preserves_original_work(logged_in_client, conn, store, settings):
    recipient, work_id, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    base = f'/internal-requests/{request_id}'
    requested = recipient.post(base + '/judgments', json=dict(expected_revision=3, submission_key='judgment-1',
        execution_id=execution_id, issue='이 조사 결과를 원래 업무에 반환해도 될까요?'))
    assert requested.status_code == 200, requested.text
    judgment = requested.json()['judgments'][0]
    assert judgment['decision'] is None and judgment['revision'] == 1
    assert judgment['result_summary'] == '검토할 조사 결과'
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 409
    response = logged_in_client.post(base + f"/judgments/{judgment['judgment_id']}/respond", json=dict(
        expected_revision=1, decision='approve', reason='근거를 확인했으며 반환해도 됩니다.'))
    assert response.status_code == 200, response.text
    decided = response.json()['judgments'][0]
    assert decided['decision'] == 'approve' and decided['revision'] == 2
    assert decided['responded_by_member_id'] == repo.find_member_by_email(conn, SESSION, 'admin@example.com')['member_id']
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before


def test_judgment_inbox_and_only_designated_member_can_respond(logged_in_client, conn, store, settings):
    from fastapi.testclient import TestClient
    from .conftest import log_in_member

    judge = log_in_member(TestClient(logged_in_client.app), display_name='업무 판단 담당')
    judge_id = next(m['member_id'] for m in repo.list_members(conn, SESSION) if m['display_name'] == '업무 판단 담당')
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(
        logged_in_client, conn, store, settings, judgment_member_id=judge_id)
    assert judge.get('/internal-requests').json()['requests'] == []
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    base = f'/internal-requests/{request_id}'
    requested = recipient.post(base + '/judgments', json=dict(expected_revision=3, submission_key='separate-judge',
        execution_id=execution_id, issue='반환 여부를 확인하세요.')).json()
    assert judge.get('/internal-requests').json()['requests'] == [requested]
    judgment_id = requested['judgments'][0]['judgment_id']
    url = base + f'/judgments/{judgment_id}/respond'
    body = dict(expected_revision=1, decision='approve', reason='검토 완료')
    assert logged_in_client.post(url, json=body).status_code == 403  # 관리자·요청자도 대체 불가
    assert recipient.post(url, json=body).status_code == 403
    assert judge.post(url, json=body).status_code == 200
    repo.disable_member(conn, SESSION, judge_id, now=utc_now())
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 409


def test_judgment_basis_changes_with_information_and_old_approval_cannot_return(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    base = f'/internal-requests/{request_id}'
    selection = dict(expected_revision=3, submission_key='basis-1', execution_id=execution_id, issue='반환 판단')
    first = recipient.post(base + '/judgments', json=selection).json()['judgments'][0]
    reply = dict(expected_revision=1, decision='approve', reason='근거 확인')
    assert logged_in_client.post(base + f"/judgments/{first['judgment_id']}/respond", json=reply).status_code == 200
    question = recipient.post(base + '/questions', json=dict(expected_revision=3, submission_key='new-info', text='추가 로그?')).json()['questions'][0]
    assert logged_in_client.post(base + f"/questions/{question['question_id']}/answer", json=dict(expected_revision=1, text='새 로그')).status_code == 200
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 409
    second = recipient.post(base + '/judgments', json={**selection, 'submission_key': 'basis-2'}).json()['judgments'][-1]
    assert second['information_versions'] != first['information_versions']
    denied = logged_in_client.post(base + f"/judgments/{second['judgment_id']}/respond", json={**reply, 'decision': 'reject', 'reason': '추가 조사 필요'})
    assert denied.status_code == 200
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 409
    assert recipient.post(base + '/judgments', json={**selection, 'submission_key': 'bypass-rejection'}).status_code == 409
    assert denied.json()['judgments'][0]['decision'] == 'approve'
    assert denied.json()['judgments'][1]['decision'] == 'reject'


def test_information_change_invalidates_pending_judgment_and_requires_new_judgment(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    base = f'/internal-requests/{request_id}'
    selection = dict(expected_revision=3, submission_key='old-basis', execution_id=execution_id, issue='반환 판단')
    old = recipient.post(base + '/judgments', json=selection).json()['judgments'][0]
    question = recipient.post(base + '/questions', json=dict(expected_revision=3, submission_key='extra', text='오류 범위?')).json()['questions'][0]
    reply = dict(expected_revision=1, decision='approve', reason='검토 완료')
    assert logged_in_client.post(base + f"/judgments/{old['judgment_id']}/respond", json=reply).status_code == 409
    assert logged_in_client.post(base + f"/questions/{question['question_id']}/answer", json=dict(expected_revision=1, text='합성 결제 사례')).status_code == 200
    assert logged_in_client.post(base + f"/judgments/{old['judgment_id']}/respond", json=reply).status_code == 409
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 409
    current = recipient.post(base + '/judgments', json={**selection, 'submission_key': 'new-basis'}).json()['judgments'][-1]
    assert logged_in_client.post(base + f"/judgments/{current['judgment_id']}/respond", json=reply).status_code == 200
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200


def test_judgment_validation_replay_and_closed_request(logged_in_client, conn, store, settings):
    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    base = f'/internal-requests/{request_id}'
    selection = dict(expected_revision=3, submission_key='validation', execution_id=execution_id, issue='반환 여부')
    assert recipient.post(base + '/judgments', json=selection).status_code == 409  # Task 검토 전
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    assert logged_in_client.post(base + '/judgments', json=selection).status_code == 403
    for invalid in ({**selection, 'issue': ' '}, {**selection, 'command': 'run'}, {**selection, 'submission_key': ' '}):
        assert recipient.post(base + '/judgments', json=invalid).status_code == 422
    assert recipient.post(base + '/judgments', json={**selection, 'expected_revision': 9}).status_code == 409
    assert recipient.post(base + '/judgments', json={**selection, 'execution_id': 'foreign'}).status_code == 409
    first = recipient.post(base + '/judgments', json=selection).json()
    assert recipient.post(base + '/judgments', json=selection).json() == first
    assert recipient.post(base + '/judgments', json={**selection, 'issue': '다른 쟁점'}).status_code == 409
    judgment_id = first['judgments'][0]['judgment_id']
    assert 'submission_key' not in first['judgments'][0]
    reply_url = base + f'/judgments/{judgment_id}/respond'
    reply = dict(expected_revision=1, decision='approve', reason='근거 확인')
    assert logged_in_client.post(base + '/judgments/missing/respond', json=reply).status_code == 404
    assert logged_in_client.post(reply_url, json={**reply, 'expected_revision': 9}).status_code == 409
    for invalid in ({**reply, 'reason': ' '}, {**reply, 'decision': 'grant_access'}, {**reply, 'execution_id': execution_id}):
        assert logged_in_client.post(reply_url, json=invalid).status_code == 422
    result = logged_in_client.post(reply_url, json=reply).json()
    assert logged_in_client.post(reply_url, json=reply).json() == result
    assert logged_in_client.post(reply_url, json={**reply, 'decision': 'reject'}).status_code == 409
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200
    assert recipient.post(base + '/judgments', json={**selection, 'submission_key': 'after-return', 'expected_revision': 4}).status_code == 409
    assert recipient.post(base + '/judgments', json=selection).status_code == 200  # 기존 기록 재시도
    assert logged_in_client.post(reply_url, json=reply).status_code == 200


def test_concurrent_judgment_request_and_response_are_recorded_once(logged_in_client, conn, store, settings):
    from concurrent.futures import ThreadPoolExecutor

    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    base = f'/internal-requests/{request_id}'
    selection = dict(expected_revision=3, submission_key='concurrent-judgment', execution_id=execution_id, issue='반환 판단')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recipient.post(base + '/judgments', json=selection), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    judgment_id = results[0].json()['judgments'][0]['judgment_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: logged_in_client.post(base + f'/judgments/{judgment_id}/respond', json=dict(
            expected_revision=1, decision='approve', reason='근거 확인')), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    assert len(results[0].json()['judgments']) == 1


def test_requester_records_resumption_after_return_without_changing_original_work(logged_in_client, conn, store, settings):
    recipient, work_id, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    base = f'/internal-requests/{request_id}'
    body = dict(expected_revision=4, text='결과를 확인하고 원래 업무를 재개했습니다.')
    assert logged_in_client.post(base + '/resume', json=body).status_code == 409
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200
    assert recipient.post(base + '/return', json=dict(expected_revision=3, execution_id=execution_id)).status_code == 200
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    assert recipient.post(base + '/resume', json=body).status_code == 403
    result = logged_in_client.post(base + '/resume', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['resumption']['text'] == body['text']
    assert result.json()['resumption']['execution_id'] == execution_id
    assert logged_in_client.post(base + '/resume', json=body).json() == result.json()
    assert logged_in_client.post(base + '/resume', json=dict(body, text='다른 기록')).status_code == 409
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before


def test_resumption_validates_input_revision_and_concurrent_replay(logged_in_client, conn, store, settings):
    from concurrent.futures import ThreadPoolExecutor

    recipient, _, request_id, task_id, execution_id = reviewed_investigation(logged_in_client, conn, store, settings)
    recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'})
    recipient.post(f'/internal-requests/{request_id}/return', json=dict(expected_revision=3, execution_id=execution_id))
    path = f'/internal-requests/{request_id}/resume'
    body = dict(expected_revision=4, text='수정 작업 재개')
    for invalid in [dict(body, text=' '), dict(body, text='x' * 4001), dict(body, command='anything'), dict(body, expected_revision='4')]:
        assert logged_in_client.post(path, json=invalid).status_code == 422
    assert logged_in_client.post(path, json=dict(body, expected_revision=3)).status_code == 409
    assert logged_in_client.post('/internal-requests/missing/resume', json=body).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: logged_in_client.post(path, json=body), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert results[0].json() == results[1].json()
    assert results[0].json()['revision'] == 4
    assert recipient.get('/internal-requests').json()['requests'][0]['resumption'] == results[0].json()['resumption']


def test_synthetic_pilot_three_members_from_information_to_judgment_return_and_resumption(logged_in_client, conn, store, settings):
    """통합 흐름 검증. 합성 산출물을 사용하며 실제 CLI·조직 효과를 증명하지 않는다."""
    from fastapi.testclient import TestClient
    from .conftest import log_in_member

    judge = log_in_member(TestClient(logged_in_client.app), display_name='파일럿 판단 담당')
    judge_id = next(m['member_id'] for m in repo.list_members(conn, SESSION) if m['display_name'] == '파일럿 판단 담당')
    recipient, work_id, request_id = investigation_request(logged_in_client, conn, judgment_member_id=judge_id)
    repo.refresh_work_status(conn, work_id, now=utc_now())
    before = dict(repo.get_work_item(conn, SESSION, work_id))
    base = f'/internal-requests/{request_id}'
    accepted = recipient.get('/internal-requests').json()['requests'][0]
    assert len({accepted['requester_member_id'], accepted['recipient_member_id'], judge_id}) == 3
    assert accepted['state'] == 'accepted' and accepted['accepted_at']

    question = dict(expected_revision=2, submission_key='pilot-information', text='오류 시각과 공유 가능한 요청 식별자는?')
    asked = recipient.post(base + '/questions', json=question)
    assert asked.status_code == 200, asked.text
    selection = dict(expected_revision=2, kind='investigate', scope_value='billing')
    assert recipient.post(base + '/investigation', json=selection).status_code == 409
    question_id = asked.json()['questions'][0]['question_id']
    answer = dict(expected_revision=1, text='합성 사례: 15:00 UTC, 요청 synth-billing-001. 실제 고객 자료 없음.')
    answered = logged_in_client.post(base + f'/questions/{question_id}/answer', json=answer)
    assert answered.status_code == 200, answered.text
    assert logged_in_client.post(base + f'/questions/{question_id}/answer', json=answer).json() == answered.json()

    started = recipient.post(base + '/investigation', json=selection)
    assert started.status_code == 200, started.text
    task_id = started.json()['investigation_task_id']
    execution = repo.list_executions(conn, task_id)[0]
    assert repo.get_task(conn, task_id)['work_item_id'] != work_id
    context = json.loads(execution['request_json'])['request']
    assert question['text'] in context and answer['text'] in context
    assert recipient.post(base + '/investigation', json=selection).json() == started.json()
    assert len(repo.list_executions(conn, task_id)) == 1

    summary = '합성 기록에서 결제 API 타임아웃 확인. 운영 원인은 미확인. 다음 행동은 타임아웃 설정 검토.'
    result_id = seed_generic_result(conn, store, execution['execution_id'], utc_now(), task_id=task_id,
                                    kind='investigate', outcome='reported', summary=summary)
    worker = Worker(logged_in_client.app.state.conn_factory, store, None, settings, utc_now)
    assert worker.tick().generic_checked == 1
    return_body = dict(expected_revision=3, execution_id=execution['execution_id'])
    assert recipient.post(base + '/return', json=return_body).status_code == 409
    assert recipient.post(f'/tasks/{task_id}/review', data={'decision': 'approve'}).status_code == 200

    judgment_body = dict(expected_revision=3, submission_key='pilot-judgment', execution_id=execution['execution_id'],
                         issue='운영 원인 미확인이라는 한계를 포함해 결과를 반환해도 되는가?')
    requested = recipient.post(base + '/judgments', json=judgment_body)
    assert requested.status_code == 200, requested.text
    judgment_id = requested.json()['judgments'][0]['judgment_id']
    response_body = dict(expected_revision=1, decision='approve', reason='불확실성과 다음 행동을 포함한 조사 결과 반환 승인.')
    response_path = base + f'/judgments/{judgment_id}/respond'
    assert logged_in_client.post(response_path, json=response_body).status_code == 403
    assert recipient.post(base + '/return', json=return_body).status_code == 409
    decided = judge.post(response_path, json=response_body)
    assert decided.status_code == 200, decided.text
    assert judge.post(response_path, json=response_body).json() == decided.json()

    returned = recipient.post(base + '/return', json=return_body)
    assert returned.status_code == 200, returned.text
    resume_body = dict(expected_revision=4, text='합성 결과를 확인하고 타임아웃 설정 검토 업무를 재개했습니다.')
    assert recipient.post(base + '/resume', json=resume_body).status_code == 403
    resumed = logged_in_client.post(base + '/resume', json=resume_body)
    assert resumed.status_code == 200, resumed.text
    final = resumed.json()
    assert logged_in_client.post(base + '/resume', json=resume_body).json() == final
    assert final['returned_summary'] == summary
    assert final['resumption']['execution_id'] == execution['execution_id']
    assert final['resumption']['artifact_id'] == final['returned_artifact_id'] == result_id
    assert final['questions'][0]['answer'] == answer['text']
    assert final['judgments'][0]['responded_by_member_id'] == judge_id
    assert [r for r in logged_in_client.get(f'/work-items/{work_id}/internal-requests').json()['requests'] if r['request_id'] == request_id] == [final]
    assert dict(repo.get_work_item(conn, SESSION, work_id)) == before
    times = [final['created_at'], final['accepted_at'], final['questions'][0]['asked_at'],
             final['questions'][0]['answered_at'], final['judgments'][0]['asked_at'],
             final['judgments'][0]['responded_at'], final['returned_at'], final['resumption']['resumed_at']]
    assert all(times) and times == sorted(times)
