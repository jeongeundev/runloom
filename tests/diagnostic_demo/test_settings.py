"""진단 서비스 설정 — 비밀값은 환경변수에서만, 상한·단가는 설정값 (ARCHITECTURE 모델 호출 예산)."""

from pathlib import Path

import pytest

from diagnostic_demo.settings import DEFAULT_FIXTURES_DIR, FAKE_MODEL_ID, Settings, load_settings

BASE = {"DIAG_API_TOKEN": "tok"}


def test_defaults_follow_architecture_budget_table():
    s = load_settings(BASE)
    assert s.api_token == "tok"
    assert s.openai_api_key is None
    assert s.db_path == Path("data/diag.sqlite")
    assert s.fixtures_dir == DEFAULT_FIXTURES_DIR and (DEFAULT_FIXTURES_DIR / "index.json").is_file()
    assert s.model == "openai" and s.model_id == "gpt-4.1-mini-2025-04-14"
    assert s.allowed_workflow_ids == frozenset({"daily-report"})
    assert (s.budget_usd, s.budget_stop_ratio) == (30.0, 0.9)
    assert (s.price_input_per_m, s.price_output_per_m) == (0.0, 0.0)
    assert (s.max_calls, s.max_input_tokens, s.max_output_tokens) == (15, 80_000, 8_000)
    assert s.timeout_seconds == 300.0
    assert s.global_daily == 60


def test_missing_token_raises_unless_dev(capsys):
    with pytest.raises(ValueError, match="DIAG_API_TOKEN"):
        load_settings({})
    s = load_settings({"DIAG_DEV": "1"})
    assert len(s.api_token) >= 32
    assert "DIAG_DEV=1" in capsys.readouterr().err


def test_env_overrides_are_parsed():
    s = load_settings({
        **BASE,
        "OPENAI_API_KEY": "sk-test",
        "DIAG_DB_PATH": "/tmp/x.sqlite",
        "DIAG_ARTIFACT_DIR": "/tmp/arts",
        "DIAG_FIXTURES_DIR": "/tmp/fx",
        "DIAG_MODEL": "fake",
        "DIAG_MODEL_ID": "gpt-test",
        "DIAG_ALLOWED_WORKFLOWS": "daily-report, weekly-report",
        "DIAG_BUDGET_USD": "10",
        "DIAG_BUDGET_STOP_RATIO": "0.5",
        "DIAG_PRICE_INPUT_PER_M": "0.4",
        "DIAG_PRICE_OUTPUT_PER_M": "1.6",
        "DIAG_MAX_CALLS": "3",
        "DIAG_MAX_INPUT_TOKENS": "100",
        "DIAG_MAX_OUTPUT_TOKENS": "50",
        "DIAG_TIMEOUT_SECONDS": "7.5",
        "DIAG_GLOBAL_DAILY": "2",
    })
    assert s.openai_api_key == "sk-test"
    assert (s.db_path, s.artifact_dir, s.fixtures_dir) == (
        Path("/tmp/x.sqlite"), Path("/tmp/arts"), Path("/tmp/fx"),
    )
    assert (s.model, s.model_id) == ("fake", "gpt-test")
    assert s.allowed_workflow_ids == frozenset({"daily-report", "weekly-report"})
    assert (s.budget_usd, s.budget_stop_ratio) == (10.0, 0.5)
    assert (s.price_input_per_m, s.price_output_per_m) == (0.4, 1.6)
    assert (s.max_calls, s.max_input_tokens, s.max_output_tokens) == (3, 100, 50)
    assert s.timeout_seconds == 7.5 and s.global_daily == 2


def test_fake_model_gets_an_honest_model_id_unless_overridden():
    assert load_settings({**BASE, "DIAG_MODEL": "fake"}).model_id == FAKE_MODEL_ID == "fake-fixture-script"
    assert load_settings({**BASE, "DIAG_MODEL": "fake", "DIAG_MODEL_ID": "x"}).model_id == "x"


def test_invalid_model_or_number_is_rejected():
    with pytest.raises(ValueError, match="DIAG_MODEL"):
        load_settings({**BASE, "DIAG_MODEL": "anthropic"})
    with pytest.raises(ValueError, match="DIAG_MAX_CALLS"):
        load_settings({**BASE, "DIAG_MAX_CALLS": "many"})


def test_settings_is_frozen_and_never_holds_key_in_repr():
    s = Settings(db_path=Path("a"), artifact_dir=Path("b"), api_token="secret-token",
                 openai_api_key="sk-secret")
    with pytest.raises(AttributeError):
        s.api_token = "x"  # type: ignore[misc]
    assert "secret-token" not in repr(s) and "sk-secret" not in repr(s)
