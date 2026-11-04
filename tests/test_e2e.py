"""End-to-end replay of the demo through the CLI, compared with the committed output."""
import hashlib
import json

from conftest import DEMO
from techsignal.cli import main

OUT_FILES = ["brief.md", "brief.json", "decision_log.json", "evidence.jsonl"]


def _run(out):
    return main(["run", "--changes", str(DEMO / "changes_report.json"), "--theses", str(DEMO / "theses.json"),
                 "--sources", str(DEMO / "sources.json"), "--provider", "mock",
                 "--replay", str(DEMO / "mock_replay.json"), "--out", str(out)])


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_demo_replay_matches_committed_output(tmp_path):
    thesis_hash = _sha(DEMO / "theses.json")
    assert _run(tmp_path) == 0
    for name in OUT_FILES:
        assert (tmp_path / name).read_text(encoding="utf-8") == (DEMO / "output" / name).read_text(encoding="utf-8"), name
    # the pipeline never writes the thesis store
    assert _sha(DEMO / "theses.json") == thesis_hash
    assert main(["validate", "--out", str(tmp_path), "--theses", str(DEMO / "theses.json")]) == 0


def test_demo_run_is_reproducible(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _run(a) == 0 and _run(b) == 0
    for name in OUT_FILES:
        assert _sha(a / name) == _sha(b / name)


def test_demo_outcomes(tmp_path):
    assert _run(tmp_path) == 0
    log = json.loads((tmp_path / "decision_log.json").read_text(encoding="utf-8"))
    dirs = sorted(a["direction"] for a in log["assessments"])
    assert dirs.count("weakens") == 2 and dirs.count("supports") == 1
    assert {f["reason_code"] for f in log["filtered"]} == {"noise_only", "duplicate", "no_net_difference"}
    assert all(a["thesis_status_change"] == "none" for a in log["assessments"])
    mock = [a for a in log["assessments"] if a["basis"] == "mock_model"]
    assert len(mock) == 1 and mock[0]["provider_name"] == "mock-replay"
    md = (tmp_path / "brief.md").read_text(encoding="utf-8")
    assert "Mock 回放，非真实模型推理" in md and "系统不会因新信息自动修改历史结论" in md


def test_rerun_with_existing_ledger_does_not_mark_own_events_duplicate(tmp_path):
    assert _run(tmp_path) == 0
    before = (tmp_path / "evidence.jsonl").read_text(encoding="utf-8")
    assert main(["run", "--changes", str(DEMO / "changes_report.json"), "--theses", str(DEMO / "theses.json"),
                 "--sources", str(DEMO / "sources.json"), "--provider", "mock",
                 "--replay", str(DEMO / "mock_replay.json"), "--out", str(tmp_path), "--append-ledger"]) == 0
    assert (tmp_path / "evidence.jsonl").read_text(encoding="utf-8") == before


def test_bad_input_exits_non_zero(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = main(["run", "--changes", str(bad), "--theses", str(DEMO / "theses.json"),
               "--sources", str(DEMO / "sources.json"), "--out", str(tmp_path / "o")])
    assert rc == 2 and not (tmp_path / "o" / "brief.md").exists()
