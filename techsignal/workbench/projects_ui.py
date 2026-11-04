"""「我的研究项目」: create, edit and follow research projects from the browser (R6).

Rendered by app.py when the sidebar workspace is 「我的研究项目」. The committed demo cases are never
read or written here; projects live under TECHSIGNAL_PROJECTS_ROOT (default ~/.techsignal/projects).
"""
from __future__ import annotations

import streamlit as st

from techsignal import projects as P
from techsignal.workbench import labels as L

NEW = "__new__"
PROJECT_PAGES = ["概况", "监测与评估", "研究设定", "修改记录"]
MATCH_NOTE = ("关键词和信号词按字面匹配（不区分大小写），属于确定性的关键词规则，不是语义理解。"
              "没有填写信号词的假设，相关变化只会被标为「待人工判断」。")


def _store() -> P.ProjectStore | None:
    try:
        return P.ProjectStore()
    except P.ProjectError as e:
        st.error(str(e))
        return None


def _goto(project_id: str, page: str = "概况") -> None:
    st.session_state["_goto_project"] = project_id
    st.session_state["_goto_ppage"] = page


def _show_problems(e: P.ProjectError) -> None:
    st.error(str(e))
    for p in e.problems:
        if p != str(e):
            st.markdown(f"- {p}")


# ---------------------------------------------------------------------------
# Form (shared by "new" and "edit")
# ---------------------------------------------------------------------------

def _project_form(prefix: str, spec: P.ProjectSpec) -> P.ProjectSpec:
    """Plain-text form. Returns the (unchecked) spec the user typed."""
    st.markdown("##### 研究对象")
    name = st.text_input("公司或主题名称 *", value=spec.name, max_chars=P.MAX_NAME, key=f"{prefix}-name",
                         placeholder="例如：Tenstorrent")
    aliases = st.text_input("别名（可选，用逗号分隔）", value="，".join(spec.aliases), key=f"{prefix}-aliases",
                            help="变化内容或页面标题中出现名称或任一别名，才认为与该对象有关。别名太短容易误命中。")
    question = st.text_area("技术领域或核心研究问题 *", value=spec.question, max_chars=P.MAX_QUESTION,
                            key=f"{prefix}-question", height=80,
                            placeholder="例如：Blackhole 芯片的大模型推理软件成熟度")
    judgement = st.text_area("当前研究判断 *", value=spec.judgement, max_chars=P.MAX_JUDGEMENT,
                             key=f"{prefix}-judgement", height=110,
                             help="你现在的结论。系统不会自动修改它；只有你在这里保存，才会生成新版本。")

    st.markdown("##### 关键假设")
    st.caption("每条假设可以写几个「支持信号」和「削弱信号」：变化内容里新增了这些词句，就按对应方向提示。" + MATCH_NOTE)
    n_default = max(1, len(spec.assumptions))
    n = st.number_input("假设数量", min_value=1, max_value=P.MAX_ASSUMPTIONS, value=n_default, step=1,
                        key=f"{prefix}-n")
    rows = []
    for i in range(int(n)):
        a = spec.assumptions[i] if i < len(spec.assumptions) else P.AssumptionInput()
        with st.container(border=True):
            statement = st.text_input(f"假设 {i + 1}", value=a.statement, max_chars=P.MAX_STATEMENT,
                                      key=f"{prefix}-a{i}", placeholder="例如：尚无 70B 级模型在 Blackhole 上的官方运行记录")
            c1, c2 = st.columns(2)
            sup = c1.text_input("支持信号（可选，逗号分隔）", value="，".join(a.support_signals), key=f"{prefix}-a{i}-s")
            weak = c2.text_input("削弱信号（可选，逗号分隔）", value="，".join(a.weaken_signals), key=f"{prefix}-a{i}-w",
                                 placeholder="例如：70B，Llama 3.1 70B")
        rows.append({"statement": statement, "support": sup, "weaken": weak})

    st.markdown("##### 要监测的公开网址")
    urls = st.text_area("每行一个网址，可在网址后空一格写说明 *", height=100, key=f"{prefix}-urls",
                        value="\n".join(f"{s.url} {s.label}".strip() for s in spec.sources),
                        placeholder="https://github.com/tenstorrent/tt-metal 官方代码仓库")
    known = {s.url: s.source_type for s in spec.sources}
    types = {}
    lines = [ln.strip().partition(" ")[0] for ln in (urls or "").splitlines() if ln.strip()]
    if lines:
        st.caption("来源类型决定证据等级：公司官方 = A 级，监管或媒体 = B 级，第三方分析 = C 级，论坛或未知 = D 级。只有 A/B 级证据可以单独定向。")
    for i, url in enumerate(lines[: P.MAX_SOURCES + 2]):
        options = [t.value for t in P.SOURCE_TYPE_CHOICES]
        default = known.get(url, P.SourceType.OFFICIAL).value
        types[url] = st.selectbox(f"来源类型：{url[:80]}", options, index=options.index(default),
                                  format_func=lambda v: L.zh(L.SOURCE_TYPE, v), key=f"{prefix}-type-{i}")
    keywords = st.text_input("关注的主题词（可选，逗号分隔）", value="，".join(spec.keywords), key=f"{prefix}-kw",
                             help="变化提到了研究对象、但没有命中任何信号词时，若包含主题词，会被标为「待人工判断」，否则视为无关。")
    owner = st.text_input("负责人（可选）", value=spec.owner, max_chars=40, key=f"{prefix}-owner")
    return P.spec_from_form(name=name, aliases=aliases, question=question, judgement=judgement, assumptions=rows,
                            urls=urls, source_types=types, keywords=keywords, owner=owner)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _new_project(store: P.ProjectStore) -> None:
    st.markdown("## 新建研究项目")
    st.caption("只需填写文字，不需要编写 JSON、代码或正则表达式。可以先保存草稿，之后再补全。")
    spec = _project_form("new", P.ProjectSpec())
    c1, c2, _ = st.columns([1.2, 1.4, 4])
    draft = c1.button("保存草稿", key="save_draft")
    create = c2.button("创建研究项目", type="primary", key="create_project")
    if draft or create:
        try:
            p = store.create(spec, ready=create)
        except P.ProjectError as e:
            _show_problems(e)
            return
        for k in [k for k in st.session_state if str(k).startswith("new-")]:
            del st.session_state[k]
        _goto(p.project_id)
        st.rerun()


def _status_tag(p: P.Project) -> str:
    return "草稿" if p.status == "draft" else "已创建"


def _overview(store: P.ProjectStore, p: P.Project) -> None:
    s = p.spec
    if p.status == "draft":
        try:
            P.check_spec(s, ready=True)
        except P.ProjectError as e:
            st.warning("这是草稿，还不能开始监测。还需要：" + "；".join(e.problems))
    with st.container(border=True):
        st.markdown(f"**研究问题**：{s.question or '（未填写）'}")
        st.markdown(f"**当前研究判断**：{s.judgement or '（未填写）'}")
        st.caption(f"版本 v{p.version}　·　最后保存：{p.updated_at.replace('T', ' ').rstrip('Z')} UTC　·　负责人：{s.owner}"
                   "　·　系统不会自动修改研究判断")
    st.markdown("#### 关键假设")
    for i, a in enumerate(s.assumptions, 1):
        sig = []
        if a.support_signals:
            sig.append("支持信号：" + "、".join(a.support_signals))
        if a.weaken_signals:
            sig.append("削弱信号：" + "、".join(a.weaken_signals))
        st.markdown(f"- **A{i}** {a.statement}")
        st.caption("　　" + ("；".join(sig) if sig else "未设置信号词：相关变化只会标为待人工判断"))
    if not s.assumptions:
        st.caption("尚未填写。")
    st.markdown("#### 监测来源")
    for src in s.sources:
        grade = P.GRADE_FOR_TYPE[src.source_type].value
        st.markdown(f"- {src.label or src.url}　<span class='ts-tag'>{L.zh(L.SOURCE_TYPE, src.source_type)}｜{grade} 级</span>",
                    unsafe_allow_html=True)
        st.markdown(f'<div class="ts-line">{src.url}</div>', unsafe_allow_html=True)
    if not s.sources:
        st.caption("尚未填写。")
    if s.keywords:
        st.caption("主题词：" + "、".join(s.keywords))
    st.caption(MATCH_NOTE)
    st.markdown("#### 监测结果")
    _results_summary(store, p)


def _results_summary(store: P.ProjectStore, p: P.Project) -> None:
    st.info("尚无监测变化。需要连接你自己运行的 changedetection.io 并建立监测后，才会有结果；"
            "这里不会显示任何示例或伪造的数据。")


def _monitor(store: P.ProjectStore, p: P.Project) -> None:
    st.info("尚无监测变化。")


def _edit(store: P.ProjectStore, p: P.Project) -> None:
    st.caption("保存后会生成新版本，旧版本保留在「修改记录」中。如果项目在别的窗口已被修改，本次保存会被拒绝，不会覆盖。")
    ss = st.session_state
    base_key = f"editbase-{p.project_id}"
    # The form stays on the version the user started editing, even if another window saves meanwhile;
    # the save then names that version and is refused instead of silently overwriting.
    base = ss.get(base_key)
    if not isinstance(base, int) or base > p.version:
        base = ss[base_key] = p.version
    rec = store.version(p.project_id, base) if base != p.version else None
    if rec is not None:
        st.warning(f"你正在编辑 v{base}，但项目已在别处更新到 v{p.version}。保存会被拒绝；"
                   "可以先复制你的修改，再载入最新版本。")
        st.button("放弃本次修改，载入最新版本", key="reload_latest", on_click=ss.pop, args=(base_key, None))
    spec_base, status = (rec.spec, rec.status) if rec is not None else (p.spec, p.status)
    prefix = f"edit-{p.project_id}-v{base}"
    spec = _project_form(prefix, spec_base)
    note = st.text_input("修改说明（可选）", key=f"{prefix}-note", max_chars=120)
    buttons = st.columns([1.2, 1.4, 4])
    if status == "draft":
        save = buttons[0].button("保存草稿", key="save_edit_draft")
        finish = buttons[1].button("完成创建", type="primary", key="finish_project")
        ready = finish
        clicked = save or finish
    else:
        clicked = buttons[0].button("保存修改", type="primary", key="save_edit")
        ready = True
    if clicked:
        try:
            new = store.update(p.project_id, spec, expected_version=base, ready=ready, note=note)
        except P.ProjectConflict as e:
            st.error(str(e))
            return
        except P.ProjectError as e:
            _show_problems(e)
            return
        ss.pop(base_key, None)
        if new.version == base:
            st.info("内容没有变化，未生成新版本。")
            return
        _goto(p.project_id, "概况")
        st.rerun()


def _history(store: P.ProjectStore, p: P.Project) -> None:
    vs = store.versions(p.project_id)
    st.caption("每次保存都会留下一个版本，旧版本不会被改写。监测运行会记录它使用的是哪个版本。")
    for v in reversed(vs):
        with st.container(border=True):
            st.markdown(f"**v{v.version}**　{v.saved_at.replace('T', ' ').rstrip('Z')} UTC　·　"
                        f"{'草稿' if v.status == 'draft' else '已创建'}　·　{v.note or '保存'}")
            older = next((x for x in vs if x.version == v.version - 1), None)
            if older:
                changed = _changed_fields(older.spec, v.spec)
                st.caption("相对上一版本修改了：" + ("、".join(changed) if changed else "无内容变化"))
            with st.expander("查看该版本的研究判断与假设"):
                st.markdown(f"**研究判断**：{v.spec.judgement or '（未填写）'}")
                for i, a in enumerate(v.spec.assumptions, 1):
                    st.markdown(f"- A{i} {a.statement}")


FIELD_ZH = {"name": "研究对象", "aliases": "别名", "question": "研究问题", "judgement": "研究判断",
            "assumptions": "关键假设", "sources": "监测网址", "keywords": "主题词", "owner": "负责人"}


def _changed_fields(a: P.ProjectSpec, b: P.ProjectSpec) -> list[str]:
    return [FIELD_ZH[f] for f in FIELD_ZH if getattr(a, f) != getattr(b, f)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render() -> None:
    store = _store()
    if store is None:
        st.stop()
    ss = st.session_state
    if "_goto_project" in ss:
        ss["project"] = ss.pop("_goto_project")
        ss["ppage"] = ss.pop("_goto_ppage", "概况")
    items, problems = store.list()
    by_id = {p.project_id: p for p in items}
    if ss.get("project") not in by_id and ss.get("project") != NEW:
        ss["project"] = NEW if not items else items[0].project_id
    with st.sidebar:
        pid = st.selectbox("研究项目", [NEW] + list(by_id), key="project",
                           format_func=lambda i: "＋ 新建研究项目" if i == NEW
                           else f"{by_id[i].title}" + ("（草稿）" if by_id[i].status == "draft" else ""))
        st.caption(f"项目保存在本机：{store.root}")
        if problems:
            with st.expander(f"有 {len(problems)} 个项目无法读取"):
                for x in problems:
                    st.caption(x)
    if pid == NEW:
        _new_project(store)
        return
    try:
        p = store.get(pid)
    except P.ProjectError as e:
        st.error(str(e))
        return
    st.markdown(f"## {p.title}")
    st.markdown(f'<span class="ts-tag">{_status_tag(p)}</span><span class="ts-tag">v{p.version}</span>'
                f'<span class="ts-tag">我的研究项目（与示例案例分开保存）</span>', unsafe_allow_html=True)
    page = st.radio("项目页面", PROJECT_PAGES, horizontal=True, key="ppage", label_visibility="collapsed")
    st.divider()
    {"概况": _overview, "监测与评估": _monitor, "研究设定": _edit, "修改记录": _history}[page](store, p)
