"""R4: the Streamlit app driven headlessly with streamlit.testing (real script, real widgets, no browser).
Skipped when streamlit is not installed (pip install -e ".[ui]")."""
import json
import shutil

import pytest

pytest.importorskip("streamlit")
from conftest import ROOT  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "techsignal" / "workbench" / "app.py")
CROSS = "tenstorrent-blackhole-70b-cross-batch"
PAGES = ["总览", "管理层速览", "研究判断", "变化时间线", "补证过程", "证据详情"]


def _app(**env):
    at = AppTest.from_file(APP, default_timeout=120)
    return at


def _text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] \
        + [w.value for w in at.warning] + [s.value for s in at.success] + [e.value for e in at.error]
    parts += [f"{m.label}:{m.value}" for m in at.metric]
    return "\n".join(str(p) for p in parts)


@pytest.fixture()
def at():
    a = _app().run()
    assert not a.exception
    return a


def test_first_screen_is_the_real_case_in_offline_mode(at):
    assert at.selectbox(key="case").value == "tenstorrent-blackhole-70b"
    t = _text(at)
    assert "Tenstorrent Blackhole" in t and "当前为离线展示" in t and "不是 AI 自主推理" in t
    assert {m.label for m in at.metric} == {"研究对象", "本批次变化", "当前判断", "待核实事项"}
    assert "在线实测" in t and "真实公开资料" in t


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_without_internal_values(at, page):
    at.selectbox(key="case").set_value(CROSS).run()
    at.radio(key="page").set_value(page).run()
    assert not at.exception
    t = _text(at)
    for raw in ("insufficient", "weakens", "live_cdio_api", "verification_tool", "related_evidence_no_direction",
                "signal_removed_or_negated", "cdio_snapshot_search"):
        assert raw not in t, (page, raw)


def test_batch_switch_changes_the_judgement(at):
    at.selectbox(key="case").set_value(CROSS).run()
    assert "证据不足，待核实" in _text(at)          # default: latest batch
    at.selectbox(key="batch").set_value(0).run()
    assert "削弱原判断" in _text(at)


def test_timeline_shows_publication_and_fetch_time(at):
    at.selectbox(key="case").set_value(CROSS).run()
    at.radio(key="page").set_value("变化时间线").run()
    t = _text(at)
    assert "发布时间" in t and "系统抓取时间" in t and "2025-07-11" in t and "2025-08-12" in t


def test_clicking_an_evidence_id_opens_its_detail(at):
    at.selectbox(key="case").set_value(CROSS).run()
    buttons = [b for b in at.button if b.label.startswith("EV-")]
    assert buttons
    target = buttons[0].label
    buttons[0].click().run()
    assert at.radio(key="page").value == "证据详情"
    t = _text(at)
    assert target in t and "发布时间" in t and "系统抓取时间" in t and "用于哪些判断" in t


def test_verification_trace_toggle(at):
    at.selectbox(key="case").set_value(CROSS).run()
    at.radio(key="page").set_value("补证过程").run()
    collapsed = _text(at)
    assert "为什么补证" in collapsed and "补证没有改变判断" in collapsed and "请求地址" not in collapsed
    at.toggle(key="full_trace").set_value(True).run()
    expanded = _text(at)
    assert "请求地址" in expanded and "api.github.com" in expanded


def test_brief_page_has_download(at):
    at.selectbox(key="case").set_value(CROSS).run()
    at.radio(key="page").set_value("管理层速览").run()
    assert not at.exception
    dl = [e for e in at.get("download_button")]
    assert dl and "下载中文速览" in dl[0].proto.label


def test_offline_replay_mode_runs_the_pipeline(at):
    at.selectbox(key="case").set_value(CROSS).run()
    at.radio(key="mode").set_value("离线回放").run()
    assert "点击左侧「运行离线回放」" in _text(at)
    at.button(key="run_replay").click().run()
    assert not at.exception
    t = _text(at)
    assert "离线回放完成：各批次判断与已保存的实测结果一致" in t
    assert "离线回放：用已保存的在线实测数据重新运行研判" in t and "未执行补证查询" in t


def test_live_mode_stays_offline_without_connection(at):
    at.radio(key="mode").set_value("在线连接").run()
    assert "当前未连接任何在线服务" in _text(at)
    at.text_input(key="cdio_url").set_value("http://127.0.0.1:9").run()
    at.button(key="run_live").click().run()
    t = _text(at)
    assert "无法完成在线拉取" in t and "当前未连接任何在线服务" in t


def test_missing_cases_show_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHSIGNAL_CASES_ROOT", str(tmp_path))
    a = _app().run()
    assert not a.exception and "没有找到可用的研究案例" in _text(a)


def test_broken_saved_output_shows_an_error(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "cases" / "tenstorrent_blackhole", tmp_path / "tt")
    (tmp_path / "tt" / "output" / "brief.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("TECHSIGNAL_CASES_ROOT", str(tmp_path))
    a = _app().run()
    assert not a.exception and "无法加载该案例" in _text(a)
    a.selectbox(key="case").set_value(CROSS).run()        # the other case still works
    assert not a.exception and "无法加载该案例" not in _text(a)
    json.loads((tmp_path / "tt" / "output_cross_batch" / "batch1" / "brief.json").read_text(encoding="utf-8"))
