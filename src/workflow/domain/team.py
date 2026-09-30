"""팀 — 비밀번호·이메일·역할 × 동작·"내 차례" 받는 사람. ARCHITECTURE "팀 — phase 15", ADR-0021.

DB·HTTP 를 보지 않는다. 표준 `hashlib`·`hmac`·`secrets`·`base64` 만 쓴다.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
_SCRYPT_N_MAX = 2**20  # DB 조작으로 CPU·메모리를 쓰게 하지 않는다
_SCRYPT_RP_MAX = 16
_SALT_BYTES = 16

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128

# 이메일이 없거나 비활성인 로그인 시도도 한 번 검증해 응답 시간을 맞춘다. 어떤 입력과도 맞지 않는다.
DUMMY_PASSWORD_HASH = (
    "scrypt$16384$8$1$MCghoZnu3vPzMi0gZ01dfg==$IxqUzQzyB8x+gaAxg1Wvkl74CFjD45KmahkrYb8dD84="
)

_EMAIL_MAX_LENGTH = 254
_DISPLAY_NAME_MAX_LENGTH = 40


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(pw: str, *, n: int | None = None) -> str:
    n = SCRYPT_N if n is None else n
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(pw: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return False
    if n < 2 or n > _SCRYPT_N_MAX or n & (n - 1):
        return False
    if not (1 <= r <= _SCRYPT_RP_MAX and 1 <= p <= _SCRYPT_RP_MAX) or not salt or not expected:
        return False
    try:
        actual = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(pw: str) -> str | None:
    if len(pw) < PASSWORD_MIN_LENGTH:
        return f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    if len(pw) > PASSWORD_MAX_LENGTH:
        return f"비밀번호는 {PASSWORD_MAX_LENGTH}자 이하여야 합니다."
    if not pw.strip():
        return "비밀번호를 공백만으로 만들 수 없습니다."
    return None


def normalize_email(s: str) -> str | None:
    value = s.strip().lower()
    if not value or len(value) > _EMAIL_MAX_LENGTH:
        return None
    if any(ch.isspace() or not ch.isprintable() for ch in value):
        return None
    local, at, domain = value.partition("@")
    if not at or not local or not domain or "@" in domain:
        return None
    return value


def clean_display_name(s: str) -> str | None:
    value = s.strip()
    if not value or len(value) > _DISPLAY_NAME_MAX_LENGTH:
        return None
    if any(not ch.isprintable() for ch in value):
        return None
    return value


# --- 역할 × 동작 ---

ROLES = ("admin", "member")

MANAGE_CONNECTIONS = "manage_connections"
MANAGE_RULES = "manage_rules"
MANAGE_TEAM = "manage_team"
MANAGE_SHARED_NOTIFY = "manage_shared_notify"
ATTACH_RUNNER = "attach_runner"
REMOVE_ANY_RUNNER = "remove_any_runner"
CREATE_WORK = "create_work"
DELEGATE = "delegate"
RESPOND = "respond"
VIEW_METRICS = "view_metrics"
EDIT_OWN_SETTINGS = "edit_own_settings"

ACTIONS: tuple[str, ...] = (
    MANAGE_CONNECTIONS,
    MANAGE_RULES,
    MANAGE_TEAM,
    MANAGE_SHARED_NOTIFY,
    ATTACH_RUNNER,
    REMOVE_ANY_RUNNER,
    CREATE_WORK,
    DELEGATE,
    RESPOND,
    VIEW_METRICS,
    EDIT_OWN_SETTINGS,
)

_ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(ACTIONS),
    "member": frozenset(
        {ATTACH_RUNNER, CREATE_WORK, DELEGATE, RESPOND, VIEW_METRICS, EDIT_OWN_SETTINGS}
    ),
}


def can(role: str, action: str) -> bool:
    if action not in ACTIONS:
        raise ValueError(f"모르는 동작: {action}")
    return action in _ROLE_ACTIONS.get(role, frozenset())


def allowed_actions(role: str) -> frozenset[str]:
    return _ROLE_ACTIONS.get(role, frozenset())


# --- "내 차례" 받는 사람 ---


@dataclass(frozen=True)
class MemberFact:
    member_id: str
    role: str
    active: bool


def turn_recipients(
    *,
    assignee_type: str | None,
    assignee_id: str | None,
    requested_by_member_id: str | None,
    members: Sequence[MemberFact],
) -> tuple[str, ...]:
    active = {m.member_id for m in members if m.active}
    if assignee_type == "member" and assignee_id in active:
        return (assignee_id,)
    if requested_by_member_id in active:
        return (requested_by_member_id,)
    return tuple(m.member_id for m in members if m.active and m.role == "admin")
