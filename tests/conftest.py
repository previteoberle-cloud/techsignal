from pathlib import Path

import pytest
from techsignal.store import SourceRegistry, load_theses

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
SCENARIOS = ROOT / "tests" / "scenarios"


@pytest.fixture(scope="session")
def theses():
    return load_theses(DEMO / "theses.json")


@pytest.fixture(scope="session")
def sources():
    return SourceRegistry.load(DEMO / "sources.json")
