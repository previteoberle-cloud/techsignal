"""「我的研究项目」: create, edit and follow research projects from the browser (R6).

Rendered by app.py when the sidebar workspace is 「我的研究项目」. The committed demo cases are never
read or written here; projects live under TECHSIGNAL_PROJECTS_ROOT (default ~/.techsignal/projects).
"""
from __future__ import annotations

import streamlit as st

from techsignal import project_monitor as M
from techsignal import projects as P
from techsignal.workbench import labels as L
from techsignal.workbench import viewmodel as vm

NEW = "__new__"
PROJECT_PAGES = ["概况", "监测与评估", "研判结果", "研究设定", "修改记录"]
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
    info = M.summary(store, p.project_id)
    last = info["last_check"]
    if not info["runs"]:
        st.info("尚无监测变化。" + ("需要连接你自己运行的 changedetection.io 并建立监测后，才会有结果；"
                                 if not info["state"].watches else "")
                + "这里不会显示任何示例或伪造的数据。")
        if last:
            st.caption(f"最近一次检查（{_t(last.finished_at)}）：{last.message}")
        return
    ref = info["runs"][-1]
    run = _load_project_run(store, p, ref.run_id)
    if run is None:
        return
    st.markdown(f"**{run.headline()}**")
    st.caption(f"最近一次产生研判：{_t(ref.created_at)}（基于研究设定 v{ref.thesis_version}）"
               + (f"；之后又检查过，最近一次：{last.message}" if last and last.run_id != ref.run_id else ""))
    st.button("查看研判结果", key="goto_results", on_click=_goto, args=(p.project_id, "研判结果"))


def _t(iso: str) -> str:
    return iso.replace("T", " ").rstrip("Z") + " UTC"


OUTCOME_ZH = {"assessed": "发现实际变化，已研判", "no_new_snapshot": "没有新的快照", "no_net_change": "没有实质差异",
              "failed": "失败"}
ACTION_ZH = {"already_linked": "已关联", "link_existing": "关联已有监测项（不重复创建）", "create": "新建监测项"}


def _monitor(store: P.ProjectStore, p: P.Project) -> None:
    if p.status != "ready":
        st.warning("草稿项目不能建立监测。请先在「研究设定」中补全并完成创建。")
        return
    ss = st.session_state
    state = M.load_state(store, p.project_id)
    st.caption("监测依赖你自己运行的 changedetection.io（外部服务）。本页只在你点击按钮时访问它；"
               "不会在后台定时运行，也不会发送通知。API key 只保存在本次浏览器会话中，不写入任何文件。")
    c1, c2 = st.columns([2, 1.3])
    url = c1.text_input("changedetection.io 地址", value=state.cdio_url or "http://127.0.0.1:5000", key="mon_url")
    key = c2.text_input("API key", type="password", key="mon_key")
    conn_key = f"conn-{p.project_id}"
    if st.button("连接并读取监测项", key="mon_connect"):
        try:
            client = M.client_for(url, key)
            ss[conn_key] = {"url": client.base_url, **M.connect(client)}
        except P.ProjectError as e:
            ss.pop(conn_key, None)
            st.error(str(e))
    conn = ss.get(conn_key)
    if conn and conn["url"] != url.strip().rstrip("/"):
        conn = None
    if not conn:
        st.info("当前未连接 changedetection.io。没有连接时无法建立监测或检查变化；已有的研判结果仍可在「研判结果」中查看。")
        _linked(state, None)
        _check_history(store, p)
        return
    st.success(f"已连接：changedetection.io {conn['version'] or '版本未知'}，共有 {len(conn['watches'])} 个监测项。")
    with st.expander(f"查看该服务中已有的监测项（{len(conn['watches'])} 个）"):
        mine = {w.watch_uuid for w in state.watches}
        for uuid, w in conn["watches"].items():
            st.markdown(f'<div class="ts-line">{"【本项目】" if uuid in mine else ""}{w["title"] or "（无标题）"}　{w["url"]}</div>',
                        unsafe_allow_html=True)
    plan = M.plan_watches(p, state, conn["watches"])
    todo = [x for x in plan if x.action != "already_linked"]
    st.markdown("#### 监测项")
    for x in plan:
        st.markdown(f"- {ACTION_ZH[x.action]}：{x.url}")
    if todo:
        ok = st.checkbox(f"我确认：在 {conn['url']} 中新建 {sum(x.action == 'create' for x in todo)} 个监测项，"
                         f"并关联 {sum(x.action == 'link_existing' for x in todo)} 个已有监测项", key="mon_confirm")
        if st.button("建立监测", key="mon_apply", disabled=not ok, type="primary"):
            try:
                M.apply_plan(store, p.project_id, M.client_for(url, key), plan, confirmed=ok)
                ss[conn_key] = {"url": conn["url"], **M.connect(M.client_for(url, key))}
                st.rerun()
            except P.ProjectError as e:
                st.error(str(e))
    _linked(state, conn)
    if state.watches:
        st.markdown("#### 手动检查一次")
        st.caption("系统会请 changedetection.io 立即重新抓取本项目的监测项，等待完成后读取新变化。"
                   "没有新快照或没有实质差异时，只记录「本次没有新的变化」，不会生成研判。第一次抓取只建立基线。")
        use_model = False
        if M.model_configured():
            use_model = st.checkbox("规则无法判定的变化，交给已配置的模型服务给出建议（建议仍需通过规则校验）", key="mon_model",
                                    help="模型服务由运行工作台的人通过环境变量配置（TECHSIGNAL_LLM_*），不在页面中填写密钥。")
        else:
            st.caption("未配置模型服务：所有判断由确定性规则作出，规则无法判定的变化列为待人工判断。")
        if st.button("立即检查一次并研判", key="mon_check", type="primary"):
            with st.spinner("正在请 changedetection.io 重新抓取并等待结果（最多约 90 秒）…"):
                try:
                    rec = M.check_now(store, p.project_id, M.client_for(url, key, timeout=30), use_model=use_model)
                except P.ProjectError as e:
                    st.error(str(e))
                    rec = None
            if rec is not None:
                if rec.outcome == "assessed":
                    st.success(rec.message)
                    st.button("查看研判结果", key="mon_goto_results", on_click=_goto, args=(p.project_id, "研判结果"))
                elif rec.outcome == "failed":
                    st.error(rec.message)
                else:
                    st.info("本次没有新的变化。" + rec.message)
    _check_history(store, p)


def _linked(state, conn) -> None:
    if not state.watches:
        return
    st.markdown("#### 已关联的监测项")
    for w in state.watches:
        exists = "" if conn is None else ("" if w.watch_uuid in conn["watches"] else "（在当前服务中已找不到）")
        st.markdown(f'<div class="ts-line">{w.url}　·　监测项 {w.watch_uuid[:8]}　·　'
                    f'{"由 TechSignal 创建" if w.created_by_techsignal else "关联已有监测项"}{exists}</div>',
                    unsafe_allow_html=True)


def _check_history(store: P.ProjectStore, p: P.Project) -> None:
    cs = M.checks(store, p.project_id)
    if not cs:
        return
    st.markdown("#### 检查记录")
    for c in reversed(cs[-10:]):
        st.markdown(f"- {_t(c.finished_at)}　**{OUTCOME_ZH[c.outcome]}**　{c.message}")


def _load_project_run(store: P.ProjectStore, p: P.Project, run_id: str):
    try:
        d = M.run_dir(store, p.project_id, run_id)
        return vm.load_run(d, d / "theses.json")
    except (vm.LoadError, P.ProjectError) as e:
        st.error(f"无法读取该次研判：{e}")
        return None


def _results(store: P.ProjectStore, p: P.Project) -> None:
    refs = M.runs(store, p.project_id)
    if not refs:
        st.info("尚无监测变化。完成「监测与评估」中的检查并发现实际变化后，研判结果会出现在这里。")
        return
    by = {r.run_id: r for r in refs}
    rid = st.selectbox("研判批次", list(by)[::-1], key="prun",
                       format_func=lambda i: f"{_t(by[i].created_at)}（研究设定 v{by[i].thesis_version}）")
    run = _load_project_run(store, p, rid)
    if run is None:
        return
    ref = by[rid]
    if ref.thesis_version != p.version:
        st.caption(f"该批次使用的是研究设定 v{ref.thesis_version}，当前为 v{p.version}；以下按当时的设定展示。")
    st.markdown("".join(f'<span class="ts-tag">{t}</span>' for t in run.provenance_lines()), unsafe_allow_html=True)
    st.info(run.headline())
    rows = [a for a in run.assessments() if a["direction"] != "unrelated"]
    if not rows:
        st.caption("本批次的变化与研究判断无关。")
    for n, a in enumerate(rows):
        _judgement_card(run, a, f"pj{n}")
    unrelated = [a for a in run.assessments() if a["direction"] == "unrelated"]
    if unrelated:
        with st.expander(f"与研究判断无关的变化（{len(unrelated)} 条）"):
            for a in unrelated:
                st.caption("；".join(a.get("rationale", [])))
    st.download_button("下载中文速览（Markdown）", data=run.markdown.encode("utf-8"),
                       file_name=f"techsignal_{p.project_id}_{rid}.md", mime="text/markdown", key="pdownload")


def _judgement_card(run, a: dict, key: str) -> None:
    x = vm.explain(run, a)
    with st.container(border=True):
        st.markdown(f"**{x['assumption']}**")
        st.markdown(f"判断：**{x['state']}**　·　判断来源：**{x['source']}**　·　置信度：{x['confidence']}")
        st.markdown(f"**发生了什么**：{x['what']}")
        with st.expander("为什么这样判断"):
            for r in x["why"]:
                st.markdown(f"- {r}")
            if x["model"]:
                st.markdown(f"- 模型建议（未直接采用为结论）：{x['model']}")
            st.markdown("**通过 / 触发的检查**")
            for g in x["gates"]:
                st.markdown(f"- {g}")
        if x["unresolved"]:
            st.markdown("**仍未解决**：" + "；".join(x["unresolved"]))
        st.caption(f"建议动作：{x['action']}　·　复核状态：{x['review']}")
        for ev in x["evidence"]:
            with st.expander(f"证据 {ev['id']}｜{ev['source_name']}｜{ev['grade']}"):
                st.markdown(f'<div class="ts-line">{ev["url"]}</div>', unsafe_allow_html=True)
                for title, lines in (("新增", ev["added"]), ("删除", ev["removed"])):
                    for line in lines[:8]:
                        st.markdown(f'<div class="ts-line">{title}：{line}</div>', unsafe_allow_html=True)


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
    if store.allow_private_urls:
        st.warning("开发测试模式：已设置 TECHSIGNAL_ALLOW_PRIVATE_URLS=1，允许监测本机或内网地址。正常使用时请不要开启。")
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
    {"概况": _overview, "监测与评估": _monitor, "研判结果": _results, "研究设定": _edit, "修改记录": _history}[page](store, p)
