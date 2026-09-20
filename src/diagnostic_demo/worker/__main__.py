"""진단 워커 진입점: `python3 -m diagnostic_demo.worker`. 3초 간격으로 `accepted` 실행을 하나씩 처리한다.

- `DIAG_MODEL=openai`(기본): `OPENAI_API_KEY` 가 없으면 시작하지 않고 stderr 에 이유를 적고 exit 2.
  ADR-0003 확정 조건(키·예산 확인) 전에는 유료 호출을 하지 않는다.
- `DIAG_MODEL=fake`: fixture 기반 대본(`worker/fake_script.py`). 테스트·로컬 e2e 전용이며 실제 진단이 아니다.
"""

import sys
import time

from diagnostic_demo.db import connect, init_schema
from diagnostic_demo.settings import Settings, load_settings
from diagnostic_demo.worker.runner import process_one

POLL_SECONDS = 3.0


def model_factory(settings: Settings):
    if settings.model == "fake":
        from diagnostic_demo.worker.fake_script import fixture_script
        from diagnostic_demo.worker.model import FakeModelClient

        return lambda: FakeModelClient(fixture_script())

    import openai

    from diagnostic_demo.worker.model import OpenAIModelClient

    # 키는 SDK 객체에만 넘긴다. 설정·로그·이벤트·산출물에 넣지 않는다
    return lambda: OpenAIModelClient(openai.OpenAI(api_key=settings.openai_api_key), settings.model_id)


def main() -> int:
    settings = load_settings()
    if settings.model == "openai" and not settings.openai_api_key:
        print(
            "진단 워커를 시작하지 않습니다: DIAG_MODEL=openai 인데 OPENAI_API_KEY 가 없습니다. "
            "키·예산 확인(ADR-0003) 후 설정하거나, 로컬 e2e 는 DIAG_MODEL=fake 로 실행하세요.",
            file=sys.stderr,
        )
        return 2
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    init_schema(conn)
    factory = model_factory(settings)
    print(f"진단 워커 시작: model={settings.model} model_id={settings.model_id} db={settings.db_path}", file=sys.stderr)
    while True:
        if not process_one(conn, settings, factory):
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
