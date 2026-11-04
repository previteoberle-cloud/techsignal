"""R2 / P2: OpenAICompatibleProvider over REAL HTTP against a local protocol stub.

The stub is a stdlib HTTP server that speaks the OpenAI-compatible /chat/completions wire format and
returns canned replies. It is NOT a language model: these tests verify request construction,
transport, error handling and validation, not reasoning quality. No real model service was called
(none is authorised for this project; see docs/R2_ACCEPTANCE.md).
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import DEMO, SCENARIOS
from techsignal.cli import main
from techsignal.models import Basis, Direction
from techsignal.pipeline import run_pipeline
from techsignal.providers import ModelRequest, OpenAICompatibleProvider, ProviderError

SECRET = "sk-test-DO-NOT-LEAK-123"


class Stub:
    def __init__(self):
        self.replies = []      # list of (status, body) - served in order, last one repeats
        self.requests = []
        self.delay = 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(n))))
                if outer.delay:
                    time.sleep(outer.delay)
                status, body = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def _chat(obj):
    content = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.fixture()
def req(theses):
    th = next(t for t in theses if t.thesis_id == "TH-002")
    return ModelRequest(replay_key="k", thesis=th, event_title="澜芯光电产品中心", event_url="https://x.example/p",
                        added_lines=["1.6T 硅光模块：送样时间另行通知"], removed_lines=["1.6T 硅光模块：预计2026年第四季度送样"],
                        evidence_ids=["EV-aaaaaaaaaa"], source_grade="A", rule_notes=["无确定性信号"])


def _provider(stub, **kw):
    return OpenAICompatibleProvider(stub.base, "stub-model", api_key=SECRET, timeout=kw.pop("timeout", 5), **kw)


def test_request_over_http(stub, req):
    stub.replies = [(200, _chat({"direction": "weakens", "assumption_id": "B1", "rationale": "送样时间改为另行通知",
                                 "cited_evidence_ids": ["EV-aaaaaaaaaa"], "confidence": "medium"}))]
    j = _provider(stub).judge(req)
    assert j.direction == Direction.WEAKENS and j.cited_evidence_ids == ["EV-aaaaaaaaaa"]
    path, headers, payload = stub.requests[0]
    assert path == "/v1/chat/completions"
    assert {k.lower(): v for k, v in headers.items()}["authorization"] == f"Bearer {SECRET}"
    assert payload["model"] == "stub-model" and payload["temperature"] == 0
    assert payload["messages"][0]["role"] == "system" and "EV-aaaaaaaaaa" in payload["messages"][1]["content"]


@pytest.mark.parametrize("status,body", [
    (401, {"error": {"message": "invalid api key"}}),
    (429, {"error": {"message": "rate limited"}}),
    (500, b"internal error"),
    (200, b"not json at all"),
    (200, {"choices": []}),
    (200, _chat("I think it weakens the thesis.")),                      # prose instead of JSON
    (200, _chat({"direction": "maybe", "rationale": "x", "cited_evidence_ids": []})),  # bad enum
    (200, _chat({"direction": "weakens", "rationale": "x", "cited_evidence_ids": [], "extra": 1})),  # extra field
], ids=["401", "429", "500", "html", "no-choices", "prose", "bad-enum", "extra-field"])
def test_failures_become_provider_errors_without_leaking_key(stub, req, status, body):
    stub.replies = [(status, body)]
    with pytest.raises(ProviderError) as e:
        _provider(stub).judge(req)
    assert SECRET not in str(e.value)


def test_timeout_is_provider_error(stub, req):
    stub.delay = 1.0
    stub.replies = [(200, _chat({"direction": "insufficient", "rationale": "x", "cited_evidence_ids": []}))]
    with pytest.raises(ProviderError):
        _provider(stub, timeout=0.2).judge(req)


def test_fenced_json_is_accepted(stub, req):
    stub.replies = [(200, _chat('```json\n{"direction":"insufficient","rationale":"信息不足","cited_evidence_ids":[]}\n```'))]
    assert _provider(stub).judge(req).direction == Direction.INSUFFICIENT


def _s12():
    return json.loads((SCENARIOS / "S12_mock_model.json").read_text(encoding="utf-8"))["report"]


def test_pipeline_labels_http_provider_as_model_and_still_validates(stub, theses, sources):
    """Through the full pipeline: output labelled Basis.MODEL (not Mock), confidence capped, citations checked."""
    stub.replies = [(200, _chat({"direction": "weakens", "assumption_id": "B1", "rationale": "送样时间改为另行通知",
                                 "cited_evidence_ids": [], "confidence": "high"}))]
    # no citation -> rejected by the pipeline's own validation
    r = run_pipeline(_s12(), theses, sources, provider=_provider(stub))
    (a,) = r.assessments
    assert a.direction == Direction.INSUFFICIENT and r.stats.provider_rejections == 1

    ev_id = a.evidence_ids[0]
    stub.replies = [(200, _chat({"direction": "weakens", "assumption_id": "B1", "rationale": "送样时间改为另行通知",
                                 "cited_evidence_ids": [ev_id], "confidence": "high"}))]
    r = run_pipeline(_s12(), theses, sources, provider=_provider(stub))
    (a,) = r.assessments
    assert (a.direction, a.basis, a.confidence) == (Direction.WEAKENS, Basis.MODEL, "medium")
    assert "Mock" not in r.markdown and "模型推断" in r.markdown


def test_provider_outage_degrades_to_insufficient(stub, theses, sources):
    stub.replies = [(503, b"down")]
    r = run_pipeline(_s12(), theses, sources, provider=_provider(stub))
    (a,) = r.assessments
    assert a.direction == Direction.INSUFFICIENT and r.stats.provider_failures == 1


def test_cli_without_model_config_fails_cleanly(tmp_path, monkeypatch):
    for k in ("TECHSIGNAL_LLM_BASE_URL", "TECHSIGNAL_LLM_MODEL", "TECHSIGNAL_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    rc = main(["run", "--changes", str(DEMO / "changes_report.json"), "--theses", str(DEMO / "theses.json"),
               "--sources", str(DEMO / "sources.json"), "--provider", "openai-compatible", "--out", str(tmp_path / "o")])
    assert rc == 2 and not (tmp_path / "o").exists()
