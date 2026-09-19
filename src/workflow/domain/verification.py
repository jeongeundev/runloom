"""진단 결과 검증기 — ARCHITECTURE "진단 완료 검증", CONTRACT 5절 "검증기가 확인하는 것".

A 의 자동 완료는 모델의 "완료했다" 가 아니라 이 판정으로 결정한다. 판정은 구조화 필드와
첨부 원문 값으로만 하고, `summary`·`claim` 같은 자연어는 읽지 않는다. 검증기는 인자로
받은 첨부만 보며 파일·네트워크 I/O 를 하지 않는다.

- `failed`: 계약 위반(읽지 않은 자료 첨부, 해시 불일치, 위치 미존재) 또는 첨부 원문과
  diagnosis 의 모순. 차단이며 보류로 완화하지 않는다.
- `undecidable`: `needs_information`, 지원하지 않는 diagnosis code, 데모 판정에 필요한
  첨부 부족. 확인 필요로 둔다.
- `passed`: 공통 검사와 데모 검사(`response_path_changed`) 모두 통과.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from workflow.contracts.v1 import Diagnosis, DiagnosisResult
from workflow.domain.evidence_location import resolve_location

# 데모 실행 기록의 evidence_id 는 `run-{run_id}`, 자료 버전은 최초 데모 버전 (PRD "데모 데이터")
_RUN_RECORD_VERSION = "1"

EvidenceKey = tuple[str, str]  # (evidence_id, version)


@dataclass(frozen=True)
class LoadedEvidence:
    """중앙이 내려받아 해시를 확인한 첨부 원문."""

    evidence_id: str
    version: str
    content_type: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class TraceEntry:
    """진단 서비스 조회 이력(`tool_trace`)의 호출 하나."""

    call_id: str
    tool: str
    input: dict
    ok: bool
    returned: tuple[EvidenceKey, ...]  # 실제로 반환한 (evidence_id, version)


@dataclass(frozen=True)
class Check:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    outcome: Literal["passed", "failed", "undecidable"]
    checks: tuple[Check, ...]


def _key(ref: Any) -> EvidenceKey:
    return (ref.evidence_id, ref.version)


def _label(key: EvidenceKey) -> str:
    return f"{key[0]}@{key[1]}"


def _labels(keys: Sequence[EvidenceKey]) -> str:
    return ", ".join(_label(k) for k in keys)


def _check(code: str, problems: Sequence[str], passed_detail: str) -> Check:
    if problems:
        return Check(code, False, " · ".join(problems))
    return Check(code, True, passed_detail)


# --- 공통 검사 ------------------------------------------------------------------


def _check_attachments_in_trace(result: DiagnosisResult, trace: Sequence[TraceEntry]) -> Check:
    # ok=False 인 호출은 자료를 반환하지 않은 것으로 본다
    returned = {key for entry in trace if entry.ok for key in entry.returned}
    missing = [_key(a) for a in result.attachments if _key(a) not in returned]
    return _check(
        "attachments_in_trace",
        [f"조회 이력에 없는 첨부: {_labels(missing)}"] if missing else [],
        f"첨부 {len(result.attachments)}개 모두 조회 이력에 있음",
    )


def _cited_keys(result: DiagnosisResult) -> list[EvidenceKey]:
    keys = [_key(ref) for finding in result.findings for ref in finding.evidence_refs]
    if result.diagnosis is not None:
        keys += [_key(result.diagnosis.change_document), _key(result.diagnosis.report_contract)]
    return keys


def _check_refs_in_attachments(result: DiagnosisResult) -> Check:
    attached = {_key(a) for a in result.attachments}
    missing = sorted({k for k in _cited_keys(result) if k not in attached})
    return _check(
        "refs_in_attachments",
        [f"첨부에 없는 인용: {_labels(missing)}"] if missing else [],
        "모든 인용이 첨부에 있음",
    )


def _check_attachments_loaded(
    result: DiagnosisResult, attachments: Mapping[EvidenceKey, LoadedEvidence]
) -> Check:
    not_loaded: list[EvidenceKey] = []
    mismatched: list[EvidenceKey] = []
    for ref in result.attachments:
        loaded = attachments.get(_key(ref))
        if loaded is None:
            not_loaded.append(_key(ref))
        elif not (loaded.sha256 == ref.sha256 == hashlib.sha256(loaded.content).hexdigest()):
            mismatched.append(_key(ref))
    problems = []
    if not_loaded:
        problems.append(f"내려받지 않음: {_labels(not_loaded)}")
    if mismatched:
        problems.append(f"해시 불일치: {_labels(mismatched)}")
    return _check("attachments_loaded", problems, f"첨부 {len(result.attachments)}개 해시 일치")


def _check_locations_resolve(
    result: DiagnosisResult, attachments: Mapping[EvidenceKey, LoadedEvidence]
) -> Check:
    unresolved: list[str] = []
    for finding in result.findings:
        for ref in finding.evidence_refs:
            loaded = attachments.get(_key(ref))
            if loaded is None or (
                resolve_location(loaded.content, loaded.content_type, ref.location) is None
            ):
                unresolved.append(f"{_label(_key(ref))} {ref.location}")
    return _check(
        "locations_resolve",
        [f"원문에서 찾을 수 없는 위치: {', '.join(unresolved)}"] if unresolved else [],
        "모든 인용 위치가 원문에 있음",
    )


def _check_outcome_shape(result: DiagnosisResult) -> Check:
    if result.outcome == "needs_information":
        return Check("outcome_shape", True, "needs_information — 정보 부족, 자동 완료 보류")
    if result.diagnosis is None:
        return Check("outcome_shape", False, "ready_for_handoff 인데 diagnosis 가 없음")
    return Check(
        "outcome_shape", True, f"ready_for_handoff · diagnosis.code={result.diagnosis.code}"
    )


# --- 데모 검사: response_path_changed ---------------------------------------------


class _MissingAttachment(Exception):
    """데모 판정에 필요한 첨부가 인자에 없다. 모순이 아니라 판정 불가."""

    def __init__(self, key: EvidenceKey) -> None:
        super().__init__(f"첨부 없음: {_label(key)}")


@dataclass(frozen=True)
class _Demo:
    """데모 검사가 공유하는 읽기 도우미.

    첨부가 없으면 `_MissingAttachment`, 형식이 깨졌으면 ValueError 를 던진다.
    """

    result: DiagnosisResult
    diagnosis: Diagnosis
    attachments: Mapping[EvidenceKey, LoadedEvidence]

    def loaded(self, key: EvidenceKey) -> LoadedEvidence:
        loaded = self.attachments.get(key)
        if loaded is None:
            raise _MissingAttachment(key)
        return loaded

    def json_object(self, key: EvidenceKey) -> dict[str, Any]:
        try:
            parsed = json.loads(self.loaded(key).content)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"{_label(key)} JSON 파싱 실패") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{_label(key)} 는 JSON 객체가 아님")
        return parsed

    def baseline(self) -> dict[str, Any]:
        return self.json_object((f"run-{self.diagnosis.baseline_run_id}", _RUN_RECORD_VERSION))

    def failed(self) -> dict[str, Any]:
        return self.json_object((f"run-{self.diagnosis.failed_run_id}", _RUN_RECORD_VERSION))

    def machine(self, key: EvidenceKey) -> dict[str, Any]:
        machine = self.json_object(key).get("machine")
        if not isinstance(machine, dict):
            raise ValueError(f"{_label(key)} 에 $.machine 객체가 없음")
        return machine

    def ref_key(self, record: dict[str, Any], field: str) -> EvidenceKey:
        ref = record.get(field)
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("evidence_id"), str)
            or not isinstance(ref.get("version"), str)
        ):
            raise ValueError(f"실행 기록 {record.get('run_id')!r} 의 {field} 가 evidence 참조가 아님")
        return (ref["evidence_id"], ref["version"])

    def is_list_at(self, key: EvidenceKey, path: str) -> bool | None:
        """path 의 값이 list 면 True, 다른 값이면 False, 경로가 없으면 None."""
        loaded = self.loaded(key)
        resolved = resolve_location(loaded.content, loaded.content_type, path)
        return None if resolved is None else isinstance(resolved.value, list)


def _stage_status(record: dict[str, Any], stage: str) -> str | None:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    for item in stages:
        if isinstance(item, dict) and item.get("stage") == stage:
            return item.get("status")
    return None


def _aware_datetime(value: Any, what: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{what} 이 문자열이 아님: {value!r}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{what} 에 시간대가 없음: {value!r}")
    return parsed


def _demo_runs_same_workflow_and_version(demo: _Demo) -> Check:
    baseline, failed = demo.baseline(), demo.failed()
    problems = []
    for field in ("workflow_id", "code_version"):
        if baseline.get(field) != failed.get(field):
            problems.append(f"{field} {baseline.get(field)!r} ≠ {failed.get(field)!r}")
    if baseline.get("status") != "succeeded":
        problems.append(f"baseline status={baseline.get('status')!r}")
    if failed.get("status") != "failed":
        problems.append(f"failed status={failed.get('status')!r}")
    return _check(
        "runs_same_workflow_and_version",
        problems,
        f"workflow_id={failed.get('workflow_id')} · code_version={failed.get('code_version')}"
        " · succeeded → failed",
    )


def _demo_failed_run_http_ok_then_transform_failed(demo: _Demo) -> Check:
    failed = demo.failed()
    problems = []
    if failed.get("http_status") != 200:
        problems.append(f"http_status={failed.get('http_status')!r}")
    if _stage_status(failed, "transform") != "failed":
        problems.append(f"transform={_stage_status(failed, 'transform')!r}")
    if _stage_status(failed, "render") != "skipped":
        problems.append(f"render={_stage_status(failed, 'render')!r}")
    log_key = demo.ref_key(failed, "log_ref")
    log_lines = demo.loaded(log_key).content.decode("utf-8", errors="replace").split("\n")
    if not any("stage=transform" in line and "ERROR" in line for line in log_lines):
        problems.append(f"{_label(log_key)} 에 stage=transform ERROR 줄 없음")
    return _check(
        "failed_run_http_ok_then_transform_failed",
        problems,
        f"http_status=200 · transform=failed · render=skipped · {_label(log_key)} 에 변환 오류 줄 있음",
    )


def _demo_paths_differ_as_claimed(demo: _Demo) -> Check:
    old_path, new_path = demo.diagnosis.old_path, demo.diagnosis.new_path
    baseline_key = demo.ref_key(demo.baseline(), "response_ref")
    failed_key = demo.ref_key(demo.failed(), "response_ref")
    problems = []
    if demo.is_list_at(baseline_key, old_path) is not True:
        problems.append(f"{_label(baseline_key)} 에 {old_path} 목록 없음")
    if demo.is_list_at(baseline_key, new_path) is not None:
        problems.append(f"{_label(baseline_key)} 에 {new_path} 가 있음")
    if demo.is_list_at(failed_key, new_path) is not True:
        problems.append(f"{_label(failed_key)} 에 {new_path} 목록 없음")
    if demo.is_list_at(failed_key, old_path) is not None:
        problems.append(f"{_label(failed_key)} 에 {old_path} 가 있음")
    return _check(
        "paths_differ_as_claimed",
        problems,
        f"{_label(baseline_key)}: {old_path} 만 · {_label(failed_key)}: {new_path} 만",
    )


def _demo_change_document_matches(demo: _Demo) -> Check:
    key = _key(demo.diagnosis.change_document)
    machine = demo.machine(key)
    workflow_ids = {demo.baseline().get("workflow_id"), demo.failed().get("workflow_id")}
    problems = []
    if workflow_ids != {machine.get("workflow_id")}:
        problems.append(
            f"workflow_id {machine.get('workflow_id')!r} ≠ 실행 기록 {sorted(map(str, workflow_ids))}"
        )
    claimed_paths = (("old_path", demo.diagnosis.old_path), ("new_path", demo.diagnosis.new_path))
    for field, claimed in claimed_paths:
        if machine.get(field) != claimed:
            problems.append(f"{field} {machine.get(field)!r} ≠ diagnosis {claimed!r}")
    return _check(
        "change_document_matches",
        problems,
        f"{_label(key)} $.machine: workflow_id·old_path·new_path 일치",
    )


def _demo_change_effective_before_failure(demo: _Demo) -> Check:
    key = _key(demo.diagnosis.change_document)
    effective_at = _aware_datetime(
        demo.machine(key).get("effective_at"), f"{_label(key)} effective_at"
    )
    baseline_started = _aware_datetime(demo.baseline().get("started_at"), "baseline started_at")
    failed_started = _aware_datetime(demo.failed().get("started_at"), "failed started_at")
    ordered = baseline_started < effective_at <= failed_started
    times = (
        f"baseline {baseline_started.isoformat()} · effective_at {effective_at.isoformat()}"
        f" · failed {failed_started.isoformat()}"
    )
    return _check(
        "change_effective_before_failure",
        [] if ordered else [f"evidence_conflict: 적용 시각이 baseline 뒤·failed 이하가 아님 ({times})"],
        f"baseline < effective_at ≤ failed ({times})",
    )


def _demo_report_contract_supports_both(demo: _Demo) -> Check:
    key = _key(demo.diagnosis.report_contract)
    machine = demo.machine(key)
    supported = machine.get("supported_paths")
    supported = supported if isinstance(supported, list) else []
    problems = [
        f"supported_paths 에 {path} 없음"
        for path in (demo.diagnosis.old_path, demo.diagnosis.new_path)
        if path not in supported
    ]
    workflow_id = demo.failed().get("workflow_id")
    if machine.get("workflow_id") != workflow_id:
        problems.append(f"workflow_id {machine.get('workflow_id')!r} ≠ 실행 기록 {workflow_id!r}")
    return _check(
        "report_contract_supports_both",
        problems,
        f"{_label(key)} supported_paths 에 {demo.diagnosis.old_path}·{demo.diagnosis.new_path} 있음",
    )


def _demo_run_ids_consistent(demo: _Demo) -> Check:
    diagnosis = demo.diagnosis
    problems = []
    if demo.result.run_id != diagnosis.failed_run_id:
        problems.append(
            f"result.run_id {demo.result.run_id!r} ≠ failed_run_id {diagnosis.failed_run_id!r}"
        )
    for name, record, expected in (
        ("baseline", demo.baseline(), diagnosis.baseline_run_id),
        ("failed", demo.failed(), diagnosis.failed_run_id),
    ):
        if record.get("run_id") != expected:
            problems.append(f"{name} 기록 run_id {record.get('run_id')!r} ≠ {expected!r}")
    return _check(
        "run_ids_consistent",
        problems,
        f"baseline={diagnosis.baseline_run_id} · failed={diagnosis.failed_run_id}"
        " · result.run_id 일치",
    )


_DEMO_CHECKS: tuple[tuple[str, Callable[[_Demo], Check]], ...] = (
    ("runs_same_workflow_and_version", _demo_runs_same_workflow_and_version),
    ("failed_run_http_ok_then_transform_failed", _demo_failed_run_http_ok_then_transform_failed),
    ("paths_differ_as_claimed", _demo_paths_differ_as_claimed),
    ("change_document_matches", _demo_change_document_matches),
    ("change_effective_before_failure", _demo_change_effective_before_failure),
    ("report_contract_supports_both", _demo_report_contract_supports_both),
    ("run_ids_consistent", _demo_run_ids_consistent),
)


# --- 진입점 -----------------------------------------------------------------------


def verify_diagnosis(
    result: DiagnosisResult,
    attachments: Mapping[EvidenceKey, LoadedEvidence],
    trace: Sequence[TraceEntry],
) -> Verdict:
    """A 결과를 자동 완료해도 되는지 판정한다. 인자로 받은 첨부·이력만 본다."""
    checks = [
        _check_attachments_in_trace(result, trace),
        _check_refs_in_attachments(result),
        _check_attachments_loaded(result, attachments),
        _check_locations_resolve(result, attachments),
        _check_outcome_shape(result),
    ]
    if not all(c.passed for c in checks):
        return Verdict("failed", tuple(checks))
    if result.outcome == "needs_information" or result.diagnosis is None:
        return Verdict("undecidable", tuple(checks))

    diagnosis = result.diagnosis
    if diagnosis.code != "response_path_changed":
        checks.append(
            Check("diagnosis_code_supported", False, f"자동 판정을 지원하지 않는 code: {diagnosis.code}")
        )
        return Verdict("undecidable", tuple(checks))

    demo = _Demo(result=result, diagnosis=diagnosis, attachments=attachments)
    contradiction = False  # 첨부 원문과 diagnosis 의 모순 또는 깨진 형식 → failed
    missing = False  # 판정에 필요한 첨부 부족 → undecidable (모순이 하나라도 있으면 failed 가 우선)
    for code, run in _DEMO_CHECKS:
        try:
            check = run(demo)
            contradiction = contradiction or not check.passed
        except _MissingAttachment as exc:
            check = Check(code, False, str(exc))
            missing = True
        except ValueError as exc:
            check = Check(code, False, str(exc))
            contradiction = True
        checks.append(check)

    if contradiction:
        return Verdict("failed", tuple(checks))
    if missing:
        return Verdict("undecidable", tuple(checks))
    return Verdict("passed", tuple(checks))
