"""멤버 토큰 API(`/api/v1/me/tokens`) — phase 24 step 3 (ADR-0032, ARCHITECTURE "토큰 API·화면", CONTRACT 18.4·18.5).

원문(`wfm_…`)은 발급 응답에만 한 번 나가고 서버에는 sha256 만 남는다. 발급은 로그인 쿠키로만(D21).
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from .conftest import bearer, log_in, log_in_member

PATH = "/api/v1/me/tokens"


@pytest.fixture
def admin(app) -> TestClient:
    return log_in(TestClient(app))


@pytest.fixture
def member(app, admin) -> TestClient:
    return log_in_member(TestClient(app), email="dev@example.com", display_name="김개발")


def issue(client: TestClient, name: str = "노트북 CLI", scope: str = "full") -> dict:
    response = client.post(PATH, json={"name": name, "scope": scope})
    assert response.status_code == 201, response.text
    return response.json()


def error(response, status: int, code: str, field: str | None = None) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code, body
    assert body["field"] == field, body
    return body


def test_issue_returns_raw_token_once_and_stores_only_hash(member, conn):
    body = issue(member, name="  노트북 CLI ", scope="read")
    assert set(body) == {"token_id", "name", "scope", "token", "created_at"}
    assert body["name"] == "노트북 CLI" and body["scope"] == "read"
    assert body["token"].startswith("wfm_") and body["token_id"].startswith("mtk-")
    row = conn.execute("SELECT * FROM member_tokens WHERE token_id = ?", (body["token_id"],)).fetchone()
    assert row["token_sha256"] == hashlib.sha256(body["token"].encode()).hexdigest()
    assert body["token"] not in repr(tuple(row))

    listed = member.get(PATH)
    assert listed.status_code == 200
    assert listed.json() == {"tokens": [{"token_id": body["token_id"], "name": "노트북 CLI", "scope": "read",
                                         "created_at": body["created_at"], "last_used_at": None}]}
    assert body["token"] not in listed.text and row["token_sha256"] not in listed.text


def test_list_with_bearer_shows_only_my_active_tokens(app, admin, member):
    mine = issue(member)
    issue(admin, name="관리자 것")
    gone = issue(member, name="버린 것")
    assert member.delete(f"{PATH}/{gone['token_id']}").status_code == 204
    response = TestClient(app).get(PATH, headers=bearer(mine["token"]))
    assert response.status_code == 200
    tokens = response.json()["tokens"]
    assert [t["token_id"] for t in tokens] == [mine["token_id"]]
    assert tokens[0]["last_used_at"] is not None  # 이 요청이 마지막 사용을 남긴다


def test_issue_with_bearer_is_403_cookie_required(app, member, conn):
    full = issue(member)
    response = TestClient(app).post(PATH, json={"name": "새 것", "scope": "full"}, headers=bearer(full["token"]))
    error(response, 403, "cookie_required")
    # 쿠키가 있어도 Bearer 헤더가 있으면 거부
    error(member.post(PATH, json={"name": "새 것", "scope": "full"}, headers=bearer(full["token"])),
          403, "cookie_required")
    assert conn.execute("SELECT count(*) FROM member_tokens").fetchone()[0] == 1


def test_issue_validation(member):
    body = error(member.post(PATH, json={"name": "  ", "scope": "read"}), 422, "invalid_field", "name")
    assert body["message"] == "토큰 이름은 1~40자로 입력하세요."
    error(member.post(PATH, json={"name": "가" * 41, "scope": "read"}), 422, "invalid_field", "name")
    body = error(member.post(PATH, json={"name": "x", "scope": "admin"}), 422, "invalid_field", "scope")
    assert body["message"] == "권한은 read(읽기 전용) 또는 full(내 권한 전부)입니다."
    error(member.post(PATH, json={"name": "x", "scope": "read", "extra": 1}), 422, "unknown_field", "extra")
    assert member.post(PATH, json={"name": "x"}).status_code == 422


def test_unauthenticated(app):
    client = TestClient(app)
    error(client.get(PATH), 401, "unauthenticated")
    error(client.post(PATH, json={"name": "x", "scope": "read"}), 401, "unauthenticated")
    error(client.delete(f"{PATH}/mtk-000000000000"), 401, "unauthenticated")
    error(client.get(PATH, headers=bearer("wfm_nope")), 401, "unauthenticated")


def test_revoke_then_token_is_401(app, member, conn):
    body = issue(member)
    client = TestClient(app)
    assert client.get(PATH, headers=bearer(body["token"])).status_code == 200
    response = client.delete(f"{PATH}/{body['token_id']}", headers=bearer(body["token"]))
    assert response.status_code == 204 and response.content == b""
    error(client.get(PATH, headers=bearer(body["token"])), 401, "unauthenticated")
    assert conn.execute("SELECT revoked_at FROM member_tokens WHERE token_id = ?",
                        (body["token_id"],)).fetchone()[0] is not None


def test_revoke_others_missing_or_revoked_token_is_404(admin, member, conn):
    others = issue(admin, name="관리자 것")
    body = error(member.delete(f"{PATH}/{others['token_id']}"), 404, "not_found", "token_id")
    assert body["message"] == f"토큰 {others['token_id']}을 찾을 수 없습니다."
    error(member.delete(f"{PATH}/mtk-000000000000"), 404, "not_found", "token_id")
    mine = issue(member)
    assert member.delete(f"{PATH}/{mine['token_id']}").status_code == 204
    error(member.delete(f"{PATH}/{mine['token_id']}"), 404, "not_found", "token_id")
    assert conn.execute("SELECT revoked_at FROM member_tokens WHERE token_id = ?",
                        (others["token_id"],)).fetchone()[0] is None


def test_read_token_cannot_delete(app, member, conn):
    read = issue(member, scope="read")
    error(TestClient(app).delete(f"{PATH}/{read['token_id']}", headers=bearer(read["token"])),
          403, "token_read_only")
    assert conn.execute("SELECT revoked_at FROM member_tokens WHERE token_id = ?",
                        (read["token_id"],)).fetchone()[0] is None

