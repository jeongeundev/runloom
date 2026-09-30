"""업무 키로 만드는 값 — ARCHITECTURE "업무 화면 — phase 16" 이름 표. 순수 함수(DB·HTTP 없음)."""

import re

BRANCH_SUMMARY_MAX = 40
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def branch_name(key: str, title: str) -> str:
    """직접 작업 브랜치 이름 `<키>-<요약>` — 요약은 제목의 ASCII 영숫자만(나머지는 `-` 하나로), 40자, 비면 키만.
    서버는 이 이름으로 명령을 실행하지 않는다 — 복사해 보여줄 뿐이다."""
    summary = _NOT_ALNUM.sub("-", title.lower()).strip("-")[:BRANCH_SUMMARY_MAX].rstrip("-")
    return f"{key}-{summary}" if summary else key


def work_path(key: str) -> str:
    """업무 주소 — 업무 화면에 그 업무의 패널을 연 상태(`/tasks?open=RUN-12`). 알림·원본 댓글 링크도 이것."""
    return f"/tasks?open={key}"
