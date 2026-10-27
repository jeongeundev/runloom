"""cli 테스트 공용 fixture — 서버 fixture 를 `tests.workflow.server.conftest` 에서 다시 내보낸다(ARCHITECTURE "CLI — phase 26" 경계).

CLI 는 API 클라이언트라 테스트는 실제 앱(`TestClient`)을 그대로 부른다. 멤버·`wfm_` 토큰은 웹과 같은 경로
(초대 가입 → 쿠키로 `/api/v1/me/tokens` 발급)로 만든다.
"""

import pytest
from fastapi.testclient import TestClient

from tests.workflow.server.conftest import (  # noqa: F401 — pytest fixture 재내보내기
    _cheap_scrypt,
    app,
    client,
    conn,
    log_in,
    log_in_member,
    settings,
)

MEMBER_EMAIL = "dev@example.com"
MEMBER_NAME = "김개발"


def issue_token(logged_in: TestClient, scope: str = "full", name: str = "노트북 CLI") -> str:
    """로그인 쿠키를 가진 클라이언트로 멤버 토큰을 발급해 원문(`wfm_…`)을 돌려준다."""
    response = logged_in.post("/api/v1/me/tokens", json={"name": name, "scope": scope})
    assert response.status_code == 201, response.text
    return response.json()["token"]


@pytest.fixture
def member(app) -> TestClient:  # noqa: F811 — 위에서 다시 내보낸 fixture 를 받는다
    """멤버 `김개발` 로 로그인한 클라이언트(관리자는 먼저 만든다)."""
    return log_in_member(TestClient(app), email=MEMBER_EMAIL, display_name=MEMBER_NAME)


@pytest.fixture
def member_token(member) -> str:
    return issue_token(member)
