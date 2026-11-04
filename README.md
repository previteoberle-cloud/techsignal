# TechSignal

**硬科技企业、行业和技术领域的公开信息采集与管理工具。**

建好采集任务，点一下「立即采集」，系统就从你指定的公开来源（RSS/Atom 订阅源、网页）取回新信息，去重归档，然后可以搜索、筛选、导出。全中文界面，不需要写代码。

> *English summary* — TechSignal is a public-information collector for hard-tech companies, industries and technologies. Create a collection task in a Chinese browser workbench (name, companies/keywords, tags, public source URLs), click "collect now", and it fetches RSS/Atom feeds and web pages, deduplicates by stable id and content fingerprint, stores items locally, and lets you search, filter and export them (CSV/Markdown). Web pages can optionally be read through your own [changedetection.io](https://github.com/dgtlmoon/changedetection.io). An earlier research-judgement engine (thesis impact assessment, evidence ledger, bounded verification) is kept as an optional "advanced research" area. No paid API and no model is used.

![信息采集首页](docs/screenshots/c03_after_second_collection.png)

## 信息采集：快速上手

### 能采集什么

| 来源类型 | 怎么采集 | 每次采集新增的是 |
|---|---|---|
| RSS 2.0、RSS 1.0（RDF）、Atom 订阅源 | 直接读取 | 订阅源里还没有保存过的条目：标题、原始链接、发布时间、摘要 |
| 普通网页 | 直接读取；也可以选择经你自己的 changedetection.io 读取 | 第一次：网页本身；之后：只有网页**新增**的文字，以「页面更新」条目保存 |

- **去重：** 按稳定标识（订阅条目的 guid/id，或链接，或标题加日期）和内容指纹去重。多次采集只追加真正的新条目，同样的内容出现在两个来源里也只保存一次。
- **失败处理：** 某个来源失败时，已有的信息全部保留；「采集状态」页写明失败原因，例如 HTTP 503、无法访问、XML 格式错误。
- **安全：** 只采集公开地址，重定向到内网地址也会被拒绝；拒绝带 DOCTYPE/ENTITY 声明的 XML；单个来源最多 5 MB，超时 20 秒。
- **不做的事：** 不使用付费 API，不使用模型；不能登录网站，不执行网页里的 JavaScript。

### 启动

**Windows：**

1. 从 python.org 安装 Python 3.11 或以上，安装时勾选「Add python.exe to PATH」。
2. 下载或克隆本仓库，然后双击 `scripts\start_windows.bat`。

脚本的行为：
- 依次查找 `py` 启动器和 `python`。找不到 Python，或版本低于 3.11，会明确提示并停止。
- 第一次运行时，在项目目录下创建 `.venv` 并安装依赖；安装失败会停止，不会假装启动成功。上次安装没有完成的，会自动重新安装。
- 不会改动或删除采集数据。
- 浏览器打开 http://localhost:8501 。如果端口被占用，Streamlit 会换一个端口，以命令行窗口里显示的为准。

**注意：** Windows 脚本只做过静态检查，以及在 Linux 上执行它调用的命令（包括带空格和中文的数据目录），**还没有在真实 Windows 上验收**。

工作台默认只监听本机（127.0.0.1），局域网和公网都访问不到。它没有登录功能，不要通过 `--server.address 0.0.0.0` 暴露出去。

也可以在 PowerShell 中手动执行：

```powershell
cd TechSignal
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[ui]"
python -m techsignal.workbench
```

**macOS / Linux：**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[ui]"
python -m techsignal.workbench
```

### 使用

1. 首页就是「信息采集」。填写任务名称（例如「AI 芯片行业动态」）、关注的企业或关键词、分类标签，以及公开来源网址（每行一个，后面可以写来源名称），然后点「创建采集任务」。
2. 点「立即采集」。信息流按时间倒序显示，点标题打开原始出处，命中的关键词会高亮。
3. 可以按任务、来源、关键词、日期筛选，或者搜索标题和摘要。点「导出 CSV」或「导出 Markdown」导出当前筛选结果。CSV 带 BOM，用 Windows 的 Excel 直接打开中文不会乱码。
4. 「采集状态」页查看每个来源的最近成功时间、失败原因和新增数量；「任务设置」页修改任务。

### 数据保存在哪里

- 默认在 `~/.techsignal/collector`，Windows 上是 `C:\Users\<你的用户名>\.techsignal\collector`。用环境变量 `TECHSIGNAL_COLLECTOR_ROOT` 可以改到别的目录。
- 每个任务一个目录：`task.json`（任务定义）、`items.jsonl`（采集到的信息，只追加）、`status.json`（各来源状态）、`pages/`（网页上次的文字）。批量采集的日志在 `logs/collect.log`。
- 写入顺序先归档、后更新网页快照。归档失败时，下次采集会重新发现这次变化；快照更新失败时，也不会重复归档。
- 重启工作台后，历史数据都还在。

不打开浏览器也可以用命令行：

```bash
python -m techsignal.collector list
python -m techsignal.collector collect <任务编号>
python -m techsignal.collector collect-all
python -m techsignal.collector export <任务编号> --csv out.csv --md out.md
```

`collect-all` 依次采集全部任务，并把结果追加到 `<数据目录>/logs/collect.log`（日志超过约 1 MB 时会轮换）。
- **退出码：** 全部成功时为 0；有来源失败，或有任务因正在被另一次采集处理而被跳过时，为 1。
- **失败不影响其他：** 某个来源或任务失败，不会影响其他任务，也不会损坏历史记录。
- **同一任务不会并发采集：** 同一个任务同时只允许一次采集，另一次会跳过并写入日志。

### 定时自动采集（可选，默认关闭）

使用 Windows 自带的任务计划程序，不需要常驻服务。只有你手动执行 `enable`，才会创建计划任务：

```bat
scripts\windows_schedule.bat enable 6
scripts\windows_schedule.bat status
scripts\windows_schedule.bat run
scripts\windows_schedule.bat disable
```

- `enable 6`：每 6 小时采集一次全部任务（1–24 小时），只在你登录时运行。
- `status`：查看计划任务状态。
- `run`：立即执行一次，与计划任务执行的命令相同。
- `disable`：删除计划任务，不删除任何数据。

计划任务名为 `TechSignal-Collector`，执行的命令是 `.venv\Scripts\pythonw.exe -m techsignal.collector collect-all`，运行结果见上面的 `collect.log`。这个脚本同样**还没有在真实 Windows 上验收**。

macOS 和 Linux 可以用 cron 执行同一条命令，例如：

```
0 */6 * * * /路径/.venv/bin/python -m techsignal.collector collect-all
```

### 已经验证过的真实来源

| 来源 | 验证方式 | 结果 |
|---|---|---|
| `raw.githubusercontent.com` 上 tenstorrent/tt-metal 的 README（网页） | 云开发环境中实际采集，用真实浏览器操作，重启后再采集一次；GitHub Actions 上同样执行 | 两次采集，第二次没有重复；重启后数据仍在 |
| GitHub 发布记录 Atom（tenstorrent/tt-metal） | 在 GitHub Actions 的 `live-cdio` job 中采集两次（run #16 通过）。云开发环境的网络策略拒绝访问，结果为 403，所以没有在那里验证 | 两次都成功；第二次没有重复条目 |
| arXiv cs.AR RSS | 同上（run #16 通过） | 两次都成功；第二次没有重复条目 |
| 经真实 changedetection.io 0.60.10 抓取网页 | 在线集成测试，使用本地测试页 | 首次记录、未变化时不重复、新增内容单独成条 |

离线测试使用 `tests/fixtures/collector/` 中的虚构 RSS/Atom 数据，只用来验证逻辑，不当作真实采集结果。交接说明见 [docs/COLLECTOR_MVP_HANDOFF.md](docs/COLLECTOR_MVP_HANDOFF.md)。

---

# 高级研究（可选）：研究判断与研判

下面是本项目早期开发的研判功能，现在在工作台的「高级研究」入口：判断网页变化对既有研究结论的影响、证据台账、有界补证、中文速览。日常的信息采集不需要它。

![高级研究：跨批次总览](docs/screenshots/r4_02_cross_batch_overview.png)

## 1. 解决什么问题

研究人员跟踪一家硬科技公司时，常常要回答：「官网、文档、仓库里**这条新变化**，会不会动摇我之前的判断？」

网页监控工具只能告诉你「页面变了」。TechSignal 进一步回答下面这些问题，并且每个回答都给出证据编号：

- 这条变化与哪一条**研究判断、哪一条关键假设**有关？
- 它是**支持**、**削弱**、**无关**，还是**证据不足、需要核实**？
- 证据来自谁？等级如何？是什么时候**发布**的，又是什么时候被**抓取**的？
- 遇到不确定的情况，系统**主动查了什么**？查到了没有？**还剩什么需要人来核实**？

系统永远不会自动修改研究结论。「削弱」只会触发「建议研究负责人复核」。

## 2. 与 changedetection.io 的关系与区别

| | changedetection.io（上游，独立项目） | TechSignal（本项目） |
|---|---|---|
| 回答的问题 | 网页变了什么 | 这对研究判断意味着什么，证据够不够 |
| 输入 | 网页、RSS、PDF | changedetection.io 的变化数据（REST API） |
| 输出 | 快照、差异、通知 | 研判结论、证据台账、跨批次历史、补证轨迹、中文速览 |
| 状态 | 监测项与快照 | 研究判断（只读）、证据台账、研判历史 |

**依赖关系：** TechSignal 通过 HTTP 读取一个**正在运行的** changedetection.io 实例，本仓库不包含、也没有修改它的源码。没有 changedetection.io 时，TechSignal 照样可以安装、测试，并打开已保存的真实案例。抓取网页、存储快照、计算差异，都是 changedetection.io 作者和贡献者的工作；本项目只负责其后的研判部分。

## 3. 本项目独立实现的部分

| 模块 | 作用 |
|---|---|
| `techsignal/adapters/cdio.py`、`cdio_client.py` | 按 changedetection.io 的 API 契约校验并解析变化报告；HTTP 客户端（鉴权、超时、重试、分页、错误分类） |
| `triage.py`、`rules.py`、`assess.py` | 降噪；去重（按「内容 + 研究对象」，高等级来源不会被低等级来源当作重复）；实体、主题和关键假设信号规则（否定语境、删除内容不会给出方向）；证据等级闸门；来源冲突不自动裁决 |
| `store.py`、`pipeline.py` | 证据台账（JSONL，校验通过后才写盘）；跨批次研判历史 |
| `brief.py`、`validate.py`、`provenance.py` | 中文速览；引用完整性校验（事实没有来源就报错）；来源声明按运行元数据生成 |
| `verify/` | **有界补证**：确定性触发与规划；白名单（每次重定向都重新检查）；每项最多 3 次调用；超时、重试、去重；工具输出只当作数据；完整执行轨迹 |
| `providers.py` | 模型层：OpenAI 兼容接口，R6 起支持「一次调用、逐条假设给建议」，带变化类型、调用上限、输入长度上限、超时和用量记录。只用 Mock 和本地协议桩测试过，**没有调用过真实模型** |
| `projects.py`、`project_monitor.py` | R6：浏览器里用文字新建研究项目（版本记录、冲突保护、与示例案例隔离）；连接用户自己的 changedetection.io，确认后建立监测，手动检查一次并研判 |
| `eval_r6.py`、`eval/` | R6：独立评测集，人工标注 / 规则层 / 协议桩 / 真实模型分列 |
| `workbench/` | 全中文 Streamlit 研究工作台 |
| `live.py`、`fixtures.py` | 开发工具：启动真实 changedetection.io 做端到端验证；用上游的报告生成器制作测试数据 |

## 4. 架构与数据流

```
                 changedetection.io（外部服务，可选）
                         │  GET /api/v1/changes（或已保存的响应）
                         ▼
 ChangesReport ─► 适配器校验 ─► 降噪 / 去重 ─► 证据台账（编号、来源、等级、可追溯路径）
                                          │
                     研究判断（JSON，只读） ─┤
                                          ▼
                      规则研判：支持 / 削弱 / 无关 / 证据不足
                      ├─ 证据等级闸门（C/D 级不能单独定向）
                      ├─ 同批次冲突与跨批次历史比对（不自动裁决）
                      └─ 有疑点 ─► 有界补证（白名单内的真实工具调用）
                                     └─► 新证据独立编号 ─► 用同一套规则复评
                                          ▼
          中文速览 + 引用校验 ─► 原子提交输出 ─► 研究工作台（浏览器）
```

## 5. 快速开始（干净环境）

需要 Python 3.11 或以上版本。不需要 Docker、模型密钥或 changedetection.io。

```bash
git clone <本仓库地址> TechSignal && cd TechSignal
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,ui]"
```

运行离线测试：

```bash
python -m pytest
```

启动研究工作台，然后在浏览器打开 http://localhost:8501：

```bash
python -m techsignal.workbench
```

在工作台里：
1. 选择「Tenstorrent Blackhole：跨批次跟踪」案例；
2. 依次查看总览、变化时间线、补证过程、证据详情；
3. 在「管理层速览」下载中文速览；
4. 把「数据来源」切到「离线回放」，用保存的数据重新运行一次研判。

### 新建你自己的研究（R6）

侧栏「工作区」切到「我的研究项目」：

1. 填写研究对象、研究问题、当前判断、关键假设（可写支持 / 削弱信号词）和要监测的公开网址，点「创建研究项目」。也可以先保存草稿。不需要写 JSON、代码或正则；信号词按字面匹配，不是语义理解。
2. 「来源确认」（R6.1）：创建时选的来源类型只是你的声明，**未确认的来源最多按 C 级处理，不能单独支撑支持或削弱判断**。核对来源身份后，写明确认人和依据并确认，才会按类型分级（公司官方 A 级，监管或媒体 B 级）。确认和撤销都会留下版本记录。
3. 「监测与评估」：填写你自己运行的 changedetection.io 地址和 API key → 连接 → 勾选确认后建立监测 → 「立即检查一次并研判」。第一次抓取只建立基线；没有新快照或没有实质差异时只记录「本次没有新的变化」，不会生成研判。
4. 「研判结果」：每条判断写明判断来源、发生了什么、为什么、引用的证据、通过了哪些检查、还有什么没解决。
5. 「研究设定」修改判断会生成新版本，「修改记录」保留全部旧版本；后台检查永远不会改写你的判断。

项目默认保存在 `~/.techsignal/projects`（环境变量 `TECHSIGNAL_PROJECTS_ROOT` 可改），不会写进仓库里的示例案例。没有 changedetection.io 时，工作台照常打开，并明确提示需要这个外部服务。

![新建项目后的研判结果](docs/screenshots/r6_07_result_explained.png)

可选的模型层（运营方配置，不在页面里填密钥）：`TECHSIGNAL_LLM_BASE_URL`、`TECHSIGNAL_LLM_MODEL`、`TECHSIGNAL_LLM_API_KEY`，以及 `TECHSIGNAL_LLM_MAX_CALLS`（默认 20）、`TECHSIGNAL_LLM_MAX_INPUT_CHARS`（默认 6000）、`TECHSIGNAL_LLM_TIMEOUT`（默认 30 秒）。规则仍然先判断，只有规则无法判定的变化才交给模型；模型建议要通过结构、引用、变化类型、主体、证据等级和提示注入检查，失败就按「证据不足」处理。规则、模型与安全闸门的关系见 [docs/DECISION_POLICY.md](docs/DECISION_POLICY.md)。

命令行演示（虚构数据，Mock 回放模型层）：

```bash
python -m techsignal run --changes demo/changes_report.json --theses demo/theses.json --sources demo/sources.json --provider mock --replay demo/mock_replay.json --out /tmp/out
```

连接你自己的 changedetection.io（API key 只从环境变量读取，不会写入任何文件）：

```bash
export TECHSIGNAL_CDIO_API_KEY=...
python -m techsignal run --cdio-url http://localhost:5000 --from 1790812800 --theses my/theses.json --sources my/sources.json --out out/ --track-history
```

## 6. 真实案例：Tenstorrent Blackhole

- **研究问题：** Tenstorrent 新一代 AI 芯片 Blackhole 的大模型推理软件是否成熟？70B 级模型是否已进入官方支持？
- **数据：** 官方开源仓库 [tenstorrent/tt-metal](https://github.com/tenstorrent/tt-metal) README 的三个提交版本，由**真实的 changedetection.io 实例**抓取。完整过程见 [cases/tenstorrent_blackhole/README.md](cases/tenstorrent_blackhole/README.md)。

| 批次 | 版本（发布时间） | 系统研判 | 补证 |
|---|---|---|---|
| 1 | `4cd196f → 168a956`（2025-07-11）：新增「Llama 3.1 70B (TP=4) QuietBox (Blackhole)」 | 假设 BH1「尚无 70B 官方记录」被**削弱**（A 级官方证据） | 不触发（判断明确） |
| 2 | `168a956 → 62f3cd0`（2025-08-12）：README 改版为精选列表，该行消失 | **证据不足，待核实**：信号只出现在删除侧，不翻转历史判断 | 触发 3 次真实查询：同页快照、官方提交说明、同版本模型文档。取得新的官方证据，但无法区分「文档重排」和「撤回支持」，**保持待核实** |

![补证过程](docs/screenshots/r4_07_verification_full.png)

这个案例的意义在于：在信息不足时，系统**如实保留不确定性**，而不是硬给结论。

## 7. 测试与可量化结果

| 项目 | 结果 |
|---|---|
| 离线测试 | 见 [docs/R5_ACCEPTANCE.md](docs/R5_ACCEPTANCE.md) 中干净克隆下的实测数字；不需要网络、模型或 changedetection.io |
| 虚构场景评测 | 23/23 场景通过（`python -m techsignal eval`）。场景和预期都由开发者编写，只说明行为符合设计，**不代表真实准确率** |
| R6 / R6.1 评测集 | 22 个虚构案例、25 个「案例 × 假设」人工标注，见 [docs/R6_EVAL.md](docs/R6_EVAL.md)，整改前后的数字都保留。规则层整改前（R6 代码）：12 个一致、7 个保守、**6 个方向错误**；整改后：15 个一致、10 个保守、**0 个方向错误**。改善主要来自保守降级，人工待核实事项因此增加。协议桩一列只检验校验与降级逻辑，**不代表模型能力**；真实模型一列为 BLOCKED |
| 变异检验 | 故意关闭等级闸门、冲突处理、去重主体、台账写盘顺序、多假设归因等保护，对应测试都会失败（见各轮验收记录） |
| 在线集成 | 4 个测试：真实 changedetection.io（固定上游版本）加真实 GitHub 来源，包括真实补证 |
| 浏览器验收 | R4：六个页面；R6：在真实 changedetection.io 上走完「新建项目 → 确认建立监测 → 基线 → 页面变化 → 研判 → 修改判断 → 版本记录」，含手机宽度。截图见 `docs/screenshots/` |
| CI | `test`（3.11 / 3.13，含 R6 评测）、`ui`（工作台测试 + 真实启动）、`upstream-contract`（与固定上游版本的报告生成器比对）、`live-cdio`（在线集成，含 R6 新项目端到端）、`r6-browser`（真实 Chromium + 真实 changedetection.io 的新项目流程） |

## 8. 关键设计决策

- **规则优先，模型可替换。** 规则只在信号明确时给出方向；否定语境、删除内容、方向混合都不定向。模型层预留了接口，输出必须引用存在的证据，置信度最高为中。
- **不确定性是一等公民。** 证据不足、来源冲突、跨批次无法定向，都会被明确标出，并给出待核实问题；系统从不改写研究结论。
- **证据可追溯。** 每条陈述都带证据编号或研究判断编号，校验不通过就不写出文件。发布时间（提交记录）与抓取时间分开记录。
- **补证有边界。** 只在有疑点时触发；白名单精确到主机、端口和路径；每项最多 3 次调用；工具输出只当作数据；失败时如实记录，不影响已有状态。
- **失败安全。** 输出先写到暂存区，替换失败就回滚；API key 和日志按调用显式传递，并发会话互不影响。
- **来源诚实。** 简报声明根据运行元数据生成，离线回放必须核对 sha256。

## 9. 尚未实现与限制

- **没有调用过真实大模型：** 模型层的接口、校验和降级已实现，但只用 Mock 和本地协议桩测试；没有获得授权的模型服务，因此没有任何模型准确率数字。
- **没有持续后台监控：** 没有调度和通知，只能手动「检查一次」（R7）；真实案例是对历史版本的观测回放。
- **只有一个真实研究案例**；评测集是 22 个虚构案例，标注由本项目编写，不能代表通用准确率。
- **规则层（R6.1 后）：** 否定按分句判断，有否定、辟谣、疑问或双重否定时一律不定向；英文简称按完整单词匹配，只命中短简称时要求人工确认。代价是「待核实」变多。规则对「传闻」类措辞不做特殊处理（交给来源等级）；对同义改写无能为力。规则先于模型判断，模型目前不复核规则的明确结论（设计见 DECISION_POLICY）。
- **来源身份只能人工确认：** 没有自动的域名身份验证；R6 时保存的项目会被降级为未确认（有记录、不会升级）。
- 新建项目已不需要写 JSON；**补证白名单仍需手写 JSON**，新建项目暂不执行补证。信号词按字面匹配，对同义改写无能为力（评测集 E03、E04）。
- **工作台只面向本机使用：** 没有登录与权限，没有做安全加固，**不适合直接部署到公网或多用户环境**。
- 补证工具目前只覆盖 changedetection.io 快照和 GitHub；官网、新闻稿、监管披露还需要新的适配器。

## 10. 开发者：可选的上游依赖

只有两类开发任务需要 changedetection.io 源码：

1. 用上游的报告生成器重新生成或校验测试数据；
2. 启动真实实例做集成测试。

```bash
scripts/fetch_changedetection.sh                       # 克隆固定提交 5e086dd 并校验 sha256
export TECHSIGNAL_CDIO_SOURCE="$PWD/.upstream/changedetection.io"
python -m techsignal.fixtures --check                  # 测试数据与上游输出一致性
pip install -r .upstream/changedetection.io/requirements.txt
TECHSIGNAL_CDIO_PYTHON="$(which python)" TECHSIGNAL_LIVE_NETWORK=1 python -m pytest tests/integration
```

## 11. 开发历程与文档

本项目先在 changedetection.io 的 Fork 中以独立目录开发（`techsignal/`，PR #1），后用 `git subtree split` 提取为本仓库，**完整保留了 R1–R5 的提交历史**。

- 各轮记录：[R1](docs/R1_ACCEPTANCE.md)、[R2](docs/R2_ACCEPTANCE.md)、[R3](docs/R3_ACCEPTANCE.md)、[R4](docs/R4_ACCEPTANCE.md)、[R5](docs/R5_ACCEPTANCE.md)、[R6](docs/R6_ACCEPTANCE.md)、[R6.1](docs/R6_1_ACCEPTANCE.md)。早期记录中的 `techsignal/...` 路径指的是 Fork 中的目录。
- 技术细节：[docs/TECHNICAL_REFERENCE.md](docs/TECHNICAL_REFERENCE.md)。
- 演示讲解稿：[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)。

**开发方式：** 代码在 Claude Code 中编写，提交的作者字段为 Claude。需求拆分、各轮验收与复核由项目所有者完成（见各轮验收记录中的 Owner 决定）。

## 许可

Apache-2.0，见 [LICENSE](LICENSE)。第三方署名见 [NOTICE](NOTICE)，包括 changedetection.io 与 tenstorrent/tt-metal。本项目与 changedetection.io、Tenstorrent 均无隶属关系。
