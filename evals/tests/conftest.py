"""Shared fixtures for the eval tests.

The eval tests get a conftest because they are new code with no legacy to
preserve; the older `tests/` files keep their own `sys.path` prologue.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import json  # noqa: E402

import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOLD_DIR = _ROOT / "evals" / "gold"


@pytest.fixture()
def gold_mini() -> dict:
    return json.loads((FIXTURES / "gold_mini.json").read_text())


@pytest.fixture()
def results_mini():
    from papertrace.models import RunResults

    return RunResults.from_json(FIXTURES / "results_mini.json")
