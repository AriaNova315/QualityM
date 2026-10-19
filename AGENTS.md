# QualityM 开发指南

本文件是 QualityM 的长期开发约定。开始任务前应同时阅读用户要求、`DEV_PLAN.md` 和本文件；不得创建或依赖 `CLAUDE.md`。

## 项目边界

QualityM 是本地单用户的项目质量保证记录生成工具。它从 `项目相关信息表.xlsx` 导入权威项目数据，维护独立的 SQLite 数据库 `qualitym.db`，生成阶段质量审核记录、公司级质量审核记录和项目质量工作趋势分析。

- 不读取 ConfigM、EstimateM 或 MeasureM 的数据库。
- 不在运行时依赖同级项目源码；MeasureM 仅可作为工程模式参考。
- 不修改或覆盖 `temp/` 中的权威样例。
- 导出的 Excel 是数据快照，首版不回写数据库。
- 技术栈为 Python、FastAPI、SQLite/aiosqlite、Alpine.js、openpyxl 和 python-docx。

## 启动与端口

```bash
python3 run.py
```

服务端口固定为 `8003`。8000、8001、8002 已分配给同族系统，不得占用。

## 架构约束

依赖方向固定为：

```text
routers → application → domain
              ↓
       repositories / excel / importers
```

- `domain` 必须保持纯 Python，不得导入 FastAPI、aiosqlite 或 openpyxl。
- SQLite 连接统一由连接工厂创建，每个连接启用外键；迁移必须编号且不可吞异常。
- Excel builder 只消费不可变导出快照，不得逐 Sheet 查询数据库。
- 路径、Sheet 名和公式/文本边界必须经过集中校验。
- 导入 `app.main` 不得创建数据库、输出目录或业务数据。
- 所有可写路径均从 `app/config.py` 解析；测试只使用 pytest 临时目录。

## 权威资料

- `DEV_PLAN.md`：已批准的工程实施方案和 Step 0～14 验收门禁。
- `QualityM_promptinit.md`：项目初始化目标和原始约束。
- `项目相关信息表.xlsx`：输入数据权威样例。
- `temp/工作产品检查单.xlsx`：工作产品检查单格式样例。
- `temp/过程检查单.xlsx`：过程检查单及分组格式样例。
- `temp/测控辅助业务-质量审核报告-软件需求与分析阶段.xlsx`：单次审核报告与阶段工作报告格式样例。
- 趋势分析暂以用户提供的截图为视觉参照；公式正确性和可维护性优先。

## 实施与验证规则

严格按 `DEV_PLAN.md` 的 Step 顺序实现。当前 Step 0～14 已完成；未经用户明确指示不得扩展新的实施范围。每一步只在以下内容全部完成后进入下一步：

1. 实现范围内代码完成；
2. 自动化测试通过；
3. 样例或可视产物完成验收；
4. 文档和任务状态同步；
5. 未解决风险已明确汇报。

基础测试命令：

```bash
python3 -m pytest -q
```

涉及 Excel 时还必须使用 openpyxl 重开验证 OOXML 结构、关键单元格、公式和样式。Step 5 的跨工作簿联动必须通过真实 Microsoft Excel 自动重算测试，不能只验证公式字符串存在。

测试不得写入真实的 `qualitym.db`、`generated/` 或权威样例。业务测试应断言稳定的诊断代码、严重度和来源位置，不依赖完整中文提示文案。

## 任务路由

- 需求澄清与业务取舍：`/office-hours`
- 架构、数据库、跨模块方案：`/plan-eng-review`
- 系统性故障定位：`/investigate`
- 代码落地前审查：`/review`
- 页面交互与行为验收：`/qa` 或 `/qa-only`
- 视觉打磨：`/design-review`
- 保存或恢复长任务上下文：`/context-save`、`/context-restore`

如果技能要求与用户明确要求冲突，以用户要求为准；如果样例、业务规则和计划互相冲突，暂停受影响的实现并逐项确认。
