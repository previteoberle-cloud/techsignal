"""TechSignal information collector: public sources -> deduplicated, searchable, exportable items.

A *task* (e.g. "AI 芯片行业动态") has keywords, tags and public source URLs. "立即采集" fetches every
source once:
  - RSS 2.0 / RSS 1.0 (RDF) / Atom feeds: one item per entry;
  - web pages: the first fetch records the page; later fetches add an item only when the visible text
    changed, with the added lines as excerpt. Pages can optionally be fetched through the user's own
    changedetection.io (cdio_* arguments), reusing the existing client.
Items are deduplicated by a stable key (feed guid/id, else link, else title+date) and by a content
fingerprint, so repeated collections only append genuinely new items. A failing source keeps all
history and records the reason.

Storage (local files, survives restarts):
    <root>/                     TECHSIGNAL_COLLECTOR_ROOT, default ~/.techsignal/collector
      tasks/t-<12 hex>/
        task.json               task definition
        items.jsonl             collected items, append-only
        status.json             last attempt / success / error per source
        pages/<source id>.txt   last seen text of page sources
No paid API and no model is used.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .projects import (
    ProjectError,
    atomic_write,
    check_public_url,
    clean_text,
    file_lock,
    now_iso,
    split_items,
)

ROOT_ENV = "TECHSIGNAL_COLLECTOR_ROOT"
TASK_ID_RE = re.compile(r"^t-[0-9a-f]{12}$")
USER_AGENT = "TechSignal-Collector/0.7 (+https://github.com/previteoberle-cloud/techsignal)"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
MAX_SOURCES = 30
SUMMARY_CHARS = 400


class CollectorError(ProjectError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    source_id: str
    url: str
    name: str = ""
    kind: Literal["auto", "feed", "page"] = "auto"


class Task(_Strict):
    task_id: str
    name: str
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    created_at: str
    updated_at: str


class Item(_Strict):
    item_id: str
    task_id: str
    source_id: str
    source_name: str
    source_url: str
    kind: Literal["feed_entry", "page_snapshot", "page_change"]
    title: str
    url: str
    published_at: str | None = None
    fetched_at: str
    summary: str = ""
    fingerprint: str
    keyword_hits: list[str] = Field(default_factory=list)
    via: Literal["direct", "changedetection.io"] = "direct"


class SourceStatus(_Strict):
    last_attempt: str | None = None
    last_success: str | None = None
    ok: bool | None = None
    error: str = ""
    detected_kind: str = ""
    last_new: int = 0
    last_duplicates: int = 0


class CollectResult(_Strict):
    task_id: str
    started_at: str
    new_items: int
    duplicates: int
    failed_sources: int
    per_source: dict[str, dict]


def _h(*parts: str, n: int = 16) -> str:
    d = hashlib.sha256()
    for p in parts:
        d.update((p or "").encode("utf-8"))
        d.update(b"\x00")
    return d.hexdigest()[:n]


def default_root() -> Path:
    env = os.getenv(ROOT_ENV)
    return Path(env).expanduser() if env else Path.home() / ".techsignal" / "collector"


# ---------------------------------------------------------------------------
# Fetching (stdlib only; public addresses only, also after redirects)
# ---------------------------------------------------------------------------

class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl, allow_private=self.allow_private)    # raises on a private target
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, allow_private: bool = False, timeout: float = TIMEOUT) -> tuple[bytes, str]:
    """(body, content-type). Raises CollectorError with a Chinese reason."""
    try:
        check_public_url(url, allow_private=allow_private)
    except ProjectError as e:
        raise CollectorError(str(e)) from None
    opener = urllib.request.build_opener(_CheckedRedirect(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES + 1)
            ctype = resp.headers.get("Content-Type", "")
    except ProjectError as e:
        raise CollectorError(f"重定向到了不允许的地址：{e}") from None
    except urllib.error.HTTPError as e:
        raise CollectorError(f"来源返回 HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise CollectorError(f"无法访问来源：{reason}") from None
    if len(body) > MAX_BYTES:
        raise CollectorError("来源内容超过 5 MB，已放弃")
    return body, ctype


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8", "gb18030"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head|noscript|title)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    lines = [re.sub(r"[ \t　\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def looks_like_feed(body: bytes) -> bool:
    head = body[:2048].lstrip().lower()
    return head.startswith(b"<?xml") and any(t in body[:4096].lower() for t in (b"<rss", b"<feed", b"<rdf:rdf")) \
        or head.startswith((b"<rss", b"<feed", b"<rdf:rdf"))


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:40]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(body: bytes) -> tuple[str, list[dict]]:
    """(feed title, entries[{key, title, url, published, summary}]). RSS 2.0, RSS 1.0/RDF and Atom.
    Documents with DOCTYPE/ENTITY declarations are refused (entity-expansion attacks)."""
    if re.search(rb"<!DOCTYPE|<!ENTITY", body[:65536], re.I):
        raise CollectorError("订阅源包含 DOCTYPE/ENTITY 声明，出于安全原因不解析")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise CollectorError(f"订阅源 XML 格式错误：{e}") from None
    kind = _local(root.tag)
    entries, feed_title = [], ""
    if kind == "feed":                                          # Atom
        for ch in root:
            if _local(ch.tag) == "title":
                feed_title = clean_text(ch.text or "")
        nodes = [e for e in root if _local(e.tag) == "entry"]
    else:                                                       # rss / rdf
        channel = next((c for c in root if _local(c.tag) == "channel"), root)
        for ch in channel:
            if _local(ch.tag) == "title":
                feed_title = clean_text(ch.text or "")
        nodes = [e for e in root.iter() if _local(e.tag) == "item"]
    for e in nodes:
        f = {}
        link = ""
        for ch in e:
            name = _local(ch.tag)
            if name == "link":
                href = ch.get("href")
                if href and (ch.get("rel") in (None, "alternate")) and not link:
                    link = href
                elif not href and (ch.text or "").strip():
                    link = ch.text.strip()
            elif name not in f:
                f[name] = (ch.text or "").strip() if len(ch) == 0 else ET.tostring(ch, encoding="unicode", method="text")
        title = clean_text(strip_html(f.get("title", ""))).replace("\n", " ")
        summary = strip_html(f.get("description") or f.get("summary") or f.get("content") or f.get("encoded") or "")
        published = _iso(f.get("pubdate") or f.get("published") or f.get("updated") or f.get("date"))
        key = f.get("guid") or f.get("id") or link or f"{title}|{published}"
        if not (title or summary):
            continue
        entries.append({"key": key, "title": title or summary[:60], "url": link, "published": published,
                        "summary": summary[:SUMMARY_CHARS]})
    return feed_title, entries


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_text(body: bytes, ctype: str) -> tuple[str, str]:
    """(title, visible text)."""
    text = _decode(body, ctype)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    title = clean_text(html.unescape(m.group(1))) if m else ""
    if "<" in text[:2000] and re.search(r"(?i)<(html|body|div|p)\b", text[:5000]):
        text = strip_html(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return title, "\n".join(lines)


def added_lines(old: str, new: str) -> list[str]:
    before = set(old.splitlines())
    return [ln for ln in new.splitlines() if ln not in before]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def keyword_hits(keywords: list[str], *texts: str) -> list[str]:
    from .rules import entity_pattern
    blob = "\n".join(t or "" for t in texts)
    return [k for k in keywords if entity_pattern(k).search(blob)]


class CollectorStore:
    def __init__(self, root: Path | None = None, allow_private_urls: bool | None = None):
        self.root = Path(root) if root is not None else default_root()
        self.allow_private_urls = (os.getenv("TECHSIGNAL_ALLOW_PRIVATE_URLS") == "1") if allow_private_urls is None \
            else bool(allow_private_urls)

    def _dir(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise CollectorError("任务编号无效")
        base = (self.root / "tasks").resolve()
        d = (base / task_id).resolve()
        if d.parent != base:
            raise CollectorError("任务编号无效")
        return d

    # -- tasks ----------------------------------------------------------------------------

    def _check(self, name: str, keywords: str | list, tags: str | list, sources_text: str,
               kinds: dict | None = None, existing: list[Source] | None = None) -> tuple[str, list, list, list[Source]]:
        problems = []
        name = clean_text(name)
        if not name:
            problems.append("请填写采集任务名称")
        elif len(name) > 60:
            problems.append("任务名称最多 60 个字符")
        kws = split_items(keywords) if isinstance(keywords, str) else [clean_text(k) for k in keywords if clean_text(k)]
        tgs = split_items(tags) if isinstance(tags, str) else [clean_text(t) for t in tags if clean_text(t)]
        if len(kws) > 30 or any(len(k) > 40 for k in kws):
            problems.append("关键词最多 30 个，每个不超过 40 个字符")
        old = {s.url: s for s in existing or []}
        sources, seen = [], set()
        for line in (sources_text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            url, _, label = line.partition(" ")
            try:
                url = check_public_url(url, allow_private=self.allow_private_urls)
            except ProjectError as e:
                problems.append(str(e))
                continue
            if url in seen:
                continue
            seen.add(url)
            prev = old.get(url)
            kind = (kinds or {}).get(url) or (prev.kind if prev else "auto")
            sources.append(Source(source_id=prev.source_id if prev else "s-" + _h(url, n=10), url=url,
                                  name=clean_text(label)[:60], kind=kind))
        if not sources:
            problems.append("请至少填写一个公开信息来源网址")
        if len(sources) > MAX_SOURCES:
            problems.append(f"每个任务最多 {MAX_SOURCES} 个来源")
        if problems:
            raise CollectorError(f"有 {len(problems)} 处需要修改", problems)
        return name, kws, tgs, sources

    def create_task(self, name: str, keywords="", tags="", sources_text: str = "", kinds: dict | None = None) -> Task:
        name, kws, tgs, sources = self._check(name, keywords, tags, sources_text, kinds)
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        with file_lock(self.root / ".lock"):
            if any(t.name.casefold() == name.casefold() for t in self.tasks()):
                raise CollectorError(f"已存在同名采集任务「{name}」", [f"已存在同名采集任务「{name}」"])
            tid = "t-" + secrets.token_hex(6)
            ts = now_iso()
            task = Task(task_id=tid, name=name, keywords=kws, tags=tgs, sources=sources, created_at=ts, updated_at=ts)
            atomic_write(self._dir(tid) / "task.json", task.model_dump_json(indent=2) + "\n")
        return task

    def update_task(self, task_id: str, name: str, keywords="", tags="", sources_text: str = "",
                    kinds: dict | None = None) -> Task:
        cur = self.get(task_id)
        name, kws, tgs, sources = self._check(name, keywords, tags, sources_text, kinds, cur.sources)
        with file_lock(self._dir(task_id) / ".lock"):
            task = cur.model_copy(update={"name": name, "keywords": kws, "tags": tgs, "sources": sources,
                                          "updated_at": now_iso()})
            atomic_write(self._dir(task_id) / "task.json", task.model_dump_json(indent=2) + "\n")
        return task

    def get(self, task_id: str) -> Task:
        f = self._dir(task_id) / "task.json"
        if not f.exists():
            raise CollectorError("采集任务不存在")
        try:
            return Task.model_validate_json(f.read_text(encoding="utf-8"))
        except ValidationError as e:
            raise CollectorError(f"任务文件损坏：{e.errors()[0]['msg']}") from None

    def tasks(self) -> list[Task]:
        d = self.root / "tasks"
        out = []
        for p in sorted(d.glob("t-*/task.json")) if d.exists() else []:
            try:
                out.append(self.get(p.parent.name))
            except CollectorError:
                continue
        return sorted(out, key=lambda t: t.created_at)

    # -- items and status -----------------------------------------------------------------

    def items(self, task_id: str | None = None) -> list[Item]:
        ids = [task_id] if task_id else [t.task_id for t in self.tasks()]
        out = []
        for tid in ids:
            f = self._dir(tid) / "items.jsonl"
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        out.append(Item.model_validate_json(line))
                    except ValidationError:
                        continue
        return sorted(out, key=lambda i: (i.published_at or i.fetched_at, i.fetched_at), reverse=True)

    def status(self, task_id: str) -> dict[str, SourceStatus]:
        f = self._dir(task_id) / "status.json"
        if not f.exists():
            return {}
        raw = json.loads(f.read_text(encoding="utf-8"))
        return {k: SourceStatus.model_validate(v) for k, v in raw.items()}

    # -- collecting -----------------------------------------------------------------------

    def collect(self, task_id: str, cdio_client=None, fetch=None) -> CollectResult:
        """Fetch every source of the task once and append only new items.
        cdio_client: optional ChangeDetectionClient; page sources are then read from changedetection.io
        snapshots (a watch is created for the URL if none exists). fetch: test hook replacing http_get."""
        task = self.get(task_id)
        fetch = fetch or (lambda url: http_get(url, allow_private=self.allow_private_urls))
        d = self._dir(task_id)
        started = now_iso()
        # A run can take minutes (sources x 20 s); only a lock older than an hour counts as abandoned.
        with file_lock(d / ".collect-lock", timeout=2, stale=3600):
            existing = self.items(task_id)
            ids = {i.item_id for i in existing}
            fps = {i.fingerprint for i in existing}
            status = self.status(task_id)
            new: list[Item] = []
            # Page snapshots are written only AFTER items.jsonl (consistency fix): if archiving fails,
            # the old snapshot stays and the next run finds the same change again. If the process dies
            # between the two writes, the next run rebuilds the same change item (same id, from the
            # text hash) and dedup skips it - nothing is lost and nothing is archived twice.
            pending_pages: list[tuple[Path, str]] = []
            per, dup_total, failed = {}, 0, 0
            for src in task.sources:
                st = status.get(src.source_id, SourceStatus())
                st.last_attempt = now_iso()
                try:
                    found, kind, via = self._fetch_source(task, src, fetch, cdio_client, d, pending_pages)
                    st.detected_kind = kind
                    n_new = n_dup = 0
                    for it in found:
                        if it.item_id in ids or it.fingerprint in fps:
                            n_dup += 1
                            continue
                        ids.add(it.item_id)
                        fps.add(it.fingerprint)
                        new.append(it)
                        n_new += 1
                    st.ok, st.error, st.last_success, st.last_new, st.last_duplicates = True, "", now_iso(), n_new, n_dup
                    dup_total += n_dup
                    per[src.source_id] = {"ok": True, "new": n_new, "duplicates": n_dup, "kind": kind, "via": via}
                except CollectorError as e:
                    failed += 1
                    st.ok, st.error, st.last_new, st.last_duplicates = False, str(e), 0, 0
                    per[src.source_id] = {"ok": False, "error": str(e)}
                status[src.source_id] = st
            if new:
                f = d / "items.jsonl"
                old = f.read_text(encoding="utf-8") if f.exists() else ""
                atomic_write(f, old + "".join(i.model_dump_json() + "\n" for i in new))
            for path, text in pending_pages:
                atomic_write(path, text)
            atomic_write(d / "status.json", json.dumps({k: v.model_dump() for k, v in status.items()},
                                                       ensure_ascii=False, indent=2) + "\n")
        return CollectResult(task_id=task_id, started_at=started, new_items=len(new), duplicates=dup_total,
                             failed_sources=failed, per_source=per)

    def _item(self, task, src, kind, key, title, url, published, summary, via="direct") -> Item:
        return Item(item_id="i-" + _h(task.task_id, src.source_id, key), task_id=task.task_id, source_id=src.source_id,
                    source_name=src.name or src.url, source_url=src.url, kind=kind, title=title[:300], url=url or src.url,
                    published_at=published, fetched_at=now_iso(), summary=summary[:SUMMARY_CHARS],
                    fingerprint=_h(_norm(title), _norm(summary)),
                    keyword_hits=keyword_hits(task.keywords, title, summary), via=via)

    def _fetch_source(self, task, src, fetch, cdio_client, d, pending) -> tuple[list[Item], str, str]:
        if src.kind == "page" and cdio_client is not None:
            title, text = _cdio_page(cdio_client, src, task)
            return self._page_items(task, src, title, text, d, pending, via="changedetection.io"), "page", \
                "changedetection.io"
        body, ctype = fetch(src.url)
        is_feed = src.kind == "feed" or (src.kind == "auto" and looks_like_feed(body))
        if is_feed:
            _, entries = parse_feed(body)
            return [self._item(task, src, "feed_entry", e["key"], e["title"], e["url"], e["published"], e["summary"])
                    for e in entries], "feed", "direct"
        title, text = page_text(body, ctype)
        return self._page_items(task, src, title, text, d, pending), "page", "direct"

    def _page_items(self, task, src, title, text, d, pending, via="direct") -> list[Item]:
        snap = d / "pages" / f"{src.source_id}.txt"
        old = snap.read_text(encoding="utf-8") if snap.exists() else None
        if old == text:
            return []
        pending.append((snap, text))           # written by collect() after the items are archived
        first = next(iter(text.splitlines()), "")[:80]
        name = title or src.name or first or src.url
        if old is None:
            return [self._item(task, src, "page_snapshot", "snapshot:" + _h(text), f"{name}", src.url, None,
                               text[:SUMMARY_CHARS], via)]
        added = added_lines(old, text)
        if not added:          # only deletions or reordering: recorded as the new baseline, not news
            return []
        return [self._item(task, src, "page_change", "change:" + _h(text), f"{name}（页面更新）", src.url, None,
                           "\n".join(added)[:SUMMARY_CHARS], via)]


def _cdio_page(client, src: Source, task: Task, wait: float = 60) -> tuple[str, str]:
    """Read the page through changedetection.io: find or create its watch, recheck, wait, read the latest
    snapshot. Reuses the R6 client and the check-completion rule (check_count, not last_checked)."""
    from .cdio_client import CdioError
    from .project_monitor import _check_finished
    try:
        watches = client.list_watches()
        uuid = next((u for u, w in watches.items() if isinstance(w, dict) and w.get("url") == src.url), None)
        if uuid is None:
            uuid = client.create_watch(src.url, title=f"TechSignal 采集｜{task.name}｜{src.name or src.url}"[:200])
        before = client.get_watch(uuid)
        client.recheck(uuid)
        deadline = time.monotonic() + wait
        while True:
            now = client.get_watch(uuid)
            if _check_finished(before, now) or time.monotonic() > deadline:
                break
            time.sleep(1)
        if now.get("last_error"):
            raise CollectorError(f"changedetection.io 抓取失败：{now.get('last_error')}")
        keys = sorted(client.history(uuid), key=int)
        if not keys:
            raise CollectorError("changedetection.io 尚未生成快照，请稍后再采集")
        text = client.get_snapshot(uuid, keys[-1])
    except CdioError as e:
        raise CollectorError(f"changedetection.io：{e}") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (src.name or (lines[0][:80] if lines else src.url)), "\n".join(lines)


# ---------------------------------------------------------------------------
# Filtering and export
# ---------------------------------------------------------------------------

def filter_items(items: list[Item], source_ids: list[str] | None = None, query: str = "",
                 only_keyword_hits: bool = False, since: str | None = None, until: str | None = None,
                 keyword: str | None = None) -> list[Item]:
    q = _norm(query)
    out = []
    for it in items:
        when = (it.published_at or it.fetched_at)[:10]
        if source_ids and it.source_id not in source_ids:
            continue
        if only_keyword_hits and not it.keyword_hits:
            continue
        if keyword and keyword not in it.keyword_hits:
            continue
        if since and when < since:
            continue
        if until and when > until:
            continue
        if q and q not in _norm(f"{it.title}\n{it.summary}\n{it.source_name}"):
            continue
        out.append(it)
    return out


CSV_FIELDS = ["title", "url", "source_name", "published_at", "fetched_at", "keyword_hits", "summary", "kind",
              "source_url", "item_id"]
CSV_HEADER_ZH = ["标题", "原始链接", "来源", "发布时间", "抓取时间", "命中关键词", "摘要", "类型", "来源网址", "条目编号"]


def to_csv(items: list[Item]) -> bytes:
    """UTF-8 with BOM so that Excel on Windows opens Chinese text correctly."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER_ZH)
    for it in items:
        row = it.model_dump()
        row["keyword_hits"] = "、".join(it.keyword_hits)
        w.writerow([row[f] if row[f] is not None else "" for f in CSV_FIELDS])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_markdown(items: list[Item], title: str = "TechSignal 信息采集导出") -> str:
    lines = [f"# {title}", "", f"导出时间：{now_iso()}，共 {len(items)} 条", ""]
    for it in items:
        lines.append(f"## [{it.title}]({it.url})")
        meta = [f"来源：{it.source_name}", f"发布：{it.published_at or '未标注'}", f"抓取：{it.fetched_at}"]
        if it.keyword_hits:
            meta.append("关键词：" + "、".join(it.keyword_hits))
        lines.append("　·　".join(meta))
        if it.summary:
            lines += ["", "> " + it.summary.replace("\n", "\n> ")]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line: python -m techsignal.collector list | collect <task id> | export <task id> --csv/--md
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="techsignal.collector")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("collect")
    c.add_argument("task_id")
    e = sub.add_parser("export")
    e.add_argument("task_id")
    e.add_argument("--csv")
    e.add_argument("--md")
    a = p.parse_args(argv)
    store = CollectorStore()
    try:
        if a.cmd == "list":
            for t in store.tasks():
                print(t.task_id, t.name, f"{len(t.sources)} 个来源", f"{len(store.items(t.task_id))} 条")
        elif a.cmd == "collect":
            r = store.collect(a.task_id)
            print(f"新增 {r.new_items} 条，重复 {r.duplicates} 条，失败来源 {r.failed_sources} 个")
            for sid, x in r.per_source.items():
                print(" ", sid, x)
        else:
            items = store.items(a.task_id)
            if a.csv:
                Path(a.csv).write_bytes(to_csv(items))
            if a.md:
                Path(a.md).write_text(to_markdown(items), encoding="utf-8")
            print(f"已导出 {len(items)} 条")
    except ProjectError as err:
        print(f"失败：{err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
