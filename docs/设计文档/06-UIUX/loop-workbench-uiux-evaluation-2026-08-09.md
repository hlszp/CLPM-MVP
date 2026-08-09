# 回路工作台 UI/UX 深度评估（2026-08-09）

> 对象：`/loop/workbench`（views/loop/workbench.vue，1260 行 + 10 个子组件）
> 方法：代码全量审读 + 暗色现状截图（BL-8 采集）+ 亮色基线截图 + 三轮整改记录交叉核对
> 结论先行：**仍需优化**——B1/P0 类问题已清零，但存在 1 个布局缺陷、3 类体验债、2 处规范违例。建议按 P0/P1/P2 三批整改，总工作量约 2.5 天。

## 1. 已完成项（不重做）

- B1 死导航：历史按钮下线、趋势改页内弹窗（LoopTrendModal 复用）✅
- B2 风险确认：整定/仿真前置 ClpmDangerConfirmModal（WARNING 简化级）✅
- D4 左栏虚拟滚动（100 条仅渲染可视窗）✅
- C1-1 默认"最需关注"排序 + DayDeltaBadge 增量徽标 ✅
- D1 列表项键盘可达（role/tabindex/Enter/Space）✅
- 左栏评分"—"已有可信度等级 + tooltip 体系（K-01 部分缓解）

## 2. 新发现问题（按严重度）

### P0-布局缺陷

**W-1 回路概览区 10% 高度裁切内容**
- 证据：dark 截图中"数据健康度 90.0% · A"行折到第二行后被固定高度裁掉一半（`.wb-overview{flex:0 0 10%}` + `overflow:hidden` 父链）。
- 根因：7 个概览字段在 1440px 宽下必然折行（位号+名称+量程+控制方式+设定值+实时值+健康度），10% 高度只容一行。
- 修法：概览区改 `min-height` 自适应（flex: 0 0 auto）或字段压缩为 5 个（合并设定值/实时值为"SP→PV"、健康度并入右槽），推荐前者 + 字段精简。

### P1-体验债

**W-2 三区空态仍是"九个字"（历史 P1 未闭环）**
- 证据：dark 截图"暂无诊断数据""暂无整定记录"居中裸文本；`WorkbenchSectionCard` 的 `empty-text` 只渲染一句话，发起按钮在右上角与文案无视觉关联。
- 背景：A-08 空态三要素覆盖了页面级，但 workbench 的**区级空态**未接入（当时只改了外层 Page 空态）。
- 修法：给 WorkbenchSectionCard 增加 `emptyReason`/`emptyAction` 插槽（或区级接入 ClpmEmptyState compact 变体）：原因（"该回路近 24h 未触发诊断"）+ 动作（"发起诊断"按钮下移入空态）+ 数据范围（"近 7 天"）。

**W-3 整定依赖链灰显无原因（Poka-Yoke 缺口）**
- 证据：dark 截图"模拟仿真"按钮置灰，但无 tooltip 说明"需要先辨识→再整定"；message.warning 要点击后才知道。
- 修法：三按钮统一 `:disabled-reason`（沿用 toolbar-button 既有模式）：辨识恒可用；整定灰显提示"需先完成回路辨识生成过程模型"；仿真灰显提示"需先生成推荐 PID"。同时把 `requestSimulate` 的 message.warning 链前置为 tooltip。

**W-4 评估趋势图空窗无提示**
- 证据：dark 截图评分趋势 8h 窗整片空白无任何说明（无数据时图表静默）。
- 修法：ScoreTrendChart 无数据时渲染 ClpmEmptyState/图表空态（"该时间窗无评估快照，可发起评估或切换更长时间窗"），对齐 C1-4"图表无数据不渲染空框架"规范——此图是规范漏网者。

### P2-规范违例

**W-5 Emoji 区标未退役（审查 P2 遗留）**
- 证据：dark 截图四区标题仍是 🛰️📊🔍🔧（`WorkbenchSectionCard icon` 传 emoji 字符）。
- 修法：换 Iconify 线性图标（lucide:activity/bar-chart-3/search/wrench）。

**W-6 枚举泄漏：`算法：IDENTIFICATION_ONLY` 原样上屏**
- 证据：dark 截图整定区。算法枚举应走 ALGORITHM_LABEL 映射，IDENTIFICATION_ONLY 未收录；且该状态语义是"仅辨识未整定"，建议显示"—（仅辨识）"或中文"辨识记录"。
- 顺带：`可信度：E` 红 Tag——按色彩约定 E=数据不足应中性灰（`status-neutral`），红 Tag 让"数据不足"看起来像"故障"，违反零值/无数据中性纪律。

**W-7 DayDeltaBadge "±0" 满屏噪音**
- 证据：dark 截图左栏 12 行全部"评分 60.5x ±0"。FLAT 态显示 ±0 是视觉噪音。
- 修法：FLAT 不渲染徽标（组件内 trend==='FLAT' 时 return null），只保留 ▼恶化/▲好转/新增三态——信息即异常。

### P3-打磨项

- **W-8 信息态误用错误样式 toast**：截图顶部"该回路暂无诊断结果"红 X toast（`message.error` 或全局拦截器把 404/空结果当错误）。空结果应 `message.info` 或区内容纳为空态。
- **W-9 概览区"趋势"按钮层级**：默认灰按钮与主操作（发起评估 primary）同级权重，建议 text/link 型弱化。
- **W-10 回路切换的数据成本**：每次切换触发 4 路并行 + 72h 快照分页全量拉取（`loadScoreHistory` 循环分页），27 回路巡检连点时请求风暴。建议快照接口加 `limit`/降采样参数或仅拉趋势所需窗（当前窗+前一窗）。

## 3. 剩余 UX 流程评估（不改，仅记录）

- 单页四区 + 页内弹窗的任务动线清晰，router.replace + fullPathKey=false 的 tab 治理是全站范本级；任务运行器（progress/stage）反馈完整。这些是保留资产。
- 整定区"当前 vs 推荐 PID"的对比呈现偏弱（左右分栏但没有并排 diff 感），Phase 3 可考虑双列对照表，不在本轮。

## 4. 整改方案（三批，预估 2.5d）

| 批次 | 内容 | 预估 | 验证 |
|---|---|---|---|
| 批 1（P0+P1） | W-1 概览区高度修复；W-2 区级空态三要素（WorkbenchSectionCard 扩 emptyReason/Action，三区接入）；W-3 按钮灰显原因；W-4 趋势图空态 | 1d | check:type + vitest + 截图复核 |
| 批 2（P2） | W-5 Emoji 退役；W-6 枚举映射 + E 级中性化；W-7 FLAT 不渲染 | 0.5d | 同上 |
| 批 3（P3） | W-8 toast 降级为 info/空态；W-9 按钮弱化；W-10 快照拉取瘦身（评估后定） | 1d | 同上 + 快照请求计数复核 |

边界：只动 workbench.vue 及其子组件 + WorkbenchSectionCard；不改后端 API（W-10 若需后端参数再单独立项）；文案进词表；新颜色不进代码（走 token）。
