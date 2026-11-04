"""R6 browser acceptance helper: a REAL changedetection.io (temporary datastore, local addresses
allowed) plus a local test page whose content the acceptance script changes between two checks.
The page content is a fictional test page; fetching, snapshots and diffs are changedetection.io's own.

    TECHSIGNAL_CDIO_SOURCE=... python scripts/r6_acceptance_env.py <cdio python> <info.json>

Writes {"cdio_url", "api_key", "page_url", "control_url"} to info.json and runs until killed.
POST <control_url> with the new page text to change the page.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from techsignal.live import LiveCdio

PAGE = {"text": "星衡半导体新闻中心\n最新动态\n星衡半导体8英寸碳化硅产线建设进展顺利\n"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = PAGE["text"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        PAGE["text"] = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        self.send_response(204)
        self.end_headers()


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with LiveCdio(sys.argv[1], allow_private_addresses=True) as cdio:
        info = {"cdio_url": cdio.url, "api_key": cdio.client.api_key, "page_url": base + "/news.txt",
                "control_url": base + "/set", "cdio_version": cdio.client.system_info().get("version")}
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(info, f)
        print(json.dumps({k: v for k, v in info.items() if k != "api_key"}), flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
