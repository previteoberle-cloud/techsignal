"""R2 / P3: judgement history across runs (--track-history). Fictional scenario data, split in batches."""
import hashlib
import json
import shutil

from conftest import DEMO, SCENARIOS
from techsignal.cli import main


def _batch_files(tmp_path, scenario, order_by_time=True):
    """One ChangesReport file per watch of a scenario, oldest first."""
    report = json.loads((SCENARIOS / scenario).read_text(encoding="utf-8"))["report"]
    watches = sorted(report["watches"], key=lambda w: w["to_timestamp"]) if order_by_time else report["watches"]
    files = []
    for i, w in enumerate(watches, 1):
        r = dict(report, watches=[w], total=1, changes_total=w["changes"])
        f = tmp_path / f"{scenario}_batch{i}.json"
        f.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        files.append(f)
    return files


def _run(changes, out, track=True):
    args = ["run", "--changes", str(changes), "--theses", str(DEMO / "theses.json"),
            "--sources", str(DEMO / "sources.json"), "--out", str(out)]
    return main(args + (["--track-history"] if track else []))


def _log(out):
    return json.loads((out / "decision_log.json").read_text(encoding="utf-8"))


def _history(out):
    return [json.loads(x) for x in (out / "judgements.jsonl").read_text(encoding="utf-8").splitlines()]


def test_opposite_evidence_in_a_later_batch_reopens_the_judgement(tmp_path):
    b1, b2 = _batch_files(tmp_path, "S09_conflict.json")  # official "如期推进" (A1+), then media "延期" (A1-)
    out = tmp_path / "out"
    thesis_hash = hashlib.sha256((DEMO / "theses.json").read_bytes()).hexdigest()

    assert _run(b1, out) == 0
    (a1,) = _log(out)["assessments"]
    assert (a1["assumption_id"], a1["direction"]) == ("A1", "supports")
    first_history = (out / "judgements.jsonl").read_text(encoding="utf-8")

    assert _run(b2, out) == 0
    log = _log(out)
    (a2,) = log["assessments"]
    # alone, the B-grade media report would be a decisive "weakens"; against the history it is a conflict
    assert (a2["direction"], a2["tentative_direction"]) == ("insufficient", "weakens")
    assert a1["evidence_ids"][0] in a2["conflicting_evidence_ids"]
    (t,) = log["cross_batch_transitions"]
    assert (t["kind"], t["before_state"], t["before_evidence_ids"]) == ("conflict", "支持", a1["evidence_ids"])

    # history is append-only: batch 1 lines are kept byte-for-byte, batch 2 appended
    text = (out / "judgements.jsonl").read_text(encoding="utf-8")
    assert text.startswith(first_history) and len(_history(out)) == 2
    md = (out / "brief.md").read_text(encoding="utf-8")
    assert "## 跨批次判断变化" in md and "出现跨批次冲突" in md and "研究结论本身未被修改" in md
    assert hashlib.sha256((DEMO / "theses.json").read_bytes()).hexdigest() == thesis_hash
    assert main(["validate", "--out", str(out), "--theses", str(DEMO / "theses.json")]) == 0


def test_same_direction_in_a_later_batch_is_reinforcement(tmp_path):
    b1, b2 = _batch_files(tmp_path, "S08_duplicate.json")  # forum (D, tentative +) then official (A, +) on A3
    out = tmp_path / "out"
    assert _run(b1, out) == 0 and _run(b2, out) == 0
    log = _log(out)
    (a,) = log["assessments"]
    assert a["direction"] == "supports"
    (t,) = log["cross_batch_transitions"]
    assert t["kind"] == "reinforced" and t["before_state"] == "证据不足（初步指向支持）"


def test_cross_batch_runs_are_reproducible(tmp_path):
    b1, b2 = _batch_files(tmp_path, "S09_conflict.json")
    base = tmp_path / "base"
    assert _run(b1, base) == 0
    copies = []
    for name in ("x", "y"):
        d = tmp_path / name
        shutil.copytree(base, d)
        assert _run(b2, d) == 0
        copies.append(d)
    for f in ("brief.md", "brief.json", "decision_log.json", "evidence.jsonl", "judgements.jsonl"):
        assert (copies[0] / f).read_bytes() == (copies[1] / f).read_bytes(), f


def test_failed_batch_leaves_history_untouched(tmp_path):
    b1, _ = _batch_files(tmp_path, "S09_conflict.json")
    out = tmp_path / "out"
    assert _run(b1, out) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert _run(bad, out) == 2
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_history_off_by_default(tmp_path):
    b1, b2 = _batch_files(tmp_path, "S09_conflict.json")
    out = tmp_path / "out"
    assert _run(b1, out, track=False) == 0 and _run(b2, out, track=False) == 0
    assert not (out / "judgements.jsonl").exists()
    (a,) = _log(out)["assessments"]
    assert a["direction"] == "weakens"  # without history the batches are judged independently
    assert "cross_batch_transitions" not in _log(out)
