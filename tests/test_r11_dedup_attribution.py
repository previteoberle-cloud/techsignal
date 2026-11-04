"""R1.1 risks 1 and 3: duplicate detection across research subjects, and multi-assumption attribution.

Scenario-level checks live in tests/scenarios/S21-S23; these add cross-run (ledger) coverage and
pin the exact outcome sets.
"""
import json

from conftest import SCENARIOS
from techsignal.pipeline import run_pipeline
from techsignal.store import EvidenceLedger


def _report(name):
    return json.loads((SCENARIOS / name).read_text(encoding="utf-8"))["report"]


def _split(report, keep_uuid):
    r = json.loads(json.dumps(report))
    r["watches"] = [w for w in r["watches"] if w["uuid"] == keep_uuid]
    r["total"] = len(r["watches"])
    return r


XH = "00000000-0000-4000-8000-000000000101"
LX = "00000000-0000-4000-8000-000000000102"


def test_same_text_two_companies_same_run(theses, sources):
    r = run_pipeline(_report("S21_cross_entity_duplicate.json"), theses, sources)
    assert r.filtered == []
    got = {(a.thesis_id, a.assumption_id, a.direction.value) for a in r.assessments}
    assert got == {("TH-001", "A1", "weakens"), ("TH-002", "B1", "weakens")}


def test_same_text_two_companies_across_runs(tmp_path, theses, sources):
    """The ledger from an earlier run must not swallow another company's identical wording."""
    report = _report("S21_cross_entity_duplicate.json")
    ledger_path = tmp_path / "evidence.jsonl"
    run_pipeline(_split(report, XH), theses, sources, ledger=EvidenceLedger(ledger_path))
    r = run_pipeline(_split(report, LX), theses, sources, ledger=EvidenceLedger(ledger_path))
    assert r.filtered == []
    assert [(a.thesis_id, a.direction.value) for a in r.assessments] == [("TH-002", "weakens")]


def test_same_subject_repost_still_deduplicated_across_runs(tmp_path, theses, sources):
    report = _report("S07_duplicate.json")
    # oldest first: the A-grade official post, then the C-grade repost (cdio lists newest first)
    first, second = sorted(report["watches"], key=lambda w: w["to_timestamp"])
    ledger_path = tmp_path / "evidence.jsonl"
    run_pipeline(_split(report, first["uuid"]), theses, sources, ledger=EvidenceLedger(ledger_path))
    r = run_pipeline(_split(report, second["uuid"]), theses, sources, ledger=EvidenceLedger(ledger_path))
    assert [f.reason_code for f in r.filtered] == ["duplicate"]


def test_every_hit_assumption_is_reported(theses, sources):
    r = run_pipeline(_report("S22_multi_assumption.json"), theses, sources)
    got = sorted((a.assumption_id, a.direction.value) for a in r.assessments)
    assert got == [("A1", "supports"), ("A2", "supports")]
    assert len({a.assessment_id for a in r.assessments}) == 2


def test_conflict_on_secondary_assumption(theses, sources):
    r = run_pipeline(_report("S23_secondary_assumption_conflict.json"), theses, sources)
    a2 = [a for a in r.assessments if a.assumption_id == "A2"]
    assert len(a2) == 2
    assert all(a.direction.value == "insufficient" and a.conflicting_evidence_ids for a in a2)
    assert [a.direction.value for a in r.assessments if a.assumption_id == "A1"] == ["supports"]
