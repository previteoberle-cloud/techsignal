"""TechSignal 研究工作台（Streamlit）。

启动：  python -m techsignal.workbench          （等同于 streamlit run 本文件）
不需要网络、Docker 或模型密钥即可展示已保存的真实案例；只有点击「在线连接」相关按钮时才会访问网络。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from techsignal.workbench import labels as L
from techsignal.workbench import viewmodel as vm
from techsignal.workbench.replay import fetch_live, replay_case

PAGES = ["总览", "管理层速览", "研究判断", "变化时间线", "补证过程", "证据详情"]
MODES = ["历史实测演示", "离线回放", "在线连接"]

st.set_page_config(page_title="TechSignal 研究工作台", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container {max-width: 1180px; padding-top: 1.6rem;}
  h1, h2, h3 {letter-spacing: 0;}
  .ts-card {border: 1px solid rgba(128,128,128,.28); border-radius: 8px; padding: 14px 16px; margin: 6px 0 12px;}
  .ts-muted {color: rgba(128,128,128,.95); font-size: .88rem;}
  .ts-tag {display: inline-block; border: 1px solid rgba(128,128,128,.35); border-radius: 4px; padding: 1px 7px;
           margin: 0 6px 4px 0; font-size: .82rem;}
  .ts-line {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .84rem; white-space: pre-wrap;
            word-break: break-word; padding: 2px 0;}
  .ts-key {background: rgba(255,196,0,.18); border-radius: 3px;}
  div[data-testid="stMarkdownContainer"] p, div[data-testid="stMarkdownContainer"] li {overflow-wrap: anywhere;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _goto_evidence(ev_id: str) -> None:
    st.session_state["page"] = "证据详情"
    st.session_state["evidence"] = ev_id


def evidence_buttons(ids: list[str], key: str) -> None:
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return
    k = min(len(ids), 4)
    cols = st.columns([0.7] + [1.6] * k + [max(0.5, 9 - 1.6 * k)], vertical_alignment="center")
    cols[0].markdown('<span class="ts-muted">证据：</span>', unsafe_allow_html=True)
    for i, ev in enumerate(ids[:4]):
        cols[i + 1].button(ev, key=f"{key}-{ev}", on_click=_goto_evidence, args=(ev,), help="查看这条证据的来源与原文")


def cases_root() -> Path:
    return vm.default_cases_root()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

cases, problems = vm.discover_cases(cases_root())
with st.sidebar:
    st.markdown("### TechSignal 研究工作台")
    st.caption("跟踪硬科技公司公开信息的变化，判断它对既有研究结论的影响。")
    if not cases:
        st.error("没有找到可用的研究案例。")
        for p in problems:
            st.caption(p)
        st.stop()
    by_id = {c.case_id: c for c in cases}
    case = by_id[st.selectbox("研究案例", list(by_id), format_func=lambda i: by_id[i].title, key="case")]
    mode = st.radio("数据来源", MODES, key="mode",
                    help="历史实测演示：查看当时在线实测保存的结果。离线回放：用保存的数据重新运行研判流水线。"
                         "在线连接：连接你自己的 changedetection.io 服务。")
    if problems:
        with st.expander(f"有 {len(problems)} 个案例无法加载"):
            for p in problems:
                st.caption(p)

# ---------------------------------------------------------------------------
# Load runs for the chosen mode
# ---------------------------------------------------------------------------

runs: list[vm.RunView] = []
status_lines: list[str] = []
replay_note = None
try:
    if mode == "历史实测演示":
        runs = vm.load_batches(case)
        status_lines = ["当前为离线展示：内容来自已保存的在线实测记录，本页面没有连接任何在线服务。"]
    elif mode == "离线回放":
        with st.sidebar:
            verify = st.checkbox("回放时同时执行补证（需要访问 GitHub）", key="replay_verify",
                                 help="不勾选时完全离线。勾选后会真实访问白名单内的 GitHub 地址；无网络时补证会记录为失败。")
            if st.button("运行离线回放", type="primary", key="run_replay"):
                with st.spinner("正在用已保存的数据重新运行研判流水线…"):
                    st.session_state.setdefault("replays", {})[case.case_id] = replay_case(case, verify=verify)
        result = st.session_state.get("replays", {}).get(case.case_id)
        if result is None:
            st.info("点击左侧「运行离线回放」，系统会用保存的在线实测数据重新执行研判流水线，结果写入临时目录，不覆盖已保存的案例。")
            st.stop()
        runs = vm.load_batches(case, result.run_dirs)
        saved = vm.load_batches(case)
        same = all([a["direction"] for a in r.assessments()] == [a["direction"] for a in s.assessments()]
                   for r, s in zip(runs, saved, strict=True))
        replay_note = (result, same)
        status_lines = ["当前为离线回放：研判由本机重新计算，未连接 changedetection.io。"
                        + ("已执行补证查询。" if result.verify else "未执行补证查询。")]
    else:
        with st.sidebar:
            url = st.text_input("changedetection.io 地址", value="http://127.0.0.1:5000", key="cdio_url")
            key = st.text_input("API key", type="password", key="cdio_key", help="只在本次会话中使用，不会写入任何文件。")
            since = st.number_input("起始时间（Unix 秒）", min_value=0, value=0, step=3600, key="cdio_from")
            go = st.button("拉取变化并研判", type="primary", key="run_live")
        if go:
            with st.spinner("正在连接 changedetection.io…"):
                try:
                    st.session_state["live"] = fetch_live(case, url, key, int(since))
                except vm.LoadError as e:
                    st.session_state["live"] = None
                    st.error(f"无法完成在线拉取：{e}")
        live = st.session_state.get("live")
        if not live:
            st.warning("当前未连接任何在线服务。填写左侧地址与 API key 后点击「拉取变化并研判」；"
                       "失败时页面会保持离线状态，不会显示伪造的实时数据。")
            st.stop()
        runs = [vm.load_run(live.run_dirs[0], case.theses_path)]
        status_lines = [f"在线连接：本次结果来自 {url} 的实时拉取。"]
except vm.LoadError as e:
    st.error(f"无法加载该案例：{e}")
    st.stop()

if not runs:
    st.warning("该案例没有可展示的运行结果。")
    st.stop()

with st.sidebar:
    labels_b = [b.label for b in case.batches] if mode != "在线连接" else ["本次在线拉取"]
    idx = st.selectbox("查看批次", range(len(runs)), index=len(runs) - 1, format_func=lambda i: labels_b[i],
                       key="batch") if len(runs) > 1 else 0
    st.divider()
    for line in status_lines:
        st.caption(line)
    st.caption("模型：未使用。当前版本的所有判断都由确定性规则作出，不是 AI 自主推理。")

run = runs[idx]

# ---------------------------------------------------------------------------
# Header and navigation
# ---------------------------------------------------------------------------

st.markdown(f"## {case.title}")
st.markdown("".join(f'<span class="ts-tag">{t}</span>' for t in run.provenance_lines()), unsafe_allow_html=True)
if replay_note:
    result, same = replay_note
    (st.success if same else st.warning)(
        "离线回放完成：" + ("各批次判断与已保存的实测结果一致。" if same else "回放判断与已保存结果不一致，请查看运行日志。"))
    with st.expander("回放运行日志"):
        st.caption(f"临时目录：{result.workdir}")
        st.code(result.log or "（无输出）", language=None)

page = st.radio("页面", PAGES, horizontal=True, key="page", label_visibility="collapsed")
st.divider()

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_overview():
    o = run.overview()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("研究对象", "、".join(o["companies"]) or "—", help="；".join(o["subjects"]))
    c2.metric("本批次变化", f"{o['changes']} 条", help=f"其中 {o['filtered']} 条被判定为噪声或重复")
    states = sorted({j["state_short"] for j in o["judgements"]})
    c3.metric("当前判断", "、".join(states) or "无相关变化")
    c4.metric("待核实事项", f"{len(o['pending'])} 项")
    st.info(o["headline"])
    st.markdown("#### 当前判断")
    if not o["judgements"]:
        st.caption("本批次没有与研究判断相关的变化。")
    for n, j in enumerate(o["judgements"]):
        with st.container(border=True):
            st.markdown(f"**{j['assumption']}**")
            st.markdown(f"判断：**{j['state']}**"
                        + (f"（初步方向：{j['tentative']}）" if j["tentative"] else "")
                        + f"　·　依据：{j['basis']}　·　置信度：{j['confidence']}"
                        + ("　·　含补证取得的证据" if j["from_verification"] else ""))
            st.caption(f"建议动作：{j['action']}")
            evidence_buttons(j["evidence_ids"], key=f"ov{n}")
    st.markdown("#### 待核实事项")
    if o["pending"]:
        for q in o["pending"]:
            st.markdown(f"- {q}")
    else:
        st.caption("暂无。")
    if len(runs) > 1:
        st.markdown("#### 批次概况")
        cols = st.columns(len(runs))
        for i, (b, r) in enumerate(zip(case.batches, runs, strict=True)):
            js = [x["state_short"] for x in r.overview()["judgements"]]
            cols[i].markdown(f"**批次 {b.number}**　{'、'.join(js) or '无相关变化'}")
            cols[i].caption(b.label.split("：", 1)[-1])


def page_brief():
    o = run.overview()
    st.markdown(f"### {o['headline']}")
    for n, item in enumerate(run.brief.items):
        with st.container(border=True):
            st.markdown(f"**{L.zh(L.DIRECTION, item.direction)}｜{item.headline.split('｜')[1]}**")
            for s in item.actions:
                st.markdown(f"建议动作：{s.text}")
            qs = [s.text for s in item.open_questions if s.text != "无"]
            if qs:
                st.markdown("仍需核实：")
                for q in qs:
                    st.markdown(f"- {q}")
            v = next((x for x in item.verification if x.text.startswith("补证结论")), None)
            if v:
                st.markdown(v.text)
            evidence_buttons([e for s in item.evidence_grade + item.verification for e in s.evidence_ids], key=f"br{n}")
    if not run.brief.items:
        st.caption("本批次没有需要关注的变化。")
    st.download_button("下载中文速览（Markdown）", data=run.markdown.encode("utf-8"),
                       file_name=f"techsignal_{case.case_id}_batch{idx + 1}.md", mime="text/markdown",
                       key="download_brief")
    with st.expander("技术详情：完整中文速览（含判断规则与全部引用）"):
        st.markdown(run.markdown)


def page_thesis():
    for th in run.theses:
        with st.container(border=True):
            st.markdown(f"#### {th.title}")
            st.markdown(th.conclusion)
            st.caption(f"截至 {th.as_of}　·　负责人：{th.owner}　·　状态：有效（系统不会自动修改研究结论）")
        st.markdown("**关键假设与本批次状态**")
        rows = {a.get("assumption_id"): a for a in run.assessments() if a.get("thesis_id") == th.thesis_id}
        for a in th.assumptions:
            cur = rows.get(a.assumption_id)
            state = L.zh(L.DIRECTION, cur["direction"]) if cur else "本批次未涉及"
            st.markdown(f"- **{a.assumption_id}** {a.statement}　→　{state}")
        if th.sources:
            st.markdown("**结论依据的公开来源**")
            for src in th.sources:
                with st.container(border=True):
                    st.markdown(f"「{vm.clean(src.quote, 300)}」")
                    st.caption(f"{src.publisher}　·　发布：{src.published_at}　·　获取方式：{src.retrieved_via}")
                    st.link_button("打开原始来源", src.url)
    st.markdown("**证据等级说明**")
    for k in ("A", "B", "C", "D"):
        st.caption(L.GRADE[k])


def page_timeline():
    st.caption("「发布时间」来自 git 提交记录，是公司公开这段内容的时间；「抓取时间」是 changedetection.io 实际读取页面的时间。"
               "本案例的抓取是在发布之后对历史版本的回放观测。")
    for i, t in enumerate(vm.timeline(case, runs)):
        with st.container(border=True):
            mark = "　（当前查看）" if i == idx else ""
            st.markdown(f"#### {t['label']}{mark}")
            a, b = st.columns(2)
            a.markdown(f"**发布时间**：{t['to_published']}")
            a.caption(f"对比版本发布：{t['from_published']}")
            b.markdown(f"**系统抓取时间**：{t['to_fetched']}")
            b.caption(f"对比版本抓取：{t['from_fetched']}")
            if t["to_url"]:
                st.link_button(f"查看提交 {t['commit']}", t["to_url"])
            st.markdown("**变化内容（关键行优先）**")
            for c in t["changes"][:6]:
                cls = "ts-line ts-key" if c["key"] else "ts-line"
                st.markdown(f'<div class="{cls}">{c["side"]}：{c["text"]}</div>', unsafe_allow_html=True)
            st.markdown("**影响**")
            for j in t["judgements"]:
                st.markdown(f"- {j['assumption']}：**{j['state']}**")
            for tr in t["transitions"]:
                st.markdown(f"- 与之前相比：{tr['kind']}（之前：{tr['before']} → 现在：{tr['after']}）。{tr['why']}")
            if t["verified"]:
                st.caption("本批次触发了补证，详见「补证过程」。")


def page_verification():
    st.caption("补证由确定性规则触发和规划：只在判断存在具体疑点时执行，每项最多 3 次查询，只访问白名单内的公开来源。没有使用任何模型。")
    vs = run.verification()
    if not vs:
        if run.trace is None:
            st.info("本批次运行时没有开启补证。")
        else:
            st.info("本批次没有触发补证：相关判断已经明确，或变化与研究判断无关。")
        return
    full = st.toggle("展开完整补证轨迹", key="full_trace")
    for n, v in enumerate(vs):
        with st.container(border=True):
            st.markdown(f"#### 补证对象：{v['target']}")
            st.markdown("**为什么补证**")
            for t in v["triggers"]:
                st.markdown(f"- {t}")
            st.markdown(f"**结果**：{v['outcome']}")
            st.caption(f"结束原因：{v['stop']}")
            if v["changed_judgement"]:
                st.success("补证改变了判断：新证据经同一套规则复评后得出了方向。研究结论仍需人工复核。")
            else:
                st.warning("补证没有改变判断，只明确了还需要核实的内容。")
            for h in v["hints"]:
                st.caption(f"规则提示（不是结论）：{h}")
            st.markdown("**查询步骤**")
            for s in v["steps"]:
                icon = "✅" if s["ok"] else "⚠️"
                st.markdown(f"{icon} **{s['id']}　{s['tool']}**：{s['status']}")
                st.caption(s["purpose"])
                if s["error"]:
                    st.caption(f"原因：{s['error']}")
                if full:
                    if s["url"]:
                        st.markdown(f'<div class="ts-line">请求地址：{s["url"]}</div>', unsafe_allow_html=True)
                    st.caption(f"尝试次数：{s['attempts']}" + ("；含疑似提示注入的内容已排除" if s["excluded_injection"] else ""))
                    for line in s["found"][:8]:
                        st.markdown(f'<div class="ts-line">{line}</div>', unsafe_allow_html=True)
                    if s["detail"]:
                        st.caption(s["detail"])
                evidence_buttons(s["evidence"], key=f"vf{n}-{s['id']}")


def page_evidence():
    ids = list(run.evidence)
    if not ids:
        st.info("本批次没有证据记录。")
        return
    want = st.session_state.get("evidence")
    default = ids.index(want) if want in ids else 0
    if want and want not in ids:
        st.warning(f"证据 {want} 不在当前批次的证据台账中，已显示第一条证据。")

    def fmt(i):
        e = run.evidence[i]
        return f"{i}｜{e.source_name}｜{L.zh(L.GRADE, e.grade).split('｜')[0]}"
    chosen = st.selectbox("选择证据", ids, index=default, format_func=fmt, key=f"ev_select_{idx}_{want}")
    v = run.evidence_view(chosen)
    with st.container(border=True):
        st.markdown(f"#### {v['id']}")
        a, b = st.columns(2)
        a.markdown(f"**来源**：{v['source_name']}")
        a.markdown(f"**来源类型**：{v['source_type']}")
        a.markdown(f"**证据等级**：{v['grade']}")
        a.markdown(f"**获取方式**：{v['obtained']}")
        b.markdown(f"**发布时间**：{v['published']}")
        b.markdown(f"**系统抓取时间**：{v['fetched']}")
        st.link_button("打开原始公开来源", v["url"])
        st.markdown(f'<div class="ts-line">{v["url"]}</div>', unsafe_allow_html=True)
    if v["used_by"]:
        st.markdown("**用于哪些判断**")
        for u in v["used_by"]:
            st.markdown(f"- {u}")
    for title, lines in (("新增内容", v["added"]), ("删除内容", v["removed"])):
        if lines:
            st.markdown(f"**{title}**（共 {len(lines)} 行）")
            for line in lines[:12]:
                st.markdown(f'<div class="ts-line">{line}</div>', unsafe_allow_html=True)
            if len(lines) > 12:
                st.caption(f"另有 {len(lines) - 12} 行未显示。")
    if v["truncated"]:
        st.caption("注意：changedetection.io 返回的差异被截断，完整内容请打开原始来源。")


{"总览": page_overview, "管理层速览": page_brief, "研究判断": page_thesis, "变化时间线": page_timeline,
 "补证过程": page_verification, "证据详情": page_evidence}[page]()
