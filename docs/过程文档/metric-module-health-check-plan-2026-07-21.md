# 性能评估模块体检报告与完善计划（2026-07-21）

## 体检方式与已闭环项

基于昨日 4 路全量审计（装置性能/回路性能/评估任务/KPI报表）+ 今日新增三功能（理想稳态时间配置/异常检测参数/可信度最新表）后的现状核验。以下项**已闭环**，不再列入计划：

- 27 项页面问题（PR #81）+ CI 格式（PR #82/#84）
- steady_rate 契约别名、worker 预载、限流熔断、SignalR 退避（PR #79/#80）
- 数据架构"导入走远端、计算全本地"（PR #83/#85）
- Beat 双触发（PR #90，合并后 0 新增重复）
- **KPI A/B 对比接口 501 → 已由另一对话实现**（`/diagnosis/ab-compare`，before/after 窗口 + dataInsufficient 标记）
- 理想稳态时间配置、8 类异常参数配置、可信度最新表+抽屉（已验证落库）
- 手动/自动任务取消删除已统一普通确认弹框

## 体检发现清单（按优先级）

### P0 — 数据/口径正确性

| # | 问题 | 根因与影响 | 修复思路 | 验证 |
|---|---|---|---|---|
| 1 | 回填任务 `loops_total` 被"回路×窗口"工作项数覆盖 | `kpi_calc._update_backfill_progress`（:2667/2698）把工作项数写进 loops_total；tasks.py 展示层已临时还原（594→27），根因仍在，其他读方会踩坑 | kpi_calc 改用独立字段（如 `work_items_total`）记录进度，loops_total 恒为回路数；移除 tasks.py 的展示层还原补丁 | 单测：回填后 loops_total=回路数；e2e F7 断言保持 |
| 2 | 权重口径分歧：契约说"a/f/s 取 4 类控制类型模板"，代码实际是 `MetricConfig.weight` 优先（当前 30/20/30），类型模板仅兜底 | `kpi_calc._build_weights_map` 优先级链 MetricConfig.weight > LoopTypeWeight > None。两套来源并存导致"页面配置的权重"与"控制类型模板"谁是权威不清晰 | 裁决（建议：以"权重配置管理"页 metric_config 为唯一用户入口，类型模板作为出厂默认一次性写入 metric_config；文档对齐该口径），改 FDS/算法说明对应段 | 指定回路改权重 → 回填分数按新权重；文档 grep 无旧口径 |
| 3 | INCONCLUSIVE 快照 `confidence_level` 存 NULL 而非 'E' | kpi_calc 持久化层对 INCONCLUSIVE 行不写等级；页面聚合可信度取"非空最差等级"，与 §7.15（E↔INCONCLUSIVE）语义偏差 | `_persist_snapshot` 对 INCONCLUSIVE 行写 `confidence_level='E'`（如 composite 血缘有等级则沿用），保持查询/展示兼容 | PG 抽查新快照 INCONCLUSIVE 行 confidence_level='E'；聚合可信度展示不受影响 |
| 4 | STANDARD 重复任务历史数据（16 组，PR #90 合并前遗留） | 双 Beat 时代的重复记录混在任务列表 | 写一次性清理脚本（按 title+小时窗保留先创建者，删除重复终态记录），脚本放 `backend/scripts/` | 脚本 dry-run 输出 16 组清单；执行后 API 查询重复组=0 |

### P1 — 功能完善

| # | 问题 | 根因与影响 | 修复思路 | 验证 |
|---|---|---|---|---|
| 5 | 装置性能 gauges 不随时间窗变化 | `board/aggregate` 无 timeWindow 参数，gauges 恒为"最新快照"；用户切时间窗时趋势/TOP5 变了但仪表盘不变，语义断裂 | 后端 aggregate 增加可选 timeWindow（默认现状=最新）；前端 loadBoard 透传；gauges 标注统计窗口 | curl 带 timeWindow 返回值随窗口变化；页面切窗 gauges 联动 |
| 6 | 实时数据过期无提示 | 状态饼图/实时自控率在实时中断时显示 DB 回退值（7/13 的旧数据），用户无感知 | `auto-rate-rt` 已返回 `readAt`；前端在卡片角加"数据更新于 HH:mm"小字，超过 10 分钟标灰/警示色 | 断实时流后页面出现过期提示；恢复后消失 |
| 7 | SPONSOR 角色可下钻单回路诊断/详情 | 契约 §5 要求 SPONSOR 只读工作台，现回路性能/监控的诊断 Modal 与详情页无角色门控 | 前端按角色隐藏诊断/详情入口（v-permission）；后端诊断详情类接口加 require_roles 防线（ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT） | e2e：sponsor 登录不见入口、直接访问被拒 |
| 8 | `GET /performance/rules` 返回裸数组 vs 前端类型 `RuleListResult{items}` | 契约不一致技术债（前端已做兼容兜底） | 统一为裸数组（改 api/metric.ts 类型，删除兼容分支）或包一层（改后端）；建议统一裸数组，改动小 | check:type + F1 用例过 |
| 9 | `api/metric.ts:1074` 注释"默认近 7 天"与实际 30 天不符 | 注释过时 | 一行改注释 | grep 无旧注释 |
| 10 | `confidence-badge.vue` 死代码 | 组件完整（Tooltip+Tag 五色+INCONCLUSIVE 图标）但零引用，各页面重复内联实现 | 二选一：①删除（内联现状稳定）②收敛到组件（loop-performance/history-snapshots/kpi-report 可信度列统一替换）。建议先收敛到一处试点再推广，失败则删 | 组件被引用或已删除；渲染一致 |

### P2 — 工程优化（视资源排期）

| # | 问题 | 修复思路 |
|---|---|---|
| 11 | pid-dashboard.vue:348 趋势时间戳 `+8h` hack | 抽统一时间工具（项目内已有"补 Z 转本地"约定），UTC→本地渲染统一处理 |
| 12 | ranking `limit=100` 上限导致 >100 回路时等级占比饼图少计 | 前端循环分页拉全量（对齐 loop-performance 的 fetchAllSnapshots 模式） |
| 13 | 生产部署走查 | prod compose + .env.prod + deploy.sh 全流程在测试机跑一遍（含 worker include、tdengine profile 恒启用验证、sys_config 首配流程） |
| 14 | E2E 补盲 | 装置性能页、KPI 报表页新增 spec；参数配置 Tab（开关生效）、可信度抽屉、理想稳态时间字段补用例 |

## 实施计划（3 批，可独立 PR）

**第一批 P0（约 1 天）**：#1 loops_total 根因、#2 权重口径裁决+文档、#3 confidence_level='E'、#4 重复任务清理脚本（dry-run 确认后执行）
**第二批 P1（约 1-2 天）**：#5 gauges 时间窗、#6 过期提示、#7 Sponsor 门控、#8 rules 契约、#9 注释、#10 死代码收敛
**第三批 P2（视情况）**：#11-#14，其中 #13 生产部署走查建议优先（上线前必做）

**纪律**：每批独立分支 + PR（遵循双机协作规范）；模型变更与迁移同批；后端 pytest + check:type + 相关 e2e 全绿方可合并；数值类改动必须 PG 复算 vs API vs 页面三方核对。

**范围外**：诊断中心整改（已有专项计划 `docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md`，A/B/C/D 四阶段）；KPI 报表图表化（PRD 原始设计为图表形态，当前表格，属产品形态决策）。
