"""Impact assessment: rules first, model only for what the rules can't decide, then hard gates.

Order of operations for each (event, matched thesis):
  1. rules.decide()                 -> decisive / unrelated / ambiguous
  2. ambiguous -> provider.judge()  -> validated, or rejected and downgraded to INSUFFICIENT
  3. grade gate                     -> supports/weakens from a grade C/D source becomes INSUFFICIENT
  4. resolve_conflicts() (per run)  -> opposing signals on one assumption become INSUFFICIENT

A thesis is never modified. The strongest outcome is "recommend a human review".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import rules
from .models import (
    DECISIVE_GRADES,
    Assessment,
    Basis,
    ChangeEvent,
    Direction,
    Evidence,
    Thesis,
    short_hash,
)
from .providers import LLMProvider, ModelRequest, ProviderError
from .triage import TriageResult

_CONF_ORDER = ["low", "medium", "high"]


@dataclass
class AssessStats:
    rule_decisions: int = 0
    provider_calls: int = 0
    provider_failures: int = 0
    provider_rejections: int = 0
    grade_downgrades: int = 0
    conflicts: int = 0
    notes: list[str] = field(default_factory=list)


def _cap(conf: str, ceiling: str) -> str:
    return _CONF_ORDER[min(_CONF_ORDER.index(conf), _CONF_ORDER.index(ceiling))]


def _new(event: ChangeEvent, thesis: Thesis | None, evidence: Evidence, **kw) -> Assessment:
    return Assessment(
        assessment_id="AS-" + short_hash(event.event_id, thesis.thesis_id if thesis else "none"),
        event_id=event.event_id,
        thesis_id=thesis.thesis_id if thesis else None,
        evidence_ids=[evidence.evidence_id],
        **kw,
    )


def _validate_judgement(j, thesis: Thesis, allowed: list[str]) -> str | None:
    """Return a rejection reason, or None if the provider output is acceptable."""
    bad = [c for c in j.cited_evidence_ids if c not in allowed]
    if bad:
        return f"引用了不存在或不属于本次变化的证据编号 {', '.join(bad)}"
    valid_assumptions = {a.assumption_id for a in thesis.assumptions}
    if j.assumption_id is not None and j.assumption_id not in valid_assumptions:
        return f"引用了研究判断中不存在的假设 {j.assumption_id}"
    if j.direction in (Direction.SUPPORTS, Direction.WEAKENS):
        if not j.cited_evidence_ids:
            return "给出了方向判断但未引用任何证据"
        if j.assumption_id is None:
            return "给出了方向判断但未指明对应的关键假设"
    if not j.rationale.strip():
        return "未给出判断理由"
    return None


def _from_model(event, t, evidence, match, outcome, provider, stats) -> Assessment:
    thesis = match.thesis
    notes = list(outcome.reasons)
    if provider is None:
        return _new(event, thesis, evidence, direction=Direction.INSUFFICIENT, basis=Basis.RULE,
                    rationale=notes + ["规则层无法判定，且未配置模型 Provider"],
                    matched_signals=[s.describe() for s in outcome.signals],
                    open_questions=["需要人工阅读变化原文，判断其与关键假设的关系"])

    basis = Basis.MOCK_MODEL if provider.is_mock else Basis.MODEL
    req = ModelRequest(
        replay_key=f"{event.replay_key}#{thesis.thesis_id}", thesis=thesis,
        event_title=event.title, event_url=event.url,
        added_lines=t.meaningful_added, removed_lines=t.meaningful_removed,
        evidence_ids=[evidence.evidence_id], source_grade=evidence.grade.value, rule_notes=notes,
    )
    stats.provider_calls += 1
    try:
        j = provider.judge(req)
    except ProviderError as e:
        stats.provider_failures += 1
        return _new(event, thesis, evidence, direction=Direction.INSUFFICIENT, basis=Basis.RULE,
                    provider_name=provider.name,
                    rationale=notes + [f"模型层调用失败（{e}），按证据不足处理"],
                    matched_signals=[s.describe() for s in outcome.signals],
                    limitations=["本条未获得模型层判断"],
                    open_questions=["需要人工阅读变化原文，判断其与关键假设的关系"])

    reason = _validate_judgement(j, thesis, req.evidence_ids)
    if reason:
        stats.provider_rejections += 1
        return _new(event, thesis, evidence, direction=Direction.INSUFFICIENT, basis=Basis.RULE,
                    provider_name=provider.name, tentative_direction=j.direction,
                    rationale=notes + [f"模型层输出未通过校验，已拒绝：{reason}"],
                    matched_signals=[s.describe() for s in outcome.signals],
                    limitations=["模型层输出被拒绝，本条结论退回为证据不足"],
                    open_questions=["需要人工复核该变化"])

    return _new(event, thesis, evidence, direction=j.direction, basis=basis, provider_name=provider.name,
                assumption_id=j.assumption_id, rationale=notes, model_rationale=j.rationale,
                matched_signals=[s.describe() for s in outcome.signals],
                # model judgements never get 'high' confidence and always ask for confirmation
                confidence=_cap(j.confidence, "medium"),
                open_questions=["该判断来自 Mock 回放（人工预置），未经真实模型或人工复核" if provider.is_mock
                                else "该判断来自模型推断，需研究员确认"])


def _apply_gates(a: Assessment, event: ChangeEvent, evidence: Evidence, stats: AssessStats) -> Assessment:
    upd: dict = {}
    limitations = list(a.limitations)
    questions = list(a.open_questions)
    conf = a.confidence

    if a.direction in (Direction.SUPPORTS, Direction.WEAKENS) and evidence.grade not in DECISIVE_GRADES:
        stats.grade_downgrades += 1
        upd["tentative_direction"] = a.direction
        upd["direction"] = Direction.INSUFFICIENT
        limitations.append(f"来源等级 {evidence.grade.value} 不足以单独支撑方向判断")
        questions.append(f"需要一手来源（公司公告或监管文件）确认「{evidence.source_name}」的说法")
        conf = "low"

    if evidence.truncated:
        limitations.append(f"changedetection.io 返回的差异已截断，完整内容见 {evidence.cdio_difference_path}")
        conf = _cap(conf, "medium")
    if event.watch_error:
        limitations.append(f"该监测项最近一次检查报错：{event.watch_error}")
        conf = _cap(conf, "medium")

    upd.update(limitations=limitations, open_questions=questions, confidence=conf)
    return a.model_copy(update=upd)


def assess_event(event: ChangeEvent, t: TriageResult, evidence: Evidence, theses: list[Thesis],
                 provider: LLMProvider | None, stats: AssessStats) -> list[Assessment]:
    matches = rules.match_theses(event.title, t, theses)
    if not matches:
        stats.rule_decisions += 1
        return [_new(event, None, evidence, direction=Direction.UNRELATED, basis=Basis.RULE,
                     rationale=["变化内容未提及任何研究判断所跟踪的实体"], confidence="high")]

    out = []
    for match in matches:
        outcome = rules.decide(match, t)
        if outcome.kind == "unrelated":
            stats.rule_decisions += 1
            a = _new(event, match.thesis, evidence, direction=Direction.UNRELATED, basis=Basis.RULE,
                     rationale=outcome.reasons, confidence="medium")
        elif outcome.kind == "decisive":
            stats.rule_decisions += 1
            a = _new(event, match.thesis, evidence, direction=outcome.direction, basis=Basis.RULE,
                     assumption_id=outcome.assumption_id, rationale=outcome.reasons,
                     matched_signals=[s.describe() for s in outcome.signals],
                     confidence="high" if evidence.grade.value == "A" else "medium")
        else:
            a = _from_model(event, t, evidence, match, outcome, provider, stats)
        out.append(_apply_gates(a, event, evidence, stats))
    return out


def _effective(a: Assessment) -> Direction | None:
    for d in (a.direction, a.tentative_direction):
        if d in (Direction.SUPPORTS, Direction.WEAKENS):
            return d
    return None


def resolve_conflicts(assessments: list[Assessment], evidence: dict[str, Evidence],
                      stats: AssessStats) -> list[Assessment]:
    """Opposing signals on the same assumption: downgrade every side, never pick a winner."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, a in enumerate(assessments):
        if a.thesis_id and a.assumption_id and _effective(a):
            groups[(a.thesis_id, a.assumption_id)].append(i)

    out = list(assessments)
    for (thesis_id, assumption_id), idx in groups.items():
        dirs = {_effective(out[i]) for i in idx}
        if len(dirs) < 2:
            continue
        stats.conflicts += 1

        def side(i):
            a = out[i]
            ev = evidence[a.evidence_ids[0]]
            word = "支持" if _effective(a) == Direction.SUPPORTS else "削弱"
            return f"{ev.evidence_id}（{ev.source_name}，等级 {ev.grade.value}）指向{word}"

        summary = "；".join(side(i) for i in idx)
        for i in idx:
            a = out[i]
            others = [e for j in idx if j != i for e in out[j].evidence_ids]
            out[i] = a.model_copy(update={
                "tentative_direction": _effective(a),
                "direction": Direction.INSUFFICIENT,
                "conflicting_evidence_ids": others,
                "confidence": "low",
                "limitations": a.limitations + ["存在相互矛盾的证据，系统不自动裁决"],
                "open_questions": a.open_questions + [f"来源冲突（{thesis_id}·{assumption_id}）：{summary}。需核实哪一方属实"],
            })
    return out


def recommend(a: Assessment) -> Assessment:
    if a.direction == Direction.WEAKENS:
        text = f"建议研究负责人复核 {a.thesis_id} 的假设 {a.assumption_id}；系统未修改结论状态"
    elif a.direction == Direction.SUPPORTS:
        text = f"记录为 {a.thesis_id}·{a.assumption_id} 的支持证据，维持原结论"
    elif a.direction == Direction.INSUFFICIENT:
        text = "列入待核实清单，补充一手来源后重新评估；在此之前不调整结论"
    else:
        text = "无需动作"
    return a.model_copy(update={"recommended_action": text})
