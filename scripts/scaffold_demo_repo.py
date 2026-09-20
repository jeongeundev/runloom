#!/usr/bin/env python3
"""B 가 수정할 데모 저장소(`demo-report-repo`)를 이 트리 **밖**에 생성한다.

    python3 scripts/scaffold_demo_repo.py [PATH] [--force]

기본 PATH 는 이 저장소의 부모/demo-report-repo. 생성물은 수정 전 기준(`items` 만 지원)이며 커밋 1개와
태그 `report-base` 를 가진다. 마지막 줄에 `base_commit={sha}` 를 출력한다 — 연결 프로그램 `register` 와
업무 등록의 `base_commit` 에 쓴다.

파일 내용은 아래 문자열 상수다. 템플릿 디렉터리를 두면 이 저장소의 pytest·tdd-guard 가 잡으므로 두지 않는다.
가상 데모 자료이며 실제 서비스·기업의 코드가 아니다.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

COMMIT_MESSAGE = "chore: report-base (수정 전 기준)"
TAG = "report-base"
GIT_USER = ("demo-report", "demo-report@example.invalid")

README = """# demo-report-repo

가상 데모 저장소. 일일 보고서 자동화의 변환부(`report_transformer` 역할)다. 실제 서비스가 아니다.

외부 업무 집계 응답(JSON)을 받아 전날의 팀별 완료·미완료 건수 보고서를 만든다.

## 실행

```bash
python3 -m pytest -q                        # 테스트
python3 -m daily_report response.json       # 보고서를 stdout 에 출력
```

종료 코드: 0 성공, 1 변환 실패(stderr 에 `ERROR stage=transform … code=…` 한 줄), 2 파일·JSON 오류.

## 구조

- `daily_report/transformer.py` — 응답 → `Report` 변환. 입력 계약은 `docs/contract.md`
- `daily_report/report.py` — `Report` → 보고서 텍스트
- `daily_report/__main__.py` — CLI 진입점
- `tests/` — pytest
"""

AGENTS_MD = """# demo-report-repo 규칙

가상 데모 저장소다. 실제 서비스가 아니다.

- 테스트를 먼저 쓴다 (TDD). 재현 테스트가 실패하는 것을 확인한 뒤 최소 수정으로 통과시킨다.
- 테스트 실행: `python3 -m pytest -q`
- 변환 계약은 `docs/contract.md` 를 따른다. 보고서 형식(`daily_report/report.py`)은 바꾸지 않는다.
- 커밋하지 말 것. 커밋은 연결 프로그램이 한다.
- 이 저장소 밖의 파일을 수정하지 않는다.
"""

CONTRACT_MD = """# 입력 계약 — 일일 보고서 변환부

가상 데모 자료. `daily_report.transformer.transform(response)` 가 따라야 할 계약이다.

## 응답 형식

응답은 `report_date`(문자열) 와 팀별 행 목록을 가진다. 행은 `team`(문자열), `completed`(0 이상 정수),
`pending`(0 이상 정수) 이다. 행의 순서는 유지한다.

행 목록은 다음 두 경로 중 **정확히 하나** 에 있어야 한다.

| 경로 | 비고 |
|---|---|
| `$.items` | 구형 형식. 과거 저장 응답 재처리를 위해 계속 지원한다 |
| `$.data.records` | 신형 형식. 2026-09-20 00:00+09:00 부터 제공자가 사용한다 |

## 판정 규칙

| 입력 | 결과 |
|---|---|
| 한 경로에만 배열이 있음 | 정상. 행 순서를 유지해 변환 |
| 명시적 빈 배열 `[]` | 정상, 0건. 팀 행 없이 합계 0·0 |
| 두 경로 모두 없음 | 오류 `MISSING_RECORDS_FIELD` |
| 경로의 값이 배열이 아님 | 오류 `MISSING_RECORDS_FIELD` |
| 두 경로가 동시에 있음 | 오류 (모호한 응답). 어느 쪽도 선택하지 않는다 |
| `report_date` 누락 | 오류 `MISSING_REPORT_DATE` |
| 행 필드 누락·타입 오류·음수 | 오류 `INVALID_ROW` |

오류를 빈 목록으로 바꿔 성공시키지 않는다. 보고서 형식과 합계 계산은 바꾸지 않는다.

## 현재 코드 상태

**수정 전 코드는 이 계약을 아직 구현하지 않았다.** `transformer.py` 는 `$.items` 만 읽으므로
신형 응답에서 `MISSING_RECORDS_FIELD` 로 실패한다.
"""

PYPROJECT = """[project]
name = "daily-report-demo"
version = "0.0.0"
description = "가상 데모: 일일 보고서 변환부"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""

GITIGNORE = """__pycache__/
.pytest_cache/
*.txt
"""

INIT = '"""일일 보고서 변환부 — 가상 데모."""\n'

TRANSFORMER = '''"""응답 변환부 — 수정 전 기준. 최상위 items 만 읽는다."""

from dataclasses import dataclass


class TransformError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Row:
    team: str
    completed: int
    pending: int


@dataclass(frozen=True)
class Report:
    report_date: str
    rows: tuple[Row, ...]


def _row(item) -> Row:
    if not isinstance(item, dict) or not isinstance(item.get("team"), str):
        raise TransformError("INVALID_ROW", f"item={item!r}")
    completed, pending = item.get("completed"), item.get("pending")
    for value in (completed, pending):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TransformError("INVALID_ROW", f"item={item!r}")
    return Row(item["team"], completed, pending)


def transform(response: dict) -> Report:
    if not isinstance(response.get("report_date"), str):
        raise TransformError("MISSING_REPORT_DATE")
    items = response.get("items")
    if not isinstance(items, list):
        raise TransformError(
            "MISSING_RECORDS_FIELD", f"expected_path=$.items observed_root_keys={sorted(response)}"
        )
    return Report(response["report_date"], tuple(_row(item) for item in items))
'''

REPORT = '''"""보고서 렌더링. 열 정렬은 공백이며 판정은 값 기준이다."""

from daily_report.transformer import Report


def render(report: Report) -> str:
    lines = [f"일일 업무 보고서 — {report.report_date}", "", "팀      완료  미완료"]
    total_completed = total_pending = 0
    for row in report.rows:
        lines.append(f"{row.team:<4}  {row.completed:<4}  {row.pending}")
        total_completed += row.completed
        total_pending += row.pending
    lines.append(f"{'합계':<4}  {total_completed:<4}  {total_pending}")
    return "\\n".join(lines) + "\\n"
'''

MAIN = '''"""python3 -m daily_report <response.json> — 보고서를 stdout 에 출력한다."""

import json
import sys

from daily_report.report import render
from daily_report.transformer import TransformError, transform


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 -m daily_report <response.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            response = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"ERROR stage=fetch {exc}", file=sys.stderr)
        return 2
    try:
        report = transform(response)
    except TransformError as exc:
        print(
            f"ERROR stage=transform component=report_transformer code={exc.code} {exc.detail}".rstrip(),
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

TEST_TRANSFORMER = '''import pytest

from daily_report.transformer import Report, Row, TransformError, transform


def test_items_format_is_transformed_in_order():
    report = transform({
        "report_date": "2026-09-18",
        "items": [
            {"team": "운영", "completed": 12, "pending": 3},
            {"team": "개발", "completed": 8, "pending": 2},
        ],
    })
    assert report == Report("2026-09-18", (Row("운영", 12, 3), Row("개발", 8, 2)))


def test_empty_items_is_zero_rows():
    assert transform({"report_date": "2026-09-18", "items": []}).rows == ()


@pytest.mark.parametrize("item", [
    {"completed": 1, "pending": 0},
    {"team": "운영", "completed": "1", "pending": 0},
    {"team": "운영", "completed": -1, "pending": 0},
    {"team": "운영", "completed": 1},
    "운영",
])
def test_invalid_row_is_rejected(item):
    with pytest.raises(TransformError) as info:
        transform({"report_date": "2026-09-18", "items": [item]})
    assert info.value.code == "INVALID_ROW"


def test_missing_report_date_is_rejected():
    with pytest.raises(TransformError) as info:
        transform({"items": []})
    assert info.value.code == "MISSING_REPORT_DATE"
'''

TEST_REPORT = '''from daily_report.report import render
from daily_report.transformer import Report, Row


def test_render_matches_report_format():
    report = Report("2026-09-18", (Row("운영", 12, 3), Row("개발", 8, 2)))
    assert render(report) == (
        "일일 업무 보고서 — 2026-09-18\\n"
        "\\n"
        "팀      완료  미완료\\n"
        "운영    12    3\\n"
        "개발    8     2\\n"
        "합계    20    5\\n"
    )


def test_totals_are_computed_from_rows():
    report = Report("2026-09-18", (Row("운영", 13, 3), Row("개발", 8, 2)))
    assert render(report).splitlines()[-1] == "합계    21    5"


def test_no_rows_renders_zero_totals():
    assert render(Report("2026-09-18", ())).splitlines()[-1] == "합계    0     0"
'''

FILES: dict[str, str] = {
    "README.md": README,
    "AGENTS.md": AGENTS_MD,
    "docs/contract.md": CONTRACT_MD,
    "pyproject.toml": PYPROJECT,
    ".gitignore": GITIGNORE,
    "daily_report/__init__.py": INIT,
    "daily_report/transformer.py": TRANSFORMER,
    "daily_report/report.py": REPORT,
    "daily_report/__main__.py": MAIN,
    "tests/__init__.py": "",
    "tests/test_transformer.py": TEST_TRANSFORMER,
    "tests/test_report.py": TEST_REPORT,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def scaffold(path: Path, *, force: bool = False) -> str:
    """`path` 에 수정 전 데모 저장소를 만들고 HEAD SHA 를 돌려준다. 이미 있으면 FileExistsError (force 면 다시 만든다)."""
    path = Path(path)
    if path.exists():
        if not force:
            raise FileExistsError(f"{path} 이미 있습니다. --force 로 다시 만들 수 있습니다.")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    for relative, content in FILES.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", GIT_USER[0])
    _git(path, "config", "user.email", GIT_USER[1])
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", COMMIT_MESSAGE)
    _git(path, "tag", TAG)
    return _git(path, "rev-parse", "HEAD")


def default_path() -> Path:
    return Path(__file__).resolve().parents[1].parent / "demo-report-repo"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B 가 수정할 데모 저장소를 이 트리 밖에 생성한다.")
    parser.add_argument("path", nargs="?", type=Path, default=default_path())
    parser.add_argument("--force", action="store_true", help="이미 있으면 지우고 다시 만든다")
    args = parser.parse_args(argv)
    try:
        sha = scaffold(args.path, force=args.force)
    except FileExistsError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"created {args.path}")
    print(f"base_commit={sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
