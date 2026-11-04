"""R6 independent evaluation: human labels vs the rule layer vs a protocol stub vs a real model.

    python -m techsignal.eval_r6                       # rule layer + protocol stub; real model BLOCKED
    python -m techsignal.eval_r6 --md docs/R6_EVAL.md --json eval/r6_results.json

Columns are never mixed:
  human        the analyst's label in eval/r6_cases.json (fictional cases written for this set)
  rule         the deterministic pipeline, no model
  stub         a PRE-WRITTEN reply per case, sent through the real OpenAI-compatible parsing path and
               labelled Mock: it tests validation, gates and fallbacks, not any model's ability
  real_model   only when an operator configured an OpenAI-compatible service AND set
               TECHSIGNAL_REAL_LLM_AUTHORIZED=1; otherwise the column says BLOCKED. No accuracy is
               reported for a model that was not called.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import projects as P
from .pipeline import run_pipeline
from .providers import OpenAICompatibleProvider, ProviderError
from .store import SourceRegistry

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "r6_cases.json"
DIRS = ("supports", "weakens", "insufficient", "unrelated")
BLOCKED = "BLOCKED：未提供已授权的模型服务"


class ProtocolStub(OpenAICompatibleProvider):
    """Pre-written replies through the real parsing path. Labelled Mock everywhere (is_mock=True)."""
    is_mock = True

    def __init__(self, replies_by_url: dict[str, object]):
        self.replies = replies_by_url
        super().__init__("http://protocol-stub.invalid/v1", "protocol-stub", protocol="multi",
                         transport=self._transport)
        self.name = "protocol-stub（预置回答，非模型）"

    def _transport(self, url, headers, body, timeout):
        user = json.loads(body)["messages"][1]["content"]
        src = re.search(r"来源：(\S+)（", user).group(1)
        ev = re.search(r"可引用的证据编号：(EV-[0-9a-f]+)", user).group(1)
        reply = self.replies.get(src)
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False).replace("$EV", ev)
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def real_model_status() -> str | None:
    """None when a real, authorised model may be called; otherwise why not."""
    if not (os.getenv("TECHSIGNAL_LLM_BASE_URL") and os.getenv("TECHSIGNAL_LLM_MODEL")):
        return BLOCKED + "（未设置 TECHSIGNAL_LLM_BASE_URL / TECHSIGNAL_LLM_MODEL）"
    if os.getenv("TECHSIGNAL_REAL_LLM_AUTHORIZED") != "1":
        return BLOCKED + "（未设置 TECHSIGNAL_REAL_LLM_AUTHORIZED=1）"
    return None


# ---------------------------------------------------------------------------
# Case -> pipeline inputs (through the R6 project path: plain text -> thesis/sources)
# ---------------------------------------------------------------------------

def build_project(defn: dict, key: str) -> tuple[list, SourceRegistry]:
    spec = P.spec_from_form(
        name=defn["name"], aliases=defn.get("aliases", ""), question=defn["question"], judgement=defn["judgement"],
        assumptions=defn["assumptions"], keywords=defn.get("keywords", ""),
        urls="\n".join(f"{s['url']} {s.get('label', '')}".strip() for s in defn["sources"]),
        source_types={s["url"]: s.get("type", "official") for s in defn["sources"]}, owner="评测集")
    spec = P.check_spec(spec, ready=True)
    proj = P.Project(project_id="p-" + (key.encode().hex() + "0" * 12)[:12], status="ready", version=1,
                     created_at="2026-11-01T00:00:00Z", updated_at="2026-11-01T00:00:00Z", spec=spec)
    thesis = P.build_thesis(proj).model_copy(update={"fictional": True})
    return [thesis], SourceRegistry(P.build_sources(proj))


def build_report(case: dict, n: int) -> dict:
    watches = []
    for i, ch in enumerate(case["changes"]):
        diff = ("--- 2026-10-01T00:00:00Z\n+++ 2026-10-02T00:00:00Z\n@@ -1 +1 @@\n " + ch["title"] + "\n"
                + "".join(f"-{x}\n" for x in ch.get("removed", [])) + "".join(f"+{x}\n" for x in ch.get("added", [])))
        watches.append({"uuid": f"00000000-0000-4000-8000-{n:04d}{i:08d}", "title": ch["title"], "url": ch["url"],
                        "changes": 1, "new_in_period": False, "no_net_difference": False, "error": None,
                        "from_timestamp": 1790000000, "to_timestamp": 1790086400 + i, "diff": diff, "truncated": False})
    return {"from": 1789999999, "to": 1790200000, "mode": "net_change", "total": len(watches), "offset": 0,
            "limit": 50, "monitors_checked": len(watches), "changes_total": len(watches), "incomplete": False,
            "watches": watches}


def outcome_by_assumption(result, thesis) -> dict[str, dict]:
    out = {}
    for a in thesis.assumptions:
        rows = [x for x in result.assessments if x.assumption_id == a.assumption_id
                or (x.assumption_id is None and x.direction.value != "unrelated")]
        dirs = {x.direction.value for x in rows}
        if not dirs:
            d = "unrelated"
        elif len(dirs) == 1:
            d = dirs.pop()
        else:
            d = "insufficient"
        out[a.assumption_id] = {"direction": d,
                                "sources": sorted({x.judgement_source or x.basis.value for x in rows}) or ["rule"]}
    return out


def score(human: str, got: str, safe: list[str] | None) -> str:
    if got == human:
        return "correct"
    if got in (safe or []) or got == "insufficient":
        return "conservative"          # asks a human instead of deciding: safe, but incomplete
    if got in ("supports", "weakens"):
        return "wrong_direction"       # a directional call the analyst disagrees with: unsafe
    return "missed"                    # called unrelated though the analyst sees an impact


def run_case(case: dict, n: int, projects: dict, system: str) -> dict:
    theses, reg = build_project(projects[case["project"]], case["project"])
    report = build_report(case, n)
    provider = None
    if system == "stub":
        reply = case["protocol_stub"]["reply"]
        provider = ProtocolStub({ch["url"]: reply for ch in case["changes"]})
    elif system == "real_model":
        provider = OpenAICompatibleProvider.from_env()
        if provider.protocol != "multi":
            raise ProviderError("真实模型评测需要 multi 协议")
    res = run_pipeline(report, theses, reg, provider=provider, persist=False)
    got = outcome_by_assumption(res, theses[0])
    rows = {}
    for aid, human in case["human"].items():
        g = got[aid]
        rows[aid] = {"human": human, "got": g["direction"], "sources": g["sources"],
                     "score": score(human, g["direction"], (case.get("safe") or {}).get(aid))}
    usage = provider.usage.model_dump() if provider is not None else None
    return {"rows": rows, "model_calls": (usage or {}).get("calls_attempted", 0), "usage": usage}


def evaluate(cases_path: Path = CASES) -> dict:
    data = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    blocked = real_model_status()
    systems = ["rule", "stub"] + ([] if blocked else ["real_model"])
    results = []
    for n, case in enumerate(data["cases"], 1):
        entry = {"id": case["id"], "category": case["category"], "title": case["title"],
                 "stub_purpose": case["protocol_stub"]["purpose"]}
        for s in systems:
            entry[s] = run_case(case, n, data["projects"], s)
        if blocked:
            entry["real_model"] = {"status": "BLOCKED"}
        results.append(entry)
    summary = {}
    for s in systems:
        counts = {"correct": 0, "conservative": 0, "missed": 0, "wrong_direction": 0}
        for r in results:
            for row in r[s]["rows"].values():
                counts[row["score"]] += 1
        summary[s] = {"labelled_pairs": sum(counts.values()), **counts,
                      "model_calls": sum(r[s]["model_calls"] for r in results)}
    if blocked:
        summary["real_model"] = {"status": blocked}
    return {"cases": len(results), "categories": sorted({r["category"] for r in results}),
            "summary": summary, "results": results}


ZH = {"supports": "支持", "weakens": "削弱", "insufficient": "待核实", "unrelated": "无关"}
SCORE_ZH = {"correct": "✅ 一致", "conservative": "🟡 保守（交人工）", "missed": "⚪ 漏判", "wrong_direction": "❌ 方向错误"}
CAT_ZH = {"multi_assumption": "多假设命中", "paraphrase": "同义改写", "negation": "否定表达",
          "deletion_restructure": "删除与文档重构", "entity_ambiguity": "公司名称歧义", "source_conflict": "来源冲突",
          "low_credibility": "低可信来源", "unrelated": "无关变化", "model_bad_citation": "模型引用不存在的证据",
          "model_format_error": "模型格式错误", "prompt_injection": "页面提示注入"}


def to_markdown(rep: dict) -> str:
    s = rep["summary"]
    lines = [
        "# TechSignal R6 评测结果（自动生成）", "",
        "由 `python -m techsignal.eval_r6 --md docs/R6_EVAL.md` 生成，请勿手工修改数字。",
        "", "- 评测集：`eval/r6_cases.json`，"
        f"{rep['cases']} 个虚构案例，覆盖 {len(rep['categories'])} 类情形。人工标注由本项目编写，代表研究员的预期判断。",
        "- **规则层**：确定性规则，不使用模型。", "- **协议桩**：每个案例预置一条回答，走真实的 OpenAI 兼容解析路径并标注为 Mock，"
        "用来检验校验、门槛与降级逻辑；**它不代表任何模型的能力**，不能当作模型准确率。",
        f"- **真实模型**：{s['real_model'].get('status', '已运行')}" if "status" in s.get("real_model", {})
        else "- **真实模型**：已调用运营方配置并授权的模型服务。", "",
        "## 汇总（按「案例 × 假设」计）", "",
        "| 系统 | 标注数 | 一致 | 保守（交人工） | 漏判 | 方向错误 | 模型调用次数 |", "|---|---|---|---|---|---|---|"]
    names = {"rule": "规则层", "stub": "协议桩（Mock）", "real_model": "真实模型"}
    for k in ("rule", "stub", "real_model"):
        v = s.get(k)
        if not v:
            continue
        if "status" in v:
            lines.append(f"| {names[k]} | — | — | — | — | — | {v['status']} |")
        else:
            lines.append(f"| {names[k]} | {v['labelled_pairs']} | {v['correct']} | {v['conservative']} | {v['missed']} | "
                         f"{v['wrong_direction']} | {v['model_calls']} |")
    lines += ["", "「保守」指系统给出「待核实」交由人工判断：安全，但没有给出研究员期望的方向。「方向错误」是需要重视的错误。", "",
              "## 逐案例", "", "| 案例 | 类别 | 假设 | 人工标注 | 规则层 | 协议桩（Mock） | 真实模型 |", "|---|---|---|---|---|---|---|"]
    for r in rep["results"]:
        for aid, row in r["rule"]["rows"].items():
            st = r["stub"]["rows"][aid]
            rm = r["real_model"]
            real = "BLOCKED" if rm.get("status") == "BLOCKED" else \
                f"{ZH[rm['rows'][aid]['got']]} {SCORE_ZH[rm['rows'][aid]['score']]}"
            stub_note = "（未调用模型）" if r["stub"]["model_calls"] == 0 else ""
            lines.append(f"| {r['id']} {r['title']} | {CAT_ZH.get(r['category'], r['category'])} | {aid} | {ZH[row['human']]} | "
                         f"{ZH[row['got']]} {SCORE_ZH[row['score']]} | {ZH[st['got']]} {SCORE_ZH[st['score']]}{stub_note} | {real} |")
    lines += ["", "## 协议桩每个案例检验的内容", ""]
    lines += [f"- {r['id']}：{r['stub_purpose']}" for r in rep["results"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="techsignal.eval_r6")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--json", help="写出完整结果 JSON")
    ap.add_argument("--md", help="写出中文 Markdown 报告")
    a = ap.parse_args(argv)
    rep = evaluate(Path(a.cases))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.md:
        Path(a.md).write_text(to_markdown(rep), encoding="utf-8")
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
