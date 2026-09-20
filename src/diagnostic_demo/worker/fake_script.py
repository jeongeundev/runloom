"""`FakeModelClient` 용 fixture 기반 대본 — 테스트·로컬 e2e 전용. `DIAG_MODEL=fake` 를 명시해야만 쓰인다.

대본은 PRD "예상 조사 순서"(실패 실행 → 직전 정상 실행 → 응답·로그 → 문서)대로 도구를 부르고, 마지막에 CONTRACT 5절과
같은 초안을 낸다. 초안 내용은 docs/CONTRACT.md 5·6절과 같아야 하며 `tests/diagnostic_demo/worker/test_fake_script.py`
가 이를 확인한다. 이 파일은 실제 모델의 동작을 대신하지 않는다 — 제품 경로(`DIAG_MODEL=openai`)에서는 읽히지 않는다.
"""

from copy import deepcopy

from diagnostic_demo.worker.model import DiagnosisDraft, ModelTurn, ToolCall

_FINDINGS_COMMON = [
    {
        "claim": "실패 실행과 직전 정상 실행은 같은 코드 버전 report-base에서 실행되었습니다.",
        "evidence_refs": [
            {"evidence_id": "run-daily-0919-0900", "version": "1", "location": "$.code_version"},
            {"evidence_id": "run-daily-0920-0900", "version": "1", "location": "$.code_version"},
        ],
    },
    {
        "claim": "정상 응답의 목록은 items에 있고, 실패 응답의 목록은 data.records에 있으며 items는 없습니다.",
        "evidence_refs": [
            {"evidence_id": "response-before", "version": "1", "location": "$.items"},
            {"evidence_id": "response-after", "version": "1", "location": "$.data.records"},
        ],
    },
]

READY_DRAFT: dict = {
    "outcome": "ready_for_handoff",
    "summary": "조회는 성공했으나, 제공자가 목록 위치를 items에서 data.records로 옮긴 뒤 기존 변환부가 items만 읽어 "
               "보고서 생성이 중단되었습니다.",
    "findings": [
        *_FINDINGS_COMMON,
        {
            "claim": "실패 실행은 HTTP 200으로 응답을 받은 뒤 변환 단계에서 MISSING_RECORDS_FIELD로 실패했고 "
                     "보고서를 만들지 않았습니다.",
            "evidence_refs": [
                {"evidence_id": "log-daily-0920", "version": "1", "location": "lines:1-2"},
                {"evidence_id": "log-daily-0920", "version": "1", "location": "lines:4-4"},
            ],
        },
        {
            "claim": "제공자 안내는 2026-09-20 00:00+09:00부터 목록 위치를 items에서 data.records로 옮기고 "
                     "행 필드는 유지한다고 명시합니다.",
            "evidence_refs": [
                {"evidence_id": "upstream-response-change", "version": "1", "location": "$.machine.effective_at"},
                {"evidence_id": "upstream-response-change", "version": "1", "location": "$.machine.new_path"},
            ],
        },
        {
            "claim": "보고서 계약은 두 경로를 모두 지원하고 목록 누락은 오류로 처리하도록 요구합니다.",
            "evidence_refs": [
                {"evidence_id": "daily-report-contract", "version": "1", "location": "$.machine.supported_paths"},
                {"evidence_id": "daily-report-contract", "version": "1", "location": "$.machine.empty_list_policy"},
            ],
        },
    ],
    "diagnosis": {
        "code": "response_path_changed",
        "baseline_run_id": "daily-0919-0900",
        "failed_run_id": "daily-0920-0900",
        "old_path": "$.items",
        "new_path": "$.data.records",
        "change_document": {"evidence_id": "upstream-response-change", "version": "1"},
        "report_contract": {"evidence_id": "daily-report-contract", "version": "1"},
    },
    "repair_request": {
        "target_component": "report_transformer",
        "change": "items와 data.records 중 정확히 하나에 있는 목록을 처리합니다.",
        "preserve": "날짜·행 순서·건수·합계 계산과 과거 응답(items) 재처리를 유지합니다.",
        "checks": [
            "변경 응답으로 수정 전 실패를 재현하는 테스트가 먼저 실패함",
            "수정 후 변경 응답에서 2026-09-19 보고서가 생성되고 합계가 입력 행에서 계산한 값과 일치함",
            "구형 응답과 빈 배열·누락·두 경로 동시 존재 사례가 계약대로 처리됨",
        ],
    },
    "missing_information": [],
}

NEEDS_INFORMATION_DRAFT: dict = {
    "outcome": "needs_information",
    "summary": "응답의 목록 위치 차이(items → data.records)는 확인했으나, 제공자 변경 안내를 읽지 못해 새 경로의 의미와 "
               "적용 시각을 확인할 수 없습니다. 형식 변경 가능성이 있으나 새 입력 계약 확인이 필요합니다.",
    "findings": [
        *_FINDINGS_COMMON,
        {
            "claim": "실패 실행은 HTTP 200으로 응답을 받은 뒤 변환 단계에서 MISSING_RECORDS_FIELD로 실패했습니다.",
            "evidence_refs": [{"evidence_id": "log-daily-0920", "version": "1", "location": "lines:1-2"}],
        },
    ],
    "diagnosis": None,
    "repair_request": None,
    "missing_information": [
        {
            "code": "evidence_unavailable",
            "description": "제공자 변경 안내(upstream-response-change) 조회가 access_denied로 실패해 새 경로의 의미·적용 시각·"
                           "유지 필드를 확인할 수 없습니다.",
            "evidence_id": "upstream-response-change",
        }
    ],
}

_READS = (
    "run-daily-0919-0900",
    "response-before",
    "response-after",
    "log-daily-0920",
    "upstream-response-change",
    "daily-report-contract",
)


def _script(reads: tuple[str, ...], draft: dict) -> list[ModelTurn]:
    calls: list[tuple[str, dict]] = [
        ("get_run", {"run_id": "daily-0920-0900"}),
        ("list_runs", {"workflow_id": "daily-report", "before": "2026-09-20T09:00:00+09:00",
                       "status": "succeeded", "limit": 1}),
        *(("read_evidence", {"evidence_id": evidence_id, "version": "1"}) for evidence_id in reads),
    ]
    turns = [
        ModelTurn([ToolCall(f"call-{n}", name, dict(arguments))], None, 1500 + 800 * n, 60, f"fake-{n}")
        for n, (name, arguments) in enumerate(calls, 1)
    ]
    n = len(turns) + 1
    turns.append(ModelTurn([], DiagnosisDraft.model_validate(deepcopy(draft)), 1500 + 800 * n, 900, f"fake-{n}"))
    return turns


def fixture_script() -> list[ModelTurn]:
    """get_run → list_runs → read_evidence ×6 → CONTRACT 5절 초안. 호출 9회."""
    return _script(_READS, READY_DRAFT)


def needs_information_script() -> list[ModelTurn]:
    """변경 안내를 읽지 않은 경우 — CONTRACT 6절 초안. 검증기는 undecidable 로 둔다."""
    return _script(tuple(r for r in _READS if r != "upstream-response-change"), NEEDS_INFORMATION_DRAFT)
