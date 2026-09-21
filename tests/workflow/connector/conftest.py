"""connector 테스트 공용 — 중앙 API 를 흉내 내는 `FakeCentral` (httpx.MockTransport).

`workflow.server` 를 띄우지 않는다. CONTRACT 2·3·4절의 응답 규칙(claim 반복 반환, seq 검증·gap·conflict,
산출물 소유·해시 검사)만 재현하며, 연결 끊김은 `reachable = False` 로 흉내 낸다.
"""

import hashlib
import json
import re
import secrets
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from workflow.connector import state
from workflow.connector.client import CentralClient
from workflow.connector.config import ConnectorPaths
from workflow.contracts.v1 import (
    ArtifactMeta,
    AttachmentRef,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
    InputRef,
    KindSpec,
)

TOKEN = "wfc_" + "t" * 43
CONNECTOR_ID = "conn-mac-01"
BASE_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
NOW = "2026-09-20T01:00:00Z"

_STATUS_AFTER = {
    "accepted": "accepted",
    "started": "running",
    "progress": "running",
    "result_ready": "result_ready",
    "failed": "failed",
}


def _error(status: int, code: str, message: str, field=None, details=None) -> httpx.Response:
    return httpx.Response(
        status, json={"code": code, "message": message, "field": field, "details": details}
    )


def _parse_multipart(request: httpx.Request) -> tuple[str, bytes]:
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    message = BytesParser(policy=HTTP).parsebytes(raw)
    parts = {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
    }
    return parts["meta"].decode(), parts["file"]


class FakeCentral:
    def __init__(self, token: str = TOKEN, connector_id: str = CONNECTOR_ID):
        self.token = token
        self.connector_id = connector_id
        self.reachable = True
        self.connect_codes: dict[str, str] = {}
        self.assignments: list[ExecutionRequest] = []
        self.current: ExecutionRequest | None = None
        self.executions: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.allowed: dict[str, set[str]] = {}
        self.claims = 0
        self.heartbeats: list[dict] = []
        self.registrations: list[dict] = []
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- 준비 ---------------------------------------------------------------------

    def add_artifact(
        self, execution_id: str, kind: str, data: bytes, content_type: str = "application/json",
        name: str | None = None,
    ) -> str:
        artifact_id = f"art-{secrets.token_hex(4)}"
        self.artifacts[artifact_id] = {
            "execution_id": execution_id, "kind": kind, "name": name or f"{kind}.bin",
            "content_type": content_type, "sha256": hashlib.sha256(data).hexdigest(), "data": data,
        }
        return artifact_id

    def assign(self, request: ExecutionRequest, downloadable: set[str] = frozenset()) -> None:
        self.assignments.append(request)
        self.executions[request.execution_id] = {"status": "queued", "last_seq": 0, "events": {}}
        self.allowed[request.execution_id] = set(downloadable)

    def events_of(self, execution_id: str) -> list[dict]:
        events = self.executions[execution_id]["events"]
        return [events[seq] for seq in sorted(events)]

    def artifacts_of(self, execution_id: str) -> dict[str, dict]:
        """kind → 산출물 (같은 kind 가 여럿이면 마지막)."""
        return {
            a["kind"]: a for a in self.artifacts.values() if a["execution_id"] == execution_id
        }

    def forget_events_after(self, execution_id: str, seq: int) -> None:
        """서버 측 손실(백업 복원 등) 흉내: seq 이후 이벤트를 지운다 → 다음 이벤트는 sequence_gap."""
        execution = self.executions[execution_id]
        for s in [s for s in execution["events"] if s > seq]:
            del execution["events"][s]
        execution["last_seq"] = seq

    # --- 처리 ---------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/connector/exchange":
            return self._exchange(json.loads(request.content))
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return _error(401, "unauthenticated", "유효한 연결 토큰이 필요합니다.")
        if path == "/connector/claim":
            return self._claim(json.loads(request.content))
        if path == "/connector/heartbeat":
            self.heartbeats.append(json.loads(request.content))
            return httpx.Response(200, json={})
        if path == "/connector/registrations":
            self.registrations.append(json.loads(request.content))
            return httpx.Response(200, json={"agent_id": "agent-codex-mac"})
        if match := re.fullmatch(r"/executions/([^/]+)/events", path):
            return self._event(match.group(1), json.loads(request.content))
        if match := re.fullmatch(r"/executions/([^/]+)/artifacts", path):
            return self._upload(match.group(1), request)
        if match := re.fullmatch(r"/executions/([^/]+)/artifacts/([^/]+)", path):
            return self._download(match.group(1), match.group(2))
        return _error(404, "not_found", path)

    def _exchange(self, body: dict) -> httpx.Response:
        connector_id = self.connect_codes.pop(body.get("connect_code"), None)
        if connector_id is None:
            return _error(404, "not_found", "연결 코드를 찾을 수 없습니다.", field="connect_code")
        return httpx.Response(200, json={"connector_id": connector_id, "token": self.token})

    def _claim(self, body: dict) -> httpx.Response:
        self.claims += 1
        if body.get("connector_id") != self.connector_id:
            return _error(403, "forbidden", "connector_id 불일치", field="connector_id")
        if self.current is None and self.assignments:
            self.current = self.assignments.pop(0)
        if self.current is None:
            return httpx.Response(204)
        return httpx.Response(200, json=self.current.model_dump(mode="json"))

    def _event(self, execution_id: str, body: dict) -> httpx.Response:
        execution = self.executions.get(execution_id)
        if execution is None:
            return _error(404, "not_found", f"execution {execution_id}을 찾을 수 없습니다.")
        event = ExecutionEvent.model_validate(body)
        stored = execution["events"].get(event.seq)
        if stored is not None:
            if stored == body:
                return self._ack(execution_id)
            return _error(409, "event_conflict", f"seq {event.seq}은 다른 내용으로 이미 저장되었습니다.", "seq")
        expected = execution["last_seq"] + 1
        if event.seq != expected:
            return _error(
                409, "sequence_gap", f"seq {expected}가 먼저 필요합니다.", "seq", {"expected_seq": expected}
            )
        status = execution["status"]
        if status in ("result_ready", "failed") or (event.type == "progress" and status != "running"):
            return _error(409, "invalid_transition", f"{status} 상태에서는 {event.type}를 받을 수 없습니다.",
                          "type", {"current_status": status})
        if event.type == "result_ready":
            owner = self.artifacts.get(event.data.result_artifact_id, {}).get("execution_id")
            if owner != execution_id:
                return _error(409, "invalid_transition", "결과 산출물이 없습니다.", "type",
                              {"reason": "result_artifact_missing"})
        execution["events"][event.seq] = body
        execution["last_seq"] = event.seq
        execution["status"] = _STATUS_AFTER[event.type]
        if event.type == "accepted" and self.current and self.current.execution_id == execution_id:
            self.current = None
        return self._ack(execution_id)

    def _ack(self, execution_id: str) -> httpx.Response:
        execution = self.executions[execution_id]
        return httpx.Response(200, json={
            "execution_id": execution_id, "last_event_seq": execution["last_seq"],
            "status": execution["status"],
        })

    def _upload(self, execution_id: str, request: httpx.Request) -> httpx.Response:
        if execution_id not in self.executions:
            return _error(404, "not_found", f"execution {execution_id}을 찾을 수 없습니다.")
        meta_text, data = _parse_multipart(request)
        meta = ArtifactMeta.model_validate_json(meta_text)
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != meta.sha256 or len(data) != meta.size:
            return _error(422, "hash_mismatch", "본문 해시가 meta.sha256 과 다릅니다.", "sha256")
        for artifact_id, artifact in self.artifacts.items():
            if (artifact["execution_id"], artifact["kind"], artifact["sha256"]) == (execution_id, meta.kind, sha256):
                return httpx.Response(200, json=self._created(artifact_id))
        artifact_id = self.add_artifact(execution_id, meta.kind, data, meta.content_type, meta.name)
        return httpx.Response(201, json=self._created(artifact_id))

    def _created(self, artifact_id: str) -> dict:
        artifact = self.artifacts[artifact_id]
        return {
            "artifact_id": artifact_id, "kind": artifact["kind"], "sha256": artifact["sha256"],
            "size": len(artifact["data"]),
        }

    def _download(self, execution_id: str, artifact_id: str) -> httpx.Response:
        artifact = self.artifacts.get(artifact_id)
        if execution_id not in self.executions or artifact is None:
            return _error(404, "not_found", "없음")
        if artifact["execution_id"] != execution_id and artifact_id not in self.allowed.get(execution_id, ()):
            return _error(403, "forbidden", f"{artifact_id}은 {execution_id}에서 내려받을 수 없습니다.")
        return httpx.Response(200, content=artifact["data"], headers={"content-type": artifact["content_type"]})


# --- fixture --------------------------------------------------------------------------


def make_request(execution_id: str = "exec-fix-001", task_id: str = "fix-daily-0920",
                 input_artifact_ids: list[str] | None = None) -> ExecutionRequest:
    return ExecutionRequest.model_validate({
        "contract_version": 1,
        "execution_id": execution_id,
        "task_id": task_id,
        "kind": "code_change",
        "agent_id": "agent-codex-mac",
        "task_revision": 1,
        "request": "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고 최소 수정하세요.",
        "input_artifact_ids": input_artifact_ids if input_artifact_ids is not None else ["art-handoff-001"],
        "target": {
            "local_registration_id": "local-demo-report",
            "base_commit": BASE_COMMIT,
            "verification_profile_id": "vp-pytest",
        },
    })


RESPONSE_AFTER = json.dumps({
    "report_date": "2026-09-19",
    "data": {"records": [{"team": "운영", "completed": 12, "pending": 3}]},
}, ensure_ascii=False).encode()
LOG_0920 = b"2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900 stage=fetch http_status=200\n"


DIAGNOSIS_RESULT = json.dumps({"contract_version": 1, "outcome": "ready_for_handoff", "summary": "(테스트 진단 결과)"},
                              ensure_ascii=False).encode()


def _input(fake: FakeCentral, source: str, kind: str, data: bytes, content_type: str, name: str) -> InputRef:
    artifact_id = fake.add_artifact(source, kind, data, content_type, name=name)
    return InputRef(kind=kind, artifact_id=artifact_id, sha256=hashlib.sha256(data).hexdigest(),
                    content_type=content_type)


def assign_with_handoff(fake: FakeCentral, request: ExecutionRequest | None = None, *,
                        tamper: bool = False) -> ExecutionRequest:
    """A 실행(exec-diagnose-001)이 만든 인계 묶음(manifest + 입력 diagnosis_result + 첨부 2개)을 두고 B 요청을
    배정한다 — 워커 `assemble_handoff` 가 내장 규칙으로 만드는 것과 같은 모양. `tamper=True` 면 첨부 하나의 실제
    바이트를 manifest 해시와 다르게 둔다."""
    source = "exec-diagnose-001"
    diagnosis = _input(fake, source, "diagnosis_result", DIAGNOSIS_RESULT, "application/json", "diagnosis_result.json")
    response_id = fake.add_artifact(source, "evidence", RESPONSE_AFTER, name="response-after.json")
    log_id = fake.add_artifact(source, "evidence", LOG_0920, "text/plain", name="log-daily-0920.txt")
    attachments = [
        AttachmentRef(evidence_id="response-after", version="1", content_type="application/json",
                      artifact_id=response_id, sha256=hashlib.sha256(RESPONSE_AFTER).hexdigest()),
        AttachmentRef(evidence_id="log-daily-0920", version="1", content_type="text/plain",
                      artifact_id=log_id, sha256=hashlib.sha256(LOG_0920).hexdigest()),
    ]
    if tamper:
        fake.artifacts[log_id]["data"] = LOG_0920 + b"tampered\n"
    bundle = HandoffBundle(contract_version=1, source_execution_id=source, source_kind="diagnosis",
                           source_result_artifact_id=diagnosis.artifact_id, inputs=[diagnosis],
                           attachments=attachments)
    bundle_id = fake.add_artifact(source, "handoff_bundle", bundle.model_dump_json().encode(), name="handoff.json")
    request = request or make_request(input_artifact_ids=[bundle_id])
    if bundle_id not in request.input_artifact_ids:
        request = request.model_copy(update={"input_artifact_ids": [bundle_id]})
    fake.assign(request, {bundle_id, diagnosis.artifact_id, response_id, log_id})
    return request


# --- 사용자 정의 종류 (CONTRACT 11절 `review`) ------------------------------------------------

REVIEW_SPEC = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"],
    instructions="인계 디렉터리의 diff 와 code_change_result 를 읽고 변경이 진단의 repair_request 를 충족하는지 검토하세요. "
                 "저장소를 수정하지 마세요.",
    builtin=False,
)
DIFF = b"--- a/daily_report/transformer.py\n+++ b/daily_report/transformer.py\n@@ -1 +1 @@\n-items\n+records\n"
CODE_CHANGE_RESULT = json.dumps({"contract_version": 1, "outcome": "ready_for_review", "summary": "(테스트 수정 결과)"},
                                ensure_ascii=False).encode()
TEST_LOG_AFTER = b"exit_code=0\n2 passed\n"


def make_local_request(execution_id: str = "exec-review-001", task_id: str = "review-daily-0920",
                       input_artifact_ids: list[str] | None = None, kind_spec: KindSpec = REVIEW_SPEC,
                       local_registration_id: str = "local-demo-report") -> ExecutionRequest:
    """내장이 아닌 종류(`review`)의 요청 — target 은 `LocalTarget`, `kind_spec` 은 서버가 채운 값."""
    return ExecutionRequest.model_validate({
        "contract_version": 1,
        "execution_id": execution_id,
        "task_id": task_id,
        "kind": kind_spec.kind,
        "agent_id": "agent-claude-mac",
        "task_revision": 1,
        "request": "인계된 diff 와 코드 수정 결과를 검토하고 승인 여부를 판단해 주세요.",
        "input_artifact_ids": input_artifact_ids if input_artifact_ids is not None else ["art-handoff-002"],
        "target": {"local_registration_id": local_registration_id},
        "kind_spec": kind_spec.model_dump(),
    })


def assign_with_generic_handoff(fake: FakeCentral, request: ExecutionRequest | None = None, *,
                                tamper: bool = False, duplicate_kind: bool = False,
                                source_result_in_inputs: bool = True) -> ExecutionRequest:
    """B 실행(exec-fix-001)의 산출물을 규칙 `code_change → review` 의 handoff_kinds [diff, code_change_result,
    test_log_after] 로 모은 인계 묶음(attachments 없음)을 두고 C(`review`) 요청을 배정한다.
    `tamper=True` 면 diff 의 실제 바이트를 manifest 해시와 다르게, `duplicate_kind=True` 면 test_log_after 를 둘,
    `source_result_in_inputs=False` 면 code_change_result 를 inputs 에서 빼고 source_result_artifact_id 로만 둔다."""
    source = "exec-fix-001"
    diff = _input(fake, source, "diff", DIFF, "text/x-diff", "fix.diff")
    result = _input(fake, source, "code_change_result", CODE_CHANGE_RESULT, "application/json",
                    "code_change_result.json")
    log = _input(fake, source, "test_log_after", TEST_LOG_AFTER, "text/plain", "pytest-after.txt")
    inputs = [diff, result, log] if source_result_in_inputs else [diff, log]
    if duplicate_kind:
        inputs.append(_input(fake, source, "test_log_after", TEST_LOG_AFTER + b"(2)\n", "text/plain", "again.txt"))
    if tamper:
        fake.artifacts[diff.artifact_id]["data"] = DIFF + b"tampered\n"
    bundle = HandoffBundle(contract_version=1, source_execution_id=source, source_kind="code_change",
                           source_result_artifact_id=result.artifact_id, inputs=inputs, attachments=[])
    bundle_id = fake.add_artifact(source, "handoff_bundle", bundle.model_dump_json().encode(), name="handoff.json")
    request = request or make_local_request(input_artifact_ids=[bundle_id])
    if bundle_id not in request.input_artifact_ids:
        request = request.model_copy(update={"input_artifact_ids": [bundle_id]})
    fake.assign(request, {bundle_id, result.artifact_id, *(i.artifact_id for i in inputs)})
    return request


@pytest.fixture
def fake() -> FakeCentral:
    return FakeCentral()


@pytest.fixture
def client(fake: FakeCentral) -> CentralClient:
    return CentralClient("http://central.test", TOKEN, transport=fake.transport())


@pytest.fixture
def paths(tmp_path) -> ConnectorPaths:
    home = tmp_path / "connector-home"
    return ConnectorPaths(
        home=home, state_db=home / "state.sqlite", token_file=home / "token.json", log_dir=home / "logs"
    )


@pytest.fixture
def state_conn(paths: ConnectorPaths):
    paths.home.mkdir(parents=True, exist_ok=True)
    conn = state.connect(paths.state_db)
    state.init_schema(conn)
    yield conn
    conn.close()
