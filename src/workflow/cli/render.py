"""텍스트 렌더 순수 함수 (ARCHITECTURE "CLI — phase 26" render.py·"업무 상세 텍스트"·"리포트 텍스트").

입력은 API 응답 dict, 출력은 끝 줄바꿈 없는 문자열. 모델이 쓴 글(`text`·`why`·`note`·`diagram`·`reason`)은 그대로 내고
이스케이프·실행하지 않는다. 절 제목·상태 문구는 웹(`web._REPORT_SECTIONS`·`report.html`)과 같은 글자다.
"""

_SECTIONS = (
    ("changes", "바뀐 것"), ("decisions", "결정과 근거"), ("open_questions", "정하지 않고 남긴 것"),
    ("to_confirm", "받는 사람이 결정·확인할 것"), ("constraints", "지킬 제약"), ("remaining_work", "남은 일"),
)
_ACTIVE = ("queued", "leased")


def _or_none(value) -> str:
    return "없음" if value is None or value == "" else str(value)


def work_line(item: dict) -> str:
    assignee = item["assignee"]["name"] if item["assignee"] else "없음"
    return f"{item['key']} · {item['priority']} · {item['status']} · {item['title']} · 담당 {assignee}"


def work_list(body: dict) -> str:
    return "\n".join(work_line(item) for item in body["items"]) or "업무가 없습니다."


def work_detail(detail: dict) -> str:
    work = detail["work"]
    assignee = work["assignee"]
    status = f"{work['status']} — {work['status_reason']}" if work["status_reason"] else work["status"]
    source = work["source"]
    lines = [
        f"# {work['key']} {work['title']}",
        f"- 종류: {work['kind_label']} ({work['kind']})",
        f"- 우선순위: {work['priority']}",
        f"- 담당: {f'{assignee['name']} ({assignee['type']})' if assignee else '없음'}",
        f"- 상태: {status}",
        f"- 다음 할 일: {_or_none(work['next_action'])}",
        f"- 원본: {source['url'] or source['key'] or '없음'}",
    ]
    if detail["direct"]:
        lines.append(f"- 직접 작업: {detail['direct']['member_name']} · 브랜치 {detail['direct']['branch']}")
    lines += [f"- 할 수 있는 행동: {', '.join(detail['actions']) or '없음'}", "", "## 요청",
              _or_none(work["request"]), "", "## 열린 사람 요청"]
    for request in detail["open_requests"]:
        lines += [f"- {request['request_id']} · {request['code']} · {request['question']}",
                  f"  행동: {', '.join(request['actions'])}"]
    if not detail["open_requests"]:
        lines.append("없음")
    return "\n".join(lines)


def report_status(view: dict) -> str:
    if view["status"] in _ACTIVE:
        return "작성 중"
    if view["status"] == "done":
        return "완성"
    return f"리포트 없음({view['reason']})"


def _answer(decisions: list[dict], item_id: str) -> str:
    decision = next((d for d in decisions if d["item_id"] == item_id), None)
    if decision is None:
        return "아직 없음"
    by = f"{decision['answered_by']} · {decision['answered_at']}"
    if decision["answer"] == "confirmed":
        return f"확인함 · {by}"
    return f"다르게 정함 — {decision['note']} · {by}"


def _claim(view: dict, claim: dict, *, answer: bool) -> list[str]:
    checked = ((view.get("evidence_check") or {}).get("claims") or {}).get(claim["id"]) or {}
    evidence = ", ".join(claim["evidence"]) or "없음"
    if checked.get("verified") is not True:
        evidence += " · 근거 확인 안 됨"
    lines = [f"- [{claim['id']}] {claim['text']}",
             f"  왜: {claim['why'] if claim['why'] is not None else '기록 없음'}",
             f"  근거: {evidence}"]
    if answer:
        lines.append(f"  답: {_answer(view['decisions'], claim['id'])}")
    return lines


def report(view: dict, *, old_base: bool = False) -> str:
    original = view["job_type"] == "original"
    if original:
        head = f"## 인계 리포트 · {view['stage']['kind_label']} · 시도 {view['source']['attempt_no']} · {view['report_id']}"
    else:
        agent = view["agent"]["name"] if view["agent"] else "에이전트 없음"
        head = f"## 맞춤 풀이 · {view['requested_by'] or '이름 없음'} · {agent} · {view['report_id']}"
    if old_base:
        head += " · 옛 리포트 기준"
    lines = [head]
    if original:
        parties = view["parties"]
        lines.append(f"보낸 사람 {parties['sender'] or '없음'} · 받는 사람 {parties['recipient'] or '없음(관리자)'}")
        if view["pr_evidence_error"]:
            lines.append(view["pr_evidence_error"])
    else:
        lines.append(f"원본 리포트: {view['base_report_id']}")
    if view["status"] in _ACTIVE:
        return "\n".join([*lines, "", "리포트 작성 중"])
    if view["status"] != "done":
        return "\n".join([*lines, "", f"리포트 없음({view['reason']})"])
    body = view["report"]
    for name, title in _SECTIONS:
        claims = body["sections"][name]
        lines += ["", f"### {title}"]
        for claim in claims:
            lines += _claim(view, claim, answer=original and name == "to_confirm")
        if not claims:
            lines.append("없음")
    if body.get("diagram"):
        lines += ["", "### 다이어그램", body["diagram"]]
    return "\n".join(lines)


_render_report = report  # `show` 의 인자 `report`(보기 묶음)가 이 이름을 가린다


def _other_lines(reports: dict, opened: dict) -> list[str]:
    lines = []
    for stage in reports["stages"]:
        views = [(stage["latest"], False)] if stage["latest"] else []
        views += [(view, True) for view in stage["previous"]]
        for view, previous in views:
            if view["report_id"] == opened["report_id"]:
                continue
            line = f"- {stage['kind_label']} · {view['report_id']} · {report_status(view)}"
            lines.append(line + " · 이전" if previous else line)
    return lines


def show(detail: dict, reports: dict, report: dict | None, tailored: dict | None) -> str:
    if report is None:
        return f"{work_detail(detail)}\n\n## 인계 리포트\n인계 리포트 없음"
    parts = [work_detail(detail), _render_report(report)]
    if tailored is not None:
        old_base = bool(tailored["stale"]) or tailored["base_report_id"] != report["report_id"]
        parts.append(_render_report(tailored, old_base=old_base))
    others = _other_lines(reports, report)
    if others:
        parts.append("\n".join(["## 다른 리포트", *others]))
    return "\n\n".join(parts)
