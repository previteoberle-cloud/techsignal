"""R4: workbench data layer (no Streamlit needed). Real Tenstorrent case outputs are the default data."""
import hashlib
import json
import re
import shutil

import pytest
from conftest import ROOT
from techsignal.workbench import labels as L
from techsignal.workbench import viewmodel as vm
from techsignal.workbench.replay import fetch_live, replay_case

CASES = ROOT / "cases"
_ENUMS = {"supports", "weakens", "unrelated", "insufficient", "mock_model", "live_cdio_api", "replay_of_captured_live",
          "verification_tool", "cdio_change", "signal_removed_or_negated", "related_evidence_no_direction",
          "plan_completed", "cross_batch_unresolved", "github_file_search", "cdio_snapshot_search", "github_commit_info"}


def _cases():
    cases, problems = vm.discover_cases(CASES)
    assert problems == []
    return {c.case_id: c for c in cases}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def test_default_cases_are_the_real_tenstorrent_case():
    cases = _cases()
    assert set(cases) == {"tenstorrent-blackhole-70b", "tenstorrent-blackhole-70b-cross-batch"}
    for c in cases.values():
        for run in vm.load_batches(c):
            assert run.provenance["input_origin"] == "live_cdio_api"
            assert run.provenance["data_nature"] == "public"
            assert all(t.fictional is False for t in run.theses)


def test_overview_and_states():
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    b1, b2 = vm.load_batches(c)
    o1, o2 = b1.overview(), b2.overview()
    assert [j["state"] for j in o1["judgements"]] == ["削弱原判断"]
    assert [j["state"] for j in o2["judgements"]] == ["证据不足，待核实"]
    assert o1["changes"] == 1 and o2["pending"]
    assert "研究结论本身未被系统修改" in o2["headline"]


def test_timeline_separates_publication_and_fetch_time():
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    t1, t2 = vm.timeline(c, vm.load_batches(c))
    assert t1["to_published"].startswith("2025-07-11") and t2["to_published"].startswith("2025-08-12")
    assert t1["to_fetched"].startswith("2026-") and t2["to_fetched"].startswith("2026-")
    assert [x["kind"] for x in t2["transitions"]] == ["无法定向，保留原判断待核实"]
    assert any(ch["key"] and "Blackhole" in ch["text"] for ch in t1["changes"])


def test_each_batch_points_at_its_own_version():
    """R4 fix: batch evidence must name the version that produced the change (was the last version)."""
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    for b, run in zip(c.batches, vm.load_batches(c), strict=True):
        own = [e for e in run.evidence.values() if e.obtained_via == "cdio_change" and e.observed_at ==
               max(x.observed_at for x in run.evidence.values() if x.obtained_via == "cdio_change")]
        assert all(b.version_to["commit"] in e.source_url for e in own), b.label


def test_evidence_view_has_both_times_and_links():
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    b1 = vm.load_batches(c)[0]
    eid = b1.assessments()[0]["evidence_ids"][0]
    v = b1.evidence_view(eid)
    assert v["published"].startswith("2025-07-11") and "168a956a09" in v["published"]
    assert "changedetection.io 抓取" in v["fetched"] and v["url"].startswith("https://raw.githubusercontent.com/tenstorrent/")
    assert v["grade"].startswith("A 级") and v["used_by"]
    assert b1.evidence_view("EV-does-not-exist") is None


def test_verification_view():
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    b2 = vm.load_batches(c)[1]
    (v,) = b2.verification()
    assert v["outcome"].startswith("找到相关官方内容") and not v["changed_judgement"]
    assert [s["status"] for s in v["steps"]] == ["成功", "失败", "成功"]
    assert v["evidence"] and all(e in b2.evidence for e in v["evidence"])
    assert vm.load_batches(c)[0].verification() == []


def test_no_internal_values_leak_into_view_models():
    c = _cases()["tenstorrent-blackhole-70b-cross-batch"]
    runs = vm.load_batches(c)
    payload = [vm.timeline(c, runs)]
    for r in runs:
        payload += [r.overview(), r.verification(), r.transitions(), r.provenance_lines(),
                    [r.evidence_view(e) for e in r.evidence]]
    leaked = {s for s in _strings(payload) for e in _ENUMS if re.search(rf"(?<![\w/]){e}(?![\w/])", s)}
    assert leaked == set()


def test_every_internal_value_has_a_label():
    from techsignal.models import Basis, Direction, EvidenceGrade, SourceType
    assert all(d.value in L.DIRECTION for d in Direction)
    assert all(g.value in L.GRADE for g in EvidenceGrade)
    assert all(t.value in L.SOURCE_TYPE for t in SourceType)
    assert all(b.value in L.BASIS for b in Basis)
    assert L.zh(L.DIRECTION, "something_new") == "未知"


# ---------------------------------------------------------------- errors ---------------------------

def test_missing_and_broken_inputs_give_chinese_errors(tmp_path):
    with pytest.raises(vm.LoadError, match="不存在"):
        vm.load_run(tmp_path / "nope", CASES / "tenstorrent_blackhole" / "theses.json")
    (tmp_path / "r").mkdir()
    with pytest.raises(vm.LoadError, match="缺少研究速览数据"):
        vm.load_run(tmp_path / "r", CASES / "tenstorrent_blackhole" / "theses.json")
    (tmp_path / "r" / "brief.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(vm.LoadError, match="无法读取"):
        vm.load_run(tmp_path / "r", CASES / "tenstorrent_blackhole" / "theses.json")
    (tmp_path / "r" / "brief.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
    with pytest.raises(vm.LoadError, match="格式不符"):
        vm.load_run(tmp_path / "r", CASES / "tenstorrent_blackhole" / "theses.json")


def test_discover_reports_bad_cases(tmp_path):
    good = tmp_path / "good"
    shutil.copytree(CASES / "tenstorrent_blackhole", good)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "case.json").write_text(json.dumps({"case_id": "x"}), encoding="utf-8")
    cases, problems = vm.discover_cases(tmp_path)
    assert len(cases) == 2 and len(problems) == 1 and "缺少字段" in problems[0]
    assert vm.discover_cases(tmp_path / "missing") == ([], [f"案例目录不存在：{tmp_path / 'missing'}"])


def test_empty_case_dir(tmp_path):
    assert vm.discover_cases(tmp_path) == ([], [])


# ---------------------------------------------------------------- replay ---------------------------

def _tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.mark.parametrize("case_id", ["tenstorrent-blackhole-70b", "tenstorrent-blackhole-70b-cross-batch"])
def test_replay_runs_real_pipeline_into_temp_dir(tmp_path, case_id):
    c = _cases()[case_id]
    before = _tree_hash(CASES)
    res = replay_case(c, workdir=tmp_path / "w")
    assert _tree_hash(CASES) == before                         # committed case data untouched
    assert all(d.is_relative_to(tmp_path) for d in res.run_dirs)
    replayed, saved = vm.load_batches(c, res.run_dirs), vm.load_batches(c)
    for r, s in zip(replayed, saved, strict=True):
        assert r.provenance["input_origin"] == "replay_of_captured_live"
        assert [a["direction"] for a in r.assessments()] == [a["direction"] for a in s.assessments()]
    assert "引用完整性校验：通过" in res.log


def test_replay_without_saved_data_fails_cleanly(tmp_path):
    c = _cases()["tenstorrent-blackhole-70b"]
    broken = vm.CaseRef(**{**c.__dict__, "batches": [vm.BatchRef(1, "x", tmp_path / "missing", None, None)]})
    with pytest.raises(vm.LoadError, match="缺少已保存的在线数据"):
        replay_case(broken, workdir=tmp_path / "w")


def test_live_connection_failure_is_reported_offline(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = _cases()["tenstorrent-blackhole-70b"]
    with pytest.raises(vm.LoadError, match="无法连接"):
        fetch_live(c, f"http://127.0.0.1:{port}", "k", 0, workdir=tmp_path)
    with pytest.raises(vm.LoadError, match="http"):
        fetch_live(c, "ftp://x", "k", 0, workdir=tmp_path)
