"""Adapter for changedetection.io's change report.

Contract source (read from this repository, not invented):
  - Endpoint:  GET /api/v1/changes?format=json        changedetectionio/api/Changes.py
  - Builder:   group_changes() / watch_changes()      changedetectionio/changes_report.py
  - Schema:    components.schemas.ChangesReport       docs/api-spec.yaml

Report shape:
  {from, to, mode, total, offset, limit, monitors_checked, changes_total, incomplete,
   watches: [{uuid, title, url, changes, new_in_period, no_net_difference, error,
              from_timestamp, to_timestamp, diff, truncated}]}

`diff` is difflib.unified_diff(..., fromfile=<ISO ts>, tofile=<ISO ts>, n=context, lineterm='')
joined with '\n'. When truncated, a final line "... N more lines, see /api/v1/watch/..." is appended.

R1 parses this offline (fixtures produced by the upstream builder). A live HTTP client against a
running changedetection.io instance is NOT part of R1.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from ..models import ChangeEvent, short_hash


class AdapterError(ValueError):
    """Input is not a ChangesReport we can trust."""


class _CdioWatchChange(BaseModel):
    # extra='ignore': upstream may add fields later; the documented ones are required.
    model_config = ConfigDict(extra="ignore", strict=True)
    uuid: str
    title: str
    url: str
    changes: int
    new_in_period: bool
    no_net_difference: bool
    error: str | None
    from_timestamp: int
    to_timestamp: int
    diff: str
    truncated: bool


class _CdioChangesReport(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    to: int
    mode: str
    total: int
    monitors_checked: int
    changes_total: int
    offset: int
    limit: int
    watches: list[_CdioWatchChange]
    # 'from' is a Python keyword, read separately below
    incomplete: bool = False


_HEADER_RE = re.compile(r"^(---|\+\+\+) \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HUNK_RE = re.compile(r"^@@ .* @@")


def parse_unified_diff(diff: str, truncated: bool = False) -> tuple[list[str], list[str]]:
    """Split a cdio unified diff into (added_lines, removed_lines), in order of appearance."""
    added: list[str] = []
    removed: list[str] = []
    lines = diff.split("\n") if diff else []
    for i, line in enumerate(lines):
        if _HEADER_RE.match(line) or _HUNK_RE.match(line):
            continue
        if truncated and i == len(lines) - 1 and line.startswith("... "):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _load(data: Any) -> dict:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AdapterError(f"不是 UTF-8 编码：{e}") from None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise AdapterError(f"不是合法 JSON：{e}") from None
    if not isinstance(data, dict):
        raise AdapterError(f"ChangesReport 必须是 JSON 对象，收到 {type(data).__name__}")
    return data


def parse_changes_report(data: Any) -> tuple[dict, list[ChangeEvent]]:
    """Validate a ChangesReport and turn each watch entry into a ChangeEvent.

    Returns (report_meta, events). Raises AdapterError on anything that does not match the contract.
    """
    raw = _load(data)
    if "from" not in raw or not isinstance(raw.get("from"), int) or isinstance(raw.get("from"), bool):
        raise AdapterError("ChangesReport 缺少整数字段 'from'")
    try:
        report = _CdioChangesReport.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise AdapterError(f"ChangesReport 字段不符合契约：{loc} - {first['msg']}") from None

    events: list[ChangeEvent] = []
    for w in report.watches:
        if w.to_timestamp < w.from_timestamp:
            raise AdapterError(f"监测项 {w.uuid} 的 to_timestamp 早于 from_timestamp")
        added, removed = parse_unified_diff(w.diff, truncated=w.truncated)
        events.append(ChangeEvent(
            event_id="CE-" + short_hash(w.uuid, str(w.from_timestamp), str(w.to_timestamp), w.diff),
            watch_uuid=w.uuid,
            title=w.title,
            url=w.url,
            domain=(urlsplit(w.url).hostname or "").lower(),
            from_timestamp=w.from_timestamp,
            to_timestamp=w.to_timestamp,
            changes=w.changes,
            new_in_period=w.new_in_period,
            no_net_difference=w.no_net_difference,
            truncated=w.truncated,
            watch_error=w.error,
            added_lines=added,
            removed_lines=removed,
            raw_diff=w.diff,
        ))
    meta = {
        "from": raw["from"], "to": report.to, "mode": report.mode, "total": report.total,
        "monitors_checked": report.monitors_checked, "changes_total": report.changes_total,
        "incomplete": report.incomplete,
    }
    return meta, events
