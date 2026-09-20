"""prompt — Codex 에 stdin 으로 넘기는 프롬프트. 요청 원문·인계 파일 경로·작업 규칙·출력 형식을 담는다."""

import json

from workflow.connector.prompt import CODEX_RESULT_SCHEMA, build_prompt

from .conftest import make_request


def _handoff(tmp_path):
    handoff = tmp_path / "fix-daily-0920.handoff"
    handoff.mkdir()
    for name in ("manifest.json", "response-before@1.json", "response-after@1.json", "log-daily-0920@1.txt",
                 "upstream-response-change@1.json", "expected-report@1.json", "diagnosis_result.json"):
        (handoff / name).write_text("{}")
    return handoff


def test_prompt_contains_request_handoff_paths_and_rules(tmp_path):
    request = make_request()
    handoff = _handoff(tmp_path)
    worktree = tmp_path / "demo-worktrees" / "fix-daily-0920"

    text = build_prompt(request, handoff, worktree)

    assert request.request in text
    assert str(worktree) in text
    for name in sorted(p.name for p in handoff.iterdir()):
        assert str(handoff / name) in text
    assert "python3 -m pytest -q" in text
    assert "커밋하지" in text  # 연결 프로그램이 커밋한다
    assert "target_component" in text and "단서" in text  # 실제 코드에서 확인
    assert "재현" in text and "먼저" in text  # 재현 테스트 먼저
    for key in ("summary", "outcome", "files_changed", "notes"):
        assert f'"{key}"' in text
    assert "ready_for_review" in text and "needs_information" in text


def test_prompt_lists_only_existing_handoff_files(tmp_path):
    handoff = tmp_path / "empty.handoff"
    handoff.mkdir()

    text = build_prompt(make_request(), handoff, tmp_path / "wt")

    assert "response-after@1.json" not in text
    assert "인계 자료 없음" in text


def test_prompt_does_not_leak_secrets_from_request_fields(tmp_path):
    request = make_request()

    text = build_prompt(request, _handoff(tmp_path), tmp_path / "wt")

    assert "wfc_" not in text and "sk-" not in text


def test_result_schema_is_strict_object_with_four_keys():
    assert CODEX_RESULT_SCHEMA["type"] == "object"
    assert CODEX_RESULT_SCHEMA["additionalProperties"] is False
    assert sorted(CODEX_RESULT_SCHEMA["required"]) == ["files_changed", "notes", "outcome", "summary"]
    assert CODEX_RESULT_SCHEMA["properties"]["outcome"]["enum"] == ["ready_for_review", "needs_information"]
    json.dumps(CODEX_RESULT_SCHEMA)  # 직렬화 가능
