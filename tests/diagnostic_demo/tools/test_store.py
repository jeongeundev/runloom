"""FixtureStore — 가상 자료의 무결성, Step 3 검증기와의 정합, 조회 도구 4개의 동작.

fixture 형식은 phases/0-mvp/step3.md "실행 기록·문서 첨부의 형식" 과 PRD "데모 데이터와 검증 상세" 를
따른다. `test_contract_section_5_passes_verifier` 가 fixture 와 검증기의 계약이다.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from diagnostic_demo.tools.store import FixtureStore, ToolError, ToolResult
from workflow.contracts.v1 import DiagnosisResult, EvidenceVersion
from workflow.domain.verification import LoadedEvidence, TraceEntry, verify_diagnosis

CONTRACT_MD = Path(__file__).resolve().parents[3] / "docs" / "CONTRACT.md"
_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)

RUN_RECORD_KEYS = {
    "run_id", "workflow_id", "started_at", "finished_at", "status", "code_version",
    "http_status", "stages", "response_ref", "log_ref", "report_ref",
}
MACHINE_KEYS = {
    "daily-report-runbook": {
        "workflow_id", "schedule", "stages", "transformer_component", "reads_path", "on_failure",
    },
    "upstream-response-change": {
        "workflow_id", "effective_at", "old_path", "new_path", "preserved_fields",
    },
    "daily-report-contract": {
        "workflow_id", "supported_paths", "required_row_fields", "empty_list_policy",
    },
}

# PRD "데모 데이터와 검증 상세" 의 응답 2개·실패 로그 그대로
PRD_ROWS = [
    {"team": "운영", "completed": 12, "pending": 3},
    {"team": "개발", "completed": 8, "pending": 2},
]
PRD_RESPONSE_BEFORE = {"report_date": "2026-09-18", "items": PRD_ROWS}
PRD_RESPONSE_AFTER = {"report_date": "2026-09-19", "data": {"records": PRD_ROWS}}
PRD_LOG_0920 = [
    "2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900 stage=fetch event=response_received"
    " http_status=200 response_ref=response-after",
    "2026-09-20T09:00:01+09:00 ERROR run_id=daily-0920-0900 stage=transform"
    " component=report_transformer code=MISSING_RECORDS_FIELD expected_path=$.items"
    " observed_root_keys=[report_date,data] response_ref=response-after",
    "2026-09-20T09:00:01+09:00 INFO  run_id=daily-0920-0900 stage=render status=skipped"
    " reason=transform_failed",
    "2026-09-20T09:00:01+09:00 ERROR run_id=daily-0920-0900 event=run_finished status=failed"
    " report_created=false",
]


def _evidence_files(root: Path, evidence_id: str, version: str) -> list[Path]:
    return sorted((root / "evidence" / evidence_id).glob(f"{version}.*"))


def _read_json(root: Path, evidence_id: str, version: str = "1") -> dict:
    (path,) = _evidence_files(root, evidence_id, version)
    return json.loads(path.read_text(encoding="utf-8"))


# --- fixture 무결성 --------------------------------------------------------------


def test_readme_disclaims_real_company_data(fixture_root):
    text = (fixture_root / "README.md").read_text(encoding="utf-8")
    assert "가상" in text
    assert "실제 기업" in text


def test_index_lists_single_workflow_and_content_types(index):
    assert index["workflows"] == ["daily-report"]
    assert index["content_types"] == {"json": "application/json", "txt": "text/plain"}


def test_every_run_and_document_has_exactly_one_evidence_file(fixture_root, index):
    for run in index["runs"]:
        ev = run["evidence"]
        files = _evidence_files(fixture_root, ev["evidence_id"], ev["version"])
        assert len(files) == 1, (run["run_id"], files)
        assert files[0].suffix == ".json"
    for doc in index["documents"]:
        files = _evidence_files(fixture_root, doc["evidence_id"], doc["version"])
        assert len(files) == 1, (doc["evidence_id"], files)
        assert files[0].suffix == ".json"


def test_every_evidence_json_parses_and_txt_is_utf8(fixture_root):
    paths = sorted((fixture_root / "evidence").glob("*/*"))
    assert len(paths) == 10
    for path in paths:
        assert path.suffix in {".json", ".txt"}, path
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            json.loads(text)


def test_run_records_follow_step3_shape(fixture_root, index):
    for run in index["runs"]:
        record = _read_json(fixture_root, run["evidence"]["evidence_id"])
        assert set(record) == RUN_RECORD_KEYS, run["run_id"]
        assert record["run_id"] == run["run_id"]
        assert record["workflow_id"] == run["workflow_id"]
        assert record["started_at"] == run["started_at"]
        assert record["status"] == run["status"]
        assert record["code_version"] == run["code_version"]
        assert [s["stage"] for s in record["stages"]] == ["fetch", "transform", "render"]


def test_failed_run_record_content(fixture_root):
    record = _read_json(fixture_root, "run-daily-0920-0900")
    assert record["http_status"] == 200
    assert record["stages"][1] == {
        "stage": "transform", "status": "failed", "error_code": "MISSING_RECORDS_FIELD",
    }
    assert record["stages"][2] == {"stage": "render", "status": "skipped"}
    assert record["response_ref"] == {"evidence_id": "response-after", "version": "1"}
    assert record["log_ref"] == {"evidence_id": "log-daily-0920", "version": "1"}
    assert record["report_ref"] is None


def test_succeeded_run_record_content(fixture_root):
    record = _read_json(fixture_root, "run-daily-0919-0900")
    assert record["http_status"] == 200
    assert all(s["status"] == "succeeded" for s in record["stages"])
    assert record["response_ref"] == {"evidence_id": "response-before", "version": "1"}
    assert record["log_ref"] == {"evidence_id": "log-daily-0919", "version": "1"}
    assert record["report_ref"] == {"evidence_id": "report-0918", "version": "1"}


def test_responses_match_prd_verbatim(fixture_root):
    assert _read_json(fixture_root, "response-before") == PRD_RESPONSE_BEFORE
    assert _read_json(fixture_root, "response-after") == PRD_RESPONSE_AFTER


def test_failed_log_matches_prd_verbatim(fixture_root):
    (path,) = _evidence_files(fixture_root, "log-daily-0920", "1")
    assert path.read_text(encoding="utf-8").rstrip("\n").split("\n") == PRD_LOG_0920


def test_succeeded_log_has_four_stages_and_report_created(fixture_root):
    (path,) = _evidence_files(fixture_root, "log-daily-0919", "1")
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(lines) == 4
    assert all("run_id=daily-0919-0900" in line for line in lines)
    assert "stage=fetch" in lines[0] and "http_status=200" in lines[0]
    assert "stage=transform" in lines[1] and "ERROR" not in lines[1]
    assert "stage=render" in lines[2]
    assert "event=run_finished status=succeeded report_created=true" in lines[3]


def test_report_0918_is_prd_report_format(fixture_root):
    (path,) = _evidence_files(fixture_root, "report-0918", "1")
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines[0] == "일일 업무 보고서 — 2026-09-18"
    assert lines[-1].split() == ["합계", "20", "5"]
    assert [ln.split() for ln in lines[-3:-1]] == [["운영", "12", "3"], ["개발", "8", "2"]]


def test_documents_have_markdown_and_step3_machine(fixture_root, index):
    for doc in index["documents"]:
        body = _read_json(fixture_root, doc["evidence_id"], doc["version"])
        assert set(body) == {"markdown", "machine"}, doc["evidence_id"]
        assert body["markdown"].splitlines()[0].startswith("(가상 데모 자료)")
        assert set(body["machine"]) == MACHINE_KEYS[doc["evidence_id"]]
        assert body["machine"]["workflow_id"] == doc["workflow_id"]


def test_document_machine_values(fixture_root):
    change = _read_json(fixture_root, "upstream-response-change")["machine"]
    assert change["effective_at"] == "2026-09-20T00:00:00+09:00"
    assert (change["old_path"], change["new_path"]) == ("$.items", "$.data.records")
    assert change["preserved_fields"] == ["report_date", "team", "completed", "pending"]
    contract = _read_json(fixture_root, "daily-report-contract")["machine"]
    assert contract["supported_paths"] == ["$.items", "$.data.records"]
    assert contract["required_row_fields"] == ["team", "completed", "pending"]
    assert contract["empty_list_policy"] == {
        "explicit_empty": "zero_rows", "missing": "error", "ambiguous": "error",
    }
    runbook = _read_json(fixture_root, "daily-report-runbook")["machine"]
    assert runbook["transformer_component"] == "report_transformer"
    assert runbook["reads_path"] == "$.items"
    assert runbook["stages"] == ["fetch", "transform", "render"]


def test_fixtures_do_not_contain_urls_or_real_company_names(fixture_root):
    # README 는 착안한 공고를 부인 문구와 함께 밝힌다. 자료 본문·index 에는 실제 기업명·URL 이 없어야 한다.
    for path in sorted(fixture_root.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "http://" not in text and "https://" not in text, path
            if path.name != "README.md":
                assert "미리디" not in text and "miridih" not in text.lower(), path


# --- Step 3 검증기와의 정합 ---------------------------------------------------------


def contract_results() -> tuple[dict, dict]:
    blocks = [json.loads(m) for m in _FENCE.findall(CONTRACT_MD.read_text(encoding="utf-8"))]
    results = [b for b in blocks if {"outcome", "findings"} <= set(b)]
    assert [r["outcome"] for r in results] == ["ready_for_handoff", "needs_information"]
    return results[0], results[1]


def load_from_store(store: FixtureStore, raw: dict) -> tuple[dict, dict, list[TraceEntry]]:
    """CONTRACT 결과의 attachments 를 store 원문으로 싣고 해시를 다시 계산한다. 이력은 전부 반환."""
    result = json.loads(json.dumps(raw))
    loaded: dict[tuple[str, str], LoadedEvidence] = {}
    trace: list[TraceEntry] = []
    for n, ref in enumerate(result["attachments"], 1):
        key = (ref["evidence_id"], ref["version"])
        read = store.read_evidence(*key)
        assert read.ok, key
        content = store.raw_bytes(*key)
        ref["sha256"] = store.sha256_of(*key)
        loaded[key] = LoadedEvidence(
            evidence_id=key[0], version=key[1], content_type=read.content_type,
            sha256=ref["sha256"], content=content,
        )
        trace.append(TraceEntry(f"call-{n}", "read_evidence", dict(zip(("evidence_id", "version"), key)), True, (key,)))
    return result, loaded, trace


def test_contract_section_5_passes_verifier(store):
    raw, loaded, trace = load_from_store(store, contract_results()[0])
    assert len(loaded) == 8

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), loaded, trace)

    assert verdict.outcome == "passed", [c for c in verdict.checks if not c.passed]
    assert all(c.passed for c in verdict.checks)


def test_contract_section_6_is_undecidable_with_fixtures(store):
    raw, loaded, trace = load_from_store(store, contract_results()[1])

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), loaded, trace)

    assert verdict.outcome == "undecidable"
    assert all(c.passed for c in verdict.checks)


def test_effective_at_conflict_replacement_is_caught_by_verifier(store, fixture_root):
    # Step 17 의 `replaced` 사용 방식: 문서 바이트만 바꿔 넣으면 검증기가 evidence_conflict 를 낸다
    body = _read_json(fixture_root, "upstream-response-change")
    body["machine"]["effective_at"] = "2026-09-21T00:00:00+09:00"
    replaced = {("upstream-response-change", "1"): json.dumps(body, ensure_ascii=False).encode()}
    conflicted = FixtureStore(fixture_root, frozenset({"daily-report"}), replaced=replaced)
    raw, loaded, trace = load_from_store(conflicted, contract_results()[0])

    verdict = verify_diagnosis(DiagnosisResult.model_validate(raw), loaded, trace)

    assert verdict.outcome == "failed"
    (check,) = [c for c in verdict.checks if c.code == "change_effective_before_failure"]
    assert not check.passed and "evidence_conflict" in check.detail


# --- get_run -----------------------------------------------------------------


def test_get_run_returns_record_and_its_evidence(store):
    result = store.get_run("daily-0920-0900")

    assert isinstance(result, ToolResult)
    assert result.ok and result.error is None
    assert result.content_type == "application/json"
    assert result.content["run_id"] == "daily-0920-0900"
    assert result.content["status"] == "failed"
    assert result.returned == (EvidenceVersion(evidence_id="run-daily-0920-0900", version="1"),)


def test_get_run_unknown_is_not_found(store):
    result = store.get_run("daily-0921-0900")

    assert result == ToolResult(False, None, None, ToolError.not_found, ())


def test_get_run_outside_scope_is_access_denied_not_empty(denied_store):
    result = denied_store.get_run("daily-0920-0900")

    assert not result.ok
    assert result.error is ToolError.access_denied
    assert result.content is None and result.returned == ()


def test_get_run_with_removed_record_is_not_found(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         removed=frozenset({("run-daily-0920-0900", "1")}))
    assert store.get_run("daily-0920-0900").error is ToolError.not_found
    assert store.get_run("daily-0919-0900").ok


def test_get_run_with_unavailable_record(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         unavailable=frozenset({("run-daily-0920-0900", "1")}))
    result = store.get_run("daily-0920-0900")
    assert result.error is ToolError.unavailable and result.content is None


# --- list_runs ---------------------------------------------------------------


def test_list_runs_newest_first_summary_only(store):
    result = store.list_runs("daily-report", before=None, status=None)

    assert result.ok and result.returned == ()
    assert result.content_type == "application/json"
    assert [r["run_id"] for r in result.content] == ["daily-0920-0900", "daily-0919-0900"]
    assert result.content[0] == {
        "run_id": "daily-0920-0900",
        "started_at": "2026-09-20T09:00:00+09:00",
        "status": "failed",
        "code_version": "report-base",
    }


def test_list_runs_before_filters_by_started_at(store):
    result = store.list_runs("daily-report", before="2026-09-20T09:00:00+09:00", status=None)
    assert [r["run_id"] for r in result.content] == ["daily-0919-0900"]

    # 다른 시간대 표기라도 같은 순간이면 같은 결과
    same_instant = store.list_runs("daily-report", before="2026-09-20T00:00:00Z", status=None)
    assert [r["run_id"] for r in same_instant.content] == ["daily-0919-0900"]


def test_list_runs_status_filter_and_limit(store):
    assert [r["run_id"] for r in store.list_runs("daily-report", None, "succeeded").content] == [
        "daily-0919-0900"
    ]
    assert [r["run_id"] for r in store.list_runs("daily-report", None, None, limit=1).content] == [
        "daily-0920-0900"
    ]
    assert store.list_runs("daily-report", None, "unknown-status").content == []


def test_list_runs_invalid_before_raises(store):
    with pytest.raises(ValueError):
        store.list_runs("daily-report", before="어제", status=None)


def test_list_runs_outside_scope_is_access_denied(denied_store):
    result = denied_store.list_runs("daily-report", None, None)
    assert result.error is ToolError.access_denied and result.content is None


# --- list_documents ------------------------------------------------------------


def test_list_documents_returns_index_entries(store, index):
    result = store.list_documents("daily-report")

    assert result.ok and result.returned == ()
    assert result.content == index["documents"]
    assert [d["evidence_id"] for d in result.content] == [
        "daily-report-runbook", "upstream-response-change", "daily-report-contract",
    ]


def test_list_documents_outside_scope_is_access_denied(denied_store):
    assert denied_store.list_documents("daily-report").error is ToolError.access_denied


def test_list_documents_unknown_workflow_in_scope_is_empty(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report", "weekly-report"}))
    result = store.list_documents("weekly-report")
    assert result.ok and result.content == []


# --- read_evidence -------------------------------------------------------------


def test_read_evidence_json(store):
    result = store.read_evidence("response-after", "1")

    assert result.ok
    assert result.content == PRD_RESPONSE_AFTER
    assert result.content_type == "application/json"
    assert result.returned == (EvidenceVersion(evidence_id="response-after", version="1"),)


def test_read_evidence_text(store):
    result = store.read_evidence("log-daily-0920", "1")

    assert result.ok
    assert result.content_type == "text/plain"
    assert isinstance(result.content, str)
    assert result.content.rstrip("\n").split("\n") == PRD_LOG_0920


def test_read_evidence_document(store):
    result = store.read_evidence("upstream-response-change", "1")
    assert result.ok and set(result.content) == {"markdown", "machine"}


def test_read_evidence_unknown_id_or_version_is_not_found(store):
    assert store.read_evidence("response-later", "1").error is ToolError.not_found
    assert store.read_evidence("response-after", "2").error is ToolError.not_found


def test_read_evidence_outside_scope_is_access_denied(denied_store):
    result = denied_store.read_evidence("response-after", "1")
    assert result.error is ToolError.access_denied and result.content is None


def test_read_evidence_removed_is_not_found(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         removed=frozenset({("upstream-response-change", "1")}))
    result = store.read_evidence("upstream-response-change", "1")
    assert result == ToolResult(False, None, None, ToolError.not_found, ())
    assert store.read_evidence("daily-report-contract", "1").ok


def test_read_evidence_unavailable(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         unavailable=frozenset({("response-after", "1")}))
    result = store.read_evidence("response-after", "1")
    assert result == ToolResult(False, None, None, ToolError.unavailable, ())


def test_read_evidence_replaced_content_and_hash(fixture_root):
    body = json.dumps({"report_date": "2026-09-19", "data": {"records": []}}).encode()
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         replaced={("response-after", "1"): body})

    result = store.read_evidence("response-after", "1")

    assert result.ok and result.content == {"report_date": "2026-09-19", "data": {"records": []}}
    assert store.raw_bytes("response-after", "1") == body
    assert store.sha256_of("response-after", "1") == hashlib.sha256(body).hexdigest()
    # 다른 자료는 그대로
    assert store.read_evidence("response-before", "1").content == PRD_RESPONSE_BEFORE


def test_raw_bytes_and_sha256_match_file(store, fixture_root):
    (path,) = _evidence_files(fixture_root, "log-daily-0920", "1")
    raw = store.raw_bytes("log-daily-0920", "1")
    assert raw == path.read_bytes()
    assert store.sha256_of("log-daily-0920", "1") == hashlib.sha256(raw).hexdigest()


def test_raw_bytes_of_missing_evidence_raises(fixture_root):
    store = FixtureStore(fixture_root, frozenset({"daily-report"}),
                         removed=frozenset({("response-after", "1")}))
    with pytest.raises(FileNotFoundError):
        store.raw_bytes("response-later", "1")
    with pytest.raises(FileNotFoundError):
        store.raw_bytes("response-after", "1")
