# ruff: noqa: F811 — 서버 테스트 픽스처(world·cycle·worker …)를 가져와 인자로 쓴다
"""`runloom` 조회 명령(login·logout·list·next·show·report, step 4)과 행동 명령(decide·respond·start·stop, step 5) — phase 26 (ARCHITECTURE "CLI — phase 26" 명령 표·
"--json"·"next 고르기"·"리포트 고르기"·"오류와 exit code").

`main(..., http=TestClient(app))` 로 실제 앱에 붙인다. 리포트 상태(작성 중·실패·done·맞춤 풀이)는 러너를 돌리지 않고
서버 리포트 테스트의 준비(`world`)와 `repo` 리포트 함수로 심는다.
"""

import json
import os
import subprocess
import sys
import tomllib
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.workflow.server.conftest import NOW as SERVER_NOW
from tests.workflow.server.conftest import SESSION, log_in_member, store  # noqa: F401 — 픽스처
from tests.workflow.server.test_report_api import finish_job, report_body, tailor, world  # noqa: F401 — 픽스처
from tests.workflow.server.test_task_cycle import (  # noqa: F401 — 픽스처
    FIX_SHOP,
    NOW,
    REVIEW,
    clock,
    cycle,
    import_issue,
    make_worker,
    settings,
    worker,
)
from tests.workflow.server.test_web_work_list import assign, new_work, set_status
from tests.workflow.server.test_work_api import member_named, token_for
from workflow.adapters import repo
from workflow.cli.main import main, pick_next, pick_reports
from workflow.server.auth import ensure_workspace

from .conftest import MEMBER_NAME, issue_token

ROOT = Path(__file__).resolve().parents[3]
SERVER = "http://localhost:8000"


class Run:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err

    def json(self):
        return json.loads(self.out)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "runloom"


def run(app, home: Path, *argv: str, token: str | None = None, stdin: str = "") -> Run:
    env = {"RUNLOOM_HOME": str(home), "HOME": str(home.parent)}
    if token is not None:
        env.update(RUNLOOM_TOKEN=token, RUNLOOM_SERVER=SERVER)
    out, err = StringIO(), StringIO()
    code = main(list(argv), env=env, stdin=StringIO(stdin), stdout=out, stderr=err, http=TestClient(app))
    return Run(code, out.getvalue(), err.getvalue())


# --- 진입점 ---------------------------------------------------------------------------------------------


def test_console_script_is_declared_in_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["runloom"] == "workflow.cli.main:main_entry"


def test_module_help_runs():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, "-m", "workflow.cli", "--help"], capture_output=True, text=True,
                          env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    for name in ("login", "logout", "list", "next", "show", "report"):
        assert name in done.stdout


def test_help_and_usage_errors_return_codes_on_the_injected_streams(app, home):
    helped = run(app, home, "--help")
    assert (helped.code, helped.err) == (0, "") and "runloom" in helped.out
    assert run(app, home, "show", "--help").code == 0
    wrong = run(app, home, "show")
    assert wrong.code == 2 and "runloom show: error:" in wrong.err and wrong.out == ""
    assert run(app, home).code == 2  # 하위 명령 필수
    assert run(app, home, "decide").code == 2  # 행동 명령은 아직 없다(step 5)


# --- login·logout ---------------------------------------------------------------------------------------


def test_login_checks_the_token_then_saves_it(app, home, member_token):
    done = run(app, home, "login", "--server", "http://localhost:8000/", stdin=f"  {member_token}  \n다음 줄\n")

    assert done.code == 0, done.err
    assert done.out == f"{MEMBER_NAME}(member, 토큰 full)로 로그인했습니다.\n"
    path = home / "credentials.json"
    assert json.loads(path.read_text()) == {"server": SERVER, "token": member_token}
    assert path.stat().st_mode & 0o777 == 0o600
    assert member_token not in done.out + done.err
    assert run(app, home, "list").code == 0  # 저장한 자격으로 다음 명령


def test_login_json_is_the_me_response(app, home, member, member_token):
    done = run(app, home, "login", "--server", SERVER, "--json", stdin=member_token)
    assert done.code == 0, done.err
    body = done.json()
    assert set(body) == {"member_id", "display_name", "role", "token_scope"}
    assert (body["display_name"], body["role"], body["token_scope"]) == (MEMBER_NAME, "member", "full")
    assert member_token not in done.out


def test_login_with_a_wrong_token_saves_nothing(app, home, member):
    done = run(app, home, "login", "--server", SERVER, stdin="wfm_notarealtoken0000")
    assert done.code == 1
    first, second = done.err.splitlines()
    assert first.startswith("오류 401 unauthenticated: ") and second == "runloom login 으로 다시 로그인하세요."
    assert not (home / "credentials.json").exists()


def test_login_needs_a_token_and_a_safe_server(app, home, member_token):
    empty = run(app, home, "login", "--server", SERVER, stdin="   \n")
    assert empty.code == 2
    assert empty.err == "토큰을 표준 입력으로 주세요 (예: pbpaste | runloom login --server URL).\n"
    plain = run(app, home, "login", "--server", "http://example.com", stdin=member_token)
    assert plain.code == 2
    assert plain.err == ("서버 주소는 https:// 여야 합니다(http:// 는 localhost·127.0.0.1·::1 만): "
                         "http://example.com\n")
    assert not (home / "credentials.json").exists()


def test_logout_deletes_the_file_and_later_commands_ask_to_log_in(app, home, member_token):
    assert run(app, home, "login", "--server", SERVER, stdin=member_token).code == 0

    done = run(app, home, "logout")

    path = home / "credentials.json"
    assert done.code == 0
    assert done.out == f"로그인 정보를 지웠습니다: {path}\n서버의 토큰은 웹 /me 에서 폐기하세요.\n"
    assert not path.exists() and home.is_dir()
    again = run(app, home, "logout")
    assert (again.code, again.out) == (0, "지울 로그인 정보가 없습니다.\n서버의 토큰은 웹 /me 에서 폐기하세요.\n")
    assert run(app, home, "logout", "--json").json() == {"deleted": False}
    after = run(app, home, "list")
    assert (after.code, after.err) == (2, "runloom login --server URL 로 먼저 로그인하세요.\n")


def test_unreachable_server_is_one_line_with_the_address(home):
    out, err = StringIO(), StringIO()
    env = {"RUNLOOM_HOME": str(home), "RUNLOOM_TOKEN": "wfm_secretsecret", "RUNLOOM_SERVER": "http://127.0.0.1:9"}
    code = main(["list"], env=env, stdin=StringIO(), stdout=out, stderr=err)
    assert code == 1
    assert err.getvalue() == "서버에 연결할 수 없습니다(연결 실패): http://127.0.0.1:9\n"
    assert "wfm_secret" not in out.getvalue() + err.getvalue()


# --- list·next ------------------------------------------------------------------------------------------


@pytest.fixture
def turns(app, conn, member) -> dict:
    """김개발 차례 3건(RUN-1 normal 오래됨 · RUN-2 high · RUN-3 normal 최근), 다른 멤버 차례 RUN-4(high, 가장 오래됨)."""
    ensure_workspace(conn, SERVER_NOW)
    log_in_member(TestClient(app), display_name="박다른")
    me, other = member_named(conn, MEMBER_NAME), member_named(conn, "박다른")
    rows = [("오래된 보통", "normal", "2026-09-20T01:00:00Z", me), ("높음", "high", "2026-09-20T05:00:00Z", me),
            ("최근 보통", "normal", "2026-09-20T03:00:00Z", me), ("남의 차례", "high", "2026-09-20T00:00:00Z", other)]
    ids = {}
    for title, priority, updated, owner in rows:
        work_item_id, key = new_work(conn, title, priority=priority, updated=updated)
        assign(conn, work_item_id, "member", owner)
        set_status(conn, work_item_id, "내 차례", "확인해 주세요")
        conn.execute("UPDATE work_items SET updated_at = ? WHERE work_item_id = ?", (updated, work_item_id))
        ids[key] = work_item_id
    conn.commit()
    return ids


def test_list_default_filter_and_bad_filter(app, home, member_token, turns):
    every = run(app, home, "list", token=member_token)
    assert every.code == 0, every.err
    assert [line.split(" ")[0] for line in every.out.splitlines()] == ["RUN-4", "RUN-3", "RUN-2", "RUN-1"]
    assert "김개발" in every.out and "박다른" in every.out

    mine = run(app, home, "list", "--filter", "my_turn", "--json", token=member_token)
    assert [i["key"] for i in mine.json()["items"]] == ["RUN-3", "RUN-2", "RUN-1"]
    assert set(mine.json()) == {"items", "counts"}

    bad = run(app, home, "list", "--filter", "nope", token=member_token)
    assert bad.code == 1 and bad.err.startswith("오류 422 invalid_field: filter 는 ")
    assert run(app, home, "list", "--closed", "all", "--repository", "acme/none", token=member_token).code == 1


def test_next_picks_high_first_and_counts_the_rest(app, home, member_token, turns):
    done = run(app, home, "next", token=member_token)

    assert done.code == 0, done.err
    assert done.out.startswith("# RUN-2 높음\n")
    assert done.out.endswith("\n\n내 차례 2건 더 — runloom list --filter my_turn\n")
    assert "## 인계 리포트\n인계 리포트 없음" in done.out

    body = run(app, home, "next", "--json", token=member_token).json()
    assert set(body) == {"detail", "reports", "report", "tailored", "more_my_turn"}
    assert (body["detail"]["work"]["key"], body["more_my_turn"]) == ("RUN-2", 2)
    assert (body["reports"], body["report"], body["tailored"]) == ({"stages": [], "tailored": []}, None, None)


def test_next_among_normals_takes_the_oldest_update(app, home, conn, member_token, turns):
    conn.execute("UPDATE work_items SET priority = 'low' WHERE work_item_id = ?", (turns["RUN-2"],))
    conn.commit()
    done = run(app, home, "next", token=member_token)
    assert done.out.startswith("# RUN-1 오래된 보통\n")


def test_next_without_my_turn(app, home, member_token):
    done = run(app, home, "next", token=member_token)
    assert (done.code, done.out) == (0, "내 차례 업무가 없습니다.\n")
    assert run(app, home, "next", "--json", token=member_token).json() == {
        "detail": None, "reports": None, "report": None, "tailored": None, "more_my_turn": 0}


def test_pick_next_rule():
    items = [{"key": "RUN-3", "priority": "normal", "updated_at": "2026-01-02T00:00:00Z"},
             {"key": "RUN-2", "priority": "low", "updated_at": "2026-01-01T00:00:00Z"},
             {"key": "RUN-1", "priority": "normal", "updated_at": "2026-01-02T00:00:00Z"}]
    assert pick_next(items) == (items[0], 2)  # 같으면 API 순서
    assert pick_next([]) == (None, 0)


def test_read_token_reads_everything(app, home, conn, member, turns):
    reader = issue_token(member, scope="read")
    for argv in (["list"], ["next"], ["show", "RUN-1"], ["next", "--json"], ["show", "RUN-4", "--json"]):
        done = run(app, home, *argv, token=reader)
        assert done.code == 0, (argv, done.err)
        assert reader not in done.out + done.err
    login = run(app, home, "login", "--server", SERVER, stdin=reader)
    assert login.out == f"{MEMBER_NAME}(member, 토큰 read)로 로그인했습니다.\n"


# --- show·report ----------------------------------------------------------------------------------------


def token_of(conn, name: str, scope: str = "full") -> str:
    return token_for(conn, member_named(conn, name), scope)


def test_show_unknown_key_is_the_server_404(app, home, world, conn):
    done = run(app, home, "show", "RUN-999", token=token_of(conn, "다른이"))
    assert done.code == 1 and done.err.startswith("오류 404 not_found: ")


def test_show_done_original(app, home, world, conn):
    done = run(app, home, "show", world["key"], token=token_of(conn, "다른이", "read"))

    assert done.code == 0, done.err
    assert done.out.startswith(f"# {world['key']} ")
    assert f"## 인계 리포트 · 버그 수정 · 시도 1 · {world['report_id']}\n보낸 사람 보낸이 · 받는 사람 받는이\n" in done.out
    assert "- [c2] 없는 산출물을 인용\n  왜: 기록 없음\n  근거: artifact:art-unknown · 근거 확인 안 됨\n" in done.out
    assert "- [q1] 할인 상한 기준 확인\n  왜: 기록 없음\n  근거: source:work\n  답: 아직 없음\n" in done.out
    assert done.out.endswith("### 다이어그램\ngraph TD; A-->B\n")
    assert "맞춤 풀이" not in done.out and "## 다른 리포트" not in done.out

    body = run(app, home, "show", world["key"], "--json", token=token_of(conn, "다른이")).json()
    assert set(body) == {"detail", "reports", "report", "tailored"}
    assert (body["report"]["report_id"], body["tailored"]) == (world["report_id"], None)


def test_show_writing_and_failed_originals(app, home, world, conn, cycle):
    sender = world["clients"]["보낸이"]
    again = sender.post(f"/api/v1/reports/{world['report_id']}/regenerate").json()["report_id"]
    reader = token_of(conn, "다른이", "read")

    writing = run(app, home, "show", world["key"], token=reader)
    assert f"{again}\n보낸 사람 보낸이 · 받는 사람 받는이\n\n리포트 작성 중\n" in writing.out
    assert writing.out.endswith(f"## 다른 리포트\n- 버그 수정 · {world['report_id']} · 완성 · 이전\n")

    assert repo.lease_handoff_report(conn, cycle["billing"], now=NOW)["report_id"] == again
    assert repo.fail_handoff_report(conn, again, connector_id=cycle["billing"], reason="러너 오류", now=NOW)
    failed = run(app, home, "show", world["key"], token=reader)
    assert "\n\n리포트 없음(러너 오류)\n" in failed.out


def test_show_two_stages_opens_the_latest_created_and_lists_the_other(app, home, world, conn):
    tasks = [r["task_id"] for r in conn.execute(
        "SELECT task_id FROM tasks WHERE work_item_id = ? ORDER BY created_at, rowid", (world["work_item_id"],))]
    review_task = next(t for t in tasks if t != world["fix_task"])
    later = repo.record_handoff_report_unavailable(
        conn, session_id=SESSION, work_item_id=world["work_item_id"], source_execution_id=world["fix_exec"],
        source_task_id=review_task, job_type="original", agent_id=REVIEW, connector_id=None,
        reason="러너가 리포트를 지원하지 않음", inputs={}, now="2026-10-06T13:00:00Z")

    done = run(app, home, "show", world["key"], token=token_of(conn, "다른이"))

    assert done.code == 0, done.err
    report_part = done.out.split("## 인계 리포트 · ", 1)[1]
    assert report_part.startswith("커밋 검토 · ") and later["report_id"] in report_part.splitlines()[0]
    assert "\n\n리포트 없음(러너가 리포트를 지원하지 않음)\n" in report_part
    assert done.out.endswith(f"## 다른 리포트\n- 버그 수정 · {world['report_id']} · 완성\n")


@pytest.fixture
def my_tailored(world, conn, store, cycle) -> str:
    """받는이가 REVIEW 로 요청한 맞춤 풀이 — 받는 러너(shop)가 써서 done."""
    response = tailor(world["clients"]["받는이"], world["report_id"], REVIEW)
    assert response.status_code == 202, response.text
    assert finish_job(conn, store, cycle["shop"]) == response.json()["report_id"]
    return response.json()["report_id"]


def test_show_opens_my_tailored_report_but_not_others(app, home, world, conn, my_tailored):
    mine = run(app, home, "show", world["key"], token=token_of(conn, "받는이"))
    assert mine.code == 0, mine.err
    assert f"## 맞춤 풀이 · 받는이 · {REVIEW} · {my_tailored}\n원본 리포트: {world['report_id']}\n" in mine.out
    assert "- [t1] 받는 사람 관점 요약\n" in mine.out and "옛 리포트 기준" not in mine.out
    assert run(app, home, "show", world["key"], "--json", token=token_of(conn, "받는이")).json()[
        "tailored"]["report_id"] == my_tailored

    other = run(app, home, "show", world["key"], token=token_of(conn, "다른이"))
    assert "맞춤 풀이" not in other.out
    assert run(app, home, "show", world["key"], "--json", token=token_of(conn, "다른이")).json()["tailored"] is None


def test_my_tailored_report_on_an_old_original_stays_open_and_is_marked(app, home, world, conn, my_tailored):
    again = world["clients"]["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate").json()["report_id"]

    done = run(app, home, "show", world["key"], token=token_of(conn, "받는이"))

    assert f"· {again}\n" in done.out and "리포트 작성 중" in done.out
    assert f"## 맞춤 풀이 · 받는이 · {REVIEW} · {my_tailored} · 옛 리포트 기준\n" in done.out
    assert done.out.endswith(f"## 다른 리포트\n- 버그 수정 · {world['report_id']} · 완성 · 이전\n")


def test_pick_reports_rule():
    def view(rid, created, **extra):
        return {"report_id": rid, "created_at": created, **extra}

    a1, a0 = view("a1", "2026-01-03"), view("a0", "2026-01-01")
    b1 = view("b1", "2026-01-03")
    tailored = [view("t-old", "2026-01-02", requested_by_member_id="me", base_report_id="b0"),
                view("t-mine", "2026-01-04", requested_by_member_id="me", base_report_id="a0"),
                view("t-other", "2026-01-05", requested_by_member_id="you", base_report_id="a1")]
    reports = {"stages": [{"latest": a1, "previous": [a0]}, {"latest": b1, "previous": []}], "tailored": tailored}
    assert pick_reports(reports, member_id="me") == (b1, None)  # 같은 시각이면 뒤 단계, 기준이 그 단계가 아님
    reports["stages"][1]["latest"] = view("b1", "2026-01-02")
    assert pick_reports(reports, member_id="me") == (a1, tailored[1])  # 이전 원본 기준도 고른다
    assert pick_reports({"stages": [], "tailored": []}, member_id="me") == (None, None)


def test_report_command_renders_and_json_is_the_body(app, home, world, conn, my_tailored):
    token = token_of(conn, "받는이")
    assert world["clients"]["받는이"].put(f"/api/v1/reports/{world['report_id']}/decisions/q1",
                                          json={"answer": "changed", "note": "상한은 1만 원"}).status_code == 200

    done = run(app, home, "report", world["report_id"], token=token)

    assert done.code == 0, done.err
    lines = done.out.splitlines()
    assert lines[0] == f"## 인계 리포트 · 버그 수정 · 시도 1 · {world['report_id']}"
    for title in ("### 바뀐 것", "### 결정과 근거", "### 정하지 않고 남긴 것", "### 받는 사람이 결정·확인할 것",
                  "### 지킬 제약", "### 남은 일"):
        assert title in lines
    assert "  근거: artifact:art-unknown · 근거 확인 안 됨" in lines
    assert any(line.startswith("  답: 다르게 정함 — 상한은 1만 원 · 받는이 · ") for line in lines)
    body = run(app, home, "report", world["report_id"], "--json", token=token).json()
    assert body["report"] == report_body(world["report_id"])

    world["clients"]["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate")
    old = run(app, home, "report", my_tailored, token=token)
    assert old.out.splitlines()[0].endswith(" · 옛 리포트 기준")
    missing = run(app, home, "report", "hrp-000000000000", token=token)
    assert missing.code == 1 and missing.err.startswith("오류 404 not_found: ")


def test_config_error_from_a_bad_env_server(app, home, member_token):
    out, err = StringIO(), StringIO()
    env = {"RUNLOOM_HOME": str(home), "RUNLOOM_TOKEN": member_token, "RUNLOOM_SERVER": "https://x/path"}
    assert main(["list"], env=env, stdin=StringIO(), stdout=out, stderr=err, http=TestClient(app)) == 2
    assert err.getvalue() == "서버 주소에 경로·쿼리를 붙이지 마세요: https://x/path\n"


# --- 행동: decide ---------------------------------------------------------------------------------------


def decisions_of(conn, report_id: str) -> list[tuple]:
    return [(d["item_id"], d["answer"], d["note"]) for d in repo.list_report_decisions(conn, report_id)]


def test_decide_confirm_and_change_save_the_answer_and_print_the_report(app, home, world, conn):
    token = token_of(conn, "받는이")
    done = run(app, home, "decide", world["report_id"], "q1", "--confirm", token=token)

    assert done.code == 0, done.err
    assert done.out.startswith(f"답을 저장했습니다.\n\n## 인계 리포트 · 버그 수정 · 시도 1 · {world['report_id']}")
    assert any(line.startswith("  답: 확인함 · 받는이 · ") for line in done.out.splitlines())
    assert decisions_of(conn, world["report_id"]) == [("q1", "confirmed", None)]

    changed = run(app, home, "decide", world["report_id"], "q1", "--change", "다르게", "--json", token=token)
    assert changed.code == 0, changed.err
    assert changed.json()["report_id"] == world["report_id"]
    assert [(d["answer"], d["note"]) for d in changed.json()["decisions"]] == [("changed", "다르게")]
    assert decisions_of(conn, world["report_id"]) == [("q1", "changed", "다르게")]


def test_decide_needs_exactly_one_answer_and_the_server_checks_the_rest(app, home, world, conn):
    token = token_of(conn, "받는이")
    both = run(app, home, "decide", world["report_id"], "q1", "--confirm", "--change", "x", token=token)
    neither = run(app, home, "decide", world["report_id"], "q1", token=token)
    assert (both.code, neither.code) == (2, 2) and both.out == neither.out == ""

    long = run(app, home, "decide", world["report_id"], "q1", "--change", "가" * 501, token=token)
    assert long.code == 1 and long.err.startswith("오류 422 invalid_field: ")
    for name in ("보낸이", "다른이"):
        refused = run(app, home, "decide", world["report_id"], "q1", "--confirm", token=token_of(conn, name))
        assert refused.code == 1 and refused.err.startswith("오류 403 forbidden: "), refused.err
    assert decisions_of(conn, world["report_id"]) == []


# --- 행동: respond --------------------------------------------------------------------------------------


@pytest.fixture
def responder(app, conn, cycle) -> str:
    """cycle 워크스페이스의 멤버 김개발 full 토큰."""
    log_in_member(TestClient(app), display_name=MEMBER_NAME)
    return token_of(conn, MEMBER_NAME)


def open_request(conn, number: int, code: str, question: str = "재현 금액이 필요합니다") -> str:
    task_id = import_issue(conn, number)
    request_id, _ = repo.create_human_request_once(conn, task_id, code, question, f"{code}:exec-{number}", NOW)
    return request_id


def test_respond_uses_the_server_revision_and_a_second_run_finds_no_open_request(app, home, conn, responder):
    request_id = open_request(conn, 1, "fix_needs_information")

    done = run(app, home, "respond", request_id, "resume", "--text", "10,000원", token=responder)

    assert done.code == 0, done.err
    assert done.out == f"응답을 저장했습니다: {request_id} resume\n"
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "answered"
    assert repo.get_task(conn, "task-gh-1")["revision"] == 2
    again = run(app, home, "respond", request_id, "resume", "--text", "10,000원", token=responder)
    assert (again.code, again.out, again.err) == (1, "", f"열린 사람 요청 {request_id} 가 없습니다.\n")
    assert repo.get_task(conn, "task-gh-1")["revision"] == 2


def test_respond_json_is_the_post_response(app, home, conn, responder):
    request_id = open_request(conn, 1, "decision")
    body = run(app, home, "respond", request_id, "close", "--json", token=responder).json()
    assert (body["request_id"], body["task_revision"], body["created"]) == (request_id, 2, True)
    assert set(body) == {"request_id", "task_id", "response_id", "task_revision", "created"}


def test_respond_unknown_request_and_wrong_action(app, home, conn, responder):
    missing = run(app, home, "respond", "hreq-nope", "resume", token=responder)
    assert (missing.code, missing.err) == (1, "열린 사람 요청 hreq-nope 가 없습니다.\n")

    request_id = open_request(conn, 1, "fix_needs_information")
    wrong = run(app, home, "respond", request_id, "approve", token=responder)
    assert wrong.code == 1 and wrong.err.startswith("오류 422 invalid_field: ")
    blank = run(app, home, "respond", request_id, "resume", token=responder)  # --text 없음 → "" 를 보낸다
    assert blank.code == 1 and blank.err == "오류 422 invalid_field: 요청한 정보를 text 에 적어야 합니다.\n"
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "open"


def test_respond_passes_the_agent(app, home, conn, responder):
    request_id = open_request(conn, 2, "assignee_multiple", "누구?")
    without = run(app, home, "respond", request_id, "choose_agent", token=responder)
    assert without.code == 1 and without.err.startswith("오류 422 invalid_field: ")

    done = run(app, home, "respond", request_id, "choose_agent", "--agent", FIX_SHOP, token=responder)
    assert done.code == 0, done.err
    assert repo.get_task(conn, "task-gh-2")["chosen_agent_id"] == FIX_SHOP


# --- 행동: start·stop -----------------------------------------------------------------------------------


def test_start_prints_the_branch_and_stop_releases_it(app, home, conn, member, member_token):
    ensure_workspace(conn, SERVER_NOW)
    work_item_id, key = new_work(conn, "로그인 만료")
    conn.commit()

    started = run(app, home, "start", key, token=member_token)

    assert started.code == 0, started.err
    work = repo.get_work_item(conn, SESSION, work_item_id)
    assert work["status"] == "직접 작업 중" and work["direct_branch"]
    assert started.out == f"직접 작업을 시작했습니다.\n브랜치: {work['direct_branch']}\n"

    stopped = run(app, home, "stop", key, "--json", token=member_token)
    assert stopped.code == 0, stopped.err
    assert stopped.json()["direct"] is None and stopped.json()["work"]["key"] == key
    assert repo.get_work_item(conn, SESSION, work_item_id)["direct_member_id"] is None
    assert run(app, home, "start", key, token=member_token).code == 0
    assert run(app, home, "stop", key, token=member_token).out == "직접 작업을 그만두었습니다.\n"


def test_start_while_an_agent_works_is_the_server_error(app, home, world, conn):
    done = run(app, home, "start", world["key"], token=token_of(conn, "받는이"))
    assert done.code == 1 and done.err.startswith("오류 409 execution_conflict: "), done.err


# --- 행동 명령 공통 -------------------------------------------------------------------------------------


def test_read_token_cannot_act_and_no_token_is_printed(app, home, world, conn):
    request_id = open_request(conn, 3, "fix_needs_information")
    reader = token_of(conn, "받는이", "read")
    for argv in (["decide", world["report_id"], "q1", "--confirm"], ["respond", request_id, "resume", "--text", "x"],
                 ["start", world["key"]], ["stop", world["key"]]):
        done = run(app, home, *argv, token=reader)
        assert (done.code, done.out) == (1, ""), argv
        assert done.err == "오류 403 token_read_only: 읽기 전용 토큰입니다\n", (argv, done.err)
        assert reader not in done.out + done.err
    writer = token_of(conn, "받는이")
    for argv in (["decide", world["report_id"], "q1", "--confirm"], ["respond", request_id, "resume", "--text", "x"]):
        done = run(app, home, *argv, token=writer)
        assert done.code == 0, (argv, done.err)
        assert writer not in done.out + done.err
