import json

import pytest
from techsignal.models import EvidenceGrade, SourceType
from techsignal.store import EvidenceLedger, StoreError, load_theses


def test_unknown_domain_is_grade_d(sources):
    p = sources.profile("random-blog.example")
    assert p.grade == EvidenceGrade.D and p.source_type == SourceType.UNKNOWN


def test_parent_domain_match(sources):
    assert sources.profile("ir.xingheng-semi.example").grade == EvidenceGrade.A


def test_duplicate_thesis_ids_rejected(tmp_path, theses):
    data = {"theses": [theses[0].model_dump(mode="json")] * 2}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StoreError):
        load_theses(p)


def test_unknown_thesis_field_rejected(tmp_path, theses):
    d = theses[0].model_dump(mode="json")
    d["typo_field"] = 1
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"theses": [d]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StoreError):
        load_theses(p)


def test_ledger_roundtrip_and_idempotent(tmp_path, theses, sources):
    from conftest import DEMO
    from techsignal.pipeline import run_pipeline
    path = tmp_path / "evidence.jsonl"
    r = run_pipeline((DEMO / "changes_report.json").read_text(encoding="utf-8"), theses, sources,
                     ledger=EvidenceLedger(path))
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n == len(r.evidence) > 0
    reloaded = EvidenceLedger(path)
    assert reloaded.ids() == set(r.evidence)
    for ev in r.evidence.values():
        assert reloaded.add(ev) is False
    assert len(path.read_text(encoding="utf-8").splitlines()) == n


def test_corrupt_ledger_line_is_an_error(tmp_path):
    p = tmp_path / "evidence.jsonl"
    p.write_text('{"evidence_id": "EV-1"}\n', encoding="utf-8")
    with pytest.raises(StoreError):
        EvidenceLedger(p)
