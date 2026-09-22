"""callback 대상 허용 목록 — `WORKFLOW_CALLBACK_HOSTS` 의 판정 (ADR-0010). 표준 urllib.parse 만 쓴다.

허용 목록은 명시 비교다 (ADR-0004 의 태도) — DNS 조회·IP 대역 검사를 하지 않는다. 환경변수 읽기는
`server/` 가 하고, 여기는 파싱된 문자열만 받는다.
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def parse_hosts(raw: str) -> tuple[str, ...]:
    """콤마 구분 문자열 → 항목 튜플. 공백 제거, 빈 항목 제거, 소문자화. 빈 문자열 → 빈 튜플."""
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def host_allowed(url: str, allowed: Sequence[str]) -> bool:
    """`url` 의 스킴이 http/https 이고 host(소문자)가 `allowed` 의 어느 항목과 맞으면 참.

    항목이 `host` 면 그 호스트의 모든 포트, `host:port` 면 포트까지 같아야 한다(스킴 기본 포트 80/443 을 채워 비교).
    `allowed` 가 비어 있으면 항상 거짓. userinfo(`user@host`)·빈 host·파싱 실패 → 거짓.
    """
    if not allowed:
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in _DEFAULT_PORTS or not host or parts.username is not None:
        return False
    if port is None:
        port = _DEFAULT_PORTS[parts.scheme]
    host = host.lower()
    return any(entry in (host, f"{host}:{port}") for entry in allowed)
