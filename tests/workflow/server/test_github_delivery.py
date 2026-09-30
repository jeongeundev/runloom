"""github_delivery — 원본 이슈 댓글 outbox 전달과 marker 조정 (phase 8 step 12, ADR-0014 결정 8).

실제 GitHub 을 부르지 않는다. `FakeIssue` 가 `httpx.MockTransport` 로 이슈 하나의 댓글 API(목록 페이지·생성·수정)를
흉내 내고 `HttpGitHubClient` 를 그대로 쓴다. 원격 exactly-once 는 보장하지 않는다 — 여기서 확인하는 것은
"응답을 잃은 POST 는 marker 를 찾기 전에 다시 보내지 않는다" 와 "한 Task 에 동시에 한 소비자만 보낸다" 다.
"""

import json
import re

import httpx
import pytest

from workflow.adapters import repo
from workflow.adapters.db import connect, init_schema
from workflow.adapters.github_client import HttpGitHubClient
from workflow.contracts.v1 import BUILTIN_KINDS
from workflow.domain.issue_intake import snapshot_to_task_spec
from workflow.domain.kinds import get_kind
from workflow.server import github_delivery
from workflow.server.github_delivery import CLAIM_SECONDS, deliver_source_updates, marker

from .test_github_sync import config, issue

BUG_FIX = get_kind(BUILTIN_KINDS, "bug_fix")
SESSION = "sess-delivery"
SOURCE = "ghs-1a2b3c4d"
TASK = "task-gh-41"
NOW = "2026-10-06T12:00:00.000000Z"
LATER = "2026-10-06T12:01:00.000000Z"
TOKEN = "ghp_" + "DeliveryT0ken" * 3
COMMENTS = "/repos/acme/billing/issues/41/comments"


def _after(seconds: int) -> str:
    return github_delivery._plus_seconds(NOW, seconds)


class FakeIssue:
    """이슈 #41 의 댓글. `script[method]` 는 그 메서드 호출마다 하나씩 꺼내는 동작이다:
    None(정상)·int(그 상태 코드)·"timeout"(보내기 전 끊김)·"timeout_after"(서버는 처리하고 응답만 끊김)·callable(정상 전에 부름)."""

    def __init__(self, per_page: int = 100):
        self.comments: dict[int, str] = {}
        self.next_id = 9000
        self.per_page = per_page
        self.script: dict[str, list] = {"GET": [], "POST": [], "PATCH": []}
        self.calls: list[tuple[str, str]] = []
        self.auth: set[str] = set()

    def add_foreign(self, body: str) -> int:
        self.next_id += 1
        self.comments[self.next_id] = body
        return self.next_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        self.auth.add(request.headers.get("authorization", ""))
        step = self.script[method].pop(0) if self.script.get(method) else None
        if step == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if isinstance(step, int):
            headers = {"retry-after": "120"} if step == 429 else {}
            return httpx.Response(step, headers=headers, json={"message": "no"})
        if callable(step):
            step()
        response = self._serve(request, method, path)
        if step == "timeout_after":
            raise httpx.ReadTimeout("timeout", request=request)
        return response

    def _serve(self, request: httpx.Request, method: str, path: str) -> httpx.Response:
        if method == "GET" and path == COMMENTS:
            page = int(request.url.params.get("page", "1"))
            ids = sorted(self.comments)
            chunk = ids[(page - 1) * self.per_page:page * self.per_page]
            headers = {}
            if page * self.per_page < len(ids):
                headers["link"] = f'<https://api.github.com{COMMENTS}?per_page=100&page={page + 1}>; rel="next"'
            return httpx.Response(200, headers=headers, json=[
                {"id": i, "body": self.comments[i], "user": {"id": 1, "login": "runloom-bot"},
                 "updated_at": "2026-10-06T12:00:00Z"} for i in chunk
            ])
        if method == "POST" and path == COMMENTS:
            self.next_id += 1
            self.comments[self.next_id] = json.loads(request.content)["body"]
            return httpx.Response(201, json={"id": self.next_id})
        found = re.fullmatch(r"/repos/acme/billing/issues/comments/(\d+)", path)
        if method == "PATCH" and found:
            comment_id = int(found.group(1))
            if comment_id not in self.comments:
                return httpx.Response(404, json={"message": "Not Found"})
            self.comments[comment_id] = json.loads(request.content)["body"]
            return httpx.Response(200, json={"id": comment_id})
        raise AssertionError(f"반영은 댓글 API 만 부른다: {method} {path}")

    def ours(self) -> list[int]:
        return [i for i, body in self.comments.items() if body.startswith(marker(TASK))]

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)


@pytest.fixture
def fake() -> FakeIssue:
    return FakeIssue()


@pytest.fixture
def client(fake) -> HttpGitHubClient:
    return HttpGitHubClient(TOKEN, ["acme/billing"], transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "central.sqlite"


@pytest.fixture
def db(db_path):
    c = connect(db_path)
    init_schema(c)
    repo.create_session(c, SESSION, NOW)
    repo.save_github_source(c, SESSION, config(), NOW)
    snapshot = issue(41)
    spec = snapshot_to_task_spec(config(), snapshot, kind=BUG_FIX, session_id=SESSION, task_id=TASK)
    repo.upsert_source_issue(c, SESSION, SOURCE, snapshot, task=spec, now=NOW)
    yield c
    c.close()


def body(text: str) -> str:
    return f"{marker(TASK)}\n{text}"


def enqueue(db, text: str, now: str = NOW):
    delivery, _ = repo.enqueue_source_delivery_once(db, TASK, body(text), now)
    return delivery


def rows(db) -> list:
    return repo.list_source_deliveries(db, TASK)


def latest(db):
    return rows(db)[-1]


# --- 정상 전달 -----------------------------------------------------------------------------


def test_first_update_creates_one_comment_and_later_revisions_edit_it(db, client, fake):
    enqueue(db, "착수")
    report = deliver_source_updates(db, client, NOW)

    assert (report.created, report.updated) == (1, 0)
    (comment_id,) = fake.ours()
    first = latest(db)
    assert (first.state, first.comment_id, first.attempts, first.last_error) == ("delivered", comment_id, 1, None)
    assert fake.comments[comment_id] == body("착수")

    # 같은 tick 을 다시 돌아도 보낼 것이 없다
    assert deliver_source_updates(db, client, NOW).any() is False
    assert fake.count("POST") == 1

    enqueue(db, "수정 결과 준비", LATER)
    report = deliver_source_updates(db, client, LATER)
    assert (report.created, report.updated) == (0, 1)
    assert fake.ours() == [comment_id]  # 댓글은 Task 당 하나 — 같은 댓글을 고친다
    assert fake.comments[comment_id] == body("수정 결과 준비")
    assert (latest(db).state, latest(db).body_revision, latest(db).comment_id) == ("delivered", 2, comment_id)
    assert fake.auth == {f"Bearer {TOKEN}"}


def test_only_the_latest_revision_is_sent(db, client, fake):
    enqueue(db, "착수")
    enqueue(db, "수정 결과 준비")
    enqueue(db, "검토 승인")

    deliver_source_updates(db, client, NOW)

    assert fake.count("POST") == 1 and fake.count("PATCH") == 0
    (comment_id,) = fake.ours()
    assert fake.comments[comment_id] == body("검토 승인")
    states = [(d.body_revision, d.state) for d in rows(db)]
    assert states == [(1, "pending"), (2, "pending"), (3, "delivered")]  # 이전 revision 은 보내지 않고 남는다


# --- 응답 유실·crash 뒤 조정 --------------------------------------------------------------


def test_timeout_after_the_comment_was_created_is_reconciled_without_a_second_post(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = ["timeout_after"]

    report = deliver_source_updates(db, client, NOW)

    assert report.uncertain == 1
    row = latest(db)
    assert (row.state, row.comment_id, row.attempts) == ("unknown", None, 1)
    assert "ReadTimeout" in row.last_error and TOKEN not in row.last_error
    assert len(fake.ours()) == 1

    report = deliver_source_updates(db, client, LATER)

    assert report.reconciled == 1
    assert fake.count("POST") == 1  # 조회로 찾았으므로 다시 보내지 않는다
    (comment_id,) = fake.ours()
    assert (latest(db).state, latest(db).comment_id) == ("delivered", comment_id)


def test_timeout_before_creation_is_reposted_only_after_the_marker_is_confirmed_absent(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = ["timeout"]
    deliver_source_updates(db, client, NOW)
    assert latest(db).state == "unknown" and fake.ours() == []

    report = deliver_source_updates(db, client, LATER)

    assert (report.requeued, report.created) == (1, 1)
    assert fake.calls.index(("GET", COMMENTS)) < len(fake.calls) - 1  # 조회가 재POST 보다 먼저
    assert fake.calls[-1] == ("POST", COMMENTS)
    assert len(fake.ours()) == 1 and latest(db).state == "delivered"


def test_failed_lookup_keeps_the_delivery_uncertain_and_does_not_post(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = ["timeout"]
    deliver_source_updates(db, client, NOW)
    fake.script["GET"] = [500]

    report = deliver_source_updates(db, client, LATER)

    assert report.uncertain == 1
    assert fake.count("POST") == 1
    row = latest(db)
    assert row.state == "unknown" and row.next_at > LATER  # 물러났다가 다시 조회
    assert "HTTP 500" in row.last_error


def test_marker_on_a_later_comment_page_is_found(db, fake):
    fake.per_page = 2
    client = HttpGitHubClient(TOKEN, ["acme/billing"], transport=httpx.MockTransport(fake.handler))
    for i in range(3):
        fake.add_foreign(f"사람 댓글 {i}")
    enqueue(db, "착수")
    fake.script["POST"] = ["timeout_after"]
    deliver_source_updates(db, client, NOW)
    fake.add_foreign(f"인용: {marker(TASK)}")  # marker 가 첫 줄이 아닌 댓글은 우리 것이 아니다

    report = deliver_source_updates(db, client, LATER)

    assert report.reconciled == 1
    assert fake.count("POST") == 1
    assert [p for m, p in fake.calls if m == "GET"] == [COMMENTS, COMMENTS]  # 두 페이지(1·2)를 봤다
    assert latest(db).comment_id == fake.ours()[0]


def test_crash_right_after_a_successful_post_is_reconciled_after_the_claim_expires(db, client, fake, monkeypatch):
    enqueue(db, "착수")
    original = HttpGitHubClient.create_comment

    def crash_after_post(self, *args):
        original(self, *args)
        raise KeyboardInterrupt  # 응답을 받은 뒤 기록 전에 프로세스가 죽는다

    monkeypatch.setattr(HttpGitHubClient, "create_comment", crash_after_post)
    with pytest.raises(KeyboardInterrupt):
        deliver_source_updates(db, client, NOW)
    monkeypatch.undo()
    assert latest(db).state == "sending"

    # claim 이 살아 있는 동안은 다른 소비자가 건드리지 않는다
    assert deliver_source_updates(db, client, _after(CLAIM_SECONDS - 1)).any() is False
    assert fake.count("GET") == 0

    report = deliver_source_updates(db, client, _after(CLAIM_SECONDS))

    assert report.reconciled == 1
    assert fake.count("POST") == 1 and len(fake.ours()) == 1
    assert latest(db).state == "delivered"


# --- 여러 소비자·순서 -----------------------------------------------------------------------


def test_second_consumer_during_an_in_flight_post_sends_nothing(db, db_path, client, fake):
    enqueue(db, "착수")
    other = connect(db_path)
    nested = []

    def second_consumer():
        repo.enqueue_source_delivery_once(other, TASK, body("수정 결과 준비"), NOW)  # 전송 중 새 revision
        nested.append(deliver_source_updates(other, client, NOW))

    fake.script["POST"] = [second_consumer]
    deliver_source_updates(db, client, NOW)
    other.close()

    assert nested[0].any() is False  # 전송 중인 Task 는 다른 소비자가 보내지 않는다
    assert fake.count("POST") == 1
    assert [(d.body_revision, d.state) for d in rows(db)] == [(1, "delivered"), (2, "pending")]

    deliver_source_updates(db, client, LATER)
    (comment_id,) = fake.ours()
    assert fake.comments[comment_id] == body("수정 결과 준비")


def test_stale_consumer_cannot_overwrite_a_newer_claim(db):
    delivery = enqueue(db, "착수")
    first = repo.claim_source_delivery(db, delivery.delivery_id, expected_state="pending", now=NOW,
                                       claim_until=_after(CLAIM_SECONDS), comment_id=None, for_send=True)
    assert first == 1
    # 같은 상태에서 두 번째 claim 은 실패한다
    assert repo.claim_source_delivery(db, delivery.delivery_id, expected_state="pending", now=NOW,
                                      claim_until=_after(CLAIM_SECONDS), comment_id=None, for_send=True) is None
    # claim 만료 뒤 다른 소비자가 가져간다
    later = _after(CLAIM_SECONDS)
    second = repo.claim_source_delivery(db, delivery.delivery_id, expected_state="sending", now=later,
                                        claim_until=_after(2 * CLAIM_SECONDS), comment_id=None, for_send=False)
    assert second == 2
    # 늦게 끝난 첫 소비자의 기록은 버려진다
    assert repo.record_source_delivery(db, delivery.delivery_id, attempts=first, state="delivered", comment_id=77,
                                       next_at=None, last_error=None, now=later) is False
    assert repo.record_source_delivery(db, delivery.delivery_id, attempts=second, state="delivered", comment_id=78,
                                       next_at=None, last_error=None, now=later) is True
    assert (latest(db).state, latest(db).comment_id) == ("delivered", 78)


def test_a_newer_revision_does_not_repost_while_an_older_post_is_uncertain(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = ["timeout_after"]
    deliver_source_updates(db, client, NOW)
    enqueue(db, "수정 결과 준비", LATER)

    deliver_source_updates(db, client, LATER)

    # 이전 POST 를 먼저 조정하고(찾음) 새 revision 은 그 댓글을 고친다 — 새 댓글을 만들지 않는다
    assert fake.count("POST") == 1 and fake.count("PATCH") == 1
    (comment_id,) = fake.ours()
    assert fake.comments[comment_id] == body("수정 결과 준비")
    assert [(d.body_revision, d.state) for d in rows(db)] == [(1, "delivered"), (2, "delivered")]


# --- 거절·한도·삭제 -------------------------------------------------------------------------


def test_rate_limit_defers_the_delivery_and_stops_this_pass(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = [429]

    report = deliver_source_updates(db, client, NOW)

    assert report.rate_limited is True
    row = latest(db)
    assert (row.state, row.next_at, row.comment_id) == ("pending", _after(120), None)
    assert deliver_source_updates(db, client, _after(119)).any() is False
    assert fake.count("POST") == 1

    deliver_source_updates(db, client, _after(120))
    assert latest(db).state == "delivered" and len(fake.ours()) == 1


def test_forbidden_is_a_delivery_failure_not_a_task_failure(db, client, fake):
    enqueue(db, "착수")
    fake.script["POST"] = [403]
    task_before = dict(repo.get_task(db, TASK))

    report = deliver_source_updates(db, client, NOW)

    assert report.failed == 1
    row = latest(db)
    assert (row.state, row.last_error) == ("failed", f"POST {COMMENTS}: HTTP 403")
    assert dict(repo.get_task(db, TASK)) == task_before  # 반영 실패는 Task 상태를 바꾸지 않는다
    assert deliver_source_updates(db, client, LATER).any() is False  # 같은 revision 은 다시 보내지 않는다


def test_deleted_comment_is_not_recreated(db, client, fake):
    enqueue(db, "착수")
    deliver_source_updates(db, client, NOW)
    (comment_id,) = fake.ours()
    del fake.comments[comment_id]  # 사람이 댓글을 지웠다
    enqueue(db, "수정 결과 준비", LATER)

    report = deliver_source_updates(db, client, LATER)

    assert report.failed == 1
    assert fake.count("POST") == 1 and fake.ours() == []
    row = latest(db)
    assert (row.state, row.comment_id) == ("failed", comment_id)
    assert "HTTP 404" in row.last_error


def test_patch_timeout_is_retried_as_an_edit(db, client, fake):
    enqueue(db, "착수")
    deliver_source_updates(db, client, NOW)
    enqueue(db, "수정 결과 준비", LATER)
    fake.script["PATCH"] = ["timeout"]

    deliver_source_updates(db, client, LATER)
    assert latest(db).state == "pending"  # PATCH 는 같은 댓글을 덮어쓰므로 다시 보내도 중복이 없다

    deliver_source_updates(db, client, latest(db).next_at)
    assert fake.count("POST") == 1 and fake.count("PATCH") == 2
    assert latest(db).state == "delivered"


def test_stopped_source_is_not_written(db, client, fake):
    enqueue(db, "착수")
    repo.save_github_source(db, SESSION, config(enabled=False, config_revision=2), NOW)

    assert deliver_source_updates(db, client, NOW).any() is False
    assert fake.calls == []
    assert latest(db).state == "pending"


def test_source_filter_writes_only_that_sources_issues(db, client, fake):
    """소스마다 자격이 다를 수 있어(ADR-0017) 워커는 소스별 클라이언트로 그 소스의 반영만 보낸다."""
    enqueue(db, "착수")
    assert deliver_source_updates(db, client, NOW, source_id="ghs-00000009").any() is False
    assert fake.calls == [] and latest(db).state == "pending"
    assert deliver_source_updates(db, client, NOW, source_id=SOURCE).created == 1


# --- 상세 링크 (phase 16 step 5) --------------------------------------------------------------------


def test_comment_links_to_the_work_panel(db):
    text = github_delivery.source_update_body(db, None, repo.get_task(db, TASK), "https://runloom.example")
    assert "- 상세: https://runloom.example/tasks?open=RUN-1 " in text
    assert f"/tasks/{TASK}" not in text
