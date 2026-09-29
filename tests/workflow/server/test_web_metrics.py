# ruff: noqa: F811 — test_metrics_api 픽스처(operator·op·fake·settings)를 가져와 인자로 쓴다
"""지표 화면 `GET /metrics` (phase 9 step 9, ADR-0015, ARCHITECTURE "측정 — phase 9" API 표).

운영자만 연다(`/operator/github` 과 같은 규칙). 계산은 `metrics_api` 와 같은 것을 쓰고, 화면은 중앙값 옆에 n·미완료·모름을 함께 적는다.
모르는 값은 "모름" 이며 0 으로 보이지 않는다. 데이터·가짜 GitHub 는 `test_metrics_api` 의 fixture 를 그대로 쓴다.
"""

import re

from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain.metrics import BASELINE_NOTE
from workflow.server.auth import SESSION_COOKIE, sign_session

from .test_github_api import login
from .test_metrics_api import (  # noqa: F401 — fixture
    MERGE_7,
    SOURCE,
    TOKEN,
    fake,
    op,
    operator,
    record_merge,
    seed_github_bundle,
    settings,
)

CAUSAL_NOTE = "관측값이며 인과 효과로 단정하지 않는다"
BASELINE_ACTION = f'data-json-action="/operator/github/sources/{SOURCE}/baseline"'


def page(client: TestClient, params: dict | None = None) -> str:
    response = client.get("/metrics", params=params or {})
    assert response.status_code == 200, response.text
    return response.text


def row(text: str, label: str) -> str:
    """지표 표에서 `label` 행(<tr>…</tr>) 하나."""
    match = re.search(rf"<tr[^>]*>\s*<th[^>]*>{re.escape(label)}</th>.*?</tr>", text, re.S)
    assert match, label
    return match.group(0)


# --- 권한 ---------------------------------------------------------------------------------------


def test_metrics_page_is_operator_only(client, conn):
    # 로그인 전, 그리고 워크스페이스가 아닌 세션 행을 서명한 쿠키 — 셀프호스트에서는 둘 다 로그인 안 된 것
    repo.create_session(conn, "sess-other", "2026-09-20T00:00:00Z")
    stranger = TestClient(client.app)
    stranger.cookies.set(SESSION_COOKIE, sign_session("sess-other", "test-session-secret"))
    for anonymous in (client, stranger):
        response = anonymous.get("/metrics", follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
        assert BASELINE_ACTION not in response.text
        tasks = anonymous.get("/tasks", follow_redirects=False)  # 사이드바 링크를 볼 화면도 없다
        assert (tasks.status_code, tasks.headers["location"]) == (303, "/login")


def test_sidebar_links_metrics_page_for_operator(op):
    assert 'href="/metrics"' in op.get("/tasks").text


# --- 데이터 없음 / 있음 ----------------------------------------------------------------------------


def test_empty_session_renders_unknowns_not_zero(app):
    client = TestClient(app)
    login(client)  # 빈 워크스페이스 — 업무·소스 없음
    text = page(client)
    assert CAUSAL_NOTE in text
    for area in ("병목", "속도", "사람 부담", "품질", "비용", "신뢰성"):
        assert f">{area}<" in text
    cost = row(text, "CLI 보고 비용")
    assert "모름" in cost and "n 0" in cost and "$0" not in cost
    assert "모름" in row(text, "1회 통과율")
    assert "GitHub 소스가 없습니다" in text


def test_page_shows_values_with_n_and_unknown_counts(op):
    text = page(op)
    cost = row(text, "CLI 보고 비용")
    assert "$0.5" in cost and "n 1" in cost and "모름 1" in cost
    tokens = row(text, "출력 토큰")
    assert "모름" in tokens and "n 0" in tokens and "모름 2" in tokens
    failure = row(text, "실패율")
    assert "50%" in failure and "1/2" in failure and "n 2" in failure
    assert "timeout 1" in row(text, "실패 사유")
    assert "20분 0초" in row(text, "실행 시간")  # 중앙값 1200초
    assert "모름" in row(text, "1회 통과율")  # 검토 결과 없음 — 0% 가 아니다
    assert "0%" not in row(text, "1회 통과율")


# --- 기준선 대 도입 후 ---------------------------------------------------------------------------------


def test_top_compares_baseline_and_after_with_button_for_operator(op, fake):
    text = page(op)
    assert BASELINE_ACTION in text
    assert "가져온 적 없음" in text
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "이슈 열림 → 병합" in top and "도입 후" in top
    assert "n 0" in top and "미완료 2" not in top  # 직접 등록 업무 2개는 이슈 열림 → 병합 비교에 들어가지 않는다
    assert "승인" not in top  # 승인 지표는 아래 속도 표에

    assert op.post(f"/operator/github/sources/{SOURCE}/baseline").status_code == 200
    text = page(op)
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "acme/billing" in top
    assert "4시간 0분" in top and "n 2" in top  # 2시간·6시간의 중앙값
    assert BASELINE_NOTE in top
    assert "마지막 가져옴" in text and "KST" in text and "2건" in text
    assert BASELINE_ACTION in text


def test_top_compares_merge_only_and_approval_goes_to_speed_table(operator, conn):
    op, session_id = operator
    seed_github_bundle(conn, session_id)  # GitHub 묶음: 열림 00:00 → 승인 02:00 → 병합 04:00
    record_merge(conn, session_id, MERGE_7, "2026-09-26T06:00:00Z")
    text = page(op, {"from": "2026-09-26T00:00:00Z"})
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "4시간 0분" in top and "2시간 0분" not in top
    assert "병합 PR" in top
    approval = row(text, "접수 → 승인")
    assert "2시간 0분" in approval and "n 1" in approval
    assert "4시간 0분" in row(text, "접수 → 완료")


# --- 기간·그룹 ---------------------------------------------------------------------------------------


def test_period_and_group_params_are_reflected(op):
    params = {"from": "2026-09-01T00:00:00Z", "to": "2026-10-01T00:00:00+09:00", "group_by": "config_revision"}
    text = page(op, params)
    assert 'name="from" value="2026-09-01T00:00:00Z"' in text
    assert 'name="to" value="2026-10-01T00:00:00+09:00"' in text
    assert '<option value="config_revision" selected>' in text
    assert "설정 번호 1" in text and "설정 번호 2" in text
    links = re.findall(r'href="(/metrics\.(?:json|csv)[^"]*)"', text)
    assert len(links) == 2
    assert all("group_by=config_revision" in link and "from=2026-09-01T00%3A00%3A00Z" in link for link in links)

    narrowed = page(op, {"from": "2026-09-21T00:00:00Z"})
    assert "n 1" in row(narrowed, "실패율")


def test_empty_form_values_mean_no_filter(op):
    text = page(op, {"from": "", "to": "", "group_by": ""})
    assert "전체" in text
    assert 'href="/metrics.json"' in text and 'href="/metrics.csv"' in text


def test_invalid_params_render_error_page(op):
    for params in ({"from": "어제"}, {"group_by": "agent"},
                   {"from": "2026-09-22T00:00:00Z", "to": "2026-09-21T00:00:00Z"}):
        response = op.get("/metrics", params=params)
        assert response.status_code == 422, params
        assert "text/html" in response.headers["content-type"]


# --- 비밀값 --------------------------------------------------------------------------------------------


def test_page_has_no_secrets_or_paths(op, fake, settings):
    op.post(f"/operator/github/sources/{SOURCE}/baseline")
    text = page(op, {"group_by": "folder_commit"})
    for secret in (TOKEN, settings.operator_token, settings.session_secret, settings.diag_api_token,
                   str(settings.db_path.parent)):
        assert secret not in text
