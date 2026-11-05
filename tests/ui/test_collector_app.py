"""Collector home page driven headlessly: create a task, collect twice, filter, search, export."""
import pytest

pytest.importorskip("streamlit")
from conftest import ROOT
from streamlit.testing.v1 import AppTest
from test_collector import FIX, Site

from techsignal import collector as C

APP = str(ROOT / "techsignal" / "workbench" / "app.py")


def _text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] \
        + [w.value for w in at.warning] + [s.value for s in at.success] + [e.value for e in at.error]
    return "\n".join(str(p) for p in parts)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    site = Site()
    monkeypatch.setenv("TECHSIGNAL_COLLECTOR_ROOT", str(tmp_path / "collector"))
    monkeypatch.setenv("TECHSIGNAL_ALLOW_PRIVATE_URLS", "1")        # the test site is on 127.0.0.1
    yield site, tmp_path / "collector"
    site.httpd.shutdown()
    site.httpd.server_close()


def test_collect_read_filter_export_without_code(env):
    site, root = env
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception and at.selectbox(key="ctask").value == "__new__"
    t = _text(at)
    assert "新建采集任务" in t and "不需要写代码" in t and "研究判断" not in t
    at.text_input(key="cnew-name").set_value("AI 芯片行业动态")
    at.text_input(key="cnew-kw").set_value("Tenstorrent，Blackhole")
    at.text_input(key="cnew-tags").set_value("AI芯片")
    at.text_area(key="cnew-urls").set_value(f"{site.base}/rss 芯片新闻").run()
    at.button(key="create_task").click().run()
    assert not at.exception
    task = C.CollectorStore(root).tasks()[0]
    assert at.selectbox(key="ctask").value == task.task_id and "还没有采集到信息" in _text(at)

    at.button(key="collect_now").click().run()
    assert "AI 芯片行业动态：新增 2 条，跳过重复 0 条" in _text(at)
    assert "[Tenstorrent 发布 Blackhole 新固件](https://news.example.com/a1)" in _text(at)
    site.put("/rss", (FIX / "rss_v2.xml").read_bytes())
    at.button(key="collect_now").click().run()
    assert "新增 1 条，跳过重复 2 条" in _text(at) and "共 3 条" in _text(at)

    at.text_input(key="c_q").set_value("碳化硅").run()
    assert "筛选后 1 条" in _text(at) and "Wormhole" not in _text(at)
    at.text_input(key="c_q").set_value("").run()
    at.checkbox(key="c_hits").check().run()
    assert "筛选后 1 条" in _text(at)
    assert at.get("download_button") and len(at.get("download_button")) == 2

    at.radio(key="cpage").set_value("采集状态").run()
    assert "✅ 成功" in _text(at) and "RSS/Atom 订阅源" in _text(at)
    site.put("/rss", b"", "text/plain", status=503)
    at.radio(key="cpage").set_value("信息流").run()
    at.button(key="collect_now").click().run()
    assert "1 个来源失败" in _text(at) and "共 3 条" in _text(at)        # history kept
    at.radio(key="cpage").set_value("采集状态").run()
    assert "HTTP 503" in _text(at)


def test_invalid_task_shows_the_problems(env):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.button(key="create_task").click().run()
    t = _text(at)
    assert "请填写采集任务名称" in t and "请至少填写一个公开信息来源网址" in t


def test_advanced_research_is_still_reachable(env):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.radio(key="workspace").set_value("我的研究项目").run()
    assert not at.exception and "新建研究项目" in _text(at)


def test_ai_panel_is_optional_and_shows_results(env, monkeypatch):
    """No key: the feed works and the AI panel says so. With stored results: the expander and the
    disclaimer are shown. (The API itself is covered by tests/test_collector_ai.py with a mock.)"""
    import json as _json
    from techsignal import collector_ai as A
    site, root = env
    monkeypatch.delenv(A.KEY_ENV, raising=False)
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    store = C.CollectorStore(root, allow_private_urls=True)
    t = store.create_task("AI 芯片", "Tenstorrent", "", f"{site.base}/rss 芯片新闻")
    store.collect(t.task_id)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert "密钥来源：未配置" in _text(at)
    at.button(key="ds_run").click().run()
    assert A.KEY_ENV in _text(at) and "共 2 条" in _text(at)          # clear error, feed intact
    it = store.items(t.task_id)[0]
    rec = {"item_id": it.item_id, "fingerprint": it.fingerprint, "processed_at": "2026-11-05T00:00:00Z",
           "model": "deepseek-flash", "prompt_version": A.PROMPT_VERSION, "summary_zh": "测试摘要",
           "relevance": "相关", "category": "产品发布", "reason": "命中关键词"}
    f = A._file(store, t.task_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert any(e.label == "AI 摘要｜相关｜产品发布" for e in at.expander)
    assert "测试摘要" in _text(at) and "AI 生成，仅供信息整理" in _text(at)
