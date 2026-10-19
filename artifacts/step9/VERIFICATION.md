# QualityM Step 9 验收记录

日期：2026-08-21  
范围：DEV_PLAN Step 9（公司级工作簿与项目质量工作趋势分析）

## 已实现

- 每次完整导出创建带时间戳的目录及 `manifest.txt`；当前包包含 6 个阶段工作簿、公司级工作簿和趋势工作簿。
- 公司级质量保证过程审核只生成在独立工作簿中，包含审核目录、检查单和单次审核报告，不进入阶段工作簿或趋势统计。
- 趋势工作簿按快照的实际阶段数生成行；没有阶段审核的阶段填零且不建立外部链接。
- 外链集中通过 `external_links.py` 创建，目标限定为导出目录内的同级 `.xlsx` 文件。
- 趋势表固定包含 RDM、PP、PMC、CM、MT、MPM、VV、LD、II、工作产品十类；阶段汇总和趋势分布表都保留零值类别。
- 数量趋势图含“NC总数”和“严重”两条折线；十类分布图以合计数量和 `IFERROR(数量/NC总数, 0)` 公式生成比例。
- 非空 `trend_conclusion_override` 输出为人工文字；清空或空白值恢复公式驱动的默认结论。项目版本设置接口以 revision 乐观锁保存该值。

## 最终样例输出

目录：`artifacts/step9/QualityM-03046-V1-20260821-215634/`

| 产物 | 数量 |
|---|---:|
| 阶段质量审核记录 | 6 |
| 公司级质量保证过程审核记录 | 1 |
| 项目质量工作趋势分析 | 1 |
| 清单条目 | 8 |
| 趋势外链 / OOXML externalLink parts | 6 / 6 |
| 趋势图表 | 2 |

结构重开验证确认趋势工作簿的 `B4` 为跨工作簿公式，默认结论为合计公式，且不存在 `#REF!` 或未替换占位符。

## 自动化验证

- `.venv/bin/python -m pytest -q tests/test_company_trend_export.py tests/test_project_commit.py tests/test_phase_export.py`：`19 passed`。
- `.venv/bin/python -m pytest -q`：`99 passed in 118.43s`。
- 使用 openpyxl 重开最终趋势和公司级工作簿：目录、超链接、自动计算设置、两张图表、6 个安全相对外链和清单均正确。
- 覆盖无公司级活动、空阶段无外链、人工结论覆盖与清空恢复、十类类别、两条折线和比例公式。

## Microsoft Excel 联动与视觉验收

- 真实 Microsoft Excel 在同一会话中先打开 6 个阶段工作簿，再打开趋势工作簿以更新外部链接并重算；最终趋势 PDF 为 1 页。
- Excel 重算后的趋势结果显示 NC 总数 57、一般 41、严重 16；趋势结论显示“当前项目共发现57项不符合项，其中严重16项。”
- 真实 Microsoft Excel 渲染公司级工作簿为 3 页。
- PDF 渲染为 PNG 后逐页检查趋势 1 页、公司级 3 页：无空白页、乱码、文字裁切、表格越界或图表缺失。
- 验收 PDF：`tmp/pdfs/step9-final-trend.pdf`、`tmp/pdfs/step9-final-company.pdf`。

## 门禁状态

Step 9 验收门禁通过。下一步为 Step 10；未经用户明确指示不得进入。
