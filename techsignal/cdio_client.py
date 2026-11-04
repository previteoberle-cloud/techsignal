"""HTTP client for a running changedetection.io instance (its REST API, /api/v1).

Stdlib only (urllib). Endpoints used, all read from this repository:
  GET  /api/v1/systeminfo                       api/SystemInfo.py
  GET  /api/v1/watch                            api/Watch.py  (CreateWatch / WatchCollection)
  POST /api/v1/watch                            -> {"uuid": ...}, 201
  GET  /api/v1/watch/{uuid}[?recheck=1]         recheck queues a check
  PUT  /api/v1/watch/{uuid}
  DELETE /api/v1/watch/{uuid}
  GET  /api/v1/watch/{uuid}/history             {timestamp: snapshot path}
  GET  /api/v1/watch/{uuid}/history/{timestamp} snapshot text
  GET  /api/v1/changes?format=json&...          api/Changes.py, paginated with limit/offset

Auth: the `x-api-key` header (api/auth.py); a wrong key answers 403.

This client only reads what changedetection.io already fetched and diffed; it does not fetch
web pages itself.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass


class CdioError(RuntimeError):
    """Base class. `status` is the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CdioAuthError(CdioError):
    pass


class CdioNotFoundError(CdioError):
    pass


class CdioHTTPError(CdioError):
    pass


class CdioConnectionError(CdioError):
    pass


class CdioTimeoutError(CdioError):
    pass


class CdioProtocolError(CdioError):
    """The server answered, but not with what the API contract says."""


@dataclass
class HTTPResponse:
    status: int
    body: bytes


Transport = Callable[[str, str, dict, bytes | None, float], HTTPResponse]


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> HTTPResponse:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-configured URL
            return HTTPResponse(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return HTTPResponse(e.code, e.read() or b"")
    except TimeoutError as e:
        raise CdioTimeoutError(f"请求超时（{timeout}s）：{method} {url}") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise CdioTimeoutError(f"请求超时（{timeout}s）：{method} {url}") from e
        raise CdioConnectionError(f"无法连接 changedetection.io：{e.reason}") from e
    except ConnectionError as e:
        raise CdioConnectionError(f"连接中断：{e}") from e


class ChangeDetectionClient:
    """Thin, strict client. GET requests are retried on connection errors, timeouts and 5xx."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 30.0,
                 retries: int = 2, backoff: float = 0.5, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.transport = transport or _urllib_transport
        self.sleep = sleep
        self.requests_made = 0

    @classmethod
    def from_env(cls, **kw) -> ChangeDetectionClient:
        return cls(os.getenv("TECHSIGNAL_CDIO_URL", ""), api_key=os.getenv("TECHSIGNAL_CDIO_API_KEY") or None,
                   timeout=float(os.getenv("TECHSIGNAL_CDIO_TIMEOUT", "30")), **kw)

    # -- core -------------------------------------------------------------------------------

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                 expect_json: bool = True):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempts = 1 + (self.retries if method == "GET" else 0)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            if attempt:
                self.sleep(self.backoff * (2 ** (attempt - 1)))
            self.requests_made += 1
            try:
                resp = self.transport(method, url, headers, data, self.timeout)
            except (CdioConnectionError, CdioTimeoutError) as e:
                last_exc = e
                continue
            if resp.status >= 500:
                last_exc = CdioHTTPError(f"服务端错误 {resp.status}：{method} {path}：{_snippet(resp.body)}", resp.status)
                continue
            return self._handle(resp, method, path, expect_json)
        raise last_exc

    @staticmethod
    def _handle(resp: HTTPResponse, method: str, path: str, expect_json: bool):
        if resp.status in (401, 403):
            raise CdioAuthError(f"鉴权失败（{resp.status}），请检查 API key：{method} {path}", resp.status)
        if resp.status == 404:
            raise CdioNotFoundError(f"不存在：{method} {path}：{_snippet(resp.body)}", resp.status)
        if resp.status >= 400:
            raise CdioHTTPError(f"请求被拒绝 {resp.status}：{method} {path}：{_snippet(resp.body)}", resp.status)
        if not expect_json:
            return resp.body
        try:
            return json.loads(resp.body.decode("utf-8")) if resp.body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise CdioProtocolError(f"响应不是 JSON：{method} {path}：{_snippet(resp.body)}", resp.status) from e

    # -- endpoints --------------------------------------------------------------------------

    def system_info(self) -> dict:
        return self._expect_dict(self._request("GET", "/api/v1/systeminfo"), "systeminfo")

    def list_watches(self, tag: str | None = None) -> dict:
        return self._expect_dict(self._request("GET", "/api/v1/watch", {"tag": tag}), "watch list")

    def get_watch(self, uuid: str) -> dict:
        return self._expect_dict(self._request("GET", f"/api/v1/watch/{uuid}"), "watch")

    def create_watch(self, url: str, title: str | None = None, tag: str | None = None, **fields) -> str:
        body = {"url": url, **({"title": title} if title else {}), **({"tag": tag} if tag else {}), **fields}
        res = self._request("POST", "/api/v1/watch", body=body)
        if not isinstance(res, dict) or not isinstance(res.get("uuid"), str):
            raise CdioProtocolError(f"创建监测项的响应缺少 uuid：{res!r}")
        return res["uuid"]

    def update_watch(self, uuid: str, **fields) -> None:
        self._request("PUT", f"/api/v1/watch/{uuid}", body=fields)

    def delete_watch(self, uuid: str) -> None:
        self._request("DELETE", f"/api/v1/watch/{uuid}")

    def recheck(self, uuid: str) -> None:
        self._request("GET", f"/api/v1/watch/{uuid}", {"recheck": "1"})

    def get_snapshot(self, uuid: str, timestamp: str) -> str:
        """Text of one stored snapshot (GET /api/v1/watch/{uuid}/history/{timestamp})."""
        body = self._request("GET", f"/api/v1/watch/{uuid}/history/{timestamp}", expect_json=False)
        return body.decode("utf-8", errors="replace")

    def history(self, uuid: str) -> dict:
        return self._expect_dict(self._request("GET", f"/api/v1/watch/{uuid}/history"), "history")

    def get_changes_report(self, from_ts: int, to_ts: int | None = None, tag: str | None = None,
                           uuids: list[str] | None = None, mode: str = "net_change", context: int = 1,
                           max_lines_per_watch: int = 200, page_size: int = 50, max_pages: int = 100) -> dict:
        """Every page of GET /api/v1/changes?format=json, merged into one ChangesReport.

        The merged report keeps the server's own fields; offset becomes 0 and limit the number of
        watches returned. It is incomplete=True if any page said so (the server's time budget).
        """
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size 必须在 1 到 1000 之间")
        params = {"from": int(from_ts), "to": int(to_ts) if to_ts is not None else None, "tag": tag,
                  "uuids": ",".join(uuids) if uuids else None, "mode": mode, "context": context,
                  "max_lines_per_watch": max_lines_per_watch, "format": "json", "limit": page_size}
        merged: dict | None = None
        watches: list[dict] = []
        seen: set[str] = set()
        offset = 0
        for page in range(max_pages):
            res = self._request("GET", "/api/v1/changes", {**params, "offset": offset})
            if not isinstance(res, dict) or not isinstance(res.get("watches"), list) or "total" not in res:
                raise CdioProtocolError(f"/api/v1/changes 的响应不符合 ChangesReport 契约：{_snippet(json.dumps(res))}")
            if merged is None:
                merged = {k: v for k, v in res.items() if k != "watches"}
            elif res["total"] != merged["total"]:
                # watches changed between pages - the merged view would be inconsistent
                raise CdioProtocolError(f"分页期间变化总数改变（{merged['total']} → {res['total']}），请重试")
            merged["incomplete"] = bool(merged.get("incomplete")) or bool(res.get("incomplete"))
            for w in res["watches"]:
                if w.get("uuid") in seen:
                    raise CdioProtocolError(f"分页结果中出现重复的监测项 {w.get('uuid')}")
                seen.add(w.get("uuid"))
                watches.append(w)
            offset += len(res["watches"])
            if not res["watches"] or offset >= res["total"]:
                break
        else:
            raise CdioProtocolError(f"超过最大页数 {max_pages}，仍未取完")
        merged["watches"] = watches
        merged["offset"] = 0
        merged["limit"] = len(watches)
        return merged

    @staticmethod
    def _expect_dict(value, what: str) -> dict:
        if not isinstance(value, dict):
            raise CdioProtocolError(f"{what} 响应应为 JSON 对象，实际为 {type(value).__name__}")
        return value


def _snippet(body: bytes, n: int = 200) -> str:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = repr(body)
    return text[:n]
