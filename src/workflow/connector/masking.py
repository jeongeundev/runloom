"""비밀값 마스킹과 Codex 프로세스 환경 허용 목록 (ARCHITECTURE "인증·권한·비밀정보 규칙").

- 산출물(JSONL·stderr)·진행 메시지·오류 메시지는 업로드 전에 `mask_secrets` 를 거친다.
- Codex 에는 `codex_env` 가 고른 변수만 전달한다. 연결 토큰·API 키·중앙 설정을 상속하지 않는다.
  예외 하나: `WORKFLOW_SCRIPT_PACE_SECONDS`(대본 에이전트 속도, `workflow.scripted`) 는 비밀이 아니라 통과시킨다.
- 러너 로컬 등록의 `--env`(ADR-0018 결정 3)는 `registered_env` 가 허용 목록·예약 이름을 뺀 뒤 그 위에 더하고, 값(8자 이상)은
  `mask_secrets(text, extra)` 가 `<env:이름>` 으로 가린다.
"""

import re
from collections.abc import Mapping

SECRET_PATTERNS = (
    re.compile(r"wfc_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{8,}"),  # 낱말 안의 `sk-`(`task-…` 의 끝)는 키가 아니다
)
_REPLACEMENTS = ("wfc_***", "sk-***")
ENV_MASK_MIN_LENGTH = 8  # 이보다 짧은 값(`1`·`dev`)은 흔한 낱말이라 가리면 로그 전체가 망가진다

ENV_ALLOWLIST = frozenset({
    "HOME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "CODEX_HOME",
    "WORKFLOW_SCRIPT_PACE_SECONDS",  # 대본 에이전트(PATH 래퍼)의 속도. 다른 WORKFLOW_* 는 여전히 빠진다
})

# `--env` 로 넘길 수 없는 이름 (접두사 `WORKFLOW_` 도). 등록 때(cli) 거부하고 실행 때(`registered_env`) 다시 뺀다.
RESERVED_ENV_NAMES = frozenset({
    "OPERATOR_TOKEN", "DIAG_API_TOKEN", "OPENAI_API_KEY", "SESSION_SECRET", "WORKFLOW_GITHUB_TOKEN", "PATH", "HOME",
})
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def mask_secrets(text: str, extra: Mapping[str, str] | None = None) -> tuple[str, int]:
    """(마스킹된 텍스트, 발견 수). `extra`(등록 env) 값 중 8자 이상은 `<env:이름>` 으로 — 긴 값부터 바꾼다."""
    total = 0
    for pattern, replacement in zip(SECRET_PATTERNS, _REPLACEMENTS, strict=True):
        text, count = pattern.subn(replacement, text)
        total += count
    for name, value in sorted((extra or {}).items(), key=lambda item: -len(item[1])):
        if len(value) >= ENV_MASK_MIN_LENGTH and value in text:
            total += text.count(value)
            text = text.replace(value, f"<env:{name}>")
    return text, total


def codex_env(base: Mapping[str, str]) -> dict[str, str]:
    """허용 목록(`ENV_ALLOWLIST` + `XDG_*`)만 남긴다. `WORKFLOW_*`(대본 속도 제외)·`OPENAI_API_KEY`·`DIAG_*` 등은 빠진다."""
    return {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.startswith("XDG_")}


def registered_env(env: Mapping[str, str]) -> dict[str, str]:
    """러너 로컬 등록의 env 중 도구·검증 환경에 더할 수 있는 것만. 허용 목록 이름(`codex_env` 가 남기는 것)·예약 이름·
    `WORKFLOW_*`·형식 위반은 뺀다 — 등록 env 가 허용 목록 값을 덮지 못한다."""
    return {
        name: value for name, value in env.items()
        if ENV_NAME.match(name) and name not in RESERVED_ENV_NAMES and name not in ENV_ALLOWLIST
        and not name.startswith(("WORKFLOW_", "XDG_"))
    }
