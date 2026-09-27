"""pull_request — 초안 PR 본문·브랜치 이름·사람 요청 문구 (ADR-0018 결정 4, ARCHITECTURE "초안 PR (step 6)")."""

from workflow.domain.pull_request import (
    PR_REQUEST_PREFIX,
    REVIEW_SUMMARY_LIMIT,
    head_branch,
    not_pushed_question,
    pr_body,
    pr_request_cause_key,
)


def test_head_branch_is_the_runner_task_branch():
    assert head_branch("task-0123456789ab") == "task/task-0123456789ab"


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
    question = not_pushed_question("task-7")
    assert "git push origin task/task-7" in question


def test_pr_request_cause_key_uses_the_prefix():
    assert pr_request_cause_key("exec-r1") == f"{PR_REQUEST_PREFIX}exec-r1"
    assert pr_request_cause_key("exec-r1").startswith(PR_REQUEST_PREFIX)
