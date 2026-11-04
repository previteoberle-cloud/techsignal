# TechSignal R1 验收报告：核心研判闭环

- 日期：2026-11-04
- 分支：`claude/affectionate-goldberg-u18nzv`
- 基线：上游 `master@5e086dd`
- 运行环境：云会话容器，Python 3.13.18，venv 中只装了 pydantic 2.14.2、pytest 9.2.0（另装 ruff 用于 lint）

> **定性：** R1 是**可运行的研究原型**。规则层、证据台账、简报和校验真实运行；模型层仅为 **Mock 回放**；数据为**虚构**；changedetection.io 接入为**离线契约 Fixture**。本报告不宣称已实现真实的 Agent 自主研究能力。

## 1. 闭环与实现状态

```
变化事件 → 历史研究判断匹配 → 证据记录 → 影响评估 → 中文简报 → 自动化验收
```

| 环节 | 状态 | 说明 |
|---|---|---|
| 变化事件（ChangeEvent） | **已实现，离线** | 解析 `GET /api/v1/changes?format=json`，按 `docs/api-spec.yaml#ChangesReport` 严格校验 |
| 研究判断（Thesis / Assumption） | **已实现** | JSON 结构化存储，只读，未知字段报错 |
| 证据记录（Evidence） | **已实现** | JSONL 台账，按内容哈希编号，幂等写入，记录来源、等级和 cdio 追溯路径 |
| 影响评估（Assessment） | **规则层已实现；模型层仅 Mock** | 四类方向；规则判定、模型判定、规则兜底三条路径都有标注 |
| 中文简报（Brief） | **已实现** | Markdown + JSON；每条陈述标注类型和引用 |
| 自动化验收 | **已实现** | 75 个测试，含 20 个虚构场景和端到端回放 |

## 2. 实际运行命令与结果

以下命令在本会话中实际执行，输出原样摘录。

```
$ python -m techsignal run --changes demo/changes_report.json --theses demo/theses.json \
    --sources demo/sources.json --provider mock --replay demo/mock_replay.json --out demo/output
变化事件 11，过滤 3，评估 8（支持 1 / 削弱 2 / 证据不足 3 / 无关 2）
模型层调用 1 次（Provider：mock-replay），拒绝 0，失败 0
引用完整性校验：通过；输出目录 demo/output

$ python -m pytest -v
============================== 75 passed in 0.19s ==============================

$ python -m techsignal.fixtures --check      # 退出码 0
checked tests/scenarios/S01_supports.json … checked demo/changes_report.json

$ python -m techsignal eval
{
  "data": "全部为虚构场景，指标只说明处理逻辑与预期一致，不代表真实世界准确率",
  "scenarios_total": 20, "scenarios_passed": 20, "scenarios_failed": 0,
  "direction_accuracy": 1.0, "direction_checks": 16,
  "filter_accuracy": 1.0, "filter_checks": 4,
  "assessments": 16, "rule_decision_share": 0.688, "model_layer_call_rate": 0.188,
  "briefs_passing_citation_validation": 17
}

$ ruff check techsignal --select E9,F63,F7,F82,INT   # 与上游 CI 的阻断级规则一致
All checks passed!
```

另外在全新 venv 中执行了 `pip install -e ".[test]"`，安装与测试均通过。上游 CI 中只报告不阻断（exit-zero）的 ruff 检查还剩 1 条 C901 复杂度提示（`evaluation.check_scenario` 为 13，阈值 12）。

### 测试分布

| 文件 | 数量 | 内容 |
|---|---|---|
| `test_scenarios.py` | 23 | 20 个场景，加上数量、分类覆盖、评测汇总 3 项检查 |
| `test_adapter.py` | 12 | 契约解析、上游 diff 字面量、截断标记、异常输入、与上游实时输出一致 |
| `test_rules_triage.py` | 10 | 噪声、移动行、指纹、实体不等于相关、否定、删除侧、混合方向、已退役结论 |
| `test_validate.py` | 10 | 篡改简报：无来源事实、无来源推断、错误证据号、错误结论号、Markdown 伪引用、Mock 未披露 |
| `test_providers.py` | 9 | Mock 替换与缺失；OpenAI 兼容 Provider 用假传输层测试请求和解析，以及四类失败 |
| `test_store.py` | 6 | 未知来源判为 D、父域名匹配、重复或未知字段、台账往返与幂等、损坏行 |
| `test_e2e.py` | 5 | CLI 端到端与已提交输出逐字节一致、两次运行一致、研究判断文件哈希不变、追加台账、异常输入不写文件 |

### 变异检验（确认测试会失败）

在临时副本中故意破坏实现，每次只改一处：

| 变异 | 结果 |
|---|---|
| 关闭证据等级闸门（任何等级都可判定方向） | 5 failed |
| 关闭来源冲突处理 | 4 failed |
| 校验器不检查证据是否存在 | 1 failed |

## 3. 场景清单（全部虚构）

| 编号 | 场景 | 预期 / 实际 |
|---|---|---|
| S01 | 官方披露良率提升 | 支持 · 规则 · A2 · high ✅ |
| S02 | 权威媒体报道量产推迟 | 削弱 · 规则 · A1 · medium；结论状态不变 ✅ |
| S03 | 未提及跟踪实体 | 无关 ✅ |
| S04 | 提及公司但只是招聘信息 | 无关（实体命中不等于相关）✅ |
| S05 | 只有阅读数和相对时间变化 | 过滤：噪声 ✅ |
| S06 | 只有顺序调整 | 过滤：噪声（移动行）✅ |
| S07 | C 级站点转载官方消息 | 过滤：重复 ✅ |
| S08 | 论坛先发、官方后证实 | 高等级来源不被当作重复；论坛条目为证据不足，官方条目为支持 ✅ |
| S09 | 官方说如期，媒体说延期 | 两方都降为证据不足，并标记冲突 ✅ |
| S10 | 只有论坛传闻 | 证据不足（初步方向：削弱）✅ |
| S11 | 涉及主题但无信号，且未配置模型 | 证据不足 · 规则 · 零模型调用 ✅ |
| S12 | 规则无法判定，交给 Mock | 削弱 · **mock_model** · 置信度被压到 medium ✅ |
| S13 | Mock 引用不存在的证据 | 拒绝，降为证据不足 ✅ |
| S14 | Mock 缺少条目 | 失败时安全降级为证据不足 ✅ |
| S15 | 「否认推迟量产」 | 不判为削弱，降为证据不足 ✅ |
| S16 | 变化后又改回 | 过滤：无净差异 ✅ |
| S17 | 差异被截断且监测项报错 | 保留判断，标注两条限制，置信度降到 medium ✅ |
| S18–S20 | 非法 JSON、缺字段、类型错误 | 抛出 AdapterError ✅ |

## 4. 能力边界

**A. 实际完成（真实运行）：** 契约校验适配器；确定性降噪和去重（含来源等级感知）；实体、主题和信号规则（含否定和删除侧保护）；证据台账；证据等级闸门；来源冲突不裁决；结论只读；中文 Markdown 与 JSON 简报；结构层和 Markdown 层的引用完整性校验；CLI；场景评测；用上游代码生成 Fixture。

**B. 使用 Mock 的能力：** 模型层判断。规则层判不了的情况，在测试和演示里由 `MockReplayProvider` 返回人工预置结果，输出中统一标注「Mock 回放（人工预置，非真实模型推理）」。

**C. 已编写但未对真实服务验证：** `OpenAICompatibleProvider`，只测过请求构造和响应解析。

**D. 尚未实现：** 在线 changedetection.io 拉取和 Webhook；补证工具调用和补证决策（R1 中 P2 推迟）；外部搜索；长期记忆和结论版本管理；同义改写去重；实体消歧；cdio 插件或页面；真实模型评测。

## 5. 上游核心是否保持不变

- `changedetectionio/` 下没有任何修改。验证方式：`git diff --stat origin/master -- changedetectionio/` 输出为空。
- 新增文件只有 `techsignal/**` 和 `.github/workflows/techsignal.yml`。这个 workflow 用路径过滤，只测本模块。
- 上游 `changes_report.py` 只是**按路径只读加载**，用于生成和校验 Fixture。加载时关闭了字节码写入，避免在上游目录留下 `__pycache__`。

## 6. 已知风险

- 规则可能漏判：开发中实际遇到「取消……订单」超出 12 字匹配窗口，已放宽到 20 字。手写规则需要随样本持续维护。
- 评测指标是自测性质：场景和预期都由开发者编写，100% 只说明实现与设计意图一致。
- 端到端的「金标准」输出是由同一份代码生成后人工审阅提交的，主要防回归，不是独立的标准答案。
