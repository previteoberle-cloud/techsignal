"""R6 Checkpoint 4: the independent evaluation set and its honesty rules."""
import os

import pytest
from conftest import ROOT

from techsignal import eval_r6 as E
from techsignal import projects as P

REQUIRED = {"multi_assumption", "paraphrase", "negation", "deletion_restructure", "entity_ambiguity",
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


def test_rule_baseline_is_pinned(rep):
    """The rule layer's numbers on this set. A change here is a behaviour change to explain."""
    s = rep["summary"]["rule"]
    assert (s["labelled_pairs"], s["correct"], s["conservative"], s["missed"], s["wrong_direction"], s["model_calls"]) \
        == (18, 9, 7, 0, 2, 0)
    # the two known rule-layer errors, reported rather than tuned away
    assert _row(rep, "E05", "rule")["score"] == "wrong_direction"      # negation far from the signal word
    assert _row(rep, "E09", "rule")["score"] == "wrong_direction"      # short alias matches another company


def test_stub_column_is_labelled_mock_and_gates_hold(rep):
    for r in rep["results"]:
        for row in r["stub"]["rows"].values():
            assert "model_validated" not in row["sources"]        # a stub is never shown as a model
    assert "model_rejected" in _row(rep, "E13", "stub")["sources"]           # nonexistent evidence
    assert "model_failed" in _row(rep, "E14", "stub")["sources"]             # format error
    for case in ("E05", "E06", "E07", "E13", "E14", "E15"):
        assert _row(rep, case, "stub")["score"] != "wrong_direction" or case == "E05"
    assert _row(rep, "E15", "stub")["got"] == "insufficient"                 # injection blocked
    assert _row(rep, "E07", "stub")["got"] == "insufficient"                 # deletion is not a new fact
    assert rep["results"][1]["stub"]["model_calls"] == 0                       # rules decided E02 first


def test_committed_report_is_current(rep):
    assert (ROOT / "docs" / "R6_EVAL.md").read_text(encoding="utf-8") == E.to_markdown(rep)


def test_short_alias_warning():
    w = P.spec_warnings(P.spec_from_form(name="星衡半导体", aliases="XH"))
    assert w and "XH" in w[0]
    assert P.spec_warnings(P.spec_from_form(name="Tenstorrent")) == []
