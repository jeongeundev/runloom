"""맡기기 규칙 — 정책 값·승인 요청 원인 키(ARCHITECTURE "사람 사이 인계 — phase 17", ADR-0023)."""

import pytest

from workflow.domain.delegation import (
    DELEGATION_POLICIES,
    OWNER_APPROVAL_CODE,
    OWNER_APPROVAL_PREFIX,
    approval_cause_key,
    parse_approval_cause_key,
)


def test_constants():
    assert DELEGATION_POLICIES == ("run", "owner_approval")
    assert OWNER_APPROVAL_CODE == "owner_approval"
    assert OWNER_APPROVAL_PREFIX == "owner_approval:"


def test_approval_cause_key_round_trips():
    assert approval_cause_key("agent-codex-mac", "mem-0000000a", 1) == "owner_approval:agent-codex-mac:mem-0000000a:1"
    assert approval_cause_key("agent-codex-mac", None, 2) == "owner_approval:agent-codex-mac:none:2"
    assert parse_approval_cause_key("owner_approval:agent-codex-mac:mem-0000000a:1") == (
        "agent-codex-mac", "mem-0000000a", 1)
    assert parse_approval_cause_key("owner_approval:agent-codex-mac:none:3") == ("agent-codex-mac", None, 3)


@pytest.mark.parametrize("key", [
    "ready:agent-codex-mac",  # 다른 요청
    "owner_approval:agent-codex-mac:none",  # seq 없음
    "owner_approval:agent-codex-mac:none:0",  # seq 는 1 이상
    "owner_approval:agent-codex-mac:none:x",
    "owner_approval::none:1",  # 에이전트 없음
    "owner_approval:agent-codex-mac::1",  # 맡긴 사람 칸 비어 있음
])
def test_parse_approval_cause_key_rejects_other_keys(key):
    assert parse_approval_cause_key(key) is None


def test_approval_cause_key_rejects_bad_seq():
    with pytest.raises(ValueError):
        approval_cause_key("agent-codex-mac", None, 0)
