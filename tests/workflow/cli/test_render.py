"""텍스트 렌더 순수 함수 (ARCHITECTURE "CLI — phase 26" 의 render.py·"업무 상세 텍스트"·"리포트 텍스트") — phase 26 step 4."""

from workflow.cli import render


def item(**overrides) -> dict:
    base = {"key": "RUN-12", "title": "로그인 만료 처리", "kind": "bug_fix", "kind_label": "버그 수정",
            "priority": "high", "assignee": {"type": "member", "id": "mem-1", "name": "김개발", "active": True},
            "status": "내 차례", "status_reason": "검토 결과를 확인해 주세요", "my_turn": True,
            "next_action": "검토 결과 확인", "triage": None, "next_step": None, "repository": "acme/api",
            "source": {"type": "github", "key": "acme/api#12", "url": "https://github.com/acme/api/issues/12"},
            "updated_at": "2026-10-22T09:00:00Z", "closed_at": None}
    return {**base, **overrides}


def detail(**overrides) -> dict:
    work = {**item(), "request": "토큰이 만료되면 갱신을 무한 재시도한다.\n재현: 30분 대기 후 새로고침"}
    base = {"work": work, "stages": [], "links": [],
            "direct": {"member_name": "김개발", "started_at": "2026-10-22T09:00:00Z", "branch": "run-12-login-expiry"},
            "open_requests": [{"request_id": "hrq-1a2b3c4d5e6f", "code": "input_missing",
                               "question": "어느 환경에서 재현되나요?", "state": "open", "revision": 1,
                               "created_at": "2026-10-22T09:00:00Z", "answered_at": None,
                               "actions": ["resume", "close"], "asks_information": True, "agent_choices": []}],
            "responses": [], "triage": None, "next_step": None, "pull_requests": [], "internal_requests": [],
            "events": [], "assignee_candidates": [], "actions": ["assign", "set_priority", "start_direct"]}
    return {**base, **overrides}


def claim(cid: str, text: str, why: str | None, evidence: list[str]) -> dict:
    return {"id": cid, "text": text, "why": why, "evidence": evidence}


def original(**overrides) -> dict:
    sections = {
        "changes": [claim("c1", "로그인 만료 시 토큰 갱신을 한 번만 시도하도록 바꿨다", "갱신 실패가 무한 재시도로 이어졌다는 이슈 본문",
                          ["commit:3f2a9c1", "source:work"])],
        "decisions": [claim("d1", "재시도 횟수는 설정값이 아니라 상수 1 로 둔다", None, ["artifact:art-7c8d9e0f1a2b"])],
        "open_questions": [claim("q1", "갱신 실패 시 사용자에게 보일 문구는 정하지 않았다", None, [])],
        "to_confirm": [claim("t1", "로그아웃 대신 로그인 화면으로 보내는 동작이 맞는지 확인", "PR 리뷰 코멘트에서 의견이 갈렸다", ["pr:42"]),
                       claim("t2", "만료 시간 확인", None, ["source:work"])],
        "constraints": [claim("k1", "토큰 저장 형식은 바꾸지 않는다", None, ["verification"])],
        "remaining_work": [],
    }
    verified = {"c1": True, "d1": False, "q1": False, "t1": True, "t2": True, "k1": True}
    base = {"report_id": "hrp-0a1b2c3d4e5f", "job_type": "original", "status": "done", "reason": None,
            "stage": {"title": "로그인 만료 수정", "kind": "bug_fix", "kind_label": "버그 수정"},
            "source": {"attempt_no": 1, "outcome": "ready_for_review"}, "agent": {"name": "Codex"},
            "requested_by": None, "requested_by_member_id": None, "base_report_id": None, "stale": False,
            "created_at": "2026-10-22T09:00:00Z", "finished_at": "2026-10-22T09:05:00Z",
            "report": {"contract_version": 1, "report_id": "hrp-0a1b2c3d4e5f", "job_type": "original",
                       "sections": sections, "diagram": "flowchart LR\n  A[요청] --> B{토큰 만료?}"},
            "evidence_check": {"claims": {k: {"verified": v, "unverified": []} for k, v in verified.items()}},
            "pull_requests": [], "pr_evidence_error": None,
            "decisions": [{"item_id": "t1", "answer": "changed", "note": "로그인 화면이 아니라 홈으로 보낸다",
                           "answered_by": "박검토", "answered_at": "2026-10-22T09:30:00.000000Z"}],
            "parties": {"sender": "김개발", "recipient": "박검토"}, "actions": []}
    return {**base, **overrides}


def tailored(**overrides) -> dict:
    sections = {"changes": [claim("x1", "받는 사람 관점 요약", None, ["claim:c1"])], "decisions": [],
                "open_questions": [], "to_confirm": [claim("x2", "확인할 것", None, [])], "constraints": [],
                "remaining_work": []}
    base = {**original(), "report_id": "hrp-111111111111", "job_type": "tailored", "requested_by": "박검토",
            "requested_by_member_id": "mem-2", "base_report_id": "hrp-0a1b2c3d4e5f", "agent": {"name": "리뷰 Codex"},
            "report": {"contract_version": 1, "report_id": "hrp-111111111111", "job_type": "tailored",
                       "sections": sections, "diagram": None},
            "evidence_check": {"claims": {"x1": {"verified": True, "unverified": []},
                                          "x2": {"verified": True, "unverified": []}}},
            "decisions": [], "parties": {"sender": None, "recipient": None}}
    return {**base, **overrides}


# --- 목록 -----------------------------------------------------------------------------------------------


def test_work_line_has_key_priority_status_title_and_assignee():
    line = render.work_line(item())
    for part in ("RUN-12", "high", "내 차례", "로그인 만료 처리", "김개발"):
        assert part in line
    assert "\n" not in line
    assert "없음" in render.work_line(item(assignee=None))


def test_work_list_is_one_line_per_item_or_the_empty_notice():
    body = {"items": [item(), item(key="RUN-3", title="둘째")], "counts": {}}
    assert render.work_list(body).splitlines() == [render.work_line(body["items"][0]),
                                                   render.work_line(body["items"][1])]
    assert render.work_list({"items": [], "counts": {}}) == "업무가 없습니다."


# --- 업무 상세 ------------------------------------------------------------------------------------------


def test_work_detail_matches_the_architecture_example():
    assert render.work_detail(detail()) == "\n".join([
        "# RUN-12 로그인 만료 처리",
        "- 종류: 버그 수정 (bug_fix)",
        "- 우선순위: high",
        "- 담당: 김개발 (member)",
        "- 상태: 내 차례 — 검토 결과를 확인해 주세요",
        "- 다음 할 일: 검토 결과 확인",
        "- 원본: https://github.com/acme/api/issues/12",
        "- 직접 작업: 김개발 · 브랜치 run-12-login-expiry",
        "- 할 수 있는 행동: assign, set_priority, start_direct",
        "",
        "## 요청",
        "토큰이 만료되면 갱신을 무한 재시도한다.",
        "재현: 30분 대기 후 새로고침",
        "",
        "## 열린 사람 요청",
        "- hrq-1a2b3c4d5e6f · input_missing · 어느 환경에서 재현되나요?",
        "  행동: resume, close",
    ])


def test_work_detail_empty_values():
    work = item(assignee=None, status_reason="", next_action=None, request="",
                source={"type": "manual", "key": None, "url": None})
    text = render.work_detail(detail(work=work, direct=None, open_requests=[], actions=[]))
    lines = text.splitlines()
    assert "- 담당: 없음" in lines and "- 상태: 내 차례" in lines and "- 다음 할 일: 없음" in lines
    assert "- 원본: 없음" in lines and "- 할 수 있는 행동: 없음" in lines
    assert not any(line.startswith("- 직접 작업") for line in lines)
    assert "## 요청\n없음\n\n## 열린 사람 요청" in text
    assert text.endswith("## 열린 사람 요청\n없음")
    source_key = item(source={"type": "github", "key": "acme/api#12", "url": None}, request="본문")
    assert "- 원본: acme/api#12" in render.work_detail(detail(work=source_key)).splitlines()


# --- 리포트 ---------------------------------------------------------------------------------------------


def test_original_report_matches_the_architecture_example():
    text = render.report(original())
    assert text.startswith("\n".join([
        "## 인계 리포트 · 버그 수정 · 시도 1 · hrp-0a1b2c3d4e5f",
        "보낸 사람 김개발 · 받는 사람 박검토",
        "",
        "### 바뀐 것",
        "- [c1] 로그인 만료 시 토큰 갱신을 한 번만 시도하도록 바꿨다",
        "  왜: 갱신 실패가 무한 재시도로 이어졌다는 이슈 본문",
        "  근거: commit:3f2a9c1, source:work",
        "",
        "### 결정과 근거",
        "- [d1] 재시도 횟수는 설정값이 아니라 상수 1 로 둔다",
        "  왜: 기록 없음",
        "  근거: artifact:art-7c8d9e0f1a2b · 근거 확인 안 됨",
        "",
        "### 정하지 않고 남긴 것",
        "- [q1] 갱신 실패 시 사용자에게 보일 문구는 정하지 않았다",
        "  왜: 기록 없음",
        "  근거: 없음 · 근거 확인 안 됨",
        "",
        "### 받는 사람이 결정·확인할 것",
        "- [t1] 로그아웃 대신 로그인 화면으로 보내는 동작이 맞는지 확인",
        "  왜: PR 리뷰 코멘트에서 의견이 갈렸다",
        "  근거: pr:42",
        "  답: 다르게 정함 — 로그인 화면이 아니라 홈으로 보낸다 · 박검토 · 2026-10-22T09:30:00.000000Z",
        "- [t2] 만료 시간 확인",
        "  왜: 기록 없음",
        "  근거: source:work",
        "  답: 아직 없음",
        "",
        "### 지킬 제약",
        "- [k1] 토큰 저장 형식은 바꾸지 않는다",
        "  왜: 기록 없음",
        "  근거: verification",
        "",
        "### 남은 일",
        "없음",
        "",
        "### 다이어그램",
        "flowchart LR",
        "  A[요청] --> B{토큰 만료?}",
    ]))


def test_confirmed_answer_and_missing_evidence_check_entry():
    view = original(decisions=[{"item_id": "t2", "answer": "confirmed", "note": None, "answered_by": "박검토",
                                "answered_at": "2026-10-22T10:00:00Z"}],
                    evidence_check={"claims": {}}, parties={"sender": None, "recipient": None})
    text = render.report(view)
    assert "  답: 확인함 · 박검토 · 2026-10-22T10:00:00Z" in text.splitlines()
    assert "보낸 사람 없음 · 받는 사람 없음(관리자)" in text.splitlines()
    assert "  근거: commit:3f2a9c1, source:work · 근거 확인 안 됨" in text.splitlines()  # 확인 기록이 없으면 확인 안 됨


def test_pr_evidence_error_is_shown_under_the_parties():
    lines = render.report(original(pr_evidence_error="PR 근거를 가져오지 못했습니다")).splitlines()
    assert lines[2] == "PR 근거를 가져오지 못했습니다"


def test_tailored_report_header_base_line_old_base_and_no_answers():
    text = render.report(tailored())
    lines = text.splitlines()
    assert lines[0] == "## 맞춤 풀이 · 박검토 · 리뷰 Codex · hrp-111111111111"
    assert lines[1] == "원본 리포트: hrp-0a1b2c3d4e5f"
    assert "답:" not in text and "다이어그램" not in text
    old = render.report(tailored(), old_base=True).splitlines()[0]
    assert old == "## 맞춤 풀이 · 박검토 · 리뷰 Codex · hrp-111111111111 · 옛 리포트 기준"
    nameless = render.report(tailored(requested_by=None, agent=None)).splitlines()[0]
    assert nameless == "## 맞춤 풀이 · 이름 없음 · 에이전트 없음 · hrp-111111111111"


def test_report_states_replace_the_body():
    for status in ("queued", "leased"):
        text = render.report(original(status=status, report=None, evidence_check=None, decisions=[]))
        assert text.splitlines()[-1] == "리포트 작성 중" and "###" not in text
        assert render.report_status(original(status=status)) == "작성 중"
    failed = original(status="failed", reason="러너 오류", report=None, evidence_check=None)
    assert render.report(failed).splitlines()[-1] == "리포트 없음(러너 오류)"
    assert render.report_status(failed) == "리포트 없음(러너 오류)"
    assert render.report_status(original()) == "완성"


def test_model_text_is_printed_as_is():
    tricky = "$(rm -rf /) `id` <b>굵게</b> **별표** \\n"
    sections = {**original()["report"]["sections"], "changes": [claim("c1", tricky, tricky, ["source:work"])]}
    view = original(report={**original()["report"], "sections": sections, "diagram": tricky})
    text = render.report(view)
    assert f"- [c1] {tricky}" in text and f"  왜: {tricky}" in text and text.endswith(tricky)


# --- show -----------------------------------------------------------------------------------------------


def stages_with(latest: dict, previous: list[dict] | None = None) -> dict:
    return {"stages": [{"title": "로그인 만료 수정", "kind": "bug_fix", "kind_label": "버그 수정",
                        "latest": latest, "previous": previous or []}], "tailored": []}


def test_show_without_reports():
    text = render.show(detail(), {"stages": [], "tailored": []}, None, None)
    assert text == render.work_detail(detail()) + "\n\n## 인계 리포트\n인계 리포트 없음"


def test_show_with_original_tailored_and_other_reports():
    previous = original(report_id="hrp-000000000001", status="failed", reason="시간 초과")
    review = original(report_id="hrp-000000000002", status="queued",
                      stage={"title": "검토", "kind": "code_review", "kind_label": "코드 검토"})
    reports = stages_with(original(), [previous])
    reports["stages"].append({"title": "검토", "kind": "code_review", "kind_label": "코드 검토",
                              "latest": review, "previous": []})
    text = render.show(detail(), reports, original(), tailored())
    assert text == "\n\n".join([
        render.work_detail(detail()),
        render.report(original()),
        render.report(tailored()),
        "## 다른 리포트\n- 버그 수정 · hrp-000000000001 · 리포트 없음(시간 초과) · 이전\n- 코드 검토 · hrp-000000000002 · 작성 중",
    ])


def test_show_marks_the_tailored_old_base_when_stale_or_on_another_original():
    reports = stages_with(original())
    stale = render.show(detail(), reports, original(), tailored(stale=True))
    assert "· 옛 리포트 기준" in stale
    other_base = render.show(detail(), reports, original(), tailored(base_report_id="hrp-999999999999"))
    assert "· 옛 리포트 기준" in other_base
    assert "옛 리포트 기준" not in render.show(detail(), reports, original(), tailored())


def test_rendered_text_has_no_token():
    text = render.show(detail(), stages_with(original()), original(), tailored())
    assert "wfm_" not in text and "Bearer" not in text
