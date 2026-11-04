# TechSignal 研究速览｜2026-11-04 至 2026-11-04

> 本速览为 TechSignal R1 研究原型的输出，不构成投资建议。
> 输入为 changedetection.io GET /api/v1/changes?format=json 的离线数据；R1 未接入在线服务。
> 结论状态不会因新信息自动改变；「削弱」仅触发人工复核建议。

## 概览

| 变化事件 | 已过滤 | 评估条目 | 支持 | 削弱 | 证据不足 | 无关 |
|---|---|---|---|---|---|---|
| 1 | 0 | 1 | 0 | 1 | 0 | 0 |

## 需要关注的变化

### [P1] 削弱历史判断｜Tenstorrent Blackhole 大模型推理软件成熟度｜Tenstorrent tt-metal README（官方 GitHub）

`AS-41f0114727`

**新增信息**
- 新增：| Llama 3.1 70B (TP=4) | 32 | QuietBox (Blackhole) | 195* | 14.9* | | 476.5* | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]
- 新增：| Qwen 3 32B (TP=8) | 32 | QuietBox (Wormhole) | 109 | 22.1 | 30 | 707.2 | v0.59.0-rc52 | f028da1 | [EV-7446cadb69]
- 新增：| QwQ 32B (TP=8) | 32 | QuietBox (Wormhole) | 133 | 25.2 | 30 | 806.4 | v0.56.0-rc51 | e2e0002 | [EV-7446cadb69]
- 新增：| DeepSeek R1 Distill Llama 3.3 70B (TP=8) | 32 | QuietBox (Wormhole) | 159 | 15.9 | 20 | 508.8 | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]
- 新增：| Llama 3.1 70B (TP=8) | 32 | QuietBox (Wormhole) | 159 | 15.9 | 20 | 508.8 | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]
- 删除：| Qwen 3 32B (TP=8) | 32 | QuietBox | 109 | 22.1 | 30 | 707.2 | v0.59.0-rc52 | f028da1 | [EV-7446cadb69]
- 删除：| QwQ 32B (TP=8) | 32 | QuietBox | 133 | 25.2 | 30 | 806.4 | v0.56.0-rc51 | e2e0002 | [EV-7446cadb69]
- 删除：| DeepSeek R1 Distill Llama 3.3 70B (TP=8) | 32 | QuietBox | 159 | 15.9 | 20 | 508.8 | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]
- 删除：| Llama 3.1 70B (TP=8) | 32 | QuietBox | 159 | 15.9 | 20 | 508.8 | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]
- 删除：| Qwen 2.5 72B (TP=8) | 32 | QuietBox | 319 | 14.6 | 20 | 467.2 | v0.59.0-rc52 | f028da1 | [EV-7446cadb69]
- 另有 11 行变化未在速览中展开，见证据台账 [EV-7446cadb69]

**来源**
- Tenstorrent 官方 GitHub 仓库（raw）（公司官方）https://raw.githubusercontent.com/tenstorrent/tt-metal/62f3cd0a4ff2e82d01af500eb5800cfcb6703d46/README.md；changedetection.io 抓取于 2026-11-04 06:10 UTC，对比其上一次抓取 2026-11-04 06:10 UTC（抓取时间不等于发布时间）；追溯：changedetection.io /api/v1/watch/99a85885-68fe-485e-b34e-f773d5453407/difference/1793772615/1793772617 [EV-7446cadb69]

**历史结论**
- Tenstorrent Blackhole 大模型推理软件成熟度：截至 2025-07-11，Tenstorrent 官方 tt-metal 模型表中，Blackhole 平台仅有 Llama 3.1 8B（p100/p150）的数据，且均标注为初步（* Blackhole software optimization is under active development），没有 70B 级模型记录；据此判断 Blackhole 的大模型推理软件支持仍处早期，70B 级模型的官方部署依托 Wormhole 平台（QuietBox / Galaxy）。（截至 2025-07-11，负责人 TechSignal 演示（依据公开资料整理，AI 辅助）） [TH-TT-001]
- 结论依据 SRC-TT-1：Tenstorrent（tt-metal 官方仓库），2025-07-11T16:33:05+00:00（提交 4cd196f3ce），「| Llama 3.1 8B | 32 | p150 | 69* | 29.1* | | 931.2* | v0.59.0-rc3 | 739dcaa |」（https://github.com/tenstorrent/tt-metal/blob/4cd196f3ce6b366fbcb7dfd59c10f02b61023aa1/README.md） [TH-TT-001]
- 结论依据 SRC-TT-2：Tenstorrent（tt-metal 官方仓库），2025-07-11T16:33:05+00:00（提交 4cd196f3ce），「\* Blackhole software optimization is under active development.」（https://github.com/tenstorrent/tt-metal/blob/4cd196f3ce6b366fbcb7dfd59c10f02b61023aa1/README.md） [TH-TT-001]
- 相关假设 BH1：Blackhole 平台上尚无 70B 级大模型的官方性能记录 [TH-TT-001]

**影响方向**
- 影响方向：削弱历史判断；置信度 high；判定层：确定性规则 [EV-7446cadb69]
- 结论状态保持「active」，系统不会因新信息自动修改历史结论 [TH-TT-001]

**判断依据**
- 规则层：BH1·削弱信号「70B[^|]*\|[^|]*\|[^|]*Blackhole」出现在新增内容：| Llama 3.1 70B (TP=4) | 32 | QuietBox (Blackhole) | 195* | 14.9* | | 476.5* | v0.59.0-rc53 | f028da1 | [EV-7446cadb69]

**证据等级**
- EV-7446cadb69：A｜公司官方一手披露 [EV-7446cadb69]

**仍需核实**
- 无

**建议动作**
- 建议研究负责人复核 TH-TT-001 的假设 BH1；系统未修改结论状态

## 跨批次判断变化

- TH-TT-001·BH1：首次出现证据。之前：无历史记录 → 现在：削弱。原因：该假设首次出现相关证据；研究结论本身未被修改 [EV-7446cadb69] [TH-TT-001]

## 与研究判断无关的变化

无

## 已过滤的变化

无

## 引用说明

- `[EV-…]` 为证据编号，对应证据台账 evidence.jsonl；`[TH-…]` 为研究判断编号。
- 事实陈述必须引用证据；判断陈述必须引用证据，否则须标注为推测。CLI 仅在引用完整性校验通过后写出本文件。
