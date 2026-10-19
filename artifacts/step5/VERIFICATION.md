# QualityM Step 5 验收记录

日期：2026-08-14  
范围：仅 DEV_PLAN Step 5（Excel 纵向闭环技术原型）

## 产物

- `QualityM-Step5-阶段质量审核记录.xlsx`
  - SHA-256：`243a121910b77648949ef7caca563ddbbd615b92c81ddfa978bfc089f978e866`
- `QualityM-Step5-质量工作趋势分析.xlsx`
  - SHA-256：`7ec89574a0ada04893d19f012adee5e04015444d5646ea1957e81f39369155d6`

权威 `temp/` 样例在构建和测试前后哈希保持不变；测试未写入正式 `qualitym.db` 或 `generated/`。

## 自动化与 OOXML

- 初始全量基线：67 passed。
- Step 5 定向测试：10 passed。
- 完成后全量回归：77 passed。
- openpyxl 重开验证：Sheet 顺序、关键样式、公式、保护、重算设置和图表均通过。
- OOXML 验证：`externalLinks`、workbook relationships、externalLinkPath relationship、content types、drawing 和 chart 均存在。
- 安全验证：外链仅接受同目录相对 `.xlsx` 文件名；绝对路径、父目录和嵌套路径被拒绝。
- 整目录移动后，外链 Target 仍为 `QualityM-Step5-阶段质量审核记录.xlsx`。

## Microsoft Excel 实测

在原型目录整体移动后，使用本机 Microsoft Excel 打开、更新链接、重算并保存：

```text
initial_phase=1.0
initial_trend=1.0
changed_audit=0.0
changed_phase=0.0
changed_trend=0.0
```

随后以 openpyxl `data_only=True` 回读缓存：单次审核报告 `D10=0`、阶段报告 `L101=0`、趋势分析 `B4=0`。Excel 保存后外链仍为同目录相对路径，图表和 externalLink 部件仍存在。

## 结论

Step 5 验收门禁通过。未开始 Step 6。
