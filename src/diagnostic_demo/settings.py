"""진단 데모 서비스 설정. 비밀값(`DIAG_API_TOKEN`, `OPENAI_API_KEY`)은 환경변수에서만 읽는다 (AGENTS.md CRITICAL).

상한·단가는 ARCHITECTURE "모델 호출 예산 — 총액 US$30" 표의 초기값이며 코드에 박지 않고 여기서 읽는다.
단가 기본값 0 은 "비용 추정 불가" 를 뜻한다 — 실연동 전 공식 가격 페이지에서 확인해 넣는다.
`OPENAI_API_KEY` 는 진단 워커만 쓰며 API 프로세스에는 없어도 된다.
"""

import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_MODEL_ID = "gpt-4.1-mini-2025-04-14"  # ADR-0003 작업 가정
FAKE_MODEL_ID = "fake-fixture-script"  # DIAG_MODEL=fake 일 때 provenance.model_id — 실제 모델이 돈 것처럼 적지 않는다
MODEL_CHOICES = ("openai", "fake")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    artifact_dir: Path
    api_token: str = field(repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR
    model: str = "openai"  # MODEL_CHOICES. fake 는 테스트·로컬 e2e 전용 대본
    model_id: str = DEFAULT_MODEL_ID
    allowed_workflow_ids: frozenset[str] = frozenset({"daily-report"})
    budget_usd: float = 30.0
    budget_stop_ratio: float = 0.9
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0
    max_calls: int = 15
    max_input_tokens: int = 80_000
    max_output_tokens: int = 8_000
    timeout_seconds: float = 300.0
    global_daily: int = 60

    @property
    def pricing_configured(self) -> bool:
        return self.price_input_per_m > 0 or self.price_output_per_m > 0


def _number(env: Mapping[str, str], key: str, default: float, cast: type) -> float | int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ValueError(f"{key} 는 {cast.__name__} 여야 합니다: {raw!r}") from None


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """`DIAG_API_TOKEN` 이 비어 있으면 ValueError. `DIAG_DEV=1` 이면 무작위 값을 만들고 stderr 에 경고한다."""
    token = env.get("DIAG_API_TOKEN", "")
    if not token:
        if env.get("DIAG_DEV") != "1":
            raise ValueError("환경변수가 비어 있습니다: DIAG_API_TOKEN")
        token = secrets.token_urlsafe(32)
        print("경고: DIAG_DEV=1 — DIAG_API_TOKEN 을 무작위 값으로 생성했습니다 (개발 전용)", file=sys.stderr)
    model = env.get("DIAG_MODEL") or "openai"
    if model not in MODEL_CHOICES:
        raise ValueError(f"DIAG_MODEL 은 {'|'.join(MODEL_CHOICES)} 중 하나여야 합니다: {model!r}")
    workflows = frozenset(
        w.strip() for w in (env.get("DIAG_ALLOWED_WORKFLOWS") or "daily-report").split(",") if w.strip()
    )
    return Settings(
        db_path=Path(env.get("DIAG_DB_PATH") or "data/diag.sqlite"),
        artifact_dir=Path(env.get("DIAG_ARTIFACT_DIR") or "data/diag-artifacts"),
        api_token=token,
        openai_api_key=env.get("OPENAI_API_KEY") or None,
        fixtures_dir=Path(env.get("DIAG_FIXTURES_DIR") or DEFAULT_FIXTURES_DIR),
        model=model,
        model_id=env.get("DIAG_MODEL_ID") or (DEFAULT_MODEL_ID if model == "openai" else FAKE_MODEL_ID),
        allowed_workflow_ids=workflows,
        budget_usd=_number(env, "DIAG_BUDGET_USD", 30.0, float),
        budget_stop_ratio=_number(env, "DIAG_BUDGET_STOP_RATIO", 0.9, float),
        price_input_per_m=_number(env, "DIAG_PRICE_INPUT_PER_M", 0.0, float),
        price_output_per_m=_number(env, "DIAG_PRICE_OUTPUT_PER_M", 0.0, float),
        max_calls=_number(env, "DIAG_MAX_CALLS", 15, int),
        max_input_tokens=_number(env, "DIAG_MAX_INPUT_TOKENS", 80_000, int),
        max_output_tokens=_number(env, "DIAG_MAX_OUTPUT_TOKENS", 8_000, int),
        timeout_seconds=_number(env, "DIAG_TIMEOUT_SECONDS", 300.0, float),
        global_daily=_number(env, "DIAG_GLOBAL_DAILY", 60, int),
    )
