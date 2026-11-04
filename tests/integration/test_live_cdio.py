"""ONLINE integration tests: a real changedetection.io process from this checkout.

Opt-in, because they need changedetection.io's own dependencies (pip install -r requirements.txt):
  TECHSIGNAL_CDIO_PYTHON=/path/to/python   python with changedetection.io's requirements installed
  TECHSIGNAL_LIVE_NETWORK=1                also run the real public-source case (needs GitHub access)
  TECHSIGNAL_CDIO_SOURCE=/path/to/changedetection.io  source checkout (scripts/fetch_changedetection.sh)
CI runs them in the `live-cdio` job.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import DEMO, ROOT
from techsignal.cli import main
from techsignal.live import LiveCdio, run_case

from techsignal import upstream  # noqa: E402

PY = os.getenv("TECHSIGNAL_CDIO_PYTHON")
pytestmark = [
    pytest.mark.skipif(not PY, reason="set TECHSIGNAL_CDIO_PYTHON to run against a real changedetection.io"),
    pytest.mark.skipif(upstream.cdio_source() is None,
                       reason="no changedetection.io source: run scripts/fetch_changedetection.sh or set TECHSIGNAL_CDIO_SOURCE"),
]


class Page:
    """A local page whose content we change between two real changedetection.io checks.
    The CONTENT is fictional; the fetching, snapshotting and diffing are changedetection.io's own."""

    def __init__(self, text):
        self.text = text
        page = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = page.text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/news.txt"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def test_live_cdio_end_to_end_with_local_page(tmp_path, monkeypatch):
    page = Page("星衡半导体新闻中心\n最新动态\n")
    with LiveCdio(PY, allow_private_addresses=True, log_path=tmp_path / "cdio.log") as cdio:
        uuid = cdio.client.create_watch(page.url, title="星衡半导体新闻中心（本地测试页）")
        first = cdio.wait_for_snapshots(uuid, 1)
        page.text = "星衡半导体新闻中心\n最新动态\n星衡半导体8英寸碳化硅产线量产时间推迟至2027年第四季度\n"
        import time
        time.sleep(1.1)
        cdio.client.recheck(uuid)
        snaps = cdio.wait_for_snapshots(uuid, 2)
        assert len(snaps) == 2 and snaps[0] == first[0]

        monkeypatch.setenv("TECHSIGNAL_CDIO_API_KEY", cdio.client.api_key)
        out = tmp_path / "out"
        rc = main(["run", "--cdio-url", cdio.url, "--from", str(snaps[0] - 1), "--page-size", "1",
                   "--theses", str(DEMO / "theses.json"), "--sources", str(DEMO / "sources.json"), "--out", str(out)])
        assert rc == 0

        # wrong key -> clean failure, previous outputs untouched
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        monkeypatch.setenv("TECHSIGNAL_CDIO_API_KEY", "wrong-key")
        assert main(["run", "--cdio-url", cdio.url, "--from", str(snaps[0] - 1),
                     "--theses", str(DEMO / "theses.json"), "--sources", str(DEMO / "sources.json"),
                     "--out", str(out)]) == 2
        assert {p.name: p.read_bytes() for p in out.iterdir()} == before

    record = json.loads((out / "run_record.json").read_text(encoding="utf-8"))
    assert record["input_mode"] == "live_api" and record["watches_in_report"] == 1
    log = json.loads((out / "decision_log.json").read_text(encoding="utf-8"))
    (a,) = log["assessments"]
    # 127.0.0.1 is not a registered source -> grade D -> the weakens signal is held back
    assert (a["thesis_id"], a["direction"], a["tentative_direction"]) == ("TH-001", "insufficient", "weakens")
    assert "wrong-key" not in (out / "run_record.json").read_text(encoding="utf-8")


@pytest.mark.skipif(os.getenv("TECHSIGNAL_LIVE_NETWORK") != "1", reason="set TECHSIGNAL_LIVE_NETWORK=1 (needs GitHub)")
def test_live_cdio_real_public_case(tmp_path):
    case = ROOT / "cases" / "tenstorrent_blackhole" / "case.json"
    out = tmp_path / "case_out"
    assert run_case(PY, case, out, log_path=tmp_path / "cdio.log") == 0
    log = json.loads((out / "decision_log.json").read_text(encoding="utf-8"))
    assert [(a["thesis_id"], a["assumption_id"], a["direction"]) for a in log["assessments"]] == \
        [("TH-TT-001", "BH1", "weakens")]
    committed = (ROOT / "cases" / "tenstorrent_blackhole" / "output" / "evidence.jsonl").read_text(encoding="utf-8")
    fresh = (out / "evidence.jsonl").read_text(encoding="utf-8")
    # same published versions -> same evidence content (ids depend on fetch times, so compare excerpts)
    pick = lambda t: [(json.loads(x)["excerpt_added"], json.loads(x)["excerpt_removed"]) for x in t.splitlines()]  # noqa: E731
    assert pick(fresh) == pick(committed)


@pytest.mark.skipif(os.getenv("TECHSIGNAL_LIVE_NETWORK") != "1", reason="set TECHSIGNAL_LIVE_NETWORK=1 (needs GitHub)")
def test_live_cdio_real_cross_batch_case(tmp_path):
    from techsignal.live import run_cross_batch_case
    case = ROOT / "cases" / "tenstorrent_blackhole" / "case_cross_batch.json"
    out = tmp_path / "cb"
    assert run_cross_batch_case(PY, case, out, log_path=tmp_path / "cdio.log") == 0
    b1 = json.loads((out / "batch1" / "decision_log.json").read_text(encoding="utf-8"))
    b2 = json.loads((out / "batch2" / "decision_log.json").read_text(encoding="utf-8"))
    assert [a["direction"] for a in b1["assessments"]] == ["weakens"]
    assert [t["kind"] for t in b2["cross_batch_transitions"]] == ["unresolved"]


@pytest.mark.skipif(os.getenv("TECHSIGNAL_LIVE_NETWORK") != "1", reason="set TECHSIGNAL_LIVE_NETWORK=1 (needs GitHub)")
def test_live_verification_on_real_case(tmp_path):
    """R3: real tools against real sources. Asserts the controls, not a particular research answer."""
    from techsignal.live import run_cross_batch_case
    case = ROOT / "cases" / "tenstorrent_blackhole" / "case_cross_batch.json"
    out = tmp_path / "cb"
    assert run_cross_batch_case(PY, case, out, log_path=tmp_path / "cdio.log") == 0
    assert not (out / "batch1" / "verification_trace.json").exists() or \
        json.loads((out / "batch1" / "verification_trace.json").read_text(encoding="utf-8"))["calls"] == []
    trace = json.loads((out / "batch2" / "verification_trace.json").read_text(encoding="utf-8"))
    (s,) = trace["summaries"]
    assert "signal_removed_or_negated" in s["triggers"] and 1 <= len(s["call_ids"]) <= 3
    allowed = ("http://127.0.0.1:", "https://raw.githubusercontent.com/tenstorrent/tt-metal/",
               "https://api.github.com/repos/tenstorrent/tt-metal/")
    for c in trace["calls"]:
        assert c["url"] is None or c["url"].startswith(allowed)
        assert c["status"] in ("ok", "not_found", "error", "timeout", "blocked")
    log = json.loads((out / "batch2" / "decision_log.json").read_text(encoding="utf-8"))
    orig = next(a for a in log["assessments"] if a["assessment_id"] == s["assessment_id"])
    assert orig["direction"] == "insufficient" and orig["thesis_status_change"] == "none"
    if s["outcome"] != "new_decisive_evidence":
        assert "尚未核实" in s["conclusion"]
