# QualityM

QualityM 是本地单用户的项目质量保证记录生成工具。它从 `项目相关信息表.xlsx` 导入项目数据，保存独立 SQLite 数据库，并导出阶段质量审核记录、公司级质量审核记录和项目质量工作趋势分析。

## 启动

```bash
python3 run.py
```

打开 `http://127.0.0.1:8003`。端口固定为 8003；首次启动会创建运行数据和迁移数据库。开发与验收测试使用临时目录，不会写入实际运行数据。

## Windows 离线安装

本次交付采用全新部署：交付方提供项目代码、运行资源及本说明；部署方负责下载 Python 安装程序、准备离线依赖包、安装和 Windows 实机验收。

从 GitHub 的 `main` 分支选择 **Code → Download ZIP**，解压后进入包含 `run.py` 的目录；也可使用 Git 克隆。离线目标机无需安装 Git。请将代码解压到当前用户可写的新目录，例如 `D:\QualityM`，不要在压缩包内直接运行。

交付清单：

- `run.py`、`requirements.txt`、`README.md`。
- 完整的 `app/`（包含 `app/static/vendor/`）、`migrations/`、`seeds/`。
- `temp/工作产品检查单.xlsx`、`temp/过程检查单.xlsx`、`temp/测控辅助业务-质量审核报告-软件需求与分析阶段.xlsx`。
- 用于首次导入的项目相关信息表，由使用方按实际项目准备。

交付包不包含旧的 `qualitym.db`、`qualitym.db-*`、`generated/`、`var/`、`.venv/`、日志或本地环境配置。部署到新的空目录，首次启动自动创建数据库、执行迁移并载入内置模板；项目列表为空，需自行导入项目表。无需迁移历史导出路径。Git 提交时务必包含本地脚本 `app/static/vendor/`。

页面使用随项目携带的 Alpine.js 3.14.9（`app/static/vendor/`），不再访问 CDN。安装好 Python 和依赖后，本机运行无需互联网。以下命令在 Windows CMD 中执行。

部署环境统一使用 **64 位 CPython 3.12**，配合 Edge 或 Chrome 浏览器；桌面版 Microsoft Excel 用于导出工作簿的查看、公式重算和链接更新。生成 Excel 文件本身无需调用 Office。无需另装数据库、Node.js 或其他同族系统。

部署方先在联网电脑从 [Python 官方下载页](https://www.python.org/downloads/windows/) 准备 Python 3.12 的 **Windows installer (64-bit)** 完整安装程序，避免使用在线安装器或嵌入式压缩包。联网准备机与目标机使用相同 Python 小版本及架构；安装时保留 pip、venv 和 Python Launcher。在联网 Windows 的项目目录运行：

```bat
py -3.12 -m venv .prepare-venv
.prepare-venv\Scripts\python.exe -m pip download --only-binary=:all: --dest wheelhouse -r requirements.txt
```

确认下载成功，再拷贝完整项目、`wheelhouse/` 和对应 Python 的 Windows 完整离线安装程序到目标机。不要使用 Python 在线安装器，也不要拷贝 Mac 的 `.venv/` 或依赖包代替 Windows 依赖。项目的 `app/`（包括 `static/vendor/`）、`migrations/`、`seeds/`、`temp/`、`run.py` 和 `requirements.txt` 必须完整保留；`temp/` 中的三个 Excel 样例用于生成导出文件。

目标机安装相同版本及架构的 Python 后，例如项目放在 `D:\QualityM`，执行：

```bat
cd /d D:\QualityM
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --no-index --find-links=wheelhouse -r requirements.txt
.venv\Scripts\python.exe -m pip check
set QUALITYM_RELOAD=0
.venv\Scripts\python.exe run.py
```

打开 `http://127.0.0.1:8003`。这里的“局域网离线环境”按每台电脑本机运行部署；默认只监听本机地址，端口固定为 8003。终端窗口需保持打开；按 Ctrl+C 停止服务。以后启动时只需执行 `cd`、`set QUALITYM_RELOAD=0` 和最后一条启动命令，无需重新安装依赖。

部署验收：

1. 断开互联网后启动，访问首页及 `http://127.0.0.1:8003/api/health`，确认数据库状态为 `ready`。
2. 项目列表为空，模板库显示内置检查单。
3. 导入实际项目表（或根目录样例），保存一条审核记录。
4. 生成导出包，下载后用 Microsoft Excel 检查阶段及趋势工作簿；保持文件名和同目录关系，验证链接更新。
5. 停止并重新启动，确认已导入项目和保存的审核仍然存在。

仓库提供 Windows/Python 3.12 自动检查：下载 Windows wheel、用 `--no-index` 安装、检查依赖并运行自动化测试。请查看 GitHub **Actions → Windows deployment checks**，确认对应提交检查通过；自动检查不包含真实浏览器操作和 Microsoft Excel GUI 联动，这两项仍须在目标机验收。

常见安装问题：`py` 不存在时检查 Python Launcher 安装；“No matching distribution”时核对 wheelhouse 是否下载完整、两台机器的 Python 版本和架构是否一致；8003 已被占用时先关闭占用程序；页面脚本缺失时重新解压完整代码，确认 `app/static/vendor/` 存在。

## 日常使用

1. 在“导入”上传权威项目表，核对阶段和工作产品映射后确认。每个工作产品会直接显示本次预览固定的自动匹配检查单及来源；可手工覆盖，确认后不会重新按规则替换。
2. 新建审核从“未检查、零问题”开始。在“审核”填写结论；结论为“不符合”时可同时选择一个或多个问题模板，也可创建自定义问题。模板给出的整改建议仅作参考，实际整改措施、关闭日期和验证信息填写完整后才能确认关闭。历史审核实例保留当时的检查单快照。
3. 在“模板库”维护检查单、分组、检查项和问题模板。模板修改只影响以后创建的审核实例。
4. 在“导出”启动后台作业。完成后下载完整包及 `manifest.txt`；下载时会复核文件 SHA-256。

## 备份与恢复

停止服务后，将以下两项作为同一时点的备份保存：

- `qualitym.db`：项目版本、审核记录和全局模板；
- `generated/`：每次导出的完整工作簿包。

恢复时将二者一并放回项目根目录，再启动服务。不要只恢复数据库而丢失 `generated/`，否则历史导出下载会被标记为缺失。`temp/` 和输入样例是权威参考资料，运行期间不会被修改，不应以导出结果覆盖它们。

## 模板更新

当前“模板库”仍保留完整模板 Excel 的导出、差异预览和确认提交能力。每个检查项建议维护 2～4 条启用问题模板，至少 1 条；暂时没有模板时显示覆盖提示，但审核人员仍可创建自定义问题。每项最多新增至 4 条，历史上已超过 4 条的项目可继续使用原数据，但不能继续新增或重新启用。带星检查项的问题固定为“严重”，非星检查项只允许“轻微、一般”。模板库的变更不回写既有审核实例。

在审核中点击“保存为问题模板”时，需要显式确认并填写可复用的问题标题、描述、整改建议和严重程度；系统不会把本次审核中的责任人、日期、实际整改措施或验证结果带入模板。

面向用户的简化问题模板 Excel（只包含检查项定位和问题内容字段）将在后续里程碑提供；当前完整模板 Excel 不应当作为历史问题整理的最终格式。

## Excel 外链

趋势分析工作簿通过同目录相对外链读取各阶段审核记录。导出时同时保存统计结果、外链和图表缓存，因此首次单独打开趋势文件、不更新链接时，也能看到导出时的完整统计。下载后请保持完整目录和文件名不变，可以整体移动文件夹。

修改阶段记录后，在 Microsoft Excel 中打开阶段工作簿并重算，再打开趋势文件并允许更新链接。链接出错不再统一显示为零；没有更新链接时，显示的可能仍是上次保存的统计值。Excel 的链接界面及保存文件中的备用地址可能显示绝对路径，同目录相对链接仍然保留。`manifest.txt` 用于复核原始导出文件；在 Excel 中保存修改后，文件校验摘要会变化。

## 验证

```bash
python3 -m pytest -q
```

涉及工作簿的变更还应使用 openpyxl 重开检查结构、公式和样式；跨工作簿链接必须在真实 Microsoft Excel 中重算验证。
