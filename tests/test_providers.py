import json

import pytest
from techsignal.models import Direction
from techsignal.providers import (
    EVENT_EVIDENCE_TOKEN,
    MockReplayProvider,
    ModelRequest,
    OpenAICompatibleProvider,
    ProviderError,
)


@pytest.fixture()
def req(theses):
    return ModelRequest(replay_key="u@1#TH-002", thesis=theses[1], event_title="t", event_url="https://x.example",
                        added_lines=["a"], removed_lines=["b"], evidence_ids=["EV-aaaaaaaaaa"], source_grade="A",
                        rule_notes=[])


def test_mock_substitutes_event_evidence(req):
    p = MockReplayProvider({req.replay_key: {"direction": "weakens", "assumption_id": "B1", "rationale": "r",
                                             "cited_evidence_ids": [EVENT_EVIDENCE_TOKEN], "_note": "x"}})
    j = p.judge(req)
    assert j.direction == Direction.WEAKENS and j.cited_evidence_ids == ["EV-aaaaaaaaaa"]
    assert p.is_mock and p.calls == [req.replay_key]


def test_mock_missing_entry(req):
    with pytest.raises(ProviderError):
        MockReplayProvider({}).judge(req)


def test_mock_bad_entry(req):
    with pytest.raises(ProviderError):
        MockReplayProvider({req.replay_key: {"direction": "maybe"}}).judge(req)


def _fake(reply, seen):
    def transport(url, headers, body, timeout):
        seen.append((url, headers, json.loads(body)))
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply).encode()
    return transport


def _chat(content):
    return {"choices": [{"message": {"content": content}}]}


def test_openai_compatible_request_and_parse(req):
    seen = []
    content = '```json\n{"direction":"insufficient","assumption_id":null,"rationale":"不确定","cited_evidence_ids":[],"confidence":"low"}\n```'
    p = OpenAICompatibleProvider("http://llm.local/v1/", "some-model", api_key="k", transport=_fake(_chat(content), seen))
    j = p.judge(req)
    assert j.direction == Direction.INSUFFICIENT and not p.is_mock
    url, headers, payload = seen[0]
    assert url == "http://llm.local/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert payload["model"] == "some-model" and payload["response_format"] == {"type": "json_object"}
    assert "EV-aaaaaaaaaa" in payload["messages"][1]["content"]


@pytest.mark.parametrize("reply", [_chat("not json"), {"choices": []}, _chat('{"direction":"weakens"}'),
                                   OSError("connection refused")])
def test_openai_compatible_failures_become_provider_errors(req, reply):
    p = OpenAICompatibleProvider("http://llm.local/v1", "m", transport=_fake(reply, []))
    with pytest.raises(ProviderError):
        p.judge(req)


def test_openai_compatible_requires_config(monkeypatch):
    monkeypatch.delenv("TECHSIGNAL_LLM_BASE_URL", raising=False)
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider.from_env()
