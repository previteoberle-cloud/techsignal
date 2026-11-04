"""R6 ONLINE end-to-end: a research project created from plain text, monitored by a REAL
changedetection.io process, assessed by the real pipeline. Same opt-in as test_live_cdio.py:
  TECHSIGNAL_CDIO_PYTHON, TECHSIGNAL_CDIO_SOURCE (scripts/fetch_changedetection.sh),
  TECHSIGNAL_LIVE_NETWORK=1 for the public-URL test.

test_new_project_to_real_assessment: the page is served locally so its content can change between
two checks; the CONTENT is a test page (stated in the project), the fetching, snapshotting and diffing
are changedetection.io's own. Local addresses need the development opt-in on both sides
(ALLOW_IANA_RESTRICTED_ADDRESSES for changedetection.io, allow_private_urls for TechSignal).
"""
import json
import os

import pytest
from conftest import ROOT  # noqa: F401
from test_live_cdio import PY, Page, pytestmark  # noqa: F401  (same skip conditions)

from techsignal import project_monitor as M
from techsignal import projects as P
from techsignal.live import LiveCdio
from techsignal.workbench import viewmodel as vm


def _spec(url):
    return P.spec_from_form(
        name="星衡半导体", aliases="", question="8 英寸碳化硅产线的量产进度",
        judgement="8 英寸碳化硅产线将按计划在 2026 年内量产。",
        assumptions=[{"statement": "8 英寸产线 2026 年内量产", "support": "如期量产", "weaken": "推迟，延期"}],
        urls=f"{url} 官方新闻页（本地测试页）", keywords="碳化硅")


def test_new_project_to_real_assessment(tmp_path):
    page = Page("星衡半导体新闻中心\n最新动态\n")
    store = P.ProjectStore(tmp_path / "projects", allow_private_urls=True)
    p = store.create(_spec(page.url), ready=True)
    # R6.1: a person confirms the source; without it the same change would only be "insufficient"
    p = store.confirm_source(p.project_id, page.url, "official", by="集成测试",
                             basis="本地测试页，身份由测试构造", expected_version=1)
    project_files = {f: f.read_bytes() for f in store.project_dir(p.project_id).glob("*.json")}
    with LiveCdio(PY, allow_private_addresses=True, log_path=tmp_path / "cdio.log") as cdio:
        client = M.client_for(cdio.url, cdio.client.api_key, timeout=30)
        info = M.connect(client)
        assert info["watches"] == {}
        plan = M.plan_watches(p, M.load_state(store, p.project_id), info["watches"])
        assert [x.action for x in plan] == ["create"]
        M.apply_plan(store, p.project_id, client, plan, confirmed=True)
        uuid = M.load_state(store, p.project_id).watches[0].watch_uuid
        cdio.wait_for_snapshots(uuid, 1)

        first = M.check_now(store, p.project_id, client, wait=60)
        assert first.outcome in ("no_new_snapshot", "no_net_change"), first
        assert M.runs(store, p.project_id) == []

        page.text = "星衡半导体新闻中心\n最新动态\n星衡半导体8英寸碳化硅产线量产时间推迟至2027年第四季度\n"
        second = M.check_now(store, p.project_id, client, wait=60)
        assert second.outcome == "assessed", second
        version = client.system_info().get("version")

    d = M.run_dir(store, p.project_id, second.run_id)
    run = vm.load_run(d, d / "theses.json")
    rows = [(a["assumption_id"], a["direction"], a["basis"]) for a in run.assessments()]
    assert rows == [("A1", "weakens", "rule")]
    ev = run.evidence[run.assessments()[0]["evidence_ids"][0]]
    assert any("推迟" in x for x in ev.excerpt_added) and ev.grade == "A"
    rec = json.loads((d / "run_record.json").read_text(encoding="utf-8"))
    assert rec["input_mode"] == "live_api" and rec["cdio_version"] == version
    assert run.provenance["data_nature"] == "fictional"        # a local test page is not public material
    assert {f: f.read_bytes() for f in store.project_dir(p.project_id).glob("*.json") if f in project_files} \
        == project_files
    print("R6 E2E:", second.message, rows, ev.evidence_id, version)


@pytest.mark.skipif(os.getenv("TECHSIGNAL_LIVE_NETWORK") != "1", reason="needs public network (GitHub)")
def test_public_url_without_change_is_reported_as_no_change(tmp_path):
    """Real public page, real changedetection.io: two checks in a row must not produce a finding."""
    url = "https://raw.githubusercontent.com/tenstorrent/tt-metal/main/README.md"
    store = P.ProjectStore(tmp_path / "projects")
    spec = P.spec_from_form(name="Tenstorrent", question="Blackhole 大模型推理软件成熟度",
                            judgement="仍处早期。", assumptions=[{"statement": "尚无 70B 官方记录", "weaken": "70B"}],
                            urls=f"{url} 官方 README", keywords="Blackhole")
    p = store.create(spec, ready=True)
    with LiveCdio(PY, log_path=tmp_path / "cdio.log") as cdio:
        client = M.client_for(cdio.url, cdio.client.api_key, timeout=30)
        plan = M.plan_watches(p, M.load_state(store, p.project_id), M.connect(client)["watches"])
        M.apply_plan(store, p.project_id, client, plan, confirmed=True)
        cdio.wait_for_snapshots(M.load_state(store, p.project_id).watches[0].watch_uuid, 1)
        r1 = M.check_now(store, p.project_id, client, wait=60)
        r2 = M.check_now(store, p.project_id, client, wait=60)
    assert r1.outcome in ("no_new_snapshot", "no_net_change") and r2.outcome == "no_new_snapshot", (r1, r2)
    assert M.runs(store, p.project_id) == []
    print("R6 public no-change:", r1.message, "|", r2.message)
