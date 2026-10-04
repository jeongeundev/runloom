"""사내 요청 생성·수락. 원업무를 변경하거나 실행을 예약하지 않는다."""
import secrets
import json
from sqlite3 import Connection, Row
from workflow.adapters import repo, responsibility_store
from workflow.adapters.errors import NextStepHandled
from workflow.contracts.internal_request import InternalRequestCreate, InternalInvestigationStart, InternalRequestReject, InternalRequestReroute, InternalInformationQuestion, InternalInformationAnswer
from workflow.contracts.responsibility import Responsibility
from workflow.contracts.v1 import Capability, NextInternalRequest, TriageCandidates
from workflow.domain.selection import Candidate, select_agent


_SELECT = (
    'SELECT r.*, i.task_id AS investigation_task_id, i.returned_execution_id, i.returned_artifact_id,'
    ' i.returned_summary, i.returned_at, i.returned_by_member_id, x.reason AS rejection_reason,'
    ' x.rejected_at, x.new_request_id, previous.request_id AS previous_request_id,'
    ' previous.reason AS previous_rejection_reason FROM internal_requests r'
    ' LEFT JOIN internal_request_investigations i ON i.request_id = r.request_id'
    ' LEFT JOIN internal_request_rejections x ON x.request_id = r.request_id'
    ' LEFT JOIN internal_request_rejections previous ON previous.new_request_id = r.request_id'
)


class RequestProblem(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def public(conn: Connection, row: Row) -> dict:
    result = {key: row[key] for key in row.keys() if key not in ('session_id', 'submission_key')}
    if result['rejected_at'] is not None:
        result['state'] = 'rejected'
    result['questions'] = [{key: q[key] for key in q.keys() if key != 'submission_key'} for q in conn.execute(
        'SELECT * FROM internal_request_questions WHERE request_id = ? ORDER BY rowid',
        (row['request_id'],))]
    result['waiting_for_information'] = any(q['answered_at'] is None for q in result['questions'])
    result['judgments'] = []
    for judgment in conn.execute('SELECT * FROM internal_request_judgments WHERE request_id = ? ORDER BY rowid',
                                 (row['request_id'],)):
        record = {key: judgment[key] for key in judgment.keys() if key not in ('submission_key', 'information_versions_json')}
        record['information_versions'] = json.loads(judgment['information_versions_json'])
        result['judgments'].append(record)
    resumption = conn.execute('SELECT * FROM internal_request_resumptions WHERE request_id = ?',
                              (row['request_id'],)).fetchone()
    result['resumption'] = dict(resumption) if resumption is not None else None
    return result


def list_for_work(conn: Connection, session_id: str, work_item_id: str) -> list[dict]:
    return [public(conn, row) for row in conn.execute(
        _SELECT + ' WHERE r.session_id = ? AND r.work_item_id = ? ORDER BY r.created_at, r.request_id',
        (session_id, work_item_id))]


def list_for_member(conn: Connection, session_id: str, member_id: str) -> list[dict]:
    return [public(conn, row) for row in conn.execute(
        _SELECT + ' WHERE r.session_id = ? AND (r.requester_member_id = ? OR r.recipient_member_id = ?'
        ' OR EXISTS (SELECT 1 FROM internal_request_judgments j WHERE j.request_id = r.request_id AND j.judgment_member_id = ?))'
        ' ORDER BY r.created_at DESC, r.request_id', (session_id, member_id, member_id, member_id))]


def create(conn: Connection, session_id: str, requester_id: str, work_item_id: str,
           body: InternalRequestCreate, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        result = _create(conn, session_id, requester_id, work_item_id, body, now=now)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


STALE_DIRECTORY_NEXT_STEP = '담당 범위 표가 바뀌었습니다 — [무시] 뒤 직접 요청하세요.'


def create_from_next_step(conn: Connection, session_id: str, member_id: str, work_item_id: str,
                          body: NextInternalRequest, *, triage_id: str, now: str) -> dict:
    """결과 뒤 판단 [제안대로] 사내 요청 — 요청자 = 누른 멤버, 접수 키 `next_step:<triage_id>`, `created_by_triage_id`.
    판단 시점 후보 항목과 지금 항목(같은 키)의 판단 담당·조사 에이전트가 다르거나 항목이 없으면 409 `stale_directory`
    (담당표 revision 은 워크스페이스 설정 번호 전체라 항목으로 비교한다). 처리 `accepted`·업무 상태 재계산과 한 트랜잭션 —
    이미 처리됐으면 NextStepHandled."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        log = conn.execute('SELECT candidates_json FROM triage_logs WHERE triage_id = ?', (triage_id,)).fetchone()
        key = (body.system_id, body.request_kind, body.recipient_member_id)
        proposed = next((r for r in TriageCandidates.model_validate_json(log['candidates_json']).responsibilities
                         if (r.system_id, r.request_kind, r.recipient_member_id) == key), None)
        current = next((e for e in responsibility_store.list_entries(conn, session_id)
                        if (e.system_id, e.request_kind, e.recipient_member_id) == key), None)
        if proposed is None or current is None or (proposed.judgment_member_id, proposed.agent_id) != (
                current.judgment_member_id, current.agent_id):
            raise RequestProblem(409, 'stale_directory', STALE_DIRECTORY_NEXT_STEP)
        result = _create(conn, session_id, member_id, work_item_id, InternalRequestCreate(
            system_id=body.system_id, request_kind=body.request_kind, recipient_member_id=body.recipient_member_id,
            expected_directory_revision=repo.get_config_revision(conn, session_id),
            submission_key=f'next_step:{triage_id}', purpose=body.purpose,
        ), now=now, created_by_triage_id=triage_id)
        if not repo.record_next_step_handling(conn, triage_id, handling='accepted', member_id=member_id, now=now):
            raise NextStepHandled(triage_id)
        repo.refresh_work_status(conn, work_item_id, now=now)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def _create(conn: Connection, session_id: str, requester_id: str, work_item_id: str,
            body: InternalRequestCreate, *, now: str, created_by_triage_id: str | None = None) -> dict:
    if repo.get_work_item(conn, session_id, work_item_id) is None:
        raise RequestProblem(404, 'not_found', '원래 업무를 찾을 수 없습니다.')
    previous = conn.execute(
        _SELECT + ' WHERE r.session_id = ? AND r.requester_member_id = ? AND r.submission_key = ?',
        (session_id, requester_id, body.submission_key)).fetchone()
    if previous is not None:
        if (previous['work_item_id'], previous['system_id'], previous['request_kind'],
            previous['recipient_member_id'], previous['purpose']) != (
                work_item_id, body.system_id, body.request_kind, body.recipient_member_id, body.purpose):
            raise RequestProblem(409, 'submission_conflict', '같은 접수 키로 다른 요청을 보낼 수 없습니다.')
        result = public(conn, previous)
    else:
        revision = repo.get_config_revision(conn, session_id)
        if revision != body.expected_directory_revision:
            raise RequestProblem(409, 'stale_directory', '담당 범위 표가 변경되었습니다. 다시 조회하세요.')
        entry = next((e for e in responsibility_store.list_entries(conn, session_id)
                      if (e.system_id, e.request_kind, e.recipient_member_id)
                      == (body.system_id, body.request_kind, body.recipient_member_id)), None)
        if entry is None:
            raise RequestProblem(422, 'invalid_recipient', '등록된 담당 후보를 선택하세요.')
        problem = responsibility_store.entry_problem(conn, session_id, entry)
        if problem:
            raise RequestProblem(422, 'invalid_recipient', problem)
        request_id = f'ir-{secrets.token_hex(8)}'
        conn.execute(
            'INSERT INTO internal_requests (request_id, session_id, work_item_id, requester_member_id,'
            ' recipient_member_id, judgment_member_id, system_id, request_kind, agent_id, directory_revision,'
            ' submission_key, purpose, state, revision, created_at, created_by_triage_id)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (request_id, session_id, work_item_id, requester_id, entry.recipient_member_id,
             entry.judgment_member_id, entry.system_id, entry.request_kind, entry.agent_id, revision,
             body.submission_key, body.purpose, 'pending', 1, now, created_by_triage_id))
        result = public(conn, conn.execute(_SELECT + ' WHERE r.request_id = ?', (request_id,)).fetchone())
    return result


def accept(conn: Connection, session_id: str, member_id: str, request_id: str,
           expected_revision: int, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute(_SELECT + ' WHERE r.session_id = ? AND r.request_id = ?',
                           (session_id, request_id)).fetchone()
        if row is None:
            raise RequestProblem(404, 'not_found', '요청을 찾을 수 없습니다.')
        if row['rejected_at'] is not None:
            raise RequestProblem(409, 'rejected_request', '담당 아님으로 반려한 요청입니다.')
        if row['recipient_member_id'] != member_id:
            raise RequestProblem(403, 'forbidden', '지정된 수신자만 수락할 수 있습니다.')
        entry = Responsibility(**{key: row[key] for key in
                                  ('system_id', 'request_kind', 'recipient_member_id', 'judgment_member_id', 'agent_id')})
        problem = responsibility_store.entry_problem(conn, session_id, entry)
        if problem:
            raise RequestProblem(422, 'invalid_recipient', problem)
        if row['state'] == 'accepted' and expected_revision in (1, 2):
            result = public(conn, row)
        else:
            if expected_revision != row['revision'] or row['state'] != 'pending':
                raise RequestProblem(409, 'stale_request', '요청 상태가 변경되었습니다. 다시 조회하세요.')
            conn.execute("UPDATE internal_requests SET state = 'accepted', revision = revision + 1, accepted_at = ?"
                         ' WHERE request_id = ?', (now, request_id))
            result = public(conn, conn.execute(_SELECT + ' WHERE r.request_id = ?', (request_id,)).fetchone())
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def get(conn: Connection, session_id: str, request_id: str) -> dict:
    row = conn.execute(_SELECT + ' WHERE r.session_id = ? AND r.request_id = ?', (session_id, request_id)).fetchone()
    if row is None:
        raise RequestProblem(404, 'not_found', '요청을 찾을 수 없습니다.')
    return public(conn, row)


def _recipient(row: dict, member_id: str) -> None:
    if row['recipient_member_id'] != member_id:
        raise RequestProblem(403, 'forbidden', '지정된 수신자만 조사·반환할 수 있습니다.')


def reject(conn: Connection, session_id: str, member_id: str, request_id: str,
           body: InternalRequestReject, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        _recipient(row, member_id)
        if row['rejected_at'] is not None:
            if row['rejection_reason'] != body.reason or body.expected_revision not in (1, row['revision']):
                raise RequestProblem(409, 'rejection_conflict', '이미 다른 사유로 반려했거나 상태가 변경되었습니다.')
        else:
            if row['state'] != 'pending' or row['revision'] != body.expected_revision:
                raise RequestProblem(409, 'stale_request', '수락 전의 최신 요청만 반려할 수 있습니다.')
            conn.execute('INSERT INTO internal_request_rejections (request_id, reason, rejected_at) VALUES (?, ?, ?)',
                         (request_id, body.reason, now))
            conn.execute('UPDATE internal_requests SET revision = revision + 1 WHERE request_id = ?', (request_id,))
        result = get(conn, session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def reroute(conn: Connection, session_id: str, member_id: str, request_id: str,
            body: InternalRequestReroute, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        if row['requester_member_id'] != member_id:
            raise RequestProblem(403, 'forbidden', '요청자만 새 담당자를 선택할 수 있습니다.')
        if row['rejected_at'] is None or body.expected_revision != row['revision']:
            raise RequestProblem(409, 'stale_request', '반려된 최신 요청을 확인하세요.')
        if row['new_request_id'] is not None:
            result = get(conn, session_id, row['new_request_id'])
            if result['recipient_member_id'] != body.recipient_member_id:
                raise RequestProblem(409, 'reroute_conflict', '이미 다른 담당자에게 재전달했습니다.')
        else:
            if body.recipient_member_id == row['recipient_member_id']:
                raise RequestProblem(422, 'invalid_recipient', '다른 담당 후보를 선택하세요.')
            selection = InternalRequestCreate(system_id=row['system_id'], request_kind=row['request_kind'],
                recipient_member_id=body.recipient_member_id, expected_directory_revision=body.expected_directory_revision,
                submission_key=f'reroute-{secrets.token_hex(16)}', purpose=row['purpose'])
            result = _create(conn, session_id, member_id, row['work_item_id'], selection, now=now)
            conn.execute('UPDATE internal_request_rejections SET new_request_id = ? WHERE request_id = ?',
                         (result['request_id'], request_id))
            result = get(conn, session_id, result['request_id'])
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def _target_problem(conn: Connection, session_id: str, row: dict) -> str | None:
    entry = Responsibility(**{key: row[key] for key in
                              ('system_id', 'request_kind', 'recipient_member_id', 'judgment_member_id', 'agent_id')})
    return responsibility_store.entry_problem(conn, session_id, entry)


def ask(conn: Connection, session_id: str, member_id: str, request_id: str,
        body: InternalInformationQuestion, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        _recipient(row, member_id)
        previous = conn.execute('SELECT * FROM internal_request_questions WHERE request_id = ? AND submission_key = ?',
                                (request_id, body.submission_key)).fetchone()
        if previous is not None:
            if previous['text'] != body.text:
                raise RequestProblem(409, 'question_conflict', '같은 접수 키로 다른 질문을 보낼 수 없습니다.')
        else:
            if row['state'] != 'accepted' or row['returned_at'] is not None or body.expected_revision != row['revision']:
                raise RequestProblem(409, 'stale_request', '수락한 최신 요청의 반환 전에 질문할 수 있습니다.')
            if row['waiting_for_information']:
                raise RequestProblem(409, 'information_pending', '이전 질문의 답변을 먼저 기다리세요.')
            conn.execute('INSERT INTO internal_request_questions (question_id, request_id, submission_key, text,'
                         ' asked_by_member_id, asked_at) VALUES (?, ?, ?, ?, ?, ?)',
                         (f'iq-{secrets.token_hex(8)}', request_id, body.submission_key, body.text, member_id, now))
        result = get(conn, session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def answer(conn: Connection, session_id: str, member_id: str, request_id: str, question_id: str,
           body: InternalInformationAnswer, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        if row['requester_member_id'] != member_id:
            raise RequestProblem(403, 'forbidden', '원래 요청자만 정보 질문에 답할 수 있습니다.')
        question = next((q for q in row['questions'] if q['question_id'] == question_id), None)
        if question is None:
            raise RequestProblem(404, 'not_found', '이 요청의 질문을 찾을 수 없습니다.')
        if question['answered_at'] is not None:
            if question['answer'] != body.text or body.expected_revision not in (1, 2):
                raise RequestProblem(409, 'answer_conflict', '이미 답변했거나 질문 상태가 변경되었습니다.')
        else:
            if body.expected_revision != question['revision']:
                raise RequestProblem(409, 'stale_question', '질문 상태가 변경되었습니다.')
            conn.execute('UPDATE internal_request_questions SET answer = ?, answered_by_member_id = ?, answered_at = ?,'
                         ' revision = 2 WHERE question_id = ?', (body.text, member_id, now, question_id))
        result = get(conn, session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def start_investigation(conn: Connection, session_id: str, member_id: str, request_id: str,
                        body: InternalInvestigationStart, *, now: str) -> tuple[dict, bool]:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        _recipient(row, member_id)
        if row['state'] != 'accepted':
            raise RequestProblem(409, 'not_accepted', '요청을 먼저 수락하세요.')
        if row['investigation_task_id'] is not None:
            task = repo.get_task(conn, row['investigation_task_id'])
            required = Capability.model_validate_json(task['required_capability_json'])
            if task['kind'] != body.kind or list(required.scope.values()) != [body.scope_value]:
                raise RequestProblem(409, 'investigation_conflict', '이미 다른 조사 작업이 연결되어 있습니다.')
            if body.expected_revision not in (2, row['revision']):
                raise RequestProblem(409, 'stale_request', '요청 상태가 변경되었습니다.')
            result, created = row, False
        else:
            if row['revision'] != body.expected_revision:
                raise RequestProblem(409, 'stale_request', '요청 상태가 변경되었습니다.')
            if row['waiting_for_information']:
                raise RequestProblem(409, 'information_pending', '정보 질문의 답변을 먼저 기다리세요.')
            problem = _target_problem(conn, session_id, row)
            if problem:
                raise RequestProblem(422, 'invalid_recipient', problem)
            spec = repo.get_kind(conn, session_id, body.kind)
            if spec is None or spec.output_kind != 'generic_result' or spec.input_kinds:
                raise RequestProblem(422, 'invalid_kind', '입력 산출물이 없는 등록된 읽기 전용 종류를 선택하세요.')
            agent = repo.get_agent(conn, row['agent_id']) if row['agent_id'] else None
            if agent is None or agent['connection_type'] != 'local' or not agent['local_registration_id']:
                raise RequestProblem(422, 'invalid_agent', '요청에 등록된 로컬 조사 에이전트가 필요합니다.')
            capability = Capability(code=spec.capability_code, scope={spec.scope_key: body.scope_value})
            task_id = f'investigation-{secrets.token_hex(8)}'
            selection = select_agent(task_id, capability, [Candidate(
                agent_id=agent['agent_id'], capabilities=tuple(Capability.model_validate(c) for c in
                                                              json.loads(agent['capabilities_json'])))],
                                     mode='manual', chosen_agent_id=agent['agent_id'])
            if selection.status != 'selected':
                raise RequestProblem(422, 'invalid_scope', '등록된 에이전트의 능력과 범위가 일치해야 합니다.')
            work_id, _ = repo.create_work_item(conn, session_id, title=f"조사 요청 · {row['system_id']}",
                                              request=row['purpose'], kind=spec.kind, source_type='manual',
                                              assignee_type='member', assignee_id=member_id, now=now)
            repo.set_work_requester(conn, work_id, member_id)
            repo._insert_task_row(conn, dict(task_id=task_id, session_id=session_id, title=f"조사 · {row['system_id']}",
                request=row['purpose'], kind=spec.kind, required_capability=capability.model_dump(),
                selection_mode='manual', chosen_agent_id=agent['agent_id'], run_mode='manual', completion_mode='review',
                criteria=[], revision=1, target={'local_registration_id': agent['local_registration_id']},
                status='대기', status_reason='조사 착수 대기'), now, work_item_id=work_id)
            repo.save_selection(conn, selection)
            conn.execute('UPDATE tasks SET start_pending_at = ? WHERE task_id = ?', (now, task_id))
            conn.execute('INSERT INTO internal_request_investigations (request_id, task_id, created_at) VALUES (?, ?, ?)',
                         (request_id, task_id, now))
            conn.execute('UPDATE internal_requests SET revision = revision + 1 WHERE request_id = ?', (request_id,))
            result, created = get(conn, session_id, request_id), True
        conn.execute('COMMIT')
        return result, created
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def investigation_problem(conn: Connection, task: Row) -> str | None:
    row = conn.execute(_SELECT + ' WHERE i.task_id = ?', (task['task_id'],)).fetchone()
    if row is None:
        return None
    if conn.execute('SELECT 1 FROM internal_request_questions WHERE request_id = ? AND answered_at IS NULL',
                    (row['request_id'],)).fetchone() is not None:
        return '정보 질문의 답변을 기다리고 있습니다.'
    problem = _target_problem(conn, task['session_id'], row)
    if problem:
        return problem
    if row['returned_at'] is not None:
        return '이미 반환한 조사입니다.'
    agent = repo.get_agent(conn, row['agent_id'])
    spec = repo.get_kind(conn, task['session_id'], task['kind'])
    required = Capability.model_validate_json(task['required_capability_json'])
    if spec is None or spec.output_kind != 'generic_result' or spec.input_kinds:
        return '조사 종류의 읽기 전용 계약이 변경되었습니다.'
    if (agent is None or agent['connection_type'] != 'local' or task['chosen_agent_id'] != row['agent_id']
            or json.loads(task['target_json']).get('local_registration_id') != agent['local_registration_id']
            or required.code != spec.capability_code or list(required.scope) != [spec.scope_key]
            or required.model_dump() not in json.loads(agent['capabilities_json'])):
        return '요청에 고정된 조사 에이전트·등록·능력·범위가 변경되었습니다.'
    return None


def information_context(conn: Connection, task_id: str) -> str:
    questions = conn.execute(
        'SELECT q.text, q.answer FROM internal_request_questions q JOIN internal_request_investigations i'
        ' ON i.request_id = q.request_id WHERE i.task_id = ? AND q.answered_at IS NOT NULL ORDER BY q.rowid',
        (task_id,))
    return ''.join(f"\n\n추가 질문: {q['text']}\n요청자 답변: {q['answer']}" for q in questions)


def information_versions(row: dict) -> list:
    return [[q['question_id'], q['revision']] for q in row['questions']]


def judgment_problem(conn: Connection, session_id: str, row: dict, execution_id: str) -> str | None:
    if not row['judgments']:
        return None
    judgment = row['judgments'][-1]
    if judgment['execution_id'] != execution_id or judgment['information_versions'] != information_versions(row):
        return '조사 결과나 추가 정보가 변경되었습니다. 새 판단을 요청하세요.'
    person = repo.get_member(conn, session_id, judgment['judgment_member_id'])
    if person is None or person['disabled_at'] is not None:
        return '지정 판단 담당자가 비활성 상태입니다.'
    if judgment['decision'] is None:
        return '지정 판단 담당자의 응답을 기다리고 있습니다.'
    if judgment['decision'] != 'approve':
        return '지정 판단 담당자가 결과 반환을 거절했습니다.'
    return None


def record_return(conn: Connection, session_id: str, member_id: str, request_id: str, *, expected_revision: int,
                  execution_id: str, artifact_id: str, summary: str, now: str) -> dict:
    # 결과 검증은 호출자가 연 쓰기 트랜잭션 안에서 수행한다.
    if not conn.in_transaction:
        raise RuntimeError('결과 반환은 트랜잭션 안에서 기록해야 합니다.')
    row = get(conn, session_id, request_id)
    _recipient(row, member_id)
    if row['returned_at'] is not None:
        if row['returned_execution_id'] != execution_id or expected_revision not in (3, row['revision']):
            raise RequestProblem(409, 'return_conflict', '이미 다른 결과가 반환되었거나 요청 상태가 변경되었습니다.')
        return row
    if row['waiting_for_information']:
        raise RequestProblem(409, 'information_pending', '정보 질문의 답변을 먼저 기다리세요.')
    problem = judgment_problem(conn, session_id, row, execution_id)
    if problem:
        raise RequestProblem(409, 'judgment_pending', problem)
    if row['revision'] != expected_revision or row['investigation_task_id'] is None:
        raise RequestProblem(409, 'stale_request', '요청 상태가 변경되었습니다.')
    conn.execute('UPDATE internal_request_investigations SET returned_execution_id = ?, returned_artifact_id = ?,'
                 ' returned_summary = ?, returned_at = ?, returned_by_member_id = ? WHERE request_id = ?',
                 (execution_id, artifact_id, summary, now, member_id, request_id))
    conn.execute('UPDATE internal_requests SET revision = revision + 1 WHERE request_id = ?', (request_id,))
    return get(conn, session_id, request_id)


def resume(conn: Connection, session_id: str, member_id: str, request_id: str,
           body: InternalInformationAnswer, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = get(conn, session_id, request_id)
        if row['requester_member_id'] != member_id:
            raise RequestProblem(403, 'not_requester', '원래 요청자만 업무 재개를 기록할 수 있습니다.')
        if row['returned_at'] is None or body.expected_revision != row['revision']:
            raise RequestProblem(409, 'stale_request', '반환된 결과와 요청 버전을 확인하세요.')
        existing = row['resumption']
        if existing is not None:
            if existing['text'] != body.text:
                raise RequestProblem(409, 'resumption_conflict', '이미 기록한 업무 재개 내용을 바꿀 수 없습니다.')
        else:
            conn.execute('INSERT INTO internal_request_resumptions '
                         '(request_id, execution_id, artifact_id, text, resumed_by_member_id, resumed_at) '
                         'VALUES (?, ?, ?, ?, ?, ?)',
                         (request_id, row['returned_execution_id'], row['returned_artifact_id'], body.text, member_id, now))
        result = get(conn, session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise
