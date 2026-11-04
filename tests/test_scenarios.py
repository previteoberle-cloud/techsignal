"""All fictional scenarios, checked against their own expectations (same checker as `techsignal eval`)."""
import json

import pytest
from conftest import SCENARIOS
from techsignal.evaluation import check_scenario, run_scenario, run_scenarios

FILES = sorted(SCENARIOS.glob("*.json"))


def test_at_least_twelve_independent_fictional_scenarios():
    assert len(FILES) >= 12
    ids = set()
    for f in FILES:
        sc = json.loads(f.read_text(encoding="utf-8"))
        assert sc["fictional"] is True and "虚构" in sc["note"], f.name
        ids.add(sc["id"])
    assert len(ids) == len(FILES)


def test_required_categories_are_covered():
    cats = {json.loads(f.read_text(encoding="utf-8"))["category"] for f in FILES}
    required = {"supports", "weakens", "unrelated", "noise", "duplicate", "conflict", "insufficient",
                "bad_citation", "bad_input"}
    assert required <= cats, required - cats


@pytest.mark.parametrize("path", FILES, ids=[f.stem for f in FILES])
def test_scenario(path, theses, sources):
    sc = json.loads(path.read_text(encoding="utf-8"))
    result, error, _ = run_scenario(sc, theses, sources)
    failures, _ = check_scenario(sc, result, error)
    assert not failures, "\n".join(failures)


def test_eval_summary_matches_tests():
    report = run_scenarios(SCENARIOS)
    s = report["summary"]
    assert s["scenarios_failed"] == 0
    assert s["direction_accuracy"] == 1.0 and s["filter_accuracy"] == 1.0
