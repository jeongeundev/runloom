"""지표 API·기준선 가져오기 — ADR-0015, ARCHITECTURE "측정 — phase 9" API 표.

- 운영자 세션(`require_operator`)만 쓰고 그 세션의 데이터만 계산한다. 다른 세션의 소스는 404.
- 계산은 `domain.metrics` 가 하고 여기서는 DB 사실을 넘겨 JSON·CSV 로 옮길 뿐이다. 모르는 값은 JSON null·CSV 빈 칸.
- 기준선 가져오기는 GitHub 호출(트랜잭션 밖) 뒤 `replace_baseline` 한 트랜잭션. 토큰은 `Settings.github_token` 에만 있고
  응답·오류 문구에 GitHub 예외 메시지를 넣지 않는다.
"""

import csv
import dataclasses
import io
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from workflow.adapters import repo
from workflow.adapters.github_client import (
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRepositoryNotAllowed,
    GitHubUnavailable,
    HttpGitHubClient,
)
from workflow.contracts.v1 import Rfc3339
from workflow.domain.metrics import (
    BASELINE_NOTE,
    BaselineItemFact,
    MetricsGroup,
    MetricsReport,
    Ratio,
    Stat,
    compute_metrics,
    summarize_baseline,
)
from workflow.server.auth import get_conn, require_operator, utc_now
from workflow.server.errors import ApiError
from workflow.server.github_sync import DEFAULT_RETRY_AFTER_SECONDS

router = APIRouter()

CSV_COLUMNS = ("group", "area", "metric", "unit", "median", "n", "incomplete", "unknown", "total",
               "numerator", "denominator", "note")

# (MetricsGroup 칸, 영역, 단위). 매핑 칸은 `칸 이름 단수:키` 행 여럿이 된다(handoff_blocked:operator, failed_code:timeout).
_CSV_METRICS = (
    ("bundles", "bundle", "count"),
    ("handoff_wait", "bottleneck", "seconds"),
    ("handoff_blocked", "bottleneck", "seconds"),
    ("intake_to_human", "speed", "seconds"),
    ("intake_to_done", "speed", "seconds"),
    ("intake_to_merge", "speed", "seconds"),
    ("intake_to_approval", "speed", "seconds"),
    ("done_by_finished_at", "speed", "count"),
    ("closed_failed", "speed", "count"),
    ("closed_unmerged", "speed", "count"),
    ("interventions", "human", "count"),
    ("response_time", "human", "seconds"),
    ("first_pass", "quality", "ratio"),
    ("rework", "quality", "count"),
    ("human_rejection", "quality", "ratio"),
    ("execution_time", "cost", "seconds"),
    ("cost_usd", "cost", "usd"),
    ("input_tokens", "cost", "tokens"),
    ("output_tokens", "cost", "tokens"),
    ("failure", "reliability", "ratio"),
    ("failed_codes", "reliability", "count"),
    ("reruns", "reliability", "ratio"),
)
_ROW_PREFIX = {"handoff_blocked": "handoff_blocked", "failed_codes": "failed_code"}


# --- 계산 --------------------------------------------------------------------------------------


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _report(
    conn: Connection, request: Request, session_id: str, since: str | None, until: str | None, group_by: str | None
) -> MetricsReport:
    if since is not None and until is not None and _parse(since) >= _parse(until):
        raise ApiError(422, "invalid_field", "from 은 to 보다 앞이어야 합니다.", field="to")
    facts = repo.list_metric_facts(conn, session_id, store=request.app.state.store)
    return compute_metrics(facts, since=since, until=until, group_by=group_by)


def _baselines(conn: Connection, session_id: str) -> list[dict[str, Any]]:
    """세션의 GitHub 소스마다 기준선 요약. 가져온 적이 없으면 `imported: false` 와 null(모름)."""
    baselines = []
    for source in repo.list_github_sources(conn, session_id):
        record, items = repo.list_baseline(conn, session_id, source.source_id)
        view: dict[str, Any] = {
            "source_id": source.source_id, "repository_full_name": source.repository_full_name,
            "imported": record is not None, "opened_before": None, "fetched_at": None, "intake_to_merge": None,
            "note": BASELINE_NOTE,
        }
        if record is not None:
            summary = summarize_baseline(
                [BaselineItemFact(issue_number=i["issue_number"], issue_opened_at=i["issue_opened_at"],
                                  pr_number=i["pr_number"], pr_merged_at=i["pr_merged_at"]) for i in items],
                opened_before=record["opened_before"], fetched_at=record["fetched_at"],
            )
            view |= {"opened_before": summary.opened_before, "fetched_at": summary.fetched_at,
                     "intake_to_merge": dataclasses.asdict(summary.intake_to_merge), "note": summary.note}
        baselines.append(view)
    return baselines


# --- 직렬화 ------------------------------------------------------------------------------------


def _ratio(r: Ratio) -> dict[str, Any]:
    return {"numerator": r.numerator, "denominator": r.denominator, "rate": r.rate,
            "incomplete": r.incomplete, "unknown": r.unknown}


def _value(value: Any) -> Any:
    if isinstance(value, Stat):
        return dataclasses.asdict(value)
    if isinstance(value, Ratio):
        return _ratio(value)
    if isinstance(value, Mapping):
        return {k: _value(v) for k, v in value.items()}
    return value


def _group(group: MetricsGroup) -> dict[str, Any]:
    return {f.name: _value(getattr(group, f.name)) for f in dataclasses.fields(group)}


def _cells(value: Any) -> dict[str, Any]:
    if isinstance(value, Stat):
        return {"median": value.median, "n": value.n, "incomplete": value.incomplete, "unknown": value.unknown,
                "total": value.total}
    if isinstance(value, Ratio):
        return {"incomplete": value.incomplete, "unknown": value.unknown, "numerator": value.numerator, "denominator": value.denominator}
    return {"total": value}  # 건수 하나


def _csv_rows(report: MetricsReport, baselines: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for group in report.groups:
        for name, area, unit in _CSV_METRICS:
            value = getattr(group, name)
            entries = (
                [(f"{_ROW_PREFIX[name]}:{key}", v) for key, v in value.items()] if isinstance(value, Mapping)
                else [(name, value)]
            )
            for metric, v in entries:
                yield {"group": group.key, "area": area, "metric": metric, "unit": unit, **_cells(v)}
    for b in baselines:
        stat = b["intake_to_merge"] or {}
        yield {"group": f"baseline:{b['source_id']}", "area": "speed", "metric": "intake_to_merge",
               "unit": "seconds", **stat, "note": b["note"]}


def _csv(rows: Iterator[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: "" if row.get(c) is None else row[c] for c in CSV_COLUMNS})  # 모름은 빈 칸
    return out.getvalue()


# --- 지표 --------------------------------------------------------------------------------------

Since = Annotated[Rfc3339 | None, Query(alias="from")]
Until = Annotated[Rfc3339 | None, Query(alias="to")]
GroupBy = Annotated[Literal["config_revision", "folder_commit"] | None, Query()]


@router.get("/metrics.json")
def metrics_json(
    request: Request, since: Since = None, until: Until = None, group_by: GroupBy = None,
    session_id: str = Depends(require_operator), conn: Connection = Depends(get_conn),
) -> JSONResponse:
    report = _report(conn, request, session_id, since, until, group_by)
    return JSONResponse({
        "from": report.since, "to": report.until, "group_by": report.group_by,
        "groups": [_group(g) for g in report.groups],
        "baselines": _baselines(conn, session_id),
    })


@router.get("/metrics.csv")
def metrics_csv(
    request: Request, since: Since = None, until: Until = None, group_by: GroupBy = None,
    session_id: str = Depends(require_operator), conn: Connection = Depends(get_conn),
) -> Response:
    report = _report(conn, request, session_id, since, until, group_by)
    return Response(_csv(_csv_rows(report, _baselines(conn, session_id))), media_type="text/csv; charset=utf-8")


# --- 기준선 가져오기 ---------------------------------------------------------------------------


def _github_error(exc: GitHubError) -> ApiError:
    """GitHub 예외 → ErrorBody. 예외 메시지는 쓰지 않는다(고정 문구)."""
    if isinstance(exc, GitHubRateLimited):
        wait = exc.retry_after_seconds
        if wait is None and exc.reset_epoch is not None:
            wait = max(0, exc.reset_epoch - int(datetime.now(UTC).timestamp()))
        return ApiError(429, "github_rate_limited", "GitHub 호출 한도에 걸렸습니다. 잠시 뒤 다시 가져오세요.",
                        details={"retry_after_seconds": DEFAULT_RETRY_AFTER_SECONDS if wait is None else wait})
    if isinstance(exc, GitHubRepositoryNotAllowed):
        return ApiError(409, "repository_not_allowed", "저장소가 WORKFLOW_GITHUB_REPOS 에 없습니다.",
                        field="repository_full_name")
    if isinstance(exc, GitHubForbidden):
        return ApiError(502, "github_forbidden", "GitHub 가 요청을 거부했습니다 — 토큰 권한(Issues·Pull requests 읽기)을 확인하세요.")
    if isinstance(exc, GitHubNotFound):
        return ApiError(502, "github_not_found", "GitHub 에서 저장소를 찾지 못했습니다.")
    if isinstance(exc, GitHubUnavailable):
        return ApiError(502, "github_unavailable", "GitHub 에 연결하지 못했습니다. 잠시 뒤 다시 가져오세요.")
    return ApiError(502, "github_error", "GitHub 응답을 처리하지 못했습니다.")


@router.post("/operator/github/sources/{source_id}/baseline")
def import_baseline(
    request: Request, source_id: str,
    session_id: str = Depends(require_operator), conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """소스 연결 시각(`github_sources.created_at`) 이전에 열린 이슈 → 병합 PR 을 가져와 전체 교체한다. 멱등."""
    source = repo.get_github_source(conn, session_id, source_id)
    if source is None:
        raise ApiError(404, "not_found", f"source {source_id}을 찾을 수 없습니다.", field="source_id")
    settings = request.app.state.settings
    if not settings.github_token:
        raise ApiError(409, "github_token_missing", "WORKFLOW_GITHUB_TOKEN 이 설정되지 않았습니다.")
    opened_before = repo.github_source_created_at(conn, session_id, source_id)
    client = request.app.state.github_client or HttpGitHubClient(settings.github_token, settings.github_repos)
    try:
        links = client.list_issue_pr_links(source.repository_full_name, opened_before=opened_before)
    except GitHubError as exc:
        raise _github_error(exc) from None
    count = repo.replace_baseline(conn, session_id, source_id, links, opened_before=opened_before, now=utc_now())
    return JSONResponse({"source_id": source_id, "opened_before": opened_before, "item_count": count})
