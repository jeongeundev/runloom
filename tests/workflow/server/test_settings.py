"""settings.py — 비밀값은 환경변수에서만, 비어 있으면 실패. WORKFLOW_DEV=1 은 개발 전용 예외."""

from pathlib import Path

import pytest

from workflow.server.settings import Limits, Settings, load_settings

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
    assert s.diag_api_url == "http://127.0.0.1:9100"
    assert s.limits == Limits(3, 7, 2, 1024, 30, 45)


def test_non_integer_limit_raises():
    with pytest.raises(ValueError):
        load_settings({**FULL, "WORKFLOW_LIMIT_GLOBAL_DAILY": "many"})


def test_settings_is_frozen():
    s = load_settings(FULL)
    with pytest.raises(AttributeError):
        s.operator_token = "x"
    assert isinstance(s, Settings)
