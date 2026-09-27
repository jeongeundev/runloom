"""masking — 산출물·로그의 `wfc_`·`sk-` 마스킹과 Codex 환경변수 허용 목록 (ARCHITECTURE 인증 절)."""

from workflow.connector.masking import ENV_ALLOWLIST, codex_env, mask_secrets, registered_env

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


def test_codex_env_passes_the_script_pace_knob_but_no_other_workflow_vars():
    """`WORKFLOW_SCRIPT_PACE_SECONDS` 는 대본 에이전트 속도(비밀 아님)라 예외로 통과한다. 다른 `WORKFLOW_*` 는 여전히 빠진다."""
    env = codex_env({
        "PATH": "/usr/bin", "WORKFLOW_SCRIPT_PACE_SECONDS": "25",
        "WORKFLOW_DB_PATH": "/y", "WORKFLOW_CONNECTOR_HOME": "/x", "WORKFLOW_SKIP_APP": "1",
    })
    assert env == {"PATH": "/usr/bin", "WORKFLOW_SCRIPT_PACE_SECONDS": "25"}


# --- 러너 로컬 등록의 `--env` (ADR-0018 결정 3, phase 12 step 4) ---------------------------------------------------


DB_URL = "postgresql://agent:pw-local-5434@localhost:5434/openarchive"


def test_mask_secrets_hides_registered_env_values_by_name():
    text, count = mask_secrets(f"connect {DB_URL} failed\nretry {DB_URL}", {"DATABASE_URL": DB_URL})

    assert DB_URL not in text
    assert text == "connect <env:DATABASE_URL> failed\nretry <env:DATABASE_URL>"
    assert count == 2


def test_mask_secrets_ignores_short_env_values():
    """8자 미만 값(`1`·`true`·`dev`)은 흔한 낱말이라 가리지 않는다 — 로그 전체가 가려진다."""
    text, count = mask_secrets("DEBUG=1 mode=dev", {"DEBUG": "1", "MODE": "dev", "EMPTY": ""})

    assert (text, count) == ("DEBUG=1 mode=dev", 0)


def test_mask_secrets_prefers_the_longer_value_when_values_overlap():
    text, _ = mask_secrets("a=secret-value-long b=secret-value", {"A": "secret-value-long", "B": "secret-value"})

    assert text == "a=<env:A> b=<env:B>"


def test_mask_secrets_still_masks_fixed_patterns_with_env():
    text, count = mask_secrets(f"{WFC} {DB_URL}", {"DATABASE_URL": DB_URL})

    assert text == "wfc_*** <env:DATABASE_URL>" and count == 2


def test_registered_env_cannot_override_allowlist_or_reserved_names():
    """등록 때(cli) 거부한 규칙을 실행 때도 다시 적용한다 — 로컬 상태 DB 를 직접 고쳐도 허용 목록 이름을 덮지 못한다."""
    env = registered_env({
        "DATABASE_URL": DB_URL, "PATH": "/evil", "HOME": "/evil", "LANG": "C", "XDG_CONFIG_HOME": "/evil",
        "CODEX_HOME": "/evil", "WORKFLOW_SCRIPT_PACE_SECONDS": "0", "WORKFLOW_DB_PATH": "/x",
        "OPENAI_API_KEY": SK, "OPERATOR_TOKEN": "o", "1BAD": "x", "BAD-NAME": "x",
    })

    assert env == {"DATABASE_URL": DB_URL}
