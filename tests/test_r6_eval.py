"""R6 Checkpoint 4: the independent evaluation set and its honesty rules."""
import os

import pytest
from conftest import ROOT

from techsignal import eval_r6 as E
from techsignal import projects as P

REQUIRED = {"unconfirmed_source", "multi_assumption", "paraphrase", "negation", "deletion_restructure", "entity_ambiguity",
            "source_conflict", "low_credibility", "unrelated", "model_bad_citation", "model_format_error"}


@pytest.fixture(scope="module")
def rep():
    saved = {k: os.environ.pop(k, None) for k in ("TECHSIGNAL_LLM_BASE_URL", "TECHSIGNAL_LLM_MODEL",
                                                   "TECHSIGNAL_REAL_LLM_AUTHORIZED")}
    try:
        yield E.evaluate()
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})


def _row(rep, case, system, aid="A1"):
    r = next(x for x in rep["results"] if x["id"] == case)
    return r[system]["rows"][aid]


def test_every_required_category_is_covered(rep):
    assert REQUIRED <= set(rep["categories"]) and rep["cases"] >= 10


def test_real_model_is_blocked_not_invented(rep):
    assert rep["summary"]["real_model"]["status"].startswith("BLOCKED")
    assert all(r["real_model"] == {"status": "BLOCKED"} for r in rep["results"])
    md = E.to_markdown(rep)
    assert "BLOCKED" in md and "不代表任何模型的能力" in md


def _tuple(c):
    return (c["labelled_pairs"], c["correct"], c["conservative"], c["missed"], c["wrong_direction"])


R6_IDS = {f"E{i:02d}" for i in range(1, 16)}


def test_before_r61_numbers_are_kept():
    """The R6 code's results (commit 7a5b2ea) on the same case file stay in the repository."""
    before = E.load_before()
    assert "7a5b2ea" in before["produced_by"]
    assert _tuple(E._counts(before["results"], "rule", R6_IDS)) == (18, 9, 7, 0, 2)       # = the R6 report
    assert _tuple(E._counts(before["results"], "rule")) == (25, 12, 7, 0, 6)
    wrong = {r["id"] for r in before["results"] for row in r["rule"]["rows"].values() if row["score"] == "wrong_direction"}
    assert wrong == {"E05", "E09", "E16", "E20", "E21", "E22"}


def test_rule_baseline_is_pinned(rep):
    """The rule layer's numbers after R6.1. A change here is a behaviour change to explain."""
    assert _tuple(E._counts(rep["results"], "rule", R6_IDS)) == (18, 10, 8, 0, 0)
    assert _tuple(E._counts(rep["results"], "rule")) == (25, 15, 10, 0, 0)
    assert rep["summary"]["rule"]["model_calls"] == 0
    # the former errors are now conservative or correct, never a direction
    for case in ("E05", "E16"):
        assert _row(rep, case, "rule")["got"] == "insufficient"
    assert _row(rep, "E09", "rule")["got"] == "unrelated"
    assert _row(rep, "E20", "rule")["got"] == "insufficient" and _row(rep, "E22", "rule")["got"] == "insufficient"
    # clear statements kept their direction
    assert _row(rep, "E17", "rule")["got"] == "weakens" and _row(rep, "E19", "rule")["got"] == "weakens"
    assert _row(rep, "E02", "rule")["got"] == "weakens"


def test_stub_column_is_labelled_mock_and_gates_hold(rep):
    for r in rep["results"]:
        for row in r["stub"]["rows"].values():
            assert "model_validated" not in row["sources"]        # a stub is never shown as a model
    assert "model_rejected" in _row(rep, "E13", "stub")["sources"]           # nonexistent evidence
    assert "model_failed" in _row(rep, "E14", "stub")["sources"]             # format error
    for r in rep["results"]:
        for row in r["stub"]["rows"].values():
            assert row["score"] != "wrong_direction", r["id"]
    assert _row(rep, "E15", "stub")["got"] == "insufficient"                 # injection blocked
    assert _row(rep, "E07", "stub")["got"] == "insufficient"                 # deletion is not a new fact
    assert rep["results"][1]["stub"]["model_calls"] == 0                       # rules decided E02 first


def test_committed_report_is_current(rep):
    assert (ROOT / "docs" / "R6_EVAL.md").read_text(encoding="utf-8") == E.to_markdown(rep, E.load_before())


def test_short_alias_warning():
    w = P.spec_warnings(P.spec_from_form(name="星衡半导体", aliases="XH"))
    assert w and "XH" in w[0]
    assert P.spec_warnings(P.spec_from_form(name="Tenstorrent")) == []
