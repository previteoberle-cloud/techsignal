# TechSignal R5 验收报告：独立项目提取与作品集交付

- 日期：2026-11-04
- 来源：`previteoberle-cloud/changedetection.io` 分支 `claude/affectionate-goldberg-u18nzv` 的 `techsignal/` 目录（PR #1，保持打开，未合并）

## 一句话结论

TechSignal 已提取为**能独立运行的项目**：

- 历史用 `git subtree split` 提取，保留 R1–R5 的提交、作者和日期；
- 仓库根目录直接就是 `pyproject.toml`、`techsignal/`、`tests/`、`cases/`、`docs/`；
- **不含任何 changedetection.io 源码**；
- 在一个新克隆、新虚拟环境、断开外部网络的环境里，安装、离线测试、工作台（真实浏览器）、离线回放都已实测通过；
- changedetection.io 只是可选的外部服务，或固定版本的可选开发依赖。

**没有创建新的 GitHub 仓库**：本会话不允许使用创建仓库的工具，GitHub 访问也只限于原 Fork。我交付了一个可直接推送的 git bundle 和操作步骤，见第七节。

## 一、提取方式与历史

- `git subtree split --prefix=techsignal` 生成只含 `techsignal/` 历史的提交链，再导入新仓库的 `main` 分支，**没有改写、删除原 Fork 的任何历史，也没有在 Fork 中新建分支**。
- 保留下来的提交：R1、R1.1、R2 三个、R3 四个、R4 三个，以及本轮的两个 R5 提交（数量以独立仓库 `git log` 为准）。作者字段均为「Claude」，与原始提交一致，未改写。
- 原 Fork 中只改动根目录 `.github/` 的提交（`785d165`，让 CI 打印补证轨迹）不在 `techsignal/` 下，所以**不在**独立仓库中。这是 subtree split 的正常行为。
- 原 PR #1 保持打开，未合并、未关闭。

## 二、消除对原仓库结构的依赖

| 位置 | 原依赖 | 现在 |
|---|---|---|
| `techsignal/live.py` | 在父目录找 `changedetection.py` | 通过 `techsignal/upstream.py` 定位：先看环境变量 `TECHSIGNAL_CDIO_SOURCE`，再看父目录（仅当身处 Fork 中）。找不到时给出明确提示 |
| `techsignal/fixtures.py` | 读取父目录的 `changedetectionio/changes_report.py` | 同上；`--check` 会先核对上游文件 sha256 是否等于固定版本（`5e086dd`，0.60.10） |
| 测试 | `test_adapter` 中 2 个上游契约测试、`tests/integration` 中 4 个在线测试依赖 Fork 布局 | 没有上游源码时**跳过并说明原因**（不删除）；`upstream-contract` 和 `live-cdio` 两个 CI job 会先拉取固定上游版本再运行它们 |
| 工作台案例目录 | 依赖包内的相对路径 | 依次查找：环境变量 `TECHSIGNAL_CASES_ROOT`、项目目录下的 `cases/`、当前目录下的 `cases/`（非可编辑安装时用） |
| CI | Fork 根目录的 workflow 使用 `working-directory: techsignal` | 新增独立仓库自己的 `.github/workflows/ci.yml`（test 3.11/3.13、ui、upstream-contract、live-cdio）；Fork 中新增 `standalone-layout` job，把 `techsignal/` 单独拷贝到空目录测试 |
| 文档 | 有 `cd techsignal` 和 `docs/…` 等基于 Fork 根目录的链接 | 新 README 面向独立仓库；旧 README 移到 `docs/TECHNICAL_REFERENCE.md` 并修正链接；R1–R4 的历史记录保持原样，README 中说明了其中的路径指的是 Fork 布局 |
| 上游源码获取 | 无 | `scripts/fetch_changedetection.sh` 浅克隆 dgtlmoon/changedetection.io 的固定提交，并校验 sha256。已实测，常量与 `upstream.py` 一致（有测试保证） |

## 三、两个交付问题

**1. 过时表述。**

- 已修正：
  - `pyproject.toml` 的描述「R1 research prototype」，版本号改为 0.5.0；
  - 包说明（`__init__.py`）；
  - 适配器中「R1 不含在线客户端」的说明；
  - Provider 中「R1 tests it only…」的说明。
- 新 README 保留了全部真实限制：
  - 没有真实大模型；
  - 没有持续监控；
  - 只有一个真实案例；
  - 研究判断需要手写 JSON；
  - 工作台不适合直接部署到公网或多用户环境。

**2. API key 与本地安全。**

- 改动：`cli.main(argv, api_key=..., stream=...)`。工作台的「在线连接」和在线测试脚本都**显式传入** key 和日志流，**不再修改 `os.environ`，也不再重定向 `sys.stdout`**。
- 命令行仍通过环境变量 `TECHSIGNAL_CDIO_API_KEY` 读取 key，供人工使用。
- **反例验证：** 新增的并发测试中，三个会话用三个不同的 key 同时拉取。在 R4 的旧代码上，有会话的运行日志**为空**（被写进了另一个会话的缓冲区）；在新代码上通过。每个 key 收到的请求数相同。
- **不泄漏：** 测试检查了工作目录下所有输出文件（运行记录、证据、简报、决策日志、补证轨迹）和运行日志，key 均未出现；key 错误时，报错信息里也没有 key（`鉴权失败`）。
- 工作台仍然只面向本机使用，没有增加任何公网部署或登录功能。

## 四、测试调整说明（R5）

| 文件 | 调整 | 原因 |
|---|---|---|
| `tests/integration/test_live_cdio.py` | 新增跳过条件：找不到 changedetection.io 源码时跳过 | 独立仓库默认没有上游源码；CI 的 `live-cdio` job 会先拉取再运行 |
| `tests/test_adapter.py` | 只改了跳过提示文字，指向获取脚本 | 原文字引用了 Fork 路径 |
| 新增 `tests/test_r5_portability.py`（8 个） | 上游定位、模拟独立克隆运行、案例目录回退、显式传 key、并发会话隔离、错误不回显 key、脚本常量一致 | R5 要求 |

**没有删除任何测试。**

## 五、干净环境验收（本会话实测）

环境：从 bundle 新克隆 → 新建 `.venv` → `pip install -e ".[test,ui]"`。之后把 `HTTP(S)_PROXY` 指向一个不可达地址、只放行 localhost，以确认确实没有外部网络（访问 pypi.org 时连接被拒）。没有设置 `TECHSIGNAL_CDIO_SOURCE`，没有 Docker，也没有模型密钥。

| 项目 | 结果 |
|---|---|
| A. 安装 | 可编辑安装成功；非可编辑安装（`pip install ".[ui]"`）后，`verify`、`workbench`、`adapters` 子包和 `workbench/app.py`、`upstream.py` 都已打包；在克隆目录中启动可以找到两个案例 |
| 离线全量测试 | **222 passed, 6 skipped**。跳过的是 2 个上游契约测试和 4 个在线测试，原因已标明 |
| 场景评测 | 23/23 |
| CLI 演示 | 运行通过，引用校验通过 |
| B. 离线工作台（真实 Chromium） | 首屏、跨批次总览、管理层速览、变化时间线、补证过程、研究判断、证据详情均无横向溢出；点击证据编号能跳到对应详情；完整轨迹能展开；能下载 `techsignal_tenstorrent-blackhole-70b-cross-batch_batch2.md`；离线回放显示「各批次判断与已保存的实测结果一致」；页面无脚本错误 |
| C. 获取固定上游后 | `scripts/fetch_changedetection.sh` 获取成功且 sha256 一致；`fixtures --check` 显示「与固定版本一致」并通过；`test_adapter` 12 passed；**在线集成 4 passed**（真实 changedetection.io 和真实 GitHub）；全量 224 passed, 4 skipped（在线测试在未设置环境变量时跳过） |
| Python 版本 | 本地只有 3.13。3.11 由 Fork CI 的 `standalone-layout` job 覆盖，以及推送后独立仓库自己的 CI |

## 六、演示与作品集材料

- README：面向求职展示，回答七个问题——解决什么、与上游的区别、独立实现了哪些、架构与数据流、快速开始、测试与量化结果、尚未实现。另附简短英文介绍、关键设计决策、真实案例截图和开发方式说明。
- 演示讲解稿：[DEMO_SCRIPT.md](DEMO_SCRIPT.md)，约 2–3 分钟，覆盖「原始判断 → 新证据 → 判断受影响 → 补证 → 保留不确定性 → 证据轨迹」，不包含任何关于模型自主决策的说法。
- 许可与署名：`LICENSE`（Apache-2.0）、`NOTICE`（changedetection.io 与 tenstorrent/tt-metal 的版权、许可证与使用说明；说明本项目不含上游源码，不使用其品牌）。

## 七、GitHub 仓库状态与操作步骤

- **没有创建**新仓库：本会话禁止使用创建仓库的工具，GitHub 访问也限定在原 Fork。
- 交付物：`techsignal.bundle`（git bundle，含 `main` 分支的完整历史），在会话中以文件形式发送。

你在自己的电脑上需要执行：

1. 在 GitHub 网页新建一个空仓库 `techsignal`（不要勾选生成 README、LICENSE 或 .gitignore）。
2. 在终端执行：

```bash
git clone techsignal.bundle techsignal
cd techsignal
git remote set-url origin git@github.com:previteoberle-cloud/techsignal.git
git push -u origin main
```

3. 推送后，`CI` workflow 会运行四个 job：test 3.11/3.13、ui、upstream-contract、live-cdio。

## 八、仍然存在的限制

- 没有真实大模型推理；没有持续后台监控和通知；
- 只有一个真实研究案例；研究判断、规则和白名单需要手写 JSON；
- 工作台没有登录与权限，没有做安全加固，不适合直接部署到公网或多用户环境；
- 补证工具只覆盖 changedetection.io 和 GitHub。
