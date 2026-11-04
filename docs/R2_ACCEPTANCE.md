# TechSignal R2 验收报告：真实数据接入与研究能力验证

- 日期：2026-11-04
- 分支：`claude/affectionate-goldberg-u18nzv`，PR #1
- 环境：云会话容器，Python 3.13；changedetection.io 依赖装在独立 venv 中，TechSignal 自身仍只依赖 pydantic

## 一句话结论

TechSignal 已能**从真实运行的 changedetection.io 实例**，经正式 API Client 拉取**真实公开来源**的变化，完成研判、生成中文速览，并在跨批次中保持可追溯。

已验证到的环境有两个：本会话容器，以及 GitHub Actions 的隔离环境。

**真实模型推理仍未实现**：本项目没有被授权的模型服务，模型层只完成了协议与异常验证。

## 一、能力分级

| 能力 | 级别 | 证据 |
|---|---|---|
| changedetection.io HTTP API Client（鉴权、超时、重试、错误分类、分页） | **真实，已在线验证** | `techsignal/cdio_client.py`；18 个测试打在真实本地 HTTP 服务上；对真实实例的在线测试见下 |
| CLI 在线模式 `run --cdio-url`，保存原始响应和运行记录 | **真实，已在线验证** | `run_record.json` 含响应 sha256，不含任何密钥 |
| 启动真实 changedetection.io 做端到端 | **真实** | `techsignal/live.py`；本会话 3/3 在线测试通过；GitHub Actions `live-cdio` job 通过 |
| 真实公开研究案例 | **真实公开资料** | `cases/tenstorrent_blackhole/`，来源是 Tenstorrent 官方 GitHub 仓库按提交固定的版本 |
| 真实历史版本差异 | **真实** | 提交 `4cd196f → 168a956`（加入 Blackhole 70B 条目），`168a956 → 62f3cd0`（该条目在 README 重排中消失）。两段差异都由 changedetection.io 实际抓取并生成 |
| 跨批次研判（P3） | **真实运行，含真实数据演示** | `--track-history` 加上只追加的 `judgements.jsonl`；真实两批次案例见 `output_cross_batch/` |
| 输出替换阶段失败保护 | **已修复并测试** | 替换中途失败会回滚到原状态（3 个注入测试） |
| 模型层 `OpenAICompatibleProvider` | **仅协议验证，未做真实推理** | 14 个测试打在本地协议桩服务器上，那是固定应答，**不是模型** |
| Mock 回放 | **模拟** | 和 R1 一样，仅用于离线演示和测试，输出中逐条标注 |
| 虚构场景 S01–S23 | **虚构数据** | 只用来验证处理逻辑 |

## 二、P0：真实数据接入

### 1. API Client（`techsignal/cdio_client.py`）

- 只用标准库 `urllib`，没有新增运行时依赖。端点均取自本仓库代码：
  - `/api/v1/systeminfo`
  - `/api/v1/watch`（GET / POST）
  - `/api/v1/watch/{uuid}`（GET / PUT / DELETE / `?recheck=1`）
  - `/api/v1/watch/{uuid}/history`
  - `/api/v1/changes?format=json`
- **鉴权：** 使用 `x-api-key` 头（依据 `changedetectionio/api/auth.py`）。401 和 403 抛 `CdioAuthError`。
- **超时与重试：** 只有 GET 会重试，且只在连接错误、超时或 5xx 时重试，按指数退避；4xx 和 POST 不重试。
- **错误分类：** Auth / NotFound / HTTP / Connection / Timeout / Protocol 六类；返回内容不是 JSON 或不符合契约时，抛 `CdioProtocolError`。
- **分页：** 按 `limit/offset` 取完所有页后合并成一份 ChangesReport，再交给已有的适配器解析。
  - 分页期间 `total` 发生变化：报错。
  - 不同页出现重复的监测项：报错。
  - 超过最大页数：报错。
  - 任一页带 `incomplete` 标志：合并结果也标为 `incomplete`。
- **API key 只从环境变量 `TECHSIGNAL_CDIO_API_KEY` 读取**，不会出现在命令行参数和任何输出文件中。在线测试会检查错误 key 不会被写入运行记录。

### 2. 在线集成测试（`tests/integration/test_live_cdio.py`，需显式开启）

| 测试 | 内容 | 本会话 | GitHub Actions |
|---|---|---|---|
| `test_live_cdio_end_to_end_with_local_page` | 真实 changedetection.io 抓取本地页面（页面内容是虚构的，抓取、快照和差异都由 changedetection.io 自己完成），两次检查，按 1 条/页分页拉取，生成研判；再用错误 key 运行，验证退出码 2 且已有输出不变 | 通过 | 通过（run 41783211312） |
| `test_live_cdio_real_public_case` | 真实 changedetection.io 抓取 GitHub 上 Tenstorrent 仓库按提交固定的两个版本，全链路生成研判 | 通过 | 通过（同上） |
| `test_live_cdio_real_cross_batch_case` | 三个真实版本、两个批次、跨批次历史 | 通过 | 随本次提交运行 |

GitHub Actions 日志节选（job 126419313374）：

```
2 passed in 13.44s
在线拉取：http://127.0.0.1:49299（changedetection.io 0.60.10），2 次请求，1 个变化监测项
变化事件 1，过滤 0，评估 1（支持 0 / 削弱 1 / 证据不足 0 / 无关 0）
引用完整性校验：通过
Artifact techsignal-live-case successfully uploaded
```

### 3. 环境约束及处理

- **问题：** 云容器的网络策略拒绝访问 `web.archive.org`、`www.tsmc.com`、`www.intel.com`、`www.sec.gov` 等站点（CONNECT 返回 403）；可以访问 GitHub 和 PyPI。容器里没有可用的 Docker daemon。
- **处理一：** changedetection.io 直接从本仓库源码以子进程启动，依赖装在独立 venv 里；启动时关闭字节码写入，不在上游目录留下任何文件。
- **处理二：** 真实来源选用 GitHub 上公司官方仓库的提交历史。每个版本都能用提交 SHA 复核。
- **处理三：** GitHub Actions 的 `live-cdio` job 在开放网络中独立复现同一条链路。
- 公司官网、新闻稿、监管披露这类来源，在当前容器中无法接入。

## 三、P1：真实研究判断验证

详见 [`cases/tenstorrent_blackhole/README.md`](../cases/tenstorrent_blackhole/README.md)。

- **研究问题：** Tenstorrent Blackhole 的大模型推理软件支持成熟度如何？70B 级模型是否已进入官方支持？
- **历史判断 TH-TT-001 的依据：**
  - SRC-TT-1：提交 `4cd196f` 中，模型表的 Blackhole 行共 3 行，全部是 8B 模型，行数由程序统计；
  - SRC-TT-2：官方脚注原文「\* Blackhole software optimization is under active development.」。
  - 引文由程序从提交内容中逐字截取。
- **真实变化：** 提交 `168a956`（2025-07-11）新增一行「Llama 3.1 70B (TP=4) | QuietBox (Blackhole)」。
- **研判：** 规则层判定为「削弱」假设 BH1；证据为 A 级（公司官方）；系统建议复核，结论状态保持 active。
- **分级：** 事实、系统研判、推测、待核实分别列在案例文档中。例如「TP=4 接近 TP=8 的吞吐，可能意味着单芯片能力更强」被明确标为推测；「`*` 表示数据仍是初步的」列为待核实。
- **中文速览：** `cases/tenstorrent_blackhole/output/brief.md`。
- **离线回放：** `tests/test_r2_real_case.py` 用捕获的原始响应回放，验证响应哈希与运行记录一致，且回放生成的速览与已提交版本逐字一致。

## 四、P2：模型层验证

- **可用服务排查：** 环境中没有本项目被授权的模型凭据。`ANTHROPIC_BASE_URL` 属于 Claude Code 会话自身的基础设施，**不属于本项目，未使用**。HuggingFace、OpenAI、Ollama 均不可达。按约束，没有接入任何未授权或不稳定的公共接口。
- **完成的部分**（`tests/test_r2_provider_http.py`，14 个测试）：
  - 用真实 HTTP 打到本地 OpenAI 兼容协议桩，验证请求格式：路径、Bearer 头、`temperature=0`、`response_format`、证据编号是否进入提示词。
  - 各类异常都转为 `ProviderError`，且异常信息中**不含密钥**：401、429、500、非 JSON、没有 choices、模型输出散文而非 JSON、枚举值非法、多余字段、超时。
  - 走完整流水线时：输出标注为「模型推断」而不是 Mock；没有引用证据的输出被拒绝；置信度最高为 medium；服务不可用时降级为「证据不足」。
  - 没有配置模型时，CLI 以退出码 2 结束，不写任何文件。
- **未完成：** 没有任何真实大模型推理，也没有模型质量评测。

## 五、P3：跨批次研究能力

- **开启方式：** `--track-history`（默认关闭，原有行为不变）。开启后证据台账累积，`judgements.jsonl` 只追加，历史行逐字节保留。
- **比对规则：** 新批次每条评估都与同一假设最近一次的历史状态比较。
  - 方向相反：降为「证据不足」，列出历史冲突证据，**不自动裁决**。
  - 方向相同：标记为「强化」。
  - 无法定向（例如相关内容被删除）：保留历史判断，标记「待核实」。
- **可解释：** 简报新增「跨批次判断变化」一节，`decision_log.json` 新增 `cross_batch_transitions`，逐条说明「之前 → 现在」和原因。研究结论本身从不被修改。
- **规则层的配套改动：** 规则层不判方向的结果，在只涉及一个假设时也标注该假设编号，以便跨批次跟踪。这不会给出方向。
- **真实数据演示**（`cases/tenstorrent_blackhole/output_cross_batch/`）：
  - 批次 1（`4cd196f → 168a956`）：BH1 被削弱。
  - 批次 2（`168a956 → 62f3cd0`，README 改为 Featured Models 列表，70B 条目消失）：信号出现在删除侧，系统**没有**把结论翻回去，而是记录「之前：削弱（批次 RUN-d10ea270）→ 现在：证据不足；保留历史判断并标记待核实」。
  - 这条 70B 条目是被撤回，还是仅因文档重排而被略去？**无法从这份 README 判断**，所以这个结果是合理的保守输出。
- **测试：** `tests/test_r2_cross_batch.py` 5 个（冲突、强化、可复现、失败不破坏历史、默认关闭），真实数据离线回放 2 个，在线 1 个。

## 六、可靠性边界补充（R1.1 遗留）

- **发现：** R1.1 验收记录写的「最坏情况只是台账比 brief 新，不会出现 brief 引用台账中不存在的证据」**表述过度**：这个结论只在 `--append-ledger` 下成立。默认模式下，如果替换中途失败，旧的 brief.md 会对着一个不含旧证据的新台账。
- **复现：** 新增 3 个测试，分别在第 1、2、4 次 `os.replace` 注入失败；修改前第 2、4 次的用例失败。
- **最小修复：** 替换前先把现有文件复制到暂存目录中的备份；任何一次替换失败，就把已替换的文件逐个恢复，原本不存在的则删除。这 3 个测试现在通过。
- **仍未覆盖：** 进程在两次替换之间被强制终止。没有写日志，也没有做目录级原子切换。

## 七、回归结果

| 检查 | 结果 |
|---|---|
| `python -m pytest`（离线） | **137 passed, 3 skipped**（跳过的是 3 个在线测试）；R1/R1.1 的原有测试文件和数据未改动，全部通过 |
| 在线测试（`TECHSIGNAL_CDIO_PYTHON` 和 `TECHSIGNAL_LIVE_NETWORK=1`） | **3 passed**（本会话） |
| GitHub Actions `TechSignal (independent module)` | 提交 8798fe2：test (3.11)、test (3.13)、live-cdio 全部成功；最终提交的结果见 PR |
| `python -m techsignal eval` | 23/23 |
| `python -m techsignal.fixtures --check` | 通过 |
| 上游核心 | `git diff origin/master -- changedetectionio/` 为 0 行；运行 changedetection.io 后上游目录下没有 `__pycache__` 残留 |
| 演示输出变化 | `brief.md` 一行措辞改为「changedetection.io 抓取于…（抓取时间不等于发布时间）」；`brief.json` 新增空的 `transitions` 字段。证据和决策记录未变 |

## 八、未完成与风险

1. **真实 LLM 推理：** 未实现（没有授权服务）。下一步需要 Owner 提供授权的模型服务或本地模型环境。
2. **官网、新闻稿、监管披露等来源：** 当前容器的网络策略不放行。在 GitHub Actions 中可以访问，但还没有设计对应案例。
3. **单一案例：** 真实研究验证只有一个公司、一个问题，不足以代表通用准确率。
4. **抓取时间与发布时间：** changedetection.io 的快照时间是抓取时间。案例中用提交时间补充，但在日常监测场景里，两者的差别需要人来判断。
5. **规则维护成本：** 真实案例的信号规则是手写的正则（例如 `70B[^|]*\|[^|]*\|[^|]*Blackhole`），针对的是表格格式；README 一重排就失效了，这在批次 2 中已经实际出现。
6. **输出提交：** 不防进程被强制终止。
