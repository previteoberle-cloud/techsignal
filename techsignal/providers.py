"""Model layer: a replaceable provider interface.

Implemented providers:
  - MockReplayProvider: replays hand-written judgements from a JSON file. Used by every test and the
    demo. Its output is labelled Basis.MOCK_MODEL everywhere and is NOT model reasoning.
  - OpenAICompatibleProvider: POSTs to an OpenAI-compatible /chat/completions endpoint (OpenAI,
    DeepSeek, Qwen, Ollama, vLLM...). R1 tests it only against an injected fake transport; it has
    NOT been run against a real model service.

The pipeline validates every provider output (see assess.py) - a provider can't cite evidence that
doesn't exist or pick an assumption the thesis doesn't have.
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import Direction, Thesis


class ProviderError(RuntimeError):
    pass


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay_key: str
    thesis: Thesis
    event_title: str
    event_url: str
    added_lines: list[str]
    removed_lines: list[str]
    evidence_ids: list[str]
    source_grade: str
    rule_notes: list[str]


class ModelJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    assumption_id: str | None = None
    rationale: str
    cited_evidence_ids: list[str]
    confidence: Literal["low", "medium", "high"] = "low"


class LLMProvider(Protocol):
    name: str
    is_mock: bool

    def judge(self, request: ModelRequest) -> ModelJudgement: ...


# ---------------------------------------------------------------------------
# Mock replay
# ---------------------------------------------------------------------------

EVENT_EVIDENCE_TOKEN = "$EVENT_EVIDENCE"


class MockReplayProvider:
    """Replays judgements keyed by '<watch_uuid>@<to_timestamp>#<thesis_id>'.

    Entries are written by hand to exercise the pipeline. `$EVENT_EVIDENCE` in cited_evidence_ids is
    replaced by the request's own evidence ids; any other id is passed through untouched, so a test
    can inject a bad citation and check that it gets rejected.
    """

    name = "mock-replay"
    is_mock = True

    def __init__(self, entries: dict[str, dict]):
        self.entries = entries
        self.calls: list[str] = []

    @classmethod
    def load(cls, path: Path) -> MockReplayProvider:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            raise ProviderError(f"{path} 不是 Mock 回放文件（缺少 'entries'）")
        return cls(data["entries"])

    def judge(self, request: ModelRequest) -> ModelJudgement:
        self.calls.append(request.replay_key)
        entry = self.entries.get(request.replay_key)
        if entry is None:
            raise ProviderError(f"Mock 回放中没有条目 {request.replay_key}")
        entry = dict(entry)
        entry.pop("_note", None)
        cited: list[str] = []
        for c in entry.get("cited_evidence_ids", []):
            cited.extend(request.evidence_ids if c == EVENT_EVIDENCE_TOKEN else [c])
        entry["cited_evidence_ids"] = cited
        try:
            return ModelJudgement.model_validate(entry)
        except ValidationError as e:
            raise ProviderError(f"Mock 条目 {request.replay_key} 格式错误：{e.errors()[0]['msg']}") from None


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP provider (untested against a live service in R1)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是硬科技投资研究助理。任务：判断一条新出现的网页变化对一条既有研究判断的影响。\n"
    "规则：\n"
    "1. 只能依据给出的变化内容，不得引入外部知识或臆测。\n"
    "2. 新信息出现不等于结论改变；只有变化内容直接关系到某条关键假设时，才判为 supports 或 weakens。\n"
    "3. 不确定、信息含糊或来源等级低时，判为 insufficient。与研究判断无关时，判为 unrelated。\n"
    "4. cited_evidence_ids 只能从给定的证据编号中选择；supports/weakens 必须至少引用一个。\n"
    "5. 只输出一个 JSON 对象，字段：direction(supports|weakens|unrelated|insufficient), "
    "assumption_id(字符串或 null), rationale(中文，一到两句), cited_evidence_ids(列表), confidence(low|medium|high)。"
)


def build_messages(req: ModelRequest) -> list[dict]:
    th = req.thesis
    assumptions = "\n".join(f"- {a.assumption_id}: {a.statement}" for a in th.assumptions)
    user = (
        f"研究判断 {th.thesis_id}：{th.conclusion}\n关键假设：\n{assumptions}\n\n"
        f"来源：{req.event_url}（证据等级 {req.source_grade}）\n页面标题：{req.event_title}\n"
        f"新增内容：\n" + "\n".join(f"+ {x}" for x in req.added_lines) + "\n"
        "删除内容：\n" + "\n".join(f"- {x}" for x in req.removed_lines) + "\n"
        f"可引用的证据编号：{', '.join(req.evidence_ids)}\n"
        f"规则层备注：{'；'.join(req.rule_notes) or '无'}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


Transport = Callable[[str, dict, bytes, int], bytes]


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL comes from operator config
        return resp.read()


class OpenAICompatibleProvider:
    is_mock = False

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 60,
                 transport: Transport | None = None):
        if not base_url or not model:
            raise ProviderError("OpenAI 兼容 Provider 需要 base_url 和 model")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport or _urllib_transport
        self.name = f"openai-compatible:{model}"
        self.calls = 0

    @classmethod
    def from_env(cls) -> OpenAICompatibleProvider:
        return cls(base_url=os.getenv("TECHSIGNAL_LLM_BASE_URL", ""),
                   model=os.getenv("TECHSIGNAL_LLM_MODEL", ""),
                   api_key=os.getenv("TECHSIGNAL_LLM_API_KEY", ""))

    def judge(self, request: ModelRequest) -> ModelJudgement:
        self.calls += 1
        payload = {"model": self.model, "messages": build_messages(request), "temperature": 0,
                   "response_format": {"type": "json_object"}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            raw = self.transport(f"{self.base_url}/chat/completions", headers,
                                 json.dumps(payload, ensure_ascii=False).encode("utf-8"), self.timeout)
            content = json.loads(raw)["choices"][0]["message"]["content"]
            return ModelJudgement.model_validate(json.loads(_strip_fences(content)))
        except ProviderError:
            raise
        except (ValidationError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise ProviderError(f"模型输出无法解析：{e}") from None
        except OSError as e:
            raise ProviderError(f"模型服务调用失败：{e}") from None


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
