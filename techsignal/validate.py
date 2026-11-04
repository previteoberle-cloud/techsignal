"""Citation integrity checks for a Brief (structured) and its rendered Markdown.

Rules (all violations are errors):
  - FACT        must cite >= 1 evidence id
  - INFERENCE   must cite >= 1 evidence id (otherwise it has to be written as SPECULATION)
  - CONTEXT     must cite >= 1 thesis id
  - any cited evidence id must exist in the ledger, any cited thesis id in the thesis store
  - Markdown: every [EV-..]/[TH-..] must resolve; every bullet under 新增信息/来源/证据等级 must
    carry an evidence citation; a Mock disclaimer must be present when Mock judgements were used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Brief
from .models import StatementKind as K

_REF_RE = re.compile(r"\[((?:EV|TH)-[\w-]+)\]")
_FACT_SECTIONS = {"新增信息", "来源", "证据等级"}


@dataclass(frozen=True)
class Issue:
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.where}: {self.message}"


class CitationError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("引用完整性校验失败：\n" + "\n".join(str(i) for i in issues))


def validate_brief(brief: Brief, evidence_ids: set[str], thesis_ids: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    pairs = [(item.item_id, s) for item in brief.items for s in item.all_statements()]
    pairs += [("unrelated", s) for s in brief.unrelated]
    pairs += [("transitions", s) for s in brief.transitions]
    for where, s in pairs:
        snippet = s.text[:40]
        if s.kind in (K.FACT, K.INFERENCE) and not s.evidence_ids:
            code = "FACT_WITHOUT_EVIDENCE" if s.kind == K.FACT else "INFERENCE_WITHOUT_EVIDENCE"
            issues.append(Issue(code, where, f"缺少证据编号：{snippet}"))
        if s.kind == K.TRACE and not s.tool_call_ids:
            issues.append(Issue("TRACE_WITHOUT_TOOL_CALL", where, f"补证记录缺少工具调用编号：{snippet}"))
        if s.kind == K.CONTEXT and not s.thesis_ids:
            issues.append(Issue("CONTEXT_WITHOUT_THESIS", where, f"缺少研究判断编号：{snippet}"))
        for eid in s.evidence_ids:
            if eid not in evidence_ids:
                issues.append(Issue("UNKNOWN_EVIDENCE", where, f"证据 {eid} 不在证据台账中：{snippet}"))
        for tid in s.thesis_ids:
            if tid not in thesis_ids:
                issues.append(Issue("UNKNOWN_THESIS", where, f"研究判断 {tid} 不存在：{snippet}"))
    return issues


def validate_markdown(md: str, evidence_ids: set[str], thesis_ids: set[str], mock_used: bool) -> list[Issue]:
    issues: list[Issue] = []
    section = None
    for n, line in enumerate(md.splitlines(), 1):
        where = f"line {n}"
        m = re.fullmatch(r"\*\*(.+)\*\*", line.strip())
        if m:
            section = m.group(1)
            continue
        if line.startswith("#"):
            section = None
        for ref in _REF_RE.findall(line):
            known = evidence_ids if ref.startswith("EV-") else thesis_ids
            if ref not in known:
                issues.append(Issue("UNKNOWN_REFERENCE", where, f"{ref} 无法追溯"))
        if section in _FACT_SECTIONS and line.startswith("- ") and "[EV-" not in line:
            issues.append(Issue("FACT_LINE_WITHOUT_EVIDENCE", where, f"「{section}」中的陈述缺少证据编号"))
    if mock_used and "Mock 回放" not in md:
        issues.append(Issue("MOCK_NOT_DISCLOSED", "document", "使用了 Mock 回放但未在简报中声明"))
    return issues


def assert_valid(brief: Brief, md: str, evidence_ids: set[str], thesis_ids: set[str]) -> None:
    issues = validate_brief(brief, evidence_ids, thesis_ids) + validate_markdown(
        md, evidence_ids, thesis_ids, brief.mock_used)
    if issues:
        raise CitationError(issues)
