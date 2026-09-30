"""업무 키로 만드는 값 — ARCHITECTURE "업무 화면 — phase 16" 이름 표. 순수 함수(DB·HTTP 없음)."""


def work_path(key: str) -> str:
    """업무 주소 — 업무 화면에 그 업무의 패널을 연 상태(`/tasks?open=RUN-12`). 알림·원본 댓글 링크도 이것."""
    return f"/tasks?open={key}"
