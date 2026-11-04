# TechSignal（R1 研究原型）

TechSignal 读取 [changedetection.io](https://github.com/dgtlmoon/changedetection.io) 输出的网页变化数据，判断这些变化对**既有研究判断**的影响，并生成带证据编号、可校验的中文管理层速览。

> **状态说明：** R1 是可运行的研究原型，不是具备自主研究能力的 Agent。规则层、证据台账、简报生成和引用校验是真实运行的。模型层目前只接了 **Mock 回放**（人工预置），没有调用过任何真实大模型。输入是离线 Fixture，没有接入在线 changedetection.io 服务。全部演示与测试数据都是**虚构**的。

## 与 changedetection.io 的分工

| | changedetection.io（上游，未修改） | TechSignal（本模块） |
|---|---|---|
| 回答的问题 | 这个网页变了什么 | 这对我们的研究判断意味着什么，证据够不够 |
| 输出 | 快照、diff、通知；可选的 AI 是否重要和摘要 | 支持 / 削弱 / 无关 / 证据不足、证据等级、待核实问题、建议动作 |
| 状态 | 每个监测项独立 | 研究判断库（只读）+ 证据台账（JSONL） |

上游已有的 AI 能力（`llm_intent` 过滤、AI 变化摘要、MCP Server）属于上游，不算本模块的贡献。本模块不修改 `changedetectionio/` 下的任何文件。

## 处理流程

```
ChangesReport JSON ──▶ adapters/cdio.py   按契约校验（docs/api-spec.yaml#ChangesReport）
                      triage.py           去掉无净差异、监测报错、噪声、位置移动和重复内容（确定性）
                      store.py            证据入台账：来源、等级、cdio 追溯路径，按内容哈希编号
                      rules.py            实体与主题匹配 + 关键假设信号规则（确定性）
                      assess.py           规则能判定的直接判定；不能判定的交给模型层 Provider
                                          → 校验模型输出 → 证据等级闸门 → 来源冲突处理
                      brief.py            中文速览：每条陈述标注类型与引用
                      validate.py         引用完整性校验，不通过就不写出文件
```

**分层原则：**
- 规则层只在信号明确时判定：信号出现在新增内容里、没有否定词、所有信号方向一致。否定语境、删除内容、方向混合、只命中主题词这几种情况都交给模型层。没有配置模型时，一律判为「证据不足」。
- 模型层的输出必须引用本次变化的证据编号，并指明对应的关键假设，否则会被拒绝并降级。模型判断的置信度最高只给 medium，并且自动附带「需人工确认」。
- 证据等级闸门：只有 A（官方一手）或 B（监管文件、权威媒体）来源才能单独支撑「支持」或「削弱」。C 或 D 级来源会降级为「证据不足」，但保留初步方向。
- 来源冲突：同一条假设同时出现支持和削弱时，系统不裁决，冲突各方都降为「证据不足」，并列出冲突证据。
- 研究判断库只读：「削弱」最多只触发「建议复核」，`thesis_status_change` 恒为 `none`。
- 去重键 = 规范化内容指纹 + 研究对象（命中的 thesis 编号）。两家企业的同一段文字不会互相吞掉；同一研究对象的转载照常去重；高等级来源晚到时不会被当作重复。
- 一条变化命中多个关键假设时，逐条生成评估，不只取第一个；同一变化里支持与削弱并存时，不判方向，但会列出全部信号。
- 失败不破坏已有结果：证据台账只在校验通过后写盘；CLI 先把四个输出文件写到 `out/.techsignal-staging-*`，全部成功后再逐个 `os.replace` 到位。输入错误、校验失败、模型异常、写盘错误都以退出码 2 结束，上一次的输出保持不变。

## 快速开始

需要 Python 3.11 或以上版本。运行时依赖只有 `pydantic`，测试另需 `pytest`。不需要安装 changedetection.io 的依赖。

```bash
cd techsignal
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
```

运行演示（Mock 回放）：

```bash
python -m techsignal run --changes demo/changes_report.json --theses demo/theses.json --sources demo/sources.json --provider mock --replay demo/mock_replay.json --out demo/output
```

对已生成的简报做引用校验：

```bash
python -m techsignal validate --out demo/output --theses demo/theses.json
```

跑测试：

```bash
python -m pytest
```

跑场景评测：

```bash
python -m techsignal eval
```

确认 Fixture 与上游 `changes_report.py` 的当前输出一致：

```bash
python -m techsignal.fixtures --check
```

演示输出已提交在 `demo/output/`：`brief.md`、`brief.json`、`decision_log.json`、`evidence.jsonl`。端到端测试会重新生成这些文件，并要求逐字节一致。

### 接入真实模型（R1 未验证）

`--provider openai-compatible` 会读取以下三个环境变量：`TECHSIGNAL_LLM_BASE_URL`（例如 `http://localhost:11434/v1`）、`TECHSIGNAL_LLM_MODEL` 和 `TECHSIGNAL_LLM_API_KEY`。这个 Provider 在 R1 中只用注入的假传输层测过请求构造和响应解析，**没有对任何真实模型服务运行过**。

## changedetection.io 数据契约

适配器解析的是 `GET /api/v1/changes?format=json` 的返回。契约依据：
- `changedetectionio/api/Changes.py`
- `changedetectionio/changes_report.py`：`group_changes()` 和 `watch_changes()`
- `docs/api-spec.yaml`：`components.schemas.ChangesReport`

Fixture 不是手写的，而是 `techsignal/fixtures.py` 按文件路径加载上游 `changes_report.py`（它只依赖标准库），用最小的替身 Watch 对象调用真实的 `group_changes()` 生成。快照文本是人工虚构的。在线 HTTP 拉取和 Webhook 接收尚未实现。

追溯：每条证据都记录了 `cdio_difference_path`，即 `/api/v1/watch/{uuid}/difference/{from}/{to}`，在真实部署中可以用它回查原始差异。

## 证据等级与陈述类型

| 等级 | 含义 |
|---|---|
| A | 公司官方一手披露 |
| B | 监管文件或权威媒体 |
| C | 二手转述或第三方分析 |
| D | 未经证实，包括论坛、匿名来源和未登记域名（默认值） |

| 陈述类型 | 校验规则 |
|---|---|
| fact（事实） | 必须引用台账中存在的证据 |
| inference（判断） | 必须引用证据，否则只能写成 speculation |
| speculation（推测） | 渲染时标注「推测，无证据支撑」 |
| context（历史结论） | 必须引用存在的研究判断编号 |
| question / action | 待核实问题、建议动作 |

Markdown 层还会检查：所有 `[EV-…]`、`[TH-…]` 都能追溯到证据或研究判断；「新增信息」「来源」「证据等级」下的每一行都带证据编号；使用了 Mock 时必须有披露声明。

## 测试

共 90 个测试，包括 23 个相互独立的虚构场景（`tests/scenarios/`），其中 S21–S23 和 `test_r11_*.py` 为 R1.1 新增的反例回归测试。场景覆盖：有效变化、支持、削弱、两类无关、两类噪声、两类重复、来源冲突、低等级证据、无信号、Mock 判定、模型错误引用、模型缺失、否定语境、无净差异、截断加报错，以及三类异常输入。各场景的结果见 [docs/R1_ACCEPTANCE.md](docs/R1_ACCEPTANCE.md)。

评测指标只说明代码行为与人工标注的预期一致。场景和预期都由开发者编写，**不代表真实世界的准确率**。

## 已知局限

- 信号规则是分析师手写的正则，存在漏判。开发中实际遇到过一次：「取消向澜芯光电采购 1.6T 光模块的订单」超出了原先 12 字的匹配窗口。
- 实体匹配用的是子串匹配，没有消歧（例如同名公司）。
- 噪声规则把只有数字的行也视为噪声，对价格类页面不适用。
- 去重只识别规范化后完全相同的内容，不识别同义改写。
- 「研究对象」目前以 thesis 为粒度：若一条 thesis 跟踪多家公司，这些公司之间的同文内容仍会被视为重复。实体级标准 ID 留待后续。
- 输出提交是逐文件原子的，四个文件作为一组并非原子：进程恰好在两次 `os.replace` 之间被杀时可能新旧混合。已按「台账先、brief.md 最后」排序，降低影响。
- 没有补证工具调用、没有在线集成、没有长期记忆或结论版本管理。

## 许可与署名

本模块以 Apache-2.0 发布，构建在 changedetection.io（Apache-2.0，Copyright Leigh Morresi and contributors）之上，以 API 数据契约集成，未修改其源代码。按照上游 `NOTICE`，changedetection.io 的名称和 Logo 不在授权范围内，本项目不使用其品牌。
