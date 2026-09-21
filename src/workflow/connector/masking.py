"""비밀값 마스킹과 Codex 프로세스 환경 허용 목록 (ARCHITECTURE "인증·권한·비밀정보 규칙").

- 산출물(JSONL·stderr)·진행 메시지·오류 메시지는 업로드 전에 `mask_secrets` 를 거친다.
- Codex 에는 `codex_env` 가 고른 변수만 전달한다. 연결 토큰·API 키·중앙 설정을 상속하지 않는다.
  예외 하나: `WORKFLOW_SCRIPT_PACE_SECONDS`(대본 에이전트 속도, `workflow.scripted`) 는 비밀이 아니라 통과시킨다.
"""

import re
from collections.abc import Mapping

SECRET_PATTERNS = (
    re.compile(r"wfc_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
)
_REPLACEMENTS = ("wfc_***", "sk-***")

ENV_ALLOWLIST = frozenset({
    "HOME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "CODEX_HOME",
    "WORKFLOW_SCRIPT_PACE_SECONDS",  # 대본 에이전트(PATH 래퍼)의 속도. 다른 WORKFLOW_* 는 여전히 빠진다
})


def mask_secrets(text: str) -> tuple[str, int]:
    """(마스킹된 텍스트, 발견 수)."""
    total = 0
    for pattern, replacement in zip(SECRET_PATTERNS, _REPLACEMENTS, strict=True):
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def codex_env(base: Mapping[str, str]) -> dict[str, str]:
    """허용 목록(`ENV_ALLOWLIST` + `XDG_*`)만 남긴다. `WORKFLOW_*`(대본 속도 제외)·`OPENAI_API_KEY`·`DIAG_*` 등은 빠진다."""
    return {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.startswith("XDG_")}
