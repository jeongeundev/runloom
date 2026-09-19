"""Jinja2 템플릿 필터. 서버는 UTC 로 저장하고 화면은 Asia/Seoul 로 보인다 (UI_GUIDE 타이포그래피 절)."""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst(value: str | None) -> str:
    """RFC 3339 시각 → `2026-09-20 10:12:03 KST`. 빈 값은 빈 문자열."""
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
