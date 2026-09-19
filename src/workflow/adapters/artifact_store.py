"""내용 주소 산출물 저장소. 같은 바이트는 한 번만 저장하고, 소유 범위는 DB 의 artifacts 행이 정한다."""

import hashlib
import os
import tempfile
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _ref(self, sha256: str) -> str:
        return f"{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def write(self, data: bytes) -> tuple[str, str]:
        """(sha256, store_ref). 임시 파일에 쓰고 fsync 한 뒤 최종 경로로 rename 한다."""
        sha256 = hashlib.sha256(data).hexdigest()
        ref = self._ref(sha256)
        final = self.root / ref
        if final.exists():
            return sha256, ref
        final.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=final.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, final)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha256, ref

    def read(self, store_ref: str) -> bytes:
        return (self.root / store_ref).read_bytes()

    def exists(self, sha256: str) -> bool:
        return (self.root / self._ref(sha256)).exists()
