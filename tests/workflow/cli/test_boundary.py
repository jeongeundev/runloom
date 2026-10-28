"""CLI import 경계(D5) — `cli` 는 `contracts`·HTTPX·표준 라이브러리만, `server`·`connector` 는 `cli` 를 모른다.

`tests/test_packages.py` 의 정규식 검사와 따로, `ast` 로 함수 안 import 까지 본다.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "workflow"


def _imports(package_dir: Path) -> set[tuple[str, str]]:
    """(파일 이름, 절대 모듈 이름) — 상대 import(`from ..server import x`)는 파일 위치로 풀어 절대 이름으로 바꾼다."""
    found: set[tuple[str, str]] = set()
    for path in package_dir.rglob("*.py"):
        package = ["workflow", *path.parent.relative_to(SRC).parts]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                found.update((path.name, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = package[: len(package) - node.level + 1] if node.level else []
                module = ".".join([*base, *(node.module or "").split(".")]).strip(".")
                found.add((path.name, module))  # `from workflow import server` 꼴도 잡도록 이름까지 붙인 것도 넣는다
                found.update((path.name, f"{module}.{alias.name}") for alias in node.names)
    return found


def _violations(package_dir: Path, forbidden: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(name, mod) for name, mod in _imports(package_dir)
            if any(mod == p or mod.startswith(p + ".") for p in forbidden)}


def test_cli_package_exists():
    assert (SRC / "cli" / "__init__.py").is_file()
    assert {"config.py", "client.py"} <= {p.name for p in (SRC / "cli").glob("*.py")}


def test_cli_imports_only_contracts_among_workflow_packages():
    forbidden = ("workflow.server", "workflow.connector", "workflow.domain", "workflow.adapters")
    assert _violations(SRC / "cli", forbidden) == set()


def test_server_and_connector_do_not_import_cli():
    assert _violations(SRC / "server", ("workflow.cli",)) == set()
    assert _violations(SRC / "connector", ("workflow.cli",)) == set()
