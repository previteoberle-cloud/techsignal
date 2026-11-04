# 真实公开案例：Tenstorrent Blackhole 是否已有 70B 级模型的官方支持

> **数据性质：** 真实公开资料。来源是 Tenstorrent 自己维护的公开 GitHub 仓库 [tenstorrent/tt-metal](https://github.com/tenstorrent/tt-metal) 的 README，按 git 提交固定版本。没有使用付费数据、内部资料或虚构内容。本文不构成投资建议。

## 研究问题

Tenstorrent 新一代 AI 加速芯片 **Blackhole** 的大模型推理软件支持成熟到什么程度？具体问题：**70B 级大模型是否已进入官方支持并公布性能数据？**

## 为什么用 GitHub 提交历史作为来源

- 本会话所在的云容器网络策略只放行 GitHub 和包仓库，公司官网与 Wayback 存档都被拒绝（403）。
- 对研究来说，git 提交历史还有两个额外的好处：
  - 每个历史版本都能用提交 SHA 精确复核；
  - 内容由公司自己发布，属于一手来源。
- 局限：这是开发者面向的技术文档，不是财报或新闻稿。它能说明「官方软件栈支持了什么」，不能直接说明商业出货或客户采用情况。

## 来源记录

| 编号 | 内容 | 发布者 | 版本与时间 | 获取方式 |
|---|---|---|---|---|
| SRC-TT-1 | 模型性能表中 Blackhole 的 3 行全部是 Llama 3.1 8B（p100、p150、2×p150），数值均带 `*`，没有 70B 级行；表格注明 *Last Update: June 23, 2025* | Tenstorrent | 提交 [`4cd196f`](https://github.com/tenstorrent/tt-metal/blob/4cd196f3ce6b366fbcb7dfd59c10f02b61023aa1/README.md)，2025-07-11 16:33 UTC | `git clone`（公开仓库）；行数用程序统计 |
| SRC-TT-2 | 脚注原文：「\* Blackhole software optimization is under active development.」 | Tenstorrent | 同上 | 同上 |
| 变化 | 新增一行：`Llama 3.1 70B (TP=4) \| 32 \| QuietBox (Blackhole) \| 195* \| 14.9* \| \| 476.5*`；同时把原有 QuietBox 行标注为「QuietBox (Wormhole)」 | Tenstorrent | 提交 [`168a956`](https://github.com/tenstorrent/tt-metal/commit/168a956a09755190e9cb46b3556bf7c1314e5ff1)「Add Llama3-70B for Blackhole to the README.md」，2025-07-11 17:16 UTC | 由真实的 changedetection.io 实例抓取 |

## 处理过程

1. `python -m techsignal.live` 从本仓库启动**真实的 changedetection.io**（临时数据目录）。
2. changedetection.io 先抓取 `4cd196f` 版本（按提交固定的 raw URL），再把同一个监测项指向 `168a956` 版本并重新检查。快照和差异都由 changedetection.io 自己生成。
3. TechSignal 用 `ChangeDetectionClient` 调用 `GET /api/v1/changes?format=json` 在线拉取差异，然后依次进入研判流程（证据台账 → 规则研判 → 中文速览 → 引用校验）。
4. 输出见 [`output/`](output/)：
   - `brief.md`：中文速览；
   - `evidence.jsonl`：证据台账；
   - `decision_log.json`：决策记录；
   - `source_changes_report.json`：在线拉取的原始响应，供离线复核；
   - `run_record.json`：拉取参数、版本和响应哈希，不含任何密钥；
   - `observation_record.json`：每个版本的提交号、发布时间和抓取时间。

说明：changedetection.io 记录的是**抓取时间**（2026-11-04），不是发布时间。发布时间以 git 提交时间为准，记录在 `observation_record.json` 和本页。

## 结论分级

| 类别 | 内容 | 依据 |
|---|---|---|
| **事实** | 2025-07-11 之前的官方模型表中，Blackhole 只有 Llama 3.1 8B 的数据，且均标注 `*`（软件优化进行中） | SRC-TT-1、SRC-TT-2，提交 `4cd196f` |
| **事实** | 提交 `168a956` 新增「Llama 3.1 70B (TP=4)，QuietBox (Blackhole)」一行：ttft 195\* ms，14.9\* t/s/u，476.5\* t/s | 证据 EV-43c4e1797e（`output/evidence.jsonl`），A 级（公司官方） |
| **事实** | 同一表中，Wormhole 平台的 QuietBox 跑同一模型（TP=8）为 15.9 t/s/u、508.8 t/s | 同一提交 |
| **系统研判（规则层）** | 假设 BH1「Blackhole 平台上尚无 70B 级大模型的官方性能记录」被**削弱**；系统建议复核 TH-TT-001，没有自动修改结论 | `output/decision_log.json`，判定层为确定性规则 |
| **推测（未经证实）** | 70B 模型在 Blackhole 上用 4 路张量并行（TP=4）就跑出了接近 Wormhole 用 8 路（TP=8）的单用户吞吐，**可能**意味着 Blackhole 单芯片能力更强 | 仅从同表数字推出；两行的配置、版本和测量条件未必可比 |
| **待核实** | `*` 表示数据仍是初步的。这条 70B 记录是否稳定、是否已能对外部署，需要等待后续版本去掉 `*`，或看到正式发布说明 | SRC-TT-2；后续版本尚未纳入本案例 |
| **待核实** | 软件栈支持不等于商业出货或客户采用；需要另找新闻稿、客户案例等来源 | 本案例来源范围之外 |

## 对历史判断的影响

- **新证据出现前：**「Blackhole 大模型推理仍处早期，70B 级模型依托 Wormhole」，依据是 SRC-TT-1 和 SRC-TT-2。
- **新证据出现后：**结论中「没有 70B 级模型记录」这一具体依据不再成立，所以假设 BH1 被削弱。但新数据仍带 `*`，「仍处早期」的整体判断还没有被推翻。
- **处理方式：**TechSignal 给出「建议研究负责人复核」，研究判断状态保持 `active`，等人工决定。

## 跨批次跟踪（真实数据）

[`case_cross_batch.json`](case_cross_batch.json) 增加了第三个真实版本：[`62f3cd0`](https://github.com/tenstorrent/tt-metal/commit/62f3cd0a4ff2e82d01af500eb5800cfcb6703d46)（2025-08-12，「Add Featured Models list」）。这一版把 README 的完整模型表改成了精选列表，Blackhole 70B 条目不再出现，8B 条目和 `*` 脚注仍然保留。

| 批次 | 版本 | 系统研判 | 跨批次说明 |
|---|---|---|---|
| 1 | `4cd196f → 168a956` | BH1 被削弱（规则层，A 级） | 首次出现证据 |
| 2 | `168a956 → 62f3cd0` | 证据不足：削弱信号出现在**被删除**的内容中，规则层不判方向 | 「之前：削弱 → 现在：证据不足；保留历史判断并标记待核实」，研究结论未改 |

**分级：**
- **事实：** 批次 2 的 README 中不再出现 Blackhole 70B 条目。
- **待核实：** 这是撤回支持，还是因为精选列表只保留部分模型？需要看发布说明，或核查当时的模型目录。
- 系统没有在这一点上下结论，这正是预期行为。

输出：[`output_cross_batch/`](output_cross_batch/)。`batch1/` 和 `batch2/` 分别是两个批次处理后的状态快照，`state/` 是累积状态，`observation_record.json` 记录三个版本的提交号和抓取时间。

## 有界补证（R3，真实执行）

批次 2 出现「70B 条目只在删除侧」的疑点后，确定性策略触发了补证，在白名单内真实执行了 3 次工具调用，完整轨迹见 [`output_cross_batch/batch2/verification_trace.json`](output_cross_batch/batch2/verification_trace.json)：

| 调用 | 做了什么 | 结果 |
|---|---|---|
| TC-01 | 在 changedetection.io 保存的新版 README 快照中检索 70B 相关内容 | 只剩「DeepSeek R1 Distill Llama 3.3 70B (TP=8)」「Llama 3.1 70B (TP=32)」两个标题，没有 Blackhole 70B 条目（新证据 EV-edded20f04） |
| TC-02 | 读取提交 `62f3cd0` 的官方提交说明（api.github.com） | 本会话容器的网络策略拒绝访问（403），如实记录为失败 |
| TC-03 | 沿被删除行中的官方链接 `./models/tt_transformers`，读取**同一提交**下的模型文档 | 文档写「Llama 3.1 70B \| LoudBox / QuietBox / Galaxy」，没有写明 Blackhole（新证据 EV-d69099674e） |

**结论：** 两条新证据都没有命中判定规则，无法区分「文档重排」和「撤回支持」，系统保持「尚未核实」，没有改变任何判断。

**人工解读（推测，非系统结论）：** QuietBox 同时有 Wormhole 版和 Blackhole 版，所以模型文档里的「QuietBox」不能直接当作 Blackhole 的证据。要回答这个问题，需要读取提交说明（在 CI 的开放网络中可以执行），或查阅发布说明。

## 复现

只做离线回放，不需要网络，也不需要 changedetection.io：

```bash
cd techsignal && python -m pytest tests/test_r2_real_case.py
```

在线重跑：需要装有 changedetection.io 依赖的 Python，并能访问 GitHub：

```bash
cd techsignal && python -m techsignal.live --python /path/to/python --case cases/tenstorrent_blackhole/case.json --out /tmp/case_out
```
