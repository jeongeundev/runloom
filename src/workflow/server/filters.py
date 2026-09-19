"""Jinja2 템플릿 필터. 서버는 UTC 로 저장하고 화면은 Asia/Seoul 로 보인다 (UI_GUIDE 타이포그래피 절).

라벨 매핑은 화면 표시용이다. 코드 값(`outcome`, `kind`) 은 바꾸지 않는다 (GLOSSARY `outcome 라벨`).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# UI_GUIDE "결과 카드" — outcome 코드 → 화면 라벨
OUTCOME_LABELS = {
    "ready_for_handoff": "인계 가능",
    "ready_for_review": "검토 가능",
    "needs_information": "정보 필요",
}

# UI_GUIDE "산출물 칩" — Artifact kind → 칩 라벨. 없는 kind 는 코드 값 그대로
KIND_LABELS = {
    "diff": "diff",
    "test_log_before": "테스트 전",
    "test_log_after": "테스트 후",
    "report_output": "보고서",
    "verification_log": "검증 로그",
    "diagnosis_result": "진단 결과",
    "code_change_result": "수정 결과",
    "handoff_bundle": "인계 묶음",
    "tool_trace": "조회 이력",
    "evidence": "근거",
    "codex_jsonl": "Codex JSONL",
    "codex_stderr": "Codex stderr",
    "review_comment": "검토 의견",
}


def kst(value: str | None) -> str:
    """RFC 3339 시각 → `2026-09-20 10:12:03 KST`. 빈 값은 빈 문자열."""
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def ago(value: str | None, now: str) -> str:
    """상대 시각 `12분 전`. 목록과 "마지막 확인" 에만 쓴다. 미래 값(시계 차이) 은 `방금 전`."""
    if not value:
        return ""
    seconds = int((datetime.fromisoformat(now) - datetime.fromisoformat(value)).total_seconds())
    if seconds < 60:
        return "방금 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def duration(seconds: int | None) -> str:
    """실행 블록 헤더의 경과 시간 `4분 13초`."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}초"
    if seconds < 3600:
        return f"{seconds // 60}분 {seconds % 60}초"
    return f"{seconds // 3600}시간 {seconds % 3600 // 60}분"


def outcome_label(outcome: str | None) -> str:
    if not outcome:
        return ""
    return OUTCOME_LABELS.get(outcome, outcome)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
