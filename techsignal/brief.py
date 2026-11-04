"""Chinese management brief: structured Brief model + deterministic Markdown rendering.

Every statement carries its kind and citations; the renderer prints citations inline as [EV-...] /
[TH-...] and marks Mock-replay judgements explicitly.
"""
from __future__ import annotations

import re
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


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MAX_DISPLAY_CHARS = 160


def _display(line: str) -> str:
    """Brief display only (the ledger keeps the raw line): markdown links -> text, collapse
    whitespace, cap the length."""
    text = re.sub(r"\s+", " ", _MD_LINK.sub(r"\1", line)).strip()
    return text if len(text) <= MAX_DISPLAY_CHARS else text[:MAX_DISPLAY_CHARS - 1] + "…"


def _display_long(text: str) -> str:
    return re.sub(r"\s+", " ", _MD_LINK.sub(r"\1", text)).strip()[:400]


def _signal_first(lines: list[str], signals: list[str]) -> list[str]:
    hit = [x for x in lines if any(x in s for s in signals)]
    return hit + [x for x in lines if x not in hit]


_STATUS_ZH = {"ok": "成功", "not_found": "未找到", "error": "失败", "timeout": "超时", "blocked": "被拦截",
              "unavailable": "不可用", "skipped_duplicate": "重复调用已跳过"}


def _verification_statements(a: Assessment, calls_by_id: dict, evidence: dict[str, Evidence]) -> list[Statement]:
    v = a.verification
    if not v:
        return []
    out = [Statement(kind=K.TRACE, tool_call_ids=v["call_ids"] or ["(无)"],
                     text=f"触发原因：{', '.join(v['triggers'])}；规划：{v['planner']}；停止原因：{v['stop_reason']}")]
    for cid in v["call_ids"]:
        c = calls_by_id.get(cid)
        if c is None:
            continue
        found = f"，匹配 {sum(len(f['lines']) for f in c.findings)} 行" if c.findings else ""
        err = f"（{c.error}）" if c.error else ""
        out.append(Statement(kind=K.TRACE, tool_call_ids=[cid], evidence_ids=c.evidence_created + c.evidence_reused,
                             text=f"{cid} {c.tool}：{c.purpose} → {_STATUS_ZH.get(c.status, c.status)}{found}{err}"
                                  + (f"；{c.url}" if c.url else "") + (f"；{c.detail}" if c.detail else "")
                                  + ("；含疑似提示注入的内容已排除" if c.flags else "")))
    for eid in v["evidence_ids"]:
        ev = evidence.get(eid)
        if ev is not None:
            out.append(Statement(kind=K.FACT, evidence_ids=[eid],
                                 text=f"补证证据 {eid}（{ev.source_name}，等级 {ev.grade.value}）：「{_display(' / '.join(ev.excerpt_added[:3]))}」"))
    for h in v["hints"]:
        out.append(Statement(kind=K.TRACE, tool_call_ids=[h.split("：")[0]], text=h))
    kind = K.INFERENCE if v["evidence_ids"] else K.QUESTION
    out.append(Statement(kind=kind, evidence_ids=list(v["evidence_ids"]), basis=Basis.RULE if v["evidence_ids"] else None,
                         text=f"补证结论：{v['conclusion']}"))
    return out


def _item(a: Assessment, ev: Evidence, event: ChangeEvent, th: Thesis, evidence: dict[str, Evidence],
          calls_by_id: dict | None = None) -> BriefItem:
    e = [ev.evidence_id]
    label_add = "补证获取" if ev.obtained_via == "verification_tool" else "新增"
    added = _signal_first(ev.excerpt_added, a.matched_signals)[:MAX_LINES_PER_SIDE]
    removed = _signal_first(ev.excerpt_removed, a.matched_signals)[:MAX_LINES_PER_SIDE]
    new_info = [Statement(kind=K.FACT, text=f"{label_add}：{_display(x)}", evidence_ids=e) for x in added]
    new_info += [Statement(kind=K.FACT, text=f"删除：{_display(x)}", evidence_ids=e) for x in removed]
    hidden = max(0, len(ev.excerpt_added) - MAX_LINES_PER_SIDE) + max(0, len(ev.excerpt_removed) - MAX_LINES_PER_SIDE)
    if hidden:
        new_info.append(Statement(kind=K.FACT, text=f"另有 {hidden} 行变化未在速览中展开，见证据台账", evidence_ids=e))

    if ev.obtained_via == "verification_tool":
        sources = [Statement(kind=K.FACT, evidence_ids=e, tool_call_ids=[ev.tool_call_id or ""], text=(
            f"{ev.source_name}（{SOURCE_TYPE_ZH[ev.source_type]}）{ev.source_url}；由补证工具调用 {ev.tool_call_id} 获取，"
            f"触发补证的变化：「{event.title}」（changedetection.io {ev.cdio_difference_path}）"))]
    else:
        sources = [Statement(kind=K.FACT, evidence_ids=e, text=(
        f"{ev.source_name}（{SOURCE_TYPE_ZH[ev.source_type]}）{ev.source_url}；changedetection.io 抓取于 {iso(ev.observed_at)}，"
        f"对比其上一次抓取 {iso(ev.baseline_at)}（抓取时间不等于发布时间）；追溯：changedetection.io {ev.cdio_difference_path}"))]

    ctx = [Statement(kind=K.CONTEXT, thesis_ids=[th.thesis_id],
                     text=f"{th.title}：{th.conclusion}（截至 {th.as_of}，负责人 {th.owner}）")]
    ctx += [Statement(kind=K.CONTEXT, thesis_ids=[th.thesis_id],
                      text=f"结论依据 {src.source_id}：{src.publisher}，{src.published_at}，「{_display(src.quote)}」（{src.url}）")
            for src in th.sources]
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
    reasoning = [Statement(kind=K.INFERENCE, text=f"规则层：{_display_long(r)}", evidence_ids=e, basis=Basis.RULE)
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
        verification=_verification_statements(a, calls_by_id or {}, evidence),
    )


def build_brief(meta: dict, events: list[ChangeEvent], evidence: dict[str, Evidence],
                assessments: list[Assessment], filtered: list[FilteredEvent], theses: list[Thesis],
                provider_name: str | None, provider_is_mock: bool, transitions: list | None = None,
                provenance=None, tool_calls: list | None = None) -> Brief:
    ev_by_id = {e.event_id: e for e in events}
    th_by_id = {t.thesis_id: t for t in theses}

    calls_by_id = {c.call_id: c for c in (tool_calls or [])}
    items, unrelated = [], []
    for a in assessments:
        ev = evidence[a.evidence_ids[0]]
        event = ev_by_id[a.event_id]
        if a.direction == Direction.UNRELATED:
            target = f"研究判断 {a.thesis_id} " if a.thesis_id else "所有研究判断"
            unrelated.append(Statement(kind=K.INFERENCE, basis=a.basis, evidence_ids=a.evidence_ids,
                                       text=f"「{event.title}」与{target}无关：{'；'.join(a.rationale)}"))
            continue
        items.append(_item(a, ev, event, th_by_id[a.thesis_id], evidence, calls_by_id))

    items.sort(key=lambda i: (i.priority, i.headline, i.item_id))

    mock_used = any(a.basis == Basis.MOCK_MODEL for a in assessments)
    # Every statement about where the data came from is derived from the run's provenance record
    # (provenance.py), never hard-coded (R3 / P0).
    from .provenance import disclaimers as _disclaimers
    if provenance is None:
        from .provenance import RunProvenance, data_nature
        provenance = RunProvenance(input_mode="file", input_origin="file_unverified", data_nature=data_nature(theses))
    disclaimers = _disclaimers(provenance)

    stats = {
        "events_total": len(events),
        "events_filtered": len(filtered),
        "assessments": len(assessments),
        "supports": sum(a.direction == Direction.SUPPORTS for a in assessments),
        "weakens": sum(a.direction == Direction.WEAKENS for a in assessments),
        "insufficient": sum(a.direction == Direction.INSUFFICIENT for a in assessments),
        "unrelated": sum(a.direction == Direction.UNRELATED for a in assessments),
    }
    kind_zh = {"first": "首次出现证据", "reinforced": "得到强化", "conflict": "出现跨批次冲突",
               "unresolved": "新变化无法定向，保留原判断待核实", "changed": "状态变化"}
    trans = [Statement(kind=K.INFERENCE, basis=Basis.RULE,
                       evidence_ids=list(dict.fromkeys(t.before_evidence_ids + t.after_evidence_ids)),
                       thesis_ids=[t.thesis_id],
                       text=(f"{t.thesis_id}·{t.assumption_id}：{kind_zh[t.kind]}。之前：{t.before_state}"
                             + (f"（批次 {t.before_run_id}）" if t.before_run_id else "")
                             + f" → 现在：{t.after_state}。原因：{t.explanation}；研究结论本身未被修改"))
             for t in (transitions or [])]
    return Brief(transitions=trans, title=f"TechSignal 研究速览｜{iso(meta['from'])[:10]} 至 {iso(meta['to'])[:10]}",
                 period_from=meta["from"], period_to=meta["to"], items=items, unrelated=unrelated,
                 filtered=filtered, disclaimers=disclaimers, mock_used=mock_used, stats=stats,
                 provenance=provenance.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

SECTION_TITLES = [
    ("new_info", "新增信息"), ("sources", "来源"), ("thesis_context", "历史结论"), ("impact", "影响方向"),
    ("reasoning", "判断依据"), ("evidence_grade", "证据等级"), ("open_questions", "仍需核实"),
    ("verification", "补证记录"), ("actions", "建议动作"),
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
            if attr == "verification" and not item.verification:
                continue
            out.append(f"**{title}**")
            out += [render_statement(s) for s in getattr(item, attr)]
            out.append("")

    if brief.transitions:
        out += ["## 跨批次判断变化", ""]
        out += [render_statement(s) for s in brief.transitions]
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
