"""Shared fixtures: recorded upstream responses and a clean response cache."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from vgi_golf import api

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> Any:
    """Decode one recorded upstream response from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    api.clear_cache()
    yield
    api.clear_cache()
