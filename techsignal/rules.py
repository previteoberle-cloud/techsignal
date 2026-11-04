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

# R6.1: negation is judged per CLAUSE, not by a fixed character window (R1-R6 looked 4 characters
# back, so "否认……推迟……传闻" was read as a delay). A clause ends at sentence or clause punctuation, a
# table-cell bar, or a contrast word ("但", "however"); a negation, denial or question cue
# anywhere in the signal's clause - before or after it, any distance - makes the signal unclear, and
# unclear signals never get a direction from the rules. Double negation is not resolved: it is unclear
# too. Words that only look negative (未来, 不断, 不仅 ...) are not cues.
_CLAUSE_SPLIT = re.compile(r"[。！？!?；;\n|]|(?:，|,|、)?\s*(?:但是|但|然而|不过|而是|however|but)\s*", re.IGNORECASE)
_NEG_CUES_ZH = re.compile(
    r"并未|并非|没有|没|未(?!来|知|必)|不(?!断|仅|少|同|久|错|但|过|止|论|管|料|妨|定期)|无法|尚无|暂无|非"
    r"|否认|辟谣|谣言|不实|是否|会否|吗")
_NEG_CUES_EN = re.compile(r"\b(?:not|no|never|none|without|den(?:y|ies|ied)|whether)\b|n't\b",
                          re.IGNORECASE)
_LEGACY_WINDOW = 4   # only decides whether a hit keeps the R1-R6 wording "（前有否定词）"
_NEGATION_RE = re.compile(r"(未|没有|并未|并非|不会|否认|不再|辟谣)")


def _clause(line: str, start: int, end: int) -> tuple[str, int]:
    """The clause of `line` containing [start, end), and the index where it ends."""
    lo, hi = 0, len(line)
    for m in _CLAUSE_SPLIT.finditer(line):
        if m.end() <= start:
            lo = m.end()
        elif m.start() >= end:
            hi = m.start()
            break
    return line[lo:hi], hi


_QUESTION_CUES = {"是否", "会否", "吗", "whether"}


def negation_context(line: str, start: int, end: int) -> str | None:
    """Why the signal at line[start:end] is unclear (a short Chinese note), or None when it is plain."""
    clause, hi = _clause(line, start, end)
    rest = clause.replace(line[start:end], " ", 1)
    cues = [m.group(0) for m in _NEG_CUES_ZH.finditer(rest)] + [m.group(0) for m in _NEG_CUES_EN.finditer(rest)]
    question = line[hi:hi + 1] in ("?", "？")
    if not cues:
        return "疑问句" if question else None
    shown = "、".join(dict.fromkeys(cues))
    if len([c for c in cues if c.lower() not in _QUESTION_CUES]) >= 2:
        return f"同一分句中有多个否定表达（{shown}），无法确定是否被否定"
    return f"同一分句中有否定、辟谣或疑问表达（{shown}）"


@dataclass
class ThesisMatch:
    thesis: Thesis
    entity_hits: list[str]
    topic_hits: list[str]
    # R6.1: only short / ambiguous aliases matched (no full name): the subject needs human confirmation
    weak: bool = False


@dataclass
class SignalHit:
    assumption_id: str
    polarity: Literal["support", "weaken"]
    pattern: str
    line: str
    side: Literal["added", "removed"]
    negated: bool
    negation_note: str = ""

    def describe(self) -> str:
        side = "新增" if self.side == "added" else "删除"
        pol = "支持" if self.polarity == "support" else "削弱"
        neg = ""
        if self.negated:
            neg = "（前有否定词）" if self.negation_note == "legacy" else f"（{self.negation_note}）"
        return f"{self.assumption_id}·{pol}信号「{display_pattern(self.pattern)}」出现在{side}内容{neg}：{self.line}"


_LITERAL = re.compile(r"^\(\?i\)(?:\(\?<!\[A-Za-z0-9\]\))?(.*?)(?:\(\?!\[A-Za-z0-9\]\))?$", re.S)


def display_pattern(pattern: str) -> str:
    """A phrase typed in the workbench is stored as a literal pattern (projects.literal_pattern);
    show the phrase, not the regex. Hand-written patterns (R1-R5 cases) are shown unchanged."""
    m = _LITERAL.match(pattern)
    return re.sub(r"\\(.)", r"\1", m.group(1)) if m else pattern


@dataclass
class RuleOutcome:
    kind: Literal["decisive", "unrelated", "ambiguous"]
    direction: Direction | None = None
    assumption_id: str | None = None              # first of assumption_ids, kept for callers that want one
    assumption_ids: list[str] = field(default_factory=list)  # every assumption the decisive signals hit
    signals: list[SignalHit] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


_LATIN = "A-Za-z0-9"


def entity_pattern(name: str) -> re.Pattern:
    """Case-insensitive literal match. A name that starts/ends with a Latin letter or digit must not be
    glued to another one: "XH" does not match inside "XHTech", "Arm" not inside "Armstrong". Chinese
    characters next to a Latin name are a boundary ("XH半导体" matches "XH")."""
    pat = re.escape(name)
    if re.match(f"[{_LATIN}]", name):
        pat = f"(?<![{_LATIN}])" + pat
    if re.search(f"[{_LATIN}]$", name):
        pat += f"(?![{_LATIN}])"
    return re.compile(pat, re.IGNORECASE)


def weak_entities(th: Thesis) -> set[str]:
    """Aliases that alone are not enough to say a change is about this subject: those the analyst marked
    as ambiguous, and Latin aliases of 3 letters/digits or fewer (XH, TT, AI). The primary name
    (entities[0]) is never weak; normal Chinese short names (星衡) are not weak unless marked."""
    out = {e for e in th.ambiguous_entities}
    for e in th.entities[1:]:
        core = re.sub(r"[\s\-_.]", "", e)
        if core.isascii() and len(core) <= 3:
            out.add(e)
    return out - {th.entities[0]} if th.entities else out


def match_theses(title: str, t: TriageResult, theses: list[Thesis]) -> list[ThesisMatch]:
    changed = "\n".join(t.meaningful_added + t.meaningful_removed)
    haystack = title + "\n" + changed
    out = []
    for th in theses:
        if th.status == "retired":
            continue
        entity_hits = [e for e in th.entities if entity_pattern(e).search(haystack)]
        if not entity_hits:
            continue
        topic_hits = [k for k in th.topic_keywords if k.lower() in changed.lower()]
        weak = set(entity_hits) <= weak_entities(th)
        out.append(ThesisMatch(thesis=th, entity_hits=entity_hits, topic_hits=topic_hits, weak=weak))
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
                        note = negation_context(line, m.start(), m.end())
                        if note and _NEGATION_RE.search(line[max(0, m.start() - _LEGACY_WINDOW):m.start()]):
                            note = "legacy"          # what R1-R6 already caught keeps its wording
                        hits.append(SignalHit(a.assumption_id, polarity, pat, line, side,
                                              negated=note is not None, negation_note=note or ""))
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
    if match.weak:
        return RuleOutcome("ambiguous", assumption_id=single, signals=signals, reasons=[
            f"只命中了简称「{'、'.join(match.entity_hits)}」，没有出现全称，可能指其他主体，需要人工确认是否为 {th.thesis_id} 的研究对象："
            + "；".join(s.describe() for s in signals)])
    assumption_ids = sorted({s.assumption_id for s in signals})
    # One change can bear on several assumptions; callers must report each of them (R1.1).
    return RuleOutcome("decisive", direction, assumption_id=assumption_ids[0], assumption_ids=assumption_ids,
                       signals=signals, reasons=[s.describe() for s in signals])
