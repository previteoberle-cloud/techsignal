"""Deterministic layer: thesis matching and signal rules.

The rule layer only decides when the evidence is unambiguous: a clean signal phrase in newly added
text, not negated, all pointing the same way. Everything else is handed to the model layer as
"ambiguous" - the rules never guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .models import Direction, Thesis
from .triage import TriageResult

_NEGATION_RE = re.compile(r"(未|没有|并未|并非|不会|否认|不再|辟谣)")
_NEGATION_WINDOW = 4


@dataclass
class ThesisMatch:
    thesis: Thesis
    entity_hits: list[str]
    topic_hits: list[str]


@dataclass
class SignalHit:
    assumption_id: str
    polarity: Literal["support", "weaken"]
    pattern: str
    line: str
    side: Literal["added", "removed"]
    negated: bool

    def describe(self) -> str:
        side = "新增" if self.side == "added" else "删除"
        pol = "支持" if self.polarity == "support" else "削弱"
        neg = "（前有否定词）" if self.negated else ""
        return f"{self.assumption_id}·{pol}信号「{self.pattern}」出现在{side}内容{neg}：{self.line}"


@dataclass
class RuleOutcome:
    kind: Literal["decisive", "unrelated", "ambiguous"]
    direction: Direction | None = None
    assumption_id: str | None = None              # first of assumption_ids, kept for callers that want one
    assumption_ids: list[str] = field(default_factory=list)  # every assumption the decisive signals hit
    signals: list[SignalHit] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def match_theses(title: str, t: TriageResult, theses: list[Thesis]) -> list[ThesisMatch]:
    changed = "\n".join(t.meaningful_added + t.meaningful_removed)
    haystack = (title + "\n" + changed).lower()
    out = []
    for th in theses:
        if th.status == "retired":
            continue
        entity_hits = [e for e in th.entities if e.lower() in haystack]
        if not entity_hits:
            continue
        topic_hits = [k for k in th.topic_keywords if k.lower() in changed.lower()]
        out.append(ThesisMatch(thesis=th, entity_hits=entity_hits, topic_hits=topic_hits))
    return out


def find_signals(thesis: Thesis, t: TriageResult) -> list[SignalHit]:
    hits: list[SignalHit] = []
    for side, lines in (("added", t.meaningful_added), ("removed", t.meaningful_removed)):
        for line in lines:
            for a in thesis.assumptions:
                for polarity, patterns in (("support", a.support_patterns), ("weaken", a.weaken_patterns)):
                    for pat in patterns:
                        m = re.search(pat, line)
                        if not m:
                            continue
                        before = line[max(0, m.start() - _NEGATION_WINDOW):m.start()]
                        hits.append(SignalHit(a.assumption_id, polarity, pat, line, side,
                                              negated=bool(_NEGATION_RE.search(before))))
    return hits


def decide(match: ThesisMatch, t: TriageResult) -> RuleOutcome:
    th = match.thesis
    signals = find_signals(th, t)
    ents = "、".join(match.entity_hits)

    if not signals and not match.topic_hits:
        return RuleOutcome("unrelated", Direction.UNRELATED, reasons=[
            f"变化提及「{ents}」，但未涉及研究判断 {th.thesis_id} 的任何主题词，也未命中假设信号"])

    if not signals:
        return RuleOutcome("ambiguous", signals=[], reasons=[
            f"变化提及「{ents}」并涉及主题词「{'、'.join(match.topic_hits)}」，但未命中任何确定性信号规则"])

    # Undecided outcomes still say which assumption they concern when that is unambiguous, so the
    # history of that assumption can be followed across batches (R2). No direction is implied.
    touched = sorted({s.assumption_id for s in signals})
    single = touched[0] if len(touched) == 1 else None

    unclear = [s for s in signals if s.negated or s.side == "removed"]
    if unclear:
        return RuleOutcome("ambiguous", assumption_id=single, signals=signals, reasons=[
            "命中的信号处于否定语境或出现在被删除的内容中，规则层不做方向判断：" + "；".join(s.describe() for s in unclear)])

    polarities = {s.polarity for s in signals}
    if len(polarities) > 1:
        # Conservative: no direction, but every signal is named so nothing is dropped silently.
        return RuleOutcome("ambiguous", assumption_id=single, signals=signals, reasons=[
            "同一变化中同时出现支持与削弱信号，规则层不做方向判断：" + "；".join(s.describe() for s in signals)])

    direction = Direction.SUPPORTS if polarities == {"support"} else Direction.WEAKENS
    assumption_ids = sorted({s.assumption_id for s in signals})
    # One change can bear on several assumptions; callers must report each of them (R1.1).
    return RuleOutcome("decisive", direction, assumption_id=assumption_ids[0], assumption_ids=assumption_ids,
                       signals=signals, reasons=[s.describe() for s in signals])
