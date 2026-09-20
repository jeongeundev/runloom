"""make_handoff_dir.py — Step 15 실연동 확인용 인계 디렉터리를 fixture·CONTRACT 에서 손으로 만든다.

연결 프로그램(`runner._download_handoff`)이 만드는 것과 같은 파일 이름·manifest 형식이어야 한다.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_handoff_dir as mhd

from workflow.contracts.v1 import DiagnosisResult, HandoffBundle
from workflow.domain.report_expectation import expected_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "diagnostic_demo" / "fixtures"

EVIDENCE_FILES = {
    "run-daily-0919-0900@1.json", "run-daily-0920-0900@1.json",
    "response-before@1.json", "response-after@1.json",
    "log-daily-0920@1.txt",
    "upstream-response-change@1.json", "daily-report-contract@1.json", "daily-report-runbook@1.json",
}


@pytest.fixture(scope="module")
def handoff(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("step15") / "fix-daily-0920.handoff"
    mhd.make_handoff_dir(dest)
    return dest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 파일 -----------------------------------------------------------------------------


def test_writes_nine_attachment_files_plus_manifest_and_result(handoff):
    names = {p.name for p in handoff.iterdir()}
    attachments = {n for n in names if "@1." in n}
    assert attachments == EVIDENCE_FILES | {"expected-report@1.json"}
    assert len(attachments) == 9
    assert names == attachments | {"manifest.json", "diagnosis_result.json"}


def test_evidence_files_are_fixture_bytes(handoff):
    for name in EVIDENCE_FILES:
        evidence_id, rest = name.split("@", 1)
        version, ext = rest.split(".", 1)
        assert (handoff / name).read_bytes() == (FIXTURES / "evidence" / evidence_id / f"{version}.{ext}").read_bytes()


# --- manifest ----------------------------------------------------------------------------


def test_manifest_is_contract_2_bundle_with_matching_hashes(handoff):
    bundle = HandoffBundle.model_validate_json((handoff / "manifest.json").read_text())
    assert bundle.contract_version == 1
    assert bundle.source_execution_id == "exec-diagnose-001"
    assert bundle.diagnosis_result_artifact_id == "art-diag-result-001"
    assert len(bundle.attachments) == 9
    for ref in bundle.attachments:
        ext = {"application/json": "json", "text/plain": "txt"}[ref.content_type]
        path = handoff / f"{ref.evidence_id}@{ref.version}.{ext}"
        assert path.is_file(), ref.evidence_id
        assert ref.sha256 == _sha(path)


def test_manifest_artifact_ids_are_unique(handoff):
    bundle = HandoffBundle.model_validate_json((handoff / "manifest.json").read_text())
    ids = [a.artifact_id for a in bundle.attachments]
    assert len(set(ids)) == len(ids)


# --- diagnosis_result ------------------------------------------------------------------


def test_diagnosis_result_is_contract_5_with_real_hashes(handoff):
    result = DiagnosisResult.model_validate_json((handoff / "diagnosis_result.json").read_text())
    assert result.outcome == "ready_for_handoff"
    assert result.diagnosis is not None and result.diagnosis.code == "response_path_changed"
    assert result.run_id == "daily-0920-0900"
    bundle = HandoffBundle.model_validate_json((handoff / "manifest.json").read_text())
    by_key = {(a.evidence_id, a.version): a for a in bundle.attachments}
    assert len(result.attachments) == 8
    for ref in result.attachments:
        assert ref.sha256 == by_key[(ref.evidence_id, ref.version)].sha256
        assert ref.artifact_id == by_key[(ref.evidence_id, ref.version)].artifact_id


# --- expected-report -----------------------------------------------------------------------


def test_expected_report_is_computed_from_response_after_and_contract(handoff):
    response = json.loads((handoff / "response-after@1.json").read_text())
    contract = json.loads((handoff / "daily-report-contract@1.json").read_text())
    computed = expected_report(response, contract["machine"]["supported_paths"])
    assert computed is not None
    written = json.loads((handoff / "expected-report@1.json").read_text())
    assert written == computed.to_dict()
    assert written["report_date"] == response["report_date"]
    assert written["total_completed"] == sum(r["completed"] for r in written["rows"])


# --- 덮어쓰기·CLI ----------------------------------------------------------------------------


def test_refuses_non_empty_dest_unless_force(tmp_path):
    dest = tmp_path / "h"
    mhd.make_handoff_dir(dest)
    with pytest.raises(FileExistsError):
        mhd.make_handoff_dir(dest)
    (dest / "manifest.json").write_text("{}")
    mhd.make_handoff_dir(dest, force=True)
    HandoffBundle.model_validate_json((dest / "manifest.json").read_text())


def test_main_prints_dest_and_count(tmp_path, capsys):
    dest = tmp_path / "cli"
    assert mhd.main([str(dest)]) == 0
    out = capsys.readouterr().out
    assert str(dest) in out and "11" in out
    assert mhd.main([str(dest)]) == 1
    assert mhd.main([str(dest), "--force"]) == 0
