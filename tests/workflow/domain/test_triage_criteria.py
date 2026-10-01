"""판단 기준 v1 상수 — 문서 `docs/product/triage-criteria-v1.md` 와 같은 글 (ADR-0025 결정 16)."""

from pathlib import Path

from workflow.domain.triage_criteria import CRITERIA_BODY_MAX, TRIAGE_CRITERIA_V1

DOC = Path(__file__).parents[3] / "docs" / "product" / "triage-criteria-v1.md"


def test_criteria_v1_is_the_document_byte_for_byte():
    assert TRIAGE_CRITERIA_V1.encode("utf-8") == DOC.read_bytes()


def test_criteria_v1_fits_the_body_limit():
    assert CRITERIA_BODY_MAX == 8000
    assert 1 <= len(TRIAGE_CRITERIA_V1) <= CRITERIA_BODY_MAX
