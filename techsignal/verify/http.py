"""Guarded HTTP fetch for verification tools: allowlist on every hop, timeout, size cap, retries."""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .policy import VerifyPolicy, check_url


class ToolBlocked(RuntimeError):
    pass


class ToolTimeout(RuntimeError):
    pass


class ToolHTTPError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class FetchResult:
    status: int
    text: str
    final_url: str
    truncated: bool
    attempts: int


def _redirect_guard(policy: VerifyPolicy):
    class Guarded(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            reason = check_url(newurl, policy)
            if reason:
                raise ToolBlocked(f"重定向目标被拦截：{reason}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return Guarded()


class GuardedHTTP:
    def __init__(self, policy: VerifyPolicy, sleep=time.sleep):
        self.policy = policy
        self.sleep = sleep
        self._opener = urllib.request.build_opener(_redirect_guard(policy))

    def get(self, url: str, headers: dict | None = None) -> FetchResult:
        reason = check_url(url, self.policy)
        if reason:
            raise ToolBlocked(reason)
        attempts = 0
        last: Exception | None = None
        for attempt in range(1 + self.policy.retries):
            if attempt:
                self.sleep(0.5 * attempt)
            attempts += 1
            req = urllib.request.Request(url, headers={"User-Agent": "TechSignal-verify/0.1", **(headers or {})})
            try:
                with self._opener.open(req, timeout=self.policy.timeout_seconds) as resp:  # noqa: S310 - guarded
                    raw = resp.read(self.policy.max_response_bytes + 1)
                    truncated = len(raw) > self.policy.max_response_bytes
                    return FetchResult(resp.status, raw[: self.policy.max_response_bytes].decode("utf-8", "replace"),
                                       resp.geturl(), truncated, attempts)
            except ToolBlocked:
                raise
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    last = ToolHTTPError(f"HTTP {e.code}", e.code)
                    continue
                err = ToolHTTPError(f"HTTP {e.code}", e.code)
                err.attempts = attempts
                raise err from None
            except TimeoutError as e:
                last = ToolTimeout(f"超时（{self.policy.timeout_seconds}s）")
                last.__cause__ = e
                continue
            except urllib.error.URLError as e:
                if isinstance(e.reason, (TimeoutError, socket.timeout)):
                    last = ToolTimeout(f"超时（{self.policy.timeout_seconds}s）")
                else:
                    last = ToolHTTPError(f"连接失败：{e.reason}")
                continue
        last.attempts = attempts
        raise last
