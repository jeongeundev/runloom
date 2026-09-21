"""업무 출처 fixture — GitHub·Jira 이슈를 흉내 낸 패키지 안 JSON.

실제 GitHub·Jira API 를 부르지 않는다 (2026-09-21 사용자 확정: 대본 에이전트가 낼 수 있는 결과는
정해져 있으므로 가져올 이슈도 그 시나리오의 것이어야 한다). 화면에는 "시연 데이터" 로 표시한다.
파일 내용은 읽을 때 한 번 검증한다 — 필수 키, key 유일, `blocked_by` 가 같은 파일의 다른 key 를 가리킴.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from workflow.domain.task_sources import Issue

SOURCES: tuple[str, ...] = ("github", "jira")
SOURCE_LABELS: dict[str, str] = {"github": "GitHub Issues", "jira": "Jira"}

FIXTURES_DIR = Path(__file__).parent / "task_source_fixtures"

_REQUIRED_KEYS = ("key", "title", "body", "labels", "blocked_by")


def load_issues(source: str) -> list[Issue]:
    """출처의 이슈를 파일 순서대로 돌려준다. 모르는 출처는 ValueError."""
    if source not in SOURCES:
        raise ValueError(f"알 수 없는 출처: {source}")
    return list(_load(FIXTURES_DIR / f"{source}.json", source))


@cache
def _load(path: Path, source: str) -> tuple[Issue, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: 이슈 목록(배열)이어야 합니다")
    issues = tuple(_parse(path.name, source, item) for item in raw)
    _check_references(path.name, issues)
    return issues


def _parse(file_name: str, source: str, item: Any) -> Issue:
    if not isinstance(item, dict):
        raise ValueError(f"{file_name}: 이슈는 객체여야 합니다")
    missing = [key for key in _REQUIRED_KEYS if key not in item]
    if missing:
        raise ValueError(f"{file_name}: 이슈 {item.get('key', '?')} 에 필수 키 없음: {', '.join(missing)}")
    key, title, body = item["key"], item["title"], item["body"]
    labels, blocked_by, url = item["labels"], item["blocked_by"], item.get("url")
    if not (isinstance(key, str) and key):
        raise ValueError(f"{file_name}: key 는 비어 있지 않은 문자열이어야 합니다")
    if not (isinstance(title, str) and isinstance(body, str)):
        raise ValueError(f"{file_name}: 이슈 {key} 의 title·body 는 문자열이어야 합니다")
    if not _is_str_list(labels) or not _is_str_list(blocked_by):
        raise ValueError(f"{file_name}: 이슈 {key} 의 labels·blocked_by 는 문자열 배열이어야 합니다")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"{file_name}: 이슈 {key} 의 url 은 null 또는 문자열이어야 합니다")
    return Issue(
        source=source,  # type: ignore[arg-type]  # SOURCES 로 검사한 값
        key=key,
        title=title,
        body=body,
        labels=tuple(labels),
        blocked_by=tuple(blocked_by),
        url=url,
    )


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_references(file_name: str, issues: tuple[Issue, ...]) -> None:
    keys: set[str] = set()
    for issue in issues:
        if issue.key in keys:
            raise ValueError(f"{file_name}: key {issue.key} 가 중복입니다")
        keys.add(issue.key)
    for issue in issues:
        for dep in issue.blocked_by:
            if dep == issue.key:
                raise ValueError(f"{file_name}: 이슈 {issue.key} 가 자기 자신을 blocked_by 로 가리킵니다")
            if dep not in keys:
                raise ValueError(f"{file_name}: 이슈 {issue.key} 의 blocked_by {dep} 가 같은 파일에 없습니다")
