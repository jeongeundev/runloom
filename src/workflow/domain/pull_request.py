"""초안 PR 본문·브랜치 이름·사람 요청 문구 — ADR-0018 결정 4, ARCHITECTURE "초안 PR (step 6)".

순수 함수만. 브랜치 이름은 서버가 발급한 `task_id`·업무 키로 계산한다(러너 `git_ops` 와 같은 `contracts/v1.result_branch`) —
외부 입력에서 브랜치 이름을 받지 않는다.
"""

from workflow.contracts.v1 import result_branch

# PR 본문에 넣는 검토 요약의 최대 글자 수
REVIEW_SUMMARY_LIMIT = 2000
# PR 을 열지 못했을 때의 사람 요청 코드·원인 키 접두사. 이 요청에 답해도 수정을 다시 돌리지 않는다(`Worker._resume`)
PR_REQUEST_CODE = "pr_unavailable"
PR_REQUEST_PREFIX = "pr:"

PR_PENDING_REASON = "검토 승인 — PR 여는 중"
PR_FAILED_REASON = "검토 승인 — PR 을 열지 못함, 병합·이슈 종료는 사람"
PR_MERGED_REASON = "PR 병합"
PR_CLOSED_REASON = "PR 이 병합 없이 닫힘"
PERMISSION_NEEDED = "GitHub App 권한(Pull requests 쓰기·Contents 읽기) 승인 필요"


def refs_unreadable(summary: str) -> bool:
    """PR 생성 422 요약이 App 이 브랜치를 읽지 못한 경우인가 — Contents 읽기 없는 App(2026-09-29 실연동 1 실제 문구)."""
    return "not all refs are readable" in summary.lower()


def head_branch(task_id: str, *, work_key: str | None = None, branch_seq: int = 1) -> str:
    return result_branch(task_id, work_key, branch_seq)


def pr_title(work_key: str | None, title: str) -> str:
    """`<업무 키> <업무 제목>` — 키가 없으면(v10 이전 Task) 제목만."""
    return f"{work_key} {title}" if work_key else title


def open_reason(number: int) -> str:
    return f"사람 차례 · PR 확인 — #{number}"


def pr_request_cause_key(review_execution_id: str) -> str:
    return f"{PR_REQUEST_PREFIX}{review_execution_id}"


def _manual_hint(branch: str) -> str:
    return f"러너 폴더에서 `git push origin {branch}` 뒤 PR 을 직접 여세요"


def not_pushed_question(branch: str) -> str:
    return f"검토 승인 — 결과 브랜치 {branch} 가 GitHub 에 push 되지 않음. {_manual_hint(branch)}"


def failed_question(branch: str, cause: str) -> str:
    return f"검토 승인 — PR 을 열지 못함({cause}). {_manual_hint(branch)}"


def pr_body(
    *, issue_number: int, task_id: str, review_summary: str, task_url: str | None, work_key: str | None = None,
) -> str:
    """첫 줄 `Fixes #N`(기본 브랜치로 병합되면 GitHub 가 이슈를 닫는다), 검토 요약, 업무 키(있으면), 업무 링크(공개
    주소가 없으면 ID), 끝에 marker."""
    lines = [f"Fixes #{issue_number}", ""]
    summary = review_summary.strip()
    if len(summary) > REVIEW_SUMMARY_LIMIT:
        summary = summary[: REVIEW_SUMMARY_LIMIT - 1] + "…"
    if summary:
        lines += ["검토 요약:", summary, ""]
    if work_key:
        lines.append(f"업무 키: {work_key}")
    lines += [f"Runloom 업무: {task_url or task_id}", "", f"<!-- runloom:task={task_id} -->"]
    return "\n".join(lines)
