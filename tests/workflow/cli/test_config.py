"""CLI 자격 파일·환경변수·서버 주소 규칙 — phase 26 step 3 (ARCHITECTURE "CLI — phase 26" config.py).

`env` dict 와 `tmp_path` 만 쓴다 — 실제 `~/.config/runloom` 은 건드리지 않는다.
"""

import json
import os
import stat

import pytest

from workflow.cli import config
from workflow.cli.config import ConfigError, Credentials

TOKEN = "wfm_secretTOKENvalue0123456789"
SERVER = "https://runloom.example.com"
LOGIN_HINT = "runloom login --server URL 로 먼저 로그인하세요."


@pytest.fixture
def env(tmp_path) -> dict:
    return {"HOME": str(tmp_path / "home")}


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_path_is_under_home_config_and_ignores_xdg(tmp_path, env):
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    assert config.credentials_path(env) == tmp_path / "home" / ".config" / "runloom" / "credentials.json"


def test_runloom_home_overrides_path(tmp_path, env):
    env["RUNLOOM_HOME"] = str(tmp_path / "rl")
    assert config.credentials_path(env) == tmp_path / "rl" / "credentials.json"
    env["RUNLOOM_HOME"] = ""
    assert config.credentials_path(env) == tmp_path / "home" / ".config" / "runloom" / "credentials.json"


def test_save_writes_0600_file_in_0700_dir_and_replaces(env):
    path = config.save(env, Credentials(SERVER, TOKEN))
    assert path == config.credentials_path(env)
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700
    assert json.loads(path.read_text(encoding="utf-8")) == {"server": SERVER, "token": TOKEN}

    config.save(env, Credentials("http://localhost:8000", "wfm_other0123456789"))
    assert json.loads(path.read_text(encoding="utf-8")) == {"server": "http://localhost:8000",
                                                             "token": "wfm_other0123456789"}
    assert _mode(path) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["credentials.json"]  # 임시 파일이 남지 않는다
    assert config.load(env) == Credentials("http://localhost:8000", "wfm_other0123456789")


def test_save_tightens_existing_wide_dir(env):
    directory = config.credentials_path(env).parent
    directory.mkdir(parents=True)
    os.chmod(directory, 0o755)
    config.save(env, Credentials(SERVER, TOKEN))
    assert _mode(directory) == 0o700


def test_delete_removes_file_only(env):
    assert config.delete(env) is False
    path = config.save(env, Credentials(SERVER, TOKEN))
    assert config.delete(env) is True
    assert not path.exists() and path.parent.is_dir()
    assert config.delete(env) is False


def test_load_from_file(env):
    config.save(env, Credentials(SERVER, TOKEN))
    assert config.load(env) == Credentials(SERVER, TOKEN)


def test_missing_file_is_login_hint(env):
    with pytest.raises(ConfigError) as caught:
        config.load(env)
    assert str(caught.value) == LOGIN_HINT


@pytest.mark.parametrize("only", ["RUNLOOM_TOKEN", "RUNLOOM_SERVER"])
def test_one_env_without_file_is_login_hint(env, only):
    env[only] = TOKEN if only == "RUNLOOM_TOKEN" else SERVER
    with pytest.raises(ConfigError) as caught:
        config.load(env)
    assert str(caught.value) == LOGIN_HINT


def test_env_token_wins_over_file(env):
    config.save(env, Credentials(SERVER, TOKEN))
    env["RUNLOOM_TOKEN"] = "wfm_fromENV0123456789"
    assert config.load(env) == Credentials(SERVER, "wfm_fromENV0123456789")


def test_env_server_wins_over_file_and_is_normalized(env):
    config.save(env, Credentials(SERVER, TOKEN))
    env["RUNLOOM_SERVER"] = "http://127.0.0.1:8000/"
    assert config.load(env) == Credentials("http://127.0.0.1:8000", TOKEN)


def test_both_env_skip_file_even_if_wide_or_broken(env):
    path = config.credentials_path(env)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    os.chmod(path, 0o644)
    env.update(RUNLOOM_TOKEN=TOKEN, RUNLOOM_SERVER=SERVER + "/")
    assert config.load(env) == Credentials(SERVER, TOKEN)


def test_empty_env_values_fall_back_to_file(env):
    config.save(env, Credentials(SERVER, TOKEN))
    env.update(RUNLOOM_TOKEN="", RUNLOOM_SERVER="")
    assert config.load(env) == Credentials(SERVER, TOKEN)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_wide_file_mode_is_rejected_with_chmod_hint(env, mode):
    path = config.save(env, Credentials(SERVER, TOKEN))
    os.chmod(path, mode)
    with pytest.raises(ConfigError) as caught:
        config.load(env)
    assert str(caught.value) == f"자격 파일 권한이 너무 넓습니다: {path} — chmod 600 {path}"
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("content", ["not json", "[]", '{"server": "https://x"}', '{"server": 1, "token": "t"}'])
def test_broken_file_is_relogin_hint(env, content):
    path = config.credentials_path(env)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError) as caught:
        config.load(env)
    assert str(caught.value) == f"자격 파일을 읽을 수 없습니다: {path} — runloom login --server URL 로 다시 로그인하세요."


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:8000/", "http://localhost:8000"),
    ("http://localhost", "http://localhost"),
    ("http://127.0.0.1", "http://127.0.0.1"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("http://LOCALHOST:8000", "http://LOCALHOST:8000"),
    ("https://runloom.example.com", "https://runloom.example.com"),
    ("https://runloom.example.com/", "https://runloom.example.com"),
])
def test_normalize_server_accepts(url, expected):
    assert config.normalize_server(url) == expected


@pytest.mark.parametrize("url", ["http://example.com", "http://192.168.0.5:8000", "ftp://x", "https://",
                                 "runloom.example.com", "http://localhost.example.com"])
def test_normalize_server_rejects_scheme_or_host(url):
    with pytest.raises(ConfigError) as caught:
        config.normalize_server(url)
    assert str(caught.value) == f"서버 주소는 https:// 여야 합니다(http:// 는 localhost·127.0.0.1·::1 만): {url}"


@pytest.mark.parametrize("url", ["https://x/path", "https://x?a=1", "https://x/#f", "http://localhost:8000/api/v1"])
def test_normalize_server_rejects_path_or_query(url):
    with pytest.raises(ConfigError) as caught:
        config.normalize_server(url)
    assert str(caught.value) == f"서버 주소에 경로·쿼리를 붙이지 마세요: {url}"


def test_env_server_follows_server_rules(env):
    env.update(RUNLOOM_TOKEN=TOKEN, RUNLOOM_SERVER="http://example.com")
    with pytest.raises(ConfigError, match="https://"):
        config.load(env)


def test_file_server_follows_server_rules(env):
    path = config.credentials_path(env)
    config.save(env, Credentials(SERVER, TOKEN))
    path.write_text(json.dumps({"server": "http://example.com", "token": TOKEN}), encoding="utf-8")
    with pytest.raises(ConfigError, match="https://"):
        config.load(env)


def test_credentials_repr_hides_token():
    creds = Credentials(SERVER, TOKEN)
    assert TOKEN not in repr(creds) and TOKEN not in str(creds)
    assert SERVER in repr(creds)
