"""Bounded evidence gathering: detect doubt -> plan -> execute allowed tools -> validate new evidence
-> re-assess with the same rules -> record everything.

Every decision in here is deterministic code. The planner interface exists so a model-assisted
planner could be plugged in later; the only planners shipped are DeterministicPlanner (default) and
FixedPlanner (tests). Whatever a planner proposes, the engine still enforces the allowlist, the call
budget, de-duplication and the stop conditions.
"""
from __future__ import annotations

import json
import posixpath
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .. import rules
from ..models import (
    DECISIVE_GRADES,
    Assessment,
    Basis,
    ChangeEvent,
    Direction,
    Evidence,
    Thesis,
    short_hash,
)
from ..store import EvidenceLedger, SourceRegistry
from ..triage import TriageResult, normalize
from .tools import ToolOutcome

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    for_assessment: str
    tool: str
    purpose: str
    args: dict
    url: str | None = None
    status: str                       # ok | not_found | error | timeout | blocked | unavailable | skipped_duplicate
    http_status: int | None = None
    attempts: int = 0
    duration_ms: int = 0
    findings: list[dict] = Field(default_factory=list)   # {"locator", "lines", "flagged_injection"}
    detail: str = ""
    error: str | None = None
    evidence_created: list[str] = Field(default_factory=list)
    evidence_reused: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None


class VerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: str
    thesis_id: str
    assumption_id: str | None
    triggers: list[str]
    planner: str
    call_ids: list[str]
    outcome: str        # new_decisive_evidence | related_evidence_no_direction | no_evidence_found | tools_failed | no_applicable_tools
    stop_reason: str
    evidence_ids: list[str]
    new_assessment_ids: list[str]
    hints: list[str]
    conclusion: str


@dataclass
class PlannedCall:
    tool: str
    purpose: str
    args: dict


@dataclass
class VerificationItem:
    assessment: Assessment
    event: ChangeEvent
    evidence: Evidence
    thesis: Thesis
    triggers: list[str]
    signals: list[rules.SignalHit]


@dataclass
class VerifyRun:
    summaries: list[VerificationSummary] = field(default_factory=list)
    calls: list[ToolCallRecord] = field(default_factory=list)
    new_evidence: list[Evidence] = field(default_factory=list)
    new_assessments: list[Assessment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Triggers (deterministic)
# ---------------------------------------------------------------------------

def detect_triggers(assessments: list[Assessment], transitions: list, events: dict[str, ChangeEvent],
                    evidence: dict[str, Evidence], theses: dict[str, Thesis]) -> list[VerificationItem]:
    """Only assessments with a concrete doubt are verified; decisive A/B judgements, unrelated or
    filtered changes, and evidence that itself came from a tool never trigger a call."""
    items = []
    for a in assessments:
        if a.direction == Direction.UNRELATED or not a.thesis_id:
            continue
        ev = evidence.get(a.evidence_ids[0])
        if ev is None or ev.obtained_via == "verification_tool":
            continue
        th = theses[a.thesis_id]
        signals = rules.find_signals(th, TriageResult(meaningful_added=ev.excerpt_added,
                                                      meaningful_removed=ev.excerpt_removed))
        own = [s for s in signals if a.assumption_id is None or s.assumption_id == a.assumption_id]
        reasons = []
        if a.direction == Direction.INSUFFICIENT and any(s.side == "removed" or s.negated for s in own):
            reasons.append("signal_removed_or_negated")
        if a.conflicting_evidence_ids:
            reasons.append("conflicting_evidence")
        if (a.direction == Direction.INSUFFICIENT and a.tentative_direction in (Direction.SUPPORTS, Direction.WEAKENS)
                and ev.grade not in DECISIVE_GRADES):
            reasons.append("low_grade_source")
        for t in transitions:
            if (t.thesis_id, t.assumption_id) == (a.thesis_id, a.assumption_id) and t.kind in ("conflict", "unresolved") \
                    and a.evidence_ids[0] in t.after_evidence_ids:
                reasons.append(f"cross_batch_{t.kind}")
        if reasons:
            items.append(VerificationItem(a, events[a.event_id], ev, th, sorted(set(reasons)), own))
    return items


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

_RAW_GH = re.compile(r"^/(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+)/(?P<ref>[^/]+)/(?P<path>.+)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REL_LINK = re.compile(r"\]\((?!https?:|mailto:|#|/)([^)\s]+)\)")


def parse_github_raw(url: str):
    u = urlsplit(url)
    if (u.hostname or "").lower() != "raw.githubusercontent.com":
        return None
    m = _RAW_GH.match(u.path)
    return m.groupdict() if m else None


def official_links(lines: list[str], base_path: str, limit: int = 2) -> list[str]:
    """Relative links inside the source lines, resolved inside the same repository."""
    out = []
    for line in lines:
        for link in _REL_LINK.findall(line):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(base_path), link.split("#")[0]))
            if target.startswith("..") or target in (".", ""):
                continue
            if "." not in posixpath.basename(target):
                target = f"{target}/README.md"
            if target not in out:
                out.append(target)
            if len(out) >= limit:
                return out
    return out


class Planner(Protocol):
    name: str

    def plan(self, item: VerificationItem, available: set[str]) -> list[PlannedCall]: ...


class DeterministicPlanner:
    name = "deterministic"

    def plan(self, item: VerificationItem, available: set[str]) -> list[PlannedCall]:
        th, ev, event = item.thesis, item.evidence, item.event
        signal_lines = [s.line for s in item.signals] or ev.excerpt_removed[:2] or ev.excerpt_added[:2]
        terms = [k for k in th.topic_keywords if any(k.lower() in ln.lower() for ln in signal_lines)]
        patterns = sorted({s.pattern for s in item.signals})
        calls: list[PlannedCall] = []
        if "cdio_snapshot_search" in available:
            calls.append(PlannedCall("cdio_snapshot_search",
                                     "核对同一页面最新快照中是否仍有相关内容（是否只是移动了位置或换了写法）",
                                     {"watch_uuid": event.watch_uuid, "timestamp": str(event.to_timestamp),
                                      "terms": terms, "patterns": patterns, "source_url": event.url}))
        gh = parse_github_raw(ev.source_url)
        if gh and _SHA.match(gh["ref"]) and "github_commit_info" in available:
            calls.append(PlannedCall("github_commit_info", "读取引起该变化的官方提交说明",
                                     {"owner": gh["owner"], "repo": gh["repo"], "sha": gh["ref"]}))
        if gh and "github_file_search" in available:
            for path in official_links(signal_lines, gh["path"]):
                calls.append(PlannedCall("github_file_search",
                                         "沿变化内容中的官方链接，在同一仓库同一版本的相关文档中查找",
                                         {"owner": gh["owner"], "repo": gh["repo"], "ref": gh["ref"], "path": path,
                                          "terms": terms, "patterns": patterns}))
        return calls


class FixedPlanner:
    """Returns pre-set calls. Used in tests to prove the engine's guards hold whatever is planned."""
    name = "fixed(test)"

    def __init__(self, calls: list[PlannedCall]):
        self.calls = calls

    def plan(self, item, available):
        return list(self.calls)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

_RESTRUCTURE = ("featured", "restructur", "reorganiz", "reorder", "move ", "moved", "rename", "cleanup",
                "clean up", "table", "list")
_WITHDRAW = ("deprecat", "drop support", "remove support", "removed support", "no longer support", "unsupported",
             "revert", "disable")


def _hints(records: list[ToolCallRecord]) -> list[str]:
    out = []
    for r in records:
        if r.tool != "github_commit_info" or r.status != "ok":
            continue
        text = " ".join(" ".join(f["lines"]) for f in r.findings).lower()
        a = [w.strip() for w in _RESTRUCTURE if w in text]
        b = [w.strip() for w in _WITHDRAW if w in text]
        if a and not b:
            out.append(f"{r.call_id}：提交说明含文档结构调整用语（{', '.join(a)}），未见撤回/弃用用语（规则提示，非结论）")
        elif b:
            out.append(f"{r.call_id}：提交说明含撤回/弃用类用语（{', '.join(b)}），需人工确认（规则提示，非结论）")
    return out


class Verifier:
    def __init__(self, policy, tools: dict, planner: Planner | None = None):
        self.policy = policy
        self.tools = tools                  # name -> tool instance with .run(**args)
        self.planner = planner or DeterministicPlanner()

    def run(self, items: list[VerificationItem], ledger: EvidenceLedger, sources: SourceRegistry,
            stats) -> VerifyRun:
        out = VerifyRun()
        seen_calls: dict[str, str] = {}
        executed = 0
        for item in items:
            a = item.assessment
            planned = self.planner.plan(item, set(self.tools))
            ids, ev_ids, new_as, stop = [], [], [], "plan_completed"
            item_calls = 0
            if not planned:
                stop = "no_applicable_tools"
            for pc in planned:
                if item_calls >= self.policy.max_calls_per_item:
                    stop = "item_budget_exhausted"
                    break
                if executed >= self.policy.max_calls_per_run:
                    stop = "run_budget_exhausted"
                    break
                call_id = f"TC-{len(out.calls) + 1:02d}"
                key = pc.tool + json.dumps(pc.args, sort_keys=True, ensure_ascii=False)
                if key in seen_calls:
                    out.calls.append(ToolCallRecord(call_id=call_id, for_assessment=a.assessment_id, tool=pc.tool,
                                                    purpose=pc.purpose, args=_summ(pc.args), status="skipped_duplicate",
                                                    duplicate_of=seen_calls[key]))
                    ids.append(call_id)
                    continue
                tool = self.tools.get(pc.tool)
                started = time.monotonic()
                if tool is None:
                    outcome = ToolOutcome("unavailable", error=f"工具 {pc.tool} 不可用")
                else:
                    try:
                        outcome = tool.run(**pc.args)
                    except Exception as e:  # noqa: BLE001 - a tool bug must not break the run
                        outcome = ToolOutcome("error", error=f"{type(e).__name__}: {str(e)[:150]}")
                seen_calls[key] = call_id
                executed += 1
                item_calls += 1
                rec = ToolCallRecord(
                    call_id=call_id, for_assessment=a.assessment_id, tool=pc.tool, purpose=pc.purpose,
                    args=_summ(pc.args), url=outcome.url, status=outcome.status, http_status=outcome.http_status,
                    attempts=outcome.attempts, duration_ms=int((time.monotonic() - started) * 1000),
                    findings=[{"locator": f.locator, "lines": f.lines, "flagged_injection": f.flagged_injection}
                              for f in outcome.findings],
                    detail=outcome.detail, error=outcome.error)
                if any(f.flagged_injection for f in outcome.findings):
                    rec.flags.append("possible_prompt_injection_excluded")
                out.calls.append(rec)
                ids.append(call_id)

                ev = self._evidence(item, outcome, call_id, sources)
                if ev is None:
                    continue
                dup = ledger.find_duplicate(ev.content_fingerprint, ev.subject_key, exclude_event=None)
                if dup is not None:
                    rec.evidence_reused.append(dup.evidence_id)
                    ev_ids.append(dup.evidence_id)
                    ev = dup
                else:
                    ledger.add(ev)
                    out.new_evidence.append(ev)
                    rec.evidence_created.append(ev.evidence_id)
                    ev_ids.append(ev.evidence_id)
                made = self._reassess(item, ev, call_id, stats)
                if made:
                    new_as.extend(made)
                    if any(m.direction in (Direction.SUPPORTS, Direction.WEAKENS) for m in made):
                        stop = "decisive_evidence_found"
                        break
            if stop == "plan_completed" and len(planned) > item_calls and \
                    item_calls >= self.policy.max_calls_per_item:
                stop = "item_budget_exhausted"
            out.new_assessments.extend(new_as)
            out.summaries.append(self._summary(item, ids, ev_ids, new_as, stop, out.calls))
        return out

    def _evidence(self, item: VerificationItem, outcome: ToolOutcome, call_id: str,
                  sources: SourceRegistry) -> Evidence | None:
        if outcome.status != "ok":
            return None
        lines = []
        for f in outcome.findings:
            if f.flagged_injection:
                continue
            for ln in f.lines:
                if ln not in lines:
                    lines.append(ln)
        if not lines or not outcome.url:
            return None
        origin = outcome.source_url or outcome.url
        src = sources.profile(urlsplit(origin).hostname or "", origin)
        fp = short_hash("tool", *sorted(normalize(x) for x in lines), length=16)
        e = item.event
        name = src.name + ("（经 changedetection.io 快照）" if outcome.source_url else "")
        return Evidence(
            evidence_id="EV-" + short_hash("verification", origin, fp), event_id=e.event_id,
            source_url=origin, source_domain=(urlsplit(origin).hostname or ""), source_name=name,
            source_type=src.source_type, grade=src.grade, baseline_at=e.from_timestamp, observed_at=e.to_timestamp,
            excerpt_added=lines, excerpt_removed=[], content_fingerprint=fp, subject_key=[item.thesis.thesis_id],
            truncated=outcome.truncated, cdio_watch_uuid=e.watch_uuid, cdio_history_path=e.cdio_history_path,
            cdio_difference_path=e.cdio_difference_path, obtained_via="verification_tool", tool_call_id=call_id)

    def _reassess(self, item: VerificationItem, ev: Evidence, call_id: str, stats) -> list[Assessment]:
        """The fetched lines go through the same rules as any change. No rule hit -> no direction."""
        from ..assess import _apply_gates, _new
        t = TriageResult(meaningful_added=ev.excerpt_added)
        th = item.thesis
        topic = [k for k in th.topic_keywords if any(k.lower() in x.lower() for x in ev.excerpt_added)]
        outcome = rules.decide(rules.ThesisMatch(th, ["(补证：同一官方来源)"], topic), t)
        if outcome.kind != "decisive":
            return []
        made = []
        for aid in outcome.assumption_ids:
            own = [s for s in outcome.signals if s.assumption_id == aid]
            a = _new(item.event, th, ev, id_suffix=f"verify:{call_id}:{aid}", direction=outcome.direction,
                     basis=Basis.RULE, assumption_id=aid,
                     rationale=[f"补证 {call_id} 获取的内容命中规则：" + s.describe() for s in own],
                     matched_signals=[s.describe() for s in own],
                     confidence="high" if ev.grade.value == "A" else "medium")
            made.append(_apply_gates(a, item.event, ev, stats))
        return made

    def _summary(self, item, ids, ev_ids, new_as, stop, calls) -> VerificationSummary:
        recs = [c for c in calls if c.call_id in ids]
        executed = [c for c in recs if c.status != "skipped_duplicate"]
        decisive = [m for m in new_as if m.direction in (Direction.SUPPORTS, Direction.WEAKENS)]
        hints = _hints(recs)
        a = item.assessment
        target = f"{a.thesis_id}·{a.assumption_id or '未定假设'}"
        if not executed:
            outcome, text = "no_applicable_tools", f"{target}：没有可用的补证工具或计划，保持「尚未核实」"
        elif decisive:
            d = decisive[0]
            outcome = "new_decisive_evidence"
            text = (f"{target}：补证获得新的官方证据 {', '.join(ev_ids)}，经同一规则判定为「"
                    f"{'支持' if d.direction == Direction.SUPPORTS else '削弱'}」{d.assumption_id}；研究结论仍需人工复核，系统未修改")
        elif ev_ids:
            outcome = "related_evidence_no_direction"
            text = (f"{target}：补证找到相关官方内容 {', '.join(ev_ids)}，但没有命中任何判定规则，"
                    "无法区分原因；保持「尚未核实」")
        elif all(c.status in ("error", "timeout", "blocked", "unavailable") for c in executed):
            outcome = "tools_failed"
            text = f"{target}：补证工具调用全部失败或被拦截；保持「尚未核实」，原有研判未受影响"
        else:
            outcome = "no_evidence_found"
            text = f"{target}：补证未找到相关官方内容；保持「尚未核实」"
        return VerificationSummary(
            assessment_id=a.assessment_id, thesis_id=a.thesis_id, assumption_id=a.assumption_id,
            triggers=item.triggers, planner=self.planner.name, call_ids=ids, outcome=outcome, stop_reason=stop,
            evidence_ids=ev_ids, new_assessment_ids=[m.assessment_id for m in new_as], hints=hints, conclusion=text)


def _summ(args: dict) -> dict:
    """Short, secret-free argument summary for the trace."""
    out = {}
    for k, v in args.items():
        if isinstance(v, list):
            out[k] = [str(x)[:80] for x in v[:8]]
        else:
            out[k] = str(v)[:160]
    return out
