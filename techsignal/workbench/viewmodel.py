"""Workbench data layer: read saved runs, validate them with the existing models, and derive what the
screens show. No Streamlit import here, so everything is unit-testable.

A *case* is a JSON file under techsignal/cases/<name>/ (case.json, case_cross_batch.json, ...) that
names its theses, sources, published versions and `output_dir`. A *run* is one output directory
written by the TechSignal CLI (brief.json, brief.md, decision_log.json, evidence.jsonl, ...).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..models import Brief, Evidence, Thesis
from ..store import StoreError, load_theses
from . import labels as L

TECHSIGNAL_ROOT = Path(__file__).resolve().parent.parent.parent
CASES_ROOT = TECHSIGNAL_ROOT / "cases"
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


class LoadError(RuntimeError):
    """Something the user should read, in Chinese."""


def clean(text: str, limit: int = 220) -> str:
    """Display form of a source line: markdown links -> text, whitespace collapsed, length capped."""
    t = re.sub(r"\s+", " ", _MD_LINK.sub(r"\1", text or "")).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "未知"
    return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def fmt_iso(value: str | None) -> str:
    if not value:
        return "未知"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def _read_json(path: Path, what: str):
    if not path.exists():
        raise LoadError(f"缺少{what}：{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LoadError(f"{what}无法读取（{path.name}）：{e}") from None


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@dataclass
class BatchRef:
    number: int
    label: str
    run_dir: Path
    version_from: dict | None
    version_to: dict | None


@dataclass
class CaseRef:
    case_id: str
    title: str
    data_kind: str
    case_path: Path
    theses_path: Path
    sources_path: Path
    verify_policy_path: Path | None
    observation_path: Path
    batches: list[BatchRef]
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def is_multi_batch(self) -> bool:
        return len(self.batches) > 1


def load_case(case_path: Path) -> CaseRef:
    case_path = Path(case_path)
    c = _read_json(case_path, "案例定义")
    base = case_path.parent
    for key in ("case_id", "title", "theses", "sources", "versions", "output_dir"):
        if key not in c:
            raise LoadError(f"案例定义 {case_path.name} 缺少字段「{key}」")
    out = base / c["output_dir"]
    versions = c["versions"]
    if c.get("batches"):
        batches = [BatchRef(n, f"批次 {n}：{versions[b]['label']}", out / f"batch{n}", versions[a], versions[b])
                   for n, (a, b) in enumerate(c["batches"], 1)]
    else:
        batches = [BatchRef(1, f"单批次：{versions[-1]['label']}", out, versions[0] if len(versions) > 1 else None,
                            versions[-1])]
    return CaseRef(case_id=c["case_id"], title=c["title"], data_kind=c.get("data_kind", ""), case_path=case_path,
                   theses_path=base / c["theses"], sources_path=base / c["sources"],
                   verify_policy_path=(base / c["verify_policy"]) if c.get("verify_policy") else None,
                   observation_path=out / "observation_record.json", batches=batches, raw=c)


def discover_cases(root: Path = CASES_ROOT) -> tuple[list[CaseRef], list[str]]:
    """All loadable cases under `root`, plus readable problems for the ones that are not."""
    cases, problems = [], []
    if not Path(root).exists():
        return [], [f"案例目录不存在：{root}"]
    for path in sorted(Path(root).glob("*/case*.json")):
        try:
            cases.append(load_case(path))
        except LoadError as e:
            problems.append(f"{path.parent.name}/{path.name}：{e}")
    return cases, problems


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@dataclass
class RunView:
    run_dir: Path
    brief: Brief
    markdown: str
    log: dict
    evidence: dict[str, Evidence]
    trace: dict | None
    run_record: dict | None
    theses: list[Thesis]
    observation: dict | None

    # -- provenance -------------------------------------------------------------------------

    @property
    def provenance(self) -> dict:
        return self.brief.provenance or {}

    def provenance_lines(self) -> list[str]:
        p = self.provenance
        out = [L.zh(L.INPUT_ORIGIN, p.get("input_origin"), "数据来源未记录")]
        if p.get("input_origin") == "live_cdio_api":
            out.append(f"在线实测时间：{fmt_iso(p.get('fetched_at_utc'))}（changedetection.io {p.get('cdio_version') or '版本未知'}）")
        elif p.get("input_origin") == "replay_of_captured_live":
            out.append(f"原始数据的在线拉取时间：{fmt_iso(p.get('fetched_at_utc'))}")
        out.append(f"数据性质：{L.zh(L.DATA_NATURE, p.get('data_nature'), '未记录')}")
        out.append(f"模型层：{L.zh(L.MODEL_LAYER, p.get('model_layer'), '未记录')}")
        if p.get("verification") == "deterministic_policy":
            out.append(f"补证：由确定性规则触发，实际执行 {p.get('verification_tool_calls', 0)} 次工具查询，未使用模型")
        return out

    # -- lookups ------------------------------------------------------------------------------

    def thesis(self, thesis_id: str) -> Thesis | None:
        return next((t for t in self.theses if t.thesis_id == thesis_id), None)

    def assumption_text(self, thesis_id: str, assumption_id: str | None) -> str:
        th = self.thesis(thesis_id)
        if not th or not assumption_id:
            return "未指明关键假设"
        a = next((x for x in th.assumptions if x.assumption_id == assumption_id), None)
        return a.statement if a else "未知假设"

    def publication_of(self, url: str, fetched_ts: int | None = None) -> dict | None:
        """The published version a piece of evidence came from: matched on changedetection.io's
        snapshot time first (exact), then on the commit-pinned URL."""
        obs = (self.observation or {}).get("observations", [])
        if fetched_ts is not None:
            hit = next((o for o in obs if o.get("snapshot_timestamp") == fetched_ts), None)
            if hit:
                return hit
        return next((o for o in obs if o.get("url") == url), None)

    # -- derived views --------------------------------------------------------------------------

    def assessments(self) -> list[dict]:
        return list(self.log.get("assessments", []))

    def overview(self) -> dict:
        s = self.brief.stats
        relevant = [a for a in self.assessments() if a["direction"] != "unrelated"]
        pending = []
        for a in relevant:
            for q in a.get("open_questions", []):
                if q not in pending:
                    pending.append(q)
        return {
            "subjects": [t.title for t in self.theses],
            "changes": s.get("events_total", 0),
            "filtered": s.get("events_filtered", 0),
            "judgements": [self.judgement_row(a) for a in relevant],
            "pending": pending,
            "headline": self.headline(),
        }

    def judgement_row(self, a: dict) -> dict:
        th = self.thesis(a.get("thesis_id") or "")
        return {
            "thesis": th.title if th else "未匹配研究判断",
            "assumption": f"{a.get('assumption_id') or ''} {self.assumption_text(a.get('thesis_id'), a.get('assumption_id'))}".strip(),
            "state": L.zh(L.DIRECTION, a["direction"]),
            "state_short": L.zh(L.DIRECTION_SHORT, a["direction"]),
            "tentative": L.zh(L.DIRECTION_SHORT, a.get("tentative_direction"), "") if a.get("tentative_direction") else "",
            "basis": L.zh(L.BASIS, a.get("basis")),
            "confidence": L.zh(L.CONFIDENCE, a.get("confidence")),
            "action": a.get("recommended_action", ""),
            "evidence_ids": list(a.get("evidence_ids", [])) + list(a.get("conflicting_evidence_ids", [])),
            "from_verification": any(self.evidence.get(e) is not None and self.evidence[e].obtained_via == "verification_tool"
                                     for e in a.get("evidence_ids", [])),
        }

    def headline(self) -> str:
        rows = [a for a in self.assessments() if a["direction"] != "unrelated"]
        if not rows:
            return "本批次没有与研究判断相关的变化。"
        weak = [a for a in rows if a["direction"] == "weakens"]
        pend = [a for a in rows if a["direction"] == "insufficient"]
        sup = [a for a in rows if a["direction"] == "supports"]
        parts = []
        if weak:
            parts.append(f"{len(weak)} 项变化削弱原判断，建议复核")
        if pend:
            parts.append(f"{len(pend)} 项证据不足，待核实")
        if sup:
            parts.append(f"{len(sup)} 项支持原判断")
        return "；".join(parts) + "。研究结论本身未被系统修改。"

    def evidence_view(self, evidence_id: str) -> dict | None:
        ev = self.evidence.get(evidence_id)
        if ev is None:
            return None
        pub = self.publication_of(ev.source_url, ev.observed_at if ev.obtained_via == "cdio_change" else None)
        used = []
        for a in self.assessments():
            if evidence_id in a.get("evidence_ids", []) or evidence_id in a.get("conflicting_evidence_ids", []):
                role = "冲突证据" if evidence_id in a.get("conflicting_evidence_ids", []) else "依据"
                used.append(f"{self.judgement_row(a)['assumption']}：{L.zh(L.DIRECTION, a['direction'])}（作为{role}）")
        via_tool = ev.obtained_via == "verification_tool"
        return {
            "id": ev.evidence_id,
            "source_name": ev.source_name,
            "source_type": L.zh(L.SOURCE_TYPE, ev.source_type),
            "grade": L.zh(L.GRADE, ev.grade),
            "url": ev.source_url,
            "obtained": f"补证查询 {ev.tool_call_id} 获取" if via_tool else "changedetection.io 监测到的变化",
            "published": (f"{fmt_iso(pub.get('commit_date'))}（提交 {pub.get('commit', '')[:10]}）" if pub
                          else "原始来源未标注发布时间"),
            "fetched": ("与本批次补证同时获取" if via_tool else f"{fmt_ts(ev.observed_at)}（changedetection.io 抓取）"),
            "added": [clean(x) for x in ev.excerpt_added],
            "removed": [clean(x) for x in ev.excerpt_removed],
            "used_by": used,
            "truncated": ev.truncated,
        }

    def verification(self) -> list[dict]:
        if not self.trace:
            return []
        calls = {c["call_id"]: c for c in self.trace.get("calls", [])}
        out = []
        for s in self.trace.get("summaries", []):
            steps = []
            for cid in s.get("call_ids", []):
                c = calls.get(cid)
                if not c:
                    continue
                lines = [clean(" ｜ ".join(f["lines"]), 260) for f in c.get("findings", []) if not f.get("flagged_injection")]
                steps.append({
                    "id": cid, "tool": L.zh(L.TOOL, c["tool"], "其他工具"), "purpose": c.get("purpose", ""),
                    "status": L.zh(L.CALL_STATUS, c["status"]), "ok": c["status"] in L.CALL_OK,
                    "url": c.get("url"), "error": c.get("error"), "detail": c.get("detail", ""),
                    "found": lines, "evidence": c.get("evidence_created", []) + c.get("evidence_reused", []),
                    "excluded_injection": bool(c.get("flags")), "attempts": c.get("attempts", 0),
                })
            out.append({
                "target": f"{s.get('assumption_id') or ''} {self.assumption_text(s.get('thesis_id'), s.get('assumption_id'))}".strip(),
                "triggers": [L.zh(L.TRIGGER, t, "其他触发原因") for t in s.get("triggers", [])],
                "outcome": L.zh(L.OUTCOME, s.get("outcome")),
                "stop": L.zh(L.STOP, s.get("stop_reason")),
                "hints": [h.split("：", 1)[-1] for h in s.get("hints", [])],
                "evidence": s.get("evidence_ids", []),
                "steps": steps,
                "changed_judgement": s.get("outcome") == "new_decisive_evidence",
            })
        return out

    def transitions(self) -> list[dict]:
        out = []
        for t in self.log.get("cross_batch_transitions", []):
            out.append({
                "assumption": f"{t['assumption_id']} {self.assumption_text(t['thesis_id'], t['assumption_id'])}",
                "kind": L.zh(L.TRANSITION, t["kind"]),
                "before": t["before_state"], "after": t["after_state"], "why": t["explanation"],
                "evidence": list(dict.fromkeys(t["before_evidence_ids"] + t["after_evidence_ids"])),
            })
        return out

    def change_lines(self, limit: int = 4) -> list[dict]:
        """Key changed lines of the monitored change(s): signal lines first."""
        out = []
        for a in self.assessments():
            ev = self.evidence.get(a["evidence_ids"][0]) if a.get("evidence_ids") else None
            if ev is None or ev.obtained_via != "cdio_change":
                continue
            sig = " ".join(a.get("matched_signals", []))
            for side, lines in (("新增", ev.excerpt_added), ("删除", ev.excerpt_removed)):
                ranked = sorted(lines, key=lambda x: x not in sig)
                for x in ranked[:limit]:
                    out.append({"side": side, "text": clean(x), "key": x in sig, "evidence": ev.evidence_id})
        return out


def load_run(run_dir: Path, theses_path: Path, observation_path: Path | None = None) -> RunView:
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise LoadError(f"运行结果目录不存在：{run_dir}")
    raw = _read_json(run_dir / "brief.json", "研究速览数据")
    try:
        brief = Brief.model_validate(raw)
    except ValidationError as e:
        raise LoadError(f"研究速览数据格式不符：{e.errors()[0]['msg']}") from None
    md_path = run_dir / "brief.md"
    if not md_path.exists():
        raise LoadError("缺少中文速览文件：brief.md")
    log = _read_json(run_dir / "decision_log.json", "决策记录")
    evidence: dict[str, Evidence] = {}
    ev_path = run_dir / "evidence.jsonl"
    if not ev_path.exists():
        raise LoadError("缺少证据台账：evidence.jsonl")
    for n, line in enumerate(ev_path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                ev = Evidence.model_validate_json(line)
            except ValidationError:
                raise LoadError(f"证据台账第 {n} 行格式不符") from None
            evidence[ev.evidence_id] = ev
    try:
        theses = load_theses(theses_path)
    except StoreError as e:
        raise LoadError(f"研究判断文件无法读取：{e}") from None
    trace = _read_json(run_dir / "verification_trace.json", "补证轨迹") if (run_dir / "verification_trace.json").exists() else None
    record = _read_json(run_dir / "run_record.json", "运行记录") if (run_dir / "run_record.json").exists() else None
    obs = _read_json(observation_path, "观测记录") if observation_path and Path(observation_path).exists() else None
    return RunView(run_dir, brief, md_path.read_text(encoding="utf-8"), log, evidence, trace, record, theses, obs)


def load_batches(case: CaseRef, run_dirs: list[Path] | None = None) -> list[RunView]:
    """Every batch of a case (saved live results by default, or replay outputs)."""
    dirs = run_dirs or [b.run_dir for b in case.batches]
    return [load_run(d, case.theses_path, case.observation_path) for d in dirs]


def timeline(case: CaseRef, runs: list[RunView]) -> list[dict]:
    """One entry per batch: publication dates vs. fetch times, the change, and its effect."""
    obs = runs[0].observation if runs else None
    fetched = {o.get("commit"): o.get("snapshot_timestamp") for o in (obs or {}).get("observations", [])}
    out = []
    for b, run in zip(case.batches, runs, strict=True):
        vf, vt = b.version_from or {}, b.version_to or {}
        out.append({
            "label": b.label,
            "from_label": vf.get("label", "（无更早版本）"),
            "from_published": fmt_iso(vf.get("commit_date")),
            "from_fetched": fmt_ts(fetched.get(vf.get("commit"))) if vf else "—",
            "to_label": vt.get("label", ""),
            "to_published": fmt_iso(vt.get("commit_date")),
            "to_fetched": fmt_ts(fetched.get(vt.get("commit"))),
            "to_url": vt.get("browse_url") or vt.get("url"),
            "commit": (vt.get("commit") or "")[:10],
            "judgements": [run.judgement_row(a) for a in run.assessments() if a["direction"] != "unrelated"],
            "transitions": run.transitions(),
            "changes": run.change_lines(),
            "verified": bool(run.verification()),
        })
    return out
