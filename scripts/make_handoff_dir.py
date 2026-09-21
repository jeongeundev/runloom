#!/usr/bin/env python3
"""Step 15 실연동 확인용 인계 디렉터리를 fixture 와 CONTRACT 예시로 손수 만든다.

    python3 scripts/make_handoff_dir.py DIR [--force]

연결 프로그램이 중앙에서 내려받아 만드는 것(`runner._download_handoff`)과 같은 모양이다:
`{evidence_id}@{version}.{ext}` 근거 8개 + `expected-report@1.json` + `manifest.json`(CONTRACT 2절) +
`diagnosis_result.json`(CONTRACT 5절). CONTRACT 의 sha256 은 형식만 맞춘 예시라 실제 fixture 바이트의 해시로
바꾼다(artifact_id 는 CONTRACT 값 그대로). 기대 보고서는 `response-after` 와 계약의 `supported_paths` 에서 계산한다.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from workflow.contracts.v1 import AttachmentRef, DiagnosisResult, HandoffBundle
from workflow.domain.report_expectation import expected_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "diagnostic_demo" / "fixtures"
CONTRACT_MD = ROOT / "docs" / "CONTRACT.md"
_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_EXTENSIONS = {"application/json": "json", "text/plain": "txt"}

EXPECTED_REPORT = ("expected-report", "1", "art-expected-001")


def _contract_result() -> dict:
    """CONTRACT 5절 — `ready_for_handoff` 결과 봉투."""
    blocks = [json.loads(m) for m in _FENCE.findall(CONTRACT_MD.read_text(encoding="utf-8"))]
    (block,) = [b for b in blocks if b.get("outcome") == "ready_for_handoff" and "findings" in b]
    return block


def _fixture_bytes(evidence_id: str, version: str, content_type: str) -> bytes:
    return (FIXTURES / "evidence" / evidence_id / f"{version}.{_EXTENSIONS[content_type]}").read_bytes()


def make_handoff_dir(dest: Path, *, force: bool = False) -> HandoffBundle:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        if not force:
            raise FileExistsError(f"{dest} 이 비어 있지 않습니다. --force 로 다시 만들 수 있습니다.")
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    result = _contract_result()
    attachments: list[AttachmentRef] = []
    for ref in result["attachments"]:
        data = _fixture_bytes(ref["evidence_id"], ref["version"], ref["content_type"])
        ref["sha256"] = hashlib.sha256(data).hexdigest()
        (dest / f"{ref['evidence_id']}@{ref['version']}.{_EXTENSIONS[ref['content_type']]}").write_bytes(data)
        attachments.append(AttachmentRef.model_validate(ref))

    response = json.loads(_fixture_bytes("response-after", "1", "application/json"))
    contract = json.loads(_fixture_bytes("daily-report-contract", "1", "application/json"))
    expected = expected_report(response, contract["machine"]["supported_paths"])
    if expected is None:
        raise ValueError("response-after 와 daily-report-contract 로 기대 보고서를 계산하지 못했습니다")
    expected_data = json.dumps(expected.to_dict(), ensure_ascii=False, indent=2).encode()
    evidence_id, version, artifact_id = EXPECTED_REPORT
    (dest / f"{evidence_id}@{version}.json").write_bytes(expected_data)
    attachments.append(AttachmentRef(
        evidence_id=evidence_id, version=version, content_type="application/json",
        artifact_id=artifact_id, sha256=hashlib.sha256(expected_data).hexdigest(),
    ))

    bundle = HandoffBundle(
        contract_version=1,
        source_execution_id=result["execution_id"],
        source_kind="diagnosis",
        source_result_artifact_id="art-diag-result-001",
        inputs=[],
        attachments=attachments,
    )
    (dest / "manifest.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    diagnosis = DiagnosisResult.model_validate(result)
    (dest / "diagnosis_result.json").write_text(diagnosis.model_dump_json(indent=2), encoding="utf-8")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Step 15 실연동 확인용 인계 디렉터리를 만든다.")
    parser.add_argument("dest", type=Path)
    parser.add_argument("--force", action="store_true", help="비어 있지 않아도 지우고 다시 만든다")
    args = parser.parse_args(argv)
    try:
        bundle = make_handoff_dir(args.dest, force=args.force)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    count = len(list(args.dest.iterdir()))
    print(f"{args.dest}: 파일 {count}개 (첨부 {len(bundle.attachments)}개 + manifest.json + diagnosis_result.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
