"""callback 대상 허용 목록 — `WORKFLOW_CALLBACK_HOSTS` 판정 (ADR-0010).

명시 비교만 한다. DNS 조회·IP 대역 검사는 하지 않는다 (ADR-0004 의 태도).
"""

import pytest

from workflow.domain.callback_policy import host_allowed, parse_hosts

# --- parse_hosts --------------------------------------------------------------


def test_parse_hosts_empty_string_is_empty_tuple():
    assert parse_hosts("") == ()


def test_parse_hosts_strips_and_drops_blank_items():
    assert parse_hosts(" localhost:5678, 127.0.0.1 ") == ("localhost:5678", "127.0.0.1")
    assert parse_hosts("localhost:5678,,") == ("localhost:5678",)
    assert parse_hosts(" , ") == ()


def test_parse_hosts_lowercases():
    assert parse_hosts("LOCALHOST:5678,Example.COM") == ("localhost:5678", "example.com")


# --- host_allowed -------------------------------------------------------------


def test_host_and_port_match():
    assert host_allowed("http://localhost:5678/webhook-waiting/12", ("localhost:5678",)) is True


def test_port_mismatch_is_rejected():
    assert host_allowed("http://localhost:5679/webhook-waiting/12", ("localhost:5678",)) is False


def test_host_only_entry_allows_any_port():
    allowed = ("localhost",)
    assert host_allowed("http://localhost:5678/x", allowed) is True
    assert host_allowed("http://localhost:8000/x", allowed) is True
    assert host_allowed("http://localhost/x", allowed) is True


def test_scheme_default_port_is_filled_in_for_comparison():
    assert host_allowed("https://example.com/x", ("example.com:443",)) is True
    assert host_allowed("http://example.com/x", ("example.com:80",)) is True
    assert host_allowed("https://example.com/x", ("example.com:80",)) is False


def test_empty_allowed_rejects_everything():
    assert host_allowed("http://localhost:5678/x", ()) is False
    assert host_allowed("http://localhost:5678/x", parse_hosts("")) is False


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost:5678/",
        "http://user@localhost:5678/",
        "http://user:pw@localhost:5678/",
        "localhost:5678/x",
        "http:///x",
        "",
        "http://[::1/x",
    ],
)
def test_bad_scheme_userinfo_empty_host_or_parse_failure_is_rejected(url):
    assert host_allowed(url, ("localhost:5678", "localhost")) is False


def test_host_comparison_is_case_insensitive():
    assert host_allowed("http://LOCALHOST:5678/x", ("localhost:5678",)) is True
    assert host_allowed("http://localhost:5678/x", parse_hosts("LOCALHOST:5678")) is True


def test_other_host_in_list_is_not_enough():
    assert host_allowed("http://127.0.0.1:8100/runs", ("localhost:5678",)) is False


def test_matches_any_entry_in_list():
    allowed = parse_hosts("localhost:5678,127.0.0.1")
    assert host_allowed("http://127.0.0.1:8100/runs", allowed) is True
    assert host_allowed("http://localhost:5678/x", allowed) is True
    assert host_allowed("http://10.0.0.1:5678/x", allowed) is False
