# QualityM Step 13 Lite 验收记录

日期：2026-08-22  
范围：DEV_PLAN Step 13（个人工具精简版）

## 已实现

- 新增 `app/application/export_storage.py`，集中校验导出目录、写入版本化恢复清单，并将历史导出原子移动到 `generated/.delete-quarantine/` 下的同盘隔离目录。
- 项目版本删除在同一个 `BEGIN IMMEDIATE` 事务中再次检查活动导出、预览快照和资源归属；隔离完成后才删除数据库记录。
- 数据库删除失败或事务取消时立即回移导出目录；回移仍失败时保留恢复清单并返回稳定诊断码，避免静默丢失。
- 数据库提交成功后清理隔离目录；清理失败不反转已完成删除，而是保留隔离记录供下次启动继续清理，并在 API 结果中返回 `artifact_cleanup_pending=true`。
- 应用启动时在导出 worker 启动前扫描恢复清单：项目版本仍存在则恢复原目录，项目版本已删除则完成清理；同时清理上次进程遗留的 `.staging` 内容。
- 所有历史输出目录必须是 `generated/` 的直接子目录，绝对路径、外部路径和 symlink 均不能进入删除隔离流程。

恢复协议：

```text
BEGIN IMMEDIATE
  → 校验令牌 / 快照 / 活动作业
  → 写恢复清单
  → os.replace(导出目录, 同盘隔离目录)
  → 删除 SQLite 项目版本
  → COMMIT
      ├─ 提交前失败：ROLLBACK + 回移目录
      └─ 提交后成功：清理隔离目录

应用启动
  → 扫描恢复清单
      ├─ 项目版本仍存在：回移目录
      └─ 项目版本已删除：清理目录
  → 清理遗留 staging
  → 启动导出 worker
```

## 自动化验证

- `.venv/bin/python -m pytest -q tests/test_project_versions.py tests/test_export_jobs.py`：最终组合覆盖共 `12` 项，均包含在 `126 passed` 全量回归中。
- `.venv/bin/python -m pytest -q tests/test_project_versions.py`：`9 passed`。
- `.venv/bin/python -m pytest -q`：`126 passed in 180.35s`。
- `.venv/bin/python -m compileall -q app tests`：通过。

新增覆盖：

- 删除预览后出现活动导出时，确认删除返回 `PROJECT_DELETE_EXPORT_ACTIVE`。
- 成功删除同步清理全部已完成导出目录。
- 隔离后数据库删除失败，事务回滚并恢复导出目录。
- 提交后清理失败，下一次启动根据已删除的数据库事实继续清理。
- 崩溃遗留隔离记录在项目仍存在时恢复原目录。
- 崩溃遗留 staging 在 worker 启动前清理。
- 数据库中的输出路径指向 `generated/` 外部时拒绝操作。

## 性能基线

权威样例完整包构建、结构验证和趋势工作簿重开测试：

```text
tests/test_company_trend_export.py::test_complete_package_has_manifest_company_trend_links_and_charts
setup（数据库初始化、导入、快照）：3.10s
call（完整包构建、验证、重开断言）：1.11s
总计：4.27s
```

完整包处理低于 Step 13 Lite 保留的 10 秒目标。未引入 200 实例/5000 检查项/1000 问题的部署级压力硬门禁。

## 已知边界

- 文件系统和 SQLite 无法组成真正的单一原子事务；本实现使用同盘原子 rename、持久化恢复清单和启动时数据库事实判定完成补偿。
- 提交后清理失败不会阻止个人工具继续使用；隔离内容保留到下次启动重试，API 会明确返回清理待完成状态。
- Step 13 不修改工作簿内容、公式、图表或外链，因此不重复执行 Microsoft Excel GUI 重算门禁；最终真实 Excel 验收仍属于 Step 14。
- 既有 Starlette `TestClient` 对 httpx 的弃用警告仍存在，与本步骤无关，不影响测试结果。

## 门禁状态

Step 13 Lite 验收门禁通过并关闭。未经用户明确指示不进入 Step 14。
