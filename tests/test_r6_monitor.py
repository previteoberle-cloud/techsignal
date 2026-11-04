"""R6 Checkpoint 2: a project linked to changedetection.io (offline stand-in, see fake_cdio.py).
The real service is covered by tests/integration/test_r6_project_live.py."""
import json
import threading

import pytest
from fake_cdio import FakeCdio

from techsignal import project_monitor as M
from techsignal import projects as P
from techsignal.workbench import viewmodel as vm

KEY = "sk-fake-SECRET-424242"
PAGE = "https://github.com/tenstorrent/tt-metal"
NEWS = "https://news.example.com/ai"
V1 = "tt-metal\nModels\n| Llama 3.1 8B | n150 (Wormhole) | 23 t/s |\n"
V2 = V1 + "| Llama 3.1 70B | QuietBox (Blackhole) | 15 t/s |\n"


def _spec(name="Tenstorrent", urls=f"{PAGE} 官方仓库\n{NEWS} 行业媒体"):
    return P.spec_from_form(
        name=name, question="Blackhole 的大模型推理软件成熟度", judgement="仍处早期，没有 70B 级模型的官方记录。",
        assumptions=[{"statement": "尚无 70B 级模型在 Blackhole 上的官方运行记录", "support": "8B", "weaken": "70B"}],
        urls=urls, source_types={NEWS: "media"}, keywords="Blackhole")


@pytest.fixture()
def cdio():
    f = FakeCdio(key=KEY)
    f.pages[PAGE] = V1
    f.pages[NEWS] = "AI news\nnothing yet\n"
    yield f
    f.close()


@pytest.fixture()
def store(tmp_path):
    return P.ProjectStore(tmp_path / "projects")


def _client(cdio, key=KEY):
    return M.client_for(cdio.url, key)


def _linked(store, cdio, name="Tenstorrent", **kw):
    p = store.create(_spec(name, **kw), ready=True)
    c = _client(cdio)
    plan = M.plan_watches(p, M.load_state(store, p.project_id), M.connect(c)["watches"])
    M.apply_plan(store, p.project_id, c, plan, confirmed=True)
    return p, c


def _snapshot_files(store, pid):
    d = store.project_dir(pid)
    return {f: f.read_bytes() for f in (d / "project.json", d / "theses.json", d / "sources.json")}


# ---------------------------------------------------------------- connection ----------------------

def test_connect_lists_existing_watches_without_writing(cdio, store):
    cdio.add_watch("https://other.example.com/", "someone else's")
    info = M.connect(_client(cdio))
    assert info["version"] == "fake-0.0" and len(info["watches"]) == 1
    assert all(m == "GET" for m, _ in cdio.requests)


def test_wrong_key_is_reported_without_echoing_it(cdio):
    with pytest.raises(P.ProjectError) as e:
        M.connect(M.client_for(cdio.url, "sk-WRONG-never-shown"))
    assert "鉴权失败" in str(e.value) and "sk-WRONG" not in str(e.value)


def test_service_down_is_a_readable_error():
    with pytest.raises(P.ProjectError) as e:
        M.connect(M.client_for("http://127.0.0.1:9", KEY, timeout=1))
    assert "无法连接" in str(e.value)


# ---------------------------------------------------------------- watches -------------------------

def test_plan_reuses_an_existing_watch_with_the_same_url(cdio, store):
    existing = cdio.add_watch(PAGE + "/", "already there")
    p = store.create(_spec(), ready=True)
    plan = M.plan_watches(p, M.load_state(store, p.project_id), M.connect(_client(cdio))["watches"])
    assert [(x.action, x.watch_uuid) for x in plan] == [("link_existing", existing), ("create", None)]


def test_nothing_is_created_without_confirmation(cdio, store):
    p = store.create(_spec(), ready=True)
    c = _client(cdio)
    plan = M.plan_watches(p, M.load_state(store, p.project_id), M.connect(c)["watches"])
    with pytest.raises(P.ProjectError):
        M.apply_plan(store, p.project_id, c, plan, confirmed=False)
    assert cdio.watches == {} and M.load_state(store, p.project_id).watches == []


def test_apply_creates_and_records_links(cdio, store):
    p, _ = _linked(store, cdio)
    st = M.load_state(store, p.project_id)
    assert {w.url for w in st.watches} == {PAGE, NEWS} and all(w.created_by_techsignal for w in st.watches)
    assert {w["url"] for w in cdio.watches.values()} == {PAGE, NEWS}
    assert all(w["title"].startswith("TechSignal｜Tenstorrent｜") for w in cdio.watches.values())
    assert st.cdio_url == cdio.url and st.cursor is not None
    raw = (store.project_dir(p.project_id) / "monitor.json").read_text(encoding="utf-8")
    assert KEY not in raw
    # applying the same plan again creates nothing new
    plan = M.plan_watches(p, st, M.connect(_client(cdio))["watches"])
    assert {x.action for x in plan} == {"already_linked"}
    M.apply_plan(store, p.project_id, _client(cdio), plan, confirmed=True)
    assert len(cdio.watches) == 2


def test_a_tampered_plan_cannot_create_foreign_urls(cdio, store):
    p = store.create(_spec(), ready=True)
    bad = [M.PlanItem(url="https://evil.example.com/", action="create")]
    with pytest.raises(P.ProjectError):
        M.apply_plan(store, p.project_id, _client(cdio), bad, confirmed=True)
    assert cdio.watches == {}


def test_draft_projects_cannot_be_monitored(cdio, store):
    p = store.create(P.ProjectSpec(name="Cerebras"), ready=False)
    with pytest.raises(P.ProjectError):
        M.apply_plan(store, p.project_id, _client(cdio), [], confirmed=True)
    with pytest.raises(P.ProjectError):
        M.check_now(store, p.project_id, _client(cdio))


def test_one_project_one_service(cdio, store):
    p, _ = _linked(store, cdio)
    other = FakeCdio(key=KEY)
    try:
        with pytest.raises(P.ProjectError):
            M.apply_plan(store, p.project_id, M.client_for(other.url, KEY), [], confirmed=True)
        with pytest.raises(P.ProjectError):
            M.check_now(store, p.project_id, M.client_for(other.url, KEY))
    finally:
        other.close()


# ---------------------------------------------------------------- manual check ----------------------

def test_baseline_then_no_change_then_real_change(cdio, store):
    p, c = _linked(store, cdio)
    before = _snapshot_files(store, p.project_id)

    r1 = M.check_now(store, p.project_id, c)                 # first snapshots are only a baseline
    assert r1.outcome in ("no_net_change", "no_new_snapshot") and r1.run_id is None
    r2 = M.check_now(store, p.project_id, c)                 # nothing changed on the pages
    assert r2.outcome == "no_new_snapshot" and "没有产生新的研判" in r2.message
    assert M.runs(store, p.project_id) == []
    assert not (store.project_dir(p.project_id) / "state").exists()

    cdio.pages[PAGE] = V2                                     # the official page gains the 70B row
    r3 = M.check_now(store, p.project_id, c)
    assert r3.outcome == "assessed" and r3.run_id
    refs = M.runs(store, p.project_id)
    assert [r.run_id for r in refs] == [r3.run_id] and refs[0].thesis_version == 1
    d = M.run_dir(store, p.project_id, r3.run_id)
    run = vm.load_run(d, d / "theses.json")
    rows = [(a["assumption_id"], a["direction"], a["basis"]) for a in run.assessments()]
    assert rows == [("A1", "weakens", "rule")]
    ev = run.evidence[run.assessments()[0]["evidence_ids"][0]]
    assert ev.source_url == PAGE and ev.grade == "A" and any("70B" in x for x in ev.excerpt_added)
    x = vm.explain(run, run.assessments()[0])
    assert x["source"] == "确定性规则" and x["review"] == "待人工复核" and "70B" in x["what"]
    rec = json.loads((d / "run_record.json").read_text(encoding="utf-8"))
    assert rec["input_mode"] == "live_api"

    # the user's judgement files were never rewritten by monitoring
    assert _snapshot_files(store, p.project_id) == before
    # the API key is in no file of the project
    for f in store.project_dir(p.project_id).rglob("*"):
        if f.is_file():
            assert KEY not in f.read_text(encoding="utf-8", errors="replace"), f

    r4 = M.check_now(store, p.project_id, c)                 # same content again: no new finding
    assert r4.outcome == "no_new_snapshot" and len(M.runs(store, p.project_id)) == 1
    assert [x.outcome for x in M.checks(store, p.project_id)][-1] == "no_new_snapshot"


def test_content_back_and_forth_is_not_news(cdio, store):
    p, c = _linked(store, cdio)
    M.check_now(store, p.project_id, c)
    cdio.pages[PAGE] = V2
    cdio.fetch(next(u for u, w in cdio.watches.items() if w["url"] == PAGE))   # changed and changed back
    cdio.pages[PAGE] = V1
    r = M.check_now(store, p.project_id, c)
    assert r.outcome == "no_net_change" and M.runs(store, p.project_id) == []


def test_a_recheck_that_never_finishes_is_not_reported_as_news(cdio, store):
    p, c = _linked(store, cdio)
    cdio.auto_check = False
    cdio.pages[PAGE] = V2
    r = M.check_now(store, p.project_id, c, wait=0, sleep=lambda s: None)
    assert r.outcome in ("no_new_snapshot", "no_net_change") and "未完成检查" in r.message
    assert M.runs(store, p.project_id) == []


def test_fetch_errors_are_recorded(cdio, store):
    p, c = _linked(store, cdio)
    cdio.pages[NEWS] = None
    r = M.check_now(store, p.project_id, c)
    assert r.watch_errors and "监测报错" in r.message


def test_service_failure_during_a_check_is_recorded_not_raised(cdio, store):
    p, c = _linked(store, cdio)
    cdio.close()
    r = M.check_now(store, p.project_id, M.client_for(cdio.url, KEY, timeout=1))
    assert r.outcome == "failed" and M.runs(store, p.project_id) == []


def test_projects_do_not_see_each_others_watches_or_results(cdio, store):
    a, c = _linked(store, cdio)
    b, _ = _linked(store, cdio, name="Cerebras", urls="https://cerebras.example.com/blog")
    cdio.pages["https://cerebras.example.com/blog"] = "blog\n"
    M.check_now(store, b.project_id, c)
    b_uuids = {w.watch_uuid for w in M.load_state(store, b.project_id).watches}
    a_uuids = {w.watch_uuid for w in M.load_state(store, a.project_id).watches}
    assert not (a_uuids & b_uuids)
    rechecks = [path for m, path in cdio.requests if "recheck=1" in path]
    assert rechecks and all(any(u in path for u in b_uuids) for path in rechecks)
    M.check_now(store, a.project_id, c)
    cdio.pages[PAGE] = V2
    M.check_now(store, a.project_id, c)
    assert len(M.runs(store, a.project_id)) == 1 and M.runs(store, b.project_id) == []


def test_edits_after_a_run_do_not_rewrite_that_run(cdio, store):
    p, c = _linked(store, cdio)
    M.check_now(store, p.project_id, c)
    cdio.pages[PAGE] = V2
    first = M.check_now(store, p.project_id, c)
    store.update(p.project_id, _spec().model_copy(update={"judgement": "已出现 70B 记录，判断改为复核中"}),
                 expected_version=1, ready=True)
    d = M.run_dir(store, p.project_id, first.run_id)
    assert vm.load_run(d, d / "theses.json").theses[0].conclusion == "仍处早期，没有 70B 级模型的官方记录。"
    cdio.pages[PAGE] = V2 + "| Llama 3.3 70B | Galaxy (Blackhole) | 30 t/s |\n"
    second = M.check_now(store, p.project_id, c)
    assert second.outcome == "assessed" and M.runs(store, p.project_id)[-1].thesis_version == 2


def test_concurrent_checks_of_one_project_are_serialized(cdio, store):
    p, c = _linked(store, cdio)
    lock = store.project_dir(p.project_id) / ".monitor-lock"
    lock.mkdir()                                   # another check is running
    with pytest.raises(P.ProjectError) as e:
        M.check_now(store, p.project_id, c)
    assert "占用" in str(e.value)
    lock.rmdir()
    results = []
    ts = [threading.Thread(target=lambda: results.append(M.check_now(store, p.project_id, c))) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 2


def test_run_ids_cannot_escape_the_project(store):
    p = store.create(_spec(), ready=True)
    for bad in ("../x", "..", "r-1/../../p"):
        with pytest.raises(P.ProjectError):
            M.run_dir(store, p.project_id, bad)


def test_private_urls_only_with_the_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv(P.ALLOW_PRIVATE_ENV, raising=False)
    with pytest.raises(P.ProjectError):
        P.ProjectStore(tmp_path / "a").create(_spec(urls="http://127.0.0.1:8000/page"), ready=True)
    monkeypatch.setenv(P.ALLOW_PRIVATE_ENV, "1")
    s = P.ProjectStore(tmp_path / "b")
    assert s.allow_private_urls and s.create(_spec(urls="http://127.0.0.1:8000/page"), ready=True)
    with pytest.raises(P.ProjectError):           # scheme and credential checks still apply
        P.check_public_url("file:///etc/passwd", allow_private=True)


# ---------------------------------------------------------------- Checkpoint 3: optional model -------

def test_project_check_with_a_configured_model_service(cdio, store, monkeypatch):
    """The operator configured an OpenAI-compatible service (here a local PROTOCOL STUB that echoes
    the allowed evidence id). The project run records the model suggestion, the gates and usage."""
    import re
    import threading as th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ev = re.search(r"可引用的证据编号：(EV-[0-9a-f]+)", body["messages"][1]["content"]).group(1)
            content = json.dumps({"assessments": [{"assumption_id": "A1", "change_kind": "new_fact",
                                                   "direction": "weakens", "rationale": "协议桩预置回答",
                                                   "cited_evidence_ids": [ev], "confidence": "medium"}]})
            data = json.dumps({"choices": [{"message": {"content": content}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("TECHSIGNAL_LLM_BASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    monkeypatch.setenv("TECHSIGNAL_LLM_MODEL", "protocol-stub")
    monkeypatch.setenv("TECHSIGNAL_LLM_API_KEY", "sk-llm-SECRET")
    try:
        p, c = _linked(store, cdio)
        M.check_now(store, p.project_id, c)
        cdio.pages[PAGE] = V1 + "| Llama 3.3 | Galaxy (Blackhole) | 30 t/s |\n"      # topic hit, no signal
        r = M.check_now(store, p.project_id, c, use_model=True)
        d = M.run_dir(store, p.project_id, r.run_id)
        run = vm.load_run(d, d / "theses.json")
        a = run.assessments()[0]
        assert (a["direction"], a["basis"], a["judgement_source"]) == ("weakens", "model", "model_validated")
        assert a["model_suggestion"]["rationale"] == "协议桩预置回答" and a["review_status"] == "pending_human"
        x = vm.explain(run, a)
        assert "模型服务建议" in x["source"] and "protocol-stub" in x["source"] and x["model"] == "协议桩预置回答"
        assert run.provenance["model_usage"]["total_tokens"] == 15
        for f in store.project_dir(p.project_id).rglob("*"):
            if f.is_file():
                assert "sk-llm-SECRET" not in f.read_text(encoding="utf-8", errors="replace")
        # without use_model the same kind of change stays with the rules
        cdio.pages[PAGE] += "| Qwen 2.5 | Galaxy (Blackhole) | 40 t/s |\n"
        r2 = M.check_now(store, p.project_id, c)
        d2 = M.run_dir(store, p.project_id, r2.run_id)
        a2 = vm.load_run(d2, d2 / "theses.json").assessments()[0]
        assert a2["basis"] == "rule" and a2["direction"] == "insufficient"
    finally:
        httpd.shutdown()
        httpd.server_close()
