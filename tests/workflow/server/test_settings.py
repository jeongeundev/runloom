"""settings.py — 비밀값은 환경변수에서만, 비어 있으면 실패. WORKFLOW_DEV=1 은 개발 전용 예외."""

from pathlib import Path

import pytest

from workflow.server.settings import (
    ENV_KEYS,
    OPTIONAL_SECRET_KEYS,
    SECRET_KEYS,
    Limits,
    Settings,
    load_settings,
)

FULL = {
    "SESSION_SECRET": "s",
    "OPERATOR_TOKEN": "o",
    "DIAG_API_TOKEN": "d",
}


def test_missing_secrets_raise_value_error_naming_them():
    with pytest.raises(ValueError) as exc:
        load_settings({"SESSION_SECRET": "s"})
    assert "OPERATOR_TOKEN" in str(exc.value)
    assert "DIAG_API_TOKEN" in str(exc.value)
    assert "SESSION_SECRET" not in str(exc.value)


def test_empty_secret_counts_as_missing():
    with pytest.raises(ValueError):
        load_settings({**FULL, "OPERATOR_TOKEN": ""})


def test_dev_mode_generates_random_secrets_and_warns(capsys):
    first = load_settings({"WORKFLOW_DEV": "1"})
    second = load_settings({"WORKFLOW_DEV": "1"})
    assert first.session_secret and first.operator_token and first.diag_api_token
    assert first.session_secret != second.session_secret
    assert first.operator_token != second.operator_token
    err = capsys.readouterr().err
    assert "WORKFLOW_DEV" in err
    assert first.session_secret not in err  # 경고에 값을 찍지 않는다


def test_dev_mode_keeps_provided_secrets(capsys):
    s = load_settings({"WORKFLOW_DEV": "1", **FULL})
    assert (s.session_secret, s.operator_token, s.diag_api_token) == ("s", "o", "d")
    assert capsys.readouterr().err == ""


def test_defaults():
    s = load_settings(FULL)
    assert s.db_path == Path("data/central.sqlite")
    assert s.artifact_dir == Path("data/artifacts")
    assert s.secret_dir == Path("data/secrets")
    assert s.diag_api_url == "http://127.0.0.1:8100"
    assert s.session_cookie_days == 14
    assert s.limits == Limits()
    assert Limits() == Limits(
        per_session_daily=10,
        global_daily=60,
        active_tasks_per_session=5,
        attachments_max_bytes=1_048_576,
        unknown_after_seconds=120,
        heartbeat_offline_seconds=90,
    )


def test_env_overrides():
    s = load_settings({
        **FULL,
        "WORKFLOW_DB_PATH": "/tmp/x/db.sqlite",
        "WORKFLOW_ARTIFACT_DIR": "/tmp/x/art",
        "WORKFLOW_SECRET_DIR": "/tmp/x/secrets",
        "DIAG_API_URL": "http://127.0.0.1:9100",
        "WORKFLOW_LIMIT_PER_SESSION_DAILY": "3",
        "WORKFLOW_LIMIT_GLOBAL_DAILY": "7",
        "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION": "2",
        "WORKFLOW_LIMIT_ATTACHMENTS_MAX_BYTES": "1024",
        "WORKFLOW_LIMIT_UNKNOWN_AFTER_SECONDS": "30",
        "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS": "45",
    })
    assert s.db_path == Path("/tmp/x/db.sqlite")
    assert s.artifact_dir == Path("/tmp/x/art")
    assert s.secret_dir == Path("/tmp/x/secrets")
    assert s.diag_api_url == "http://127.0.0.1:9100"
    assert s.limits == Limits(3, 7, 2, 1024, 30, 45)


def test_callback_hosts_and_public_url_default_to_empty():
    """phase 7 — 둘 다 비밀값이 아니다. 비어 있으면 callback 없음(접수 시 422)·chain_url null."""
    s = load_settings(FULL)
    assert s.callback_hosts == ()
    assert s.public_url == ""
    assert "WORKFLOW_CALLBACK_HOSTS" not in SECRET_KEYS and "WORKFLOW_PUBLIC_URL" not in SECRET_KEYS
    assert {"WORKFLOW_CALLBACK_HOSTS", "WORKFLOW_PUBLIC_URL"} <= set(ENV_KEYS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        ("localhost:5678", ("localhost:5678",)),
        (" localhost:5678, 127.0.0.1 ,", ("localhost:5678", "127.0.0.1")),
        ("N8N.Example:5678", ("n8n.example:5678",)),
    ],
)
def test_callback_hosts_parse_with_parse_hosts(raw, expected):
    s = load_settings({**FULL, "WORKFLOW_CALLBACK_HOSTS": raw})
    assert s.callback_hosts == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("https://runloom.duckdns.org/", "https://runloom.duckdns.org"),
        ("https://runloom.duckdns.org//", "https://runloom.duckdns.org"),
    ],
)
def test_public_url_drops_trailing_slash(raw, expected):
    s = load_settings({**FULL, "WORKFLOW_PUBLIC_URL": raw})
    assert s.public_url == expected


def test_github_token_is_an_optional_secret():
    """phase 8 step 6 — 없으면 GitHub 연결이 꺼질 뿐 서버는 뜬다. 개발 모드도 무작위 값을 만들지 않는다(가짜 토큰 금지)."""
    s = load_settings(FULL)
    assert s.github_token == ""
    assert s.github_repos == ()
    assert load_settings({"WORKFLOW_DEV": "1"}).github_token == ""
    assert OPTIONAL_SECRET_KEYS == ("WORKFLOW_GITHUB_TOKEN",)
    assert "WORKFLOW_GITHUB_TOKEN" not in SECRET_KEYS
    assert {"WORKFLOW_GITHUB_TOKEN", "WORKFLOW_GITHUB_REPOS"} <= set(ENV_KEYS)


def test_github_token_and_repos_are_read_and_token_stays_out_of_repr():
    token = "github_pat_" + "x" * 40
    s = load_settings({**FULL, "WORKFLOW_GITHUB_TOKEN": token, "WORKFLOW_GITHUB_REPOS": " acme/billing, ,Acme/Lib ,"})
    assert s.github_token == token
    assert s.github_repos == ("acme/billing", "Acme/Lib")
    assert token not in repr(s)


def test_non_integer_limit_raises():
    with pytest.raises(ValueError):
        load_settings({**FULL, "WORKFLOW_LIMIT_GLOBAL_DAILY": "many"})


def test_settings_is_frozen():
    s = load_settings(FULL)
    with pytest.raises(AttributeError):
        s.operator_token = "x"
    assert isinstance(s, Settings)


class _RecordingEnv(dict):
    """`load_settings` 가 실제로 조회한 키를 기록한다 — ENV_KEYS 가 코드와 어긋나면 잡는다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.asked: set[str] = set()

    def get(self, key, default=None):
        self.asked.add(key)
        return super().get(key, default)


def test_env_keys_lists_exactly_what_load_settings_reads():
    """deploy/env/central.env.example 이 이 목록과 비교된다 (Step 16). 개발 플래그 WORKFLOW_DEV 는 제외."""
    env = _RecordingEnv(FULL)
    load_settings(env)
    assert env.asked - {"WORKFLOW_DEV"} == set(ENV_KEYS)
    assert set(SECRET_KEYS) | set(OPTIONAL_SECRET_KEYS) <= set(ENV_KEYS)
    assert len(ENV_KEYS) == len(set(ENV_KEYS))


# --- 셀프호스트 모드 (phase 10, ADR-0016) ----------------------------------------------


def test_mode_defaults_to_demo():
    assert load_settings(FULL).mode == "demo"
    assert load_settings({**FULL, "WORKFLOW_MODE": ""}).mode == "demo"
    assert "WORKFLOW_MODE" in ENV_KEYS and "WORKFLOW_MODE" not in SECRET_KEYS


def test_mode_selfhost_is_read():
    assert load_settings({**FULL, "WORKFLOW_MODE": "selfhost"}).mode == "selfhost"


def test_unknown_mode_raises():
    with pytest.raises(ValueError) as exc:
        load_settings({**FULL, "WORKFLOW_MODE": "prod"})
    assert "WORKFLOW_MODE" in str(exc.value)


def test_selfhost_loads_without_diag_token_and_diagnosis_is_off():
    s = load_settings({"SESSION_SECRET": "s", "OPERATOR_TOKEN": "o", "WORKFLOW_MODE": "selfhost"})
    assert s.diag_api_token == ""
    assert s.diagnosis_enabled is False
    assert load_settings({**FULL, "WORKFLOW_MODE": "selfhost"}).diagnosis_enabled is True


def test_selfhost_still_requires_session_secret_and_operator_token():
    with pytest.raises(ValueError) as exc:
        load_settings({"WORKFLOW_MODE": "selfhost"})
    assert "SESSION_SECRET" in str(exc.value) and "OPERATOR_TOKEN" in str(exc.value)
    assert "DIAG_API_TOKEN" not in str(exc.value)


def test_selfhost_dev_mode_does_not_invent_a_diag_token(capsys):
    """개발 모드여도 가짜 진단 토큰을 만들면 없는 진단 API 를 부르게 된다 — 비워 두어 기능을 끈다."""
    s = load_settings({"WORKFLOW_DEV": "1", "WORKFLOW_MODE": "selfhost"})
    assert s.session_secret and s.operator_token
    assert s.diag_api_token == ""


def test_demo_without_diag_token_still_fails():
    with pytest.raises(ValueError) as exc:
        load_settings({"SESSION_SECRET": "s", "OPERATOR_TOKEN": "o"})
    assert "DIAG_API_TOKEN" in str(exc.value)
