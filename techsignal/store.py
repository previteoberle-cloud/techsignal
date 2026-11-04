"""Structured, reproducible storage: thesis store (JSON), source registry (JSON), evidence ledger (JSONL).

The pipeline only ever READS the thesis store. Changing a research conclusion is a human decision.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .models import Evidence, EvidenceGrade, SourceProfile, SourceType, Thesis


class StoreError(ValueError):
    pass


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise StoreError(f"无法读取 {path}：{e}") from None


def load_theses(path: Path) -> list[Thesis]:
    data = _read_json(path)
    items = data.get("theses") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise StoreError(f"{path} 缺少 'theses' 列表")
    try:
        theses = TypeAdapter(list[Thesis]).validate_python(items)
    except ValidationError as e:
        raise StoreError(f"{path} 中的研究判断格式错误：{e.errors()[0]}") from None
    ids = [t.thesis_id for t in theses]
    if len(ids) != len(set(ids)):
        raise StoreError(f"{path} 中存在重复的 thesis_id")
    return theses


class SourceRegistry:
    """Maps a domain to a source profile. Unknown domains are grade D, never silently trusted."""

    def __init__(self, profiles: list[SourceProfile]):
        self._by_domain = {p.domain.lower(): p for p in profiles}

    @classmethod
    def load(cls, path: Path) -> SourceRegistry:
        data = _read_json(path)
        items = data.get("sources") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise StoreError(f"{path} 缺少 'sources' 列表")
        return cls(TypeAdapter(list[SourceProfile]).validate_python(items))

    def profile(self, domain: str) -> SourceProfile:
        domain = (domain or "").lower()
        # exact match, then parent domains (news.example.org -> example.org)
        parts = domain.split(".")
        for i in range(len(parts) - 1):
            p = self._by_domain.get(".".join(parts[i:]))
            if p:
                return p
        return SourceProfile(domain=domain, name=f"未登记来源（{domain or '无域名'}）",
                             source_type=SourceType.UNKNOWN, grade=EvidenceGrade.D,
                             note="来源未在登记表中，按最低等级处理")


class EvidenceLedger:
    """Append-only JSONL ledger. Writing the same evidence twice is a no-op (ids are content hashes)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._items: dict[str, Evidence] = {}
        if self.path and self.path.exists():
            for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    ev = Evidence.model_validate_json(line)
                except ValidationError as e:
                    raise StoreError(f"证据台账 {self.path} 第 {n} 行无效：{e.errors()[0]['msg']}") from None
                self._items[ev.evidence_id] = ev

    def add(self, ev: Evidence) -> bool:
        if ev.evidence_id in self._items:
            return False
        self._items[ev.evidence_id] = ev
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.model_dump_json() + "\n")
        return True

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def ids(self) -> set[str]:
        return set(self._items)

    def find_by_fingerprint(self, fingerprint: str, exclude_event: str | None = None) -> Evidence | None:
        for ev in self._items.values():
            if ev.content_fingerprint == fingerprint and ev.event_id != exclude_event:
                return ev
        return None

    def __len__(self) -> int:
        return len(self._items)
