"""R6 Checkpoint 1: the 「我的研究项目」 workspace driven headlessly (real Streamlit script and widgets)."""
import pytest

pytest.importorskip("streamlit")
from conftest import ROOT
from streamlit.testing.v1 import AppTest

from techsignal import projects as P

APP = str(ROOT / "techsignal" / "workbench" / "app.py")


def _text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] \
        + [w.value for w in at.warning] + [s.value for s in at.success] + [e.value for e in at.error]
    return "\n".join(str(p) for p in parts)


@pytest.fixture()
def root(tmp_path, monkeypatch):
    r = tmp_path / "projects"
    monkeypatch.setenv(P.PROJECTS_ENV, str(r))
    return r


def _open(root):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.radio(key="workspace").set_value("我的研究项目").run()
    assert not at.exception
    return at


def _fill(at, prefix="new", name="Tenstorrent", urls="https://github.com/tenstorrent/tt-metal 官方仓库"):
    at.text_input(key=f"{prefix}-name").set_value(name)
    at.text_area(key=f"{prefix}-question").set_value("Blackhole 的大模型推理软件成熟度")
    at.text_area(key=f"{prefix}-judgement").set_value("仍处早期，没有 70B 级模型的官方记录。")
    at.text_input(key=f"{prefix}-a0").set_value("尚无 70B 级模型在 Blackhole 上的官方运行记录")
    at.text_input(key=f"{prefix}-a0-w").set_value("70B，Llama 3.1 70B")
    at.text_area(key=f"{prefix}-urls").set_value(urls)
    at.text_input(key=f"{prefix}-kw").set_value("Blackhole")
    return at.run()


def test_default_workspace_is_still_the_demo_case(root):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert at.radio(key="workspace").value == "示例案例" and at.selectbox(key="case").value == "tenstorrent-blackhole-70b"


def test_empty_workspace_opens_the_new_project_form(root):
    at = _open(root)
    assert at.selectbox(key="project").value == "__new__"
    assert "新建研究项目" in _text(at) and "不需要编写 JSON" in _text(at)


def test_create_project_from_the_form(root):
    at = _fill(_open(root))
    at.button(key="create_project").click().run()
    assert not at.exception
    items, _ = P.ProjectStore(root).list()
    assert len(items) == 1 and items[0].status == "ready"
    assert at.selectbox(key="project").value == items[0].project_id
    t = _text(at)
    assert "尚无监测变化" in t and "不会显示任何示例或伪造的数据" in t
    assert "70B" in t and "公司官方｜A 级" in t and "不是语义理解" in t


def test_invalid_input_shows_errors_and_creates_nothing(root):
    at = _fill(_open(root), urls="http://127.0.0.1:5000/ 内网")
    at.button(key="create_project").click().run()
    assert "不能监测本机、内网或保留地址" in _text(at)
    assert P.ProjectStore(root).list()[0] == []


def test_draft_can_be_saved_and_completed_later(root):
    at = _open(root)
    at.text_input(key="new-name").set_value("Cerebras").run()
    at.button(key="save_draft").click().run()
    p = P.ProjectStore(root).list()[0][0]
    assert p.status == "draft" and "这是草稿" in _text(at)
    at.radio(key="ppage").set_value("研究设定").run()
    _fill(at, prefix=f"edit-{p.project_id}-v1", name="Cerebras")
    at.button(key="finish_project").click().run()
    assert not at.exception
    p2 = P.ProjectStore(root).get(p.project_id)
    assert p2.status == "ready" and p2.version == 2


def test_duplicate_name_is_refused_in_the_form(root):
    P.ProjectStore(root).create(P.spec_from_form(name="Tenstorrent"), ready=False)
    at = _open(root)
    at.selectbox(key="project").set_value("__new__").run()
    _fill(at, name="tenstorrent")
    at.button(key="create_project").click().run()
    assert "同名" in _text(at) and len(P.ProjectStore(root).list()[0]) == 1


def test_edit_keeps_history_and_stale_edit_is_refused(root):
    at = _fill(_open(root))
    at.button(key="create_project").click().run()
    pid = at.selectbox(key="project").value
    at.radio(key="ppage").set_value("研究设定").run()
    at.text_area(key=f"edit-{pid}-v1-judgement").set_value("出现 70B 记录后，判断需要复核。")
    at.text_input(key=f"edit-{pid}-v1-note").set_value("复核")
    at.button(key="save_edit").click().run()
    assert P.ProjectStore(root).get(pid).version == 2
    at.radio(key="ppage").set_value("修改记录").run()
    t = _text(at)
    assert "v1" in t and "v2" in t and "研究判断" in t and "复核" in t

    # another window saves v3 while this one still shows the v2 form
    at.radio(key="ppage").set_value("研究设定").run()
    store = P.ProjectStore(root)
    cur = store.get(pid)
    store.update(pid, cur.spec.model_copy(update={"judgement": "另一个窗口的修改"}), expected_version=2, ready=True)
    at.text_area(key=f"edit-{pid}-v2-judgement").set_value("本窗口基于旧版本的修改")
    at.button(key="save_edit").click().run()
    t = _text(at)
    assert "为避免覆盖，本次未保存" in t and "已在别处更新到 v3" in t
    assert store.get(pid).spec.judgement == "另一个窗口的修改"
    at.button(key="reload_latest").click().run()
    assert at.text_area(key=f"edit-{pid}-v3-judgement").value == "另一个窗口的修改"


def test_projects_root_inside_cases_is_refused(monkeypatch):
    monkeypatch.setenv(P.PROJECTS_ENV, str(ROOT / "cases" / "mine"))
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.radio(key="workspace").set_value("我的研究项目").run()
    assert "不能放在示例案例目录中" in _text(at)
    assert not (ROOT / "cases" / "mine").exists()


# ---------------------------------------------------------------- Checkpoint 2: monitoring ---------

def test_monitoring_flow_in_the_workbench(root):
    from fake_cdio import FakeCdio
    page = "https://github.com/tenstorrent/tt-metal"
    cdio = FakeCdio(key="sk-ui-SECRET")
    cdio.pages[page] = "Models\n| Llama 3.1 8B | n150 | 23 t/s |\n"
    try:
        at = _fill(_open(root))
        at.button(key="create_project").click().run()
        pid = at.selectbox(key="project").value
        at.radio(key="ppage").set_value("监测与评估").run()
        assert "当前未连接 changedetection.io" in _text(at)          # works without the service
        at.text_input(key="mon_url").set_value(cdio.url)
        at.text_input(key="mon_key").set_value("sk-ui-SECRET")
        at.button(key="mon_connect").click().run()
        assert "已连接：changedetection.io fake-0.0" in _text(at)
        assert at.button(key="mon_apply").disabled                  # explicit confirmation first
        assert cdio.watches == {}
        at.checkbox(key="mon_confirm").check().run()
        at.button(key="mon_apply").click().run()
        assert not at.exception and len(cdio.watches) == 1
        at.button(key="mon_check").click().run()
        assert "本次没有新的变化" in _text(at)
        cdio.pages[page] += "| Llama 3.1 70B | QuietBox (Blackhole) | 15 t/s |\n"
        at.button(key="mon_check").click().run()
        assert "已用真实流水线完成研判" in _text(at)
        at.radio(key="ppage").set_value("研判结果").run()
        t = _text(at)
        assert "削弱原判断" in t and "判断来源：**确定性规则**" in t and "待人工复核" in t
        assert "70B" in t and "研究判断保持不变" in t
        at.radio(key="ppage").set_value("概况").run()
        assert "尚无监测变化" not in _text(at) and "削弱原判断" in _text(at)
        for f in root.rglob("*"):
            if f.is_file():
                assert "sk-ui-SECRET" not in f.read_text(encoding="utf-8", errors="replace")
        assert P.ProjectStore(root).get(pid).version == 1
    finally:
        cdio.close()
