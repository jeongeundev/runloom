"""맡기기 규칙 — 정책 값·승인 요청 원인 키(ARCHITECTURE "사람 사이 인계 — phase 17", ADR-0023)."""

import pytest

from workflow.domain.delegation import (
    APPROVAL_STATES,
    DELEGATION_POLICIES,
    OWNER_APPROVAL_CODE,
    OWNER_APPROVAL_PREFIX,
    ApprovalFact,
    approval_cause_key,
    approval_deciders,
    approval_question,
    approval_state,
    can_decide_approval,
    can_set_policy,
    candidate_label,
    declined_reason,
    needs_owner_approval,
    offline_reason,
    parse_approval_cause_key,
)
from workflow.domain.team import MemberFact


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


# --- 승인 필요·승인자 ---

OWNER = "mem-00000001"
OTHER = "mem-00000002"
ADMIN = "mem-0000000a"
ADMINS = frozenset({ADMIN})


@pytest.mark.parametrize("policy, requester, owner, expected", [
    # 정책 run 은 아무도 묻지 않는다
    ("run", OWNER, OWNER, False),
    ("run", OTHER, OWNER, False),
    ("run", ADMIN, OWNER, False),
    ("run", None, OWNER, False),
    ("run", OWNER, None, False),
    ("run", OTHER, None, False),
    ("run", ADMIN, None, False),
    ("run", None, None, False),
    # 소유자 있음 — 소유자 본인만 묻지 않는다(관리자도 묻는다)
    ("owner_approval", OWNER, OWNER, False),
    ("owner_approval", OTHER, OWNER, True),
    ("owner_approval", ADMIN, OWNER, True),
    ("owner_approval", None, OWNER, True),  # 맡긴 사람 모름(자동 착수)은 소유자가 아니다
    # 공용 — 활성 관리자가 소유자 역할
    ("owner_approval", OWNER, None, True),
    ("owner_approval", OTHER, None, True),
    ("owner_approval", ADMIN, None, False),
    ("owner_approval", None, None, True),
])
def test_needs_owner_approval_table(policy, requester, owner, expected):
    assert needs_owner_approval(policy=policy, requester_id=requester, owner_id=owner, admin_ids=ADMINS) is expected


MEMBERS = (
    MemberFact("mem-0000000b", "admin", True),
    MemberFact(OWNER, "member", True),
    MemberFact("mem-0000000c", "admin", False),
    MemberFact(ADMIN, "admin", True),
    MemberFact("mem-00000003", "member", False),
)


def test_approval_deciders_owner_or_active_admins():
    assert approval_deciders(OWNER, MEMBERS) == (OWNER,)
    assert approval_deciders(None, MEMBERS) == ("mem-0000000b", ADMIN)  # 공용 — members 순서
    assert approval_deciders("mem-00000003", MEMBERS) == ("mem-0000000b", ADMIN)  # 비활성 소유자
    assert approval_deciders("mem-99999999", MEMBERS) == ("mem-0000000b", ADMIN)  # 모르는 소유자


@pytest.mark.parametrize("member, role, owner, expected", [
    (OWNER, "member", OWNER, True),
    (OTHER, "member", OWNER, False),
    (ADMIN, "admin", OWNER, True),
    (OWNER, "member", None, False),  # 공용은 관리자만
    (ADMIN, "admin", None, True),
])
def test_can_decide_approval(member, role, owner, expected):
    assert can_decide_approval(member_id=member, role=role, owner_id=owner) is expected


@pytest.mark.parametrize("member, role, owner, expected", [
    (OWNER, "member", OWNER, True),
    (OTHER, "member", OWNER, False),
    (ADMIN, "admin", OWNER, True),
    (OWNER, "member", None, False),
    (ADMIN, "admin", None, True),
    (OWNER, "guest", OWNER, False),  # 모르는 역할은 동작이 없다
])
def test_can_set_policy_follows_runner_removal_rule(member, role, owner, expected):
    assert can_set_policy(member_id=member, role=role, owner_id=owner) is expected


# --- 승인 상태 ---


def test_approval_states_constant():
    assert APPROVAL_STATES == ("not_needed", "approved", "pending", "declined", "missing")
    assert ApprovalFact("pending", "이유") == ApprovalFact(state="pending", reason="이유")


@pytest.mark.parametrize("latest_state, latest_action, expected", [
    (None, None, "missing"),
    ("open", None, "pending"),
    ("answered", "approve", "approved"),
    ("answered", "decline", "declined"),
    ("answered", "withdraw", "missing"),
])
def test_approval_state_table(latest_state, latest_action, expected):
    assert approval_state(needed=True, latest_state=latest_state, latest_action=latest_action) == expected
    assert approval_state(needed=False, latest_state=latest_state, latest_action=latest_action) == "not_needed"


# --- 문구 ---


def test_approval_question():
    assert approval_question("김OO", "이OO", "opensql") == (
        "김OO 가 맡김 · 이OO 승인 대기\n에이전트 opensql — [승인]하면 곧 시작합니다.")
    assert approval_question(None, "이OO", "opensql").split("\n")[0] == "자동으로 맡김 · 이OO 승인 대기"
    assert approval_question("김OO", None, "opensql").split("\n")[0] == "김OO 가 맡김 · 관리자 승인 대기"


def test_declined_reason():
    assert declined_reason("이OO", None) == "이OO 가 거절"
    assert declined_reason("이OO", "  ") == "이OO 가 거절"
    assert declined_reason("이OO", "오늘은 Mac 을 못 씁니다\n내일 다시") == "이OO 가 거절 — 오늘은 Mac 을 못 씁니다"
    assert declined_reason("이OO", "가" * 100) == "이OO 가 거절 — " + "가" * 80
    assert declined_reason(None, None) == "관리자 가 거절"


def test_offline_reason():
    assert offline_reason("이OO") == "이OO의 러너 꺼짐 · 켜지면 시작"
    assert offline_reason(None) == "공용 러너 꺼짐 · 켜지면 시작"


def test_candidate_label():
    assert candidate_label("opensql", "이OO", True) == "opensql · 이OO의 Mac · 켜짐"
    assert candidate_label("opensql", None, False) == "opensql · 공용 · 꺼짐"
