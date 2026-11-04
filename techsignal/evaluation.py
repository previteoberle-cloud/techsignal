"""Scenario runner and lightweight metrics.

Each scenario file (tests/scenarios/*.json) is fictional and carries its own expectations. The same
checker backs the pytest suite and `python -m techsignal eval`, so the metrics printed are exactly
what the tests assert.
"""
from __future__ import annotations

import json
from pathlib import Path

from .adapters.cdio import AdapterError
from .pipeline import RunResult, run_pipeline
from .providers import MockReplayProvider
from .store import SourceRegistry, load_theses

TECHSIGNAL_ROOT = Path(__file__).resolve().parent.parent
DEMO = TECHSIGNAL_ROOT / "demo"

_ERRORS = {"AdapterError": AdapterError}


def load_scenario(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_scenario(sc: dict, theses=None, sources=None) -> tuple[RunResult | None, Exception | None, object]:
    theses = theses if theses is not None else load_theses(DEMO / "theses.json")
    sources = sources if sources is not None else SourceRegistry.load(DEMO / "sources.json")
    provider = MockReplayProvider(sc.get("mock_replay", {})) if sc.get("provider") == "mock" else None
    report = sc["raw_input"] if "raw_input" in sc else sc["report"]
    try:
        return run_pipeline(report, theses, sources, provider=provider), None, provider
    except Exception as e:  # noqa: BLE001 - reported per scenario
        return None, e, provider


def _check_assessment(a, e: dict) -> list[str]:
    problems = []
    if a.direction.value != e["direction"]:
        problems.append(f"方向期望 {e['direction']}，实际 {a.direction.value}")
    for key in ("basis", "assumption_id", "confidence", "tentative_direction"):
        if key in e:
            actual = getattr(a, key)
            actual = getattr(actual, "value", actual)
            if actual != e[key]:
                problems.append(f"{key} 期望 {e[key]}，实际 {actual}")
    if e.get("has_conflict") and not a.conflicting_evidence_ids:
        problems.append("期望标记来源冲突")
    if "limitation_contains" in e and not any(e["limitation_contains"] in x for x in a.limitations):
        problems.append(f"limitations 中缺少「{e['limitation_contains']}」：{a.limitations}")
    return problems


def check_scenario(sc: dict, result: RunResult | None, error: Exception | None) -> tuple[list[str], dict]:
    """Return (failures, counters). counters feed the summary metrics."""
    exp = sc["expect"]
    failures: list[str] = []
    counters = {"direction_total": 0, "direction_ok": 0, "filter_total": 0, "filter_ok": 0}

    if "error" in exp:
        if error is None or not isinstance(error, _ERRORS[exp["error"]]):
            failures.append(f"期望抛出 {exp['error']}，实际 {type(error).__name__ if error else '无异常'}")
        return failures, counters
    if error is not None:
        return [f"意外异常 {type(error).__name__}: {error}"], counters

    for uuid, reason in exp.get("filtered", {}).items():
        counters["filter_total"] += 1
        f = result.filtered_for(uuid)
        if f and f.reason_code == reason:
            counters["filter_ok"] += 1
        else:
            failures.append(f"{uuid}: 期望过滤原因 {reason}，实际 {f.reason_code if f else '未过滤'}")

    for uuid, expected_list in exp.get("assessed", {}).items():
        counters["direction_total"] += len(expected_list)
        if result.filtered_for(uuid):
            failures.append(f"{uuid}: 期望被评估，实际被过滤（{result.filtered_for(uuid).reason_code}）")
            continue
        got = result.by_watch(uuid)
        for e in expected_list:
            # match on assumption too when the expectation names one (an event can hit several)
            a = next((x for x in got if x.thesis_id == e["thesis_id"]
                      and ("assumption_id" not in e or x.assumption_id == e["assumption_id"])), None)
            if a is None:
                failures.append(f"{uuid}: 没有针对 {e['thesis_id']}·{e.get('assumption_id', '*')} 的评估")
                continue
            problems = _check_assessment(a, e)
            if a.direction.value == e["direction"]:
                counters["direction_ok"] += 1
            failures.extend(f"{uuid}: {p}" for p in problems)

    for key, val in exp.get("stats", {}).items():
        actual = getattr(result.stats, key)
        if actual != val:
            failures.append(f"stats.{key} 期望 {val}，实际 {actual}")

    # Every successful run already passed citation validation inside run_pipeline.
    return failures, counters


def run_scenarios(directory: Path) -> dict:
    theses = load_theses(DEMO / "theses.json")
    sources = SourceRegistry.load(DEMO / "sources.json")
    results = []
    totals = {"direction_total": 0, "direction_ok": 0, "filter_total": 0, "filter_ok": 0}
    assessments = provider_calls = rule_decisions = briefs_validated = 0
    for path in sorted(Path(directory).glob("*.json")):
        sc = load_scenario(path)
        result, error, _ = run_scenario(sc, theses, sources)
        failures, counters = check_scenario(sc, result, error)
        for k in totals:
            totals[k] += counters[k]
        if result is not None:
            assessments += len(result.assessments)
            provider_calls += result.stats.provider_calls
            rule_decisions += result.stats.rule_decisions
            briefs_validated += 1
        results.append({"id": sc["id"], "title": sc["title"], "category": sc["category"],
                        "passed": not failures, "failures": failures})

    def ratio(a, b):
        return round(a / b, 3) if b else None

    summary = {
        "data": "全部为虚构场景，指标只说明处理逻辑与预期一致，不代表真实世界准确率",
        "scenarios_total": len(results),
        "scenarios_passed": sum(r["passed"] for r in results),
        "scenarios_failed": sum(not r["passed"] for r in results),
        "direction_accuracy": ratio(totals["direction_ok"], totals["direction_total"]),
        "direction_checks": totals["direction_total"],
        "filter_accuracy": ratio(totals["filter_ok"], totals["filter_total"]),
        "filter_checks": totals["filter_total"],
        "assessments": assessments,
        "rule_decision_share": ratio(rule_decisions, assessments),
        "model_layer_call_rate": ratio(provider_calls, assessments),
        "briefs_passing_citation_validation": briefs_validated,
    }
    return {"summary": summary, "results": results}
