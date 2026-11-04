"""R3 / P0: the brief's statements about data origin come from explicit run metadata."""
import json

import pytest
from conftest import DEMO, ROOT
from techsignal.cli import main

from techsignal import provenance as prov

CASE = ROOT / "cases" / "tenstorrent_blackhole"


def _md(out):
    return (out / "brief.md").read_text(encoding="utf-8")


def test_no_stale_r1_wording_anywhere():
    for f in list((ROOT / "cases").rglob("brief.*")) + list((ROOT / "demo").rglob("brief.*")):
        text = f.read_text(encoding="utf-8")
        assert "R1 未接入在线服务" not in text and "R1 研究原型" not in text, f


def test_fictional_fixture_is_labelled_as_such(tmp_path):
    assert main(["run", "--changes", str(DEMO / "changes_report.json"), "--theses", str(DEMO / "theses.json"),
                 "--sources", str(DEMO / "sources.json"), "--out", str(tmp_path)]) == 0
    md = _md(tmp_path)
    assert "离线虚构 Fixture" in md and "虚构测试数据" in md and "模型层：未配置" in md
    assert "在线拉取" not in md


def test_live_case_outputs_say_live():
    for brief in [CASE / "output" / "brief.json"] + sorted((CASE / "output_cross_batch").glob("batch*/brief.json")):
        b = json.loads(brief.read_text(encoding="utf-8"))
        assert b["provenance"]["input_origin"] == "live_cdio_api" and b["provenance"]["data_nature"] == "public"
        assert any("在线拉取 changedetection.io" in d for d in b["disclaimers"])
        assert not any("未接入" in d or "离线" in d for d in b["disclaimers"])


def test_replay_with_verified_record(tmp_path):
    out = CASE / "output"
    assert main(["run", "--changes", str(out / "source_changes_report.json"),
                 "--input-record", str(out / "run_record.json"), "--theses", str(CASE / "theses.json"),
                 "--sources", str(CASE / "sources.json"), "--out", str(tmp_path)]) == 0
    md = _md(tmp_path)
    assert "离线回放" in md and "sha256 已与运行记录核对" in md and "真实公开资料" in md


def test_replay_with_mismatched_record_is_refused(tmp_path):
    out = CASE / "output"
    # a run record from a different capture must not vouch for this file
    other = CASE / "output_cross_batch" / "batch2" / "run_record.json"
    rc = main(["run", "--changes", str(out / "source_changes_report.json"), "--input-record", str(other),
               "--theses", str(CASE / "theses.json"), "--sources", str(CASE / "sources.json"), "--out", str(tmp_path / "o")])
    assert rc == 2 and not (tmp_path / "o").exists()


def test_public_file_without_record_is_unverified(tmp_path):
    out = CASE / "output"
    assert main(["run", "--changes", str(out / "source_changes_report.json"), "--theses", str(CASE / "theses.json"),
                 "--sources", str(CASE / "sources.json"), "--out", str(tmp_path)]) == 0
    assert "未提供运行记录，来源未经核验" in _md(tmp_path)


def test_mock_and_real_model_labels():
    p = prov.RunProvenance(input_mode="file", input_origin="file_unverified", data_nature="fictional")

    class P:
        is_mock, name = True, "mock-replay"

    class A:
        def __init__(self, b):
            self.basis = b
    from techsignal.models import Basis
    mock = prov.with_model_usage(p, P(), [A(Basis.MOCK_MODEL), A(Basis.RULE)])
    assert (mock.model_layer, mock.model_judgements) == ("mock_replay", 1)
    assert any("不是真实大模型推理" in d for d in prov.disclaimers(mock))
    P.is_mock, P.name = False, "openai-compatible:x"
    real = prov.with_model_usage(p, P(), [A(Basis.RULE)])
    assert (real.model_layer, real.model_judgements) == ("real_model", 0)


def test_record_must_be_a_live_capture():
    with pytest.raises(prov.ProvenanceError):
        prov.for_file("{}", [], json.dumps({"input_mode": "something_else"}))
