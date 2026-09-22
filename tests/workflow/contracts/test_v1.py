"""계약 v1 모델의 계약 테스트.

`docs/CONTRACT.md` 가 fixture 다. 문서의 ```json 펜스 블록 35개와 표 안의 인라인
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
    DiagnosisResult,
    DiagnosisTarget,
    ErrorBody,
    EvidenceRef,
    ExecutionEvent,
    ExecutionRequest,
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
    RunStatus,
    SelectionRecord,
    SuccessorRule,
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
    ("GenericResult", lambda k: {"outcome", "kind"} <= k, GenericResult),
    ("ReviewComment", lambda k: "decision" in k, ReviewComment),
    ("SelectionRecord", lambda k: "required_capability" in k, SelectionRecord),
    ("ErrorBody", lambda k: {"code", "message"} <= k, ErrorBody),
    ("KindSpec", lambda k: "capability_code" in k, KindSpec),
    ("SuccessorRule", lambda k: "from_kind" in k, SuccessorRule),
    ("InboundChainRequest", lambda k: "items" in k, InboundChainRequest),
    ("InboundChainResponse", lambda k: "started" in k, InboundChainResponse),
    ("ChainCallback", lambda k: "human_gate" in k, ChainCallback),
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
    assert len(FENCED) == 35
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
    )
    assert len(ARTIFACT_KINDS) == 16


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
    assert BUILTIN_KIND_NAMES == ("diagnosis", "code_change")
    assert tuple(k.kind for k in BUILTIN_KINDS) == BUILTIN_KIND_NAMES
    diagnosis, code_change = BUILTIN_KINDS
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


def test_builtin_rules_match_concept_table():
    assert BUILTIN_RULES == (
        SuccessorRule(
            from_kind="diagnosis", on_outcomes=["ready_for_handoff"], to_kind="code_change",
            handoff_kinds=["diagnosis_result", "evidence"],
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


@pytest.mark.parametrize("output_kind", ["diagnosis_result", "code_change_result"])
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
