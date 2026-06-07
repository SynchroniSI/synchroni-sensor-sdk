"""Minimal Typer CLI for smoke-testing the v2 sensor SDK.

Examples::

    poetry run python examples/v2_cli.py list-adapters
    poetry run python examples/v2_cli.py scan
    poetry run python examples/v2_cli.py scan --timeout-ms 5000 --adapter-id system:default
    poetry run python examples/v2_cli.py connect
    poetry run python examples/v2_cli.py connect --adapter-id usb:…
    poetry run python examples/v2_cli.py collect -o session.csv
    poetry run python examples/v2_cli.py collect --adapter-id usb:… -o session.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python examples/v2_cli.py …` to import the local `cli` package.
_EXAMPLES_DIR = Path(__file__).resolve().parent
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_DIR))

from cli.app import app  # noqa: E402

if __name__ == "__main__":
    app()
