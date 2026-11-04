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

---

# R1.1 定向补修与回归验收（2026-11-04）

范围：只处理 Owner 复核提出的三个代码风险和 CI 失败归因，不扩大产品范围，不进入 R2。

**方法：先写反例测试，再修复。** 新增测试先在**未修改的 R1 代码**上运行（从 `git archive HEAD` 导出的副本，套上新测试）：**14 failed, 74 passed, 2 skipped**。这 2 个跳过是因为副本旁边没有上游文件。14 个失败全部是真实复现，原因逐条核对过，见下表。修复后：**90 passed**。

两处测试自身的错误，修正前不算复现：
- 一个「保留原有行为」的测试把 cdio 按新到旧排列的转载顺序弄反了。
- 一个模型异常测试在 `--append-ledger` 模式下内容被正确去重，注入的错误根本没触发。改为默认模式后，才在旧代码上真实复现。

## 风险 1（P0）：跨企业误去重 —— 已复现，已修复

| | 内容 |
|---|---|
| 反例 | S21：星衡半导体官网与澜芯光电官网发布完全相同的一句「公司宣布新产品送样与量产计划推迟至2027年」 |
| 旧代码结果 | 澜芯事件被判为「重复」并丢弃，TH-002 的「削弱」信号丢失；跨批次（上一次的台账）同样丢失 |
| 原因 | 去重键只有内容指纹 |
| 修复 | 去重键改为「内容指纹 + 研究对象（命中的 thesis 编号，排序后）」。`Evidence` 新增 `subject_key` 字段，`EvidenceLedger.find_duplicate(fp, subject_key, …)` 替换 `find_by_fingerprint`。thesis 匹配提前到去重之前执行 |
| 保留的行为 | S07（同一研究对象，C 级转载 A 级原文）仍去重；S08（论坛先发、官方后证实）高等级来源仍不被丢弃；新增跨批次转载去重测试 |
| 测试 | S21、`test_same_text_two_companies_same_run`、`…_across_runs`、`test_same_subject_repost_still_deduplicated_across_runs` |

## 风险 2（P0）：失败时破坏已有结果 —— 已复现，已修复

旧代码的问题有三：
- CLI 在运行**之前**就删除已有的 `evidence.jsonl`（默认模式）。
- `EvidenceLedger.add()` 每加一条就**立即追加写盘**，早于引用校验。
- 四个输出文件直接覆盖写入；非预期异常（如模型层抛 `RuntimeError`）不被捕获，直接崩溃。

| 注入的失败 | 旧代码结果 | 修复后 |
|---|---|---|
| 非法 JSON 输入（默认模式） | 上次的台账被删除，与 brief.md 不再对应 | 四个文件逐字节不变，退出码 2 |
| 非法 JSON 输入（追加模式） | 未损坏（适配器在写盘前失败） | 不变 |
| 引用校验失败（追加模式） | 新证据已追加进台账 | 不变 |
| 模型层非预期异常 | 未捕获并崩溃；台账已被删除 | 不变，退出码 2 |
| 写 decision_log.json 时磁盘错误 | 未捕获；brief.md 和 brief.json 已被新内容覆盖 | 不变，无临时目录残留 |
| 成功 → 失败 → 成功 | 中间一步删除台账 | 最终输出与已提交的金标准逐字节一致 |
| 库调用 `run_pipeline` 校验失败 | 台账已追加 | 台账文件不变 |

修复内容：
- `EvidenceLedger.add()` 只在内存中缓冲；`save()` 先写同目录临时文件，fsync 后 `os.replace`，原有行逐字节保留。
- `run_pipeline` 只在校验通过后保存（`persist=True` 且台账有路径时）。
- CLI 用 `persist=False`：四个文件先写入 `out/.techsignal-staging-*`，全部写好后按「台账 → decision_log → brief.json → brief.md」顺序逐个 `os.replace`，`finally` 中清理暂存目录。
- CLI 边界捕获所有异常，打印类型和信息，返回 2。

**剩余风险：** 单个文件的替换是原子的，四个文件作为一组不是。如果进程恰好在两次 `os.replace` 之间被强制终止，可能出现新旧混合；按上述顺序，最坏情况是台账比 brief 新，不会出现 brief 引用台账里不存在的证据。没有实现目录级原子切换。

## 风险 3（P1）：多假设命中只取第一个 —— 已复现，已修复

| | 内容 |
|---|---|
| 反例一 | S22：「良率突破65%，产线如期推进」同时命中 A1（如期）和 A2（良率突破） |
| 旧代码结果 | 只产生 A1 一条评估，A2 被静默丢弃 |
| 反例二 | S23：上述官方消息，加上 B 级媒体「良率低于预期」（A2 削弱） |
| 旧代码结果 | 由于 A2 没有被记录，**冲突检测失效**，媒体那条被判为确定性「削弱」（错误结论） |
| 修复 | `RuleOutcome` 新增 `assumption_ids`；同方向命中多个假设时，**每个假设单独生成一条评估**，只带自己的信号，并注明「同一变化还命中假设 …，已分别评估」。单假设评估的 ID 与 R1 相同；多假设时 ID 加上假设编号 |
| 保守策略 | 同一变化中支持与削弱并存时，仍不判方向（R1 原测试保持不变），但理由中逐条列出全部信号，不再静默 |
| 测试 | S22、S23、`test_every_hit_assumption_is_reported`、`test_conflict_on_secondary_assumption` |

**剩余风险：** 模型层（Mock 或真实 Provider）一次只返回一个 `assumption_id`。规则层判定不了、交给模型的情况下，多假设归因仍可能不完整。这需要在 R2 改为多假设输出格式。

## 变异检验（修复后）

| 撤销的修复 | 结果 |
|---|---|
| 去重不看研究对象 | 4 failed |
| 台账在校验前写盘 | 1 failed |
| CLI 不经暂存直接写 | 1 failed |
| 多假设只取第一个 | 5 failed |

## 回归结果

| 检查 | 结果 |
|---|---|
| `python -m pytest` | **90 passed**。原 75 个测试的文件与场景数据**未做任何修改**，全部通过 |
| `python -m techsignal eval` | 23/23 场景通过；规则直接判定占 0.783，模型层调用率 0.13 |
| `python -m techsignal.fixtures --check` | 通过 |
| ruff 阻断级规则 | 通过；不阻断的仍只有 1 条 C901 |
| 演示输出 | 只有 `evidence.jsonl` 变化（新增 `subject_key` 字段）；`brief.md`、`brief.json`、`decision_log.json` 逐字节不变 |
| 上游核心 | `git diff origin/master -- changedetectionio/` 为 0 行 |

测试框架有一处必要改动：`evaluation.check_scenario` 在预期中写了 `assumption_id` 时按假设匹配，因为一条变化现在可能对应多条评估。原有场景的预期都没有改。

## CI 失败归因（基于 GitHub Actions 实际日志，提交 2b45aa2）

TechSignal 自己的 workflow：push 和 PR 两次运行都成功。上游 workflow 中的失败如下：

| 失败的 job | 日志证据 | 归因 |
|---|---|---|
| App Test · 3.11 build：「THIRD-PARTY-LICENSES.md is up to date」 | 差异：`-distro`，`+httpcore2`、`+httpx2`、`+truststore`，`openai` 的许可证字符串从 `Apache Software License` 变为 `Apache-2.0` | **上游基线 / 依赖漂移。** 镜像只 COPY `requirements.txt`、`changedetectionio/`、`docs/api-spec.yaml`，不包含 `techsignal/`；`requirements.txt` 没有改动；差异都是 openai/litellm 链路上未锁定的传递依赖新版本。该失败使 3.11 的后续 job 全部被跳过 |
| App Test · 3.14 unit-tests：`test_llm_features_enabled_by_default` | `tests/llm/test_llm_features_disabled.py:99`，断言 `href="/settings/ai/mcp"` 失败 | **上游基线。** 基线提交 `5e086dd`（上游 dgtlmoon）把 `settings-ai-tabs.html` 的链接改成带 `#overview`，测试没有同步更新 |
| App Test · 3.14 basic-tests：`test_mcp_settings_page`、`test_ai_pages_settings_tab_row` | 断言 `<a href="/settings/ai/mcp" class=…>` 和 `/settings/ai/llm` 失败（782 passed / 2 failed） | **上游基线**，原因同上（`5e086dd`）。日志中大量 `browser-chrome` 域名解析错误来自不依赖浏览器的测试的日志输出，不是失败原因 |
| App Test · 3.14 upgrade-path-test | `error: pathspec '0.49.1' did not match any file(s) known to git` | **Fork / CI 环境。** 本 Fork 没有任何 tag（`git ls-remote --tags origin` 为 0 行），升级路径测试依赖上游的 tag |
| Container Build（PR 事件）· linux/arm/v7 | `Failed to fetch: https://www.piwheels.org/…/pillow-13.0.0-cp311-cp311-linux_armv7l.whl … connection closed before message completed`；其余 ARM job 随之取消 | **外部网络。** 同一提交在 push 事件下的 Container Build 运行成功 |

TechSignal 引入的问题：**未发现**。没有修改任何上游代码或 workflow，也没有为了让 CI 变绿而屏蔽或跳过任何检查。说明：上述「上游基线」的结论依据的是日志和提交内容。我没有在 Fork 的 master 上单独运行一次上游 CI 作为对照，因为 Fork 的 master 没有 CI 运行记录。

> **R2 更正（2026-11-04）：** 上文风险 2 的「剩余风险」中写道「按上述顺序，最坏情况是台账比 brief 新，不会出现 brief 引用台账里不存在的证据」。这个结论只在 `--append-ledger` 模式下成立。默认模式下，替换中途失败可能让旧的 brief.md 对着不含旧证据的新台账。R2 已补充失败注入测试，并在替换失败时回滚，详见 [R2_ACCEPTANCE.md](R2_ACCEPTANCE.md) 第六节。
