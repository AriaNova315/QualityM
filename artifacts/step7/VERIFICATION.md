# QualityM Step 7 验收记录

日期：2026-08-18  
范围：仅 DEV_PLAN Step 7（审核快照、确定性建议与编辑 API）

## 已实现

- 项目版本提交与审核实例、检查单层级、检查项和全部候选问题模板在同一事务中创建。
- 项目实例保存模板内容和版本快照；后续全局模板修改不会改变已有项目快照。
- 初始建议使用 SHA-256 稳定选择，每实例 0～2 条问题、最多 1 条严重问题。
- 建议问题默认带入项目组长、审核日期、计划关闭日期、整改建议和建议验证信息，`suggested_closed=1`、`confirmed=0`。
- 审核列表和详情返回分组检查单、全部候选模板、已选问题与稳定诊断。
- 支持结论修改、候选模板选择、手工新增问题、轻微/一般/严重调整、整改验证、正式确认和删除。
- 删除最后一条问题必须明确确认将检查结论改为“符合”。
- “不符合”必须至少有一条问题；“符合/不适用”不能保留问题。
- 所有编辑使用审核 revision 乐观锁；旧 revision 返回 `AUDIT_REVISION_CONFLICT`。
- 问题日期限制在审核日至阶段结束日，验证日期不得早于实际关闭日期。
- 审核确认前必须完成全部检查结论，并正式确认关闭所有问题。

## API 闭环

自动化测试通过以下路径：

```text
读取项目审核列表
→ 读取审核及候选模板快照
→ 将检查项改为“不符合”并选择候选问题
→ 将严重程度调整为“轻微”
→ 确认整改和验证事实
→ 将审核状态改为“confirmed”
```

覆盖路由：

- `GET /api/project-versions/{id}/audits`
- `GET/PATCH /api/audits/{id}`
- `PUT /api/checklist-items/{id}/conclusion`
- `POST /api/checklist-items/{id}/issues`
- `PATCH/DELETE /api/issues/{id}`

## 验证结果

- `.venv/bin/python -m compileall -q app scripts tests`：通过。
- `.venv/bin/python -m pytest -q tests/test_audit_workflow.py`：`4 passed`。
- `.venv/bin/python -m pytest -q`：`85 passed in 93.94s`。
- 全部测试使用 pytest 临时数据库；正式 `qualitym.db`、`generated/` 和 `temp/` 未修改。
- 项目不是 Git 仓库，无法运行基于 Git diff 的 `/review`；已执行本地静态检查和完整回归。

## 门禁状态

Step 7 验收门禁通过。当前停在 Step 7 之后，未经用户明确指示不进入 Step 8。
