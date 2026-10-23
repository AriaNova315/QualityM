# QualityM 工程实施计划

状态：已批准，Step 0～14 已完成  
生成日期：2026-08-12  
依据：已批准的 `/office-hours` 设计文档与 `/plan-eng-review` D1-D12 决策

## 1. 实施目标

从空仓库搭建一个运行于 `http://127.0.0.1:8003` 的单用户本地 Web 工具，完整实现：

```text
项目相关信息表.xlsx
  → 只读解析与诊断
  → 项目数据版本与审核实例
  → 分组检查单与问题模板选择
  → 整改、验证和统计
  → 阶段综合质量审核工作簿
  → 公司级质量审核工作簿
  → 项目质量工作趋势分析
```

最终功能范围不删减，但按纵向闭环分阶段实施。最危险的 Excel 公式和外部链接在前期验证，避免完成全部 UI 后再返工。

## 2. 已锁定工程决策

1. FastAPI + aiosqlite + SQLite 模块化单体。
2. Alpine.js + ES Modules，不引入 Node 构建链。
3. 纯业务核心不访问数据库；应用服务负责编排；仓储负责 SQL；Excel builder 只消费不可变导出快照。
4. 使用编号 SQL migration；每个 SQLite 连接显式执行 `PRAGMA foreign_keys = ON`。
5. 检查单模板采用“检查单 → 分组 → 检查项 → 多个问题模板”四层模型。
6. 内置模板使用版本化 JSON 种子；SQLite 保存可编辑副本；项目保存不可变快照；Excel 用于模板库批量交换。
7. Excel 采用 MeasureM 的 builder 分层、共享样式和强制重算模式，同时复用权威样例 Sheet 版式。
8. 跨工作簿链接先做最小原型；普通 openpyxl 不足时只在隔离模块中处理 OOXML 外链关系。
9. 校验统一返回结构化诊断对象，只有 `blocker` 阻止提交或导出，`warning` 只提示。
10. 导出先读取一次一致性 `ExportSnapshot`，再从内存生成整个输出包。
11. 导出使用进程内后台作业和有界线程池，不引入 Celery/Redis。
12. 自动化测试分为纯函数、SQLite/API、OOXML/工作簿结构和真实 Microsoft Excel 冒烟四层。

## 3. 总体架构

```text
┌──────────────────────────────────────────────────────────────────┐
│ Alpine.js 页面模块                                               │
│ projects │ import-preview │ audit-editor │ templates │ exports   │
└───────────────────────────────┬──────────────────────────────────┘
                                │ JSON / multipart
┌───────────────────────────────▼──────────────────────────────────┐
│ FastAPI routers                                                  │
│ projects │ imports │ audits │ templates │ exports               │
└───────────────────────────────┬──────────────────────────────────┘
                                │ command/query DTO
┌───────────────────────────────▼──────────────────────────────────┐
│ application services                                             │
│ import workflow │ audit workflow │ template workflow │ export job│
└────────────────┬───────────────────────────────┬─────────────────┘
                 │                               │
┌────────────────▼──────────────┐  ┌────────────▼──────────────────┐
│ repositories / SQLite         │  │ domain（纯函数）              │
│ migrations + transactions     │  │ WBS/映射/编号/日期/统计/诊断  │
└────────────────┬──────────────┘  └────────────┬──────────────────┘
                 │                               │ ExportSnapshot
                 └───────────────────────────────▼──────────────────┐
                                                   Excel adapters   │
                             templates + builders + OOXML link layer│
                                                   + validators     │
                                                   └────────────────┘
```

依赖只允许由外向内：`routers → application → domain`。`repositories` 和 `excel` 是适配器，不允许领域函数反向导入 FastAPI、aiosqlite 或 openpyxl。

## 4. 目标目录结构

```text
QualityM/
├── AGENTS.md
├── DEV_PLAN.md
├── run.py
├── requirements.txt
├── pytest.ini
├── migrations/
│   ├── 001_initial.sql
│   └── 002_seed_tracking.sql
├── seeds/
│   ├── schema/checklist-library.schema.json
│   ├── product-checklists/*.json
│   └── process-checklists/*.json
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── diagnostics.py
│   ├── safety.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── wbs.py
│   │   ├── activity_rules.py
│   │   ├── mapping.py
│   │   ├── numbering.py
│   │   ├── calendar.py
│   │   ├── issue_generation.py
│   │   └── trend.py
│   ├── repositories/
│   │   ├── projects.py
│   │   ├── templates.py
│   │   ├── audits.py
│   │   └── exports.py
│   ├── services/
│   │   ├── import_service.py
│   │   ├── audit_service.py
│   │   ├── template_service.py
│   │   ├── snapshot_service.py
│   │   └── export_service.py
│   ├── importers/project_workbook.py
│   ├── routers/
│   │   ├── projects.py
│   │   ├── imports.py
│   │   ├── audits.py
│   │   ├── templates.py
│   │   └── exports.py
│   ├── excel/
│   │   ├── templates.py
│   │   ├── styles.py
│   │   ├── formulas.py
│   │   ├── sheet_names.py
│   │   ├── checklist_builder.py
│   │   ├── audit_report_builder.py
│   │   ├── phase_report_builder.py
│   │   ├── company_builder.py
│   │   ├── trend_builder.py
│   │   ├── external_links.py
│   │   ├── package_builder.py
│   │   └── validator.py
│   └── static/
│       ├── index.html
│       ├── css/app.css
│       └── js/
│           ├── api.js
│           ├── state.js
│           ├── pages/*.js
│           └── components/*.js
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── workbook/
│   ├── e2e/
│   ├── excel_live/
│   └── fixtures/
├── scripts/
│   ├── extract_checklist_seeds.py
│   ├── validate_seed_library.py
│   └── excel_smoke.applescript
├── generated/
└── temp/                       # 权威样例，只读
```

具体文件可以在实现时按职责合并，但不得把导入、业务生成和 Excel 排版重新塞入单个巨型模块。

## 5. SQLite 数据模型

### 5.1 项目版本域

```text
projects
  id, project_code, project_name

project_versions
  id, project_id, version_no, source_filename, source_sha256,
  imported_at, config_item_number(default '0.0'), project_leader,
  qa_person, raw_metadata_json

phases
  id, project_version_id, wbs, name, start_date, end_date, sort_order

tracking_nodes
  id, phase_id, source_wbs, node_type('双周'|'阶段'), node_date, sort_order

config_items
  id, project_version_id, source_seq, name, identifier, version,
  item_type, phase_wbs, review_date

quality_activities
  id, phase_id, source_wbs, name, audit_type('product'|'process'|'company'),
  start_date, end_date, auditor, linked_items_json, sort_order
```

`projects` 表示项目身份；相同项目编号的每次导入创建新的 `project_versions`，不合并旧版本。版本号在同一项目下单调递增。

### 5.2 全局检查单模板域

```text
checklist_templates
  id, stable_key, name, audit_kind, builtin_version, enabled,
  user_modified, source_sheet, display_order

checklist_groups
  id, checklist_template_id, stable_key, name, version,
  enabled, user_modified, display_order

checklist_items
  id, checklist_group_id, stable_key, display_no, content, starred,
  version, enabled, user_modified, display_order

issue_templates
  id, checklist_item_id, stable_key, version, title, description,
  corrective_suggestion, default_severity, placeholders_json,
  is_default, enabled, user_modified, display_order

mapping_rules
  id, target_kind, match_kind, pattern, checklist_template_id,
  priority, source('builtin'|'user'), enabled

seed_imports
  id, seed_version, seed_sha256, applied_at
```

数据库约束：

- 稳定 key 在相应父作用域内唯一。
- 每个启用检查项最多一个启用默认问题模板。
- 服务层阻止停用后导致检查项少于两个启用问题模板；数据库保留基本唯一性和外键约束。
- 已被项目快照引用的全局模板只允许停用，不物理删除。

### 5.3 审核记录域

```text
audit_instances
  id, project_version_id, phase_id,
  activity_id, config_item_id(nullable for process), audit_kind,
  report_scope('phase'|'company'), process_code, checklist_template_id,
  sequence_no, audit_date, tracking_node_id, auditor,
  confirmation_state, revision, created_at, updated_at

checklist_instance_groups
  id, audit_instance_id, source_group_id, stable_key,
  name_snapshot, version_snapshot, display_order

checklist_instance_items
  id, instance_group_id, source_item_id, stable_key,
  display_no_snapshot, content_snapshot, starred_snapshot,
  conclusion('符合'|'不符合'|'不适用'), display_order

checklist_instance_issue_templates
  id, checklist_instance_item_id, source_issue_template_id, stable_key,
  template_version_snapshot, title_snapshot, description_snapshot,
  corrective_suggestion_snapshot, default_severity_snapshot,
  placeholders_snapshot_json, is_default_snapshot, display_order

nonconformities
  id, checklist_instance_item_id, instance_issue_template_id,
  template_version_snapshot, title, description, severity,
  responsible_person, submitted_date, planned_close_date,
  corrective_action, actual_close_date, verification_date,
  verification_conclusion, suggested_closed, confirmed

project_version_settings
  project_version_id, config_item_number, trend_conclusion_override,
  revision, updated_at

phase_detection_overrides
  id, project_version_id, source_wbs, detected_as_phase,
  user_selected_as_phase, source_reason

import_previews
  id, token_hash, source_filename, source_sha256, temp_relative_path,
  parsed_payload_json, overrides_json, diagnostics_json,
  status('pending'|'consumed'|'expired'), expires_at, created_at

export_jobs
  id, project_version_id, status, progress_step, requested_at,
  started_at, finished_at, output_dir, error_json

export_artifacts
  id, export_job_id, artifact_kind, filename, sha256, byte_size
```

关键不变量：

- `不符合`检查项至少有一条不符合项；`符合/不适用`不得有不符合项。
- 严重程度限定为轻微、一般、严重；系统自动建议只产生一般或严重。
- 产品审核关联一个配置项；过程审核不关联配置项。
- 公司级质量保证审核生成独立工作簿，不进入阶段报告和趋势。
- 公司级审核仍保留来源 `phase_id`，由 `report_scope='company'` 控制排除。
- 同一项目数据版本中审核流水号唯一，三位显示但数据库存整数。
- 每个实例检查项完整快照创建时可用的全部问题模板候选；已选问题只引用候选快照，不直接读取全局模板。
- 所有审核编辑使用 `audit_instances.revision` 乐观锁，在同一事务中执行 `UPDATE ... WHERE revision=?` 并检查影响行数。
- 阶段识别人工调整、配置项编号和趋势结论覆盖均按项目数据版本保存并进入导出快照。

### 5.4 迁移与连接

- `schema_migrations(version, applied_at, checksum)` 记录已执行迁移。
- 应用启动时校验 checksum 后顺序执行未应用迁移；失败立即停止启动，不吞异常。
- 每个连接执行 `PRAGMA foreign_keys = ON`、`PRAGMA busy_timeout = 5000`。
- 导入、模板批量导入、审核结论与问题一致性更新、项目版本删除均使用显式事务。
- 导入提交、模板批量提交和创建导出作业均要求服务端幂等键或一次性 preview token，并用唯一约束防并发重复提交。
- 项目版本永久删除前，由 API 同时核对项目编号和数据版本；级联删除项目数据，不删除全局模板和映射。

## 6. 结构化诊断契约

统一对象：

```json
{
  "code": "IMPORT_UNKNOWN_CHECKLIST_MAPPING",
  "severity": "blocker",
  "scope": "activity",
  "location": {"sheet": "项目进度计划", "row": 93, "wbs": "3.6.7"},
  "message": "关联配置项无法匹配检查单",
  "resolution": "请选择检查单，并可将映射保存到全局映射库"
}
```

原则：

- 测试断言 `code/severity/location`，不依赖完整中文文案。
- `warning`：待确认、未关闭、关闭周期压缩等，允许导出。
- `blocker`：必需字段缺失、日期超出阶段、未知映射、输入结构错误、工作簿结构或公式错误，阻止相应操作。
- API 返回诊断数组，前端按 scope 跳转到对应项目、阶段、活动或检查项。

## 7. 分阶段实现与逐步测试

每一步都要求：实现代码、自动化测试、样例验收、文档/AGENTS 状态更新同时完成。

### Step 0：项目脚手架与质量门禁

实现：

- 创建 `AGENTS.md`，写入项目边界、端口 8003、权威样例、技能路由和验证规则。
- 创建 `run.py`、`requirements.txt`、FastAPI 应用壳、静态页面壳、健康检查。
- 创建 pytest 配置、临时数据库 fixture、测试目录和基础日志配置。
- 配置路径全部从 `app/config.py` 解析；测试不得写真实 `qualitym.db` 或 `generated/`。

测试：

- `python3 -m pytest -q` 可运行。
- `GET /api/health` 返回应用版本和数据库状态。
- 服务启动并监听 8003；静态首页可加载 ES modules。
- 导入 `app.main` 不创建项目数据或导出文件。

验收门禁：脚手架、测试、端口和空页面均通过后进入 Step 1。

### Step 1：数据库迁移与全局种子框架

实现：

- 建立编号 SQL migration runner 和首版表结构。
- 实现连接工厂、外键、事务和测试数据库隔离。
- 定义 JSON Schema、稳定 ID 规则和种子导入器骨架。

测试：

- 空数据库可从 0 升级到最新版本；重复启动幂等。
- migration checksum 变化、SQL 失败、版本缺口均明确失败。
- 外键确实生效；事务异常回滚；项目版本级联删除不影响全局模板。
- 非法种子、重复稳定 ID、多个默认模板被拒绝。

验收门禁：`PRAGMA foreign_key_check` 无结果，迁移和回滚测试全绿。

### Step 2：完整检查单层级提取与最小种子库

实现：

- 从工作产品和过程检查单样例提取检查单、分组、检查项、星号、显示序号、顺序和来源格式定位。
- 生成可重复的版本化 JSON 种子；重复提取不产生无意义 diff。
- 每个检查项先建立至少两个结构有效的候选问题模板，确保后续实例可关联；完整业务文案在 Step 6 扩充和审核。
- 保留“工作产品评审过程”“测试过程”等分组，组内显示序号允许重新从 1 开始，stable key 必须唯一。

测试：

- 检查单、分组和检查项数量与样例基线一致。
- JSON Schema、稳定 ID、默认模板唯一性和占位符白名单校验通过。
- 种子导入后映射规则可以引用真实 `checklist_template_id`。

验收门禁：执行种子提取与校验命令，生成固定覆盖报告；全局模板表存在完整检查单层级，`PRAGMA foreign_key_check` 为空。

### Step 3：项目 Excel 只读解析与导入预览

实现：

- 解析 `项目信息`、`项目进度计划`、`配置项清单`；保留来源 Sheet、行号和原始顺序。
- WBS 使用字符串规范化，不把 `1.10` 转为浮点数。
- 识别“顶层 WBS 且有下级活动”的真实阶段，排除独立“公司质量验证”。
- 识别质量保证父节点的直接子活动、跟踪节点和关联配置项。
- 生成导入预览 DTO 和结构化诊断，不在预览阶段写数据库。
- 将预览元数据和解析结果写入 `import_previews`，原文件写入受控临时目录；令牌只保存哈希且一次性消费。
- 预览页面允许调整顶层 WBS 的阶段识别结果，提交时保存 `phase_detection_overrides`。

测试：

- 当前样例阶段、节点、质量活动、配置项数量与分析基线一致。
- 浮点 WBS、空日期、重复配置项、同名不同版本、缺失 Sheet/列、多人资源均有确定诊断。
- 顶层独立活动不被误识别为阶段；孙级活动不被误识别为质量保证直接子活动。
- 上传非 xlsx、损坏 ZIP、压缩炸弹、超大小文件被拒绝。
- 预览令牌过期、篡改、重放、双击提交、应用重启和临时文件清理均有测试。

验收门禁：生成当前样例的 golden preview JSON/HTML artifact，所有来源定位可追溯到 Excel 行；预览提交前数据库中不存在项目版本。

### Step 4：映射、审核实例展开、日期与编号纯业务核心

实现：

- 活动分类：产品、过程、公司级质量保证、报告编制排除项。
- 映射优先级：项目本次人工选择 > 用户全局精确映射 > 内置精确映射 > 内置别名/规范化规则；同优先级多命中返回 blocker。
- 产品审核按关联配置项展开；过程审核一活动一实例；质量保证过程一律归公司级。
- 审核日期取活动计划完成日期。
- 最近后续双周/阶段节点归组和计划关闭边界计算。
- 按阶段、日期、WBS、配置项原顺序分配项目版本内流水号。
- 相同项目编号导入生成新数据版本，事务提交整个预览结果。
- 保存阶段识别覆盖、默认配置项编号 `0.0` 和项目版本设置；设置可在系统中受 revision 保护地修改。

测试：

- 当前样例产品审核展开为 14 个实例；所有已确认过程映射正确。
- 软件接口设计说明、单元测试计划复用映射正确。
- 报告编制活动和“公司质量验证”不生成实例。
- 公司级质量保证实例不进入阶段统计集合。
- 工作日、法定休息日、调休上班日、阶段末压缩、无后续节点全覆盖。
- 同一版本重复计算结果稳定；新版本编号重新从 001 开始。

验收门禁：命令行/测试可从当前输入生成完整、可复算的审核实例清单。

### Step 5：Excel 纵向闭环技术原型

实现：

- 从权威样例提取最小检查单和单次审核报告 Sheet 模板。
- 生成一个阶段、一个审核实例、一个问题的工作簿。
- 建立检查单 → 单次审核报告 → 阶段报告的工作簿内公式。
- 生成最小趋势文件并引用阶段工作簿。
- 设置自动计算、完整重算和强制重算。
- 若 openpyxl 无法正确创建外链，在 `excel/external_links.py` 中实现最小 OOXML 关系适配，不允许业务 builder 直接操作 ZIP/XML。

测试：

- `openpyxl` 重开后 Sheet、样式、公式、保护和图表引用正确。
- 解压 `.xlsx` 检查 workbook relationships、externalLinks、drawing/chart 和 content types。
- 修改检查单问题数后，通过 Microsoft Excel 自动打开、更新链接、重算、保存；再次读取缓存值，单次报告、阶段报告和趋势数字同步变化。
- 移动整个导出目录后再次打开，验证相对链接仍有效。

验收门禁：真实 Microsoft Excel 冒烟全绿。若失败，暂停后续 UI，先修正外链策略。

完成记录（2026-08-14）：最小阶段工作簿和趋势文件已生成；检查单 → 单次审核报告 → 阶段报告 → 趋势分析链路在真实 Microsoft Excel 中完成 `1 → 1 → 1` 初始重算和清空问题后的 `0 → 0 → 0` 联动验证。整个输出目录移动后外链仍可更新，Excel 保存后的缓存值、相对 externalLink、relationships、content types、drawing/chart 和工作簿结构均验证通过。全量回归为 77 passed。

### Step 6：扩充并审核完整初始问题模板库

实现：

- 为每个检查项编写至少 2 个实用问题模板；窄项 2～3 个，复合/高风险项 4～6 个。
- 模板包含标题、描述、整改建议、严重程度和有限占位符，不虚构数量、章节、人员或证据。
- 实现种子升级：用户未修改的内置记录可更新；用户已修改的记录进入差异确认；删除转为停用。

测试：

- 每个启用检查项至少两个启用问题模板且恰有一个默认模板。
- 占位符白名单、空文案、近重复模板、机械否定句由校验报告发现。
- 样例中的“工作产品评审过程”“测试过程”等分组完整保留。

验收门禁：输出模板覆盖报告，由业务抽查每类检查单的代表性模板后通过。

技术完成记录（2026-08-18）：种子升级至 `2.0.1`，25 类检查单、28 个分组、361 个检查项共生成 1288 条实用候选问题模板，每项 3～5 条，覆盖完整性、一致性、追踪性、可验证性、变更同步、评审证据、过程执行、职责边界和风险处置等问题模型。问题描述直接陈述缺陷，不复制原始“是否/有无”检查问句；原检查项仍通过关联字段追溯。校验器可发现模板数越界、默认模板异常、机械否定和项内近重复；当前质量扫描 0 个发现。种子导入支持跨版本更新、删除转停用以及用户修改冲突预览/显式保留。全量回归 `81 passed`，权威 `temp/` 样例哈希未变化。业务抽查报告位于 `artifacts/step6/TEMPLATE_REVIEW.md`；用户确认前 Step 6 门禁尚未最终关闭，不进入 Step 7。

业务验收记录（2026-08-18）：用户确认问题描述不应复制原始“是否/有无”检查问句，修正后完成抽查并明确批准继续下一步。Step 6 门禁关闭。

### Step 7：审核数据生成、快照与编辑 API

实现：

- 项目提交后复制检查单、分组、检查项以及每个检查项下全部候选问题模板的内容快照。
- 使用 `项目编号 + 数据版本 + 流水号` 的稳定哈希，确定性建议 0～2 个不符合检查项，每实例最多一个严重问题。
- 实现结论、模板选择、拆分/新增/删除问题、轻微调整、责任人、整改、验证和确认 API。
- 使用事务维护“结论与问题数量”不变量。
- 使用乐观版本号防止两个标签页覆盖较新的编辑。
- 审核人默认活动资源，责任人默认项目组长，均允许修改；未确认和未关闭状态写入审核目录诊断。

测试：

- 同种子结果稳定，不使用 Python 进程随机化的 `hash()`。
- 修改全局模板不改变既有实例快照。
- 不符合项最后一条删除、符合项新增问题、并发旧版本写入均被正确处理。
- 建议已关闭与用户确认事实明确区分。
- API 404、422、409 和诊断载荷一致。

验收门禁：无需 Excel 即可在 API 层完成一条审核记录的完整编辑闭环。

完成记录（2026-08-18）：项目版本导入事务现同步冻结检查单、分组、检查项及每项全部候选问题模板；初始建议使用 `SHA-256(项目编号 + 数据版本 + 审核流水号 + 检查项稳定键)`，每实例稳定生成 0～2 条问题且最多 1 条严重问题，不使用 Python 进程随机化 `hash()`。实现审核列表/详情、结论切换、候选模板选择、手工新增、问题编辑、最后问题删除确认、整改验证确认和审核确认 API；所有编辑以 `audit_instances.revision` 乐观锁保护。事务持续维护“结论与问题数量”不变量，日期限制在审核日至阶段结束日，建议关闭与正式确认明确区分，未确认状态输出稳定 warning 诊断。API 自动化完成一条 `不符合 → 选择问题 → 调整为轻微 → 确认整改验证 → 确认审核` 闭环；404/409/422 返回稳定代码。全量回归 `85 passed`，未触碰正式数据库、`generated/` 或权威样例。验收记录见 `artifacts/step7/VERIFICATION.md`。

### Step 8：阶段综合工作簿构建器

实现：

- `ExportSnapshot` 一次加载项目版本全量数据。
- 快照在单个 SQLite 连接的明确读事务中构造；启用 WAL，计算 revision/hash 后提交。线程池只接收冻结 DTO，不接收连接。
- 生成审核目录、阶段质量审核工作报告、各单次审核报告、工作产品检查单、过程检查单和隐藏计算 Sheet。
- 目录超链接、Sheet 稳定短名、分组标题、保护区、打印区域和分页与样例一致。
- 同一审核实例的检查单和审核报告共用流水号；阶段质量报告独立编号。
- 所有下游问题描述、严重程度和统计使用公式引用，不重复生成业务事实。

测试：

- 零问题、一个问题、多问题、同检查项拆分、轻微/一般/严重、全部关闭/未确认分支。
- 双周块、阶段块、无匹配节点和零审核阶段。
- 每个公式引用合法；无 `#REF!`、循环引用、未替换占位符或 Sheet 重名。
- 与权威样例的尺寸、合并、字体、边框、列宽、行高、页面设置做快照比较。
- 所有生成 Sheet 渲染为 PDF/图片进行视觉检查。

验收门禁：当前样例全部有效阶段均能生成并通过结构、统计和视觉测试。

完成记录（2026-08-21）：实现单连接显式读事务中的不可变 `ExportSnapshot`，启用 WAL 并在事务内冻结项目、阶段、跟踪节点、审核、检查项和问题事实，计算 revision 向量与内容 SHA-256 后再提交；Excel builder 只消费冻结 DTO。当前样例 6 个有效阶段均生成独立综合工作簿，共包含 51 次阶段审核、57 个问题；每个工作簿包含目录、阶段质量审核工作报告、成对的检查单/单次审核报告和隐藏计算 Sheet。问题描述、严重程度和统计沿检查单 → 单次报告 → 隐藏计算表 → 阶段报告公式链传递。覆盖零/单/多问题、同项拆分、零审核阶段、双周/阶段/未匹配节点和同日同类型节点。全量回归 `94 passed`；最终 6 个工作簿经真实 Microsoft Excel 渲染 120 个 Sheet、142 个 A4 页面，逐页扫描 0 空白页。审查修复了零问题提示行误计 NC 以及同日同类型节点重复归组。验收记录见 `artifacts/step8/VERIFICATION.md`。Step 8 门禁关闭。

### Step 9：公司级工作簿与趋势分析构建器

实现：

- 所有质量保证过程审核进入独立公司级工作簿，只含目录、检查单和单次报告。
- 趋势表按真实阶段动态生成，零审核阶段填零但不建立空外链。
- 固定保留严重度和 RDM/PP/PMC/CM/MT/MPM/VV/LD/II/工作产品十类。
- 生成两条折线的数量趋势图、十类分布比例图和公式驱动的默认趋势结论。
- 项目版本设置中的趋势结论人工覆盖非空时优先输出；清空后恢复公式默认结论。
- 全部阶段工作簿、公司级工作簿和趋势文件写入时间戳导出目录及清单。

测试：

- 公司级问题不进入阶段报告或趋势。
- 没有公司级质量保证活动时不生成公司级工作簿。
- 各阶段和合计的数量、占比、零分母、并列峰值、全零、单阶段公式正确。
- 外部链接只指向同一导出目录下实际存在的阶段文件。
- Excel 重算后图表系列范围和趋势文字更新；人工覆盖文字不要求联动。

验收门禁：当前项目完整输出包通过 Microsoft Excel 联动和视觉验收。

完成记录（2026-08-21）：实现时间戳导出目录与清单，当前样例输出 6 个阶段工作簿、1 个公司级质量保证过程审核工作簿和 1 个趋势分析工作簿。公司级审核仅进入公司级工作簿；阶段趋势只通过同目录、相对 OOXML externalLink 引用存在阶段审核的阶段工作簿。趋势表按 6 个真实阶段动态生成，零审核阶段写入零值且不创建外链；固定保留 RDM/PP/PMC/CM/MT/MPM/VV/LD/II/工作产品十类。数量趋势图包含 NC 总数与严重 NC 两条折线，十类分布图使用总计行的公式驱动比例；默认结论随合计公式计算，项目版本的非空人工结论优先，清空后恢复默认公式。补齐 `LD` 阶段汇总，避免其在跨工作簿趋势中被误归零。专项回归 19 passed，全量回归 99 passed；最终输出包经真实 Microsoft Excel 打开全部阶段工作簿后重算趋势并渲染，趋势 1 页、公司级 3 页，视觉检查无空白页、裁切或乱码。验收记录见 `artifacts/step9/VERIFICATION.md`。Step 9 门禁关闭。

### Step 10：后台导出作业基础能力

实现：

- 建立持久化 `queued → snapshotting → building → validating → completed/failed` 状态机和单 worker 有界线程池。
- 创建作业、进度查询、失败重试和 artifact 下载 API；同一项目数据版本仅允许一个活动作业。
- API 创建使用 idempotency key；下载只允许数据库清单中的 artifact。
- 应用重启将未完成任务标记失败，允许用户重试。

测试：

- 合法/非法状态转换、并发重复创建、队列满、shutdown race、失败重试和重启恢复。
- 磁盘满、无权限、临时目录 rename 失败、artifact 缺失/被篡改、下载路径穿越均可见失败。
- Excel 不可用不影响普通文件生成；Excel live 验收单独标记失败或跳过自动作业但不能绕过 Step 5 本机门禁。

验收门禁：API 可创建真实导出作业、轮询至完成并下载 artifact；失败作业保留结构化诊断。

完成记录（2026-08-21）：新增 004 迁移，将原有 `running` 状态升级为持久化 `queued → snapshotting → building → validating → completed/failed` 状态机，并以部分唯一索引确保同一项目数据版本至多一个活动作业。应用生命周期启动时将未完成作业标为带 `EXPORT_INTERRUPTED` 诊断的失败，允许重试；单个有界 asyncio 队列配合单线程 executor 只向线程交付冻结导出快照。API 支持带 `Idempotency-Key` 的创建、状态查询、失败重试及仅限数据库 artifact 清单的下载；下载前验证同级文件名、存在性和 SHA-256，路径穿越、缺失或篡改均显式失败。导出先在受控 staging 目录构建和结构校验，再以原子 rename 发布到含时间戳和作业 ID 的目录，避免同秒目录碰撞。专项回归 `3 passed`，导出/迁移/应用生命周期组合回归 `22 passed`，全量回归 `102 passed`；API 测试完成真实构建、轮询和清单下载。验收记录见 `artifacts/step10/VERIFICATION.md`。Step 10 门禁关闭。

### Step 11：模板库管理与 Excel 批量交换

实现：

- 模板页面按检查单/分组折叠展示，支持新增、编辑、复制、停用、排序和恢复内置版本。
- 修改检查项文字增加版本但保持 stable ID；修改分组和顺序同理。
- 导出模板库 Excel；导入后先解析到临时差异模型，展示新增、修改、停用、冲突，再由用户确认事务提交。
- 已有审核实例不自动应用新版检查单；首版不提供审核实例内模板合并。

测试：

- 所有 CRUD 不变量、停用限制、默认模板唯一性和用户修改标记。
- Excel round-trip 保持稳定 ID、分组、顺序、版本和中文内容。
- 冲突、部分失败、重复提交均整体回滚。
- 大批量模板导入查询数和耗时受控。

验收门禁：全局模板变更与既有项目快照隔离，批量导入差异可复算。

完成记录（2026-08-21）：新增 005 号迁移保存一次性模板导入预览和基线摘要；模板服务与 API 覆盖检查单、分组、检查项、问题模板四层新增、编辑、复制、停用/启用、排序和内置版本恢复，修改保持数据库 ID 与 stable key 不变并递增版本、标记 `user_modified`。模板页按四层折叠展示并支持上述操作，以及 Excel 导出、差异预览和确认提交。模板库 Excel 固定 28 列，保留四层 stable key、版本、顺序和中文内容，含筛选、冻结窗格、8 组数据验证和公式注入防护。导入先校验 OOXML 和层级一致性，再以当前模板摘要复算预览基线；提交在单一 `BEGIN IMMEDIATE` 事务中使用固定批次预取，冲突、陈旧预览、不变量失败和重复令牌均不产生部分写入。已有审核实例继续使用创建时的检查项与问题模板快照。专项回归 14 passed，全量回归 111 passed；真实浏览器 QA 修复 Alpine 初始化竞争和移动端上传按钮溢出，最终健康分 100/100、无控制台错误；1,288 行最终工作簿经 openpyxl 重开和 Microsoft Excel 代表性渲染验收。验收记录见 `artifacts/step11/VERIFICATION.md`。Step 11 门禁关闭。

### Step 12：完整 Alpine.js 用户界面

实现：

- 项目版本列表、上传与导入预览、未知映射确认、审核实例列表、分组检查单编辑、问题编辑、模板管理、导出进度和诊断面板。
- 项目版本设置页可编辑配置项编号和趋势结论人工覆盖；导入预览可调整阶段识别。
- 统一 `api.js` 处理超时、网络错误和结构化诊断。
- 表单防重复提交；页面卸载前提示未保存修改；409 时提示刷新而不是静默覆盖。
- 删除项目版本显示名称、编号、版本和记录数量，提交确认令牌。

测试：

- API 集成测试覆盖全部路由成功、失败和权限边界（本地无登录，但仍校验资源归属）。
- 浏览器 E2E：导入 → 确认映射 → 编辑问题 → 导出 → 下载。
- E2E：双击提交、慢导出、失败重试、两标签页冲突、空列表、长中文名称、永久删除确认。
- 页面模块无未处理 Promise rejection，错误可恢复。

验收门禁：不借助数据库工具即可通过 UI 完成核心工作流。

完成记录（2026-08-22）：新增 006 号迁移和项目版本应用服务，提供版本列表/详情、删除摘要、10 分钟一次性令牌、资源归属校验、快照防陈旧校验和活动导出 409 保护；删除最后一个版本时同步清理项目数据库记录。前端重组为项目、导入、审核、设置、模板库、导出六个 Alpine.js 工作区，统一 `api.js` 处理超时、网络错误、结构化诊断和 409；表单具备操作键级去重、未保存离页提醒和冲突刷新。导入预览支持阶段调整和工作产品映射，审核页支持分组结论与问题编辑，导出页轮询状态并提供 9 个产物下载。专项回归 10 passed，全量回归 `121 passed`，浏览器 E2E 完成导入 → 编辑问题 → 设置冲突 → 导出 → 下载，并验证空列表、并发双提交、移动端、模板页和永久删除确认；发现并修复 3 个前端状态问题，最终控制台零错误，QA 健康分 100/100。已完成导出目录的原子隔离与恢复仍按计划留在 Step 13。验收记录见 `artifacts/step12/VERIFICATION.md`。Step 12 门禁关闭。

### Step 13：后台导出与删除恢复硬化（个人工具精简版）

实现：

- 保留 Step 10 已完成的有界单线程导出、不可变快照、staging 原子发布、中断失败可重试、下载路径与 SHA-256 校验，不重建并行作业体系。
- 删除项目版本时，若有活动作业返回 409。无活动作业时先将导出目录原子移动到 `generated/` 内同盘隔离目录，持久化原路径清单，再执行数据库删除；失败移回，提交成功后清理。
- 启动时扫描遗留隔离目录：项目版本仍存在则恢复原目录，已删除则继续清理；同时清理上次进程遗留的 staging 目录。
- 只记录当前权威样例的完整导出性能基线，保留 10 秒目标，不把大规模压力夹具作为个人工具的交付硬门禁。

测试：

- 复用既有状态转换、重复请求去重、失败重试、中断作业恢复、快照和下载安全测试。
- 新增删除与活动导出竞争、隔离后数据库失败回移、提交后清理失败、启动恢复与遗留 staging 清理测试。
- 重新运行当前权威样例完整导出并记录耗时；上传和 OOXML 解压上限继续由既有测试保证。

验收门禁：删除不留孤儿导出、中断可重复恢复、当前样例性能基线和全量回归通过。

完成记录（2026-08-22）：按个人工具定位完成 Step 13 Lite。新增集中导出存储适配器，删除项目版本前将历史导出目录按恢复清单原子移入 `generated/.delete-quarantine/`；数据库失败立即回移，提交后清理失败留待下次启动处理。应用启动时以项目版本是否存在为恢复事实，可重复地恢复或清理隔离目录，并清理遗留 staging。路径校验限定为受控输出根的直接子目录，拒绝外部路径和 symlink。恢复/删除组合专项 `12 passed`，项目版本专项 `9 passed`，最终全量回归 `126 passed`；权威样例完整包构建、校验和重开调用耗时 `1.11s`，低于 10 秒目标。验收记录见 `artifacts/step13/VERIFICATION.md`。Step 13 门禁关闭。

### Step 14：最终回归、文档与交付

实现：

- 完善 README/AGENTS、数据备份说明、模板更新说明和 Excel 外链使用说明。
- 固定当前输入和输出样例为测试夹具，记录 SHA-256。
- 生成最终输出包并制作结构、数值和视觉验收报告。

测试：

- 全量 `python3 -m pytest -q`。
- 服务启动、端口 8003、完整用户路径 E2E。
- 全 Sheet 结构扫描、公式错误扫描、Microsoft Excel 重算、图表和外链验证。
- 随机抽取代表性产品、过程、公司级审核核对检查单 → 单次报告 → 阶段报告 → 趋势。

验收门禁：所有自动化测试通过，当前样例完整输出经用户验收。

完成记录（2026-08-22）：完成最终交付收口。新增 `README.md`，说明启动、日常使用、SQLite/导出包成对备份、模板差异更新和 Excel 相对外链打开方式；固定权威输入与完整 9 文件导出包至 `artifacts/step14/fixtures/`，哈希清单见 `artifacts/step14/FIXTURE_SHA256.txt`。在隔离实例完成上传预览、确认导入、审核问题保存和后台导出 UI E2E；页面生成 6 份阶段记录、公司级记录、趋势分析和 manifest，浏览器无控制台错误。对真实输出包执行全阶段 openpyxl 结构/公式重开、`#REF!` 扫描、6 条趋势外链与 2 个图表核验，并以 Microsoft Excel 打开全部阶段文件和趋势文件、更新链接并强制重算（趋势首阶段统计值为 4.0）。最终全量回归为 `126 passed, 1 warning in 177.66s`。完整验收记录见 `artifacts/step14/VERIFICATION.md`；Step 14 门禁关闭。

### 2026-09 问题模板优化（批准后的新增范围）

本增强按三个里程碑实施，详细业务设计、工程方案和测试方案分别见：

- `artifacts/design/QualityM-问题模板优化设计-20260901.md`
- `artifacts/design/QualityM-问题模板优化工程实施计划-20260901.md`
- `artifacts/design/QualityM-问题模板优化测试计划-20260901.md`

里程碑 1（已完成）：修正审核事实语义。新审核实例只冻结候选问题快照，所有检查项初始为未检查，不再按哈希自动生成问题；一个不符合检查项可同时选择多个候选问题，也可添加自定义问题。整改建议与实际整改措施分离，系统预填的关闭和验证信息明确标为“待确认的建议值”，只有实际整改、实际关闭日期、验证日期和验证结论完整时才能确认关闭。带星检查项的问题固定为严重，非星检查项只允许轻微或一般，序号后的星号在审核页和导出中保持一致。审核问题可经显式确认另存为问题模板，但不携带项目事实字段。模板启用数量规则调整为每项最多 4 条；零条显示覆盖警告并允许审核时自定义，历史超过 4 条的数据保留但禁止继续增加。英文诊断代码仅保留为稳定机器码，页面展示中文问题和处理建议。

里程碑 1 数据升级使用 `007_problem_template_workflow.sql`，新增模板独立整数修订号、审核快照模式版本和 `user_edited` 标志；既有审核实例不自动重建。专项回归为 `31 passed`；补齐种子版本内容指纹回归后，最终全量回归为 `143 passed, 1 warning in 185.42s`，前端模块语法检查通过。验收记录见 `artifacts/problem-template-m1/VERIFICATION.md`。

里程碑 2（待实施）：提供面向用户的简化问题模板 Excel、隐藏系统映射表、逐行导入语义、预览确认和新增行自动映射，不再要求用户维护完整 28 列模板。

里程碑 3（已完成）：全部 25 个检查单均改用逐检查项整理的专属问题，不再由通用关键词模板直接充当启用候选问题。361 个检查项共整理 970 条启用模板，每项 2～4 条；内容字段为问题标题、具体表现、整改建议和默认严重度，不增加影响域。带星检查项全部为严重，非星检查项只使用轻微或一般。专业化内容按检查单保存在 25 个独立、可审查的版本化文件中；构建种子时执行检查单与检查项精确覆盖、数量、稳定键、严重度及通用标题禁用校验。种子版本升级为 `2.2.0`；数据库升级保留用户修改，1288 条旧通用模板停用而不删除，既有审核实例快照不自动重建，新建审核实例使用新模板。专项回归和真实页面抽查通过，浏览器无控制台错误。验收记录见 `artifacts/problem-template-m3/VERIFICATION.md`。里程碑 3 门禁关闭。

## 8. 测试覆盖图

```text
CODE PATHS                                            USER FLOWS
[+] 启动与迁移                                        [+] 首次启动
  ├─ migration 成功 [UNIT+INT]                         ├─ 首页/健康检查 [E2E]
  ├─ checksum/SQL 失败 [UNIT+INT]                      └─ 迁移失败可见 [INT]
  └─ FK/事务回滚 [INT]

[+] 项目导入                                          [+] 导入项目
  ├─ 合法 xlsx [UNIT+INT]                              ├─ 上传→预览→提交 [E2E]
  ├─ 缺 Sheet/列/日期 [UNIT]                           ├─ 未知映射修复 [E2E]
  ├─ WBS/阶段/直属子项 [UNIT]                          └─ 同编号新数据版本 [E2E]
  └─ 损坏/超大/压缩炸弹 [INT]

[+] 审核生成                                          [+] 编辑审核
  ├─ 活动分类/映射 [UNIT]                              ├─ 分组检查单填写 [E2E]
  ├─ 产品展开/过程实例 [UNIT]                          ├─ 选择多个问题模板 [E2E]
  ├─ 日期/节点/编号 [UNIT]                             ├─ 拆分/删除/确认 [E2E]
  └─ 确定性问题建议 [UNIT]                             └─ 两标签页冲突 [E2E]

[+] 模板库                                            [+] 维护模板
  ├─ seed/schema/升级 [UNIT+INT]                       ├─ 分组编辑 [E2E]
  ├─ CRUD 不变量 [INT]                                 ├─ Excel 导出/导入 [E2E]
  └─ Excel 差异事务 [INT]                              └─ 冲突确认 [E2E]

[+] 导出                                              [+] 获取记录包
  ├─ 一致快照 [INT]                                    ├─ 创建→轮询→下载 [E2E]
  ├─ Sheet/公式/保护 [WORKBOOK]                         ├─ 失败→重试 [E2E]
  ├─ OOXML 外链/图表 [OOXML]                            └─ Excel 更新链接 [EXCEL LIVE]
  ├─ 趋势统计/文字 [UNIT+WORKBOOK]
  └─ Excel 真重算缓存 [EXCEL LIVE]

[+] 永久删除                                          [+] 删除项目版本
  ├─ 确认令牌匹配 [INT]                                ├─ 明细二次确认 [E2E]
  ├─ 级联删除 [INT]                                    └─ 重复请求安全 [E2E]
  └─ 全局模板不受影响 [INT]
```

目标是所有领域分支和错误路径 100% 覆盖；视觉、Excel GUI 和操作系统行为不以行覆盖率替代，而使用专门验收测试。

## 9. 真实失败模式与处理

| 路径 | 生产失败示例 | 测试 | 错误处理 | 用户可见性 |
|---|---|---|---|---|
| 上传解析 | 文件损坏或压缩炸弹 | 集成测试 | 大小/压缩比限制，拒绝解析 | 明确 blocker |
| WBS 解析 | Excel 将 `1.10` 变为浮点导致层级错误 | 单元测试 | 字符串规范化并保留来源值 | 定位 Sheet/行 |
| 映射 | 多条别名同优先级命中 | 单元/E2E | 不猜测，要求用户选择 | 导入预览 blocker |
| 数据版本提交 | 中途异常留下半个项目 | 集成测试 | 单事务回滚 | 提交失败可重试 |
| 日期 | 日历不覆盖项目年份 | 单元/E2E | 阻止日期确认 | 明确缺少年份 |
| 审核编辑 | 两个标签页覆盖数据 | 集成/E2E | 乐观版本号返回 409 | 提示刷新 |
| 模板升级 | 用户修改被内置升级覆盖 | 集成测试 | user_modified + 差异确认 | 展示冲突 |
| 快照 | 导出期间数据被修改 | 集成测试 | 只读事务构造单一快照 | 本次包稳定 |
| Sheet 命名 | 长名称或非法字符冲突 | 单元/工作簿 | 稳定短名 + 目录全名 | 警告或 blocker |
| 公式 | 生成 `#REF!` 或循环引用 | 工作簿/Excel | 导出 validator 阻止完成 | 作业失败且定位单元格 |
| 外链 | 移动目录或 Excel 拒绝更新链接 | OOXML/Excel | 相对路径、清单、使用说明 | 作业/打开提示 |
| 图表 | 数据范围漏掉新增阶段 | 工作簿/Excel | 动态范围结构测试 | 导出失败或视觉测试发现 |
| 后台作业 | 应用重启时线程中断 | 集成测试 | 启动时标记 failed，可重试 | 作业状态可见 |
| 下载 | 构造路径读取任意文件 | 安全测试 | 只按 artifact ID 和白名单路径 | 404/403 |
| 删除 | 旧页面删除了错误数据版本 | 集成/E2E | 项目编号+版本确认令牌 | 409，不删除 |

不得存在“无测试、无错误处理、静默失败”的新路径。

## 10. API 草案

```text
GET    /api/health
GET    /api/projects
POST   /api/imports/preview
POST   /api/imports/commit
GET    /api/project-versions/{id}
DELETE /api/project-versions/{id}

GET    /api/project-versions/{id}/audits
GET    /api/audits/{id}
PATCH  /api/audits/{id}
PUT    /api/checklist-items/{id}/conclusion
POST   /api/checklist-items/{id}/issues
PATCH  /api/issues/{id}
DELETE /api/issues/{id}

GET    /api/templates/checklists
POST   /api/templates/issues
PATCH  /api/templates/issues/{id}
POST   /api/templates/export
POST   /api/templates/import-preview
POST   /api/templates/import-commit

POST   /api/project-versions/{id}/exports
GET    /api/exports/{job_id}
GET    /api/exports/{job_id}/artifacts/{filename}
```

导入预览可使用短期、服务端保存的 preview token，避免客户端回传篡改后的完整解析结果。预览提交时校验源文件 SHA-256 和 token 过期时间。

## 11. Excel 构建原则

- 样例是格式权威，数据库快照是业务事实权威。
- 检查单和单次报告优先复制样例 Sheet；阶段报告和趋势使用专门 builder。
- Builder 不查询数据库、不随机生成问题、不计算另一套业务统计。
- 所有 Sheet 引用统一经 `formulas.py` 生成并对名称转义。
- 目录、文件名和 Sheet 名统一经 `safe_path_component` / `safe_sheet_name`，并验证解析后的最终路径仍位于导出根目录。
- 普通业务文本统一经 `write_literal_cell` 写入；只有内部创建的强类型 `Formula` 对象允许写为公式，阻止用户输入触发公式、DDE 或外链注入。
- 工作簿内引用统一使用 `'Sheet Name'!A1`。
- 导出设置自动重算、打开时完整重算和强制重算。
- 外部引用集中在趋势 builder 和 `external_links.py`，其他模块不得创建跨文件公式。
- 公式区保护只用于防误编辑，不宣称提供防篡改安全。
- 输出清单记录项目数据版本、导出时间、文件名、SHA-256 和大小。
- 安全测试覆盖 `../`、绝对路径、symlink/TOCTOU、引号、方括号、超长中文、Unicode 归一化、重名和以 `= + - @` 开头的业务文本。

## 12. 性能和资源边界

- 默认上传上限 25 MiB，可配置。
- OOXML 解压总大小和压缩比设上限，拒绝异常包。
- SQLite 读取使用批量查询和索引：所有项目外键、阶段排序、实例流水号、模板 stable key、作业状态。
- 导入使用 `executemany` 和单事务。
- 导出只构建一次 `ExportSnapshot`，禁止 Sheet 级 SQL 查询。
- 后台线程池默认 `max_workers=1`；个人工具不并发生成多个大型包。
- 输出到临时时间戳目录，验证通过后原子移动到最终目录；失败目录标记并可清理。

## 13. 并行实施策略

本项目虽可拆多条工作流，但用户要求顺序可验收，因此实际实现按 Step 0～14 逐步推进。Step 5 的真实 Excel 门禁通过后，只有在不影响逐步验收且用户明确要求时才有限并行。

| 工作流 | 模块 | 依赖 |
|---|---|---|
| A：脚手架/数据库/导入/领域规则 | `app/domain`, `app/importers`, `app/repositories`, migrations | — |
| B：Excel 纵向原型 | `app/excel`, workbook tests | A 的 DTO 契约 |
| C：完整模板种子与问题内容 | `seeds`, extraction/validation scripts | Step 2 最小种子结构 |
| D：审核/API/UI | `services`, `routers`, `static` | A + C |
| E：完整导出与趋势 | `app/excel`, export tests | B + D |

执行顺序：

```text
主闭环：Step 0 → 1 → 2 → 3 → 4 → 5（顺序，Step 5 为真实 Excel 门禁）
内容与审核：Step 6 → 7
完整导出：Step 8 → 9 → 10
界面：Step 11 → 12
加固与收口：Step 13 → 14
```

潜在并行仅限：Step 6 的模板内容补全可在 Step 5 门禁通过后与界面原型准备并行，但 Step 7 开始前必须完成种子校验。任何并行都不得跨越当前 Step 的验收门禁。API DTO 在 Step 7 冻结后才进入 Step 12；Step 5、8、9 都修改 `app/excel`，保持顺序实施。

## 14. What already exists

- MeasureM 的 FastAPI 启动、aiosqlite 使用、项目导入、阶段和跟踪节点解析模式可参考，但 QualityM 不运行时依赖其源码。
- MeasureM 的 `excel_builder` 分层、共享样式、Sheet 内公式和强制重算设置可复用为设计模式。
- MeasureM 的不足需要修正：不使用吞异常式迁移、每连接启用 SQLite 外键、导出不在 async 事件循环中执行 CPU/I/O 工作。
- `temp/工作产品检查单.xlsx` 和 `temp/过程检查单.xlsx` 提供检查单、分组和格式权威模板。
- `temp/测控辅助业务-质量审核报告-软件需求与分析阶段.xlsx` 提供单次报告和阶段报告格式及编号参考。
- `项目相关信息表.xlsx` 是输入结构和当前样例验收夹具。
- 趋势分析只有用户提供的截图，没有原始工作簿；样式按截图复刻，公式、可维护性和数值正确性优先于像素级一致。

## 15. NOT in scope

- Word 检查单或 Word 报告：批准后的业务设计已统一为 Excel，且没有 Word 权威输出样例；后续有样例时单独设计。
- 读取 ConfigM、EstimateM、MeasureM 数据库或运行时源码：系统必须独立。
- 项目计划结构在线编辑及同项目版本差异合并：计划变化通过修改源 Excel 后重新导入新数据版本。
- Excel 修改回导 SQLite：导出 Excel 是独立快照；首版不做双向同步。
- 多用户账号、权限、网络部署和实时协作：本地单用户工具不需要。
- Celery、Redis、分布式任务和多实例部署：进程内有界线程池足够。
- LibreOffice/WPS 完整兼容：首版以当前 macOS Microsoft Excel 为行为验收环境。
- 历史项目版本归档、回收站和恢复：用户确认后永久删除。
- 已有审核实例自动合并新版检查单：防止破坏已填写事实；新实例使用新版，旧实例保留快照。
- 自动联网抓取法定节假日：首版使用版本化本地日历和年份覆盖 blocker，更新责任在种子维护流程。

## 16. Implementation Tasks

- [x] **T1 (P1)** — Scaffold — 建立 FastAPI/Alpine/pytest 脚手架、端口 8003 和 AGENTS.md。
- [x] **T2 (P1)** — Database — 实现编号迁移、外键连接工厂和完整 SQLite 模型。
- [x] **T3 (P1)** — Checklist hierarchy — 提取四层检查单结构，建立可运行的最小种子和校验器。
- [x] **T4 (P1)** — Import preview — 实现只读解析、持久化预览、诊断和一次性确认令牌。
- [x] **T5 (P1)** — Domain & commit — 实现活动分类、映射、实例展开、编号、节点、工作日及项目版本事务提交。
- [x] **T6 (P1)** — Excel spike — 打通检查单、审核报告、阶段报告到趋势的最小公式链并通过真实 Excel 门禁。
- [x] **T7 (P1)** — Seed library — 建立完整初始检查问题模板库并通过覆盖率与稳定键校验。
- [x] **T8 (P1)** — Audit workflow — 实现实例快照、候选问题快照、确定性建议、乐观锁和审核编辑 API。
- [x] **T9 (P1)** — Phase export — 完成阶段综合工作簿及结构、视觉、公式测试。
- [x] **T10 (P1)** — Company/trend — 完成公司级工作簿、趋势分析、外链和图表。
- [x] **T11 (P1)** — Export jobs — 建立后台导出作业、不可变快照和基础恢复能力。
- [x] **T12 (P2)** — Template UI — 完成模板管理和 Excel 批量交换差异确认。
- [x] **T13 (P1)** — Product UI — 完成项目、导入、审核、诊断、导出和删除 UI/E2E。
- [x] **T14 (P1)** — Hardening — 按个人工具精简范围完成导出性能基线、并发冲突、删除恢复和下载路径安全加固。
- [x] **T15 (P1)** — Final verification — 完成全量测试、真实 Excel 实测和最终样例验收。

## 17. 方案验收条件

在用户批准本计划前不开始实现。批准后严格从 Step 0 开始，每完成一个 Step 汇报：

1. 新增或修改的功能；
2. 执行的测试和结果；
3. 生成的可验收文件或页面；
4. 尚未解决的风险；
5. 下一 Step 的明确入口条件。

若 Step 5 的真实 Excel 联动门禁失败，暂停扩展功能，先重新评审外部链接方案，不以静态公式字符串存在冒充联动成功。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 未运行 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | CLEAR | Step 13 Lite 复用既有导出状态机，以同盘隔离、恢复清单和 SQLite 事实判定收口跨存储删除，无未解决项 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 未运行 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

- **VERDICT:** ENG CLEARED；Step 0～14 验收门禁通过。

NO UNRESOLVED DECISIONS

## 2026-10-23 离线 Windows 部署适配

交付收口：本地 Alpine.js、完整模板资源、全新部署说明和 Windows/Python 3.12 CI 一并提交。修正公式缓存写入时源 ZIP 未关闭即替换文件的 Windows 兼容问题，并补充句柄关闭及失败清理回归。实际暂存区导出到独立临时目录后，全量 152 个测试通过；使用该目录的 run.py 在 8003 启动，健康检查、静态资源、空项目列表和模板接口正常。验证详情见 `artifacts/deployment/VERIFICATION.md`。Windows CI 结果以对应 GitHub 提交的 Actions 为准；真实 Excel GUI 联动仍须在目标机验收。

交付方式已由用户确认：全新部署，交付方提供代码、模板资源和 README，部署方负责下载及安装依赖；不携带旧数据库、导出记录、临时数据和本地环境，不涉及历史导出路径迁移。本次确认仅更新部署文档，未改动运行逻辑，未新增测试。

为用户指定的无互联网新环境，将 Alpine.js 3.14.9 和 MIT 许可证随静态资源提供，保持原有先定义应用工厂再加载运行库的初始化顺序，取消页面 CDN 请求。README 补充在同版本、同架构 Windows 上准备 wheelhouse、离线安装和启动的步骤。未扩展业务功能。Windows 离线依赖包尚未制作，目标 Windows 的安装、页面交互及真实 Excel 联动仍需现场验收；历史导出绝对目录的跨系统迁移不在本次修改内。

验证：页面与脚手架相关 18 个现有测试通过；应用脚本按 ES module、Alpine.js 按普通脚本执行语法检查通过；通过 ASGI 确认首页及四个本地静态资源返回 200，Alpine.js 文件摘要与下载原件一致。检查应用 HTML、JS、CSS 无外部 URL；未启动真实数据库，未修改业务数据。未执行全量测试或真实浏览器/Windows 验收。

## 2026-10-19 趋势统计与相对外链修复

用户授权的缺陷修复，保持 Step 0～14 范围。已补齐阶段与趋势统计缓存、外链缓存及图表缓存，外链改为明确的汇总单元格引用，移除错误转零，阶段文件精确匹配。保留同目录相对外链。

按用户要求仅验证影响域：27 个不同自动化用例通过，并在真实 Microsoft Excel 中验证首次打开、整包移动、保存后二次移动、源数据变化联动、关闭源工作簿更新和源缺失保留缓存。详情见 `artifacts/trend-link-fix/VERIFICATION.md`。没有执行全量测试；没有改写真实运行数据或历史导出。
