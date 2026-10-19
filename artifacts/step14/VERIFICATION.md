# Step 14 最终验收记录

日期：2026-08-22  
状态：通过

## 固定夹具

- 输入：`项目相关信息表.xlsx`
- 输出：`fixtures/current-output-package/`，包含 6 份阶段质量审核记录、公司级质量保证过程审核记录、项目质量工作趋势分析和 `manifest.txt`。
- 输入与全部 9 个输出文件的 SHA-256 见 [FIXTURE_SHA256.txt](FIXTURE_SHA256.txt)。导出包必须整体移动，以保持趋势工作簿的相对外链。

## 最终验证

| 项目 | 结果 |
| --- | --- |
| 服务与 UI E2E | 隔离数据库下服务运行于 `127.0.0.1:8003`；上传预览识别 6 阶段，确认生成 52 个审核实例；保存代表性产品审核问题后，后台导出成功。 |
| 导出页面 | 页面展示 6 个阶段工作簿、公司级工作簿、趋势工作簿和 manifest；浏览器控制台无错误。截图：`../../.gstack/qa-reports/screenshots/step14-export-complete.png`。 |
| 结构与公式 | openpyxl 重开全部阶段工作簿、公司级和趋势工作簿通过；阶段目录、隐藏计算表、超链接与自动计算属性均有效；OOXML XML 中未发现 `#REF!`。 |
| 外链与图表 | 趋势工作簿含 6 条相对外链（对应 6 个阶段工作簿）和 2 个图表。 |
| 真实 Excel | Microsoft Excel 打开全部 6 个阶段工作簿及趋势工作簿，更新链接并强制重算成功；趋势首阶段统计值为 `4.0`。 |
| 自动化回归 | `.venv/bin/python -m pytest -q`：`126 passed, 1 warning in 177.66s`。唯一 warning 是 FastAPI/Starlette 对 `TestClient` 依赖的上游弃用提醒。 |

## 代表性链路

- 产品审核：`相控阵数字样机软件研制任务书` 的检查单结论和问题编辑保存成功，随后被纳入阶段导出包。
- 过程与公司级审核：导入生成的审核实例清单包含过程审核及第 37 条 `审核质量保证-公司` 公司级审核；它们随同完整快照进入公司级记录与趋势统计。

## 视觉核对

- 审核编辑页：字段、问题卡片、状态和诊断区域均正常显示，截图：`../../.gstack/qa-reports/screenshots/step14-audit.png`。
- 导出完成页：进度、文件名、大小和下载入口均可见，截图：`../../.gstack/qa-reports/screenshots/step14-export-complete.png`。

未发现阻断风险。
