# ruff: noqa: F811 — test_web_github_connect·test_task_cycle 픽스처를 가져와 인자로 쓴다
"""저장소 카드 [러너 붙이기] (phase 12 step 9, ADR-0018 결정 6, ARCHITECTURE "경로").

러너가 없는 카드에는 버튼, POST 하면 연결 코드가 들어간 명령 한 줄을 같은 카드에 그대로 렌더한다(리다이렉트 없음).
러너가 매칭된 카드에는 버튼이 없고 고급 설정 안에 [러너 다시 붙이기] 만 있다.
"""

import dataclasses
import html as html_lib
import re

import httpx
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.app import create_app
from workflow.server.auth import SESSION_COOKIE, sign_session

from .test_task_cycle import SESSION as CYCLE_SESSION
from .test_task_cycle import (  # noqa: F401 — 픽스처
    SOURCE,
    auto_source,
    config,
    cycle,
)
from .test_web_github_connect import (  # noqa: F401 — 픽스처
    BASE,
    app,
    card_of,
    connect_app,
    folded,
    github,
    names,
    op,
    op_session,
    pem,
    secrets,
    settings,
    visible,
)

COMMAND = re.compile(
    r"deploy/selfhost/install-runner\.sh --server (\S+) --code ([A-Za-z0-9_-]+) --repo &lt;이 저장소를 클론한 폴더&gt;"
)


def runner_action(source_id: str) -> str:
    return f'action="/operator/github/sources/{source_id}/runner"'


def billing(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    return names(conn, op_session(op))["acme/billing"]


def test_card_without_runner_shows_the_attach_button(op, conn, secrets, pem):
    source = billing(op, conn, secrets, pem)

    card = card_of(visible(op.get("/operator/github").text), source.source_id)

    assert "data-runner-missing" in card and "이 저장소를 등록한 러너 없음" in card
    assert runner_action(source.source_id) in card and "러너 붙이기" in card
    assert "install-runner.sh" not in card  # 누르기 전에는 명령·코드 없음
    assert repo.list_connect_codes(conn) == []


def test_post_issues_a_code_and_renders_one_command_in_that_card(op, conn, secrets, pem):
    source = billing(op, conn, secrets, pem)
    other = names(conn, op_session(op))["acme/shop"]

    response = op.post(f"/operator/github/sources/{source.source_id}/runner", follow_redirects=False)

    assert response.status_code == 200, response.text
    (row,) = repo.list_connect_codes(conn)
    card = card_of(response.text, source.source_id)
    match = COMMAND.search(card)
    assert match, card
    assert match.group(1) == BASE and match.group(2) == row["code"]
    assert "data-runner-command" in card and "10분" in card
    assert html_lib.escape(row["expires_at"]) in card or "만료" in card
    assert runner_action(source.source_id) in card and "다시 발급" in card
    assert row["code"] not in card_of(response.text, other.source_id)
    assert row["code"] not in str(response.url)


def test_command_uses_workflow_public_url_when_set(settings, github, conn, secrets, pem):
    app = create_app(dataclasses.replace(settings, public_url="https://runloom.example.com"))
    app.state.github_transport = httpx.MockTransport(github)
    client = TestClient(app, base_url=BASE)
    client.post("/operator/login", data={"token": "test-operator-token"})
    source = billing(client, conn, secrets, pem)

    text = client.post(f"/operator/github/sources/{source.source_id}/runner").text

    assert COMMAND.search(card_of(text, source.source_id)).group(1) == "https://runloom.example.com"


def test_matched_card_has_no_attach_button_only_a_folded_reattach(client, auto_source, conn):
    repo.mark_operator(conn, CYCLE_SESSION)
    client.cookies.set(SESSION_COOKIE, sign_session(CYCLE_SESSION, "test-session-secret"))

    card = card_of(client.get("/operator/github").text, SOURCE)

    assert runner_action(SOURCE) not in visible(f"<main>{card}")
    assert "data-runner-missing" not in card
    assert runner_action(SOURCE) in folded(card, "고급 설정") and "러너 다시 붙이기" in card


def test_other_workspace_source_is_404(op, client, auto_source, conn):
    before = len(repo.list_connect_codes(conn))  # cycle 픽스처가 연결에 쓴 코드

    response = op.post(f"/operator/github/sources/{SOURCE}/runner")

    assert response.status_code == 404
    assert len(repo.list_connect_codes(conn)) == before


def test_non_operator_is_refused(app, conn, secrets, pem, op):
    source = billing(op, conn, secrets, pem)
    anonymous = TestClient(app, base_url=BASE)

    response = anonymous.post(f"/operator/github/sources/{source.source_id}/runner", follow_redirects=False)

    assert response.status_code == 403
    assert repo.list_connect_codes(conn) == []


def test_selfhost_without_login_redirects_to_login(settings, github, conn):
    app = create_app(dataclasses.replace(settings, mode="selfhost"))
    client = TestClient(app, base_url=BASE)

    response = client.post(f"/operator/github/sources/{SOURCE}/runner", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert repo.list_connect_codes(conn) == []
