"""계약 v1 모델의 계약 테스트.

`docs/CONTRACT.md` 가 fixture 다. 문서의 ```json 펜스 블록 57개와 표 안의 인라인
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
    BUILTIN_KIND_NAMES,
    BUILTIN_KINDS,
    BUILTIN_RULES,
    CONTRACT_VERSION,
    ArtifactCreated,
    ArtifactMeta,
    CallbackTask,
    Capability,
    ChainCallback,
    ClaimRequest,
    CodeChangeResult,
    CodeChangeTarget,
    CodeReviewResult,
    CommitReviewTarget,
    DiagnosisResult,
    DiagnosisTarget,
    ErrorBody,
    EvidenceRef,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionUsage,
    GenericResult,
    HandoffBundle,
    HeartbeatRequest,
    InboundChainRequest,
    InboundChainResponse,
    InboundItem,
    InputRef,
    KindSpec,
    LocalTarget,
    ReviewComment,
    ReviewFinding,
    RunStatus,
    SelectionRecord,
    SuccessorRule,
    parse_rfc3339_aware,
)
from workflow.contracts.github import AssigneeBinding, GitHubIssueSnapshot, GitHubSourceConfig, SourceDelivery
from workflow.server.machine_api import RegistrationRequest, RegistrationResponse

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
    ("RegistrationResponse", lambda k: k == {"agent_id", "created"}, RegistrationResponse),
    ("HandoffBundle", lambda k: "source_execution_id" in k and "reviewed_commit" not in k, HandoffBundle),
    ("ExecutionEvent", lambda k: {"seq", "type"} <= k, ExecutionEvent),
    ("ArtifactMeta", lambda k: {"kind", "sha256", "size", "contract_version"} <= k, ArtifactMeta),
    (
        "ArtifactCreated",
        lambda k: {"artifact_id", "sha256"} <= k and "contract_version" not in k,
        ArtifactCreated,
    ),
    ("DiagnosisResult", lambda k: {"outcome", "findings", "run_id"} <= k, DiagnosisResult),
    ("CodeReviewResult", lambda k: "reviewed_commit" in k, CodeReviewResult),
    ("CodeChangeResult", lambda k: {"outcome", "base_commit"} <= k, CodeChangeResult),
    ("GenericResult", lambda k: {"outcome", "kind"} <= k, GenericResult),
    ("ReviewComment", lambda k: "decision" in k, ReviewComment),
    ("SelectionRecord", lambda k: "required_capability" in k, SelectionRecord),
    ("ErrorBody", lambda k: {"code", "message"} <= k, ErrorBody),
    ("KindSpec", lambda k: "capability_code" in k, KindSpec),
    ("SuccessorRule", lambda k: "from_kind" in k, SuccessorRule),
    ("InboundChainRequest", lambda k: "items" in k, InboundChainRequest),
    ("InboundChainResponse", lambda k: "started" in k, InboundChainResponse),
    ("ChainCallback", lambda k: "human_gate" in k, ChainCallback),
    ("GitHubSourceConfig", lambda k: "label_filter" in k, GitHubSourceConfig),
    ("AssigneeBinding", lambda k: "github_user_id" in k, AssigneeBinding),
    ("GitHubIssueSnapshot", lambda k: "is_pull_request" in k, GitHubIssueSnapshot),
    ("SourceDelivery", lambda k: "delivery_id" in k, SourceDelivery),
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
    assert len(FENCED) == 57
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
        "generic_result",
        "code_review_result",
    )
    assert len(ARTIFACT_KINDS) == 17


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


def test_claim_registration_heads_are_optional_commit_shas():
    block = _first("ClaimRequest")
    assert ClaimRequest.model_validate(block).registration_heads is None
    heads = ClaimRequest.model_validate({**block, "registration_heads": {"OpenArchive": "a" * 40}})
    assert heads.registration_heads == {"OpenArchive": "a" * 40}
    for bad in ({"OpenArchive": "A" * 40}, {"OpenArchive": "a" * 39}, {"": "a" * 40},
                {f"reg-{i}": "a" * 40 for i in range(51)}):
        with pytest.raises(ValidationError):
            ClaimRequest.model_validate({**block, "registration_heads": bad})


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


# --- 측정 칸 (CONTRACT 3.1절, ADR-0015) ---------------------------------------

_SHA40 = "9f3c2a1b7d4e5f60718293a4b5c6d7e8f9012345"


def _event(type_: str, data: dict) -> dict:
    return {
        "contract_version": 1,
        "execution_id": "exec-fix-001",
        "seq": 2,
        "occurred_at": "2026-09-20T01:00:03+09:00",
        "type": type_,
        "data": data,
    }


def _measure_blocks(type_: str) -> list[dict]:
    return [b for b in FENCED if _model_for(b) is ExecutionEvent and b["type"] == type_]


def test_contract_md_has_measure_field_examples():
    assert any("folder_commit" in b["data"] for b in _measure_blocks("started"))
    assert any(b["data"].get("usage") for b in _measure_blocks("result_ready"))
    failed = [b for b in _measure_blocks("failed") if "usage" in b["data"]]
    assert failed and failed[0]["data"]["usage"]["cost_usd"] is None


@pytest.mark.parametrize(
    ("type_", "data"),
    [
        ("started", {"runtime_ref": "pid:1"}),
        ("result_ready", {"result_artifact_id": "art-1"}),
        ("failed", {"code": "timeout", "message": "m", "process_stopped": True}),
    ],
)
def test_events_without_measure_fields_still_pass(type_, data):
    event = ExecutionEvent.model_validate(_event(type_, data))
    if type_ == "started":
        assert event.data.folder_commit is None and event.data.folder_dirty is None
    else:
        assert event.data.usage is None


def test_started_accepts_folder_commit_and_dirty():
    event = ExecutionEvent.model_validate(
        _event("started", {"runtime_ref": "pid:1", "folder_commit": _SHA40, "folder_dirty": True})
    )
    assert event.data.folder_commit == _SHA40 and event.data.folder_dirty is True


def test_started_accepts_folder_commit_without_dirty():
    event = ExecutionEvent.model_validate(_event("started", {"runtime_ref": "pid:1", "folder_commit": _SHA40}))
    assert event.data.folder_dirty is None


def test_started_rejects_folder_dirty_without_commit():
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(_event("started", {"runtime_ref": "pid:1", "folder_dirty": False}))


@pytest.mark.parametrize("commit", ["abc", _SHA40.upper(), _SHA40 + "0"])
def test_started_rejects_malformed_folder_commit(commit):
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(_event("started", {"runtime_ref": "pid:1", "folder_commit": commit}))


@pytest.mark.parametrize("type_", ["result_ready", "failed"])
def test_terminal_events_accept_usage(type_):
    base = (
        {"result_artifact_id": "art-1"}
        if type_ == "result_ready"
        else {"code": "timeout", "message": "m", "process_stopped": True}
    )
    usage = {"cost_usd": 0.42, "input_tokens": 10, "output_tokens": 5}
    event = ExecutionEvent.model_validate(_event(type_, {**base, "usage": usage}))
    assert event.data.usage == ExecutionUsage(**usage)


def test_usage_fields_default_to_unknown():
    usage = ExecutionUsage.model_validate({})
    assert (usage.cost_usd, usage.input_tokens, usage.output_tokens) == (None, None, None)


def test_usage_accepts_integer_cost_from_json():
    usage = ExecutionUsage.model_validate_json('{"cost_usd": 0, "input_tokens": 0, "output_tokens": 0}')
    assert usage.cost_usd == 0.0 and isinstance(usage.cost_usd, float)
    assert ExecutionUsage.model_validate({"cost_usd": 1}).cost_usd == 1.0


@pytest.mark.parametrize(
    "usage",
    [
        {"cost_usd": -0.01},
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"input_tokens": 1.5},
        {"input_tokens": "10"},
        {"cost_usd": True},
        {"cost_usd": 0.1, "cache_tokens": 3},
    ],
)
def test_usage_rejects_invalid_values(usage):
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(_event("result_ready", {"result_artifact_id": "art-1", "usage": usage}))


@pytest.mark.parametrize(
    ("type_", "data"),
    [
        ("started", {"runtime_ref": "pid:1"}),
        ("result_ready", {"result_artifact_id": "art-1"}),
        ("failed", {"code": "timeout", "message": "m", "process_stopped": True}),
    ],
)
def test_events_without_measure_fields_serialize_unchanged(type_, data):
    """구버전 서버는 새 칸을 null 이라도 422 로 거부한다 — 값이 없으면 직렬화에서 뺀다."""
    event = ExecutionEvent.model_validate(_event(type_, {**data}))
    assert event.data.model_dump() == data
    assert json.loads(event.model_dump_json())["data"] == data


def test_measure_fields_serialize_when_present():
    started = ExecutionEvent.model_validate(
        _event("started", {"runtime_ref": "pid:1", "folder_commit": _SHA40, "folder_dirty": False})
    )
    assert started.data.model_dump(mode="json")["folder_dirty"] is False
    usage = {"cost_usd": None, "input_tokens": 3, "output_tokens": None}
    ready = ExecutionEvent.model_validate(_event("result_ready", {"result_artifact_id": "a", "usage": usage}))
    assert ready.data.model_dump(mode="json")["usage"] == usage


def test_measure_fields_rejected_on_other_event_types():
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(_event("progress", {"message": "m", "usage": None}))
    with pytest.raises(ValidationError):
        ExecutionEvent.model_validate(_event("result_ready", {"result_artifact_id": "a", "folder_commit": _SHA40}))


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


@pytest.mark.parametrize("code", ["review", "code.modify", "operations.diagnose", "a1.b_2"])
def test_capability_accepts_code_pattern(code):
    assert Capability.model_validate({"code": code, "scope": {"repository_id": "x"}}).code == code


@pytest.mark.parametrize("code", ["Code.Modify", "a.b.c", "", ".a", "a.", "1a", "a-b", "a b"])
def test_capability_rejects_bad_code(code):
    with pytest.raises(ValidationError):
        Capability.model_validate({"code": code, "scope": {"repository_id": "x"}})


@pytest.mark.parametrize(
    "scope",
    [{}, {"repository_id": "x", "workflow_id": "y"}, {"repository_id": ""}, {"Repo": "x"}, {"repo-id": "x"}],
)
def test_capability_rejects_bad_scope(scope):
    """scope 는 식별자 키 정확히 하나와 비어 있지 않은 값. 코드 ↔ 키 대응은 서버가 등록부로 검사한다."""
    with pytest.raises(ValidationError):
        Capability.model_validate({"code": "code.modify", "scope": scope})


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


@pytest.mark.parametrize(
    "model_name",
    ["DiagnosisResult", "ExecutionRequest", "InboundChainRequest", "InboundChainResponse", "ChainCallback"],
)
def test_json_roundtrip(model_name):
    for block in FENCED:
        model = _model_for(block)
        if model.__name__ != model_name:
            continue
        parsed = model.model_validate(block)
        dumped = parsed.model_dump(mode="json")
        expected = dict(block)
        if model is ExecutionRequest:
            expected.setdefault("kind_spec", None)  # 내장 종류 요청은 생략 가능, dump 에는 null
        assert dumped == expected
        assert model.model_validate(dumped) == parsed


# --- 업무 종류·후속 규칙·범용 결과 (CONTRACT 11절) ---------------------------


def _kind_spec(**overrides) -> dict:
    spec = next(
        json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is KindSpec and not b["builtin"]
    )
    spec.update(overrides)
    return spec


def _rule(**overrides) -> dict:
    rule = next(json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is SuccessorRule)
    rule.update(overrides)
    return rule


def _review_request() -> dict:
    return next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is ExecutionRequest and b["kind"] == "review"
    )


def test_builtin_kinds_match_concept_table():
    assert BUILTIN_KIND_NAMES == ("diagnosis", "code_change", "bug_fix", "code_review")
    assert tuple(k.kind for k in BUILTIN_KINDS) == BUILTIN_KIND_NAMES
    diagnosis, code_change, bug_fix, code_review = BUILTIN_KINDS
    assert diagnosis == KindSpec(
        kind="diagnosis", label="진단", capability_code="operations.diagnose", scope_key="workflow_id",
        input_kinds=[], output_kind="diagnosis_result",
        outcomes=["ready_for_handoff", "needs_information"], instructions="", builtin=True,
    )
    assert code_change == KindSpec(
        kind="code_change", label="코드 수정", capability_code="code.modify", scope_key="repository_id",
        input_kinds=["diagnosis_result", "evidence"], output_kind="code_change_result",
        outcomes=["ready_for_review", "needs_information"], instructions="", builtin=True,
    )
    assert bug_fix == KindSpec(
        kind="bug_fix", label="버그 수정", capability_code="code.fix", scope_key="repository_id",
        input_kinds=[], output_kind="code_change_result",
        outcomes=["ready_for_review", "needs_information"], instructions="", builtin=True,
    )
    assert code_review == KindSpec(
        kind="code_review", label="커밋 검토", capability_code="code.review", scope_key="repository_id",
        input_kinds=["code_change_result"], output_kind="code_review_result",
        outcomes=["approved", "changes_requested", "needs_information"], instructions="", builtin=True,
    )


def test_builtin_rules_match_concept_table():
    assert BUILTIN_RULES == (
        SuccessorRule(
            from_kind="diagnosis", on_outcomes=["ready_for_handoff"], to_kind="code_change",
            handoff_kinds=["diagnosis_result", "evidence"],
        ),
        SuccessorRule(
            from_kind="bug_fix", on_outcomes=["ready_for_review"], to_kind="code_review",
            handoff_kinds=["code_change_result", "diff", "test_log_after", "verification_log"],
        ),
    )


def test_builtin_code_change_kind_equals_contract_md_example():
    block = next(b for b in FENCED if _model_for(b) is KindSpec and b["builtin"])
    assert KindSpec.model_validate(block) == BUILTIN_KINDS[1]


def test_builtin_rule_equals_contract_md_example():
    block = next(b for b in FENCED if _model_for(b) is SuccessorRule and b["from_kind"] == "diagnosis")
    assert SuccessorRule.model_validate(block) == BUILTIN_RULES[0]


@pytest.mark.parametrize("kind", ["Review", "re view", "r" * 41, "r", "1review", "review-x", ""])
def test_kind_spec_rejects_bad_kind(kind):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(kind=kind))


@pytest.mark.parametrize("capability_code", ["Code.Modify", "a.b.c", "review-x", ""])
def test_kind_spec_rejects_bad_capability_code(capability_code):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(capability_code=capability_code))


@pytest.mark.parametrize("scope_key", ["Repository", "repo id", "r" * 41, ""])
def test_kind_spec_rejects_bad_scope_key(scope_key):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(scope_key=scope_key))


@pytest.mark.parametrize("outcomes", [[], ["approved", "approved"], ["Approved"], ["needs info"]])
def test_kind_spec_rejects_bad_outcomes(outcomes):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(outcomes=outcomes))


@pytest.mark.parametrize("input_kinds", [["diff", "diff"], ["not_a_kind"]])
def test_kind_spec_rejects_bad_input_kinds(input_kinds):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(input_kinds=input_kinds))


def test_kind_spec_accepts_empty_input_kinds():
    assert KindSpec.model_validate(_kind_spec(input_kinds=[])).input_kinds == []


@pytest.mark.parametrize("output_kind", ["diagnosis_result", "code_change_result", "code_review_result"])
def test_kind_spec_rejects_user_defined_with_builtin_output(output_kind):
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(output_kind=output_kind))


def test_kind_spec_rejects_unknown_builtin_name():
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(builtin=True))


def test_kind_spec_rejects_unknown_output_kind():
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(output_kind="review_result"))


def test_successor_rule_rejects_same_from_and_to():
    with pytest.raises(ValidationError):
        SuccessorRule.model_validate(_rule(to_kind=_rule()["from_kind"]))


def test_successor_rule_rejects_handoff_bundle_in_handoff_kinds():
    with pytest.raises(ValidationError):
        SuccessorRule.model_validate(_rule(handoff_kinds=["diagnosis_result", "handoff_bundle"]))


@pytest.mark.parametrize("handoff_kinds", [["diff", "diff"], ["not_a_kind"]])
def test_successor_rule_rejects_bad_handoff_kinds(handoff_kinds):
    with pytest.raises(ValidationError):
        SuccessorRule.model_validate(_rule(handoff_kinds=handoff_kinds))


def test_successor_rule_accepts_empty_handoff_kinds():
    assert SuccessorRule.model_validate(_rule(handoff_kinds=[])).handoff_kinds == []


@pytest.mark.parametrize("on_outcomes", [[], ["ready_for_handoff", "ready_for_handoff"], ["Ready"]])
def test_successor_rule_rejects_bad_on_outcomes(on_outcomes):
    with pytest.raises(ValidationError):
        SuccessorRule.model_validate(_rule(on_outcomes=on_outcomes))


@pytest.mark.parametrize("field", ["from_kind", "to_kind"])
def test_successor_rule_rejects_bad_kind_identifier(field):
    with pytest.raises(ValidationError):
        SuccessorRule.model_validate(_rule(**{field: "Bad Kind"}))


@pytest.mark.parametrize(
    ("target", "model"),
    [
        ({"run_id": "daily-0920-0900"}, DiagnosisTarget),
        (
            {
                "local_registration_id": "local-demo-report",
                "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
                "verification_profile_id": "vp-pytest",
            },
            CodeChangeTarget,
        ),
        ({"local_registration_id": "local-demo-report-claude"}, LocalTarget),
    ],
)
def test_execution_request_target_roundtrips_to_same_class(target, model):
    """`extra="forbid"` 라 LocalTarget 이 CodeChangeTarget 으로(또는 반대로) 읽히지 않는다."""
    kind = {DiagnosisTarget: "diagnosis", CodeChangeTarget: "code_change", LocalTarget: "review"}[model]
    block = _review_request() if model is LocalTarget else _first("ExecutionRequest")
    block.update(kind=kind, target=target)
    if model is CodeChangeTarget:
        block["input_artifact_ids"] = ["art-handoff-001"]
    parsed = ExecutionRequest.model_validate(block)
    assert type(parsed.target) is model
    dumped = parsed.model_dump(mode="json")
    assert dumped["target"] == target
    assert type(ExecutionRequest.model_validate(dumped).target) is model


def test_execution_request_review_with_local_target_and_kind_spec_passes():
    request = ExecutionRequest.model_validate(_review_request())
    assert request.kind == "review"
    assert isinstance(request.target, LocalTarget)
    assert request.kind_spec is not None and request.kind_spec.kind == "review"


def test_execution_request_review_requires_kind_spec():
    block = _review_request()
    block["kind_spec"] = None
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)
    del block["kind_spec"]
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_execution_request_rejects_kind_spec_kind_mismatch():
    block = _review_request()
    block["kind_spec"]["kind"] = "audit"
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)
    diagnosis = _first("ExecutionRequest")
    diagnosis["kind_spec"] = BUILTIN_KINDS[1].model_dump()  # code_change 봉투를 diagnosis 요청에
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(diagnosis)


def test_execution_request_accepts_builtin_kind_spec_when_it_matches():
    diagnosis = _first("ExecutionRequest")
    diagnosis["kind_spec"] = BUILTIN_KINDS[0].model_dump()
    assert ExecutionRequest.model_validate(diagnosis).kind_spec == BUILTIN_KINDS[0]


def test_execution_request_rejects_builtin_kind_spec_with_local_target():
    block = _review_request()
    block["kind_spec"]["builtin"] = True
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_execution_request_rejects_local_target_for_builtin_kind():
    diagnosis = _first("ExecutionRequest")
    diagnosis["target"] = {"local_registration_id": "local-demo-report"}
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(diagnosis)
    code_change = _first("ExecutionRequest")
    code_change.update(kind="code_change", input_artifact_ids=["art-handoff-001"],
                       target={"local_registration_id": "local-demo-report"})
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(code_change)


def test_execution_request_rejects_builtin_target_for_user_kind():
    block = _review_request()
    block["target"] = {"run_id": "daily-0920-0900"}
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


@pytest.mark.parametrize("kind", ["Review", "re view", "r" * 41, "r", ""])
def test_execution_request_rejects_bad_kind_identifier(kind):
    block = _review_request()
    block["kind"] = kind
    block["kind_spec"]["kind"] = kind
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_handoff_bundle_rejects_duplicate_input_artifact_id():
    block = next(json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is HandoffBundle and b["inputs"])
    block["inputs"].append(dict(block["inputs"][0]))
    with pytest.raises(ValidationError):
        HandoffBundle.model_validate(block)


def test_handoff_bundle_rejects_bad_source_kind():
    block = _first("HandoffBundle")
    block["source_kind"] = "Diagnosis"
    with pytest.raises(ValidationError):
        HandoffBundle.model_validate(block)


def test_handoff_bundle_accepts_empty_inputs_and_attachments():
    block = _first("HandoffBundle")
    block["inputs"] = []
    block["attachments"] = []
    bundle = HandoffBundle.model_validate(block)
    assert bundle.inputs == [] and bundle.attachments == []


def test_input_ref_rejects_bad_fields():
    good = {"kind": "diff", "artifact_id": "art-diff-001", "sha256": "c" * 64, "content_type": "text/x-diff"}
    InputRef.model_validate(good)
    for bad in ({"kind": "not_a_kind"}, {"sha256": "C" * 64}, {"artifact_id": ""}, {"content_type": ""}):
        with pytest.raises(ValidationError):
            InputRef.model_validate({**good, **bad})


def test_generic_result_roundtrip():
    block = _first("GenericResult")
    parsed = GenericResult.model_validate(block)
    assert parsed.kind == "review" and parsed.outcome == "approved"
    dumped = parsed.model_dump(mode="json")
    assert dumped == block
    assert GenericResult.model_validate(dumped) == parsed


def test_generic_result_rejects_duplicate_artifact_ids():
    block = _first("GenericResult")
    block["artifact_ids"] = ["a", "a"]
    with pytest.raises(ValidationError):
        GenericResult.model_validate(block)


@pytest.mark.parametrize("outcome", ["Approved", "needs info", "", "o" * 41])
def test_generic_result_rejects_bad_outcome(outcome):
    block = _first("GenericResult")
    block["outcome"] = outcome
    with pytest.raises(ValidationError):
        GenericResult.model_validate(block)


def test_local_target_shape():
    LocalTarget.model_validate({"local_registration_id": "local-demo-report-claude"})
    with pytest.raises(ValidationError):
        LocalTarget.model_validate({"local_registration_id": ""})
    with pytest.raises(ValidationError):
        LocalTarget.model_validate({"local_registration_id": "l", "base_commit": "3" * 40})


# --- n8n 입구·callback (CONTRACT 12절) ---------------------------------------


def _inbound_request() -> dict:
    return _first("InboundChainRequest")


def _item(key: str, blocked_by: list[str] | None = None) -> dict:
    return {"key": key, "title": f"제목 {key}", "body": "본문", "labels": [], "blocked_by": blocked_by or []}


def test_inbound_chain_request_accepts_contract_md_example():
    block = _inbound_request()
    request = InboundChainRequest.model_validate(block)
    assert [item.key for item in request.items] == ["run-daily-0920", "fix-format"]
    assert request.items[1].blocked_by == ["run-daily-0920"]
    assert request.callback_url == "http://localhost:5678/webhook-waiting/1234"


def test_inbound_chain_request_callback_url_is_optional():
    block = _inbound_request()
    del block["callback_url"]
    assert InboundChainRequest.model_validate(block).callback_url is None
    block["callback_url"] = None
    assert InboundChainRequest.model_validate(block).callback_url is None


@pytest.mark.parametrize("callback_url", ["https://n8n.example/webhook-waiting/1", "http://localhost:5678"])
def test_inbound_chain_request_keeps_callback_url_verbatim(callback_url):
    """HttpUrl 정규화(끝 `/` 추가 등)가 없어야 n8n 의 resume URL 과 같은 문자열로 남는다."""
    block = _inbound_request()
    block["callback_url"] = callback_url
    assert InboundChainRequest.model_validate(block).callback_url == callback_url


@pytest.mark.parametrize(
    "callback_url",
    [
        "ftp://localhost:5678/x",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "localhost:5678/webhook-waiting/1",
        "http://localhost:5678/web hook",
        "http://localhost:5678/x\n",
        "",
        "http://" + "a" * 2042,
    ],
    ids=["ftp", "javascript", "file", "no-scheme", "space", "newline", "empty", "over-2048"],
)
def test_inbound_chain_request_rejects_bad_callback_url(callback_url):
    block = _inbound_request()
    block["callback_url"] = callback_url
    with pytest.raises(ValidationError):
        InboundChainRequest.model_validate(block)


def test_inbound_chain_request_accepts_one_and_ten_items():
    block = _inbound_request()
    block["items"] = [_item("only")]
    assert len(InboundChainRequest.model_validate(block).items) == 1
    block["items"] = [_item(f"k{i}") for i in range(10)]
    assert len(InboundChainRequest.model_validate(block).items) == 10


@pytest.mark.parametrize(
    "items",
    [
        [],
        [_item(f"k{i}") for i in range(11)],
        [_item("a"), _item("a")],
        [_item("a", ["a"])],
        [_item("a"), _item("b", ["fix"])],
    ],
    ids=["empty", "eleven", "duplicate-key", "self-reference", "unknown-key"],
)
def test_inbound_chain_request_rejects_bad_items(items):
    block = _inbound_request()
    block["items"] = items
    with pytest.raises(ValidationError):
        InboundChainRequest.model_validate(block)


def test_inbound_chain_request_rejects_unknown_field():
    block = _inbound_request()
    block["kind"] = "diagnosis"
    with pytest.raises(ValidationError):
        InboundChainRequest.model_validate(block)


def test_inbound_item_rejects_unknown_field_and_non_string_label():
    # 매핑 경로는 라벨 규칙 하나 — `kind`·`scope`·`run_id` 같은 별도 필드는 없다 (ADR-0010)
    for extra in ({"kind": "diagnosis"}, {"run_id": "daily-0920-0900"}, {"url": None}):
        with pytest.raises(ValidationError):
            InboundItem.model_validate({**_item("a"), **extra})
    with pytest.raises(ValidationError):
        InboundItem.model_validate({**_item("a"), "labels": ["incident", 1]})


def test_inbound_item_requires_all_five_fields():
    for missing in ("key", "title", "body", "labels", "blocked_by"):
        item = _item("a")
        del item[missing]
        with pytest.raises(ValidationError):
            InboundItem.model_validate(item)
    with pytest.raises(ValidationError):
        InboundItem.model_validate(_item(""))


def test_inbound_chain_response_started_false_carries_error_body():
    block = next(b for b in FENCED if _model_for(b) is InboundChainResponse and not b["started"])
    response = InboundChainResponse.model_validate(block)
    assert response.start_error == ErrorBody(
        code="selection_required", message="담당 에이전트를 먼저 확정하세요.", field=None, details=None
    )
    assert [t.kind for t in response.tasks] == ["diagnosis", "code_change"]


def test_inbound_chain_response_rejects_bad_task_ref():
    block = next(json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is InboundChainResponse)
    block["tasks"][0]["kind"] = "Diagnosis"
    with pytest.raises(ValidationError):
        InboundChainResponse.model_validate(block)
    block = next(json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is InboundChainResponse)
    block["tasks"][0]["status"] = ""
    with pytest.raises(ValidationError):
        InboundChainResponse.model_validate(block)


def test_inbound_chain_response_accepts_null_chain_url():
    block = _first("InboundChainResponse")
    block["chain_url"] = None
    assert InboundChainResponse.model_validate(block).chain_url is None


@pytest.mark.parametrize("model_name", ["InboundChainRequest", "InboundChainResponse", "ChainCallback"])
def test_inbound_models_dump_in_contract_md_field_order(model_name):
    for block in FENCED:
        model = _model_for(block)
        if model.__name__ != model_name:
            continue
        assert list(model.model_validate(block).model_dump(mode="json")) == list(block)


def test_chain_callback_rejects_empty_tasks():
    block = _first("ChainCallback")
    block["tasks"] = []
    with pytest.raises(ValidationError):
        ChainCallback.model_validate(block)


def test_chain_callback_rejects_non_n8n_source():
    block = _first("ChainCallback")
    block["source"] = "github"
    with pytest.raises(ValidationError):
        ChainCallback.model_validate(block)


def test_chain_callback_rejects_naive_settled_at():
    block = _first("ChainCallback")
    block["settled_at"] = "2026-09-22T12:34:56"
    with pytest.raises(ValidationError):
        ChainCallback.model_validate(block)


def test_chain_callback_accepts_null_urls_and_missing_result():
    block = _first("ChainCallback")
    block["chain_url"] = None
    block["tasks"][0].update(outcome=None, summary=None, task_url=None)
    parsed = ChainCallback.model_validate(block)
    assert parsed.chain_url is None
    assert parsed.tasks[0] == CallbackTask(
        task_id="task-8a1b2c3d4e5f", key="run-daily-0920", kind="diagnosis",
        title="일일 보고서 2026-09-20 09:00 실행 실패", status="완료", status_reason="판정 근거: 14/14",
        outcome=None, summary=None, task_url=None,
    )


# --- GitHub 업무 순환 확장 (CONTRACT 13절) -------------------------------------


def _request_of(kind: str) -> dict:
    return next(
        json.loads(json.dumps(b)) for b in FENCED if _model_for(b) is ExecutionRequest and b["kind"] == kind
    )


def _review_result(outcome: str) -> dict:
    return next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is CodeReviewResult and b["outcome"] == outcome
    )


def _needs_information_review() -> dict:
    block = _review_result("approved")
    block.update(outcome="needs_information", findings=[], missing_information=["재현 조건이 이슈에 없습니다."])
    return block


def test_sections_1_to_12_fixtures_are_unchanged():
    """추가형 확장 — 13절 이전의 json 블록 수는 phase 7 의 35개 + phase 9 3.1절 측정 예시 3개다."""
    text = CONTRACT_MD.read_text(encoding="utf-8")
    before_13 = text.split("## 13. GitHub 업무 순환", 1)[0]
    assert len(_FENCE.findall(before_13)) == 38


def test_builtin_cycle_kinds_equal_contract_md_examples():
    blocks = {b["kind"]: b for b in FENCED if _model_for(b) is KindSpec and b["builtin"]}
    assert KindSpec.model_validate(blocks["bug_fix"]) == BUILTIN_KINDS[2]
    assert KindSpec.model_validate(blocks["code_review"]) == BUILTIN_KINDS[3]


def test_builtin_bug_fix_rule_equals_contract_md_example():
    block = next(b for b in FENCED if _model_for(b) is SuccessorRule and b["from_kind"] == "bug_fix")
    assert SuccessorRule.model_validate(block) == BUILTIN_RULES[1]


def test_artifact_meta_accepts_code_review_result_kind():
    meta = _first("ArtifactMeta")
    meta["kind"] = "code_review_result"
    assert ArtifactMeta.model_validate(meta).kind == "code_review_result"


def test_bug_fix_request_roundtrip_with_empty_inputs():
    """일반 버그 수정은 진단 인계가 없다 — 첫 시도의 입력이 비어도 된다 (code_change 는 여전히 422)."""
    block = _request_of("bug_fix")
    assert block["input_artifact_ids"] == []
    parsed = ExecutionRequest.model_validate(block)
    assert type(parsed.target) is CodeChangeTarget
    dumped = parsed.model_dump(mode="json")
    assert dumped == {**block, "kind_spec": None}
    assert ExecutionRequest.model_validate(dumped) == parsed


def test_bug_fix_request_accepts_rework_inputs():
    block = _request_of("bug_fix")
    block["input_artifact_ids"] = ["art-fix-result-001", "art-gh-review-result-001"]
    assert ExecutionRequest.model_validate(block).input_artifact_ids == block["input_artifact_ids"]


def test_code_review_request_roundtrip():
    block = _request_of("code_review")
    parsed = ExecutionRequest.model_validate(block)
    assert parsed.target == CommitReviewTarget(
        local_registration_id="local-billing-claude", source_execution_id="exec-gh-fix-001",
        base_commit="5d1c9a3e7b2f4c6a8e0d1b3f5a7c9e2d4b6f8a0c",
        result_commit="8e2a4c6f0b1d3e5a7c9f2b4d6e8a0c1f3b5d7e9a",
    )
    dumped = parsed.model_dump(mode="json")
    assert dumped == {**block, "kind_spec": None}
    assert type(ExecutionRequest.model_validate(dumped).target) is CommitReviewTarget


def test_code_review_request_requires_input_artifacts():
    block = _request_of("code_review")
    block["input_artifact_ids"] = []
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


@pytest.mark.parametrize(
    "target",
    [
        {"local_registration_id": "local-billing"},
        {"run_id": "daily-0920-0900"},
        {
            "local_registration_id": "local-billing", "source_execution_id": "exec-gh-fix-001",
            "base_commit": "5" * 40, "result_commit": "8" * 40,
        },
    ],
    ids=["local", "diagnosis", "commit-review"],
)
def test_bug_fix_request_rejects_other_targets(target):
    block = _request_of("bug_fix")
    block["target"] = target
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


@pytest.mark.parametrize(
    "target",
    [
        {"local_registration_id": "local-billing-claude"},
        {"local_registration_id": "l", "base_commit": "5" * 40, "verification_profile_id": "vp-pytest"},
    ],
    ids=["local", "code-change"],
)
def test_code_review_request_rejects_other_targets(target):
    block = _request_of("code_review")
    block["target"] = target
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_existing_kinds_reject_commit_review_target():
    commit_review = _request_of("code_review")["target"]
    code_change = _request_of("code_change")
    code_change["target"] = commit_review
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(code_change)
    review = _review_request()
    review["target"] = commit_review
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(review)


@pytest.mark.parametrize("profile", [None, "", 7], ids=["null", "empty", "int"])
def test_bug_fix_request_rejects_bad_verification_profile_reference(profile):
    """검증 프로필은 로컬 등록에 있는 ID 문자열 참조뿐이다 — 명령·빈 값·다른 타입은 계약에서 거부."""
    block = _request_of("bug_fix")
    block["target"]["verification_profile_id"] = profile
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)
    del block["target"]["verification_profile_id"]
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


def test_bug_fix_request_rejects_command_in_target():
    block = _request_of("bug_fix")
    block["target"]["verification_command"] = "pytest -q"
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(block)


@pytest.mark.parametrize("version", [2, 0, "1"])
def test_cycle_payloads_reject_other_contract_versions(version):
    for block in (_request_of("bug_fix"), _request_of("code_review")):
        block["contract_version"] = version
        with pytest.raises(ValidationError):
            ExecutionRequest.model_validate(block)
    result = _review_result("approved")
    result["contract_version"] = version
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(result)


def test_commit_review_target_rules():
    good = _request_of("code_review")["target"]
    CommitReviewTarget.model_validate(good)
    for bad in (
        {"result_commit": "8e2a4c6f"},
        {"base_commit": "Z" * 40},
        {"source_execution_id": ""},
        {"local_registration_id": ""},
        {"result_commit": good["base_commit"]},  # 바뀐 커밋이 없는 검토
    ):
        with pytest.raises(ValidationError):
            CommitReviewTarget.model_validate({**good, **bad})
    missing = dict(good)
    del missing["source_execution_id"]
    with pytest.raises(ValidationError):
        CommitReviewTarget.model_validate(missing)


@pytest.mark.parametrize("outcome", ["changes_requested", "approved"])
def test_code_review_result_roundtrip_in_contract_md_field_order(outcome):
    block = _review_result(outcome)
    parsed = CodeReviewResult.model_validate(block)
    dumped = parsed.model_dump(mode="json")
    assert dumped == block
    assert list(dumped) == list(block)
    assert CodeReviewResult.model_validate(dumped) == parsed


def test_code_review_result_needs_information_accepted():
    parsed = CodeReviewResult.model_validate(_needs_information_review())
    assert parsed.missing_information == ["재현 조건이 이슈에 없습니다."]


@pytest.mark.parametrize("outcome", ["rejected", "ready_for_review", "Approved", "", None])
def test_code_review_result_rejects_unknown_outcome(outcome):
    block = _review_result("approved")
    block["outcome"] = outcome
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(block)


def test_changes_requested_requires_blocking_finding():
    block = _review_result("changes_requested")
    block["findings"] = [f for f in block["findings"] if f["severity"] != "blocking"]
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(block)


def test_approved_rejects_blocking_finding_but_allows_non_blocking():
    block = _review_result("approved")
    block["findings"] = [{"severity": "non_blocking", "path": None, "line": None, "message": "사소함"}]
    CodeReviewResult.model_validate(block)
    block["findings"].append({"severity": "blocking", "path": "a.py", "line": 1, "message": "막힘"})
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(block)


def test_needs_information_requires_missing_information():
    block = _needs_information_review()
    block["missing_information"] = []
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(block)


@pytest.mark.parametrize("outcome", ["approved", "changes_requested"])
def test_decided_review_rejects_missing_information(outcome):
    block = _review_result(outcome)
    block["missing_information"] = ["무언가 없음"]
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(block)


def test_code_review_result_rejects_bad_fields():
    for bad in (
        {"reviewed_commit": "8e2a4c6f"},
        {"source_execution_id": ""},
        {"artifact_ids": ["a", "a"]},
        {"missing_information": [""]},
    ):
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate({**_review_result("approved"), **bad})


@pytest.mark.parametrize("field", ["agent_id", "assignee", "command", "workdir", "local_path", "merge"])
def test_review_and_fix_results_do_not_carry_authority_fields(field):
    """모델이 담당자·셸 명령·로컬 경로를 출력해도 계약 필드가 아니다 — 권한으로 해석되지 않고 거부된다."""
    review = _review_result("approved")
    review[field] = "x"
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(review)
    finding = _review_result("changes_requested")
    finding["findings"][0][field] = "x"
    with pytest.raises(ValidationError):
        CodeReviewResult.model_validate(finding)
    fix = next(
        json.loads(json.dumps(b))
        for b in FENCED
        if _model_for(b) is CodeChangeResult and b["outcome"] == "ready_for_review"
    )
    fix[field] = "x"
    with pytest.raises(ValidationError):
        CodeChangeResult.model_validate(fix)


def test_review_finding_path_is_display_text_only():
    finding = ReviewFinding.model_validate(
        {"severity": "blocking", "path": "../../etc/passwd; rm -rf /", "line": 3, "message": "m"}
    )
    assert finding.path == "../../etc/passwd; rm -rf /"


def test_review_finding_rules():
    good = {"severity": "blocking", "path": "billing/coupon.py", "line": 42, "message": "m"}
    ReviewFinding.model_validate(good)
    for bad in (
        {"severity": "critical"},
        {"line": 0},
        {"line": "42"},
        {"path": None},  # 줄 번호는 파일이 있을 때만
        {"message": ""},
    ):
        with pytest.raises(ValidationError):
            ReviewFinding.model_validate({**good, **bad})


def test_claim_request_supported_kinds_optional_for_old_connectors():
    old = _first("ClaimRequest")
    assert "supported_kinds" not in old
    assert ClaimRequest.model_validate(old).supported_kinds is None
    new = next(b for b in FENCED if _model_for(b) is ClaimRequest and "supported_kinds" in b)
    parsed = ClaimRequest.model_validate(new)
    assert parsed.supported_kinds == ["code_change", "bug_fix", "code_review"]
    assert parsed.model_dump(mode="json", exclude_none=True) == new


@pytest.mark.parametrize(
    "kinds", [["bug_fix", "bug_fix"], ["Bug Fix"], ["bug-fix"], "bug_fix"], ids=["dup", "space", "dash", "str"]
)
def test_claim_request_rejects_bad_supported_kinds(kinds):
    block = _first("ClaimRequest")
    block["supported_kinds"] = kinds
    with pytest.raises(ValidationError):
        ClaimRequest.model_validate(block)


@pytest.mark.parametrize("kind", ["bug_fix", "code_review", "diagnosis", "code_change"])
def test_user_defined_kind_cannot_take_builtin_name(kind):
    """내장 이름은 예약어다 — 사용자 정의 `bug_fix` 가 있으면 ExecutionRequest 의 target 규칙과 어긋난다."""
    with pytest.raises(ValidationError):
        KindSpec.model_validate(_kind_spec(kind=kind))
