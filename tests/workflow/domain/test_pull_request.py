"""pull_request — 초안 PR 본문·브랜치 이름·사람 요청 문구 (ADR-0018 결정 4, ARCHITECTURE "초안 PR (step 6)")."""

from workflow.domain.pull_request import (
    PR_REQUEST_PREFIX,
    REVIEW_SUMMARY_LIMIT,
    head_branch,
    not_pushed_question,
    origin_line,
    pr_body,
    pr_request_cause_key,
    pr_title,
)


def test_head_branch_is_the_runner_task_branch():
    assert head_branch("task-0123456789ab") == "task/task-0123456789ab"


def test_head_branch_uses_the_work_key_like_the_runner():
    """phase 14 step 7 — 러너 `git_ops` 와 같은 `contracts/v1.result_branch`."""
    assert head_branch("task-1", work_key="RUN-3") == "runloom/RUN-3"
    assert head_branch("task-1", work_key="RUN-3", branch_seq=1) == "runloom/RUN-3"
    assert head_branch("task-1", work_key="RUN-3", branch_seq=2) == "runloom/RUN-3-2"


def test_pr_title_puts_the_work_key_first():
    assert pr_title("RUN-23", "할인 쿠폰이 두 번 적용됨") == "RUN-23 할인 쿠폰이 두 번 적용됨"
    assert pr_title(None, "할인 쿠폰이 두 번 적용됨") == "할인 쿠폰이 두 번 적용됨"


def test_body_names_the_work_key_and_keeps_fixes_first():
    body = pr_body(issue_number=12, task_id="task-1", review_summary="요약", task_url=None, work_key="RUN-3")
    lines = body.splitlines()
    assert lines[0] == "Fixes #12"
    assert "업무 키: RUN-3" in lines
    assert lines[-1] == "<!-- runloom:task=task-1 -->"
    assert "업무 키" not in pr_body(issue_number=12, task_id="task-1", review_summary="요약", task_url=None)


def test_body_starts_with_fixes_then_summary_link_and_marker():
    body = pr_body(issue_number=12, task_id="task-1", review_summary="중복 조건 제거 확인",
                   task_url="https://runloom.example/tasks/task-1")
    lines = body.splitlines()
    assert lines[0] == "Fixes #12"
    assert "중복 조건 제거 확인" in body
    assert "Runloom 업무: https://runloom.example/tasks/task-1" in lines
    assert lines[-1] == "<!-- runloom:task=task-1 -->"


def test_body_without_public_url_names_the_task_instead_of_a_link():
    body = pr_body(issue_number=3, task_id="task-9", review_summary="요약", task_url=None)
    assert "Runloom 업무: task-9" in body.splitlines()
    assert "http" not in body


def test_long_review_summary_is_cut():
    body = pr_body(issue_number=1, task_id="t", review_summary="가" * (REVIEW_SUMMARY_LIMIT + 500), task_url=None)
    assert "가" * REVIEW_SUMMARY_LIMIT not in body
    assert "가" * (REVIEW_SUMMARY_LIMIT - 1) + "…" in body


def test_empty_summary_leaves_no_summary_section():
    body = pr_body(issue_number=1, task_id="t", review_summary="  ", task_url=None)
    assert "검토 요약" not in body


def test_not_pushed_question_tells_the_push_command():
    question = not_pushed_question("runloom/RUN-7")
    assert "git push origin runloom/RUN-7" in question


def test_pr_request_cause_key_uses_the_prefix():
    assert pr_request_cause_key("exec-r1") == f"{PR_REQUEST_PREFIX}exec-r1"
    assert pr_request_cause_key("exec-r1").startswith(PR_REQUEST_PREFIX)


# --- phase 18 step 6 — Jira 원본은 `Fixes` 없이 원본 링크 한 줄 ---------------------------------------


def test_origin_line_names_the_source_key_and_url():
    assert origin_line("SHOP-12", "https://acme.atlassian.net/browse/SHOP-12") == (
        "원본: SHOP-12 — https://acme.atlassian.net/browse/SHOP-12")
    assert origin_line("SHOP-12", None) == "원본: SHOP-12"


def test_body_without_issue_number_starts_with_the_origin_line_and_never_fixes():
    line = origin_line("SHOP-12", "https://acme.atlassian.net/browse/SHOP-12")
    body = pr_body(issue_number=None, task_id="task-1", review_summary="요약", task_url=None, work_key="RUN-3",
                   origin_line=line)
    lines = body.splitlines()
    assert lines[:2] == [line, ""] and "Fixes" not in body
    assert "업무 키: RUN-3" in lines and lines[-1] == "<!-- runloom:task=task-1 -->"


def test_body_without_issue_number_or_origin_starts_with_the_summary():
    body = pr_body(issue_number=None, task_id="task-1", review_summary="요약", task_url=None)
    assert body.splitlines()[:2] == ["검토 요약:", "요약"] and "Fixes" not in body


def test_issue_number_wins_over_the_origin_line():
    body = pr_body(issue_number=12, task_id="task-1", review_summary="요약", task_url=None, origin_line="원본: X-1")
    assert body.splitlines()[0] == "Fixes #12" and "원본: X-1" not in body
