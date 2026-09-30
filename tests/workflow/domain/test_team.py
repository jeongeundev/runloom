"""팀 도메인 — 비밀번호·이메일·역할 × 동작·"내 차례" 받는 사람(ARCHITECTURE "팀 — phase 15", ADR-0021)."""

import base64

import pytest

from workflow.domain import team
from workflow.domain.team import (
    ACTIONS,
    DUMMY_PASSWORD_HASH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    ROLES,
    MemberFact,
    allowed_actions,
    can,
    clean_display_name,
    hash_password,
    normalize_email,
    password_problem,
    turn_recipients,
    verify_password,
)


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(team, "SCRYPT_N", 2**4)


# --- 비밀번호 해시 ---


def test_hash_round_trip():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterx", stored)


def test_hash_format_and_params_from_module_constant():
    stored = hash_password("pw-1234567890")
    algo, n, r, p, salt, digest = stored.split("$")
    assert (algo, n, r, p) == ("scrypt", str(2**4), "8", "1")
    assert len(base64.b64decode(salt, validate=True)) == 16
    assert len(base64.b64decode(digest, validate=True)) == 32


def test_explicit_n_overrides_constant():
    stored = hash_password("pw-1234567890", n=2**5)
    assert stored.split("$")[1] == str(2**5)
    assert verify_password("pw-1234567890", stored)


def test_same_password_gets_different_hash():
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_reads_params_from_stored_string(monkeypatch):
    stored = hash_password("pw-1234567890", n=2**6)
    monkeypatch.setattr(team, "SCRYPT_N", 2**8)  # 지금 상수가 바뀌어도 저장된 n 으로 검증
    assert verify_password("pw-1234567890", stored)


def _replace(stored, index, value):
    parts = stored.split("$")
    parts[index] = value
    return "$".join(parts)


@pytest.mark.parametrize(
    "broken",
    [
        "",
        "plain",
        "scrypt$16$8$1$abc",  # 칸 부족
        "bcrypt$16$8$1$AAAA$AAAA",  # 모르는 알고리즘
        "scrypt$x$8$1$AAAA$AAAA",  # 숫자 아님
        "scrypt$16$8$1$@@@@$AAAA",  # base64 아님
    ],
)
def test_broken_stored_string_is_false(broken):
    assert verify_password("pw-1234567890", broken) is False


@pytest.mark.parametrize("n", ["15", "1", "0", "-16", str(2**21)])
def test_bad_n_is_false(n):
    stored = _replace(hash_password("pw-1234567890"), 1, n)
    assert verify_password("pw-1234567890", stored) is False


@pytest.mark.parametrize("index,value", [(2, "0"), (3, "0"), (2, "-1")])
def test_bad_r_p_is_false(index, value):
    stored = _replace(hash_password("pw-1234567890"), index, value)
    assert verify_password("pw-1234567890", stored) is False


def test_dummy_hash_is_valid_format_and_matches_nothing():
    assert DUMMY_PASSWORD_HASH.startswith(f"scrypt${2**14}$8$1$")
    assert verify_password("", DUMMY_PASSWORD_HASH) is False
    assert verify_password("pw-1234567890", DUMMY_PASSWORD_HASH) is False


# --- 비밀번호 규칙 ---


def test_password_rule_boundaries():
    assert (PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH) == (10, 128)
    assert password_problem("a" * 9) == "비밀번호는 10자 이상이어야 합니다."
    assert password_problem("a" * 10) is None
    assert password_problem("가" * 10) is None  # 글자 수 기준
    assert password_problem("a" * 128) is None
    assert password_problem("a" * 129) == "비밀번호는 128자 이하여야 합니다."
    assert password_problem("") == "비밀번호는 10자 이상이어야 합니다."


def test_password_whitespace_only_rejected():
    assert password_problem(" " * 12) == "비밀번호를 공백만으로 만들 수 없습니다."
    assert password_problem(" pass word ") is None


# --- 이메일·표시 이름 ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Kim@Example.com", "kim@example.com"),
        ("  kim@example.com \n", "kim@example.com"),
        ("a@b", "a@b"),
        ("", None),
        ("   ", None),
        ("kim", None),
        ("@example.com", None),
        ("kim@", None),
        ("a@b@c", None),
        ("k im@example.com", None),
        ("kim@exa\tmple.com", None),
        ("kim\x00@example.com", None),
        ("a@" + "b" * 252, "a@" + "b" * 252),  # 254자
        ("a@" + "b" * 253, None),  # 255자
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  김OO  ", "김OO"),
        ("김 OO", "김 OO"),
        ("", None),
        ("   ", None),
        ("가" * 40, "가" * 40),
        ("가" * 41, None),
        ("김\nOO", None),
        ("김\x07", None),
    ],
)
def test_clean_display_name(raw, expected):
    assert clean_display_name(raw) == expected


# --- 역할 × 동작 ---

MEMBER_ACTIONS = {
    "attach_runner",
    "create_work",
    "delegate",
    "respond",
    "view_metrics",
    "edit_own_settings",
}


def test_roles_and_actions_constants():
    assert ROLES == ("admin", "member")
    assert ACTIONS == (
        "manage_connections",
        "manage_rules",
        "manage_team",
        "manage_shared_notify",
        "attach_runner",
        "remove_any_runner",
        "create_work",
        "delegate",
        "respond",
        "view_metrics",
        "edit_own_settings",
    )
    assert team.MANAGE_CONNECTIONS == "manage_connections"
    assert team.REMOVE_ANY_RUNNER == "remove_any_runner"
    assert team.EDIT_OWN_SETTINGS == "edit_own_settings"


@pytest.mark.parametrize("action", ACTIONS)
def test_role_action_table(action):
    assert can("admin", action) is True
    assert can("member", action) is (action in MEMBER_ACTIONS)


def test_unknown_role_is_false():
    assert can("owner", "create_work") is False
    assert can("", "create_work") is False
    assert allowed_actions("owner") == frozenset()


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        can("admin", "manage_everything")


def test_allowed_actions():
    assert allowed_actions("admin") == frozenset(ACTIONS)
    assert allowed_actions("member") == frozenset(MEMBER_ACTIONS)


# --- "내 차례" 받는 사람 ---

ADMIN_A = MemberFact("mem-0000000a", "admin", True)
ADMIN_B = MemberFact("mem-0000000b", "admin", True)
ADMIN_OFF = MemberFact("mem-0000000c", "admin", False)
KIM = MemberFact("mem-00000001", "member", True)
LEE = MemberFact("mem-00000002", "member", True)
PARK_OFF = MemberFact("mem-00000003", "member", False)
TEAM = (ADMIN_B, KIM, ADMIN_OFF, LEE, ADMIN_A, PARK_OFF)


def recipients(assignee_type=None, assignee_id=None, requested_by=None, members=TEAM, approvers=None):
    return turn_recipients(
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        requested_by_member_id=requested_by,
        members=members,
        approvers=approvers,
    )


def test_active_member_assignee():
    assert recipients("member", KIM.member_id, requested_by=LEE.member_id) == (KIM.member_id,)


def test_inactive_member_assignee_falls_to_requester():
    assert recipients("member", PARK_OFF.member_id, requested_by=LEE.member_id) == (LEE.member_id,)


def test_agent_assignee_goes_to_requester():
    assert recipients("agent", "agent-1", requested_by=KIM.member_id) == (KIM.member_id,)


def test_agent_id_matching_member_id_is_not_member():
    assert recipients("agent", KIM.member_id, requested_by=LEE.member_id) == (LEE.member_id,)


def test_unknown_assignee_member_falls_through():
    assert recipients("member", "mem-99999999", requested_by=KIM.member_id) == (KIM.member_id,)


def test_inactive_requester_goes_to_active_admins_in_given_order():
    assert recipients("agent", "agent-1", requested_by=PARK_OFF.member_id) == (
        ADMIN_B.member_id,
        ADMIN_A.member_id,
    )


def test_nobody_goes_to_active_admins():
    assert recipients() == (ADMIN_B.member_id, ADMIN_A.member_id)


def test_no_active_admin_is_empty():
    assert recipients(members=(KIM, ADMIN_OFF)) == ()
    assert recipients(members=()) == ()


# --- 열린 소유자 승인 요청 (phase 17) ---


def test_open_owner_approval_goes_to_owner_before_assignee_and_requester():
    assert recipients("member", KIM.member_id, requested_by=LEE.member_id, approvers=(LEE.member_id,)) == (
        LEE.member_id,)
    assert recipients("agent", "agent-1", requested_by=KIM.member_id, approvers=(LEE.member_id,)) == (LEE.member_id,)


def test_open_owner_approval_on_shared_runner_goes_to_admins():
    admins = (ADMIN_B.member_id, ADMIN_A.member_id)

    assert recipients("agent", "agent-1", requested_by=KIM.member_id, approvers=admins) == admins


def test_no_open_owner_approval_keeps_existing_rule():
    assert recipients("agent", "agent-1", requested_by=KIM.member_id, approvers=None) == (KIM.member_id,)
    assert recipients("agent", "agent-1", requested_by=KIM.member_id) == (KIM.member_id,)


def test_empty_approvers_are_returned_as_is():
    assert recipients("agent", "agent-1", requested_by=KIM.member_id, approvers=()) == ()
