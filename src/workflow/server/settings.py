"""중앙 서버 설정. 비밀값은 환경변수에서만 읽는다 (AGENTS.md CRITICAL).

상한 값은 ARCHITECTURE "배포와 실행 예산" 의 초기값이며 코드에 박지 않고 여기서 읽는다.
"""

import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SECRET_KEYS = ("SESSION_SECRET", "OPERATOR_TOKEN", "DIAG_API_TOKEN")

# `load_settings` 가 읽는 환경변수 전부 (개발 플래그 `WORKFLOW_DEV` 제외).
# deploy/env/central.env.example 의 키 목록이 이것과 일치해야 한다 (tests/test_deploy_files.py).
ENV_KEYS = (
    "WORKFLOW_DB_PATH",
    "WORKFLOW_ARTIFACT_DIR",
    *SECRET_KEYS,
    "DIAG_API_URL",
    "WORKFLOW_LIMIT_PER_SESSION_DAILY",
    "WORKFLOW_LIMIT_GLOBAL_DAILY",
    "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION",
    "WORKFLOW_LIMIT_ATTACHMENTS_MAX_BYTES",
    "WORKFLOW_LIMIT_UNKNOWN_AFTER_SECONDS",
    "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS",
)


@dataclass(frozen=True)
class Limits:
    per_session_daily: int = 10
    global_daily: int = 60
    active_tasks_per_session: int = 5
    attachments_max_bytes: int = 1_048_576
    unknown_after_seconds: int = 120
    heartbeat_offline_seconds: int = 90


@dataclass(frozen=True)
class Settings:
    db_path: Path
    artifact_dir: Path
    session_secret: str
    operator_token: str
    diag_api_url: str
    diag_api_token: str
    session_cookie_days: int = 14
    limits: Limits = field(default_factory=Limits)


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} 는 정수여야 합니다: {raw!r}") from None


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """비밀값이 비어 있으면 ValueError. `WORKFLOW_DEV=1` 이면 무작위 값을 만들고 stderr 에 경고한다."""
    dev = env.get("WORKFLOW_DEV") == "1"
    secrets_found: dict[str, str] = {}
    missing: list[str] = []
    generated: list[str] = []
    for key in SECRET_KEYS:
        value = env.get(key, "")
        if value:
            secrets_found[key] = value
        elif dev:
            secrets_found[key] = secrets.token_urlsafe(32)
            generated.append(key)
        else:
            missing.append(key)
    if missing:
        raise ValueError(f"환경변수가 비어 있습니다: {', '.join(missing)}")
    if generated:
        print(
            f"경고: WORKFLOW_DEV=1 — {', '.join(generated)} 를 무작위 값으로 생성했습니다 (개발 전용)",
            file=sys.stderr,
        )
    return Settings(
        db_path=Path(env.get("WORKFLOW_DB_PATH") or "data/central.sqlite"),
        artifact_dir=Path(env.get("WORKFLOW_ARTIFACT_DIR") or "data/artifacts"),
        session_secret=secrets_found["SESSION_SECRET"],
        operator_token=secrets_found["OPERATOR_TOKEN"],
        diag_api_url=env.get("DIAG_API_URL") or "http://127.0.0.1:8100",
        diag_api_token=secrets_found["DIAG_API_TOKEN"],
        limits=Limits(
            per_session_daily=_int(env, "WORKFLOW_LIMIT_PER_SESSION_DAILY", 10),
            global_daily=_int(env, "WORKFLOW_LIMIT_GLOBAL_DAILY", 60),
            active_tasks_per_session=_int(env, "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION", 5),
            attachments_max_bytes=_int(env, "WORKFLOW_LIMIT_ATTACHMENTS_MAX_BYTES", 1_048_576),
            unknown_after_seconds=_int(env, "WORKFLOW_LIMIT_UNKNOWN_AFTER_SECONDS", 120),
            heartbeat_offline_seconds=_int(env, "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS", 90),
        ),
    )
