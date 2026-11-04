# TechSignal 信息采集 MVP 交接说明

- 日期：2026-11-04
- 分支：`feature/r6-research-workflow`（PR #1，没有合并 main）
- 背景：Owner 纠偏，TechSignal 的核心产品改为「硬科技公开信息的采集与管理工具」。原有的研判功能保留为可选的「高级研究」，本轮没有扩展。

## 一、已完成

| 项目 | 状态 | 位置 |
|---|---|---|
| P0 浏览器中创建采集任务：名称、企业或关键词、标签、来源网址（每行一个，可选来源类型） | 完成 | 工作台首页「信息采集」；`techsignal/collector.py` |
| P1 真正采集：RSS 2.0 / RSS 1.0 / Atom 逐条采集；网页首次记录、之后只采集新增文字；可选经 changedetection.io 抓取网页 | 完成 | `CollectorStore.collect` |
| P1 每条信息保存：标题、原始链接、来源、发布时间（有则存）、抓取时间、摘要或摘录、命中的关键词 | 完成 | `items.jsonl` |
| P1 去重：稳定标识（guid/id、链接、标题加日期）加内容指纹；多次采集只追加新条目 | 完成 | 有离线、浏览器和真实来源测试 |
| P1 失败处理：保留历史，按来源记录失败原因（HTTP 状态、无法访问、XML 错误、超出大小） | 完成 | 「采集状态」页、`status.json` |
| P2 全中文信息流首页：按时间倒序，点击打开原始出处，按任务、来源、关键词、日期筛选，搜索，采集状态和最后更新时间，导出 CSV 和 Markdown | 完成 | `techsignal/workbench/collector_ui.py` |
| P2 研判功能移到「高级研究」入口 | 完成 | 侧栏「工作区」 |
| P3 本地持久化，重启后历史不丢失 | 完成，浏览器中实际重启验证 | `~/.techsignal/collector` |
| P3 浏览器实际操作验收 | 完成（本会话和 CI 都执行） | `docs/screenshots/collector_browser_check.js`，截图 `c01`–`c05` |
| P3 README 改为信息采集器定位；Windows 启动说明和一键启动脚本 | 完成 | `README.md`、`scripts/start_windows.bat` |
| 命令行采集和导出 | 完成 | `python -m techsignal.collector` |

## 二、哪些经过真实在线验证

| 来源 | 在哪里验证 | 结果 |
|---|---|---|
| raw.githubusercontent.com 上 tenstorrent/tt-metal 的 README（网页） | 本会话：命令行、真实浏览器（采集两次、搜索、导出、重启后再采集）；CI：`live-cdio` 和 `r6-browser` job | 第一次 1 条，第二次 0 条（没有重复）；重启后历史仍在 |
| GitHub 发布记录 Atom（`https://github.com/tenstorrent/tt-metal/releases.atom`） | 只在 CI 的 `live-cdio` job 中验证（run #16 通过，采集两次） | 通过。本会话的网络策略拒绝访问这个地址，返回 403，所以**没有**在本会话验证 |
| arXiv cs.AR RSS（`https://rss.arxiv.org/rss/cs.AR`） | 只在 CI 的 `live-cdio` job 中验证（run #16 通过） | 通过。本会话无法连接这个地址 |
| 经真实 changedetection.io 0.60.10 抓取网页 | 本会话和 CI 的在线集成测试，使用本地测试页 | 首次记录；内容未变时不新增；新增内容单独成条 |

离线测试使用 `tests/fixtures/collector/` 中的虚构 RSS/Atom 数据，只用来验证解析、去重和失败处理，**不是**实时采集结果。

## 三、运行命令

```bash
pip install -e ".[ui]"            # 安装（Windows 可以直接双击 scripts\start_windows.bat）
python -m techsignal.workbench    # 启动工作台，打开 http://localhost:8501
python -m techsignal.collector list
python -m techsignal.collector collect <任务编号>
python -m techsignal.collector export <任务编号> --csv out.csv --md out.md
pip install -e ".[test,ui]" && python -m pytest          # 离线测试
TECHSIGNAL_LIVE_NETWORK=1 python -m pytest tests/integration/test_collector_live.py   # 真实来源（需要网络）
```

环境变量：
- `TECHSIGNAL_COLLECTOR_ROOT`：数据目录。
- `TECHSIGNAL_ALLOW_PRIVATE_URLS=1`：只用于开发测试，允许采集本机地址，页面会显示提示。

## 四、已知问题与限制

- **没有定时采集和通知**：只能手动点「立即采集」，或用命令行，再配合系统自带的定时任务（Windows 任务计划程序或 cron）运行。
- **网页采集比较粗**：
  - 去掉 HTML 标签后，按行比较新增文字；不执行 JavaScript，不支持登录；
  - 对 Markdown、表格等格式化文本，摘录会显示原始符号；
  - 页面结构调整时，可能把已有内容当成新增（会合并成一条「页面更新」）。
- **订阅条目更新不重复采集**：同一条目的内容后来被修改时，不会再采集一次（按稳定标识视为已采集）。
- **单机设计**：数据存在本地文件；每次采集会重写整个 `items.jsonl`，适合几万条以内的规模；没有多用户权限。
- **网络受限时**：采集失败会如实显示原因（例如 HTTP 403、无法访问），不会使用离线数据冒充实时结果。
- **数据位置**：工作台侧栏显示了数据保存的绝对路径。
- **研判功能**（高级研究）保留 R6.1 的状态，本轮没有扩展；真实模型仍然 BLOCKED。

## 五、建议的下一步（由 Owner 决定）

1. **定时采集**：在工作台外运行的调度，例如按任务设置间隔，或由系统定时任务调用 `python -m techsignal.collector collect`。
2. **新内容提醒**：有新条目或命中关键词时，通过邮件或企业 IM 提醒。
3. **扩展来源**：sitemap、带分页的新闻列表页、GitHub Release 和 Tag 的结构化读取。
4. **数据量增大后的存储**：改用 SQLite 存储和全文检索。
