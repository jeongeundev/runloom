"""`python3 -m workflow.connector` 진입점."""

import sys

from workflow.connector.cli import main

if __name__ == "__main__":
    sys.exit(main())
