# QualityM Step 10 验收记录

日期：2026-08-21  
范围：DEV_PLAN Step 10（后台导出作业基础能力）

## 已实现

- 004 号迁移将导出作业升级为 `queued → snapshotting → building → validating → completed/failed` 状态机；历史 `running` 作业迁移为 `building`。
- `idx_export_jobs_one_active_per_version` 部分唯一索引保证一个项目数据版本只有一个活动作业。
- `ExportJobManager` 在应用 lifespan 中启动，使用有界 asyncio 队列和单线程 executor；线程只接收 `ExportSnapshot`，不接收数据库连接。
- 应用启动将全部未完成作业标为 `failed`，写入稳定诊断码 `EXPORT_INTERRUPTED`；失败作业可通过重试 API 重新入队。
- 创建 API 强制 `Idempotency-Key`；同 key 返回原作业，异 key 的并发活动作业返回 `EXPORT_ACTIVE_JOB_EXISTS`。
- 构建在 `generated/.staging/<job id>/` 下完成，阶段/公司级/趋势结构校验通过后才以原子 rename 发布；最终目录保留时间戳并附加作业 ID。
- artifact 下载只接受数据库清单中的同级文件名，且在返回前核对 SHA-256；路径穿越、文件丢失和篡改分别返回稳定错误码。

## API 验收

| 操作 | 路由 |
|---|---|
| 创建 | `POST /api/project-versions/{project_version_id}/exports` |
| 查询 | `GET /api/exports/{job_id}` |
| 重试 | `POST /api/exports/{job_id}/retry` |
| 下载 | `GET /api/exports/{job_id}/artifacts/{filename}` |

端到端 API 测试导入权威输入后创建真实导出作业、轮询至 `completed`，并下载 `manifest.txt`。完成作业含 9 个数据库 artifact（6 个阶段工作簿、公司级工作簿、趋势工作簿和清单）。

## 自动化验证

- `.venv/bin/python -m pytest -q tests/test_export_jobs.py`：`3 passed`。
- `.venv/bin/python -m pytest -q tests/test_export_jobs.py tests/test_database.py tests/test_scaffold.py`：`22 passed`。
- `.venv/bin/python -m pytest -q`：`102 passed in 140.46s`。
- 覆盖 idempotency、并发活动作业冲突、合法/非法状态转换、重启恢复、失败重试、真实构建、清单下载、篡改、缺失路径与路径穿越。

## 已知边界

- 后台作业仅使用 openpyxl 生成普通导出文件，不依赖 Microsoft Excel；Step 5/9 的本机 Excel 联动门禁仍单独保留，未被自动导出作业绕过。
- Step 10 不包含 UI 进度页、模板库或部署级多进程队列，这些工作留给后续步骤。

## 门禁状态

Step 10 验收门禁通过。下一步为 Step 11；未经用户明确指示不得进入。
