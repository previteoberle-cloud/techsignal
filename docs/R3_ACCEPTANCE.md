# TechSignal R3 验收报告：有界自主补证

- 日期：2026-11-04
- 分支：`claude/affectionate-goldberg-u18nzv`，PR #1
- 提交：`f0e8b63`（P0 来源声明修复）、`bc91fd6`（补证模块），以及本报告所在提交

## 一句话结论

TechSignal 现在可以对存在具体疑点的研判**主动补证**：

1. 由**确定性策略**决定是否补证、调用哪些工具；
2. 在白名单内**真实执行**工具调用（changedetection.io 快照查询、GitHub 官方文件查询、GitHub 提交元数据查询）；
3. 新取得的内容以**独立编号的证据**入账，再交给**同一套规则**重新评估；
4. 每一次调用都写入执行轨迹。

在 Tenstorrent 真实案例中，补证取得了 2 条新的官方证据，但**没有改变判断**，只把剩余疑问说得更具体了。**整个过程没有任何大模型参与。**

## 一、P0：展示与溯源修复

- **问题：** R2 的三份案例输出都是在线调用真实 changedetection.io 生成的，声明里却写着「R1 研究原型」「离线数据；R1 未接入在线服务」。原因是 `brief.py` 把这两句话写死了。
- **修复：** 新增 `techsignal/provenance.py`，声明只根据运行元数据生成，不根据目录名猜测。
  - **输入方式：** 在线 API，或文件。
  - **离线回放的认定：** 必须用 `--input-record` 提供在线运行记录，且其中的 `report_sha256` 与输入文件一致，才会标为「离线回放」；不一致就拒绝运行。
  - **数据性质：** 依据每条研究判断自带的 `fictional` 标志。
  - **模型层：** 依据实际配置的 Provider，以及实际有几条判断来自它。
  - **补证：** 说明是否启用、实际执行了几次调用。
- **区分出的类别：**
  - 输入：离线虚构 Fixture、真实公开资料的离线回放、在线调用真实 changedetection.io、未经核验的离线文件。
  - 模型层：Mock 模型、真实模型（目前没有发生过）、未配置。
- **同步更新：**
  - 两个案例**真实重新在线运行**，证据编号、抓取时间和批次编号随之变化，文档已同步；
  - R2 验收记录追加了更正说明；
  - R2 的回放测试改为带 `--input-record` 回放，并比较研判内容（回放必须标明自己是回放，所以文本不再与在线版逐字相同）。
- **测试：** `tests/test_r3_provenance.py`，8 个。

## 二、P1：补证闭环

模块位于 `techsignal/techsignal/verify/`：

| 环节 | 实现 | 文件 |
|---|---|---|
| 发现疑点 | `detect_triggers`：只在四种情况下触发——信号只出现在删除或否定语境中；存在冲突证据（同批次或跨批次）；低等级来源带有方向；跨批次「无法定向」 | `engine.py` |
| 决定补证、选择工具 | `DeterministicPlanner`：同一页面最新快照 → 引起变化的官方提交说明 → 疑点内容中的官方相对链接，在**同一仓库同一版本**中查找。没有任何针对具体案例的写死逻辑 | `engine.py` |
| 实际执行 | `CdioSnapshotSearch`（`GET /api/v1/watch/{uuid}/history/{ts}`）、`GitHubFileSearch`（raw.githubusercontent.com，按 ref 取文件）、`GitHubCommitInfo`（api.github.com 公开 REST） | `tools.py` |
| 新证据校验 | 工具输出只当作数据：只做固定的词语或正则检索；像指令的行会被标记并排除；新证据独立编号（`obtained_via=verification_tool`、`tool_call_id`），并按「内容 + 研究对象」去重 | `tools.py`、`engine.py` |
| 重新评估或降级 | 新证据走**同一套规则**（`rules.decide`）和**同一证据等级闸门**；没有命中规则就不给方向。之后重新做冲突与跨批次处理 | `engine.py`、`pipeline.py` |
| 执行轨迹 | `verification_trace.json`：每次调用的目的、参数摘要、URL、状态、HTTP 码、尝试次数、耗时、匹配到的原文、生成或复用的证据、标记、停止原因；简报新增「补证记录」一节 | `engine.py`、`brief.py` |

## 三、P2：执行控制

| 要求 | 实现 | 测试 |
|---|---|---|
| 触发条件明确 | 见上；裁定明确的 A/B 级研判、无关变化、噪声、工具取得的证据都不触发 | 3 个触发、6 个不触发、1 个无关变化零调用 |
| 调用次数上限 | 每项默认 3 次，允许配置但最多 5 次；每次运行另有总上限 | 单项上限、运行上限 |
| 超时、重试、去重、停止 | 超时可配置；只对 5xx、超时、连接错误重试；相同工具加相同参数只执行一次；停止原因包括 plan_completed、decisive_evidence_found、item_budget_exhausted、run_budget_exhausted、no_applicable_tools | 500、超时、404，重复调用，重复证据 |
| 白名单 | 主机必须完全一致，可限定端口和路径前缀；changedetection.io 只放行本次 `--cdio-url` 的 `/api/v1/` | 11 类恶意或越界 URL 被拒，合法 URL 放行 |
| 内网与重定向 | 每一跳重定向都重新检查；IP 字面量、凭据、`..`（包括编码形式）、非 http/https 协议一律拒绝 | 302 跳到 169.254.169.254 被拦截 |
| 提示注入 | 工具输出不参与控制流；像指令的行被标记、不进入证据和研判 | 注入文本没有带来额外调用、没有入账、没有改变方向 |
| 不使用收费 API | 只用公开端点；GitHub token 可选，只从策略指定的环境变量读取，不写入轨迹 | — |
| 结论只读 | 研究判断文件从不写入；`thesis_status_change` 恒为 `none` | 补证失败后研究判断文件哈希不变 |
| 失败不破坏状态 | 单个工具异常被捕获并记录；补证器整体崩溃时 CLI 退出码为 2，台账、历史和输出逐字节不变 | 历史状态保护 |
| 规划器不能绕过控制 | 即使规划器提出越界目标，引擎照样拦截 | `FixedPlanner` 恶意规划被拦截 |

## 四、P3：模型层

- 环境中没有获准使用的真实模型服务；Claude Code 会话自身的变量和凭据**没有使用**。
- 补证决策**只有确定性实现**：`DeterministicPlanner`。`Planner` 是可替换的接口；`FixedPlanner` 只用于测试，用来证明「无论规划器提出什么，引擎的控制都生效」。**没有实现 Mock 模型决策，也没有实现真实模型决策。**
- 研判中的模型层仍是 R2 的状态：Mock 回放，以及对本地协议桩的 HTTP 验证。

## 五、真实公开资料的端到端补证演示

- **命令：** `python -m techsignal.live --python <装有 cdio 依赖的 python> --case cases/tenstorrent_blackhole/case_cross_batch.json --out …`
- **执行方式：**
  - 真实 changedetection.io 依次抓取 Tenstorrent 官方 tt-metal 仓库 README 的三个提交版本：`4cd196f → 168a956 → 62f3cd0`；
  - 两个批次都启用 `--track-history --verify`。
- **批次 1：** 加入 Blackhole 70B 条目，BH1 被削弱，证据为 A 级。**不触发补证**，因为判断明确。
- **批次 2：** README 重排后，70B 条目出现在删除侧，研判为证据不足。**触发原因：** `signal_removed_or_negated`、`cross_batch_unresolved`。

本会话中的真实调用（见 `cases/tenstorrent_blackhole/output_cross_batch/batch2/verification_trace.json`）：

| 调用 | 工具 | 结果 | 新证据 |
|---|---|---|---|
| TC-01 | cdio 快照检索（本次运行的 changedetection.io，`/api/v1/watch/…/history/<ts>`） | 200。新版 README 中与 70B 相关的只有「DeepSeek R1 Distill Llama 3.3 70B (TP=8)」「Llama 3.1 70B (TP=32)」两个标题，没有 Blackhole 70B 条目 | EV-edded20f04（官方页面的快照，A 级） |
| TC-02 | GitHub 提交元数据（api.github.com） | **403**：本容器的网络策略拒绝访问。如实记录为失败，不重试，也不绕过 | 无 |
| TC-03 | GitHub 文件检索：沿被删除行中的官方链接 `./models/tt_transformers`，取**同一提交**下的 `models/tt_transformers/README.md` | 200。模型文档写「Llama 3.1 70B \| LoudBox / QuietBox / Galaxy」，没有写明 Blackhole（QuietBox 同时有 Wormhole 版和 Blackhole 版） | EV-d69099674e（A 级） |

- **结论：** `related_evidence_no_direction`。两条新证据都没有命中任何判定规则，无法区分「文档重排」和「撤回支持」，**保持尚未核实**。
- 原有研判（证据不足）、历史判断（批次 1 的削弱）和研究结论（active）都没有改变。
- 停止原因为 `plan_completed`：3 次调用，正好用完单项上限。

## 六、回答 Owner 的问题

**A. 哪些决定由规则作出，哪些由模型作出？**

本轮所有决定都由**规则或确定性代码**作出：

- 是否触发补证；
- 调用哪些工具、调用顺序、什么时候停止；
- URL 能否访问；
- 哪些行算作相关；
- 新证据能否定向（使用同一套信号规则）；
- 证据等级闸门；
- 冲突与跨批次处理。

**没有任何决定由模型作出。** 演示中没有配置模型；测试中出现的 Mock，只存在于 R1/R2 研判场景里。

**B. 实际调用了哪些外部工具，获得了哪些新证据？**

见第五节：本会话真实执行了 3 次调用，其中 2 次成功、1 次被网络策略拒绝（403），得到 2 条新的官方证据 EV-edded20f04 和 EV-d69099674e。GitHub Actions 的 live-cdio job 会在开放网络里重跑同一案例，并把输出（含轨迹）作为 artifact 上传；那边的提交元数据调用可能成功，具体以 CI 产物为准。

**C. 补证真正改变了判断，还是只明确了剩余疑问？**

**只明确了剩余疑问，没有改变判断。** 新证据说明：

- 新 README 仍保留 70B 条目，但只对应 Galaxy 和 TP=8 的配置；
- 同一版本的官方模型文档把 70B 的支持硬件写成 LoudBox / QuietBox / Galaxy，没有明确写出 Blackhole。

两者都不能证明「撤回」，也不能证明「仍然支持」，所以系统保持「尚未核实」。在测试中，当工具返回能命中规则的官方内容时，系统确实会产生新的判定（`new_decisive_evidence`）。也就是说，能改变判断的能力是存在的，只是这个真实案例没有满足条件。

**D. 与 R2 相比新增了什么实质能力？**

- R2 只能被动接收 changedetection.io 推送的变化。R3 可以对有疑点的研判，**主动、有界地去其他官方来源取证**，并把取得的内容当作新证据交给同一套规则复评。
- 来源声明从写死的文本，改为根据运行元数据生成，并且回放必须通过哈希核验。

**E. 新增测试和 GitHub CI 是否真实通过？**

- 本会话离线测试 **185 passed, 4 skipped**（跳过的是 4 个在线测试）。
- 在线测试 **4 passed**，包括新增的真实补证集成测试：它只断言控制条件（触发、不超过 3 次、只访问白名单地址、结论只读），不断言研究答案。
- GitHub CI 的结果见第七节和 PR。

**F. 距离「可交给非技术人员使用的 Agent」还差什么？**

1. **没有真实模型：** 规则需要人工写正则，遇到表格以外的写法（比如 TC-03 中的文档表）就无法定向。这正是需要模型或更好的抽取器的地方。
2. **没有持续自动监控：** 现在是按需运行和历史回放，还没有定时任务、通知，也没有长期运行的 changedetection.io 实例。
3. **没有交互界面：** 研究判断、信号规则和白名单都要手写 JSON；输出是 Markdown 和 JSON 文件。
4. **来源面窄：** 当前容器只能访问 GitHub。官网、新闻稿、监管披露都需要放开网络，并为它们写新的工具适配器。
5. **评测不足：** 真实案例只有一个；缺少标注数据集和误报/漏报统计。
6. **运维：** 没有部署方案、权限管理和多用户支持；补证轨迹也没有可视化。

## 七、测试与 CI

| 检查 | 结果 |
|---|---|
| `python -m pytest`（离线） | **185 passed, 4 skipped**；R1/R1.1 的测试未改。R2 测试有两处为配合 P0 和 R3 做了调整：回放改为带运行记录、按研判内容比较，以及按证据来源过滤；其余未改 |
| 在线测试（本会话） | **4 passed**（真实 changedetection.io 与真实 GitHub 来源） |
| 新增测试 | `test_r3_provenance.py` 8 个，`test_r3_verification.py` 40 个，在线 1 个 |
| GitHub Actions | 见 PR #1 最新提交的检查结果；live-cdio job 已改为运行跨批次补证案例，并上传输出 |
| 上游核心 | `git diff origin/master -- changedetectionio/` 为 0 行 |

## 八、未完成与风险

- GitHub 提交元数据工具在本容器中被网络策略拒绝（403），只能在 CI 等开放网络中工作。
- 检索采用词语加正则加排序的方式，对长文档只取前 8 个匹配；可能漏掉相关行，例如模型文档中第 256 行的上下文长度表。
- 证据去重键是「内容 + 研究对象」：同一内容来自两个不同 URL 时只记一条，第二个来源只在轨迹中出现。
- 补证不会递归：工具取得的证据不会再次触发补证。
- 输出整组提交仍然不防进程被强制终止（R2 已说明）。
