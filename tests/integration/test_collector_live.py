"""ONLINE checks of the information collector against REAL public sources (and a real changedetection.io).

  TECHSIGNAL_LIVE_NETWORK=1                          real public feeds and pages
  TECHSIGNAL_CDIO_PYTHON + TECHSIGNAL_CDIO_SOURCE    page collection through a real changedetection.io
CI runs both in the `live-cdio` job. Each test collects twice: the second run must not add duplicates.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from techsignal import collector as C
from techsignal import upstream

LIVE = os.getenv("TECHSIGNAL_LIVE_NETWORK") == "1"

FEEDS = [
    ("https://github.com/tenstorrent/tt-metal/releases.atom", "tt-metal 发布记录（Atom）"),
    ("https://rss.arxiv.org/rss/cs.AR", "arXiv 计算机体系结构（RSS）"),
]
PAGE = ("https://raw.githubusercontent.com/tenstorrent/tt-metal/main/README.md", "tt-metal README（网页）")


@pytest.mark.skipif(not LIVE, reason="set TECHSIGNAL_LIVE_NETWORK=1 to collect from real public sources")
@pytest.mark.parametrize("url,name", FEEDS + [PAGE])
def test_real_public_source_twice(tmp_path, url, name):
    store = C.CollectorStore(tmp_path / "c")
    t = store.create_task(f"在线验证：{name}", "Tenstorrent, Blackhole, RISC-V", "在线验证", f"{url} {name}")
    r1 = store.collect(t.task_id)
    st = store.status(t.task_id)[t.sources[0].source_id]
    assert r1.failed_sources == 0, st.error
    assert r1.new_items >= 1
    items = store.items(t.task_id)
    assert all(i.title and i.url and i.fetched_at for i in items)
    r2 = store.collect(t.task_id)
    assert r2.failed_sources == 0
    assert len(store.items(t.task_id)) == len(items) + r2.new_items
    assert r2.new_items <= 2                  # a busy feed may publish in between; nothing is re-added
    ids = [i.item_id for i in store.items(t.task_id)]
    assert len(ids) == len(set(ids))
    print(f"{name}: 第一次 {r1.new_items} 条（{st.detected_kind}），第二次新增 {r2.new_items}、重复 {r2.duplicates}；"
          f"示例：{items[0].title[:60]} {items[0].published_at}")


PY = os.getenv("TECHSIGNAL_CDIO_PYTHON")


@pytest.mark.skipif(not PY or upstream.cdio_source() is None, reason="needs a real changedetection.io (TECHSIGNAL_CDIO_PYTHON)")
def test_page_collection_through_real_changedetection(tmp_path):
    from techsignal.cdio_client import ChangeDetectionClient
    from techsignal.live import LiveCdio
    text = {"v": "公司新闻\n旧消息\n"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            b = text["v"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/news.txt"
    try:
        with LiveCdio(PY, allow_private_addresses=True, log_path=tmp_path / "cdio.log") as cdio:
            client = ChangeDetectionClient(cdio.url, api_key=cdio.client.api_key, timeout=30)
            store = C.CollectorStore(tmp_path / "c", allow_private_urls=True)
            t = store.create_task("经 changedetection.io 采集", "Blackhole", "", f"{url} 本地测试页", kinds={url: "page"})
            r1 = store.collect(t.task_id, cdio_client=client)
            assert r1.new_items == 1 and r1.per_source[t.sources[0].source_id]["via"] == "changedetection.io"
            assert store.collect(t.task_id, cdio_client=client).new_items == 0
            text["v"] = "公司新闻\n新：Blackhole 量产\n旧消息\n"
            r3 = store.collect(t.task_id, cdio_client=client)
            change = [i for i in store.items(t.task_id) if i.kind == "page_change"]
            assert r3.new_items == 1 and change[0].summary == "新：Blackhole 量产" and change[0].keyword_hits == ["Blackhole"]
    finally:
        httpd.shutdown()
        httpd.server_close()
