"""App 설치·PAT 연결이 만드는 GitHub 소스 — ADR-0017, ARCHITECTURE "GitHub App 연결 — phase 11" 경로 절.

- 새 소스는 `intake: all_open`·`trigger_label: runloom`·`run_mode: auto` 이고 자동 결정 칸(로컬 저장소·검증 프로필·
  검토·기본 수정 Agent)은 비운다 — 준비 판정의 자동 매칭 몫이다. `start_at` 은 연결 시각 기록일 뿐이다.
- 이미 같은 저장소(대소문자 무시) 소스가 있으면 `installation_id` 만 바꾼다. 설치에서 빠진 저장소의 소스는 수집만 멈춘다
  (`enabled=false`, 삭제 없음). 바뀐 소스만 `config_revision` + 1 — 다시 불러도 같은 결과다.
- GitHub 호출·비밀값은 다루지 않는다(호출자가 설치 저장소 목록·확인 결과를 넘긴다).
"""

import secrets
from collections.abc import Sequence
from sqlite3 import Connection

from workflow.adapters import repo
from workflow.adapters.github_app import InstalledRepository
from workflow.contracts.github import GitHubSourceConfig
from workflow.server.github_api import DEFAULT_TRIGGER_LABEL


def _new_source(full_name: str, now: str, installation_id: int | None) -> GitHubSourceConfig:
    return GitHubSourceConfig(
        source_id=f"ghs-{secrets.token_hex(4)}", repository_full_name=full_name, intake="all_open",
        trigger_label=DEFAULT_TRIGGER_LABEL, run_mode="auto", label_filter=[], selected_issue_numbers=[],
        start_at=now, installation_id=installation_id, enabled=True, config_revision=1,
    )


def _save_change(conn: Connection, session_id: str, current: GitHubSourceConfig, now: str, *, member_id: str | None,
                 **changes) -> None:
    changed = current.model_copy(update={**changes, "config_revision": current.config_revision + 1})
    repo.save_github_source(conn, session_id, changed, now, expected_revision=current.config_revision,
                            member_id=member_id)


def sync_installation_sources(
    conn: Connection, session_id: str, installation_id: int, repositories: Sequence[InstalledRepository], now: str,
    *, member_id: str | None = None,
) -> list[str]:
    """설치 저장소에 소스를 맞춘다. 반환은 새로 만들었거나 바꾼(설치 id·수집 중지) source_id."""
    existing = {s.repository_full_name.lower(): s for s in repo.list_github_sources(conn, session_id)}
    installed = {r.full_name.lower() for r in repositories}
    changed: list[str] = []
    for repository in repositories:
        current = existing.get(repository.full_name.lower())
        if current is None:
            config = _new_source(repository.full_name, now, installation_id)
            repo.save_github_source(conn, session_id, config, now, member_id=member_id)
            existing[repository.full_name.lower()] = config
            changed.append(config.source_id)
        elif current.installation_id != installation_id:
            _save_change(conn, session_id, current, now, member_id=member_id, installation_id=installation_id)
            changed.append(current.source_id)
    for name, current in existing.items():
        if current.installation_id == installation_id and name not in installed and current.enabled:
            _save_change(conn, session_id, current, now, member_id=member_id, enabled=False)
            changed.append(current.source_id)
    return changed


def ensure_token_source(conn: Connection, session_id: str, repository_full_name: str, now: str, *,
                        member_id: str | None = None) -> str | None:
    """PAT 로 확인한 저장소의 소스. 이미 있으면 그대로(None), 없으면 만들어 source_id."""
    if any(s.repository_full_name.lower() == repository_full_name.lower()
           for s in repo.list_github_sources(conn, session_id)):
        return None
    config = _new_source(repository_full_name, now, None)
    repo.save_github_source(conn, session_id, config, now, member_id=member_id)
    return config.source_id
