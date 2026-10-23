# 全新 Windows 离线部署交付验证

日期：2026-10-23。

## 交付内容

- Alpine.js 3.14.9 固定版本与 MIT 许可证随 app/static/vendor 提供，页面运行不再依赖 CDN。
- 三个导出所需 Excel 模板、迁移、种子、项目表样例均在 Git 中。
- README 说明 GitHub ZIP 下载、Windows/Python 3.12 x64 环境、wheelhouse 准备、离线安装、启动、验收和常见问题。
- .gitattributes 固定文本换行，Excel/PDF/图像按二进制处理；忽略离线依赖准备目录及 wheelhouse。
- 没有提交 qualitym.db、generated、var、本机虚拟环境或本地环境配置。
- 保留此前的趋势统计与相对外链修复；相关真实 Mac Excel 验证见 ../trend-link-fix/VERIFICATION.md。

## 本次验证

1. 从实际 Git 暂存区生成树并使用 git archive 导出到独立系统临时目录；确认运行资源完整、没有数据库和历史导出。
2. 在导出目录运行全量 pytest：152 passed，1 个 FastAPI/Starlette 测试客户端的上游弃用警告。解释器为本机 CPython 3.14；使用已有虚拟环境依赖，不等同于 Windows 安装验证。
3. 从导出目录执行 run.py，关闭热重载，监听 127.0.0.1:8003。健康检查数据库 ready；首页、本地 Alpine.js、内置模板列表、空项目列表均返回 200。检查结束后停止临时服务。
4. JavaScript 语法检查、pip check、暂存区 git diff --check 均通过。
5. 新增首次启动/重启集成测试和公式缓存的三个回归用例：关闭 ZIP 句柄后才替换；写入数值/文本缓存并保留公式；目标单元格缺失或文件替换失败时保留原文件并清理临时文件。
6. 独立只读审查未发现新的部署阻塞项。

业务测试和启动检查只使用临时目录，未改写真实数据库、generated 或 temp 权威样例。

## Windows 验证边界

新增 GitHub Actions：Windows x64 / CPython 3.12，先下载 wheelhouse，再通过 --no-index 安装，执行 pip check 和全量 pytest。执行结果以 GitHub 对应提交的 Windows deployment checks 为准。

自动化测试涵盖数据库/API/工作簿结构及缓存，不包括真实浏览器交互和 Microsoft Excel GUI 重算联动。部署人员仍应按 README 在目标机完成离线操作验收。

## Windows 首轮发现与修正

Windows/Python 3.12.10 已实际完成依赖下载、--no-index 安装和 pip check。首轮完整测试为 149 passed、3 failed，运行约 19 分钟，工作流触及 20 分钟时限。三个失败分别是：

- 规范 JSON 写入使用平台默认换行，Windows 生成 CRLF，而版本化种子固定 LF。修正写入函数显式使用 LF，保持种子字节与版本指纹一致。
- 种子校验脚本将 sqlite3 事务上下文误作连接关闭，Windows 无法删除仍打开的临时数据库。使用 contextlib.closing 显式关闭连接。
- HTTP 导出测试以固定 200 次、50ms 间隔轮询，Windows runner 导出尚未结束就超过次数。改为单调时钟 180 秒明确时限、500ms 间隔，保持 completed/failed 状态及下载校验断言。

三个对应失败用例在本地修正后均通过（3 passed）。Windows 工作流先运行这三个用例，再执行全量测试，任务时限调整为 45 分钟并输出慢测试耗时。后续执行结果仍以对应提交的 Actions 为准。
