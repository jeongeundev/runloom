"""diag_eval.py — Step 17 진단 모델 평가 하네스. 네트워크 없이 사례 정의·실행 경로·예산 중단·표 렌더를 검사한다.

실제 OpenAI 호출은 없다. 모델은 `FakeModelClient` 에 대본을 주입한다. 실호출 결과는 docs/DIAG_EVAL.md 에 있다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import diag_eval

from diagnostic_demo.settings import FAKE_MODEL_ID, Settings
from diagnostic_demo.worker.fake_script import fixture_script, needs_information_script
from diagnostic_demo.worker.model import FakeModelClient

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "diagnostic_demo" / "fixtures" / "evidence"


def _original(evidence_id: str, ext: str) -> bytes:
    return (FIXTURES / evidence_id / f"1.{ext}").read_bytes()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "diag.sqlite",
        artifact_dir=tmp_path / "diag-artifacts",
        api_token="unused",
        model="fake",
        model_id=FAKE_MODEL_ID,
        price_input_per_m=0.40,
        price_output_per_m=1.60,
    )


def _fake(script_factory):
    return lambda: FakeModelClient(script_factory())


# --- 사례 정의 ----------------------------------------------------------------------


def test_cases_are_the_five_from_architecture():
    assert list(diag_eval.CASES) == [
        "normal", "missing_response", "missing_change_doc", "effective_conflict", "http_error_input",
    ]
    assert diag_eval.CASES["normal"] == {}
    assert diag_eval.CASES["missing_response"]["removed"] == {("response-after", "1")}
    assert diag_eval.CASES["missing_change_doc"]["removed"] == {("upstream-response-change", "1")}
    assert set(diag_eval.CASES) == set(diag_eval.EXPECTATIONS)


def test_effective_conflict_document_moves_effective_at_after_failed_run():
    replaced = diag_eval.CASES["effective_conflict"]["replaced"]
    assert set(replaced) == {("upstream-response-change", "1")}
    doc = json.loads(replaced[("upstream-response-change", "1")])
    original = json.loads(_original("upstream-response-change", "json"))

    assert doc["machine"]["effective_at"] == "2026-09-21T00:00:00+09:00"
    for field in ("workflow_id", "old_path", "new_path", "preserved_fields"):
        assert doc["machine"][field] == original["machine"][field]
    # markdown 과 machine 은 한 자료로 함께 바뀐다 (ARCHITECTURE: 별개로 수정하지 않는다)
    assert "2026-09-21 00:00" in doc["markdown"] and "2026-09-20 00:00" not in doc["markdown"]
    assert set(doc) == {"markdown", "machine"}


def test_http_error_input_replaces_run_record_and_log():
    replaced = diag_eval.CASES["http_error_input"]["replaced"]
    assert set(replaced) == {("run-daily-0920-0900", "1"), ("log-daily-0920", "1")}
    record = json.loads(replaced[("run-daily-0920-0900", "1")])
    original = json.loads(_original("run-daily-0920-0900", "json"))

    assert record["http_status"] == 503
    assert [(s["stage"], s["status"]) for s in record["stages"]] == [
        ("fetch", "failed"), ("transform", "skipped"), ("render", "skipped"),
    ]
    assert record["response_ref"] is None and record["report_ref"] is None
    for field in ("run_id", "workflow_id", "started_at", "status", "code_version", "log_ref"):
        assert record[field] == original[field]

    log = replaced[("log-daily-0920", "1")].decode("utf-8")
    lines = [line for line in log.split("\n") if line]
    assert len(lines) == 4 and all("run_id=daily-0920-0900" in line for line in lines)
    assert "stage=fetch" in lines[0] and "http_status=503" in lines[0] and "ERROR" in lines[0]
    assert "MISSING_RECORDS_FIELD" not in log and "data.records" not in log and "response_ref" not in log


# --- run_case: 실제 경로 (run_diagnosis → assemble_result → verify_diagnosis) --------------


def test_run_case_normal_with_fixture_script_passes(settings):
    results = diag_eval.run_case("normal", repeat=2, model=_fake(fixture_script), settings=settings)

    assert [r.attempt for r in results] == [1, 2]
    for r in results:
        assert (r.case, r.outcome, r.verdict) == ("normal", "ready_for_handoff", "passed")
        assert r.handoff_passed and r.error is None and r.failed_checks == ()
        assert r.calls == 9 and r.input_tokens > 0 and r.output_tokens > 0
        assert r.estimated_usd == pytest.approx(
            r.input_tokens / 1e6 * 0.40 + r.output_tokens / 1e6 * 1.60
        )
        assert r.diagnosis_code == "response_path_changed" and r.missing_codes == ()
        assert r.tool_calls[0] == "get_run daily-0920-0900 ok" and len(r.tool_calls) == 8
        assert r.seconds >= 0
    assert results[0].execution_id != results[1].execution_id


def test_run_case_missing_change_doc_yields_needs_information(settings):
    (r,) = diag_eval.run_case(
        "missing_change_doc", repeat=1, model=_fake(needs_information_script), settings=settings
    )

    assert (r.outcome, r.verdict) == ("needs_information", "undecidable")
    assert r.missing_codes == ("evidence_unavailable",) and not r.handoff_passed


def test_run_case_missing_change_doc_marks_tool_failure_when_read(settings):
    (r,) = diag_eval.run_case("missing_change_doc", repeat=1, model=_fake(fixture_script), settings=settings)

    assert "read_evidence upstream-response-change@1 not_found" in r.tool_calls
    assert r.outcome == "ready_for_handoff" and r.verdict == "failed" and not r.handoff_passed
    assert "refs_in_attachments" in r.failed_checks


@pytest.mark.parametrize(
    "case, expected_check",
    [
        ("missing_response", "refs_in_attachments"),
        ("effective_conflict", "change_effective_before_failure"),
        ("http_error_input", "failed_run_http_ok_then_transform_failed"),
    ],
)
def test_ready_for_handoff_is_blocked_by_verifier_in_error_cases(settings, case, expected_check):
    """모델이 정답 대본을 그대로 재생해도 검증기가 인계를 막는다 — 잘못된 수정 착수 0 의 마지막 방어선."""
    (r,) = diag_eval.run_case(case, repeat=1, model=_fake(fixture_script), settings=settings)

    assert r.outcome == "ready_for_handoff" and r.verdict == "failed"
    assert expected_check in r.failed_checks and not r.handoff_passed


def test_run_case_records_model_error_without_raising(settings):
    (r,) = diag_eval.run_case("normal", repeat=1, model=lambda: FakeModelClient([]), settings=settings)

    assert r.outcome is None and r.verdict is None
    assert r.error is not None and "RuntimeError" in r.error and r.calls == 0


def test_run_case_preserves_invalid_draft_for_analysis(settings):
    """strict 스키마를 통과해도 계약 검증기(Location 등)가 거부한 초안은 원문·오류 위치를 workdir 에 남긴다."""
    from pydantic import ValidationError

    from diagnostic_demo.worker.model import DiagnosisDraft, DraftInvalid

    raw = json.dumps({
        "outcome": "ready_for_handoff", "summary": "s",
        "findings": [{"claim": "c", "evidence_refs": [
            {"evidence_id": "log-daily-0920", "version": "1", "location": "lines:2"}]}],
        "diagnosis": None, "repair_request": None, "missing_information": [],
    })

    class Client:
        def start(self, *_):
            try:
                DiagnosisDraft.model_validate_json(raw)
            except ValidationError as exc:
                raise DraftInvalid(raw, "초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류") from exc
            raise AssertionError("검증기가 거부해야 한다")

        def continue_with_tool_results(self, results):
            raise AssertionError("호출되지 않는다")

    (r,) = diag_eval.run_case("normal", repeat=1, model=Client, settings=settings)

    assert r.outcome is None and r.error.startswith("model_output_invalid:")
    assert "findings.0.evidence_refs.0.location" in r.error
    saved = json.loads((settings.artifact_dir.parent / "invalid-drafts" / f"{r.execution_id}.json").read_text())
    assert saved["raw"] == raw and saved["errors"][0]["loc"] == ["findings", 0, "evidence_refs", 0, "location"]


def test_error_message_masks_secret_tokens():
    assert diag_eval.safe_message("boom sk-abcdefghijklmnop rest") == "boom sk-*** rest"


# --- 예산 -------------------------------------------------------------------------


def test_ledger_stops_before_next_run(settings):
    ledger = diag_eval.CostLedger(limit_usd=0.000001)

    results = diag_eval.run_case(
        "normal", repeat=3, model=_fake(fixture_script), settings=settings, ledger=ledger
    )

    assert len(results) == 1 and ledger.exhausted()
    assert ledger.spent_usd == pytest.approx(results[0].estimated_usd)


def test_ledger_allows_all_runs_under_limit(settings):
    ledger = diag_eval.CostLedger(limit_usd=1.0)

    results = diag_eval.run_case(
        "normal", repeat=3, model=_fake(fixture_script), settings=settings, ledger=ledger
    )

    assert len(results) == 3 and not ledger.exhausted()


# --- 판정·렌더 -----------------------------------------------------------------------


def _result(case, attempt, outcome, verdict, **extra) -> diag_eval.CaseResult:
    base = dict(
        case=case, attempt=attempt, execution_id=f"eval-{case}-{attempt}", outcome=outcome,
        verdict=verdict, calls=10, input_tokens=30_000, output_tokens=1_000, seconds=12.5,
        estimated_usd=0.0136, error=None,
    )
    return diag_eval.CaseResult(**{**base, **extra})


def _full_set(normal_verdict="passed", bad=()):
    results = [_result("normal", n, "ready_for_handoff", normal_verdict) for n in (1, 2, 3)]
    for case in list(diag_eval.CASES)[1:]:
        for n in (1, 2, 3):
            if (case, n) in bad:
                results.append(_result(case, n, "ready_for_handoff", "passed"))
            else:
                results.append(_result(case, n, "needs_information", "undecidable"))
    return results


def test_evaluate_passes_only_when_normal_all_pass_and_no_false_handoff():
    ok = diag_eval.evaluate(_full_set(), repeat=3)
    assert ok.passed and ok.normal_passed == 3 and ok.false_handoffs == () and ok.complete

    bad = diag_eval.evaluate(_full_set(bad={("effective_conflict", 2)}), repeat=3)
    assert not bad.passed and [(r.case, r.attempt) for r in bad.false_handoffs] == [("effective_conflict", 2)]

    weak = diag_eval.evaluate(_full_set(normal_verdict="undecidable"), repeat=3)
    assert not weak.passed and weak.normal_passed == 0

    partial = diag_eval.evaluate(_full_set()[:-1], repeat=3)
    assert not partial.passed and not partial.complete


def test_render_markdown_has_table_judgement_and_checks(settings):
    results = _full_set(bad={("http_error_input", 3)})
    results[-1] = _result(
        "http_error_input", 3, "ready_for_handoff", "failed",
        failed_checks=("failed_run_http_ok_then_transform_failed",),
        tool_calls=("get_run daily-0920-0900 ok",),
    )
    ledger = diag_eval.CostLedger(limit_usd=2.0, spent_usd=0.2)

    text = diag_eval.render_markdown(
        results, settings=settings, ledger=ledger, repeat=3,
        started_at="2026-09-20T15:00:00+09:00", finished_at="2026-09-20T15:10:00+09:00",
    )

    assert text.startswith("# 진단 모델 실호출 평가")
    assert "| 사례 | 회차 | outcome | 검증기 |" in text
    for case in diag_eval.CASES:
        assert f"| `{case}` |" in text
    assert "normal 3/3" in text and "잘못된 수정 착수 0/12" in text
    assert "확정 조건 충족" in text
    assert "failed_run_http_ok_then_transform_failed" in text
    assert "US$0.2000" in text and "US$2" in text
    assert FAKE_MODEL_ID in text and "diag-prompt-v1" in text
    assert "sk-" not in text


def test_render_markdown_reports_failure():
    results = _full_set(bad={("effective_conflict", 1)})
    text = diag_eval.render_markdown(
        results, settings=Settings(db_path=Path("x"), artifact_dir=Path("y"), api_token="t"),
        ledger=diag_eval.CostLedger(limit_usd=2.0, spent_usd=0.3), repeat=3,
        started_at="2026-09-20T15:00:00+09:00", finished_at="2026-09-20T15:10:00+09:00",
    )
    assert "잘못된 수정 착수 1/12" in text and "확정 보류" in text and "effective_conflict 1회차" in text


# --- main: 시작 전 확인 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["OPENAI_API_KEY", "DIAG_PRICE_INPUT_PER_M", "DIAG_PRICE_OUTPUT_PER_M", "DIAG_EVAL_BUDGET_USD"],
)
def test_main_refuses_without_required_env(tmp_path, capsys, missing):
    env = {
        "OPENAI_API_KEY": "sk-test", "DIAG_PRICE_INPUT_PER_M": "0.4", "DIAG_PRICE_OUTPUT_PER_M": "1.6",
        "DIAG_EVAL_BUDGET_USD": "2",
    }
    env.pop(missing)
    out = tmp_path / "DIAG_EVAL.md"

    code = diag_eval.main(["--cases", "all", "--out", str(out)], env=env)

    assert code == 2 and not out.exists()
    err = capsys.readouterr().err
    assert missing in err and "sk-test" not in err


def test_main_rejects_unknown_case(tmp_path, capsys):
    env = {
        "OPENAI_API_KEY": "sk-test", "DIAG_PRICE_INPUT_PER_M": "0.4", "DIAG_PRICE_OUTPUT_PER_M": "1.6",
        "DIAG_EVAL_BUDGET_USD": "2",
    }
    code = diag_eval.main(["--cases", "nope", "--out", str(tmp_path / "x.md")], env=env)
    assert code == 2 and "nope" in capsys.readouterr().err
