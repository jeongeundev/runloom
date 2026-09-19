"""패키지 뼈대와 AGENTS.md 의존 방향 규칙의 회귀 테스트.

- `workflow.domain` 은 FastAPI·sqlite3·HTTPX·subprocess 를 import 하지 않는다.
- `workflow.server` 와 `workflow.connector` 는 서로 import 하지 않는다.
- `diagnostic_demo` 는 `workflow.adapters`·`workflow.server` 를 import 하지 않는다.

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
    import diagnostic_demo
    import workflow

    assert workflow is not None
    assert diagnostic_demo is not None


def test_domain_does_not_import_infrastructure():
    forbidden = ("fastapi", "sqlite3", "httpx", "subprocess")
    assert _violations(SRC / "workflow" / "domain", forbidden) == set()


def test_server_and_connector_do_not_import_each_other():
    assert _violations(SRC / "workflow" / "server", ("workflow.connector",)) == set()
    assert _violations(SRC / "workflow" / "connector", ("workflow.server",)) == set()


def test_diagnostic_demo_shares_only_contracts():
    forbidden = ("workflow.adapters", "workflow.server")
    assert _violations(SRC / "diagnostic_demo", forbidden) == set()
