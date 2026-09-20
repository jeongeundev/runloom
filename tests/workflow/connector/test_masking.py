"""masking — 산출물·로그의 `wfc_`·`sk-` 마스킹과 Codex 환경변수 허용 목록 (ARCHITECTURE 인증 절)."""

from workflow.connector.masking import ENV_ALLOWLIST, codex_env, mask_secrets

WFC = "wfc_" + "a" * 43
SK = "sk-proj-" + "b" * 40


def test_masks_connect_token_and_counts():
    text, count = mask_secrets(f"Authorization: Bearer {WFC}\nnext line {WFC}")

    assert WFC not in text
    assert text == "Authorization: Bearer wfc_***\nnext line wfc_***"
    assert count == 2


def test_masks_openai_key():
    text, count = mask_secrets(f'{{"key": "{SK}"}}')

    assert SK not in text
    assert text == '{"key": "sk-***"}'
    assert count == 1


def test_short_prefixes_are_not_secrets():
    text, count = mask_secrets("wfc_ab sk-12 task-sk-x")

    assert (text, count) == ("wfc_ab sk-12 task-sk-x", 0)


def test_clean_text_is_unchanged():
    assert mask_secrets("정상 로그 줄") == ("정상 로그 줄", 0)


def test_codex_env_keeps_only_allowlist():
    base = {
        "HOME": "/Users/op", "PATH": "/usr/bin", "LANG": "ko_KR.UTF-8", "TERM": "xterm",
        "CODEX_HOME": "/Users/op/.codex", "XDG_CONFIG_HOME": "/Users/op/.config",
        "OPENAI_API_KEY": SK, "WORKFLOW_CONNECTOR_HOME": "/x", "WORKFLOW_DB_PATH": "/y",
        "DIAG_API_TOKEN": "d", "SESSION_SECRET": "s", "OPERATOR_TOKEN": "o", "AWS_SECRET": "z",
    }

    env = codex_env(base)

    assert env == {
        "HOME": "/Users/op", "PATH": "/usr/bin", "LANG": "ko_KR.UTF-8", "TERM": "xterm",
        "CODEX_HOME": "/Users/op/.codex", "XDG_CONFIG_HOME": "/Users/op/.config",
    }
    assert "OPENAI_API_KEY" not in env
    assert not any(k.startswith(("WORKFLOW_", "DIAG_")) for k in env)


def test_codex_env_allowlist_has_no_secret_names():
    assert not ENV_ALLOWLIST & {"OPENAI_API_KEY", "DIAG_API_TOKEN", "SESSION_SECRET", "OPERATOR_TOKEN"}
