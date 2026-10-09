"""패키지 뼈대와 AGENTS.md 의존 방향 규칙의 회귀 테스트.

- `workflow.domain` 은 FastAPI·sqlite3·HTTPX·subprocess 를 import 하지 않는다.
- `workflow.server` 와 `workflow.connector` 는 서로 import 하지 않는다.
- 진단 데모 패키지 `diagnostic_demo`·대본 에이전트 `workflow.scripted` 는 공개 데모 전용(종료)이라 여기 없다 (ADR-0019).

소스 텍스트를 정규식으로 검사하므로 빈 패키지에서도 자명하게 통과하며,
이후 step 이 규칙을 어기면 여기서 실패한다.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# `import x`, `import x.y as z, w` 와 `from x.y import ...` 를 잡는다.
_IMPORT_STMT = re.compile(r"^\s*import\s+(.+?)\s*$", re.MULTILINE)
_FROM_STMT = re.compile(r"^\s*from\s+([\w.]+)\s+import\b", re.MULTILINE)


def _imported_modules(package_dir: Path) -> set[str]:
    modules: set[str] = set()
    for path in package_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        modules.update(_FROM_STMT.findall(text))
        for group in _IMPORT_STMT.findall(text):
            for item in group.split(","):
                modules.add(item.strip().split(" as ")[0].strip())
    return modules


def _violations(package_dir: Path, forbidden_prefixes: tuple[str, ...]) -> set[str]:
    return {
        mod
        for mod in _imported_modules(package_dir)
        if any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes)
    }


def test_packages_import():
    import importlib.util

    import workflow

    assert workflow is not None
    assert importlib.util.find_spec("diagnostic_demo") is None  # 진단 데모는 공개 데모 전용(종료) (ADR-0019)
    assert importlib.util.find_spec("workflow.scripted") is None  # 대본 에이전트도 공개 데모 전용(종료) (ADR-0019)


def test_domain_does_not_import_infrastructure():
    forbidden = ("fastapi", "sqlite3", "httpx", "subprocess")
    assert _violations(SRC / "workflow" / "domain", forbidden) == set()


def test_server_and_connector_do_not_import_each_other():
    assert _violations(SRC / "workflow" / "server", ("workflow.connector",)) == set()
    assert _violations(SRC / "workflow" / "connector", ("workflow.server",)) == set()

