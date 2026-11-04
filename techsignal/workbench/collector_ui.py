"""「信息采集」: the workbench home page. Create collection tasks, collect now, read, filter, search and
export the collected items. No research judgement is needed here."""
from __future__ import annotations

from datetime import date

import streamlit as st

from techsignal import collector as C

ALL, NEW = "__all__", "__new__"
PAGES = ["信息流", "采集状态", "任务设置"]
KIND_ZH = {"auto": "自动识别", "feed": "RSS/Atom 订阅源", "page": "网页"}
ITEM_KIND_ZH = {"feed_entry": "订阅条目", "page_snapshot": "网页（首次采集）", "page_change": "网页更新"}


def _t(iso: str | None) -> str:
    return (iso or "未标注").replace("T", " ").replace("Z", " UTC")


def _goto(task_id: str, page: str = "信息流") -> None:
    st.session_state["_goto_ctask"] = task_id
    st.session_state["_goto_cpage"] = page


def _problems(e: C.CollectorError) -> None:
    st.error(str(e))
    for p in e.problems:
        if p != str(e):
            st.markdown(f"- {p}")


def _task_form(prefix: str, task: C.Task | None):
    name = st.text_input("采集任务名称 *", value=task.name if task else "", key=f"{prefix}-name", max_chars=60,
                         placeholder="例如：AI 芯片行业动态")
    keywords = st.text_input("关注的企业或关键词（可选，逗号分隔）", key=f"{prefix}-kw",
                             value="，".join(task.keywords) if task else "", placeholder="例如：Tenstorrent，Blackhole，RISC-V",
                             help="用于高亮和筛选。不命中关键词的信息也会保存。英文按完整单词匹配。")
    tags = st.text_input("分类标签（可选，逗号分隔）", key=f"{prefix}-tags", value="，".join(task.tags) if task else "",
                         placeholder="例如：AI芯片，竞品")
    urls = st.text_area("公开信息来源网址 *（每行一个，可在网址后空一格写来源名称）", key=f"{prefix}-urls", height=120,
                        value="\n".join(f"{s.url} {s.name}".strip() for s in task.sources) if task else "",
                        placeholder="https://github.com/tenstorrent/tt-metal/releases.atom tt-metal 发布记录\n"
                                    "https://www.example.com/news 公司新闻页")
    known = {s.url: s.kind for s in task.sources} if task else {}
    kinds = {}
    lines = [ln.strip().partition(" ")[0] for ln in (urls or "").splitlines() if ln.strip()]
    if lines:
        st.caption("来源类型默认「自动识别」：是 RSS/Atom 订阅源就逐条采集，否则按网页处理（记录页面，之后只采集新增内容）。")
    for i, u in enumerate(lines[: C.MAX_SOURCES]):
        opts = ["auto", "feed", "page"]
        kinds[u] = st.selectbox(f"来源类型：{u[:80]}", opts, index=opts.index(known.get(u, "auto")),
                                format_func=KIND_ZH.get, key=f"{prefix}-kind-{i}")
    return name, keywords, tags, urls, kinds


def _new_task(store: C.CollectorStore) -> None:
    st.markdown("## 新建采集任务")
    st.caption("填写任务名称和公开信息来源即可，不需要写代码、JSON 或正则表达式。")
    name, kw, tags, urls, kinds = _task_form("cnew", None)
    if st.button("创建采集任务", type="primary", key="create_task"):
        try:
            t = store.create_task(name, kw, tags, urls, kinds)
        except C.CollectorError as e:
            _problems(e)
            return
        for k in [k for k in st.session_state if str(k).startswith("cnew-")]:
            del st.session_state[k]
        _goto(t.task_id)
        st.rerun()


def _cdio_options():
    """Optional: read web pages through the user's own changedetection.io. The key stays in this browser
    session's memory only."""
    ss = st.session_state
    mem = ss.setdefault("_c_cdio", {})
    with st.expander("可选：通过 changedetection.io 抓取网页来源"):
        st.caption("勾选后，「网页」类型的来源改由你自己运行的 changedetection.io 抓取（会为网址建立监测项）。"
                   "订阅源仍直接读取。API key 只保存在本次浏览器会话中。")
        use = st.checkbox("使用 changedetection.io", value=mem.get("use", False), key="c_cdio_use")
        url = st.text_input("changedetection.io 地址", value=mem.get("url", "http://127.0.0.1:5000"), key="c_cdio_url")
        key = st.text_input("API key", type="password", value=mem.get("key", ""), key="c_cdio_key")
        mem.update(use=use, url=url, key=key)
    if not use:
        return None
    from techsignal.cdio_client import ChangeDetectionClient
    try:
        return ChangeDetectionClient(url.strip(), api_key=key or None, timeout=30, retries=1)
    except ValueError as e:
        st.error(f"changedetection.io 地址无效：{e}")
        return None


def _feed(store: C.CollectorStore, tasks: list[C.Task]) -> None:
    ss = st.session_state
    ids = [t.task_id for t in tasks]
    cdio = _cdio_options()
    c1, c2 = st.columns([1.3, 4])
    if c1.button("立即采集", type="primary", key="collect_now"):
        with st.spinner("正在采集公开信息来源…"):
            msgs = []
            for t in tasks:
                try:
                    r = store.collect(t.task_id, cdio_client=cdio)
                    msgs.append((t, r))
                except C.CollectorError as e:
                    st.error(f"{t.name}：{e}")
        ss["_c_last"] = [(t.name, r.new_items, r.duplicates, r.failed_sources) for t, r in msgs]
    for name, n_new, n_dup, n_fail in ss.get("_c_last", []):
        text = f"{name}：新增 {n_new} 条，跳过重复 {n_dup} 条" + (f"，{n_fail} 个来源失败（见「采集状态」）" if n_fail else "")
        (st.warning if n_fail else st.success)(text)
    last = [s.last_success for t in tasks for s in store.status(t.task_id).values() if s.last_success]
    c2.caption("最近一次成功采集：" + (_t(max(last)) if last else "尚未采集"))

    items = [i for tid in ids for i in store.items(tid)]
    items.sort(key=lambda i: (i.published_at or i.fetched_at, i.fetched_at), reverse=True)
    if not items:
        st.info("还没有采集到信息。点击「立即采集」获取来源中的公开信息。")
        return
    sources = {s.source_id: (s.name or s.url) for t in tasks for s in t.sources}
    keywords = sorted({k for t in tasks for k in t.keywords})
    f1, f2, f3 = st.columns([2, 2, 1.4])
    q = f1.text_input("搜索标题和摘要", key="c_q", placeholder="输入关键词")
    src = f2.multiselect("来源", list(sources), format_func=lambda s: sources.get(s, s), key="c_src")
    kw = f3.selectbox("关键词", ["（全部）"] + keywords, key="c_kw")
    g1, g2, g3 = st.columns([1.4, 1.4, 2])
    since = g1.date_input("开始日期", value=None, key="c_since")
    until = g2.date_input("结束日期", value=None, key="c_until")
    hits = g3.checkbox("只看命中关键词的信息", key="c_hits")
    shown = C.filter_items(items, source_ids=src or None, query=q, only_keyword_hits=hits,
                           since=since.isoformat() if isinstance(since, date) else None,
                           until=until.isoformat() if isinstance(until, date) else None,
                           keyword=None if kw == "（全部）" else kw)
    e1, e2, e3 = st.columns([1.2, 1.4, 3])
    e1.download_button("导出 CSV", data=C.to_csv(shown), file_name="techsignal_items.csv", mime="text/csv",
                       key="export_csv")
    e2.download_button("导出 Markdown", data=C.to_markdown(shown).encode("utf-8"), file_name="techsignal_items.md",
                       mime="text/markdown", key="export_md")
    e3.caption(f"共 {len(items)} 条，筛选后 {len(shown)} 条（导出筛选后的结果）")
    for it in shown[:200]:
        with st.container(border=True):
            st.markdown(f"**[{it.title}]({it.url})**")
            meta = [it.source_name, ITEM_KIND_ZH[it.kind], f"发布：{_t(it.published_at)}", f"抓取：{_t(it.fetched_at)}"]
            if it.via == "changedetection.io":
                meta.append("经 changedetection.io")
            st.caption("　·　".join(meta))
            if it.keyword_hits:
                st.markdown("".join(f'<span class="ts-tag">{k}</span>' for k in it.keyword_hits), unsafe_allow_html=True)
            if it.summary:
                st.markdown(f'<div class="ts-line">{it.summary[:300]}</div>', unsafe_allow_html=True)
    if len(shown) > 200:
        st.caption(f"只显示前 200 条，共 {len(shown)} 条；可用筛选或导出查看全部。")


def _status(store: C.CollectorStore, tasks: list[C.Task]) -> None:
    for t in tasks:
        st.markdown(f"#### {t.name}")
        stat = store.status(t.task_id)
        counts = {}
        for i in store.items(t.task_id):
            counts[i.source_id] = counts.get(i.source_id, 0) + 1
        for s in t.sources:
            x = stat.get(s.source_id)
            with st.container(border=True):
                st.markdown(f"**{s.name or s.url}**")
                st.markdown(f'<div class="ts-line">{s.url}</div>', unsafe_allow_html=True)
                if x is None or x.ok is None:
                    st.caption("尚未采集")
                    continue
                kind = KIND_ZH.get(x.detected_kind, x.detected_kind or "—")
                state = "✅ 成功" if x.ok else f"⚠️ 失败：{x.error}"
                st.caption(f"{state}　·　类型：{kind}　·　最近尝试：{_t(x.last_attempt)}　·　最近成功：{_t(x.last_success)}"
                           f"　·　上次新增 {x.last_new} 条、重复 {x.last_duplicates} 条　·　已保存 {counts.get(s.source_id, 0)} 条")


def _settings(store: C.CollectorStore, task: C.Task) -> None:
    st.caption(f"创建于 {_t(task.created_at)}，最后修改 {_t(task.updated_at)}。修改来源不会删除已采集的信息。")
    prefix = f"cedit-{task.task_id}-{task.updated_at}"
    name, kw, tags, urls, kinds = _task_form(prefix, task)
    if st.button("保存任务设置", type="primary", key="save_task"):
        try:
            store.update_task(task.task_id, name, kw, tags, urls, kinds)
        except C.CollectorError as e:
            _problems(e)
            return
        st.rerun()


def render() -> None:
    store = C.CollectorStore()
    ss = st.session_state
    if "_goto_ctask" in ss:
        ss["ctask"] = ss.pop("_goto_ctask")
        ss["cpage"] = ss.pop("_goto_cpage", "信息流")
    tasks = store.tasks()
    by = {t.task_id: t for t in tasks}
    options = ([ALL] if tasks else []) + list(by) + [NEW]
    if ss.get("ctask") not in options:
        ss["ctask"] = options[0]
    with st.sidebar:
        cur = st.selectbox("采集任务", options, key="ctask",
                           format_func=lambda i: "全部任务" if i == ALL else "＋ 新建采集任务" if i == NEW else by[i].name)
        st.caption(f"数据保存在本机：{store.root}")
    if store.allow_private_urls:
        st.warning("开发测试模式：已设置 TECHSIGNAL_ALLOW_PRIVATE_URLS=1，允许采集本机或内网地址。正常使用时请不要开启。")
    if cur == NEW:
        _new_task(store)
        return
    shown = tasks if cur == ALL else [by[cur]]
    st.markdown("## " + ("全部采集任务" if cur == ALL else by[cur].name))
    tags = sorted({g for t in shown for g in t.tags})
    st.markdown("".join(f'<span class="ts-tag">{g}</span>' for g in tags)
                + f'<span class="ts-tag">{sum(len(t.sources) for t in shown)} 个来源</span>', unsafe_allow_html=True)
    pages = PAGES if cur != ALL else PAGES[:2]
    if ss.get("cpage") not in pages:
        ss["cpage"] = pages[0]
    page = st.radio("采集页面", pages, horizontal=True, key="cpage", label_visibility="collapsed")
    st.divider()
    if page == "信息流":
        _feed(store, shown)
    elif page == "采集状态":
        _status(store, shown)
    else:
        _settings(store, by[cur])
