"""R2: the real public case, replayed OFFLINE from the report a live changedetection.io returned.

cases/tenstorrent_blackhole/output/source_changes_report.json was captured from the live API by
`python -m techsignal.live` (see run_record.json / observation_record.json). Replaying it here is a
regression check of the pipeline on real data; it is not itself an online test (that is
tests/integration/test_live_cdio.py).
"""
import hashlib
import json

from conftest import ROOT
from techsignal.models import EvidenceGrade
from techsignal.pipeline import run_pipeline
from techsignal.store import SourceRegistry, load_theses

CASE = ROOT / "cases" / "tenstorrent_blackhole"
OUT = CASE / "output"


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def test_captured_report_matches_run_record():
    report = json.loads((OUT / "source_changes_report.json").read_text(encoding="utf-8"))
    record = json.loads((OUT / "run_record.json").read_text(encoding="utf-8"))
    assert record["input_mode"] == "live_api"
    assert hashlib.sha256(_canonical(report).encode()).hexdigest() == record["report_sha256"]
    assert not any("key" in k.lower() or "token" in k.lower() for k in record)  # no credentials recorded


def test_observations_are_commit_pinned_public_versions():
    obs = json.loads((OUT / "observation_record.json").read_text(encoding="utf-8"))
    case = json.loads((CASE / "case.json").read_text(encoding="utf-8"))
    assert [o["commit"] for o in obs["observations"]] == [v["commit"] for v in case["versions"]]
    for o in obs["observations"]:
        assert o["url"].startswith("https://raw.githubusercontent.com/tenstorrent/tt-metal/")
        assert o["commit"] in o["url"]
    report = json.loads((OUT / "source_changes_report.json").read_text(encoding="utf-8"))
    assert report["watches"][0]["url"] == case["versions"][-1]["url"]


def test_replay_reproduces_live_outcome():
    report = json.loads((OUT / "source_changes_report.json").read_text(encoding="utf-8"))
    theses = load_theses(CASE / "theses.json")
    r = run_pipeline(report, theses, SourceRegistry.load(CASE / "sources.json"))
    assert [(a.thesis_id, a.assumption_id, a.direction.value, a.basis.value) for a in r.assessments] == \
        [("TH-TT-001", "BH1", "weakens", "rule")]
    ev = next(iter(r.evidence.values()))
    assert ev.grade == EvidenceGrade.A and ev.source_type.value == "official"
    assert any("70B (TP=4)" in x and "QuietBox (Blackhole)" in x for x in ev.excerpt_added)
    assert r.markdown == (OUT / "brief.md").read_text(encoding="utf-8")
    assert theses[0].fictional is False and theses[0].status == "active"


def test_thesis_sources_are_quoted_verbatim_in_brief():
    md = (OUT / "brief.md").read_text(encoding="utf-8")
    assert "结论依据 SRC-TT-1" in md and "结论依据 SRC-TT-2" in md
    assert "Blackhole software optimization is under active development." in md


def test_path_prefix_source_matching():
    reg = SourceRegistry.load(CASE / "sources.json")
    assert reg.profile("raw.githubusercontent.com",
                       "https://raw.githubusercontent.com/tenstorrent/tt-metal/x/README.md").grade == EvidenceGrade.A
    other = reg.profile("raw.githubusercontent.com", "https://raw.githubusercontent.com/someone/else/x/README.md")
    assert other.grade == EvidenceGrade.D and other.source_type.value == "unknown"


# --- P3 on real data: two batches replayed offline from what the live API returned ---

CB = CASE / "output_cross_batch"


def test_real_cross_batch_replay(tmp_path):
    from techsignal.cli import main
    out = tmp_path / "state"
    for n in (1, 2):
        src = CB / f"batch{n}" / "source_changes_report.json"
        assert main(["run", "--changes", str(src), "--theses", str(CASE / "theses.json"),
                     "--sources", str(CASE / "sources.json"), "--out", str(out), "--track-history"]) == 0
        if n == 1:
            (a,) = json.loads((out / "decision_log.json").read_text(encoding="utf-8"))["assessments"]
            assert (a["assumption_id"], a["direction"]) == ("BH1", "weakens")
    log = json.loads((out / "decision_log.json").read_text(encoding="utf-8"))
    (a,) = log["assessments"]
    assert (a["assumption_id"], a["direction"], a["basis"]) == ("BH1", "insufficient", "rule")
    (t,) = log["cross_batch_transitions"]
    assert (t["kind"], t["before_state"], t["after_state"]) == ("unresolved", "削弱", "证据不足")
    for f in ("brief.md", "decision_log.json", "judgements.jsonl", "evidence.jsonl"):
        assert (out / f).read_text(encoding="utf-8") == (CB / "batch2" / f).read_text(encoding="utf-8"), f
    assert json.loads((CASE / "theses.json").read_text(encoding="utf-8"))["theses"][0]["status"] == "active"


def test_real_cross_batch_versions_are_the_recorded_commits():
    obs = json.loads((CB / "observation_record.json").read_text(encoding="utf-8"))
    assert [b["to_version"][:7] for b in obs["batches"]] == ["168a956", "62f3cd0"]
    removed = json.loads((CB / "batch2" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert any("70B (TP=4)" in x and "QuietBox (Blackhole)" in x for x in removed["excerpt_removed"])
