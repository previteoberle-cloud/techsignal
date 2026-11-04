"""R6 Checkpoint 3: the multi-assumption model protocol over real HTTP against a local PROTOCOL STUB.

The stub returns canned replies. It is not a language model: these tests verify the request, the
structure checks, citation checks, change-kind gates, conservative fallbacks, limits and usage
accounting - not reasoning quality. No real model service is called here (see test_r6_real_llm.py).
"""
import json

import pytest
from test_r2_provider_http import SECRET, Stub, _chat

from techsignal.cli import main
from techsignal.models import Assumption, Basis, Direction, SourceProfile, Thesis
from techsignal.pipeline import run_pipeline
from techsignal.providers import OpenAICompatibleProvider
from techsignal.store import SourceRegistry

TH = Thesis(thesis_id="TH-R6", title="星衡半导体：碳化硅产线", conclusion="8 英寸产线将在 2026 年内量产，良率可达 60% 以上。",
            entities=["星衡半导体"], topic_keywords=["碳化硅"], as_of="2026-11-01", owner="测试", fictional=True,
            assumptions=[Assumption(assumption_id="A1", statement="8 英寸产线 2026 年内量产"),
                         Assumption(assumption_id="A2", statement="良率达到 60% 以上")])
REG = SourceRegistry([SourceProfile(domain="xh-semi.example", name="星衡半导体官网", source_type="official", grade="A"),
                      SourceProfile(domain="forum.example", name="论坛", source_type="forum", grade="D")])


def _report(added=("星衡半导体宣布 8 英寸碳化硅产线已投产，良率提升至65%",), removed=(), url="https://xh-semi.example/news",
            n=1):
    watches = []
    for i in range(n):
        diff = "--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1 @@\n 新闻中心\n" + "".join(f"-{x}\n" for x in removed) \
            + "".join(f"+{x}{'' if i == 0 else f'（{i}）'}\n" for x in added)
        watches.append({"uuid": f"00000000-0000-4000-8000-00000000010{i}", "title": "星衡半导体新闻中心", "url": url,
                        "changes": 1, "new_in_period": False, "no_net_difference": False, "error": None,
                        "from_timestamp": 100, "to_timestamp": 200 + i, "diff": diff, "truncated": False})
    return {"from": 1, "to": 300, "mode": "net_change", "total": n, "offset": 0, "limit": 50, "monitors_checked": n,
            "changes_total": n, "incomplete": False, "watches": watches}


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def _prov(stub, **kw):
    return OpenAICompatibleProvider(stub.base, "stub-model", api_key=SECRET, timeout=kw.pop("timeout", 5),
                                    protocol="multi", **kw)


def _verdicts(*items, usage=None):
    body = _chat({"assessments": [dict(zip(("assumption_id", "change_kind", "direction", "cited_evidence_ids"), it),
                                       rationale="依据新增内容。", confidence="high") for it in items]})
    if usage:
        body["usage"] = usage
    return body


EV = "$EV"   # replaced with the evidence id of the change


def _run(stub, reply, report=None, **kw):
    report = report or _report()
    if not isinstance(reply, tuple):
        reply = (200, reply)
    # The evidence id is a content hash: find it from a dry run without a provider.
    ev_id = next(iter(run_pipeline(report, [TH], REG, persist=False).evidence))
    body = reply[1]
    if isinstance(body, dict):
        body = json.loads(json.dumps(body).replace(EV, ev_id))
    stub.replies = [(reply[0], body)]
    p = _prov(stub, **kw)
    return run_pipeline(report, [TH], REG, provider=p, persist=False), p, ev_id


def _rows(res):
    return [(a.assumption_id, a.direction.value, a.judgement_source) for a in res.assessments]


# ---------------------------------------------------------------- happy path ------------------------

def test_one_change_two_assumptions(stub):
    res, p, ev = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV]), ("A2", "new_fact", "supports", [EV]),
                                      usage={"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020}))
    assert _rows(res) == [("A1", "supports", "model_validated"), ("A2", "supports", "model_validated")]
    a1 = res.assessments[0]
    assert a1.basis == Basis.MODEL and a1.review_status == "pending_human" and a1.confidence == "medium"
    assert a1.model_suggestion["direction"] == "supports" and a1.model_suggestion["confidence"] == "high"
    assert a1.evidence_ids == [ev] and len({a.assessment_id for a in res.assessments}) == 2
    assert any("允许定向" in g for g in a1.gate_log)
    prov = res.brief.provenance
    assert prov["model_layer"] == "real_model" and prov["model_protocol"] == "multi"
    assert prov["model_usage"]["calls_succeeded"] == 1 and prov["model_usage"]["total_tokens"] == 1020
    assert "模型推断" in res.markdown and "Mock" not in res.markdown
    path, headers, payload = stub.requests[0]
    assert path == "/v1/chat/completions" and payload["messages"][0]["content"].startswith("你是硬科技投资研究助理")
    assert "A1: 8 英寸产线" in payload["messages"][1]["content"] and "A2: 良率" in payload["messages"][1]["content"]
    assert res.assessments[0].thesis_status_change == "none"


def test_unrelated_verdict(stub):
    res, _, _ = _run(stub, _verdicts(("A1", "unrelated", "unrelated", [])))
    assert _rows(res) == [(None, "unrelated", "model_validated")]


# ---------------------------------------------------------------- validation -----------------------

def test_citing_nonexistent_evidence_rejects_only_that_verdict(stub):
    res, _, _ = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV]), ("A2", "new_fact", "weakens", ["EV-ffffffffff"])))
    assert _rows(res) == [("A2", "insufficient", "model_rejected"), ("A1", "supports", "model_validated")]
    rej = res.assessments[0]
    assert rej.basis == Basis.RULE and rej.tentative_direction is None
    assert "EV-ffffffffff" in " ".join(rej.gate_log) and rej.model_suggestion["direction"] == "weakens"
    assert res.stats.provider_rejections == 1


@pytest.mark.parametrize("items,why", [
    ((("A9", "new_fact", "supports", [EV]),), "不存在的假设"),
    ((("A1", "new_fact", "weakens", []),), "未引用任何证据"),
    ((("A1", "new_fact", "weakens", [EV]), ("A1", "new_fact", "supports", [EV])), "相互矛盾"),
])
def test_invalid_verdicts_fall_back_to_insufficient(stub, items, why):
    res, _, _ = _run(stub, _verdicts(*items))
    assert {a.direction for a in res.assessments} == {Direction.INSUFFICIENT}
    assert all(a.judgement_source == "model_rejected" for a in res.assessments)
    assert why in " ".join(g for a in res.assessments for g in a.gate_log)


@pytest.mark.parametrize("reply", [
    _chat("这不是 JSON"),
    _chat({"assessments": [{"assumption_id": "A1", "direction": "supports", "rationale": "x"}]}),   # no change_kind
    _chat({"direction": "supports", "rationale": "单一判断格式", "cited_evidence_ids": []}),         # old protocol
    _chat({"assessments": "A1"}),
    {"no": "choices"},
    (500, {"error": "boom"}),
    (401, {"error": "bad key"}),
])
def test_malformed_or_failed_calls_degrade_without_breaking_the_run(stub, reply):
    res, p, _ = _run(stub, reply)
    assert _rows(res) == [(None, "insufficient", "model_failed")]
    assert res.assessments[0].basis == Basis.RULE and res.stats.provider_failures == 1
    assert p.usage.calls_failed == 1 and p.usage.calls_succeeded == 0
    assert res.brief.provenance["model_layer"] == "real_model_unused"
    assert "没有成功的模型调用" in res.markdown and "模型推断" not in res.markdown
    assert SECRET not in res.markdown + json.dumps(res.decision_log(), ensure_ascii=False)


def test_timeout_degrades(stub):
    stub.delay = 2.0
    res, p, _ = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV])), timeout=0.5)
    assert _rows(res) == [(None, "insufficient", "model_failed")] and p.usage.calls_failed == 1


# ---------------------------------------------------------------- change-kind gates -----------------

@pytest.mark.parametrize("kind", ["removed_record", "negation", "restructure", "unclear"])
def test_only_a_new_fact_can_carry_a_direction(stub, kind):
    res, _, _ = _run(stub, _verdicts(("A1", kind, "weakens", [EV])))
    a = res.assessments[0]
    assert (a.direction, a.judgement_source, a.change_kind) == (Direction.INSUFFICIENT, "model_validated", kind)
    assert a.tentative_direction is None and a.model_suggestion["direction"] == "weakens"
    assert any("不是新出现的事实" in g for g in a.gate_log) and a.confidence == "low"


def test_new_fact_claim_on_a_pure_deletion_is_downgraded(stub):
    rep = _report(added=(), removed=("星衡半导体 8 英寸碳化硅产线 2026 年量产计划",))
    res, _, _ = _run(stub, _verdicts(("A1", "new_fact", "weakens", [EV])), report=rep)
    a = res.assessments[0]
    assert a.direction == Direction.INSUFFICIENT and any("没有新增内容" in g for g in a.gate_log)


def test_grade_gate_still_applies_to_model_suggestions(stub):
    res, _, _ = _run(stub, _verdicts(("A1", "new_fact", "weakens", [EV])), report=_report(url="https://forum.example/t/1"))
    a = res.assessments[0]
    assert a.direction == Direction.INSUFFICIENT and a.tentative_direction == Direction.WEAKENS
    assert any("证据等级门槛" in g for g in a.gate_log)


# ---------------------------------------------------------------- limits and usage -----------------

def test_call_budget_per_run(stub):
    res, p, _ = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV])), report=_report(n=3), max_calls=1)
    assert len(stub.requests) == 1
    srcs = [a.judgement_source for a in res.assessments]
    assert srcs.count("model_skipped") == 2 and p.usage.calls_skipped_budget == 2
    assert res.brief.provenance["model_usage"]["calls_skipped_budget"] == 2


def test_input_length_limit(stub):
    long = ["星衡半导体 碳化硅 " + "很长的说明" * 200] * 5
    res, p, _ = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV])), report=_report(added=long),
                     max_input_chars=1500)
    user = stub.requests[0][2]["messages"][1]["content"]
    assert len(user) < 1500 + 800 and p.usage.inputs_truncated == 1
    assert any("未提供" in x for x in res.assessments[0].limitations)


def test_usage_absent_is_reported_as_absent(stub):
    res, p, _ = _run(stub, _verdicts(("A1", "new_fact", "supports", [EV])))
    assert p.usage.usage_reported == 0 and "服务未报告用量" in res.markdown


def test_rules_still_decide_first(stub):
    """A change the rules can decide never reaches the model."""
    th = TH.model_copy(update={"assumptions": [TH.assumptions[0].model_copy(update={"weaken_patterns": ["推迟"]})]})
    stub.replies = [(200, _verdicts(("A1", "new_fact", "supports", ["x"])))]
    res = run_pipeline(_report(added=("星衡半导体碳化硅产线量产推迟至2027年",)), [th], REG, provider=_prov(stub),
                       persist=False)
    assert stub.requests == [] and res.assessments[0].basis == Basis.RULE
    assert res.assessments[0].judgement_source is None          # rule-only records keep the R5 form
    assert "judgement_source" not in json.dumps(res.decision_log())


def test_cli_reads_operator_config_and_keeps_the_key_out_of_outputs(stub, tmp_path, monkeypatch):
    ev_id = next(iter(run_pipeline(_report(), [TH], REG, persist=False).evidence))
    stub.replies = [(200, json.loads(json.dumps(_verdicts(("A1", "new_fact", "supports", [EV]))).replace(EV, ev_id)))]
    (tmp_path / "r.json").write_text(json.dumps(_report()), encoding="utf-8")
    (tmp_path / "t.json").write_text(json.dumps({"theses": [TH.model_dump(mode="json")]}), encoding="utf-8")
    (tmp_path / "s.json").write_text(json.dumps({"sources": [s.model_dump(mode="json") for s in REG._by_domain["xh-semi.example"]]}),
                                     encoding="utf-8")
    for k, v in {"TECHSIGNAL_LLM_BASE_URL": stub.base, "TECHSIGNAL_LLM_MODEL": "stub-model",
                 "TECHSIGNAL_LLM_API_KEY": SECRET, "TECHSIGNAL_LLM_MAX_CALLS": "3"}.items():
        monkeypatch.setenv(k, v)
    out = tmp_path / "o"
    assert main(["run", "--changes", str(tmp_path / "r.json"), "--theses", str(tmp_path / "t.json"),
                 "--sources", str(tmp_path / "s.json"), "--provider", "openai-compatible", "--out", str(out)]) == 0
    log = json.loads((out / "decision_log.json").read_text(encoding="utf-8"))
    assert log["assessments"][0]["judgement_source"] == "model_validated"
    assert log["provenance"]["model_usage"]["calls_attempted"] == 1
    for f in out.iterdir():
        assert SECRET not in f.read_text(encoding="utf-8")
    assert {k.lower(): v for k, v in stub.requests[0][1].items()}["authorization"] == f"Bearer {SECRET}"


def test_env_limits_must_be_numbers(monkeypatch):
    from techsignal.providers import ProviderError
    monkeypatch.setenv("TECHSIGNAL_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("TECHSIGNAL_LLM_MODEL", "m")
    monkeypatch.setenv("TECHSIGNAL_LLM_MAX_CALLS", "many")
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider.from_env()
