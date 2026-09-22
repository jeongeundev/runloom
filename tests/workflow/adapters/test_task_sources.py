"""업무 출처 fixture — GitHub·Jira 이슈를 흉내 낸 패키지 안 JSON. 실제 API 를 부르지 않는다."""

import json
import re

import pytest

from workflow.adapters import task_sources
from workflow.adapters.task_sources import SOURCE_LABELS, SOURCES, load_issues
from workflow.contracts.v1 import BUILTIN_KINDS, Capability
from workflow.domain.task_sources import Issue, map_issue
from workflow.server.web import EXAMPLES

DIAGNOSE = Capability(code="operations.diagnose", scope={"workflow_id": "daily-report"})
MODIFY = Capability(code="code.modify", scope={"repository_id": "demo-report-repo"})

KEY_PATTERN = {"github": re.compile(r"^#\d+$"), "jira": re.compile(r"^OPS-\d+$")}


def _by_number(source: str) -> dict[int, Issue]:
    # "#41" / "OPS-41" 을 번호 41 로 맞춰 두 출처를 같은 키로 비교한다.
    return {int(re.search(r"\d+$", issue.key).group()): issue for issue in load_issues(source)}


# --- 출처 목록 -------------------------------------------------------------------------


def test_sources_and_labels():
    assert SOURCES == ("github", "jira")
    # n8n 은 라벨만 있고 SOURCES 에 없다 — fixture 파일이 없어 가져오기 화면에 나오지 않는다 (ADR-0010)
    assert SOURCE_LABELS == {"github": "GitHub Issues", "jira": "Jira", "n8n": "n8n"}


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="출처"):
        load_issues("linear")


# --- 각 출처 4개 -----------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_each_source_has_four_issues_in_file_order(source):
    issues = load_issues(source)

    assert len(issues) == 4
    assert [int(re.search(r"\d+$", i.key).group()) for i in issues] == [41, 42, 43, 44]
    assert all(isinstance(i, Issue) for i in issues)
    assert all(i.source == source for i in issues)


@pytest.mark.parametrize("source", SOURCES)
def test_key_format_per_source(source):
    for issue in load_issues(source):
        assert KEY_PATTERN[source].match(issue.key), issue.key


@pytest.mark.parametrize("source", SOURCES)
def test_keys_are_unique(source):
    keys = [i.key for i in load_issues(source)]
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize("source", SOURCES)
def test_urls_are_none(source):
    # fixture 는 가짜 외부 링크를 만들지 않는다.
    assert all(issue.url is None for issue in load_issues(source))


@pytest.mark.parametrize("source", SOURCES)
def test_blocked_by_refers_to_keys_in_same_file(source):
    issues = load_issues(source)
    keys = {i.key for i in issues}
    for issue in issues:
        for dep in issue.blocked_by:
            assert dep in keys, f"{issue.key} blocked_by {dep} 는 같은 파일에 없다"
            assert dep != issue.key


def test_load_returns_fresh_list_each_call():
    first = load_issues("github")
    first.append("junk")  # type: ignore[arg-type]
    assert len(load_issues("github")) == 4


# --- 내용: 같은 사건을 두 도구 형식으로 ---------------------------------------------------


def test_github_and_jira_describe_the_same_issues():
    github = _by_number("github")
    jira = _by_number("jira")

    assert github.keys() == jira.keys()
    for number in github:
        g, j = github[number], jira[number]
        assert g.title == j.title
        assert g.body == j.body
        assert g.labels == j.labels
        assert [int(re.search(r"\d+$", d).group()) for d in g.blocked_by] == [
            int(re.search(r"\d+$", d).group()) for d in j.blocked_by
        ]


@pytest.mark.parametrize("source", SOURCES)
def test_issue_contents(source):
    issues = _by_number(source)
    key = {n: issues[n].key for n in issues}

    assert issues[41].title == "일일 보고서 생성 실패 (09-20 09:00)"
    assert issues[41].labels == ("incident", "workflow:daily-report", "run:daily-0920-0900")
    assert issues[41].blocked_by == ()
    assert issues[41].body == EXAMPLES["diagnose"]["request"]

    assert issues[42].title == "집계 API 응답 형식 변경 대응"
    assert issues[42].labels == ("bug", "repo:demo-report-repo")
    assert issues[42].blocked_by == (key[41],)
    assert issues[42].body == EXAMPLES["fix"]["request"]

    assert issues[43].title == "변경 응답 형식 모니터링 알림 추가"
    assert issues[43].labels == ("enhancement", "repo:demo-report-repo")
    assert issues[43].blocked_by == (key[42],)

    assert issues[44].title == "README 오타 수정"
    assert issues[44].labels == ("docs",)
    assert issues[44].blocked_by == ()


@pytest.mark.parametrize("source", SOURCES)
def test_issue_mapping_per_source(source):
    issues = _by_number(source)

    m41 = map_issue(issues[41], BUILTIN_KINDS)
    assert m41.capability == DIAGNOSE
    assert m41.run_id == "daily-0920-0900"

    m42 = map_issue(issues[42], BUILTIN_KINDS)
    assert m42.capability == MODIFY
    assert m42.run_id is None

    assert map_issue(issues[43], BUILTIN_KINDS).capability is None
    assert map_issue(issues[44], BUILTIN_KINDS).capability is None


# --- fixture 파일 검증 -----------------------------------------------------------------


def _write(tmp_path, source: str, payload) -> None:
    (tmp_path / f"{source}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _valid_issue(**overrides):
    base = {
        "key": "#1",
        "title": "t",
        "body": "b",
        "labels": ["docs"],
        "blocked_by": [],
        "url": None,
    }
    base.update(overrides)
    return base


def test_fixture_missing_required_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(task_sources, "FIXTURES_DIR", tmp_path)
    bad = _valid_issue()
    del bad["labels"]
    _write(tmp_path, "github", [bad])

    with pytest.raises(ValueError, match="labels"):
        load_issues("github")


def test_fixture_blocked_by_unknown_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(task_sources, "FIXTURES_DIR", tmp_path)
    _write(tmp_path, "github", [_valid_issue(key="#1", blocked_by=["#9"])])

    with pytest.raises(ValueError, match="#9"):
        load_issues("github")


def test_fixture_duplicate_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(task_sources, "FIXTURES_DIR", tmp_path)
    _write(tmp_path, "github", [_valid_issue(key="#1"), _valid_issue(key="#1")])

    with pytest.raises(ValueError, match="#1"):
        load_issues("github")


def test_fixture_self_dependency_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(task_sources, "FIXTURES_DIR", tmp_path)
    _write(tmp_path, "github", [_valid_issue(key="#1", blocked_by=["#1"])])

    with pytest.raises(ValueError, match="#1"):
        load_issues("github")


def test_fixture_url_defaults_to_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(task_sources, "FIXTURES_DIR", tmp_path)
    issue = _valid_issue()
    del issue["url"]
    _write(tmp_path, "github", [issue])

    assert load_issues("github")[0].url is None
