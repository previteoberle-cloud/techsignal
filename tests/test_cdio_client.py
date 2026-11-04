"""ChangeDetectionClient against a real local HTTP server (stdlib http.server), not a mock transport.

The server imitates the /api/v1 contract (x-api-key auth, limit/offset paging of a ChangesReport
built by upstream changes_report.group_changes). Behaviour of a real changedetection.io instance is
covered separately by tests/integration/test_live_cdio.py.
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import DEMO
from techsignal.adapters.cdio import parse_changes_report
from techsignal.cdio_client import (
    CdioAuthError,
    CdioConnectionError,
    CdioHTTPError,
    CdioNotFoundError,
    CdioProtocolError,
    CdioTimeoutError,
    ChangeDetectionClient,
    HTTPResponse,
)

KEY = "test-key-123"
REPORT = json.loads((DEMO / "changes_report.json").read_text(encoding="utf-8"))  # 11 watches


class Server:
    def __init__(self):
        self.calls = []
        self.script = {}          # path -> list of (status, body) served in order, then the default
        self.total_override = {}  # offset -> total to report (to simulate data changing mid-paging)
        self.incomplete_at = None
        self.delay = 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.calls.append((method, u.path, q, dict(self.headers), body))
                if outer.delay:
                    time.sleep(outer.delay)
                if self.headers.get("x-api-key") != KEY:
                    return self._send(403, "Invalid access - API key invalid.")
                scripted = outer.script.get(u.path)
                if scripted:
                    return self._send(*scripted.pop(0))
                if u.path == "/api/v1/systeminfo":
                    return self._send(200, {"version": "test", "watch_count": 11})
                if u.path == "/api/v1/watch" and method == "POST":
                    return self._send(201, {"uuid": "new-uuid"})
                if u.path == "/api/v1/changes":
                    off, lim = int(q.get("offset", 0)), int(q.get("limit", 50))
                    page = dict(REPORT, offset=off, limit=lim, watches=REPORT["watches"][off:off + lim])
                    page["total"] = outer.total_override.get(off, REPORT["total"])
                    page["incomplete"] = outer.incomplete_at == off
                    return self._send(200, page)
                return self._send(404, {"message": "not found"})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def srv():
    s = Server()
    yield s
    s.close()


def _client(srv, **kw):
    kw.setdefault("sleep", lambda s: None)
    return ChangeDetectionClient(srv.url, api_key=kw.pop("key", KEY), **kw)


def test_auth_header_and_systeminfo(srv):
    assert _client(srv).system_info()["version"] == "test"
    assert {k.lower(): v for k, v in srv.calls[0][3].items()}["x-api-key"] == KEY


def test_wrong_key_is_auth_error(srv):
    with pytest.raises(CdioAuthError) as e:
        _client(srv, key="wrong").system_info()
    assert e.value.status == 403
    assert len(srv.calls) == 1  # 4xx is not retried


def test_not_found(srv):
    with pytest.raises(CdioNotFoundError):
        _client(srv).get_watch("missing")


def test_get_retries_5xx_then_succeeds(srv):
    srv.script["/api/v1/systeminfo"] = [(502, b"bad gateway"), (503, b"busy")]
    assert _client(srv, retries=2).system_info()["version"] == "test"
    assert len(srv.calls) == 3


def test_get_gives_up_after_retries(srv):
    srv.script["/api/v1/systeminfo"] = [(500, b"x")] * 5
    with pytest.raises(CdioHTTPError) as e:
        _client(srv, retries=1).system_info()
    assert e.value.status == 500 and len(srv.calls) == 2


def test_post_is_not_retried(srv):
    srv.script["/api/v1/watch"] = [(500, b"x")]
    with pytest.raises(CdioHTTPError):
        _client(srv).create_watch("https://example.com/")
    assert len(srv.calls) == 1


def test_create_watch_sends_body(srv):
    assert _client(srv).create_watch("https://example.com/", title="t", tag="g") == "new-uuid"
    assert srv.calls[0][4] == {"url": "https://example.com/", "title": "t", "tag": "g"}


def test_non_json_is_protocol_error(srv):
    srv.script["/api/v1/systeminfo"] = [(200, b"<html>login</html>")]
    with pytest.raises(CdioProtocolError):
        _client(srv).system_info()


def test_timeout(srv):
    srv.delay = 1.0
    with pytest.raises(CdioTimeoutError):
        _client(srv, timeout=0.2, retries=0).system_info()


def test_connection_refused_after_retries():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    slept = []
    c = ChangeDetectionClient(f"http://127.0.0.1:{port}", retries=2, backoff=0.1, sleep=slept.append)
    with pytest.raises(CdioConnectionError):
        c.system_info()
    assert c.requests_made == 3 and slept == [0.1, 0.2]


def test_rejects_non_http_base_url():
    with pytest.raises(ValueError):
        ChangeDetectionClient("ftp://x")


def test_pagination_merges_every_page(srv):
    report = _client(srv).get_changes_report(from_ts=REPORT["from"], to_ts=REPORT["to"], page_size=4)
    pages = [c for c in srv.calls if c[1] == "/api/v1/changes"]
    assert [p[2]["offset"] for p in pages] == ["0", "4", "8"]
    assert all(p[2]["format"] == "json" and p[2]["limit"] == "4" for p in pages)
    assert [w["uuid"] for w in report["watches"]] == [w["uuid"] for w in REPORT["watches"]]
    assert report["offset"] == 0 and report["limit"] == 11 and report["total"] == 11
    meta, events = parse_changes_report(report)  # the merged result is a valid ChangesReport
    assert len(events) == 11 and meta["incomplete"] is False


def test_incomplete_flag_propagates(srv):
    srv.incomplete_at = 4
    report = _client(srv).get_changes_report(from_ts=1, page_size=4)
    assert report["incomplete"] is True


def test_total_changing_mid_pagination_is_an_error(srv):
    srv.total_override = {4: 12}
    with pytest.raises(CdioProtocolError):
        _client(srv).get_changes_report(from_ts=1, page_size=4)


def test_max_pages_guard(srv):
    with pytest.raises(CdioProtocolError):
        _client(srv).get_changes_report(from_ts=1, page_size=1, max_pages=3)


def test_bad_report_shape_is_protocol_error(srv):
    srv.script["/api/v1/changes"] = [(200, {"watches": "nope"})]
    with pytest.raises(CdioProtocolError):
        _client(srv).get_changes_report(from_ts=1)


def test_duplicate_watch_across_pages_is_an_error():
    page = dict(REPORT, total=2, watches=[REPORT["watches"][0]])

    def transport(method, url, headers, body, timeout):
        return HTTPResponse(200, json.dumps(page).encode())
    c = ChangeDetectionClient("http://x.invalid", transport=transport)
    with pytest.raises(CdioProtocolError):
        c.get_changes_report(from_ts=1, page_size=1)


def test_from_env(monkeypatch):
    monkeypatch.setenv("TECHSIGNAL_CDIO_URL", "http://cdio.local:5000/")
    monkeypatch.setenv("TECHSIGNAL_CDIO_API_KEY", "k")
    monkeypatch.setenv("TECHSIGNAL_CDIO_TIMEOUT", "7")
    c = ChangeDetectionClient.from_env()
    assert (c.base_url, c.api_key, c.timeout) == ("http://cdio.local:5000", "k", 7.0)
