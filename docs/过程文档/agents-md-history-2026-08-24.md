# AGENTS.md 瘦身移出的历史背景（2026-08-24）

> 本文档为 AGENTS.md 瘦身移出的历史背景，按需读取，非每轮注入。现行规则与口径以根目录 `AGENTS.md` 为准。以下各条均于 **2026-08-24** 移出，按原 AGENTS.md 章节结构组织。

## 1. §MVP 覆盖说明 — 「模块现状」演进过程细节（移出日期 2026-08-24）

原「模块现状」条目全文（含各方案日期标注的演进过程）：

> "监控 → 评估 → 诊断 → 整定 → 处置 → 报告"完整闭环 + 管理层视图已落地（2026-08-23 IA 优化 P0~P4 完成）：诊断两页式（07 方案，2026-08-16）/ 整定三页式（09 方案恢复一级模块，2026-08-19）/ 处置已升 **v2.0 双实体**（08 处置方案：loop_action_item 收敛为建议实体 + 新建 handling_order 处置工单表，2026-08-20）；**统计报告升为一级菜单**（order=6，配置→7、系统→8），含管理总览/绩效/诊断/处置/收益/订阅配置 6 子页，成熟度 S1/S2/S3 自适应骨架 + 旧路径 redirect 就位；**模块热插拔**落地（`app/core/modules.py` 注册表 + `use-modules` composable + 系统-模块管理页 + `beat_registry.py` 条件调度 + 5 处跨模块守卫），诊断/整定/处置支持按客户阶段弹性启用禁用；**适用性评估 L0~L4** 落地（fitness_level/fitness_tags/fitness_detail 三字段加 `kpi_snapshot_hourly` 与 `_custom`、ClpmFitnessBadge 公共组件、7 个 IA 落点、诊断 L0/L1 阻止 L2 横幅、整定 L3 以下 ERR_TUNING_FITNESS_INSUFFICIENT 门禁）；前端路由模块 `monitor/assess/diagnosis/tuning/handling/reports/alert/config/system/task/loop`，左侧导航按闭环顺序排列（监控-评估-诊断-整定-处置-报告-配置-系统，2026-08-23）；系统管理含可配置字典管理页（MEASURE_TYPE/TAG_TYPE/LOOP_TYPE 三类字典，2026-08-21）+ 模块管理页

演进时间线摘要：

- 2026-08-16：诊断两页式落地（07 方案）
- 2026-08-19：整定三页式落地（09 方案恢复一级模块）
- 2026-08-20：处置升 v2.0 双实体（08 处置方案）
- 2026-08-21：系统管理字典管理页落地（MEASURE_TYPE/TAG_TYPE/LOOP_TYPE）
- 2026-08-23：IA 优化 P0~P4 完成；统计报告升一级菜单；导航按闭环顺序排列
- 另：适用性评估 L0~L4 落地（fitness 三字段加 `kpi_snapshot_hourly` 与 `_custom`、ClpmFitnessBadge 公共组件、7 个 IA 落点）；模块热插拔含 5 处跨模块守卫

## 2. §MVP 覆盖说明 — 「已知残留」完整描述（移出日期 2026-08-24）

原条目全文：

> 精简阶段 5 个聚合 service stub 化（monitor_attention 关注队列三来源 / workbench_summary 诊断/整定/tracker 摘要恒 None / dashboard 与 anomaly_prediction 计数恒零），部分已被新 API 路径绕过；是否恢复 monitor_attention 的 TRACKER/VERIFICATION 来源待人工决策（详见 `docs/MVP设计/README.md` §已知残留）

## 3. §历史基线（v6.2 归档）整段（移出日期 2026-08-24）

原段落全文：

> 原 CLPM v6.2 的文档基线、核心架构组件、历史决策与下阶段规则已迁至 **`docs/历史基线/AGENTS-v6.2-archive.md`**。**读取时机**：仅当任务涉及 v6.2 架构溯源、历史交付核对、基线文档版本核对时读取，日常会话不加载。**修改纪律**：仅在用户显式指令或重大架构变更（如基线文档版本升级）时更新，日常不维护。根目录 `DESIGN.md` 为 active-baseline（v3.1，2026-08-24）：保留视觉/布局/组件/状态机横切设计约束；其 IA 与菜单口径以 `docs/MVP设计/00-信息架构.md` 为准，v6.2 系列文档引用降为历史基线。

## 4. §关键注意事项 — 历史背景叙述（移出日期 2026-08-24）

以下背景叙述从行为红线条目中移出，规则本身保留在 AGENTS.md：

- lefthook pre-push 门禁启用历史：2026-07-28 起配置；schema 漂移检查（`alembic check`）2026-08-24 接入。
- 「模型变更必须与迁移同批应用」背景：2026-07-21 教训。
- 「热路径禁止对 naive datetime 逐点调 `.timestamp()`」背景：macOS fork 时区慢路径陷阱，背景详见 ops-runbook。
- 「禁止模块级 asyncio.Lock / Semaphore / Event」背景：2026-07-28 全回路 INCONCLUSIVE 事故根因，ops-runbook 已记录。
- 「断点续传配置运行时可调」背景：2026-08-06 落地。
- 诊断调度背景：**2026-08-07 起自动诊断 Beat 停用**（现行口径"仅保留手动触发"仍在 AGENTS.md）；历史双轨口径备查 → ops-runbook §诊断调度细节。
- uvicorn 静默挂死排查背景全文：2026-08-09 加固 `2b9fb9d`：SQL echo 关停 + `command_timeout=60` + 噪音日志钳制；现象=进程存活 0% CPU/API 全挂起，多为连接风暴/资源耗竭；取证=PG 连接监控 `scripts/monitor_db_connections.py`（支持 --dsn 直连）+ `/proc/<pid>/net/tcp` TIME_WAIT 计数。详见 ops-runbook §uvicorn 静默挂死排查。

## 5. §核心决策 — 日期性背景注记（移出日期 2026-08-24）

- 数据架构决策定调日期：2026-07-20（决策记录文档：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`，该指针保留在 AGENTS.md）。
- 网络模式切换（应用层局域网/公网）定调日期：2026-07-19。

## 6. §Git 工作流 — 日期性修订注记（移出日期 2026-08-24）

- 章节修订日期：2026-08-20（MVP 口径修订）。
- 双机分支策略生效日期：2026-08-22 起。
- 「提交/推送/CI 仅在用户显式要求时执行」纪律确立日期：2026-08-22。
- CI 现状更新日期：2026-08-20。

## 7. §MVP 覆盖说明 — 章节标题日期注记（移出日期 2026-08-24）

- 原标题日期注记：「⚠️ MVP 覆盖说明（2026-08-20，优先级最高）」。
