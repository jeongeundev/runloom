"""알림 웹훅 문구·본문·URL 형식 (ADR-0018 결정 5, ARCHITECTURE "알림 (step 7·8)"). 순수 함수 — HTTP 는 `adapters/notify_sender`.

문구는 원인 한 줄 + 업무 링크. 비밀·이슈 본문 전체·로그를 넣지 않는다. URL 은 토큰을 담으므로 로그에는 `webhook_host` 만 쓴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

DISCORD_CONTENT_LIMIT = 2000  # Discord 웹훅 `content` 상한
WEBHOOK_URL_MAX_LENGTH = 2048
_DISCORD_HOSTS = ("discord.com", "discordapp.com")
_HEADLINES = {"human_request": "사람 차례", "pr_opened": "PR 확인", "task_failed": "실패"}


@dataclass(frozen=True)
class NotificationMessage:
    event: str
    task_id: str | None
    title: str
    content: str
    task_url: str | None
    pr_url: str | None


def notification_text(
    event: str, *, title: str, detail: str | None = None, pr_url: str | None = None, task_url: str | None = None,
    recipients: Sequence[str] = (),
) -> str:
    """`[Runloom] <원인> — <제목>: <내용>` 또는 PR 이면 `… — <제목> <pr_url>`. 받는 사람 표시 이름이 있으면 끝에
    ` → 이름, 이름`(공용 경로). 업무 링크가 있으면 다음 줄."""
    line = f"[Runloom] {_HEADLINES[event]} — {title}"
    if pr_url:
        line += f" {pr_url}"
    elif detail:
        line += f": {detail}"
    if recipients:
        line += f" → {', '.join(recipients)}"
    return f"{line}\n{task_url}" if task_url else line


def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def is_discord_url(url: str) -> bool:
    host = _host(url) or ""
    return any(host == d or host.endswith(f".{d}") for d in _DISCORD_HOSTS)


def notification_body(url: str, message: NotificationMessage) -> dict:
    """Discord 호스트면 `{"content"}`(2000자 자름) 만, 그 밖은 필드를 함께 싣는 JSON."""
    if is_discord_url(url):
        return {"content": message.content[:DISCORD_CONTENT_LIMIT]}
    return {
        "content": message.content, "event": message.event, "task_id": message.task_id,
        "task_url": message.task_url, "title": message.title, "pr_url": message.pr_url,
    }


def webhook_url_valid(url: str) -> bool:
    """`http`·`https`, 호스트 있음, 2048자 이하, 사용자 정보(`user:pass@`) 없음."""
    if not url or len(url) > WEBHOOK_URL_MAX_LENGTH:
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and parts.username is None and parts.password is None


def webhook_host(url: str) -> str:
    """로그·화면용 호스트 이름. 읽을 수 없으면 `?`."""
    return _host(url) or "?"
