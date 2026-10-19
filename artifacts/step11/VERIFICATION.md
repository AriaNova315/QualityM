# QualityM Step 11 验收记录

日期：2026-08-21  
范围：DEV_PLAN Step 11（模板库管理与 Excel 批量交换）

## 已实现

- 005 号迁移新增 `template_import_previews`，保存令牌哈希、源文件摘要、冻结输入、差异、状态和过期时间；确认令牌只能消费一次。
- 模板查询以固定四次批量读取构建检查单 → 分组 → 检查项 → 问题模板树，空的停用新实体也能立即显示。
- 四层实体均支持新增、编辑、停用/启用、排序与内置版本恢复；问题模板另支持复制和默认项切换。所有编辑保持数据库 ID 和 stable key，版本增加并设置 `user_modified=1`。
- 新检查项初始停用；只有至少两个启用问题且恰有一个默认问题时才能启用。默认问题不能直接取消或停用，物理删除未开放。
- 页面使用嵌套 `<details>` 展示四层模板树，提供 Excel 导出、导入差异预览和确认事务提交；全局修改不会回写已有审核实例快照。

## Excel 批量交换

- [QualityM-模板库.xlsx](./QualityM-模板库.xlsx)：完整 1,288 行模板库，单 Sheet、28 列。
- [QualityM-模板库预览.pdf](./QualityM-模板库预览.pdf)：由 Microsoft Excel 对前 20 行代表性数据渲染的 9 页横向分栏预览。
- openpyxl 重开结果：`1,289 × 28`（含表头）、冻结 `A2`、筛选范围 `A1:AB1289`、8 组下拉数据验证、80% 缩放、无公式单元格。
- Excel 中保留检查单/分组/检查项/问题四层 stable key、版本、启用状态和顺序；业务文本以 literal cell 写入，避免以 `= + - @` 开头的文本被解释为公式。
- 导入校验压缩包体积、条目数、解压体积和压缩比；逐行校验稳定键、层级一致性、正整数顺序、布尔值、严重度和占位符 JSON。

## 差异与事务

- 预览报告 `added / modified / disabled / conflict`，不修改模板数据。
- 确认时在同一数据库连接和写事务内重新计算模板摘要；模板在预览后变化则返回 `TEMPLATE_IMPORT_STALE_PREVIEW`。
- 层级移动冲突、陈旧预览、不变量失败、重复提交均有稳定错误码；失败事务整体回滚。
- 1,288 行批量提交仅以固定批次读取四层实体；专项测试断言 SELECT 不超过 12 次且本机耗时低于 5 秒。

## 浏览器 QA

- QA 报告：[qa-report-qualitym-2026-08-21.md](../../.gstack/qa-reports/qa-report-qualitym-2026-08-21.md)。
- 验证桌面折叠树、问题停用/恢复、Excel 无差异预览与确认、新增检查单和 375px 移动布局。
- 发现并修复两项：Alpine runtime 早于应用状态工厂初始化；移动端批量导入按钮溢出。
- 最终健康分 `100/100`，控制台无错误。

## 自动化验证

- `.venv/bin/python -m pytest -q tests/test_template_ui.py tests/test_scaffold.py tests/test_template_library.py`：`14 passed`。
- 模板、数据库迁移及既有审核快照组合专项：`22 passed`（页面补齐前执行，随后相关专项再次通过）。
- `.venv/bin/python -m pytest -q`：`111 passed in 149.60s`。

## 门禁结论

Step 11 验收门禁通过：全局模板变更与已有审核实例快照隔离；批量导入差异可复算，确认提交具备原子性和一次性语义。下一步为 Step 12，需用户明确指示后才能进入。
