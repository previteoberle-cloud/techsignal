"""End-to-end run: ChangesReport -> triage -> evidence ledger -> assessment -> brief -> validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .adapters.cdio import parse_changes_report
from .assess import AssessStats, assess_event, recommend, resolve_conflicts, resolve_cross_batch
from .brief import build_brief, render_markdown
from .models import (
    Assessment,
    Brief,
    ChangeEvent,
    Evidence,
    EvidenceGrade,
    FilteredEvent,
    JudgementRecord,
    JudgementTransition,
    Thesis,
    short_hash,
)
from .providers import LLMProvider
from .rules import match_theses
from .store import EvidenceLedger, SourceRegistry
from .triage import build_evidence, clean_lines, fingerprint, pre_filter
from .validate import assert_valid

_GRADE_RANK = {EvidenceGrade.A: 0, EvidenceGrade.B: 1, EvidenceGrade.C: 2, EvidenceGrade.D: 3}


@dataclass
class RunResult:
    meta: dict
    events: list[ChangeEvent]
    filtered: list[FilteredEvent]
    evidence: dict[str, Evidence]
    assessments: list[Assessment]
    brief: Brief
    markdown: str
    stats: AssessStats
    provider_name: str | None = None
    notes: list[str] = field(default_factory=list)
    transitions: list[JudgementTransition] = field(default_factory=list)
    new_records: list[JudgementRecord] = field(default_factory=list)

    def decision_log(self) -> dict:
        return {
            "meta": self.meta,
            "provenance": self.brief.provenance,
            "provider": self.provider_name,
            "stats": asdict(self.stats),
            "filtered": [f.model_dump(mode="json") for f in self.filtered],
            "assessments": [a.model_dump(mode="json") for a in self.assessments],
            **({"cross_batch_transitions": [t.model_dump(mode="json") for t in self.transitions]}
               if self.transitions else {}),
        }

    def by_watch(self, watch_uuid: str) -> list[Assessment]:
        ids = {e.event_id for e in self.events if e.watch_uuid == watch_uuid}
        return [a for a in self.assessments if a.event_id in ids]

    def filtered_for(self, watch_uuid: str) -> FilteredEvent | None:
        return next((f for f in self.filtered if f.watch_uuid == watch_uuid), None)


def run_pipeline(report: Any, theses: list[Thesis], sources: SourceRegistry,
                 provider: LLMProvider | None = None, ledger: EvidenceLedger | None = None,
                 validate: bool = True, persist: bool = True,
                 history: list[JudgementRecord] | None = None, provenance=None) -> RunResult:
    """Run the whole chain. The ledger is written only at the very end, after validation passed,
    and only when persist=True and the ledger has a path. The CLI passes persist=False and commits
    the ledger together with the other outputs (see cli._commit_outputs).

    history: judgement records of earlier runs (judgements.jsonl). When given, this run's
    assessments are checked against them (assess.resolve_cross_batch) and the brief explains every
    before -> after transition. Theses are never modified either way."""
    meta, events = parse_changes_report(report)
    ledger = ledger if ledger is not None else EvidenceLedger()
    stats = AssessStats()

    # Oldest first, so "first seen" in duplicate detection means earliest observed.
    events = sorted(events, key=lambda e: (e.to_timestamp, e.watch_uuid, e.event_id))
    filtered: list[FilteredEvent] = []
    used_evidence: dict[str, Evidence] = {}
    assessments: list[Assessment] = []
    seen: dict[tuple, Evidence] = {}

    for event in events:
        f = pre_filter(event)
        if f:
            filtered.append(f)
            continue
        t = clean_lines(event)
        if t.is_empty:
            filtered.append(FilteredEvent(
                event_id=event.event_id, watch_uuid=event.watch_uuid, title=event.title,
                reason_code="noise_only",
                reason_zh=f"仅含噪声或位置移动：噪声 {len(t.dropped_noise)} 行，移动 {len(t.dropped_moved)} 行"))
            continue

        source = sources.profile(event.domain, event.url)
        fp = fingerprint(t)
        # Duplicate = same content about the same research subject. Matching first means identical
        # wording on two different companies' pages is assessed twice, not merged (R1.1).
        matches = match_theses(event.title, t, theses)
        subject = sorted(m.thesis.thesis_id for m in matches)
        earlier = (seen.get((fp, tuple(subject)))
                   or ledger.find_duplicate(fp, subject, exclude_event=event.event_id))
        if earlier and _GRADE_RANK[source.grade] >= _GRADE_RANK[earlier.grade]:
            filtered.append(FilteredEvent(
                event_id=event.event_id, watch_uuid=event.watch_uuid, title=event.title,
                reason_code="duplicate", duplicate_of=earlier.evidence_id,
                reason_zh=f"内容与已记录证据一致，且来源等级（{source.grade.value}）不高于原证据（{earlier.grade.value}）"))
            continue

        ev = build_evidence(event, t, source, subject_key=subject)
        ledger.add(ev)
        seen[(fp, tuple(subject))] = ev
        used_evidence[ev.evidence_id] = ev
        assessments.extend(assess_event(event, t, ev, theses, provider, stats, matches=matches))

    assessments = resolve_conflicts(assessments, used_evidence, stats)
    transitions: list[JudgementTransition] = []
    if history is not None:
        assessments, transitions = resolve_cross_batch(assessments, history, stats)
    assessments = [recommend(a) for a in assessments]
    run_id = "RUN-" + short_hash(str(meta["from"]), str(meta["to"]), *sorted(used_evidence), length=8)
    new_records = [JudgementRecord(
        run_id=run_id, period_to=meta["to"], assessment_id=a.assessment_id, thesis_id=a.thesis_id,
        assumption_id=a.assumption_id, direction=a.direction, tentative_direction=a.tentative_direction,
        basis=a.basis, evidence_ids=a.evidence_ids, evidence_grade=used_evidence[a.evidence_ids[0]].grade.value,
        source_name=used_evidence[a.evidence_ids[0]].source_name) for a in assessments]

    provider_name = getattr(provider, "name", None) if provider else None
    # Conflicting evidence may come from earlier batches (history mode): look it up in the ledger.
    evidence_lookup = {i: ledger.get(i) for i in ledger.ids()} | used_evidence
    from .provenance import RunProvenance, data_nature, with_model_usage
    if provenance is None:
        provenance = RunProvenance(input_mode="file", input_origin="file_unverified", data_nature=data_nature(theses))
    provenance = with_model_usage(provenance, provider, assessments)
    brief = build_brief(meta, events, evidence_lookup, assessments, filtered, theses,
                        provider_name, bool(provider and provider.is_mock), transitions=transitions,
                        provenance=provenance)
    md = render_markdown(brief)
    if validate:
        assert_valid(brief, md, ledger.ids(), {t.thesis_id for t in theses})
    if persist and ledger.path:
        ledger.save()

    return RunResult(meta=meta, events=events, filtered=filtered, evidence=used_evidence,
                     assessments=assessments, brief=brief, markdown=md, stats=stats,
                     provider_name=provider_name, transitions=transitions, new_records=new_records)
