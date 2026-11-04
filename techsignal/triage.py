"""Deterministic triage: noise removal, duplicate detection, evidence construction.

No model is involved here. Every decision carries a machine-readable reason code.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .models import ChangeEvent, Evidence, FilteredEvent, SourceProfile, short_hash

# Lines that change on every crawl without carrying information.
_NOISE_PATTERNS = [
    re.compile(r"^\d+\s*(秒|分钟|小时|天|周)前$"),
    re.compile(r"^\d+\s+(second|minute|hour|day|week)s?\s+ago$", re.I),
    re.compile(r"^(更新于|发布于|最后更新|updated|last updated)[:：]?\s*[\d\-/: T]+$", re.I),
    re.compile(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(\s+\d{1,2}:\d{2}(:\d{2})?)?$"),
    re.compile(r"^(阅读|浏览|访问量|浏览量|点赞|评论|views?|visitors?)[:：]?\s*[\d,\.]+\s*(次|人)?$", re.I),
    re.compile(r"^[\d,\.\s]+$"),
    re.compile(r"^(©|copyright).{0,40}$", re.I),
]


def normalize(line: str) -> str:
    line = unicodedata.normalize("NFKC", line).strip().lower()
    return re.sub(r"[\s　]+", " ", line)


def is_noise_line(line: str) -> bool:
    s = normalize(line)
    if not s or not re.search(r"\w", s):
        return True
    return any(p.match(s) for p in _NOISE_PATTERNS)


@dataclass
class TriageResult:
    meaningful_added: list[str] = field(default_factory=list)
    meaningful_removed: list[str] = field(default_factory=list)
    dropped_noise: list[str] = field(default_factory=list)
    dropped_moved: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.meaningful_added and not self.meaningful_removed


def clean_lines(event: ChangeEvent) -> TriageResult:
    """Drop lines that only moved (present on both sides) and lines that are pure noise."""
    res = TriageResult()
    added_norm = {normalize(x) for x in event.added_lines}
    removed_norm = {normalize(x) for x in event.removed_lines}
    moved = added_norm & removed_norm
    for src, dst in ((event.added_lines, res.meaningful_added), (event.removed_lines, res.meaningful_removed)):
        for line in src:
            n = normalize(line)
            if n in moved:
                res.dropped_moved.append(line)
            elif is_noise_line(line):
                res.dropped_noise.append(line)
            else:
                dst.append(line.strip())
    return res


def fingerprint(t: TriageResult) -> str:
    """Content identity, independent of which page it appeared on."""
    a = "\n".join(sorted(normalize(x) for x in t.meaningful_added))
    r = "\n".join(sorted(normalize(x) for x in t.meaningful_removed))
    return short_hash("added", a, "removed", r, length=16)


def pre_filter(event: ChangeEvent) -> FilteredEvent | None:
    """Reasons to skip an event before looking at its content."""
    if event.watch_error and not event.raw_diff.strip():
        return FilteredEvent(event_id=event.event_id, watch_uuid=event.watch_uuid, title=event.title,
                             reason_code="watch_error",
                             reason_zh=f"监测项最近一次检查失败且无可用差异：{event.watch_error}")
    if event.no_net_difference or not event.raw_diff.strip():
        return FilteredEvent(event_id=event.event_id, watch_uuid=event.watch_uuid, title=event.title,
                             reason_code="no_net_difference",
                             reason_zh=f"期间内有 {event.changes} 次变化，但首尾无净差异")
    return None


def build_evidence(event: ChangeEvent, t: TriageResult, source: SourceProfile) -> Evidence:
    fp = fingerprint(t)
    return Evidence(
        evidence_id="EV-" + short_hash(event.event_id, fp),
        event_id=event.event_id,
        source_url=event.url,
        source_domain=event.domain,
        source_name=source.name,
        source_type=source.source_type,
        grade=source.grade,
        baseline_at=event.from_timestamp,
        observed_at=event.to_timestamp,
        excerpt_added=t.meaningful_added,
        excerpt_removed=t.meaningful_removed,
        content_fingerprint=fp,
        truncated=event.truncated,
        cdio_watch_uuid=event.watch_uuid,
        cdio_history_path=event.cdio_history_path,
        cdio_difference_path=event.cdio_difference_path,
    )
