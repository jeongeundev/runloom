"""사람용 CLI `runloom` — `/api/v1` 을 멤버 토큰(`wfm_…`)으로 부르는 얇은 클라이언트 (ADR-0034).

`workflow.contracts`·HTTPX·표준 라이브러리만 import 한다 — server·connector·domain·adapters 는 모른다.
"""
