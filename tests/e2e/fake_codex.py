"""`workflow.scripted.codex` 의 shim — 이전 경로 호환 (`scripts/local_stack.py --fake-codex tests/e2e/fake_codex.py`)."""
import sys

from workflow.scripted.codex import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
