"""R1.1 risk 2: a failed run must not modify or delete the previous successful outputs or ledger.

Each test first produces a good output directory, then injects one failure and checks that every
output file is byte-for-byte what it was before.
"""
import json
import pathlib

import pytest
from conftest import DEMO, SCENARIOS
from techsignal.cli import main
from techsignal.pipeline import run_pipeline
from techsignal.providers import MockReplayProvider
from techsignal.store import EvidenceLedger
from techsignal.validate import CitationError, Issue

OUT_FILES = ["brief.md", "brief.json", "decision_log.json", "evidence.jsonl"]


def _args(changes, out, replay=DEMO / "mock_replay.json", append=False):
    a = ["run", "--changes", str(changes), "--theses", str(DEMO / "theses.json"),
         "--sources", str(DEMO / "sources.json"), "--provider", "mock", "--replay", str(replay), "--out", str(out)]
    return a + (["--append-ledger"] if append else [])


def _snapshot(out):
    return {n: (out / n).read_bytes() for n in OUT_FILES}


def _good_run(out):
    assert main(_args(DEMO / "changes_report.json", out)) == 0
    return _snapshot(out)


def _scenario_input(tmp_path, scenario_file):
    sc = json.loads((SCENARIOS / scenario_file).read_text(encoding="utf-8"))
    changes = tmp_path / f"{sc['id']}_changes.json"
    changes.write_text(json.dumps(sc["report"], ensure_ascii=False), encoding="utf-8")
    replay = tmp_path / f"{sc['id']}_replay.json"
    replay.write_text(json.dumps({"entries": sc.get("mock_replay", {})}, ensure_ascii=False), encoding="utf-8")
    return changes, replay


def _no_leftovers(out):
    extra = sorted(p.name for p in out.iterdir() if p.name not in OUT_FILES)
    assert extra == [], f"leftover files: {extra}"


@pytest.mark.parametrize("append", [False, True], ids=["fresh-ledger", "append-ledger"])
def test_bad_input_keeps_previous_outputs(tmp_path, append):
    out = tmp_path / "out"
    before = _good_run(out)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(_args(bad, out, append=append)) != 0
    assert _snapshot(out) == before
    _no_leftovers(out)


def test_citation_failure_keeps_previous_outputs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    before = _good_run(out)
    changes, replay = _scenario_input(tmp_path, "S02_weakens.json")  # new evidence that would be appended

    def broken(*a, **k):
        raise CitationError([Issue("INJECTED", "test", "注入的校验失败")])
    monkeypatch.setattr("techsignal.pipeline.assert_valid", broken)
    assert main(_args(changes, out, replay=replay, append=True)) != 0
    assert _snapshot(out) == before
    _no_leftovers(out)


def test_unexpected_provider_exception_keeps_previous_outputs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    before = _good_run(out)
    changes, replay = _scenario_input(tmp_path, "S12_mock_model.json")

    def boom(self, request):
        raise RuntimeError("injected provider crash")
    monkeypatch.setattr(MockReplayProvider, "judge", boom)
    # Fresh-ledger mode: with --append-ledger this content would already be a duplicate of the demo
    # ledger and the provider would never be called.
    assert main(_args(changes, out, replay=replay)) != 0
    assert _snapshot(out) == before
    _no_leftovers(out)


def test_write_failure_midway_keeps_previous_outputs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    before = _good_run(out)
    changes, replay = _scenario_input(tmp_path, "S02_weakens.json")
    real_write = pathlib.Path.write_text

    def flaky(self, *a, **k):
        if self.name == "decision_log.json":
            raise OSError("injected disk error")
        return real_write(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "write_text", flaky)
    assert main(_args(changes, out, replay=replay, append=True)) != 0
    monkeypatch.undo()
    assert _snapshot(out) == before
    _no_leftovers(out)


def test_success_fail_success_sequence(tmp_path):
    out = tmp_path / "out"
    first = _good_run(out)
    bad = tmp_path / "bad.json"
    bad.write_text('{"from": 1}', encoding="utf-8")
    assert main(_args(bad, out)) != 0
    assert _snapshot(out) == first
    assert _good_run(out) == first
    for name in OUT_FILES:
        assert (out / name).read_bytes() == (DEMO / "output" / name).read_bytes()
    _no_leftovers(out)


def test_library_ledger_untouched_when_validation_fails(tmp_path, theses, sources, monkeypatch):
    path = tmp_path / "evidence.jsonl"
    run_pipeline((DEMO / "changes_report.json").read_text(encoding="utf-8"), theses, sources,
                 provider=MockReplayProvider.load(DEMO / "mock_replay.json"), ledger=EvidenceLedger(path))
    before = path.read_bytes()
    sc = json.loads((SCENARIOS / "S02_weakens.json").read_text(encoding="utf-8"))

    def broken(*a, **k):
        raise CitationError([Issue("INJECTED", "test", "注入的校验失败")])
    monkeypatch.setattr("techsignal.pipeline.assert_valid", broken)
    with pytest.raises(CitationError):
        run_pipeline(sc["report"], theses, sources, ledger=EvidenceLedger(path))
    assert path.read_bytes() == before
