"""진단 결과 검증기 — ARCHITECTURE "진단 완료 검증", CONTRACT 5절 끝 "검증기가 확인하는 것".

fixture 는 CONTRACT 5·6절의 DiagnosisResult JSON 을 그대로 파싱하고, 첨부 원문은
Step 3 스펙의 실행 기록·문서 형식과 PRD "데모 데이터와 검증 상세" 의 응답·로그로 만든다.
해시는 fixture 내용에서 다시 계산해 넣는다. 조회 이력은 첨부 8개를 모두 반환한 것으로 둔다.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from workflow.contracts.v1 import DiagnosisResult
from workflow.domain.verification import (
    Check,
    LoadedEvidence,
    TraceEntry,
    Verdict,
    verify_diagnosis,
)

CONTRACT_MD = Path(__file__).resolve().parents[3] / "docs" / "CONTRACT.md"
_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)

COMMON_CHECKS = (
    "attachments_in_trace",
    "refs_in_attachments",
    "attachments_loaded",
    "locations_resolve",
    "outcome_shape",
)
DEMO_CHECKS = (
    "runs_same_workflow_and_version",
    "failed_run_http_ok_then_transform_failed",
    "paths_differ_as_claimed",
    "change_document_matches",
    "change_effective_before_failure",
    "report_contract_supports_both",
    "run_ids_consistent",
)


# --- fixture ------------------------------------------------------------------


def contract_results() -> tuple[dict, dict]:
    """CONTRACT.md 의 DiagnosisResult 블록 2개: (5절 ready_for_handoff, 6절 needs_information)."""
    blocks = [json.loads(m) for m in _FENCE.findall(CONTRACT_MD.read_text(encoding="utf-8"))]
    results = [b for b in blocks if {"outcome", "findings"} <= set(b)]
    assert [r["outcome"] for r in results] == ["ready_for_handoff", "needs_information"]
    return results[0], results[1]


def demo_sources() -> dict[tuple[str, str], tuple[str, object]]:
    """(evidence_id, version) → (content_type, 원문 객체 또는 텍스트). 테스트가 고쳐서 쓴다."""
    rows = [
        {"team": "운영", "completed": 12, "pending": 3},
        {"team": "개발", "completed": 8, "pending": 2},
    ]
    log_0920 = (
        "2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900 stage=fetch event=response_received"
        " http_status=200 response_ref=response-after\n"
        "2026-09-20T09:00:01+09:00 ERROR run_id=daily-0920-0900 stage=transform"
        " component=report_transformer code=MISSING_RECORDS_FIELD expected_path=$.items"
        " observed_root_keys=[report_date,data] response_ref=response-after\n"
        "2026-09-20T09:00:01+09:00 INFO  run_id=daily-0920-0900 stage=render status=skipped"
        " reason=transform_failed\n"
        "2026-09-20T09:00:01+09:00 ERROR run_id=daily-0920-0900 event=run_finished status=failed"
        " report_created=false\n"
    )
    return {
        ("run-daily-0919-0900", "1"): (
            "application/json",
            {
                "run_id": "daily-0919-0900",
                "workflow_id": "daily-report",
                "started_at": "2026-09-19T09:00:00+09:00",
                "finished_at": "2026-09-19T09:00:01+09:00",
                "status": "succeeded",
                "code_version": "report-base",
                "http_status": 200,
                "stages": [
                    {"stage": "fetch", "status": "succeeded"},
                    {"stage": "transform", "status": "succeeded"},
                    {"stage": "render", "status": "succeeded"},
                ],
                "response_ref": {"evidence_id": "response-before", "version": "1"},
                "log_ref": {"evidence_id": "log-daily-0919", "version": "1"},
                "report_ref": {"evidence_id": "report-0918", "version": "1"},
            },
        ),
        ("run-daily-0920-0900", "1"): (
            "application/json",
            {
                "run_id": "daily-0920-0900",
                "workflow_id": "daily-report",
                "started_at": "2026-09-20T09:00:00+09:00",
                "finished_at": "2026-09-20T09:00:01+09:00",
                "status": "failed",
                "code_version": "report-base",
                "http_status": 200,
                "stages": [
                    {"stage": "fetch", "status": "succeeded"},
                    {"stage": "transform", "status": "failed", "error_code": "MISSING_RECORDS_FIELD"},
                    {"stage": "render", "status": "skipped"},
                ],
                "response_ref": {"evidence_id": "response-after", "version": "1"},
                "log_ref": {"evidence_id": "log-daily-0920", "version": "1"},
                "report_ref": None,
            },
        ),
        ("response-before", "1"): (
            "application/json",
            {"report_date": "2026-09-18", "items": [dict(r) for r in rows]},
        ),
        ("response-after", "1"): (
            "application/json",
            {"report_date": "2026-09-19", "data": {"records": [dict(r) for r in rows]}},
        ),
        ("log-daily-0920", "1"): ("text/plain", log_0920),
        ("upstream-response-change", "1"): (
            "application/json",
            {
                "markdown": "# 응답 형식 변경 안내\n\n2026-09-20 00:00 KST 부터 목록 위치가 바뀝니다.",
                "machine": {
                    "workflow_id": "daily-report",
                    "effective_at": "2026-09-20T00:00:00+09:00",
                    "old_path": "$.items",
                    "new_path": "$.data.records",
                    "preserved_fields": ["report_date", "team", "completed", "pending"],
                },
            },
        ),
        ("daily-report-contract", "1"): (
            "application/json",
            {
                "markdown": "# 일일 보고서 계약",
                "machine": {
                    "workflow_id": "daily-report",
                    "supported_paths": ["$.items", "$.data.records"],
                    "required_row_fields": ["team", "completed", "pending"],
                    "empty_list_policy": {
                        "explicit_empty": "zero_rows",
                        "missing": "error",
                        "ambiguous": "error",
                    },
                },
            },
        ),
        ("daily-report-runbook", "1"): (
            "application/json",
            {
                "markdown": "# 일일 보고서 런북",
                "machine": {
                    "workflow_id": "daily-report",
                    "schedule": "09:00 Asia/Seoul",
                    "stages": ["fetch", "transform", "render"],
                    "transformer_component": "report_transformer",
                    "reads_path": "$.items",
                    "on_failure": "no_report_for_date",
                },
            },
        ),
    }


def _encode(content_type: str, source: object) -> bytes:
    if content_type == "text/plain":
        assert isinstance(source, str)
        return source.encode("utf-8")
    return json.dumps(source, ensure_ascii=False, indent=2).encode("utf-8")


def make_demo_attachments(
    sources: dict[tuple[str, str], tuple[str, object]] | None = None,
) -> dict[tuple[str, str], LoadedEvidence]:
    loaded = {}
    for (evidence_id, version), (content_type, source) in (sources or demo_sources()).items():
        content = _encode(content_type, source)
        loaded[(evidence_id, version)] = LoadedEvidence(
            evidence_id=evidence_id,
            version=version,
            content_type=content_type,
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )
    return loaded


def make_trace(attachments: dict[tuple[str, str], LoadedEvidence]) -> list[TraceEntry]:
    """첨부 하나당 read_evidence 호출 하나. 실제 진단 서비스의 순서·도구는 여기서 중요하지 않다."""
    return [
        TraceEntry(
            call_id=f"call-{n}",
            tool="read_evidence",
            input={"evidence_id": key[0], "version": key[1]},
            ok=True,
            returned=(key,),
        )
        for n, key in enumerate(attachments, 1)
    ]


def with_hashes(raw: dict, attachments: dict[tuple[str, str], LoadedEvidence]) -> dict:
    """CONTRACT 의 가상 해시를 fixture 내용의 실제 해시로 바꾼다."""
    result = json.loads(json.dumps(raw))
    for ref in result["attachments"]:
        ref["sha256"] = attachments[(ref["evidence_id"], ref["version"])].sha256
    return result


@pytest.fixture
def attachments():
    return make_demo_attachments()


@pytest.fixture
def trace(attachments):
    return make_trace(attachments)


@pytest.fixture
def handoff_raw(attachments):
    return with_hashes(contract_results()[0], attachments)


@pytest.fixture
def needs_info_raw(attachments):
    return with_hashes(contract_results()[1], attachments)


def _check(verdict: Verdict, code: str) -> Check:
    matches = [c for c in verdict.checks if c.code == code]
    assert len(matches) == 1, f"check {code} 가 {len(matches)}개"
    return matches[0]


def _codes(verdict: Verdict) -> tuple[str, ...]:
    return tuple(c.code for c in verdict.checks)


# --- 정상 ---------------------------------------------------------------------


def test_contract_section_5_passes_every_check(handoff_raw, attachments, trace):
    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "passed"
    assert _codes(verdict) == COMMON_CHECKS + DEMO_CHECKS
    assert all(c.passed for c in verdict.checks), [c for c in verdict.checks if not c.passed]


def test_changed_row_count_still_passes(handoff_raw, trace):
    # 검증기는 20·5 같은 고정 수치를 판정에 쓰지 않는다
    sources = demo_sources()
    sources[("response-after", "1")][1]["data"]["records"][0]["completed"] = 13
    sources[("response-before", "1")][1]["items"][0]["completed"] = 13
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "passed"


def test_verdict_and_checks_are_frozen(handoff_raw, attachments, trace):
    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert isinstance(verdict.checks, tuple)
    with pytest.raises(AttributeError):
        verdict.outcome = "failed"  # type: ignore[misc]


# --- 공통 검사 실패 → failed --------------------------------------------------


def test_attachment_not_in_trace_fails(handoff_raw, attachments, trace):
    trace = [t for t in trace if ("upstream-response-change", "1") not in t.returned]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    check = _check(verdict, "attachments_in_trace")
    assert not check.passed
    assert "upstream-response-change@1" in check.detail


def test_failed_trace_call_does_not_count_as_returned(handoff_raw, attachments, trace):
    trace = [
        TraceEntry(t.call_id, t.tool, t.input, ok=False, returned=t.returned)
        if ("log-daily-0920", "1") in t.returned
        else t
        for t in trace
    ]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    assert not _check(verdict, "attachments_in_trace").passed


def test_ref_not_in_attachments_fails(handoff_raw, attachments, trace):
    handoff_raw["attachments"] = [
        a for a in handoff_raw["attachments"] if a["evidence_id"] != "daily-report-contract"
    ]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    check = _check(verdict, "refs_in_attachments")
    assert not check.passed
    assert "daily-report-contract@1" in check.detail


def test_tampered_hash_in_result_fails(handoff_raw, attachments, trace):
    handoff_raw["attachments"][3]["sha256"] = "0" * 64

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    check = _check(verdict, "attachments_loaded")
    assert not check.passed
    assert "response-after@1" in check.detail


def test_tampered_content_fails(handoff_raw, attachments, trace):
    key = ("response-after", "1")
    original = attachments[key]
    attachments[key] = LoadedEvidence(
        original.evidence_id,
        original.version,
        original.content_type,
        original.sha256,
        original.content + b"\n",
    )

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    assert not _check(verdict, "attachments_loaded").passed


def test_attachment_not_loaded_fails(handoff_raw, attachments, trace):
    del attachments[("daily-report-runbook", "1")]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    check = _check(verdict, "attachments_loaded")
    assert not check.passed
    assert "daily-report-runbook@1" in check.detail


def test_location_typo_fails(handoff_raw, attachments, trace):
    ref = handoff_raw["findings"][1]["evidence_refs"][1]
    assert ref["location"] == "$.data.records"
    ref["location"] = "$.data.record"

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    check = _check(verdict, "locations_resolve")
    assert not check.passed
    assert "response-after@1 $.data.record" in check.detail


def test_array_index_location_resolves(handoff_raw, attachments, trace):
    # 실행 기록의 stages 는 배열 — DIAG_EVAL 관찰 3 에서 모델이 인용한 형태를 검증기가 원문에서 찾는다
    handoff_raw["findings"][2]["evidence_refs"].append(
        {"evidence_id": "run-daily-0920-0900", "version": "1", "location": "$.stages[1].status"}
    )

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "passed"
    assert _check(verdict, "locations_resolve").passed


def test_line_range_beyond_log_fails(handoff_raw, attachments, trace):
    handoff_raw["findings"][2]["evidence_refs"][1]["location"] = "lines:4-5"

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    assert not _check(verdict, "locations_resolve").passed


def test_common_failure_skips_demo_checks(handoff_raw, attachments, trace):
    handoff_raw["attachments"][0]["sha256"] = "0" * 64

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    assert _codes(verdict) == COMMON_CHECKS


# --- needs_information → undecidable ---------------------------------------


def test_contract_section_6_is_undecidable(needs_info_raw, attachments, trace):
    verdict = verify_diagnosis(DiagnosisResult.model_validate(needs_info_raw), attachments, trace)

    assert verdict.outcome == "undecidable"
    assert _codes(verdict) == COMMON_CHECKS
    assert all(c.passed for c in verdict.checks)
    assert "needs_information" in _check(verdict, "outcome_shape").detail


def test_needs_information_with_unread_citation_still_fails(needs_info_raw, attachments, trace):
    # 정보 부족은 보류지만, 읽지 않은 근거 인용은 보류가 아니라 차단이다
    trace = [t for t in trace if ("log-daily-0920", "1") not in t.returned]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(needs_info_raw), attachments, trace)

    assert verdict.outcome == "failed"


# --- 데모 검사 -----------------------------------------------------------------


def test_effective_at_after_failure_is_evidence_conflict(handoff_raw, trace):
    sources = demo_sources()
    sources[("upstream-response-change", "1")][1]["machine"]["effective_at"] = "2026-09-21T00:00:00+09:00"
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    check = _check(verdict, "change_effective_before_failure")
    assert not check.passed
    assert "evidence_conflict" in check.detail


def test_effective_at_before_baseline_is_evidence_conflict(handoff_raw, trace):
    sources = demo_sources()
    sources[("upstream-response-change", "1")][1]["machine"]["effective_at"] = "2026-09-18T00:00:00+09:00"
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert "evidence_conflict" in _check(verdict, "change_effective_before_failure").detail


def test_swapped_paths_fail(handoff_raw, attachments, trace):
    diagnosis = handoff_raw["diagnosis"]
    diagnosis["old_path"], diagnosis["new_path"] = diagnosis["new_path"], diagnosis["old_path"]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "failed"
    assert not _check(verdict, "paths_differ_as_claimed").passed
    assert not _check(verdict, "change_document_matches").passed
    assert _check(verdict, "report_contract_supports_both").passed


def test_same_code_version_required(handoff_raw, trace):
    sources = demo_sources()
    sources[("run-daily-0920-0900", "1")][1]["code_version"] = "report-next"
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "runs_same_workflow_and_version").passed


def test_http_error_instead_of_transform_failure_fails(handoff_raw, trace):
    # 목록 위치는 그대로인데 HTTP 오류로 실패한 실행 → 형식 변경 진단을 재생하면 안 된다
    sources = demo_sources()
    failed = sources[("run-daily-0920-0900", "1")][1]
    failed["http_status"] = 503
    failed["stages"] = [
        {"stage": "fetch", "status": "failed", "error_code": "HTTP_503"},
        {"stage": "transform", "status": "skipped"},
        {"stage": "render", "status": "skipped"},
    ]
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "failed_run_http_ok_then_transform_failed").passed


def test_log_without_transform_error_line_fails(handoff_raw, trace):
    sources = demo_sources()
    log = sources[("log-daily-0920", "1")][1]
    assert isinstance(log, str)
    sources[("log-daily-0920", "1")] = (
        "text/plain",
        log.replace("ERROR run_id=daily-0920-0900 stage=transform", "WARN  run_id=daily-0920-0900 stage=transform"),
    )
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "failed_run_http_ok_then_transform_failed").passed


def test_report_contract_without_new_path_fails(handoff_raw, trace):
    sources = demo_sources()
    sources[("daily-report-contract", "1")][1]["machine"]["supported_paths"] = ["$.items"]
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "report_contract_supports_both").passed


def test_run_record_id_mismatch_fails(handoff_raw, trace):
    sources = demo_sources()
    sources[("run-daily-0919-0900", "1")][1]["run_id"] = "daily-0918-0900"
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "run_ids_consistent").passed


def test_missing_run_record_is_undecidable(handoff_raw, attachments, trace):
    # 기준 실행 기록을 첨부·인용하지 않은 결과: 계약은 지켰지만 데모 판정을 할 수 없다
    handoff_raw["findings"] = handoff_raw["findings"][1:]
    handoff_raw["attachments"] = [
        a for a in handoff_raw["attachments"] if a["evidence_id"] != "run-daily-0919-0900"
    ]
    del attachments[("run-daily-0919-0900", "1")]

    verdict = verify_diagnosis(DiagnosisResult.model_validate(handoff_raw), attachments, trace)

    assert verdict.outcome == "undecidable"
    assert all(_check(verdict, code).passed for code in COMMON_CHECKS)
    check = _check(verdict, "runs_same_workflow_and_version")
    assert not check.passed
    assert "run-daily-0919-0900@1" in check.detail
    assert _check(verdict, "failed_run_http_ok_then_transform_failed").passed


def test_contradiction_wins_over_missing_attachment(handoff_raw):
    # 기준 실행 기록은 없고(보류 사유) 보고서 계약은 새 경로를 지원하지 않는다(모순) → 차단이 우선
    sources = demo_sources()
    del sources[("run-daily-0919-0900", "1")]
    sources[("daily-report-contract", "1")][1]["machine"]["supported_paths"] = ["$.items"]
    attachments = make_demo_attachments(sources)
    handoff_raw["findings"] = handoff_raw["findings"][1:]
    handoff_raw["attachments"] = [
        a for a in handoff_raw["attachments"] if a["evidence_id"] != "run-daily-0919-0900"
    ]
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    assert verdict.outcome == "failed"
    assert not _check(verdict, "report_contract_supports_both").passed
    assert "첨부 없음: run-daily-0919-0900@1" in _check(verdict, "runs_same_workflow_and_version").detail


def test_malformed_run_record_fails(handoff_raw, trace):
    sources = demo_sources()
    sources[("run-daily-0920-0900", "1")] = ("application/json", ["not", "an", "object"])
    attachments = make_demo_attachments(sources)
    raw = with_hashes(handoff_raw, attachments)

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), attachments, make_trace(attachments))

    # findings 가 $.code_version 을 인용하므로 공통 검사에서 이미 막힌다
    assert verdict.outcome == "failed"
    assert not _check(verdict, "locations_resolve").passed
