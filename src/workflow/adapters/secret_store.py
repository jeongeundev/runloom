"""비밀 파일 저장소 (ADR-0017, ARCHITECTURE "비밀 파일").

루트 = `WORKFLOW_SECRET_DIR`(기본 `data/secrets`). 디렉터리 0700·파일 0600, 임시 파일 → fsync → rename 으로 원자 교체.
이름은 아래 고정 목록만 받는다 — 외부 입력으로 경로를 만들지 않는다. 값은 repr·예외 문구에 넣지 않는다.
백업 CLI 는 이 디렉터리를 담지 않는다.
"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

GITHUB_APP_INFO = "github_app.json"
GITHUB_APP_PRIVATE_KEY = "github_app_private_key.pem"
GITHUB_APP_CLIENT_SECRET = "github_app_client_secret"
GITHUB_APP_WEBHOOK_SECRET = "github_app_webhook_secret"
GITHUB_TOKEN = "github_token"

NAMES = frozenset({
    GITHUB_APP_INFO,
    GITHUB_APP_PRIVATE_KEY,
    GITHUB_APP_CLIENT_SECRET,
    GITHUB_APP_WEBHOOK_SECRET,
    GITHUB_TOKEN,
})


class SecretStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "SecretStore":
        return cls(Path(env.get("WORKFLOW_SECRET_DIR") or "data/secrets"))

    def __repr__(self) -> str:
        return f"SecretStore(root={str(self.root)!r})"

    def _path(self, name: str) -> Path:
        if name not in NAMES:
            raise ValueError(f"허용되지 않은 비밀 파일 이름입니다: {name!r}")
        return self.root / name

    def read(self, name: str) -> str | None:
        try:
            return self._path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, name: str, value: str) -> None:
        path = self._path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        # mkstemp 는 0600 으로 만든다
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()
