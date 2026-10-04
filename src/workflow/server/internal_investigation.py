"""요청의 조사 실행과 검토된 결과 반환. 실행 조건은 기존 단계 착수 경로를 사용한다."""
import json
import secrets
from sqlite3 import Connection
from pydantic import ValidationError
from workflow.adapters import repo, internal_request_store as store
from workflow.adapters.errors import ArtifactMissing, NotFound
from workflow.contracts.internal_request import InternalInvestigationStart, InternalResultReturn, InternalJudgmentRequest, InternalJudgmentResponse
from workflow.contracts.v1 import ExecutionRequest, GenericResult
from workflow.server import work_actions


def start(conn: Connection, app, member, request_id: str, body: InternalInvestigationStart, *, now: str) -> dict:
    row, created = store.start_investigation(conn, member.session_id, member.member_id, request_id, body, now=now)
    if created:
        work_actions.start_stage(conn, app.state.store, app.state.settings, repo.get_task(conn, row['investigation_task_id']),
                                 session_id=member.session_id, now=now, strict=False, secrets=app.state.secrets)
    return store.get(conn, member.session_id, request_id)


def reviewed_result(conn: Connection, artifact_store, row: dict, execution_id: str) -> tuple[str, str]:
    task = repo.get_task(conn, row['investigation_task_id']) if row['investigation_task_id'] else None
    executions = repo.list_executions(conn, task['task_id']) if task else []
    if (task is None or task['review_decision'] != 'approve' or task['status'] != '완료' or not executions
            or executions[-1]['execution_id'] != execution_id):
        raise store.RequestProblem(409, 'result_not_reviewed', '최신 조사 결과를 먼저 검토·승인하세요.')
    execution = executions[-1]
    verdict = repo.get_verdict(conn, execution_id)
    if execution['status'] != 'result_ready' or verdict is None or json.loads(verdict['verdict_json'])['outcome'] != 'passed':
        raise store.RequestProblem(409, 'result_not_valid', '계약 검증을 통과한 조사 결과가 필요합니다.')
    try:
        result = GenericResult.model_validate_json(repo.read_artifact(conn, artifact_store, execution['result_artifact_id']))
        request = ExecutionRequest.model_validate_json(execution['request_json'])
    except (ValidationError, ValueError, NotFound, ArtifactMissing):
        raise store.RequestProblem(409, 'result_not_valid', '조사 결과를 읽을 수 없습니다.') from None
    if (result.execution_id, result.task_id, result.kind) != (execution_id, task['task_id'], task['kind']) or (
            request.kind_spec is None or result.outcome not in request.kind_spec.outcomes):
        raise store.RequestProblem(409, 'result_not_valid', '조사 결과의 실행 참조와 계약이 일치하지 않습니다.')
    return execution['result_artifact_id'], result.summary


def return_result(conn: Connection, artifact_store, member, request_id: str, body: InternalResultReturn, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = store.get(conn, member.session_id, request_id)
        store._recipient(row, member.member_id)
        if row['returned_at'] is not None:
            result = store.record_return(conn, member.session_id, member.member_id, request_id,
                                         expected_revision=body.expected_revision, execution_id=body.execution_id,
                                         artifact_id=row['returned_artifact_id'], summary=row['returned_summary'], now=now)
        else:
            artifact_id, summary = reviewed_result(conn, artifact_store, row, body.execution_id)
            result = store.record_return(conn, member.session_id, member.member_id, request_id,
                                         expected_revision=body.expected_revision, execution_id=body.execution_id,
                                         artifact_id=artifact_id, summary=summary, now=now)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def request_judgment(conn: Connection, artifact_store, member, request_id: str, body: InternalJudgmentRequest, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = store.get(conn, member.session_id, request_id)
        store._recipient(row, member.member_id)
        previous = conn.execute('SELECT * FROM internal_request_judgments WHERE request_id = ? AND submission_key = ?',
                                (request_id, body.submission_key)).fetchone()
        if previous is not None:
            if (previous['execution_id'], previous['issue']) != (body.execution_id, body.issue):
                raise store.RequestProblem(409, 'judgment_conflict', '같은 접수 키로 다른 판단을 요청할 수 없습니다.')
        else:
            if row['revision'] != body.expected_revision or row['returned_at'] is not None or row['waiting_for_information']:
                raise store.RequestProblem(409, 'stale_request', '최신 요청과 정보 답변을 확인하세요.')
            person = repo.get_member(conn, member.session_id, row['judgment_member_id'])
            if person is None or person['disabled_at'] is not None:
                raise store.RequestProblem(422, 'invalid_judge', '지정 판단 담당자가 비활성 상태입니다.')
            artifact_id, summary = reviewed_result(conn, artifact_store, row, body.execution_id)
            versions = json.dumps(store.information_versions(row))
            if conn.execute('SELECT 1 FROM internal_request_judgments WHERE request_id = ? AND execution_id = ?'
                            ' AND information_versions_json = ?', (request_id, body.execution_id, versions)).fetchone():
                raise store.RequestProblem(409, 'judgment_conflict', '같은 결과와 정보에 대한 판단이 이미 요청되었습니다.')
            conn.execute('INSERT INTO internal_request_judgments (judgment_id, request_id, submission_key, execution_id,'
                         ' artifact_id, result_summary, information_versions_json, issue, asked_by_member_id,'
                         ' judgment_member_id, asked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (f'ij-{secrets.token_hex(8)}', request_id, body.submission_key, body.execution_id, artifact_id,
                          summary, versions, body.issue, member.member_id, row['judgment_member_id'], now))
        result = store.get(conn, member.session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def respond_judgment(conn: Connection, artifact_store, member, request_id: str, judgment_id: str,
                     body: InternalJudgmentResponse, *, now: str) -> dict:
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = store.get(conn, member.session_id, request_id)
        judgment = next((j for j in row['judgments'] if j['judgment_id'] == judgment_id), None)
        if judgment is None:
            raise store.RequestProblem(404, 'not_found', '이 요청의 판단을 찾을 수 없습니다.')
        if judgment['judgment_member_id'] != member.member_id:
            raise store.RequestProblem(403, 'forbidden', '지정 판단 담당자만 응답할 수 있습니다.')
        if judgment['responded_at'] is not None:
            if (judgment['decision'], judgment['reason']) != (body.decision, body.reason) or body.expected_revision not in (1, 2):
                raise store.RequestProblem(409, 'judgment_conflict', '이미 응답했거나 판단 상태가 변경되었습니다.')
        else:
            if (body.expected_revision != judgment['revision'] or row['returned_at'] is not None
                    or row['waiting_for_information'] or row['judgments'][-1]['judgment_id'] != judgment_id
                    or judgment['information_versions'] != store.information_versions(row)):
                raise store.RequestProblem(409, 'stale_judgment', '판단할 결과나 추가 정보가 변경되었습니다.')
            reviewed_result(conn, artifact_store, row, judgment['execution_id'])
            conn.execute('UPDATE internal_request_judgments SET decision = ?, reason = ?, responded_by_member_id = ?,'
                         ' responded_at = ?, revision = 2 WHERE judgment_id = ?',
                         (body.decision, body.reason, member.member_id, now, judgment_id))
        result = store.get(conn, member.session_id, request_id)
        conn.execute('COMMIT')
        return result
    except BaseException:
        conn.execute('ROLLBACK')
        raise
