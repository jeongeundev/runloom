"""공용 fixture 자리. 필요한 step 에서 채운다.

`workflow.server.app`·`diagnostic_demo.api.app` 은 import 시 `create_app()` 을 호출해 환경변수를 읽는다 (uvicorn 진입점).
테스트는 `create_app(settings)` 를 직접 쓰므로 모듈 로드 시점의 호출을 건너뛴다.
"""

import os

os.environ.setdefault("WORKFLOW_SKIP_APP", "1")
os.environ.setdefault("DIAG_SKIP_APP", "1")
