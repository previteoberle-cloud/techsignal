import json

import pytest
from conftest import DEMO
from techsignal.adapters.cdio import AdapterError, parse_changes_report, parse_unified_diff
from techsignal.fixtures import build_report, fixture_files, load_upstream_builder

upstream = load_upstream_builder()
needs_upstream = pytest.mark.skipif(
    upstream is None, reason="needs changedetection.io source: scripts/fetch_changedetection.sh + TECHSIGNAL_CDIO_SOURCE")


def test_parse_demo_report():
    meta, events = parse_changes_report((DEMO / "changes_report.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "net_change" and meta["total"] == len(events) == 11
    ev = next(e for e in events if e.title == "澜芯光电产品中心")
    assert ev.added_lines == ["1.6T 硅光模块：送样时间另行通知"]
    assert ev.removed_lines == ["1.6T 硅光模块：预计2026年第四季度送样"]
    assert ev.domain == "www.lanxin-photonics.example"
    assert ev.cdio_difference_path == f"/api/v1/watch/{ev.watch_uuid}/difference/{ev.from_timestamp}/{ev.to_timestamp}"


def test_unified_diff_literal_from_upstream_test():
    # Literal taken from changedetectionio/tests/test_api_changes.py:83
    diff = "--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1 @@\n-In stock, 149.99 USD\n+In stock, 129.99 USD"
    assert parse_unified_diff(diff) == (["In stock, 129.99 USD"], ["In stock, 149.99 USD"])


def test_content_lines_that_look_like_headers_are_kept():
    diff = "--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1 @@\n--- not a header\n+++ also content"
    assert parse_unified_diff(diff) == (["++ also content"], ["-- not a header"])


def test_truncation_marker_is_not_content():
    diff = "@@ -1 +1,3 @@\n+a\n... 8 more lines, see /api/v1/watch/x/difference/1/2"
    assert parse_unified_diff(diff, truncated=True) == (["a"], [])


@pytest.mark.parametrize("bad", ["{not json", "[]", json.dumps({"to": 1}), b"\xff\xfe"])
def test_bad_input_raises_adapter_error(bad):
    with pytest.raises(AdapterError):
        parse_changes_report(bad)


def test_reversed_timestamps_rejected():
    report = json.loads((DEMO / "changes_report.json").read_text(encoding="utf-8"))
    w = report["watches"][0]
    w["from_timestamp"], w["to_timestamp"] = w["to_timestamp"], w["from_timestamp"]
    with pytest.raises(AdapterError):
        parse_changes_report(report)


def test_unknown_extra_fields_are_tolerated():
    report = json.loads((DEMO / "changes_report.json").read_text(encoding="utf-8"))
    report["future_field"] = 1
    report["watches"][0]["future_field"] = "x"
    _, events = parse_changes_report(report)
    assert len(events) == 11


@needs_upstream
def test_stored_fixtures_equal_fresh_upstream_output():
    """Every stored report is exactly what upstream's group_changes() produces today."""
    for path in fixture_files():
        spec = json.loads(path.read_text(encoding="utf-8"))
        if "watches" not in spec:
            continue
        fresh = build_report(spec, upstream)
        stored = (json.loads((DEMO / "changes_report.json").read_text(encoding="utf-8"))
                  if path.name == "demo_source.json" else spec["report"])
        assert fresh == stored, path.name


@needs_upstream
def test_upstream_report_keys_match_adapter_contract():
    report = build_report({"from": 100, "to": 300, "watches": [
        {"uuid": "u1", "title": "t", "url": "https://technews.example/a", "snapshots": {"50": "a", "200": "b"}}]}, upstream)
    assert set(report) >= {"from", "to", "mode", "total", "offset", "limit", "monitors_checked",
                           "changes_total", "incomplete", "watches"}
    assert set(report["watches"][0]) == {"uuid", "title", "url", "changes", "new_in_period", "no_net_difference",
                                         "error", "from_timestamp", "to_timestamp", "diff", "truncated"}
    _, events = parse_changes_report(report)
    assert events[0].added_lines == ["b"] and events[0].removed_lines == ["a"]
