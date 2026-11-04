"""Information collector MVP: tasks, RSS/Atom and page collection, dedup, failures, persistence, export.
A local HTTP server serves the fixtures in tests/fixtures/collector (fictional, offline). Real public
sources are covered by tests/integration/test_collector_live.py."""
import csv
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import ROOT

from techsignal import collector as C

FIX = ROOT / "tests" / "fixtures" / "collector"


class Site:
    def __init__(self):
        self.pages = {}                       # path -> (status, content-type, bytes)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                status, ctype, body = outer.pages.get(self.path, (404, "text/plain", b"not found"))
                self.send_response(status)
                if status in (301, 302):
                    self.send_header("Location", body.decode())
                    body = b""
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def put(self, path, body, ctype="application/rss+xml; charset=utf-8", status=200):
        self.pages[path] = (status, ctype, body if isinstance(body, bytes) else body.encode("utf-8"))


@pytest.fixture()
def site():
    s = Site()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


@pytest.fixture()
def store(tmp_path):
    return C.CollectorStore(tmp_path / "collector", allow_private_urls=True)   # the fixtures are on 127.0.0.1


def _task(store, site, *paths, keywords="Tenstorrent, Blackhole"):
    return store.create_task("AI 芯片行业动态", keywords, "AI芯片", "\n".join(f"{site.base}{p} 来源{p}" for p in paths))


def test_create_task_without_any_judgement(store, site):
    t = _task(store, site, "/rss")
    assert t.name == "AI 芯片行业动态" and t.keywords == ["Tenstorrent", "Blackhole"] and t.tags == ["AI芯片"]
    assert len(t.sources) == 1 and t.sources[0].kind == "auto"
    assert store.get(t.task_id) == t and [x.task_id for x in store.tasks()] == [t.task_id]


@pytest.mark.parametrize("name,urls,why", [
    ("", "https://example.com/feed", "名称"),
    ("x", "", "至少填写一个"),
    ("x", "http://127.0.0.1/feed", "本机"),
    ("x", "ftp://example.com/feed", "http"),
])
def test_invalid_tasks_are_refused(tmp_path, name, urls, why):
    s = C.CollectorStore(tmp_path / "c", allow_private_urls=False)
    with pytest.raises(C.CollectorError) as e:
        s.create_task(name, "", "", urls)
    assert why in " ".join(e.value.problems)


def test_duplicate_task_name_refused(store, site):
    _task(store, site, "/rss")
    with pytest.raises(C.CollectorError):
        _task(store, site, "/other")


def test_rss_two_collections_only_append_new_items(store, site):
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss")
    r1 = store.collect(t.task_id)
    assert (r1.new_items, r1.duplicates, r1.failed_sources) == (2, 0, 0)
    first = store.items(t.task_id)
    it = next(i for i in first if i.url.endswith("/a1"))
    assert it.title == "Tenstorrent 发布 Blackhole 新固件" and it.published_at == "2026-11-03T08:00:00Z"
    assert it.summary == "新固件支持\nLlama 3.1 70B\n。" or "Llama 3.1 70B" in it.summary
    assert it.keyword_hits == ["Tenstorrent", "Blackhole"] and it.source_name == "来源/rss"
    site.put("/rss", (FIX / "rss_v2.xml").read_bytes())       # one new entry, two repeated
    r2 = store.collect(t.task_id)
    assert (r2.new_items, r2.duplicates) == (1, 2)
    assert len(store.items(t.task_id)) == 3
    r3 = store.collect(t.task_id)                              # nothing new
    assert (r3.new_items, r3.duplicates) == (0, 3) and len(store.items(t.task_id)) == 3


def test_atom_feed(store, site):
    site.put("/atom", (FIX / "atom.xml").read_bytes(), "application/atom+xml")
    t = _task(store, site, "/atom")
    store.collect(t.task_id)
    items = store.items(t.task_id)
    assert [i.title for i in items] == ["v1.2 发布", "v1.1 发布"]
    assert items[0].url == "https://example.com/releases/v1.2" and items[0].published_at == "2026-11-01T10:00:00Z"
    assert items[0].summary == "支持 Blackhole" and items[1].summary == "修复若干问题"


def test_same_content_from_two_sources_is_stored_once(store, site):
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    site.put("/mirror", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss", "/mirror")
    r = store.collect(t.task_id)
    assert (r.new_items, r.duplicates) == (2, 2)


def test_page_baseline_then_only_real_changes(store, site):
    site.put("/page", "<html><head><title>公司新闻</title></head><body><p>旧消息</p></body></html>", "text/html")
    t = _task(store, site, "/page")
    store.collect(t.task_id)
    assert [i.kind for i in store.items(t.task_id)] == ["page_snapshot"]
    assert store.collect(t.task_id).new_items == 0                       # unchanged
    site.put("/page", "<html><head><title>公司新闻</title></head><body><p>新：Blackhole 量产</p><p>旧消息</p></body></html>",
             "text/html")
    r = store.collect(t.task_id)
    ch = [i for i in store.items(t.task_id) if i.kind == "page_change"]
    assert r.new_items == 1 and ch[0].title == "公司新闻（页面更新）" and ch[0].summary == "新：Blackhole 量产"
    site.put("/page", "<html><head><title>公司新闻</title></head><body><p>新：Blackhole 量产</p></body></html>", "text/html")
    assert store.collect(t.task_id).new_items == 0                       # a deletion only is not news


def test_failure_keeps_history_and_says_why(store, site):
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss")
    store.collect(t.task_id)
    site.put("/rss", b"oops", "text/plain", status=500)
    r = store.collect(t.task_id)
    st = store.status(t.task_id)[t.sources[0].source_id]
    assert r.failed_sources == 1 and st.ok is False and "HTTP 500" in st.error and st.last_success
    assert len(store.items(t.task_id)) == 2
    site.put("/rss", b"<rss><channel><item><title>x", "application/rss+xml")
    store.collect(t.task_id)
    assert "XML 格式错误" in store.status(t.task_id)[t.sources[0].source_id].error


def test_entity_declarations_are_refused(store, site):
    site.put("/rss", b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>')
    t = _task(store, site, "/rss")
    store.collect(t.task_id)
    assert "DOCTYPE" in store.status(t.task_id)[t.sources[0].source_id].error


def test_private_sources_and_redirects_are_refused(site):
    with pytest.raises(C.CollectorError):
        C.http_get(site.base + "/x", allow_private=False)                 # the source itself is private
    guard = C._CheckedRedirect(allow_private=False)
    import urllib.request
    req = urllib.request.Request("https://example.com/feed")
    with pytest.raises(C.ProjectError):
        guard.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/")
    assert guard.redirect_request(req, None, 302, "Found", {}, "https://example.org/feed") is not None


def test_persistence_across_store_instances(store, site, tmp_path):
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss")
    store.collect(t.task_id)
    again = C.CollectorStore(tmp_path / "collector", allow_private_urls=True)    # "restart"
    assert len(again.items(t.task_id)) == 2 and again.get(t.task_id).name == t.name
    assert again.collect(t.task_id).new_items == 0


def test_filters_and_exports(store, site):
    site.put("/rss", (FIX / "rss_v2.xml").read_bytes())
    t = _task(store, site, "/rss")
    store.collect(t.task_id)
    items = store.items(t.task_id)
    assert [i.title for i in C.filter_items(items, query="碳化硅")] == ["某公司扩产 8 英寸碳化硅产线"]
    assert len(C.filter_items(items, only_keyword_hits=True)) == 1
    assert len(C.filter_items(items, since="2026-11-03")) == 2
    assert len(C.filter_items(items, until="2026-11-02")) == 1
    rows = list(csv.reader(io.StringIO(C.to_csv(items).decode("utf-8-sig"))))
    assert rows[0][:3] == ["标题", "原始链接", "来源"] and len(rows) == 4
    md = C.to_markdown(items)
    assert "[Wormhole 集群软件更新](https://news.example.com/a3)" in md and "共 3 条" in md


def test_task_ids_cannot_escape(store):
    for bad in ("../x", "t-../../a", "", None):
        with pytest.raises(C.CollectorError):
            store.get(bad)


def test_edit_task_keeps_source_ids(store, site):
    t = _task(store, site, "/rss")
    t2 = store.update_task(t.task_id, "AI 芯片行业动态", "Tenstorrent", "", f"{site.base}/rss 来源\n{site.base}/atom 新来源")
    assert t2.sources[0].source_id == t.sources[0].source_id and len(t2.sources) == 2


# ---------------------------------------------------------------- consistency (final round, P0) -----

def test_failed_archive_write_does_not_lose_a_page_change(store, site, monkeypatch):
    """Counterexample: the page snapshot was written before items.jsonl. If the archive write failed,
    the next collection compared against the NEW snapshot and the change was never archived."""
    site.put("/page", "<html><title>公司新闻</title><body><p>旧消息</p></body></html>", "text/html")
    t = _task(store, site, "/page")
    store.collect(t.task_id)
    site.put("/page", "<html><title>公司新闻</title><body><p>新：Blackhole 量产</p><p>旧消息</p></body></html>", "text/html")
    real = C.atomic_write

    def boom(path, text):
        if path.name == "items.jsonl":
            raise OSError("disk full")
        return real(path, text)
    monkeypatch.setattr(C, "atomic_write", boom)
    with pytest.raises(OSError):
        store.collect(t.task_id)
    monkeypatch.setattr(C, "atomic_write", real)
    assert [i.kind for i in store.items(t.task_id)] == ["page_snapshot"]       # nothing half-written
    r = store.collect(t.task_id)                                                # retry
    changes = [i for i in store.items(t.task_id) if i.kind == "page_change"]
    assert r.new_items == 1 and changes and changes[0].summary == "新：Blackhole 量产"
    assert store.collect(t.task_id).new_items == 0                               # and only once


def test_failed_archive_write_does_not_lose_feed_entries(store, site, monkeypatch):
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss")
    real = C.atomic_write
    monkeypatch.setattr(C, "atomic_write", lambda p, x: (_ for _ in ()).throw(OSError("disk full"))
                        if p.name == "items.jsonl" else real(p, x))
    with pytest.raises(OSError):
        store.collect(t.task_id)
    monkeypatch.setattr(C, "atomic_write", real)
    assert store.collect(t.task_id).new_items == 2


def test_crash_between_archive_and_snapshot_does_not_duplicate(store, site, monkeypatch):
    """The other order of failure: items.jsonl written, then the snapshot write fails."""
    site.put("/page", "<html><title>公司新闻</title><body><p>旧消息</p></body></html>", "text/html")
    t = _task(store, site, "/page")
    store.collect(t.task_id)
    site.put("/page", "<html><title>公司新闻</title><body><p>新：Blackhole 量产</p><p>旧消息</p></body></html>", "text/html")
    real = C.atomic_write

    def boom(path, text):
        if path.parent.name == "pages":
            raise OSError("disk full")
        return real(path, text)
    monkeypatch.setattr(C, "atomic_write", boom)
    with pytest.raises(OSError):
        store.collect(t.task_id)
    monkeypatch.setattr(C, "atomic_write", real)
    assert [i.kind for i in store.items(t.task_id)].count("page_change") == 1     # archived once
    r = store.collect(t.task_id)                                                  # recovers the snapshot
    assert (r.new_items, r.duplicates) == (0, 1)
    assert [i.kind for i in store.items(t.task_id)].count("page_change") == 1
    assert store.collect(t.task_id).new_items == 0


def test_a_long_running_collection_is_not_taken_over(store, site):
    """A collection may legitimately run for minutes (many sources x 20 s timeout). A second run must
    not treat its lock as stale after 120 s and collect concurrently."""
    import os
    import time
    site.put("/rss", (FIX / "rss_v1.xml").read_bytes())
    t = _task(store, site, "/rss")
    lock = store._dir(t.task_id) / ".collect-lock"
    lock.mkdir(parents=True)
    old = time.time() - 300                     # a run that started 5 minutes ago is still going
    os.utime(lock, (old, old))
    with pytest.raises(C.ProjectError) as e:
        store.collect(t.task_id)
    assert "占用" in str(e.value) and store.items(t.task_id) == []
