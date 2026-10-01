"""Jira 소스 계약 — ARCHITECTURE "Jira 소스 — phase 18" 계약·클라이언트 절, ADR-0024 결정 1·17.

사이트 주소 허용/거부(SSRF 방지), 모델의 거부 규칙, `snapshot_digest` 를 본다.
"""

import pytest
from pydantic import ValidationError

from workflow.adapters import db
from workflow.contracts import jira
from workflow.contracts.jira import (
    JiraChoices,
    JiraConnection,
    JiraIssueRef,
    JiraIssueSnapshot,
    JiraProjectConfig,
    JiraProjectRef,
    JiraTransition,
    normalize_site_url,
    snapshot_digest,
)


def _snapshot(**overrides) -> dict:
    data = {
        "issue_id": "10012",
        "key": "SHOP-12",
        "project_id": "10000",
        "summary": "쿠폰 적용 오류",
        "description_text": "## 재현 절차\n\n1. 쿠폰 입력",
        "status_name": "대기",
        "status_category": "new",
        "issue_type": "버그",
        "priority": "High",
        "labels": ["checkout"],
        "created": "2026-09-29T10:00:00.000+09:00",
        "updated": "2026-09-29T11:00:00.000+09:00",
    }
    return {**data, **overrides}


def _project(**overrides) -> dict:
    data = {
        "source_id": "jps-0000000a",
        "session_id": "s-1",
        "project_id": "10000",
        "project_key": "SHOP",
        "project_name": "쇼핑몰",
        "github_source_id": "ghs-00000001",
        "issue_types": ["버그", "작업"],
        "start_mode": "from_now",
        "start_at": "2026-09-29T00:00:00Z",
        "status_on_start": "진행 중",
        "status_on_review": "리뷰중",
        "status_on_done": "종료",
        "followup_issue_type": None,
        "enabled": True,
    }
    return {**data, **overrides}


def _connection(**overrides) -> dict:
    data = {
        "session_id": "s-1",
        "site_url": "https://shop.atlassian.net",
        "cloud_id": "1324a887-45db-1bf4-1e99-ef0ff456d421",
        "api_base": "gateway",
        "email": "dev@example.com",
        "account_id": "5b10a2844c20165700ede21g",
        "display_name": "개발자",
        "connected_at": "2026-09-29T00:00:00Z",
        "disconnected_at": None,
        "auth_failed_at": None,
    }
    return {**data, **overrides}


# --- 사이트 주소 ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://shop.atlassian.net", "https://shop.atlassian.net"),
        ("https://shop.atlassian.net/", "https://shop.atlassian.net"),
        ("  https://Shop.Atlassian.NET/ ", "https://shop.atlassian.net"),
        ("https://my-team-1.atlassian.net", "https://my-team-1.atlassian.net"),
        ("https://a.atlassian.net", "https://a.atlassian.net"),
    ],
)
def test_site_url_accepts_atlassian_cloud(raw, expected):
    assert normalize_site_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://shop.atlassian.net",  # http
        "https://shop.atlassian.net:443",  # 포트
        "https://shop.atlassian.net/jira",  # 경로
        "https://shop.atlassian.net//",  # 끝 / 는 하나만 지운다
        "https://shop.atlassian.net?x=1",  # 쿼리
        "https://shop.atlassian.net#a",
        "https://user@shop.atlassian.net",  # 사용자 정보
        "https://evil.com",
        "https://shop.atlassian.net.evil.com",
        "https://evil.com/.atlassian.net",
        "https://a.b.atlassian.net",  # 하위 이름 둘
        "https://-shop.atlassian.net",
        "https://shop-.atlassian.net",
        "https://shop_x.atlassian.net",
        "https://.atlassian.net",
        "https://atlassian.net",
        "https://api.atlassian.com",
        "shop.atlassian.net",
        "https://sh op.atlassian.net",
        "https://shop.atlassian.net\n/x",
        "",
    ],
)
def test_site_url_rejects_other_hosts_paths_ports(raw):
    with pytest.raises(ValueError):
        normalize_site_url(raw)


def test_site_url_rejects_too_long_label():
    with pytest.raises(ValueError):
        normalize_site_url("https://" + "a" * 64 + ".atlassian.net")
    assert normalize_site_url("https://" + "a" * 63 + ".atlassian.net")


def test_connection_normalizes_site_and_checks_cloud_id():
    connection = JiraConnection.model_validate(_connection(site_url="https://SHOP.atlassian.net/"))
    assert connection.site_url == "https://shop.atlassian.net"
    for bad in ("https://evil.com", "http://shop.atlassian.net"):
        with pytest.raises(ValidationError):
            JiraConnection.model_validate(_connection(site_url=bad))
    with pytest.raises(ValidationError):
        JiraConnection.model_validate(_connection(cloud_id="not-a-uuid"))
    with pytest.raises(ValidationError):
        JiraConnection.model_validate(_connection(api_base="other"))
    with pytest.raises(ValidationError):
        JiraConnection.model_validate(_connection(email="no-at-sign"))
    with pytest.raises(ValidationError):
        JiraConnection.model_validate(_connection(token="x"))  # 토큰 칸 없음


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("dev@example.com", True),
        ("a@b", True),
        ("no-at", False),
        ("a b@example.com", False),
        ("a@@b", False),
        ("a" * 250 + "@b.cd", False),  # 254자 초과
    ],
)
def test_email_pattern(value, ok):
    assert jira.valid_email(value) is ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("ATATT3xFfGF0abc=", True),
        ("x", True),
        ("", False),
        ("has space", False),
        ("tab\tin", False),
        ("한글", False),
        ("x" * 2001, False),
    ],
)
def test_token_pattern(value, ok):
    assert jira.valid_token(value) is ok


def test_cloud_id_is_lowercased_uuid():
    assert jira.normalize_cloud_id("1324A887-45DB-1BF4-1E99-EF0FF456D421") == "1324a887-45db-1bf4-1e99-ef0ff456d421"
    for bad in ("", "1324a887", "1324a887-45db-1bf4-1e99-ef0ff456d421/../x", "g324a887-45db-1bf4-1e99-ef0ff456d421"):
        with pytest.raises(ValueError):
            jira.normalize_cloud_id(bad)


# --- 프로젝트 설정 ---

def test_project_config_round_trip():
    config = JiraProjectConfig.model_validate(_project())
    assert config.model_dump(mode="json") == _project()


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_id": "ghs-00000001"},
        {"source_id": "jps-0000000"},
        {"project_id": "0"},
        {"project_id": "1 OR 1=1"},
        {"github_source_id": "jps-0000000a"},
        {"issue_types": ["버그", "버그"]},
        {"issue_types": ["버그", "BUG", "bug"]},
        {"issue_types": [""]},
        {"start_mode": "filtered"},
        {"start_at": "2026-09-29"},
        {"status_on_start": ""},
        {"followup_issue_type": ""},
        {"enabled": 1},
        {"extra": 1},
    ],
)
def test_project_config_rejects(overrides):
    with pytest.raises(ValidationError):
        JiraProjectConfig.model_validate(_project(**overrides))


# --- 스냅숏 ---

def test_snapshot_round_trip_and_rejects():
    snapshot = JiraIssueSnapshot.model_validate(_snapshot())
    assert snapshot.model_dump(mode="json") == _snapshot()
    for overrides in (
        {"key": "shop-12"},
        {"key": "SHOP-0"},
        {"key": "SHOP 12"},
        {"issue_id": "abc"},
        {"summary": ""},
        {"status_category": "undefined"},
        {"updated": "2026-09-29T11:00:00.000+0900"},  # Jira 원래 형식은 클라이언트가 바꾼다
        {"labels": "checkout"},
        {"description": {"type": "doc"}},
    ):
        with pytest.raises(ValidationError):
            JiraIssueSnapshot.model_validate(_snapshot(**overrides))
    assert JiraIssueSnapshot.model_validate(_snapshot(priority=None)).priority is None


def test_snapshot_digest_is_stable_and_content_sensitive():
    a = JiraIssueSnapshot.model_validate(_snapshot())
    b = JiraIssueSnapshot.model_validate(dict(reversed(list(_snapshot().items()))))
    assert snapshot_digest(a) == snapshot_digest(b)
    assert len(snapshot_digest(a)) == 64
    changed = JiraIssueSnapshot.model_validate(_snapshot(status_name="진행 중"))
    assert snapshot_digest(changed) != snapshot_digest(a)


# --- 작은 모델·상수 ---

def test_small_models():
    assert JiraProjectRef(project_id="10000", key="SHOP", name="쇼핑몰").key == "SHOP"
    with pytest.raises(ValidationError):
        JiraProjectRef(project_id="10000", key="shop", name="쇼핑몰")
    assert JiraIssueRef(issue_id="10013", key="SHOP-13").issue_id == "10013"
    choices = JiraChoices.model_validate({"issue_types": [{"id": "10001", "name": "버그"}], "statuses": ["대기", "진행 중"]})
    assert choices.issue_types[0].name == "버그"
    transition = JiraTransition(transition_id="21", name="시작", to_name="진행 중", to_category="indeterminate")
    assert transition.to_category == "indeterminate"
    with pytest.raises(ValidationError):
        JiraTransition(transition_id="21", name="시작", to_name="진행 중", to_category="other")


def test_constants():
    assert jira.JIRA_GATEWAY == "https://api.atlassian.com/ex/jira/"
    assert jira.JIRA_MOMENTS == ("start", "review", "done")
    assert jira.JIRA_DELIVERY_STATES == ("pending", "sending", "delivered", "unknown", "failed", "skipped")
    assert jira.JIRA_FIELDS == ("summary", "description", "status", "issuetype", "priority", "labels", "created",
                                "updated", "project")
    # 스키마 CHECK 는 계약 상수를 쓴다
    assert db.JIRA_DELIVERY_STATES is jira.JIRA_DELIVERY_STATES
