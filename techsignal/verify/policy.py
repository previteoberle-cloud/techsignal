"""Verification policy: which public sources tools may touch, and the budget/limits for each run.

Every URL a tool requests - including every redirect hop - must pass check_url(). There is no
wildcard and no suffix matching: an entry names one exact host (and optionally a port and a path
prefix). IP-literal hosts are refused unless an entry names that exact IP (used only for the local
changedetection.io instance the run itself was given via --cdio-url).
"""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PolicyError(ValueError):
    pass


class AllowEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheme: Literal["https", "http"] = "https"
    host: str
    port: int | None = None
    path_prefix: str = "/"
    note: str = ""


class VerifyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: list[AllowEntry]
    max_calls_per_item: int = Field(3, ge=1, le=5)
    max_calls_per_run: int = Field(10, ge=1, le=50)
    timeout_seconds: float = Field(15.0, gt=0, le=60)
    retries: int = Field(1, ge=0, le=3)
    max_response_bytes: int = Field(2_000_000, ge=1_000, le=20_000_000)
    max_findings_per_call: int = Field(8, ge=1, le=50)
    # Name of an environment variable holding a GitHub token for api.github.com (optional, e.g.
    # GITHUB_TOKEN inside GitHub Actions). The value is never logged or written to the trace.
    github_token_env: str | None = None
    allow_cdio_tool: bool = True

    @classmethod
    def load(cls, path: Path) -> VerifyPolicy:
        try:
            return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise PolicyError(f"补证策略文件无效：{e}") from None

    def with_cdio(self, base_url: str) -> VerifyPolicy:
        """Allow exactly the changedetection.io instance this run was given, nothing else on that host."""
        u = urlsplit(base_url)
        entry = AllowEntry(scheme=u.scheme, host=(u.hostname or "").lower(), port=u.port,
                           path_prefix="/api/v1/", note="本次运行指定的 changedetection.io 实例")
        return self.model_copy(update={"allow": self.allow + [entry]})


_BAD_PATH = re.compile(r"(^|/)\.\.?(/|$)|\\|[\x00-\x1f\x7f]")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def check_url(url: str, policy: VerifyPolicy) -> str | None:
    """None if the URL is allowed, otherwise the reason it is refused."""
    if not isinstance(url, str) or len(url) > 2048:
        return "URL 为空或过长"
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError as e:
        return f"URL 无法解析：{e}"
    if u.scheme not in ("https", "http"):
        return f"不允许的协议 {u.scheme!r}"
    if u.username or u.password or "@" in u.netloc:
        return "URL 中不允许包含凭据"
    host = (u.hostname or "").lower()
    if not host:
        return "URL 缺少主机名"
    if host.rstrip(".") != host:
        return "主机名不允许以点结尾"
    path = u.path or "/"
    decoded = unquote(unquote(path))
    if _BAD_PATH.search(decoded):
        return "路径包含 '..'、反斜杠或控制字符"
    for e in policy.allow:
        if e.host != host or e.scheme != u.scheme:
            continue
        if e.port != port and not (e.port is None and port in (None, 443 if e.scheme == "https" else 80)):
            continue
        if not path.startswith(e.path_prefix):
            continue
        if _is_ip(host) and e.host != host:
            continue
        return None
    if _is_ip(host):
        return f"IP 地址 {host} 不在白名单中"
    return f"{u.scheme}://{host}{':' + str(port) if port else ''}{path[:60]} 不在补证白名单中"
