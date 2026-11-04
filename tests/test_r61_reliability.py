"""R6.1 P0 reliability: negation, entity matching, source identity.

Counterexamples first (E05, E09 from the R6 evaluation, and a user-declared "official" URL getting
grade A). The other cases are deliberately different from the evaluation set, so the fix is not
tuned to it: other word orders, distances, sentence boundaries, double negation, questions, English,
other aliases and names."""
import json

import pytest
from conftest import ROOT

from techsignal import projects as P
from techsignal.models import Assumption, Direction, SourceProfile, Thesis
from techsignal.pipeline import run_pipeline
from techsignal.rules import decide, match_theses
from techsignal.store import SourceRegistry, load_theses
from techsignal.triage import TriageResult

TH = Thesis(thesis_id="TH-N", title="星衡半导体：量产", conclusion="2026 年内量产", entities=["星衡半导体", "XH"],
            topic_keywords=["产线"], as_of="2026-11-01", owner="测试", fictional=True,
            assumptions=[Assumption(assumption_id="A1", statement="2026 年内量产",
                                    support_patterns=["如期量产", "提升至"], weaken_patterns=["推迟", "延期", "(?i)delayed"])])


def _outcome(line, title="星衡半导体新闻", side="added", th=TH):
    t = TriageResult(meaningful_added=[line] if side == "added" else [],
                     meaningful_removed=[line] if side == "removed" else [])
    ms = match_theses(title, t, [th])
    return (decide(ms[0], t) if ms else None), ms


def _kind(line, **kw):
    o, _ = _outcome(line, **kw)
    return None if o is None else (o.kind, o.direction.value if o.direction else None)


# ---------------------------------------------------------------- P0-01 negation -----------------

def test_E05_counterexample_negation_far_from_the_signal():
    assert _kind("星衡半导体否认8英寸碳化硅产线推迟量产的市场传闻") == ("ambiguous", None)


@pytest.mark.parametrize("line", [
    "公司今日在发布会上正式否认了关于8英寸产线将会推迟的报道",          # cue far before the signal
    "8英寸产线推迟量产的消息不实",                                      # cue after the signal
    "8英寸产线推迟量产一事，纯属谣言",                                   # cue in the next clause about the same thing
    "星衡半导体8英寸产线并非没有推迟的可能",                              # double negation: not resolved, not guessed
    "星衡半导体8英寸产线是否会推迟？",                                    # question
    "星衡半导体8英寸产线会推迟吗",                                        # question without a mark
    "星衡半导体的产线没有像外界说的那样推迟",                            # negation with words in between
    "XH denies reports that the 8-inch line will be delayed",           # English
    "The 8-inch line is not delayed, 星衡半导体 said",                   # English, cue before
])
def test_negated_or_uncertain_signals_never_give_a_direction(line):
    kind = _kind(line, title="星衡半导体新闻")
    assert kind is None or kind[0] != "decisive", line


@pytest.mark.parametrize("line,expected", [
    ("星衡半导体否认裁员传闻。8英寸产线量产推迟至2027年。", "weakens"),   # negation in another sentence
    ("星衡半导体否认了收购传闻，但8英寸产线量产推迟至2027年", "weakens"),  # contrast starts a new clause
    ("星衡半导体8英寸产线量产推迟至2027年", "weakens"),                   # plain, unchanged
    ("星衡半导体8英寸产线将如期量产", "supports"),                        # plain, unchanged
    ("星衡半导体未来8英寸产线将如期量产", "supports"),                    # 未来 is not a negation
    ("星衡半导体良率不断提升至65%", "supports"),                          # 不断 is not a negation
    ("The 8-inch line of 星衡半导体 was delayed to 2027", "weakens"),
])
def test_clear_statements_keep_their_direction(line, expected):
    assert _kind(line) == ("decisive", expected), line


def test_removed_side_is_still_never_directional():
    assert _kind("星衡半导体8英寸产线量产推迟至2027年", side="removed")[0] == "ambiguous"


# ---------------------------------------------------------------- P0-02 entities ------------------

def test_E09_counterexample_short_alias_inside_another_name():
    o, ms = _outcome("XHTech 宣布其碳化硅快充产线推迟至明年投产", title="XHTech 新闻")
    assert ms == [] or o.kind != "decisive"


@pytest.mark.parametrize("line,title", [
    ("XHS 社区上有人说产线推迟", "社区"),            # alias followed by a Latin letter
    ("AXH 公司产线推迟", "新闻"),                     # alias preceded by a Latin letter
    ("产品型号 XH2 的产线推迟", "新闻"),              # alias followed by a digit
])
def test_latin_alias_needs_word_boundaries(line, title):
    assert match_theses(title, TriageResult(meaningful_added=[line]), [TH]) == []


def test_short_alias_alone_matches_but_needs_confirmation():
    o, ms = _outcome("XH 8英寸碳化硅产线量产推迟至2027年", title="行业新闻")
    assert ms and ms[0].weak and o.kind == "ambiguous"
    assert any("XH" in r and "人工确认" in r for r in o.reasons)
    o, ms = _outcome("XH半导体8英寸产线量产推迟", title="行业新闻")      # CJK next to Latin is a boundary
    assert ms and o.kind == "ambiguous"


def test_full_name_present_is_a_strong_match():
    o, ms = _outcome("星衡半导体（XH）8英寸产线量产推迟至2027年", title="行业新闻")
    assert not ms[0].weak and o.kind == "decisive"


def test_primary_latin_name_uses_boundaries_but_stays_strong():
    th = TH.model_copy(update={"entities": ["Arm"], "topic_keywords": ["chip"]})
    assert match_theses("news", TriageResult(meaningful_added=["Armstrong chip line delayed"]), [th]) == []
    o, ms = _outcome("Arm chip line delayed to 2027", title="news", th=th)
    assert not ms[0].weak and o.kind == "decisive" and o.direction == Direction.WEAKENS


def test_user_marked_ambiguous_chinese_alias():
    th = TH.model_copy(update={"entities": ["星衡半导体", "星衡"], "ambiguous_entities": ["星衡"]})
    o, ms = _outcome("星衡科技8英寸产线量产推迟", title="行业新闻", th=th)
    assert ms[0].weak and o.kind == "ambiguous"
    o, ms = _outcome("星衡半导体8英寸产线量产推迟", title="行业新闻", th=th)
    assert not ms[0].weak and o.kind == "decisive"


def test_normal_chinese_short_name_is_not_auto_weak():
    th = TH.model_copy(update={"entities": ["星衡半导体", "星衡"]})
    o, ms = _outcome("星衡8英寸产线量产推迟", title="行业新闻", th=th)
    assert not ms[0].weak and o.kind == "decisive"


def test_existing_case_entities_still_match():
    th = load_theses(ROOT / "cases" / "tenstorrent_blackhole" / "theses.json")[0]
    t = TriageResult(meaningful_added=["See tenstorrent/tt-metal for Blackhole models"])
    assert match_theses("tt-metal README", t, [th])


# ---------------------------------------------------------------- P0-03 source identity -----------

def _spec(**kw):
    base = dict(name="星衡半导体", question="量产进度", judgement="2026 年内量产",
                assumptions=[{"statement": "2026 年内量产", "weaken": "推迟"}],
                urls="https://xh-semi.example/news 官网新闻", source_types={"https://xh-semi.example/news": "official"})
    base.update(kw)
    return P.spec_from_form(**base)


@pytest.fixture()
def store(tmp_path):
    return P.ProjectStore(tmp_path / "projects")


def _grade(store, pid, url="https://xh-semi.example/news"):
    reg = SourceRegistry.load(store.sources_path(pid))
    from urllib.parse import urlsplit
    return reg.profile(urlsplit(url).hostname, url)


def test_counterexample_declared_official_is_not_grade_A(store):
    p = store.create(_spec(), ready=True)
    prof = _grade(store, p.project_id)
    assert prof.grade not in ("A", "B") and "未确认" in prof.name


@pytest.mark.parametrize("declared", ["official", "regulator", "media", "analyst", "forum", "unknown"])
def test_no_declared_type_alone_reaches_A_or_B(store, declared):
    p = store.create(_spec(source_types={"https://xh-semi.example/news": declared}), ready=True)
    assert _grade(store, p.project_id).grade in ("C", "D")


def test_form_default_is_not_official():
    spec = P.spec_from_form(urls="https://unknown.example/page")
    assert spec.sources[0].source_type == "unknown"


def _report(line="星衡半导体8英寸产线量产推迟至2027年"):
    return {"from": 1, "to": 3, "mode": "net_change", "total": 1, "offset": 0, "limit": 50, "monitors_checked": 1,
            "changes_total": 1, "incomplete": False, "watches": [{
                "uuid": "00000000-0000-4000-8000-000000000611", "title": "星衡半导体新闻中心",
                "url": "https://xh-semi.example/news", "changes": 1, "new_in_period": False, "no_net_difference": False,
                "error": None, "from_timestamp": 1, "to_timestamp": 2,
                "diff": f"--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1,2 @@\n 新闻\n+{line}",
                "truncated": False}]}


def _direction(store, pid):
    res = run_pipeline(_report(), load_theses(store.theses_path(pid)), SourceRegistry.load(store.sources_path(pid)),
                       persist=False)
    return res.assessments[0]


def test_unconfirmed_source_cannot_carry_a_direction_and_confirmed_can(store):
    p = store.create(_spec(), ready=True)
    a = _direction(store, p.project_id)
    assert a.direction == Direction.INSUFFICIENT and a.tentative_direction == Direction.WEAKENS
    p2 = store.confirm_source(p.project_id, "https://xh-semi.example/news", "official", by="张三",
                              basis="官网域名与公司 2025 年报披露的官网一致", expected_version=1)
    assert p2.version == 2
    src = p2.spec.sources[0]
    assert src.confirmation.by == "张三" and src.confirmation.confirmed_type == "official" and src.confirmation.at
    assert _grade(store, p.project_id).grade == "A" and "已确认" in _grade(store, p.project_id).name
    assert _direction(store, p.project_id).direction == Direction.WEAKENS
    # history keeps the unconfirmed v1
    assert store.version(p.project_id, 1).spec.sources[0].confirmation is None
    assert store.versions(p.project_id)[-1].note.startswith("确认来源")


def test_confirmation_needs_who_and_why_and_a_current_version(store):
    p = store.create(_spec(), ready=True)
    for by, basis in (("", "官网域名一致"), ("张三", ""), ("张三", "是")):
        with pytest.raises(P.ProjectError):
            store.confirm_source(p.project_id, "https://xh-semi.example/news", "official", by=by, basis=basis,
                                 expected_version=1)
    with pytest.raises(P.ProjectError):
        store.confirm_source(p.project_id, "https://other.example/", "official", by="张三", basis="依据充分",
                             expected_version=1)
    store.update(p.project_id, _spec(judgement="改"), expected_version=1, ready=True)
    with pytest.raises(P.ProjectConflict):
        store.confirm_source(p.project_id, "https://xh-semi.example/news", "official", by="张三", basis="依据充分",
                             expected_version=1)


def test_revoking_and_url_changes_drop_the_confirmation(store):
    p = store.create(_spec(), ready=True)
    p = store.confirm_source(p.project_id, "https://xh-semi.example/news", "media", by="张三", basis="知名媒体官网",
                             expected_version=1)
    assert _grade(store, p.project_id).grade == "B"
    p = store.revoke_source(p.project_id, "https://xh-semi.example/news", by="李四", expected_version=2)
    assert p.spec.sources[0].confirmation is None and _grade(store, p.project_id).grade == "C"
    p = store.confirm_source(p.project_id, "https://xh-semi.example/news", "official", by="张三", basis="官网域名一致",
                             expected_version=3)
    # editing the URL list keeps confirmations of unchanged URLs and never carries one to a new URL
    p = store.update(p.project_id, _spec(urls="https://xh-semi.example/news 官网新闻\nhttps://xh-semi.example/ir 投资者关系"),
                     expected_version=4, ready=True)
    conf = {s.url: s.confirmation for s in p.spec.sources}
    assert conf["https://xh-semi.example/news"] is not None and conf["https://xh-semi.example/ir"] is None


def test_form_edits_cannot_forge_a_confirmation(store):
    p = store.create(_spec(), ready=True)
    forged = _spec()
    forged.sources[0].confirmation = P.SourceConfirmation(by="x", at="2020-01-01T00:00:00Z", basis="伪造的确认",
                                                          confirmed_type="official")
    p2 = store.update(p.project_id, forged, expected_version=1, ready=True)
    assert p2.spec.sources[0].confirmation is None


def test_r6_projects_are_migrated_down_not_up(store):
    """A project saved by R6 has no confirmation field and a generated sources.json with grade A.
    It must be regenerated as unconfirmed (grade C), logged, and project.json left untouched."""
    p = store.create(_spec(), ready=True)
    d = store.project_dir(p.project_id)
    raw = json.loads((d / "project.json").read_text(encoding="utf-8"))
    for s in raw["spec"]["sources"]:
        s.pop("confirmation", None)
    (d / "project.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "sources.json").write_text(json.dumps({"sources": [SourceProfile(
        domain="xh-semi.example", path_prefix="/news", name="官网新闻", source_type="official", grade="A",
        note="R6 生成").model_dump(mode="json")]}, ensure_ascii=False), encoding="utf-8")
    before = (d / "project.json").read_bytes()
    changed = store.sync_generated(p.project_id)
    assert changed and _grade(store, p.project_id).grade == "C"
    assert (d / "project.json").read_bytes() == before
    log = (d / "migrations.jsonl").read_text(encoding="utf-8")
    assert "A" in log and "C" in log and "未确认" in log
    assert store.sync_generated(p.project_id) == []          # idempotent
