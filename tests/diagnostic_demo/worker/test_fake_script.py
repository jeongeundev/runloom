"""fixture 기반 fake 대본 — 테스트·로컬 e2e 전용. 정답을 만드는 것이 아니라 대본이 실제 도구·검증기와 맞는지 확인한다."""

from diagnostic_demo.tools.api import Tools
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.fake_script import fixture_script, needs_information_script
from tests.diagnostic_demo.conftest import contract_results
from tests.diagnostic_demo.worker.test_model import DRAFT_KEYS


def _replay(script, fixture_store) -> tuple[ToolTraceRecorder, list[dict]]:
    """대본의 도구 호출을 실제 Tools 로 실행한다. 인자 오류면 ValueError 가 난다."""
    recorder = ToolTraceRecorder()
    tools = Tools(fixture_store, recorder)
    outputs = [tools.call(c.name, c.arguments) for turn in script for c in turn.tool_calls]
    return recorder, outputs


def test_fixture_script_matches_contract_section_5_and_reads_before_citing(fixture_store):
    script = fixture_script()
    assert [len(t.tool_calls) for t in script] == [1] * 8 + [0]
    assert [c.name for t in script for c in t.tool_calls] == ["get_run", "list_runs"] + ["read_evidence"] * 6
    draft = script[-1].draft
    expected = contract_results()[0]
    assert draft.model_dump(mode="json") == {k: expected[k] for k in DRAFT_KEYS}

    recorder, outputs = _replay(script, fixture_store)
    assert all(o["ok"] for o in outputs)
    cited = {(r.evidence_id, r.version) for f in draft.findings for r in f.evidence_refs}
    cited |= {(d.evidence_id, d.version) for d in (draft.diagnosis.change_document, draft.diagnosis.report_contract)}
    assert cited <= recorder.returned_set()
    assert len(script) <= 15
    assert all(t.input_tokens > 0 and t.output_tokens > 0 for t in script)


def test_needs_information_script_skips_the_change_document(fixture_store):
    script = needs_information_script()
    names = [(c.name, c.arguments.get("evidence_id")) for t in script for c in t.tool_calls]
    assert ("read_evidence", "upstream-response-change") not in names
    draft = script[-1].draft
    expected = contract_results()[1]
    assert draft.model_dump(mode="json") == {k: expected[k] for k in DRAFT_KEYS}
    recorder, _ = _replay(script, fixture_store)
    cited = {(r.evidence_id, r.version) for f in draft.findings for r in f.evidence_refs}
    assert cited <= recorder.returned_set()


def test_scripts_are_fresh_lists_each_call():
    a, b = fixture_script(), fixture_script()
    assert a == b and a is not b and a[0] is not b[0]
