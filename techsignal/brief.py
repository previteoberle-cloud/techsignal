"""Chinese management brief: structured Brief model + deterministic Markdown rendering.

Every statement carries its kind and citations; the renderer prints citations inline as [EV-...] /
[TH-...] and marks Mock-replay judgements explicitly.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    BASIS_ZH,
    DIRECTION_ZH,
    GRADE_ZH,
    Assessment,
    Basis,
    Brief,
    BriefItem,
    ChangeEvent,
    Direction,
    Evidence,
    FilteredEvent,
    SourceType,
    Statement,
    Thesis,
)
from .models import (
    StatementKind as K,
)

SOURCE_TYPE_ZH = {
    SourceType.OFFICIAL: "公司官方", SourceType.REGULATOR: "监管/交易所", SourceType.MEDIA: "媒体",
    SourceType.ANALYST: "第三方分析", SourceType.FORUM: "论坛/社区", SourceType.UNKNOWN: "未知",
}
FILTER_REASON_ZH = {
    "no_net_difference": "无净差异", "watch_error": "监测报错", "noise_only": "仅噪声变化", "duplicate": "重复信息",
}
_PRIORITY = {Direction.WEAKENS: "P1", Direction.INSUFFICIENT: "P2", Direction.SUPPORTS: "P3"}
MAX_LINES_PER_SIDE = 5


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _item(a: Assessment, ev: Evidence, event: ChangeEvent, th: Thesis, evidence: dict[str, Evidence]) -> BriefItem:
    e = [ev.evidence_id]
    new_info = [Statement(kind=K.FACT, text=f"新增：{x}", evidence_ids=e) for x in ev.excerpt_added[:MAX_LINES_PER_SIDE]]
    new_info += [Statement(kind=K.FACT, text=f"删除：{x}", evidence_ids=e) for x in ev.excerpt_removed[:MAX_LINES_PER_SIDE]]
    hidden = max(0, len(ev.excerpt_added) - MAX_LINES_PER_SIDE) + max(0, len(ev.excerpt_removed) - MAX_LINES_PER_SIDE)
    if hidden:
        new_info.append(Statement(kind=K.FACT, text=f"另有 {hidden} 行变化未在速览中展开，见证据台账", evidence_ids=e))

    sources = [Statement(kind=K.FACT, evidence_ids=e, text=(
        f"{ev.source_name}（{SOURCE_TYPE_ZH[ev.source_type]}）{ev.source_url}；观测于 {iso(ev.observed_at)}，"
        f"对比基线 {iso(ev.baseline_at)}；追溯：changedetection.io {ev.cdio_difference_path}"))]

    ctx = [Statement(kind=K.CONTEXT, thesis_ids=[th.thesis_id],
                     text=f"{th.title}：{th.conclusion}（截至 {th.as_of}，负责人 {th.owner}）")]
    if a.assumption_id:
        stmt = next((x.statement for x in th.assumptions if x.assumption_id == a.assumption_id), "")
        ctx.append(Statement(kind=K.CONTEXT, thesis_ids=[th.thesis_id], text=f"相关假设 {a.assumption_id}：{stmt}"))

    cited = e + a.conflicting_evidence_ids
    impact_text = f"影响方向：{DIRECTION_ZH[a.direction]}"
    if a.tentative_direction and a.tentative_direction != a.direction:
        impact_text += f"（初步信号指向「{DIRECTION_ZH[a.tentative_direction]}」，已因证据限制降级）"
    impact_text += f"；置信度 {a.confidence}；判定层：{BASIS_ZH[a.basis]}"
    impact = [
        # the text already names the layer, so no extra Mock suffix here
        Statement(kind=K.INFERENCE, text=impact_text, evidence_ids=cited),
        Statement(kind=K.CONTEXT, thesis_ids=[th.thesis_id],
                  text=f"结论状态保持「{th.status}」，系统不会因新信息自动修改历史结论"),
    ]
    reasoning = [Statement(kind=K.INFERENCE, text=f"规则层：{r}", evidence_ids=e, basis=Basis.RULE)
                 for r in a.rationale]
    if a.model_rationale:
        reasoning.append(Statement(kind=K.INFERENCE, text=f"模型层：{a.model_rationale}", evidence_ids=e,
                                   basis=a.basis))
    reasoning += [Statement(kind=K.INFERENCE, text=f"限制：{x}", evidence_ids=cited, basis=Basis.RULE)
                  for x in a.limitations]

    grades = [Statement(kind=K.FACT, text=f"{eid}：{GRADE_ZH[evidence[eid].grade]}", evidence_ids=[eid])
              for eid in cited]
    questions = [Statement(kind=K.QUESTION, text=q) for q in a.open_questions] or [
        Statement(kind=K.QUESTION, text="无")]
    actions = [Statement(kind=K.ACTION, text=a.recommended_action)]

    return BriefItem(
        item_id=a.assessment_id, priority=_PRIORITY[a.direction],
        headline=f"{DIRECTION_ZH[a.direction]}｜{th.title}｜{event.title}",
        direction=a.direction, new_info=new_info, sources=sources, thesis_context=ctx, impact=impact,
        reasoning=reasoning, evidence_grade=grades, open_questions=questions, actions=actions,
    )


def build_brief(meta: dict, events: list[ChangeEvent], evidence: dict[str, Evidence],
                assessments: list[Assessment], filtered: list[FilteredEvent], theses: list[Thesis],
                provider_name: str | None, provider_is_mock: bool) -> Brief:
    ev_by_id = {e.event_id: e for e in events}
    th_by_id = {t.thesis_id: t for t in theses}

    items, unrelated = [], []
    for a in assessments:
        ev = evidence[a.evidence_ids[0]]
        event = ev_by_id[a.event_id]
        if a.direction == Direction.UNRELATED:
            target = f"研究判断 {a.thesis_id} " if a.thesis_id else "所有研究判断"
            unrelated.append(Statement(kind=K.INFERENCE, basis=a.basis, evidence_ids=a.evidence_ids,
                                       text=f"「{event.title}」与{target}无关：{'；'.join(a.rationale)}"))
            continue
        items.append(_item(a, ev, event, th_by_id[a.thesis_id], evidence))

    items.sort(key=lambda i: (i.priority, i.headline, i.item_id))

    mock_used = any(a.basis == Basis.MOCK_MODEL for a in assessments)
    disclaimers = [
        "本速览为 TechSignal R1 研究原型的输出，不构成投资建议。",
        "输入为 changedetection.io GET /api/v1/changes?format=json 的离线数据；R1 未接入在线服务。",
        "结论状态不会因新信息自动改变；「削弱」仅触发人工复核建议。",
    ]
    if mock_used:
        disclaimers.append(
            f"标注「{BASIS_ZH[Basis.MOCK_MODEL]}」的判断来自人工预置的 Mock 回放（{provider_name}），"
            "不是真实大模型推理。")
    elif provider_name and not provider_is_mock:
        disclaimers.append(f"标注「模型推断」的判断来自 {provider_name}。")

    stats = {
        "events_total": len(events),
        "events_filtered": len(filtered),
        "assessments": len(assessments),
        "supports": sum(a.direction == Direction.SUPPORTS for a in assessments),
        "weakens": sum(a.direction == Direction.WEAKENS for a in assessments),
        "insufficient": sum(a.direction == Direction.INSUFFICIENT for a in assessments),
        "unrelated": sum(a.direction == Direction.UNRELATED for a in assessments),
    }
    return Brief(title=f"TechSignal 研究速览｜{iso(meta['from'])[:10]} 至 {iso(meta['to'])[:10]}",
                 period_from=meta["from"], period_to=meta["to"], items=items, unrelated=unrelated,
                 filtered=filtered, disclaimers=disclaimers, mock_used=mock_used, stats=stats)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

SECTION_TITLES = [
    ("new_info", "新增信息"), ("sources", "来源"), ("thesis_context", "历史结论"), ("impact", "影响方向"),
    ("reasoning", "判断依据"), ("evidence_grade", "证据等级"), ("open_questions", "仍需核实"),
    ("actions", "建议动作"),
]


def render_statement(s: Statement) -> str:
    text = s.text
    if s.kind == K.SPECULATION:
        text = f"（推测，无证据支撑）{text}"
    if s.basis == Basis.MOCK_MODEL:
        text += " 〔Mock 回放，非真实模型推理〕"
    refs = "".join(f" [{x}]" for x in s.evidence_ids + s.thesis_ids)
    return f"- {text}{refs}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(brief: Brief) -> str:
    st = brief.stats
    out = [f"# {brief.title}", ""]
    out += [f"> {d}" for d in brief.disclaimers] + [""]
    out += ["## 概览", "",
            "| 变化事件 | 已过滤 | 评估条目 | 支持 | 削弱 | 证据不足 | 无关 |",
            "|---|---|---|---|---|---|---|",
            f"| {st['events_total']} | {st['events_filtered']} | {st['assessments']} | {st['supports']} | "
            f"{st['weakens']} | {st['insufficient']} | {st['unrelated']} |", ""]

    out += ["## 需要关注的变化", ""]
    if not brief.items:
        out += ["本期没有与研究判断相关的变化。", ""]
    for item in brief.items:
        out += [f"### [{item.priority}] {item.headline}", "", f"`{item.item_id}`", ""]
        for attr, title in SECTION_TITLES:
            out.append(f"**{title}**")
            out += [render_statement(s) for s in getattr(item, attr)]
            out.append("")

    out += ["## 与研究判断无关的变化", ""]
    out += [render_statement(s) for s in brief.unrelated] or ["无"]
    out.append("")

    out += ["## 已过滤的变化", ""]
    if brief.filtered:
        out += ["| 监测项 | 原因 | 说明 |", "|---|---|---|"]
        for f in brief.filtered:
            note = f.reason_zh + (f"（与 {f.duplicate_of} 一致）" if f.duplicate_of else "")
            out.append(f"| {_cell(f.title)} | {FILTER_REASON_ZH[f.reason_code]} | {_cell(note)} |")
    else:
        out.append("无")
    out += ["", "## 引用说明", "",
            "- `[EV-…]` 为证据编号，对应证据台账 evidence.jsonl；`[TH-…]` 为研究判断编号。",
            "- 事实陈述必须引用证据；判断陈述必须引用证据，否则须标注为推测。CLI 仅在引用完整性校验通过后写出本文件。",
            ""]
    return "\n".join(out)
