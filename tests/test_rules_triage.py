from techsignal.models import ChangeEvent
from techsignal.rules import decide, find_signals, match_theses
from techsignal.triage import TriageResult, clean_lines, fingerprint, is_noise_line


def _event(added, removed=(), title="t"):
    return ChangeEvent(event_id="CE-x", watch_uuid="u", title=title, url="https://x.example/", domain="x.example",
                       from_timestamp=1, to_timestamp=2, changes=1, new_in_period=False, no_net_difference=False,
                       truncated=False, added_lines=list(added), removed_lines=list(removed), raw_diff="+x")


def test_noise_lines():
    for line in ["3小时前", "5 minutes ago", "阅读 1,234", "浏览量：88次", "2026-10-15 09:30", "   ", "——", "更新于 2026-10-01"]:
        assert is_noise_line(line), line
    for line in ["星衡半导体良率提升至65%", "1.6T 硅光模块：送样时间另行通知"]:
        assert not is_noise_line(line), line


def test_moved_lines_are_dropped_and_real_changes_kept():
    t = clean_lines(_event(["A 公告", "新增内容"], ["A 公告 "]))
    assert t.meaningful_added == ["新增内容"] and t.dropped_moved == ["A 公告", "A 公告 "]


def test_fingerprint_ignores_order_and_whitespace():
    a = TriageResult(meaningful_added=["甲", "乙"])
    b = TriageResult(meaningful_added=["乙 ", " 甲"])
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(TriageResult(meaningful_removed=["甲", "乙"]))


def _decide(theses, added, removed=(), title=""):
    t = TriageResult(meaningful_added=list(added), meaningful_removed=list(removed))
    m = match_theses(title, t, theses)
    return m, (decide(m[0], t) if m else None)


def test_entity_alone_is_not_relevance(theses):
    m, out = _decide(theses, ["星衡半导体年会圆满举办"])
    assert m[0].thesis.thesis_id == "TH-001" and out.kind == "unrelated"


def test_clean_signal_is_decisive(theses):
    _, out = _decide(theses, ["星衡半导体宣布量产推迟"])
    assert out.kind == "decisive" and out.direction.value == "weakens" and out.assumption_id == "A1"


def test_negated_signal_is_never_decisive(theses):
    _, out = _decide(theses, ["星衡半导体否认推迟量产"])
    assert out.kind == "ambiguous"


def test_signal_in_removed_text_is_ambiguous(theses):
    _, out = _decide(theses, ["星衡半导体 产线公告已下线"], removed=["星衡半导体 8英寸产线如期量产"])
    assert out.kind == "ambiguous"


def test_mixed_polarity_is_ambiguous(theses):
    _, out = _decide(theses, ["星衡半导体良率提升，但量产延期"])
    assert out.kind == "ambiguous"


def test_retired_thesis_is_ignored(theses):
    retired = [t.model_copy(update={"status": "retired"}) for t in theses]
    assert match_theses("", TriageResult(meaningful_added=["星衡半导体量产推迟"]), retired) == []


def test_signals_record_side_and_negation(theses):
    th = next(t for t in theses if t.thesis_id == "TH-001")
    hits = find_signals(th, TriageResult(meaningful_added=["并未推迟"]))
    assert hits and hits[0].negated and hits[0].side == "added"
