"""notification.py — 알림 문구·본문·URL 형식 (ADR-0018 결정 5, ARCHITECTURE "알림 (step 7·8)"). 순수 함수."""

import pytest

from workflow.domain.notification import (
    DISCORD_CONTENT_LIMIT,
    NotificationMessage,
    is_discord_url,
    notification_body,
    notification_text,
    webhook_host,
    webhook_url_valid,
)

DISCORD = "https://discord.com/api/webhooks/123/tok-secret"
GENERIC = "https://hooks.example.com/runloom/tok-secret"


def _message(**overrides) -> NotificationMessage:
    values = {
        "event": "pr_opened", "task_id": "task-gh-1", "title": "버그 1",
        "content": "[Runloom] PR 확인 — 버그 1 https://github.com/acme/billing/pull/31",
        "task_url": "https://runloom.example/tasks/task-gh-1", "pr_url": "https://github.com/acme/billing/pull/31",
    }
    return NotificationMessage(**{**values, **overrides})


def test_text_per_event():
    assert notification_text("human_request", title="버그 1", detail="무엇을 재현할까요?") == (
        "[Runloom] 사람 차례 — 버그 1: 무엇을 재현할까요?"
    )
    assert notification_text("pr_opened", title="버그 1", pr_url="https://github.com/a/b/pull/3") == (
        "[Runloom] PR 확인 — 버그 1 https://github.com/a/b/pull/3"
    )
    assert notification_text("task_failed", title="버그 1", detail="timeout · 20분 초과") == (
        "[Runloom] 실패 — 버그 1: timeout · 20분 초과"
    )



def test_text_for_delegation_events():
    """phase 17 사건 3개의 머리(ARCHITECTURE "알림 사건 3개")."""
    assert notification_text("delegated_to_you", title="쿠폰 오류", detail="김OO 가 opensql 에게 맡김") == (
        "[Runloom] 맡김 — 쿠폰 오류: 김OO 가 opensql 에게 맡김"
    )
    assert notification_text("runner_offline_waiting", title="쿠폰 오류", detail="러너가 꺼져 있어 RUN-12 가 기다림") == (
        "[Runloom] 러너 꺼짐 — 쿠폰 오류: 러너가 꺼져 있어 RUN-12 가 기다림"
    )
    assert notification_text("delegation_declined", title="쿠폰 오류", detail="이OO 가 거절 — 오늘은 Mac 을 못 씁니다") == (
        "[Runloom] 거절 — 쿠폰 오류: 이OO 가 거절 — 오늘은 Mac 을 못 씁니다"
    )

def test_text_for_next_step_events():
    """phase 22 사건 2개의 머리(ARCHITECTURE "결과 뒤 판단 — phase 22" 알림)."""
    assert notification_text("internal_request_received", title="쿠폰 오류",
                             detail="김OO · kube_proxy/investigation · 호스트 sysctl 값 확인") == (
        "[Runloom] 요청 받음 — 쿠폰 오류: 김OO · kube_proxy/investigation · 호스트 sysctl 값 확인"
    )
    assert notification_text("next_step_proposed", title="쿠폰 오류", detail="사람 확인 — 금액? · 확신도 0.82") == (
        "[Runloom] 다음 단계 제안 — 쿠폰 오류: 사람 확인 — 금액? · 확신도 0.82"
    )


def test_text_adds_the_task_link_on_its_own_line():
    text = notification_text("task_failed", title="버그 1", detail="x", task_url="https://runloom.example/tasks/t")
    assert text.splitlines() == ["[Runloom] 실패 — 버그 1: x", "https://runloom.example/tasks/t"]


def test_text_names_the_recipients_on_the_first_line():
    """공용 경로 본문 끝 `→ 이름[, 이름]` (ARCHITECTURE "알림 — 받는 사람별"). 링크는 그다음 줄."""
    assert notification_text("human_request", title="쿠폰 오류", detail="검토 승인", recipients=("김OO", "이OO")) == (
        "[Runloom] 사람 차례 — 쿠폰 오류: 검토 승인 → 김OO, 이OO"
    )
    text = notification_text("pr_opened", title="버그 1", pr_url="https://github.com/a/b/pull/3", recipients=("김OO",),
                             task_url="https://runloom.example/tasks/t")
    assert text.splitlines() == ["[Runloom] PR 확인 — 버그 1 https://github.com/a/b/pull/3 → 김OO",
                                 "https://runloom.example/tasks/t"]
    assert notification_text("task_failed", title="버그 1", detail="x", recipients=()) == "[Runloom] 실패 — 버그 1: x"


@pytest.mark.parametrize(("url", "expected"), [
    (DISCORD, True),
    ("https://DISCORDAPP.com/api/webhooks/1/x", True),
    ("https://canary.discord.com/api/webhooks/1/x", True),
    ("https://notdiscord.com/api/webhooks/1/x", False),
    ("https://discord.com.evil.example/x", False),
    (GENERIC, False),
])
def test_discord_host_detection(url, expected):
    assert is_discord_url(url) is expected


def test_discord_body_is_content_only_and_cut_at_2000():
    assert notification_body(DISCORD, _message()) == {"content": _message().content}
    long = _message(content="가" * 2500)
    body = notification_body(DISCORD, long)
    assert set(body) == {"content"} and len(body["content"]) == DISCORD_CONTENT_LIMIT == 2000


def test_generic_body_carries_the_fields():
    assert notification_body(GENERIC, _message()) == {
        "content": _message().content, "event": "pr_opened", "task_id": "task-gh-1",
        "task_url": "https://runloom.example/tasks/task-gh-1", "title": "버그 1",
        "pr_url": "https://github.com/acme/billing/pull/31",
    }
    body = notification_body(GENERIC, _message(event="task_failed", task_url=None, pr_url=None))
    assert (body["task_url"], body["pr_url"]) == (None, None)


@pytest.mark.parametrize(("url", "ok"), [
    (DISCORD, True),
    ("http://n8n.local:5678/webhook/abc", True),
    ("ftp://example.com/x", False),
    ("https:///no-host", False),
    ("https://user:pass@example.com/x", False),
    ("https://example.com/" + "a" * 2048, False),
    ("not a url", False),
    ("", False),
])
def test_webhook_url_format(url, ok):
    assert webhook_url_valid(url) is ok


def test_host_only_for_logs():
    assert webhook_host(DISCORD) == "discord.com"
    assert webhook_host("http://n8n.local:5678/webhook/abc") == "n8n.local"
    assert webhook_host("not a url") == "?"
