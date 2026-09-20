"""계약 v1 모델의 계약 테스트.

`docs/CONTRACT.md` 가 fixture 다. 문서의 ```json 펜스 블록 24개와 표 안의 인라인
JSON 8개를 추출해, 키 서명으로 모델에 대응시킨 뒤 검증에 성공해야 한다.
문서를 고쳐서 테스트를 통과시키지 않는다 — 모순이 있으면 모델 또는 문서의 버그다.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow.contracts import v1
from workflow.contracts.v1 import (
    ARTIFACT_KINDS,
    CONTRACT_VERSION,
    ArtifactCreated,
    ArtifactMeta,
    Capability,
    ClaimRequest,
    CodeChangeResult,
    CodeChangeTarget,
    DiagnosisResult,
    DiagnosisTarget,
    ErrorBody,
    EvidenceRef,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
    HeartbeatRequest,
    ReviewComment,
    RunStatus,
    SelectionRecord,
    parse_rfc3339_aware,
)
from workflow.server.machine_api import RegistrationRequest

CONTRACT_MD = Path(__file__).resolve().parents[3] / "docs" / "CONTRACT.md"

_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_INLINE = re.compile(r"\|\s*`(\{.*?\})`\s*\|")

# (이름, 키 서명 판정, 모델). 블록마다 정확히 하나만 참이어야 한다.
_SIGNATURES = [
    ("ExecutionRequest", lambda k: {"kind", "target"} <= k, ExecutionRequest),
    ("RunStatus", lambda k: {"status", "last_event_seq"} <= k, RunStatus),
    (
        "ClaimRequest",
        lambda k: "connector_id" in k and "current_execution_id" not in k and "local_registration_id" not in k,
        ClaimRequest,
    ),
    ("RegistrationRequest", lambda k: {"local_registration_id", "tool"} <= k, RegistrationRequest),
    ("HandoffBundle", lambda k: "source_execution_id" in k, HandoffBundle),
    ("ExecutionEvent", lambda k: {"seq", "type"} <= k, ExecutionEvent),
    ("ArtifactMeta", lambda k: {"kind", "sha256", "size", "contract_version"} <= k, ArtifactMeta),
    (
        "ArtifactCreated",
        lambda k: {"artifact_id", "sha256"} <= k and "contract_version" not in k,
        ArtifactCreated,
    ),
    ("DiagnosisResult", lambda k: {"outcome", "findings"} <= k, DiagnosisResult),
    ("CodeChangeResult", lambda k: {"outcome", "base_commit"} <= k, CodeChangeResult),
    ("ReviewComment", lambda k: "decision" in k, ReviewComment),
    ("SelectionRecord", lambda k: "required_capability" in k, SelectionRecord),
    ("ErrorBody", lambda k: {"code", "message"} <= k, ErrorBody),
]


def _fenced_blocks() -> list[dict]:
    text = CONTRACT_MD.read_text(encoding="utf-8")
    return [json.loads(m) for m in _FENCE.findall(text)]


def _inline_blocks() -> list[dict]:
    text = CONTRACT_MD.read_text(encoding="utf-8")
    return [json.loads(m) for m in _INLINE.findall(text)]


def _model_for(block: dict):
    keys = set(block)
    matches = [(name, model) for name, pred, model in _SIGNATURES if pred(keys)]
    assert len(matches) == 1, f"키 {sorted(keys)} 가 모델 {[m[0] for m in matches]} 에 대응됨"
    return matches[0][1]


FENCED = _fenced_blocks()
INLINE = _inline_blocks()


def test_contract_md_has_expected_block_counts():
    assert len(FENCED) == 24
    assert len(INLINE) == 8


@pytest.mark.parametrize("index", range(len(FENCED)))
def test_every_fenced_block_validates_with_exactly_one_model(index):
    block = FENCED[index]
    model = _model_for(block)
    model.model_validate(block)


@pytest.mark.parametrize("index", range(len(INLINE)))
def test_every_inline_error_body_validates(index):
    block = INLINE[index]
    assert _model_for(block) is ErrorBody
    ErrorBody.model_validate(block)


def test_all_models_are_covered_by_fixtures():
    covered = {_model_for(b).__name__ for b in FENCED}
    expected = {name for name, _, _ in _SIGNATURES}
    assert covered == expected


# --- 공통 규칙 --------------------------------------------------------------


def test_constants():
    assert CONTRACT_VERSION == 1
    assert ARTIFACT_KINDS == (
        "handoff_bundle",
        "diagnosis_result",
        "tool_trace",
        "evidence",
        "codex_jsonl",
        "codex_stderr",
        "diff",
        "test_log_before",
        "test_log_after",
        "verification_log",
        "report_output",
        "code_change_result",
        "review_comment",
        "claude_jsonl",
        "claude_stderr",
    )
    assert len(ARTIFACT_KINDS) == 15


def test_artifact_kinds_match_contract_md_list():
    """CONTRACT 4절 "산출물 `kind` 목록:" 한 줄의 백틱 이름들이 상수와 순서까지 같다."""
    text = CONTRACT_MD.read_text(encoding="utf-8")
    (line,) = [ln for ln in text.splitlines() if ln.startswith("산출물 `kind` 목록:")]
    assert tuple(re.findall(r"`([a-z_]+)`", line.split(":", 1)[1])) == ARTIFACT_KINDS


@pytest.mark.parametrize("kind", ["claude_jsonl", "claude_stderr"])
def test_artifact_meta_accepts_claude_kinds(kind):
    meta = _first("ArtifactMeta")
    meta["kind"] = kind
    assert ArtifactMeta.model_validate(meta).kind == kind


def test_registration_request_tool_literal():
    body = _first("RegistrationRequest")
    assert {b["tool"] for b in FENCED if _model_for(b) is RegistrationRequest} == {"codex", "claude"}
    body["tool"] = "gemini"
    with pytest.raises(ValidationError):
        RegistrationRequest.model_validate(body)


@pytest.mark.parametrize(
    "value",
    ["2026-09-20T00:10:01Z", "2026-09-20T01:00:00+09:00", "2026-09-20T01:00:00.123-05:00"],
)
def test_parse_rfc3339_aware_returns_original(value):
    assert parse_rfc3339_aware(value) == value


@pytest.mark.parametrize(
    "value",
    ["2026-09-20T01:00:00", "2026-09-20", "20260920T010000+0900", "", "not a time"],
)
def test_parse_rfc3339_aware_rejects_naive_or_malformed(value):
    with pytest.raises(ValueError):
        parse_rfc3339_aware(value)


def test_all_models_forbid_extra_and_are_strict():
    from pydantic import BaseModel

    models = [
        obj
        for obj in vars(v1).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]
    assert models
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert model.model_config.get("strict") is True, model.__name__


# --- 거부 사례 --------------------------------------------------------------


def _first(model_name: str) -> dict:
    for block in FENCED:
        if _model_for(block).__name__ == model_name:
            return json.loads(json.dumps(block))
    raise AssertionError(model_name)


def _diagnosis_result(outcome: str) -> dict:
    for block in FENCED:
        if _model_for(block) is DiagnosisResult and block["outcome"] == outcome:
            return json.loads(json.dumps(block))
    raise AssertionError(outcome)


def test_rejects_unknown_field():
    block = _first("ClaimRequest")
    block["extra"] = 1
    with pytest.raises(ValidationError):
        ClaimRequest.model_validate(block)


def test_rejects_unsupported_contract_version():
    block = _first("ClaimRequest")
    block["contract_version"] = 2
    with pytest.raises(ValidationError):
        ClaimRequest.model_validate(block)


def test_rejects_string_task_revision():
    block = _first("ExecutionRequest")
    block["task_revision"] = "1"
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_rejects_naive_occurred_at():
    block = _first("ExecutionEvent")
    block["occurred_at"] = "2026-09-20T01:00:00"
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(block)


def test_rejects_started_event_with_empty_data():
    block = _first("ExecutionEvent")
    block["type"] = "started"
    block["data"] = {}
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(block)


def test_rejects_event_data_with_extra_key():
    block = _first("ExecutionEvent")
    block["type"] = "progress"
    block["data"] = {"message": "m", "runtime_ref": "r"}
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(block)


def test_rejects_ready_for_handoff_without_diagnosis():
    block = _diagnosis_result("ready_for_handoff")
    block["diagnosis"] = None
    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(block)


def test_rejects_needs_information_without_missing_information():
    block = _diagnosis_result("needs_information")
    block["missing_information"] = []
    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(block)


def test_rejects_diagnosis_failed_run_id_mismatch():
    block = _diagnosis_result("ready_for_handoff")
    block["diagnosis"]["failed_run_id"] = "daily-0919-0900"
    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(block)


# 문법 위반 — 스키마 pattern 단계에서 거부된다
BAD_LOCATION_SYNTAX = [
    "$.items[*]", "$", "lines:0-1", "$.a.", "items", "$.keys()", "$.a[01]", "$.a[-1]", "$[0]", "$.a[]",
    "$.a[1", "$.a[1].", "$.a[x]",
]
# 문법은 맞지만 M ≥ N 위반 — AfterValidator 가 거부한다
BAD_LINE_ORDER = ["lines:3-2"]
GOOD_LOCATIONS = [
    "$.items", "$.data.records", "lines:2-3", "lines:4-4",
    "$.stages[1].status", "$.items[0]", "$.data.records[0].team", "$.a[0][1]",
]


@pytest.mark.parametrize("location", BAD_LOCATION_SYNTAX + BAD_LINE_ORDER)
def test_rejects_bad_location(location):
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"evidence_id": "e", "version": "1", "location": location})


@pytest.mark.parametrize("location", GOOD_LOCATIONS)
def test_accepts_good_location(location):
    EvidenceRef.model_validate({"evidence_id": "e", "version": "1", "location": location})


def test_location_schema_pattern_is_the_validator_grammar():
    """모델에 보내는 JSON Schema 에 location 문법이 `pattern` 으로 드러나고, 그 pattern 이 검증기와 같은 문법이다."""
    schema = EvidenceRef.model_json_schema()["properties"]["location"]

    assert schema["pattern"] == v1.LOCATION_PATTERN
    compiled = re.compile(schema["pattern"])
    for location in GOOD_LOCATIONS + BAD_LINE_ORDER:
        assert compiled.match(location), location
    for location in BAD_LOCATION_SYNTAX:
        assert compiled.match(location) is None, location


def test_rejects_capability_scope_key_mismatch():
    with pytest.raises(ValidationError):
        Capability.model_validate({"code": "operations.diagnose", "scope": {"repository_id": "x"}})
    with pytest.raises(ValidationError):
        Capability.model_validate({"code": "code.modify", "scope": {"workflow_id": "x"}})
    with pytest.raises(ValidationError):
        Capability.model_validate(
            {"code": "code.modify", "scope": {"repository_id": "x", "workflow_id": "y"}}
        )


def test_rejects_code_change_request_without_input_artifacts():
    block = next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is ExecutionRequest and b["kind"] == "code_change"
    )
    block["input_artifact_ids"] = []
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_rejects_kind_target_mismatch():
    diagnosis = _first("ExecutionRequest")
    assert diagnosis["kind"] == "diagnosis"
    diagnosis["target"] = {
        "local_registration_id": "local-demo-report",
        "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
        "verification_profile_id": "vp-pytest",
    }
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(diagnosis)


def test_rejects_duplicate_input_artifact_ids():
    block = _first("ExecutionRequest")
    block["input_artifact_ids"] = ["a", "a"]
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_rejects_handoff_bundle_duplicate_evidence():
    block = _first("HandoffBundle")
    block["attachments"].append(dict(block["attachments"][0]))
    with pytest.raises(ValidationError):
        HandoffBundle.model_validate(block)


def test_rejects_ready_for_review_without_result_commit():
    block = next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is CodeChangeResult and b["outcome"] == "ready_for_review"
    )
    block["result_commit"] = None
    with pytest.raises(ValidationError):
        CodeChangeResult.model_validate(block)


def test_rejects_verification_commit_mismatch():
    block = next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is CodeChangeResult and b["outcome"] == "ready_for_review"
    )
    block["verification"]["result_commit"] = block["base_commit"]
    with pytest.raises(ValidationError):
        CodeChangeResult.model_validate(block)


def test_rejects_selected_record_without_agent():
    block = _first("SelectionRecord")
    block["selected_agent_id"] = None
    with pytest.raises(ValidationError):
        SelectionRecord.model_validate(block)


def test_needs_selection_record_accepted():
    SelectionRecord.model_validate(
        {
            "task_id": "t",
            "mode": "auto",
            "required_capability": {"code": "code.modify", "scope": {"repository_id": "r"}},
            "candidate_count": 2,
            "selected_agent_id": None,
            "matched": None,
            "status": "needs_selection",
            "reason": "후보 2개 — 선택 필요",
        }
    )


def test_rejects_bad_sha256_and_commit():
    meta = _first("ArtifactMeta")
    meta["sha256"] = meta["sha256"][:-1] + "G"
    with pytest.raises(ValidationError):
        ArtifactMeta.model_validate(meta)
    with pytest.raises(ValidationError):
        CodeChangeTarget.model_validate(
            {"local_registration_id": "l", "base_commit": "3f9c2e1a", "verification_profile_id": "v"}
        )


def test_rejects_missing_required_field():
    block = _first("ExecutionRequest")
    del block["agent_id"]
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_heartbeat_request_shape():
    HeartbeatRequest.model_validate(
        {"contract_version": 1, "connector_id": "conn-mac-01", "current_execution_id": None}
    )
    with pytest.raises(ValidationError):
        HeartbeatRequest.model_validate({"contract_version": 1, "connector_id": "conn-mac-01"})


def test_targets_are_distinct():
    DiagnosisTarget.model_validate({"run_id": "daily-0920-0900"})
    with pytest.raises(ValidationError):
        DiagnosisTarget.model_validate({"run_id": ""})


# --- 왕복 ------------------------------------------------------------------


@pytest.mark.parametrize("model_name", ["DiagnosisResult", "ExecutionRequest"])
def test_json_roundtrip(model_name):
    for block in FENCED:
        model = _model_for(block)
        if model.__name__ != model_name:
            continue
        parsed = model.model_validate(block)
        dumped = parsed.model_dump(mode="json")
        assert dumped == block
        assert model.model_validate(dumped) == parsed
