"""Chinese labels for every internal value the brief or the workbench may show. Nothing raw reaches the screen:
unknown values fall back to a neutral Chinese label, never to the internal code."""
from __future__ import annotations

DIRECTION = {"supports": "支持原判断", "weakens": "削弱原判断", "unrelated": "与判断无关",
             "insufficient": "证据不足，待核实"}
DIRECTION_SHORT = {"supports": "支持", "weakens": "削弱", "unrelated": "无关", "insufficient": "待核实"}
GRADE = {"A": "A 级｜公司官方一手披露", "B": "B 级｜监管文件或权威媒体", "C": "C 级｜二手转述",
         "D": "D 级｜未经证实"}
SOURCE_TYPE = {"official": "公司官方", "regulator": "监管/交易所", "media": "媒体", "analyst": "第三方分析",
               "forum": "论坛/社区", "unknown": "未登记来源"}
BASIS = {"rule": "确定性规则", "model": "模型推断", "mock_model": "Mock 回放（人工预置，非真实模型）"}
CONFIDENCE = {"high": "高", "medium": "中", "low": "低"}
TRIGGER = {
    "signal_removed_or_negated": "关键信号只出现在被删除或否定的内容中",
    "conflicting_evidence": "存在方向相反的证据",
    "low_grade_source": "来源等级低，却带有方向性信息",
    "cross_batch_unresolved": "与上一批次判断相比，无法确定方向",
    "cross_batch_conflict": "与上一批次判断方向相反",
}
OUTCOME = {
    "new_decisive_evidence": "找到新的官方证据，并据此作出判断",
    "related_evidence_no_direction": "找到相关官方内容，但不足以确定方向，保持待核实",
    "no_evidence_found": "没有找到相关内容，保持待核实",
    "tools_failed": "补证工具均未成功，保持待核实",
    "no_applicable_tools": "没有可用的补证工具，保持待核实",
}
PLANNER = {"deterministic": "确定性规则", "fixed(test)": "测试用固定计划"}
STOP = {"plan_completed": "计划内的查询已全部执行", "decisive_evidence_found": "已找到可定向的证据，提前结束",
        "item_budget_exhausted": "达到单项调用上限（3 次）", "run_budget_exhausted": "达到本次运行的调用上限",
        "no_applicable_tools": "没有可用的工具"}
TOOL = {"cdio_snapshot_search": "检索 changedetection.io 保存的页面快照",
        "github_file_search": "查阅 GitHub 官方仓库中的相关文件",
        "github_commit_info": "读取 GitHub 官方提交说明"}
CALL_STATUS = {"ok": "成功", "not_found": "未找到", "error": "失败", "timeout": "超时", "blocked": "已被安全策略拦截",
               "unavailable": "工具不可用", "skipped_duplicate": "重复查询，已跳过"}
CALL_OK = {"ok"}
TRANSITION = {"first": "首次出现证据", "reinforced": "得到进一步支持", "conflict": "出现相反证据",
              "unresolved": "无法定向，保留原判断待核实", "changed": "状态变化"}
INPUT_ORIGIN = {"live_cdio_api": "在线实测：当时通过 REST API 从真实 changedetection.io 拉取",
                "replay_of_captured_live": "离线回放：用已保存的在线实测数据重新运行研判（数据指纹已核对）",
                "file_unverified": "离线文件：来源未经核验"}
DATA_NATURE = {"public": "真实公开资料", "fictional": "虚构测试数据", "mixed": "部分虚构、部分公开"}
MODEL_LAYER = {"none": "未使用任何模型：全部由确定性规则判断", "mock_replay": "Mock 回放（人工预置，非真实模型）",
               "real_model": "真实模型"}
FILTER_REASON = {"no_net_difference": "前后无净差异", "watch_error": "监测报错", "noise_only": "仅噪声变化",
                 "duplicate": "重复信息"}


def zh(table: dict, value, default: str = "未知") -> str:
    if value is None:
        return default
    return table.get(getattr(value, "value", value), default)
