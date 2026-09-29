"""중앙 서버 설정. 비밀값은 환경변수에서만 읽는다 (AGENTS.md CRITICAL).

상한 값은 ARCHITECTURE "배포와 실행 예산" 의 초기값이며 코드에 박지 않고 여기서 읽는다.
"""

import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from workflow.domain.callback_policy import parse_hosts

SECRET_KEYS = ("SESSION_SECRET", "OPERATOR_TOKEN")
# 비밀값이지만 선택 — 비면 그 기능만 꺼진다. WORKFLOW_DEV 도 무작위 값을 만들지 않는다.
# WORKFLOW_GITHUB_TOKEN: GitHub 업무 순환(ADR-0014)의 저장소 한정 토큰. 운영자 API 는 "있음/없음"만 응답한다.
OPTIONAL_SECRET_KEYS = ("WORKFLOW_GITHUB_TOKEN",)

# `load_settings` 가 읽는 환경변수 전부 (개발 플래그 `WORKFLOW_DEV` 제외).
# deploy/env/central.env.example 의 키 목록이 이것과 일치해야 한다 (tests/test_deploy_files.py).
ENV_KEYS = (
    # 셀프호스트 전용(ADR-0019) — 빈 값·`selfhost` 는 읽고 버리고, 그 밖의 값(`demo` 등)은 SettingsError
    "WORKFLOW_MODE",
    "WORKFLOW_DB_PATH",
    "WORKFLOW_ARTIFACT_DIR",
    # 비밀 파일 디렉터리 (ADR-0017) — 경로라 비밀값이 아니다
    "WORKFLOW_SECRET_DIR",
    *SECRET_KEYS,
    "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION",
    "WORKFLOW_LIMIT_UNKNOWN_AFTER_SECONDS",
    "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS",
    # n8n 입구·출구 (ADR-0010) — 비밀값이 아니다
    "WORKFLOW_CALLBACK_HOSTS",
    "WORKFLOW_PUBLIC_URL",
    # GitHub 업무 순환 (ADR-0014) — 토큰은 비밀값, 허용 저장소 목록(owner/name 콤마 구분)은 아니다
    *OPTIONAL_SECRET_KEYS,
    "WORKFLOW_GITHUB_REPOS",
)


@dataclass(frozen=True)
class Limits:
    active_tasks_per_session: int = 5
    unknown_after_seconds: int = 120
    heartbeat_offline_seconds: int = 90


@dataclass(frozen=True)
class Settings:
    db_path: Path
    artifact_dir: Path
    session_secret: str
    operator_token: str
    session_cookie_days: int = 14
    limits: Limits = field(default_factory=Limits)
    # callback 허용 목록(`parse_hosts` 결과, 비면 callback 없음)과 chain_url·task_url 앞의 공개 주소(끝 `/` 없음, 비면 null)
    callback_hosts: tuple[str, ...] = ()
    public_url: str = ""
    # GitHub 업무 순환: 토큰(비면 연결 불가, repr 에 넣지 않음)과 연결을 허용한 저장소 `owner/name` 목록
    github_token: str = field(default="", repr=False)
    github_repos: tuple[str, ...] = ()
    # 비밀 파일 저장소 루트(ADR-0017, `adapters/secret_store.py`). 백업에 들어가지 않는다
    secret_dir: Path = Path("data/secrets")


class SettingsError(ValueError):
    """시작을 막는 설정 오류 — 지금은 `WORKFLOW_MODE` 가 셀프호스트가 아닐 때 (ADR-0019)."""


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} 는 정수여야 합니다: {raw!r}") from None


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """비밀값이 비어 있으면 ValueError. `WORKFLOW_DEV=1` 이면 무작위 값을 만들고 stderr 에 경고한다.
    `WORKFLOW_MODE` 는 동작을 정하지 않는다 — 빈 값·`selfhost` 가 아니면 SettingsError (ADR-0019)."""
    dev = env.get("WORKFLOW_DEV") == "1"
    mode = env.get("WORKFLOW_MODE") or ""
    if mode not in ("", "selfhost"):
        raise SettingsError(
            f"WORKFLOW_MODE={mode} 는 지원하지 않습니다 — service 브랜치는 셀프호스트 전용이며 공개 데모는 main 브랜치입니다"
        )
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
        limits=Limits(
            active_tasks_per_session=_int(env, "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION", 5),
            unknown_after_seconds=_int(env, "WORKFLOW_LIMIT_UNKNOWN_AFTER_SECONDS", 120),
            heartbeat_offline_seconds=_int(env, "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS", 90),
        ),
        callback_hosts=parse_hosts(env.get("WORKFLOW_CALLBACK_HOSTS") or ""),
        public_url=(env.get("WORKFLOW_PUBLIC_URL") or "").rstrip("/"),
        github_token=env.get("WORKFLOW_GITHUB_TOKEN") or "",
        github_repos=tuple(r.strip() for r in (env.get("WORKFLOW_GITHUB_REPOS") or "").split(",") if r.strip()),
        secret_dir=Path(env.get("WORKFLOW_SECRET_DIR") or "data/secrets"),
    )
