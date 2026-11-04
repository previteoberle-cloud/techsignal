"""R6 Checkpoint 1: research projects created from plain text, stored safely and kept apart from the
committed demo cases. Counterexamples first: each test names the failure it guards against."""
import hashlib
import json
import os
import threading

import pytest
from conftest import ROOT

from techsignal import projects as P
from techsignal.pipeline import run_pipeline
from techsignal.store import SourceRegistry, load_theses


def _spec(name="Tenstorrent", **kw):
    base = dict(name=name, aliases="TT-Metal", question="Blackhole 的大模型推理软件成熟度",
                judgement="Blackhole 的大模型推理仍处早期，没有 70B 级模型的官方记录。",
                assumptions=[{"statement": "尚无 70B 级模型在 Blackhole 上的官方运行记录",
                              "support": "8B", "weaken": "70B，Llama 3.1 70B"},
                             {"statement": "官方仍以 Wormhole 为主力推理平台", "support": "Wormhole n300",
                              "weaken": "Blackhole Galaxy"}],
                urls="https://github.com/tenstorrent/tt-metal 官方仓库\nhttps://news.example.com/ai 行业媒体",
                source_types={"https://news.example.com/ai": "media"}, keywords="Blackhole，LLM")
    base.update(kw)
    return P.spec_from_form(**base)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("TECHSIGNAL_CASES_ROOT", raising=False)
    return P.ProjectStore(tmp_path / "projects")


def _tree_digest(path):
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- create / read --------------------

def test_create_read_and_generated_inputs_validate(store):
    p = store.create(_spec(), ready=True)
    assert P.PROJECT_ID_RE.fullmatch(p.project_id) and p.version == 1 and p.status == "ready"
    again = store.get(p.project_id)
    assert again == p
    theses = load_theses(store.theses_path(p.project_id))           # existing Pydantic models
    th = theses[0]
    assert th.thesis_id == f"TH-{p.project_id}" and th.fictional is False
    assert th.entities == ["Tenstorrent", "TT-Metal"] and th.topic_keywords == ["Blackhole", "LLM"]
    assert [a.assumption_id for a in th.assumptions] == ["A1", "A2"]
    reg = SourceRegistry.load(store.sources_path(p.project_id))
    assert reg.profile("github.com", "https://github.com/tenstorrent/tt-metal").grade == "A"
    assert reg.profile("news.example.com", "https://news.example.com/ai").grade == "B"
    # another path on the same host is NOT covered by the declared source
    assert reg.profile("github.com", "https://github.com/someone-else/repo").grade == "D"


def test_user_phrases_are_literal_not_regex(store):
    """A user typing regex syntax must not change matching (and must not crash the rules)."""
    spec = _spec(assumptions=[{"statement": "x", "weaken": "a.b(c, [70B]"}])
    p = store.create(spec, ready=True)
    pats = load_theses(store.theses_path(p.project_id))[0].assumptions[0].weaken_patterns
    import re
    assert re.search(pats[0], "版本 a.b(c 发布") and not re.search(pats[0], "版本 aXb(c 发布")
    assert re.search(pats[1], "新增 [70B] 支持")


def test_alnum_phrases_need_boundaries():
    import re
    pat = P.literal_pattern("70B")
    assert re.search(pat, "Llama 3.1 70B on QuietBox") and re.search(pat, "llama-70b")
    assert not re.search(pat, "170B params") and not re.search(pat, "70Bx")
    assert re.search(P.literal_pattern("量产"), "宣布量产时间")


def test_generated_thesis_drives_the_real_pipeline(store):
    p = store.create(_spec(), ready=True)
    report = {"from": 1, "to": 3, "mode": "net_change", "total": 1, "offset": 0, "limit": 50, "monitors_checked": 1,
              "changes_total": 1, "incomplete": False, "watches": [{
        "uuid": "11111111-1111-4111-8111-111111111111", "title": "Tenstorrent tt-metal README",
        "url": "https://github.com/tenstorrent/tt-metal", "changes": 1, "new_in_period": False,
        "no_net_difference": False, "error": None, "from_timestamp": 1, "to_timestamp": 2,
        "diff": "--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1,2 @@\n Models\n+| Llama 3.1 70B | QuietBox (Blackhole) | 15 t/s |",
        "truncated": False}]}
    res = run_pipeline(report, load_theses(store.theses_path(p.project_id)),
                       SourceRegistry.load(store.sources_path(p.project_id)), persist=False)
    rows = [(a.assumption_id, a.direction) for a in res.assessments]
    assert rows == [("A1", "weakens")]
    assert res.assessments[0].basis == "rule"


# ---------------------------------------------------------------- illegal input --------------------

@pytest.mark.parametrize("url", [
    "ftp://example.com/x", "javascript:alert(1)", "file:///etc/passwd", "https://user:pw@example.com/",
    "http://localhost:5000/", "http://127.0.0.1/", "http://[::1]/", "http://10.1.2.3/", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "https://intranet/", "https://printer.local/",
    "https://svc.internal/", "https://exa mple.com/", "https://example.com:99999/", "https://" + "a" * 600 + ".com",
    "https://bad_host!.com/",
])
def test_unsafe_or_malformed_urls_are_refused(url):
    with pytest.raises(P.ProjectError):
        P.check_public_url(url)


def test_public_urls_are_normalized():
    assert P.check_public_url("HTTPS://GitHub.com/a/b#frag") == "https://github.com/a/b"
    assert P.check_public_url("https://example.com") == "https://example.com/"


def test_ready_project_lists_every_missing_field(store):
    with pytest.raises(P.ProjectError) as e:
        store.create(P.ProjectSpec(name="X公司"), ready=True)
    text = " ".join(e.value.problems)
    for want in ("研究问题", "当前研究判断", "关键假设", "公开网址"):
        assert want in text
    assert list(store.root.glob("p-*")) == []                     # nothing half-created


def test_bad_urls_are_refused_even_in_a_draft(store):
    with pytest.raises(P.ProjectError) as e:
        store.create(_spec(urls="http://127.0.0.1:5000/ 内网"), ready=False)
    assert any("内网" in x for x in e.value.problems)


def test_phrase_and_length_problems(store):
    with pytest.raises(P.ProjectError) as e:
        store.create(_spec(assumptions=[{"statement": "x" * 400, "support": "a，70B", "weaken": "70B"}]), ready=True)
    text = " ".join(e.value.problems)
    assert "太短" in text and "同时被写成支持和削弱信号" in text and "过长" in text


def test_control_characters_are_removed(store):
    p = store.create(_spec(name="Tens\u200btorrent\x00"), ready=False)
    assert p.spec.name == "Tenstorrent"


def test_too_many_sources(store):
    urls = "\n".join(f"https://example{i}.com/" for i in range(P.MAX_SOURCES + 1))
    with pytest.raises(P.ProjectError):
        store.create(_spec(urls=urls), ready=True)


def test_duplicate_urls_are_merged(store):
    p = store.create(_spec(urls="https://example.com/a\nhttps://EXAMPLE.com/a/\nhttps://example.com/a#x"), ready=True)
    assert [s.url for s in p.spec.sources] == ["https://example.com/a"]


# ---------------------------------------------------------------- duplicates ----------------------

@pytest.mark.parametrize("other", ["Tenstorrent", "tenstorrent", " Ten storrent ", "ＴＥＮＳＴＯＲＲＥＮＴ"])
def test_duplicate_project_names_are_refused(store, other):
    store.create(_spec(), ready=True)
    with pytest.raises(P.ProjectError) as e:
        store.create(_spec(name=other), ready=False)
    assert "同名" in str(e.value)
    assert len(store.list()[0]) == 1


def test_rename_onto_an_existing_name_is_refused(store):
    store.create(_spec(), ready=True)
    b = store.create(_spec(name="Cerebras"), ready=True)
    with pytest.raises(P.ProjectError):
        store.update(b.project_id, _spec(name="Tenstorrent"), expected_version=1, ready=True)
    assert store.get(b.project_id).spec.name == "Cerebras"


def test_concurrent_creates_with_the_same_name_make_one_project(store):
    errors = []

    def go():
        try:
            store.create(_spec(), ready=True)
        except P.ProjectError as e:
            errors.append(e)
    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(store.list()[0]) == 1 and len(errors) == 5


# ---------------------------------------------------------------- edits and versions ----------------

def test_edit_creates_a_new_version_and_keeps_the_old_one(store):
    p = store.create(_spec(), ready=True)
    v1_text = (store.project_dir(p.project_id) / "versions" / "v1.json").read_text(encoding="utf-8")
    p2 = store.update(p.project_id, _spec(judgement="判断已更新"), expected_version=1, ready=True, note="复核后修改")
    assert p2.version == 2 and p2.spec.judgement == "判断已更新"
    assert (store.project_dir(p.project_id) / "versions" / "v1.json").read_text(encoding="utf-8") == v1_text
    vs = store.versions(p.project_id)
    assert [v.version for v in vs] == [1, 2] and vs[0].spec.judgement != vs[1].spec.judgement
    assert vs[1].note == "复核后修改"
    assert load_theses(store.theses_path(p.project_id))[0].conclusion == "判断已更新"


def test_stale_edit_is_refused_and_does_not_overwrite(store):
    p = store.create(_spec(), ready=True)
    store.update(p.project_id, _spec(judgement="窗口 A 的修改"), expected_version=1, ready=True)
    with pytest.raises(P.ProjectConflict):
        store.update(p.project_id, _spec(judgement="窗口 B 基于旧版本的修改"), expected_version=1, ready=True)
    assert store.get(p.project_id).spec.judgement == "窗口 A 的修改"


def test_concurrent_edits_from_the_same_version_only_one_wins(store):
    p = store.create(_spec(), ready=True)
    ok, conflicts = [], []

    def go(i):
        try:
            ok.append(store.update(p.project_id, _spec(judgement=f"修改 {i}"), expected_version=1, ready=True))
        except P.ProjectConflict:
            conflicts.append(i)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(ok) == 1 and len(conflicts) == 4
    assert [v.version for v in store.versions(p.project_id)] == [1, 2]


def test_unchanged_save_makes_no_version(store):
    p = store.create(_spec(), ready=True)
    assert store.update(p.project_id, _spec(), expected_version=1, ready=True).version == 1


def test_draft_then_complete_and_no_way_back(store):
    p = store.create(P.ProjectSpec(name="Cerebras"), ready=False)
    assert p.status == "draft" and not store.theses_path(p.project_id).exists()   # drafts never reach the pipeline
    p2 = store.update(p.project_id, _spec(name="Cerebras"), expected_version=1, ready=True)
    assert p2.status == "ready" and store.theses_path(p.project_id).exists()
    with pytest.raises(P.ProjectError):
        store.update(p.project_id, _spec(name="Cerebras", judgement="改"), expected_version=2, ready=False)


# ---------------------------------------------------------------- paths and isolation ---------------

@pytest.mark.parametrize("bad", ["../etc", "..", "p-../../x", "/etc/passwd", "p-ABCDEFABCDEF", "p-123", "", None,
                                 "p-0123456789ab/../p-0123456789ac"])
def test_path_traversal_ids_are_refused(store, bad):
    with pytest.raises(P.ProjectNotFound):
        store.get(bad)


def test_symlinked_project_dir_pointing_outside_is_refused(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir(parents=True)
    os.symlink(outside, store.root / "p-0123456789ab")
    with pytest.raises(P.ProjectNotFound):
        store.get("p-0123456789ab")


def test_projects_cannot_live_inside_committed_cases():
    with pytest.raises(P.ProjectError):
        P.ProjectStore(ROOT / "cases" / "mine")
    with pytest.raises(P.ProjectError):
        P.ProjectStore(ROOT / "cases")


def test_default_root_is_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.delenv(P.PROJECTS_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = P.default_projects_root()
    assert root == tmp_path / ".techsignal" / "projects" and ROOT not in root.parents


def test_projects_are_isolated_and_committed_cases_untouched(store):
    before = _tree_digest(ROOT / "cases")
    a = store.create(_spec(), ready=True)
    b = store.create(_spec(name="Cerebras", aliases=""), ready=True)
    store.update(a.project_id, _spec(judgement="A 改"), expected_version=1, ready=True)
    assert store.get(b.project_id).version == 1
    assert load_theses(store.theses_path(b.project_id))[0].thesis_id == f"TH-{b.project_id}"
    assert {x.name for x in store.root.iterdir() if x.is_dir()} == {a.project_id, b.project_id}
    assert _tree_digest(ROOT / "cases") == before


# ---------------------------------------------------------------- failure recovery ------------------

def test_failed_save_keeps_the_previous_version(store, monkeypatch):
    p = store.create(_spec(), ready=True)
    real = P.atomic_write

    def boom(path, text):
        if path.name == "project.json":
            raise OSError("disk full")
        return real(path, text)
    monkeypatch.setattr(P, "atomic_write", boom)
    with pytest.raises(OSError):
        store.update(p.project_id, _spec(judgement="不会生效"), expected_version=1, ready=True)
    monkeypatch.setattr(P, "atomic_write", real)
    cur = store.get(p.project_id)
    assert cur.version == 1 and cur.spec.judgement == p.spec.judgement
    # retrying works: the orphaned v2 record from the crashed save is replaced, not a conflict
    p2 = store.update(p.project_id, _spec(judgement="重试成功"), expected_version=1, ready=True)
    assert p2.version == 2 and store.version(p.project_id, 2).spec.judgement == "重试成功"
    assert not list(store.project_dir(p.project_id).rglob("*.tmp"))


def test_failed_create_leaves_nothing_behind(store, monkeypatch):
    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(P, "atomic_write", boom)
    with pytest.raises(OSError):
        store.create(_spec(), ready=True)
    assert list(store.root.glob("p-*")) == []


def test_corrupt_project_is_reported_and_others_still_load(store):
    a = store.create(_spec(), ready=True)
    b = store.create(_spec(name="Cerebras"), ready=True)
    (store.project_dir(a.project_id) / "project.json").write_text("{not json", encoding="utf-8")
    items, problems = store.list()
    assert [x.project_id for x in items] == [b.project_id] and len(problems) == 1
    with pytest.raises(P.ProjectError):
        store.get(a.project_id)


def test_stale_lock_from_a_crashed_process_is_taken_over(store):
    p = store.create(_spec(), ready=True)
    lock = store.project_dir(p.project_id) / ".lock"
    lock.mkdir()
    old = 1_000_000_000
    os.utime(lock, (old, old))
    assert store.update(p.project_id, _spec(judgement="改"), expected_version=1, ready=True).version == 2


def test_project_file_is_plain_json_without_secrets(store):
    p = store.create(_spec(), ready=True)
    data = json.loads((store.project_dir(p.project_id) / "project.json").read_text(encoding="utf-8"))
    assert data["schema"] == P.SCHEMA and set(data) == {"schema", "project_id", "status", "version", "created_at",
                                                         "updated_at", "spec"}
