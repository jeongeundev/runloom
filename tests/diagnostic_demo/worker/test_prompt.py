"""시스템 프롬프트 — 역할·도구 규칙·조사 순서·인용 문법·결론 규칙만. 정답 문장·고정 답변은 넣지 않는다."""

import json

from diagnostic_demo.tools.api import TOOL_SCHEMAS
from diagnostic_demo.worker.prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from tests.diagnostic_demo.conftest import diagnosis_request
from workflow.contracts.v1 import ExecutionRequest


def test_prompt_version():
    assert PROMPT_VERSION == "diag-prompt-v1"


def test_system_prompt_names_every_tool_and_the_investigation_order():
    for schema in TOOL_SCHEMAS:
        assert schema["name"] in SYSTEM_PROMPT
    section = SYSTEM_PROMPT.split("## 조사 순서")[1].split("## ")[0]
    order = [section.index(w) for w in ("실패 실행", "직전 정상 실행", "로그", "문서")]
    assert order == sorted(order)


def test_system_prompt_states_citation_and_conclusion_rules():
    for needle in ("$.", "lines:", "$.machine.", "needs_information", "unsupported_diagnosis",
                   "response_path_changed", "읽은 근거", "evidence_id", "version"):
        assert needle in SYSTEM_PROMPT, needle


def test_system_prompt_has_no_fixed_answer():
    # 데모 정답(경로 이름·오류 코드·적용 시각)은 자료를 읽어야 나온다. 프롬프트에 박지 않는다
    for banned in ("data.records", "$.items", "MISSING_RECORDS_FIELD", "2026-09-20", "report_transformer",
                   "daily-0920-0900", "daily-0919-0900"):
        assert banned not in SYSTEM_PROMPT, banned


def test_user_message_is_the_prd_investigation_request():
    request = ExecutionRequest.model_validate(diagnosis_request())
    body = json.loads(user_message(request))
    assert body == {
        "task_id": request.task_id, "run_id": "daily-0920-0900", "request": request.request,
    }
