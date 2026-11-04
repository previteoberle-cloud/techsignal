"""Citation integrity: tampering with a valid brief must be caught."""
import pytest
from conftest import DEMO
from techsignal.models import Statement
from techsignal.models import StatementKind as K
from techsignal.pipeline import run_pipeline
from techsignal.providers import MockReplayProvider
from techsignal.validate import CitationError, assert_valid, validate_brief, validate_markdown


@pytest.fixture()
def demo_run(theses, sources):
    return run_pipeline((DEMO / "changes_report.json").read_text(encoding="utf-8"), theses, sources,
                        provider=MockReplayProvider.load(DEMO / "mock_replay.json"))


def _ids(run, theses):
    return set(run.evidence), {t.thesis_id for t in theses}


def test_demo_brief_is_valid(demo_run, theses):
    ev, th = _ids(demo_run, theses)
    assert validate_brief(demo_run.brief, ev, th) == []
    assert validate_markdown(demo_run.markdown, ev, th, demo_run.brief.mock_used) == []


def _tamper(run, attr, statement):
    brief = run.brief.model_copy(deep=True)
    getattr(brief.items[0], attr).append(statement)
    return brief


def test_fact_without_source_is_an_error(demo_run, theses):
    brief = _tamper(demo_run, "new_info", Statement(kind=K.FACT, text="星衡半导体获得大额订单"))
    issues = validate_brief(brief, *_ids(demo_run, theses))
    assert [i.code for i in issues] == ["FACT_WITHOUT_EVIDENCE"]


def test_inference_without_source_is_an_error(demo_run, theses):
    brief = _tamper(demo_run, "reasoning", Statement(kind=K.INFERENCE, text="管理层可能更换"))
    assert [i.code for i in validate_brief(brief, *_ids(demo_run, theses))] == ["INFERENCE_WITHOUT_EVIDENCE"]


def test_speculation_may_stand_without_source(demo_run, theses):
    brief = _tamper(demo_run, "reasoning", Statement(kind=K.SPECULATION, text="管理层可能更换"))
    assert validate_brief(brief, *_ids(demo_run, theses)) == []


def test_wrong_evidence_id_is_an_error(demo_run, theses):
    brief = _tamper(demo_run, "new_info", Statement(kind=K.FACT, text="x", evidence_ids=["EV-ffffffffff"]))
    assert [i.code for i in validate_brief(brief, *_ids(demo_run, theses))] == ["UNKNOWN_EVIDENCE"]


def test_wrong_thesis_id_is_an_error(demo_run, theses):
    brief = _tamper(demo_run, "thesis_context", Statement(kind=K.CONTEXT, text="x", thesis_ids=["TH-999"]))
    assert [i.code for i in validate_brief(brief, *_ids(demo_run, theses))] == ["UNKNOWN_THESIS"]


def test_markdown_fake_reference_is_an_error(demo_run, theses):
    md = demo_run.markdown + "\n- 补充说明 [EV-0123456789]\n"
    assert "UNKNOWN_REFERENCE" in [i.code for i in validate_markdown(md, *_ids(demo_run, theses), True)]


def test_markdown_fact_line_without_citation_is_an_error(demo_run, theses):
    md = demo_run.markdown.replace("**新增信息**\n", "**新增信息**\n- 新增：无出处的说法\n", 1)
    assert "FACT_LINE_WITHOUT_EVIDENCE" in [i.code for i in validate_markdown(md, *_ids(demo_run, theses), True)]


def test_mock_must_be_disclosed(demo_run, theses):
    md = demo_run.markdown.replace("Mock 回放", "模型")
    codes = [i.code for i in validate_markdown(md, *_ids(demo_run, theses), True)]
    assert "MOCK_NOT_DISCLOSED" in codes


def test_assert_valid_raises(demo_run, theses):
    brief = _tamper(demo_run, "new_info", Statement(kind=K.FACT, text="无出处"))
    with pytest.raises(CitationError):
        assert_valid(brief, demo_run.markdown, *_ids(demo_run, theses))
