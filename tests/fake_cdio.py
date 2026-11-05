"""A small in-process stand-in for changedetection.io's REST API, for offline unit tests of the R6
project monitoring flow. It is NOT changedetection.io: the real service is exercised by
tests/integration/test_r6_project_live.py. Pages are served from a dict the test edits.

Implemented: systeminfo, watch list/create/get(+recheck)/history, /api/v1/changes?format=json with
the same window semantics as upstream's changes_report.watch_changes (baseline = last snapshot at or
before `from`, or the first snapshot in range for a new watch; a watch with one snapshot in range has
no change). Snapshots are stored only when content differs from the previous one (like upstream).
"""
from __future__ import annotations

import difflib
import json
import threading
import time
import uuid as uuidlib
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FakeCdio:
    def __init__(self, key: str = "test-key", auto_check: bool = True, async_delay: float = 0.0):
        self.key = key
        self.pages: dict[str, str | None] = {}        # url -> text (None = fetch error)
        self.watches: dict[str, dict] = {}
        self.snapshots: dict[str, dict[int, str]] = {}
        self.auto_check = auto_check                  # False: recheck never completes
        # > 0: like the real worker, last_checked is set when the check STARTS and the snapshot and
        # check_count follow `async_delay` seconds later (changedetectionio/worker.py)
        self.async_delay = async_delay
        self.requests: list[tuple[str, str]] = []
        self.keys_seen: list[str | None] = []
        self._clock = 0
        self._lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                outer.keys_seen.append(self.headers.get("x-api-key"))
                if self.headers.get("x-api-key") != outer.key:
                    self._send(403, {"message": "invalid key"})
                    return False
                return True

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if not self._auth():
                    return
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                parts = u.path.strip("/").split("/")
                with outer._lock:
                    if u.path == "/api/v1/systeminfo":
                        return self._send(200, {"version": "fake-0.0", "watch_count": len(outer.watches)})
                    if u.path == "/api/v1/watch":
                        return self._send(200, {k: dict(w) for k, w in outer.watches.items()})
                    if u.path == "/api/v1/changes":
                        return self._send(200, outer.report(q))
                    if len(parts) >= 4 and parts[:3] == ["api", "v1", "watch"]:
                        wid = parts[3]
                        if wid not in outer.watches:
                            return self._send(404, {"message": "no such watch"})
                        if len(parts) == 4:
                            if q.get("recheck") and outer.auto_check:
                                if outer.async_delay:
                                    outer.watches[wid]["last_checked"] = outer._now()
                                    threading.Timer(outer.async_delay, outer.fetch_locked, args=(wid,)).start()
                                else:
                                    outer.fetch(wid)
                            return self._send(200, dict(outer.watches[wid]))
                        if parts[4] == "history" and len(parts) == 5:
                            return self._send(200, {str(k): f"/snap/{k}" for k in outer.snapshots[wid]})
                return self._send(404, {"message": "not found"})

            def do_POST(self):
                outer.requests.append(("POST", self.path))
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/v1/watch":
                    with outer._lock:
                        wid = outer.add_watch(body["url"], body.get("title", ""))
                        outer.fetch(wid)        # creating a watch queues its first check
                    return self._send(201, {"uuid": wid})
                return self._send(404, {"message": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # -- model --------------------------------------------------------------------------------

    def add_watch(self, url: str, title: str = "") -> str:
        wid = str(uuidlib.uuid4())
        self.watches[wid] = {"uuid": wid, "url": url, "title": title, "last_checked": 0, "last_changed": 0, "check_count": 0,
                             "last_error": False}
        self.snapshots[wid] = {}
        return wid

    def _now(self) -> int:
        self._clock = max(int(time.time()), self._clock + 1)
        return self._clock

    def fetch_locked(self, wid: str) -> None:
        with self._lock:
            self.fetch(wid)

    def fetch(self, wid: str) -> None:
        w = self.watches[wid]
        ts = self._now()
        w["last_checked"] = ts
        w["check_count"] = w.get("check_count", 0) + 1
        text = self.pages.get(w["url"])
        if text is None:
            w["last_error"] = "fetch failed (fake)"
            return
        w["last_error"] = False
        snaps = self.snapshots[wid]
        if not snaps or snaps[max(snaps)] != text:
            snaps[ts] = text
            w["last_changed"] = ts

    def report(self, q: dict) -> dict:
        frm = int(q["from"])
        to = int(q.get("to") or self._now())
        uuids = q["uuids"].split(",") if q.get("uuids") else list(self.watches)
        out = []
        for wid in uuids:
            if wid not in self.watches:
                continue
            keys = sorted(self.snapshots[wid])
            in_range = [k for k in keys if frm < k <= to]
            before = [k for k in keys if k <= frm]
            if not in_range:
                continue
            new = not before
            base = in_range.pop(0) if new else before[-1]
            if not in_range:
                continue
            a, b = self.snapshots[wid][base], self.snapshots[wid][in_range[-1]]
            iso = lambda t: datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
            diff = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), iso(base), iso(in_range[-1]),
                                                  n=1, lineterm=""))
            w = self.watches[wid]
            out.append({"uuid": wid, "title": w["title"], "url": w["url"], "changes": len(in_range),
                        "new_in_period": new, "no_net_difference": a == b, "error": w["last_error"] or None,
                        "from_timestamp": base, "to_timestamp": in_range[-1], "diff": diff if a != b else "",
                        "truncated": False})
        return {"from": frm, "to": to, "mode": "net_change", "total": len(out), "offset": 0, "limit": 50,
                "monitors_checked": len(uuids), "changes_total": sum(w["changes"] for w in out),
                "incomplete": False, "watches": out}

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
