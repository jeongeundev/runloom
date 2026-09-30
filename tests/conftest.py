"""공용 fixture 자리. 필요한 step 에서 채운다.

`workflow.server.app` 은 import 시 `create_app()` 을 호출해 환경변수를 읽는다 (uvicorn 진입점).
테스트는 `create_app(settings)` 를 직접 쓰므로 모듈 로드 시점의 호출을 건너뛴다.
"""

import os

os.environ.setdefault("WORKFLOW_SKIP_APP", "1")

from urllib.parse import urlsplit  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

_test_client_init = TestClient.__init__


def _test_client_with_origin(self, app, base_url: str = "http://testserver", *args, **kwargs):
    """테스트 클라이언트는 기본 헤더 `Origin: <base_url 의 scheme://host[:port]>` 를 싣는다 — 브라우저 폼 제출과 같게
    (ARCHITECTURE "Origin 검사"). 출처 없는 요청을 보려면 테스트가 `client.headers` 에서 지운다."""
    _test_client_init(self, app, base_url, *args, **kwargs)
    parts = urlsplit(base_url)
    self.headers.setdefault("origin", f"{parts.scheme}://{parts.netloc}")


TestClient.__init__ = _test_client_with_origin
