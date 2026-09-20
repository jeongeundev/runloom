"""대본(scripted) 에이전트 — 공개 데모 전용. 실제 Codex·Claude·OpenAI 를 돌리지 않는다.

2026-09-21 사용자 확정: 공개 데모(runloom.duckdns.org)는 심사위원 외에 투표하는 불특정 방문자도 쓰므로 **실제 에이전트를
돌리지 않는다** — OpenAI 진단 호출도, 운영자 Mac 의 Codex·Claude 도 마찬가지. 대신 "실제 에이전트라면 이렇게 돌아갈 것"
이라는 대본 에이전트가 같은 계약·같은 검증기·같은 worktree·같은 pytest 를 거쳐 결과를 낸다. 모델 호출만 없다.
화면에는 "시연용 · 대본 재생" 을 표시한다. 실제 어댑터(`connector/codex.py`·`connector/claude.py`)는 셀프호스트
실사용을 위해 그대로 남는다 — 배포·로컬 스택은 이 패키지를 `codex`/`claude` 이름의 PATH 래퍼로 앞에 둘 뿐이다.

    python3 -m workflow.scripted.codex  exec --json -C <worktree> … --output-last-message <file> -
    python3 -m workflow.scripted.claude -p --output-format json … --json-schema <schema>

시연 전용 패키지이므로 `domain`·`server`·`connector` 를 import 하지 않는다. 속도는 `WORKFLOW_SCRIPT_PACE_SECONDS`
(`_common.pace_seconds`), 진단 쪽 대본 속도는 진단 서비스의 `DIAG_FAKE_TURN_SECONDS` 다.
"""

# 대본임을 나타내는 식별자. 결과·로그에 실제 모델(gpt-…, claude-…)이 돈 것처럼 적지 않는다 (AGENTS.md — 가상 실행을 실제로 표시하지 않는다).
SCRIPT_MODEL_ID = "scripted-demo-agent"
