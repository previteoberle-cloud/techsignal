"""Verification tools. Each performs a real request and returns DATA only.

Tool output is never interpreted as instructions: the engine only (a) searches it for lines, with
fixed term/pattern matching, and (b) runs the same deterministic rules over the matched lines. Lines
that look like instructions to an AI system are flagged and excluded from re-assessment.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from ..cdio_client import CdioError, ChangeDetectionClient
from .http import GuardedHTTP, ToolBlocked, ToolHTTPError, ToolTimeout
from .policy import VerifyPolicy, check_url

_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)?\s*(instructions|rules|prompts?)"
    r"|system\s*prompt|you\s+are\s+now|<\|im_start\|>|\bassistant\s*:|\bsystem\s*:"
    r"|忽略(之前|以上|上述|所有)|你现在是|执行以下(指令|命令)|系统提示",
    re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text or ""))


@dataclass
class Finding:
    lines: list[str]               # verbatim lines from the source (table header first when relevant)
    locator: str                   # where in the source, e.g. "line 10"
    flagged_injection: bool = False


@dataclass
class ToolOutcome:
    status: str                    # ok | not_found | error | timeout | blocked | unavailable
    url: str | None = None
    http_status: int | None = None
    attempts: int = 0
    findings: list[Finding] = field(default_factory=list)
    detail: str = ""               # short factual note (e.g. commit message summary), data only
    error: str | None = None
    truncated: bool = False
    # Where the content was originally published, when that differs from the URL the tool called
    # (a changedetection.io snapshot is a stored copy of the watched page). Evidence is graded by this.
    source_url: str | None = None


def search_lines(text: str, terms: list[str], patterns: list[str], min_terms: int, limit: int) -> list[Finding]:
    """Lines matching any pattern, or containing at least `min_terms` of `terms` (case-insensitive).

    Ranked by (pattern hit, number of distinct terms), best `limit` kept, then returned in document
    order. For markdown table rows the table's header row is put in front so the row can be read."""
    lines = text.splitlines()
    low_terms = [t.lower() for t in terms if t]
    need = min(min_terms, len(low_terms)) if low_terms else None
    scored = []
    for i, line in enumerate(lines):
        hit_pattern = any(re.search(p, line) for p in patterns)
        hits = sum(1 for t in low_terms if t in line.lower())
        if hit_pattern or (need and hits >= need):
            scored.append((int(hit_pattern), hits, -i))
    keep = sorted(-x[2] for x in sorted(scored, reverse=True)[:limit])
    found: list[Finding] = []
    for i in keep:
        line = lines[i]
        block = [line.strip()]
        if line.lstrip().startswith("|"):
            j = i
            while j > 0 and lines[j - 1].lstrip().startswith("|"):
                j -= 1
            if j < i and not re.fullmatch(r"\|?[\s:\-|]+\|?", lines[j].strip()):
                block = [lines[j].strip(), line.strip()]
        found.append(Finding(block, f"line {i + 1}", flagged_injection=looks_like_injection(line)))
    return found


class CdioSnapshotSearch:
    """Search a stored snapshot of a watch in the run's changedetection.io (GET .../history/{ts})."""
    name = "cdio_snapshot_search"

    def __init__(self, client: ChangeDetectionClient, policy: VerifyPolicy):
        self.client, self.policy = client, policy

    def run(self, watch_uuid: str, timestamp: str, terms: list[str], patterns: list[str],
            source_url: str | None = None) -> ToolOutcome:
        path = f"/api/v1/watch/{watch_uuid}/history/{timestamp}"
        url = self.client.base_url + path
        reason = check_url(url, self.policy)
        if reason:
            return ToolOutcome("blocked", url=url, error=reason)
        try:
            before = self.client.requests_made
            text = self.client.get_snapshot(watch_uuid, timestamp)
            attempts = self.client.requests_made - before
        except CdioError as e:
            status = "not_found" if getattr(e, "status", None) == 404 else "error"
            return ToolOutcome(status, url=url, http_status=getattr(e, "status", None), error=str(e)[:200])
        findings = search_lines(text, terms, patterns, 2, self.policy.max_findings_per_call)
        return ToolOutcome("ok" if findings else "not_found", url=url, http_status=200, attempts=attempts,
                           findings=findings, source_url=source_url,
                           detail=f"changedetection.io 保存的快照（{timestamp}），原始页面 {source_url}" if source_url else "")


def _http_outcome(http: GuardedHTTP, url: str, headers: dict | None = None):
    try:
        return http.get(url, headers), None
    except ToolBlocked as e:
        return None, ToolOutcome("blocked", url=url, error=str(e))
    except ToolTimeout as e:
        return None, ToolOutcome("timeout", url=url, error=str(e), attempts=getattr(e, "attempts", 0))
    except ToolHTTPError as e:
        status = "not_found" if e.status == 404 else "error"
        return None, ToolOutcome(status, url=url, http_status=e.status, error=str(e),
                                 attempts=getattr(e, "attempts", 0))


class GitHubFileSearch:
    """Fetch one file of a public GitHub repository at a fixed ref (raw.githubusercontent.com) and search it."""
    name = "github_file_search"

    def __init__(self, http: GuardedHTTP, base: str = "https://raw.githubusercontent.com"):
        self.http, self.base = http, base.rstrip("/")

    def run(self, owner: str, repo: str, ref: str, path: str, terms: list[str], patterns: list[str]) -> ToolOutcome:
        url = f"{self.base}/{owner}/{repo}/{ref}/{path.lstrip('/')}"
        res, failed = _http_outcome(self.http, url)
        if failed:
            return failed
        findings = search_lines(res.text, terms, patterns, 2, self.http.policy.max_findings_per_call)
        return ToolOutcome("ok" if findings else "not_found", url=res.final_url, http_status=res.status,
                           attempts=res.attempts, findings=findings, truncated=res.truncated)


class GitHubCommitInfo:
    """Official commit metadata from the public GitHub REST API (message, date, changed files)."""
    name = "github_commit_info"

    def __init__(self, http: GuardedHTTP, base: str = "https://api.github.com"):
        self.http, self.base = http, base.rstrip("/")

    def run(self, owner: str, repo: str, sha: str) -> ToolOutcome:
        url = f"{self.base}/repos/{owner}/{repo}/commits/{sha}"
        headers = {"Accept": "application/vnd.github+json"}
        env = self.http.policy.github_token_env
        if env and os.getenv(env):
            headers["Authorization"] = f"Bearer {os.getenv(env)}"
        res, failed = _http_outcome(self.http, url, headers)
        if failed:
            if failed.http_status == 403:
                failed.error = "HTTP 403（GitHub API 拒绝或受环境网络策略限制）"
            return failed
        try:
            data = json.loads(res.text)
            message = data["commit"]["message"]
            date = data["commit"]["committer"]["date"]
            files = [f.get("filename", "") for f in data.get("files", [])][:20]
        except (ValueError, KeyError, TypeError) as e:
            return ToolOutcome("error", url=url, http_status=res.status, error=f"响应格式不符：{e}")
        lines = [ln.strip() for ln in message.splitlines() if ln.strip()][:10]
        finding = Finding(lines, f"commit {sha[:10]} ({date})", flagged_injection=looks_like_injection(message))
        return ToolOutcome("ok", url=url, http_status=res.status, attempts=res.attempts, findings=[finding],
                           detail=f"提交时间 {date}；改动文件：{', '.join(files) or '未返回'}")


def now_ms() -> float:
    return time.monotonic() * 1000
