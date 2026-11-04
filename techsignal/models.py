"""Core data models: ChangeEvent, Thesis, Evidence, Assessment, Brief.

All models forbid unknown fields so a typo in a fixture or a stored record fails loudly instead of
being silently dropped.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def short_hash(*parts: str, length: int = 10) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:length]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Direction(StrEnum):
    SUPPORTS = "supports"
    WEAKENS = "weakens"
    UNRELATED = "unrelated"
    INSUFFICIENT = "insufficient"


DIRECTION_ZH = {
    Direction.SUPPORTS: "支持历史判断",
    Direction.WEAKENS: "削弱历史判断",
    Direction.UNRELATED: "与历史判断无关",
    Direction.INSUFFICIENT: "证据不足，需要核实",
}


class EvidenceGrade(StrEnum):
    A = "A"  # company's own first-hand disclosure
    B = "B"  # regulator filing / established media with named sources
    C = "C"  # second-hand: reposts, third-party analysis
    D = "D"  # unverified: forums, anonymous claims, unknown sites


GRADE_ZH = {
    EvidenceGrade.A: "A｜公司官方一手披露",
    EvidenceGrade.B: "B｜监管文件或权威媒体",
    EvidenceGrade.C: "C｜二手转述或第三方分析",
    EvidenceGrade.D: "D｜未经证实（论坛、匿名或未知来源）",
}

# Grades strong enough for a decisive supports/weakens call on their own.
DECISIVE_GRADES = {EvidenceGrade.A, EvidenceGrade.B}


class SourceType(StrEnum):
    OFFICIAL = "official"
    REGULATOR = "regulator"
    MEDIA = "media"
    ANALYST = "analyst"
    FORUM = "forum"
    UNKNOWN = "unknown"


class Basis(StrEnum):
    """Which layer produced a judgement. Never blurred together in output."""
    RULE = "rule"              # deterministic Python rules
    MODEL = "model"            # a real model provider call
    MOCK_MODEL = "mock_model"  # Mock replay - hand-written, NOT model reasoning


BASIS_ZH = {
    Basis.RULE: "确定性规则",
    Basis.MODEL: "模型推断",
    Basis.MOCK_MODEL: "Mock 回放（人工预置，非真实模型推理）",
}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

class ChangeEvent(_Strict):
    """One watch's change from a changedetection.io ChangesReport (GET /api/v1/changes?format=json)."""
    event_id: str
    source_system: Literal["changedetection.io"] = "changedetection.io"
    watch_uuid: str
    title: str
    url: str
    domain: str
    from_timestamp: int
    to_timestamp: int
    changes: int
    new_in_period: bool
    no_net_difference: bool
    truncated: bool
    watch_error: str | None = None
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)
    raw_diff: str = ""

    @property
    def cdio_history_path(self) -> str:
        return f"/api/v1/watch/{self.watch_uuid}/history/{self.to_timestamp}"

    @property
    def cdio_difference_path(self) -> str:
        return f"/api/v1/watch/{self.watch_uuid}/difference/{self.from_timestamp}/{self.to_timestamp}"

    @property
    def replay_key(self) -> str:
        return f"{self.watch_uuid}@{self.to_timestamp}"


class SourceProfile(_Strict):
    domain: str
    # Optional URL path prefix, for hosts that serve many publishers (e.g. raw.githubusercontent.com
    # serves every GitHub repository, but only /tenstorrent/ is that company's own publication).
    path_prefix: str = ""
    name: str
    source_type: SourceType
    grade: EvidenceGrade
    note: str = ""


class Assumption(_Strict):
    assumption_id: str
    statement: str
    # Deterministic signal patterns (Python regex), written by the analyst who owns the thesis.
    support_patterns: list[str] = Field(default_factory=list)
    weaken_patterns: list[str] = Field(default_factory=list)


class ThesisSource(_Strict):
    """A public source the thesis was built on (R2). Text is quoted, never paraphrased into facts."""
    source_id: str
    url: str
    publisher: str
    published_at: str            # when the source says it was published/committed
    retrieved_via: str           # how it was retrieved, e.g. "git clone of public repo"
    quote: str
    note: str = ""


class Thesis(_Strict):
    thesis_id: str
    title: str
    conclusion: str
    entities: list[str]               # company names / aliases that make an event "about" this thesis
    topic_keywords: list[str]         # terms that make an entity mention relevant to the thesis
    assumptions: list[Assumption]
    status: Literal["active", "under_review", "retired"] = "active"
    as_of: str
    owner: str
    fictional: bool
    sources: list[ThesisSource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence & judgement
# ---------------------------------------------------------------------------

class Evidence(_Strict):
    evidence_id: str
    event_id: str
    source_url: str
    source_domain: str
    source_name: str
    source_type: SourceType
    grade: EvidenceGrade
    baseline_at: int
    observed_at: int
    excerpt_added: list[str]
    excerpt_removed: list[str]
    content_fingerprint: str
    # Research subjects the content was matched to (sorted thesis ids). Part of the duplicate key so
    # identical wording about two different companies is never merged. [] = matched no thesis.
    subject_key: list[str] = Field(default_factory=list)
    truncated: bool
    cdio_watch_uuid: str
    cdio_history_path: str
    cdio_difference_path: str
    # R3: "verification_tool" = fetched by an evidence-gathering tool call (tool_call_id); the cdio_*
    # fields then point at the change that triggered the verification.
    obtained_via: Literal["cdio_change", "verification_tool"] = "cdio_change"
    tool_call_id: str | None = None


class Assessment(_Strict):
    assessment_id: str
    event_id: str
    thesis_id: str | None = None
    assumption_id: str | None = None
    direction: Direction
    # Direction the rule/model layer pointed to before grade gating or conflict handling downgraded it.
    tentative_direction: Direction | None = None
    basis: Basis
    provider_name: str | None = None
    rationale: list[str] = Field(default_factory=list)          # rule-layer reasoning
    model_rationale: str | None = None                          # model/Mock layer reasoning, kept apart
    matched_signals: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    limitations: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    # R1 never changes a thesis on its own. A human decides; we only recommend a review.
    thesis_status_change: Literal["none"] = "none"
    verification: dict | None = None   # R3: summary of the evidence-gathering run for this item


class JudgementRecord(_Strict):
    """One assessment as recorded in judgements.jsonl (append-only history across runs, R2)."""
    run_id: str
    period_to: int
    assessment_id: str
    thesis_id: str | None
    assumption_id: str | None
    direction: Direction
    tentative_direction: Direction | None
    basis: Basis
    evidence_ids: list[str]
    evidence_grade: str
    source_name: str


class JudgementTransition(_Strict):
    thesis_id: str
    assumption_id: str
    kind: Literal["first", "reinforced", "conflict", "unresolved", "changed"]
    explanation: str
    before_state: str
    before_evidence_ids: list[str]
    before_run_id: str | None
    after_state: str
    after_evidence_ids: list[str]


class FilteredEvent(_Strict):
    event_id: str | None
    watch_uuid: str
    title: str
    reason_code: Literal["no_net_difference", "watch_error", "noise_only", "duplicate"]
    reason_zh: str
    duplicate_of: str | None = None


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------

class StatementKind(StrEnum):
    FACT = "fact"                # observed content - must cite evidence
    CONTEXT = "context"          # the stored thesis - must cite a thesis id
    INFERENCE = "inference"      # a judgement - must cite evidence
    SPECULATION = "speculation"  # explicitly marked as conjecture, no citation required
    QUESTION = "question"        # open question for follow-up
    ACTION = "action"            # recommended next step
    TRACE = "trace"              # what a verification tool call did - must cite its tool call id


class Statement(_Strict):
    kind: StatementKind
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    thesis_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    basis: Basis | None = None


class BriefItem(_Strict):
    item_id: str
    priority: Literal["P1", "P2", "P3"]
    headline: str
    direction: Direction
    new_info: list[Statement]
    sources: list[Statement]
    thesis_context: list[Statement]
    impact: list[Statement]
    reasoning: list[Statement]
    evidence_grade: list[Statement]
    open_questions: list[Statement]
    actions: list[Statement]
    verification: list[Statement] = Field(default_factory=list)

    def all_statements(self) -> list[Statement]:
        return (self.new_info + self.sources + self.thesis_context + self.impact + self.reasoning
                + self.evidence_grade + self.open_questions + self.verification + self.actions)


class Brief(_Strict):
    title: str
    period_from: int
    period_to: int
    items: list[BriefItem]
    unrelated: list[Statement]
    filtered: list[FilteredEvent]
    disclaimers: list[str]
    mock_used: bool
    stats: dict[str, int]
    transitions: list[Statement] = Field(default_factory=list)
    provenance: dict | None = None   # RunProvenance.model_dump(); see provenance.py

    def all_statements(self) -> list[Statement]:
        out: list[Statement] = []
        for item in self.items:
            out.extend(item.all_statements())
        out.extend(self.unrelated)
        out.extend(self.transitions)
        return out


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)
