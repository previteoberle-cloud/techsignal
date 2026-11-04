# TechSignal 信息采集器：交接说明（最终收口）

- 日期：2026-11-04
- 仓库：`previteoberle-cloud/techsignal`，分支 `feature/r6-research-workflow`，PR #1（没有合并 main）
- 产品定位（固定）：硬科技企业、行业和技术公开信息的采集、增量去重、归档、搜索、导出工具。研判功能保留为可选的「高级研究」入口，不再开发。

## 1. 最新稳定提交

| 提交 | 内容 | GitHub CI |
|---|---|---|
| `5de01a8` | **最新代码提交**：批量采集、计划任务脚本、默认只监听本机、Windows 脚本加固 | run #22：1 个测试因缺少 Streamlit 失败（测试问题），已在其后的提交修复，见第 9 节 |
| `2735a93` | 数据一致性修复（第 3 节） | run #20：6 个 job 全部通过 |
| `0e125dc` | 采集器 MVP 验收版本 | run #18 / #19：全部通过 |

本文档和 README 所在的提交位于 `5de01a8` 之后，只修改文档。接手时以 PR #1 的 head 为准。

## 2. 采集器已实现的能力

- **采集任务**：在浏览器首页「信息采集」中填写名称、关注的企业或关键词、分类标签和公开来源网址即可创建，不需要写代码、JSON 或正则。
- **采集方式**：
  - RSS 2.0、RSS 1.0（RDF）、Atom 订阅源：逐条采集；
  - 普通网页：第一次记录页面，之后只记录新增的文字；
  - 网页也可以选择经用户自己的 changedetection.io 抓取。
- **每条信息保存**：标题、原始链接、来源、发布时间（有则存）、抓取时间、摘要或摘录、命中的关键词。
- **去重**：按稳定标识（guid/id、链接、或标题加日期）和内容指纹去重；多次采集只追加新条目；不同来源的同一内容只保存一次。
- **失败处理**：保留全部历史，按来源记录失败原因。
- **安全**：只采集公开地址，重定向时也会检查；拒绝带 DOCTYPE/ENTITY 声明的 XML；单个来源最多 5 MB、超时 20 秒。
- **信息流**：按时间倒序；按任务、来源、关键词、日期筛选；全文搜索；点击标题打开原始出处；显示采集状态和最后更新时间。
- **导出**：CSV（带 BOM，Excel 打开中文不乱码）和 Markdown，导出的是当前筛选结果。
- **存储**：本地文件，重启后历史不丢失。
- **命令行**：`list`、`collect <任务编号>`、`collect-all`、`export <任务编号>`。
- **定时采集**：通过 Windows 任务计划程序（默认关闭，见第 4 节）。
- **工作台默认只监听 127.0.0.1。**

## 3. 数据一致性修复及反例

故障注入测试位于 `tests/test_collector.py`：

| 反例 | 修复前 | 修复 |
|---|---|---|
| 网页有变化，但归档文件 `items.jsonl` 写入失败（模拟磁盘已满） | 网页快照已经更新，重试时与新快照比较，这次变化**永远丢失**（重试新增 0 条） | 网页快照改为在归档成功**之后**才写入。重试时会重新发现这次变化，而且只归档一次 |
| 归档成功，但随后快照写入失败 | （修复后新增的情况） | 下次采集会重新生成同一条目。条目编号由内容哈希得出，所以会被去重跳过，**不会重复归档**，快照随之补齐 |
| 订阅源的条目在归档失败后重试 | 本来就不会丢（订阅条目不依赖快照） | 补了测试，确认这一点 |
| 一次采集持续超过 120 秒（来源多时可能发生） | 锁被当作已废弃，另一次采集（例如计划任务）会同时运行 | 采集锁要超过 1 小时才视为已废弃；正在采集时，另一次会被拒绝（界面提示）或跳过（批量日志） |
| 4 个线程同时采集同一个任务 | — | 测试确认历史记录不损坏、不重复 |

## 4. 自动采集的启用与关闭（Windows 任务计划程序）

默认**不会**创建任何计划任务。在项目目录中执行：

```bat
scripts\windows_schedule.bat enable 6
scripts\windows_schedule.bat status
scripts\windows_schedule.bat run
scripts\windows_schedule.bat disable
```

- `enable 6`：每 6 小时采集一次全部任务（可设 1–24 小时），仅在当前用户登录时运行。
- `status`：查看计划任务状态。
- `run`：立即运行一次，与计划任务执行的命令相同。
- `disable`：删除计划任务，不删除数据。

计划任务的细节：
- 名称为 `TechSignal-Collector`，执行 `.venv\Scripts\pythonw.exe -m techsignal.collector collect-all`（不弹出命令行窗口）。
- 运行日志在 `%USERPROFILE%\.techsignal\collector\logs\collect.log`。
- 退出码：0 表示全部成功；1 表示有来源失败，或有任务因正在采集而被跳过。

macOS 和 Linux 可以用 cron 执行同一条命令。

## 5. Windows 启动与本地验收步骤

**状态：Windows 实机待验收。** 本项目的开发环境和 CI 都没有 Windows 机器。已经做过的检查：
- 静态检查：`tests/test_windows_scripts.py`，每次 CI 都会运行；
- 在 Linux 上执行脚本调用的命令，包括使用带空格和中文的数据目录。

请在一台 Windows 10 或 11 上按以下步骤验收：

1. 把仓库放在**带空格和中文**的路径下，例如 `C:\Users\张三\Desktop\TechSignal 测试`。
2. **没有安装 Python 时**，双击 `scripts\start_windows.bat`，应提示「没有找到 Python」并停止。
3. 安装 Python 3.11 或以上（勾选 Add to PATH）后再双击：应创建 `.venv`、安装依赖，并启动工作台。在浏览器打开命令行显示的地址。
4. 在首页新建任务，例如来源填写 `https://github.com/tenstorrent/tt-metal/releases.atom`。点「立即采集」两次，第二次应显示「新增 0 条」。然后导出 CSV，用 Excel 打开确认中文正常。
5. 关闭窗口后再次双击启动脚本：应直接启动，信息仍在。
6. 在另一台电脑上访问 `http://<本机IP>:8501`，应**无法访问**（只监听本机）。
7. 执行 `scripts\windows_schedule.bat enable 1`，然后执行 `status`；等计划任务运行一次后，检查 `collect.log` 有没有新增记录。最后执行 `disable`。
8. 可选：断网后安装依赖，或删掉 `.venv` 中的部分文件，确认脚本提示失败，而不是假装启动成功。

## 6. 真实外部来源测试结果

| 来源 | 在哪里验证 | 结果 |
|---|---|---|
| raw.githubusercontent.com 上 tenstorrent/tt-metal 的 README（网页） | 云开发环境：命令行、`collect-all`（数据目录带空格和中文）、真实浏览器（采集两次、搜索、导出、重启后再采集）；CI 的 `live-cdio` 和 `r6-browser` job | 第一次新增 1 条，之后不重复；重启后数据仍在 |
| GitHub 发布记录 Atom：`https://github.com/tenstorrent/tt-metal/releases.atom` | CI 的 `live-cdio` job，采集两次（run #16 起每次运行） | 通过。在云开发环境中被网络策略拒绝（HTTP 403），日志如实记录 |
| arXiv cs.AR RSS：`https://rss.arxiv.org/rss/cs.AR` | CI 的 `live-cdio` job | 通过。在云开发环境中无法连接 |
| 经真实 changedetection.io 0.60.10 抓取网页 | 在线集成测试（本地测试页） | 通过 |

离线测试使用的 `tests/fixtures/collector/*.xml` 是虚构数据，只用来验证逻辑。

## 7. 当前仍未实现的功能

- **通知**：没有邮件、企业 IM 等提醒（Owner 明确不需要）。
- **网页采集比较粗**：
  - 按行比较新增文字；不执行 JavaScript，不支持登录；
  - Markdown 等格式会保留原始符号；
  - 页面改版时，可能把已有内容当成新增。
- **订阅条目更新**：同一条目的内容后来被修改时，不会再采集一次。
- **存储规模**：数据存在本地 JSONL 文件，每次采集会重写整个 `items.jsonl`，适合几万条以内；没有多用户和权限管理。
- **定时运行**：只在用户登录时运行，没有在后台以系统服务方式运行。
- **Windows 实机验收**：尚未进行（见第 5 节）。
- **高级研究（研判）**：保持 R6.1 的状态；真实模型仍为 BLOCKED。

## 8. 给后续接手者（Opus / Codex）的最小交接

**关键文件：**
- `techsignal/collector.py`：全部采集逻辑，包括任务存储、抓取、解析、去重、批量采集、导出和命令行。
  - 写入顺序（不要改）：`items.jsonl` → `pages/*.txt` → `status.json`，见 `collect()` 中的说明。
  - 锁：每个任务一个 `.collect-lock` 目录，超过 1 小时才视为已废弃。
- `techsignal/workbench/collector_ui.py`：首页界面。`techsignal/workbench/app.py` 负责切换「工作区」。
- `scripts/start_windows.bat`、`scripts/windows_schedule.bat`：Windows 启动和计划任务。

**测试：**
- 离线：`tests/test_collector.py`、`tests/ui/test_collector_app.py`、`tests/test_windows_scripts.py`。
- 在线：`tests/integration/test_collector_live.py`，需设置 `TECHSIGNAL_LIVE_NETWORK=1`。
- 浏览器：`docs/screenshots/collector_browser_check.js`，CI 的 `r6-browser` job 会运行。

**常用命令：**

```bash
pip install -e ".[test,ui]" && python -m pytest
TECHSIGNAL_LIVE_NETWORK=1 python -m pytest tests/integration/test_collector_live.py
python -m techsignal.workbench
```

**约束：**
- 不要删除 `techsignal/`（研判）、`cases/` 和 `demo/` 的代码与输出，测试依赖它们逐字节不变。
- 不要依赖付费 API 或未经授权的模型。
- 网络受限时，如实报告失败，不得用离线数据冒充实时采集。

**建议的下一步（需 Owner 决定）：**
1. 在 Windows 实机完成第 5 节的验收。
2. 数据量增大后，改用 SQLite 存储并支持全文检索。
3. 增加来源类型：sitemap、新闻列表页、GitHub Release 结构化读取。
4. 订阅条目被修改时，记录为「已更新」。

## 9. 本轮验证结果

- 本地离线测试：429 passed, 13 skipped。跳过的是需要网络、changedetection.io 或授权模型的测试，它们在 CI 中运行。
- 本地在线测试：真实网页采集、`collect-all`、经真实 changedetection.io 抓取网页，均通过。
- GitHub CI run #22（提交 `5de01a8`）：**失败**，只有 1 个测试，原因是测试本身的问题。新增的「默认只监听本机」测试导入了 Streamlit，而 CI 的 `test` job 不安装界面依赖（本地装了，所以没发现）。已改为没有 Streamlit 时跳过；这个测试在 `ui` job 和本地仍会运行。采集器代码没有改动。修复后的 CI 结果以 PR #1 页面为准。
