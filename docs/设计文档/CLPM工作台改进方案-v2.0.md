# CLPM 工作台改进方案 v2.0

> 依据原型：`/Users/zhangping/Downloads/Kimi_Agent_CLPM工作台设计/`
> 目标系统：`CLPM-MVP` (github.com/hlszp/CLPM-MVP, main)
> 版本：v2.0 | 状态：方案交付（待实施）
> 本文档覆盖：§1 数据模型改进 · §2 业务逻辑优化 · §3 API 接口完善 · §4 系统架构调整 · §5 功能模块补充 · §6 实施路线图（M1/M2/M3 · 9 周 · 15.5 人周）

---

## 0. 原型要点回顾（对照基准）

### 0.1 原型信息架构

- **单屏 100vh 工作台**（尺寸：顶栏 56px / Tab 栏 48px / 内容区 ≈760px / 状态栏 28px，总 892px @1600×900）
- **5 Tab 主体**：系统总览 / 性能评估 / 回路诊断 / 参数整定 / 问题处置
- **模块热插拔 4 态**：`CORE` 内置 · `ENABLED` 在线 · `MAINTENANCE` 维护中（带进度条 + 面纱）· `UNINSTALLED` 未安装
- **顶栏**：范围选择器（全厂/装置/单元/回路）· 时间胶囊（24h/7d/30d + 自定义）· 数据可信徽章 · 通知铃铛（未读红点）· 人员
- **底栏**：刷新时间 · 评估周期 · 数据流时延 · 插件在线数

### 0.2 原型演示数据总账（15 类）

| ID | 常量 | 含义（关键字段） | 当前 MVP 覆盖度 |
|---|---|---|---|
| W1 | WINDOWS | 三窗口 KPI / trend / flags | 60%（KPI 三窗口存在，flags 缺） |
| W2 | SCOPES | 范围层级列表 | 100%（PlantNode FACTORY/AREA/UNIT） |
| W3 | PLANTS | 装置排名（spark / lose_factors / alarm_count / overdue_tasks） | 40%（排名在，spark/lose/overdue 缺聚合） |
| W4 | UNITS | 单元 ×6 指标热力 h[6] | 70%（6 指标在，热力矩阵视图缺） |
| W5 | METRICS | 6 项 KPI 定义 | 100%（node_kpi 6 指标字段齐） |
| W6 | LOOPS | 关键异常 6 条（spark / sla_due / conclusion） | 45%（DiagnosisTag 在，SLA/conclusion 三态缺） |
| W7 | PARETO | 异常类型分布 | 0%（物化视图 MV-02 缺） |
| W8 | ROOTS | 根因 Top N | 30%（DiagnosisTag 有，Top 聚合视图缺） |
| W9 | CONCL | 诊断结论时间线（3 态 disposition） | 10%（结论有，disposition 三态标签缺字段） |
| W10 | EVENTS | 7 类来源事件归一 | 0%（alert/diagnosis/handling/tuning 独立，未归一总线） |
| W11 | BATCHES | 整定批次（前置依赖 pr_cl-2026-0819） | 5%（TuningRecord 单记录在，批次/前置依赖实体缺） |
| W12 | PENDING_TUNE | 待整定队列 | 60%（TuningRecord DRAFT 在，阻塞语义缺） |
| W13 | SCATTER | 整定前后散点 Δ（11 点） | 40%（kpi_before/after 在，散点聚合接口缺） |
| W14 | TASKS | 处置看板 4 泳道 13 条（lane / od 超期 / reopen_count） | 55%（HandlingOrder 6 态在，泳道看板视图/od/reopen 计数缺） |
| W15 | STAFF | 6 人负载（及时率 / 超期 / 分布） | 5%（handler FK 在，物化负载 MV-01 缺） |
| W16 | PLUGINS | 5 插件 · 版本/状态/核心标记/未安装 | 10%（modules.py 静态字典在，4 态 / 版本 / 维护窗口 / 审计缺） |

### 0.3 原型视觉规范（UI 落地基线）

- 背景 `#F5F7FA` · 卡片 `#FFFFFF` · 主色 `#1F4E79` · 强调 `#2563EB`
- 状态色工业规范：绿=运行 / 蓝=启动中 / 橙=停止中 / 黄=待机 / 红=维护 / 灰=禁用 / 青=检查中
- 卡片：1px `#E4E7ED` 边框 · 4px 圆角 · 8px padding · 无多余装饰
- 表格：选中行 1px 细分隔；列宽按字符数百分比分配（5ch = 5/39×100%）
- 树形：缩进 8px/层，行高 28px，flex 防换行
- Sparkline：无动画；饼状图无引线仅悬浮框；

---

## 1. 数据模型改进

### 1.1 现有模型盘点（30+ 表，按域）

| 域 | 表 | 关键实体 |
|---|---|---|
| 回路 | loop.py | LoopLedger + LoopTagMapping (PV/SP/OP/MODE/PID_P/I/D) |
| 组织 | plant_node.py | PlantNode (FACTORY/AREA/UNIT 三层自引用) |
| KPI | node_kpi.py | KpiNodeSnapshotHourly / Daily / Monthly（10+ 指标 × 6 级状态） |
| 处置 | handling_order.py + loop_action_item.py | HandlingOrder 6 态 × 8 类；LoopActionItem 5 态审核；kpi_before/after 固化 |
| 诊断 | diagnosis.py（7 表） | Config/Result/Task/Tag/Rule/ThresholdOverride/ConfigChange |
| 整定 | tuning.py | TuningRecord 11 态 · 多候选 · 回退 · 风险评估 |
| 预警 | alert.py（5 表） | AlertRule/Subscription/Event（4 级）/Audit/Suppression |
| 系统 | sys_config.py | SysConfig KV（enabled_modules 存此） |
| 模块 | core/modules.py | MODULES 8 项静态字典（进程内缓存） |

**缺口一句话诊断**：单实体域已闭环，**跨模块聚合、批量首屏、工作台专用视图**三大类实体缺失。

---

### 1.2 新增实体（M-01 ~ M-08）

#### M-01 module_plugin（模块插件注册表 · 4 态状态机载体）

> 替代 modules.py 静态字典，持久化版本/维护/安装/审计。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | int | PK |  |
| module_key | varchar(32) | UK, NOT NULL | 对应 MODULES.key (monitor/assess/diagnosis/...) |
| display_name | varchar(64) | NOT NULL | 中文 4 字名（监控/评估/诊断/整定/处置/报告/配置/系统） |
| status | varchar(16) | NOT NULL, CK IN (CORE/ENABLED/MAINTENANCE/UNINSTALLED) | 4 态状态机 |
| version | varchar(32) | NULL | 语义化版本 (e.g. "2.1.0") |
| is_core | boolean | NOT NULL DEFAULT false | 基础模块，不可卸载不可禁用 |
| order_index | int | NOT NULL | 显示排序（1..8） |
| dependencies | JSONB | NOT NULL DEFAULT '[]' | 依赖 module_key 列表（兼容 modules.py deps） |
| maintenance_window | JSONB | NULL | `{start_at, end_at, progress_pct, message}` 维护横幅信息 |
| installed_at | timestamptz | NULL | 安装时间（UNINSTALLED→ENABLED 写入） |
| last_maintenance_at | timestamptz | NULL | 最近维护结束时间 |
| updated_by | int | FK→user |  |
| updated_at | timestamptz | NOT NULL DEFAULT now() |  |
| created_at | timestamptz | NOT NULL DEFAULT now() |  |

索引：`idx_module_plugin_status(status)`；`uniq_module_plugin_key(module_key UNIQUE)`；
兼容策略：启动时若表空，从 MODULES 字典写 8 条种子（is_core 对应 monitor/assess/reports/config/system）。

#### M-02 workbench_window_summary（三窗口 KPI + 趋势 Flags 预计算表）

> 支撑 W1 WINDOWS 与首屏快速渲染。Celery 5min 全量刷新。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigint | PK, SERIAL |  |
| scope_type | varchar(16) | NOT NULL | FACTORY/AREA/UNIT/LOOP/GLOBAL |
| scope_id | int | NOT NULL | 对应实体 ID；GLOBAL=0 |
| window | varchar(8) | NOT NULL, CK IN (24h/7d/30d) | 窗口 |
| window_start | timestamptz | NOT NULL | 窗口左端 |
| window_end | timestamptz | NOT NULL | 窗口右端 |
| score | numeric(6,3) | NOT NULL | 综合得分（0-100） |
| status | varchar(16) | NOT NULL | 对应 KpiNodeStatus（EXCELLENT..INCONCLUSIVE） |
| loop_count | int | NOT NULL | 参评回路数 |
| good_value_rate | numeric(6,3) | NULL |  |
| auto_mode_rate | numeric(6,3) | NULL |  |
| effective_auto_rate | numeric(6,3) | NULL |  |
| steady_rate | numeric(6,3) | NULL |  |
| accuracy_rate | numeric(6,3) | NULL |  |
| fast_rate | numeric(6,3) | NULL |  |
| oscillation_rate | numeric(6,3) | NULL |  |
| saturation_rate | numeric(6,3) | NULL |  |
| instrument_fault_rate | numeric(6,3) | NULL |  |
| score_trend | JSONB | NOT NULL | 24h=24 点 / 7d=7 点 / 30d=15 点，sparkline 数组 `[{t,v}]` |
| flags | JSONB | NOT NULL DEFAULT '[]' | 趋势 Flags：`[{kind:'dip'|'spike'|'deterioration'|'jump', t, severity, desc}]` |
| snapshot_at | timestamptz | NOT NULL DEFAULT now() | 计算时间 |

复合 PK（业务唯一）：`UNIQUE(scope_type, scope_id, window, window_end)`；
索引：`idx_ws_scope_window(scope_type, scope_id, window)`。

#### M-03 event_bus（跨模块事件归一总线 · 铃铛未读 + SLA + 趋势 flags 统一入口）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigint | PK, SERIAL |  |
| source_module | varchar(16) | NOT NULL | monitor/assess/diagnosis/tuning/handling/alert/system |
| event_type | varchar(32) | NOT NULL | 见下枚举 |
| severity | varchar(8) | NOT NULL, CK IN (INFO/WARN/ERROR/CRITICAL) |  |
| scope_type | varchar(16) | NULL | 关联范围 |
| scope_id | int | NULL |  |
| loop_id | int | FK→loop_ledger NULLABLE | 关联回路 |
| order_id | int | FK→handling_order NULLABLE | 关联工单 |
| record_id | int | FK→tuning_record NULLABLE | 关联整定 |
| tag_id | int | FK→diagnosis_tag NULLABLE | 关联异常 |
| alert_event_id | int | FK→alert_event NULLABLE | 关联预警 |
| title | varchar(200) | NOT NULL | 铃铛卡片首行 |
| body | text | NULL | 详情（支持抽屉展开） |
| metadata | JSONB | NOT NULL DEFAULT '{}' | 扩展（sla_level, disposition 等） |
| occurred_at | timestamptz | NOT NULL | 事件发生时间 |
| read_by_users | JSONB | NOT NULL DEFAULT '[]' | 已读 user_id 数组（避免另建关联表，够用） |
| created_at | timestamptz | NOT NULL DEFAULT now() |  |

event_type 枚举（对应原型 7 类 + 扩展）：
`ALERT_NEW` / `DIAG_TAG_OPENED` / `DIAG_TAG_CONFIRMED` / `DIAG_CONCL_READY` / `ORDER_CREATED` / `ORDER_SLA_WARN` / `ORDER_SLA_BREACH` / `ORDER_REOPENED` / `ORDER_CLOSED` / `TUNE_BATCH_READY` / `TUNE_COMPLETED` / `TUNE_ROLLBACK` / `MODULE_STATUS_CHANGED` / `TREND_FLAG_DETECTED` / `CONFIG_CHANGED`

索引：`idx_eb_read( (cardinality(read_by_users)=0) ) WHERE cardinality(read_by_users)=0` 未读 GIN；
`idx_eb_scope(scope_type, scope_id, occurred_at DESC)`；
`idx_eb_user_read(user_id)` —— 用 `jsonb_ops` 对 read_by_users 建 GIN 以支持 "我未读" 查询。
清理策略：Celery 日任务归档 > 90 天的历史事件到 event_bus_archive（同结构分区表）。

#### M-04 sla_policy + 三列调整（SLA 到期管理实体）

**sla_policy 表**：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | int | PK |  |
| action_type | varchar(32) | NOT NULL | 对应 HandlingOrder.action_type（8 类） |
| priority | varchar(8) | NOT NULL, CK IN (LOW/MEDIUM/HIGH/CRITICAL) |  |
| warn_minutes | int | NOT NULL | 距到期 warn 阈值（分钟） |
| breach_minutes | int | NOT NULL | 到期总阈值（分钟） |
| is_default | boolean | NOT NULL DEFAULT false | 每 action_type 一个默认 |
| scope_type | varchar(16) | NULL | 可按范围覆盖 |
| scope_id | int | NULL |  |
| created_at | timestamptz | NOT NULL DEFAULT now() |  |

UK：`UNIQUE(action_type, priority, COALESCE(scope_type,''), COALESCE(scope_id,0))`
种子：8 类 × 4 级 = 32 条默认（CRITICAL：15min warn / 60min breach；LOW：4h warn / 24h breach）。

**handling_order 新增 3 列**（Alter，不破坏）：

| 新列 | 类型 | 说明 |
|---|---|---|
| sla_policy_id | int FK→sla_policy | 应用的策略 |
| sla_deadline_at | timestamptz | 截止时间（PENDING/EXECUTING/VERIFYING 三态有效） |
| sla_stage | varchar(8) CK IN (NONE/WARN/BREACH) | 当前级别，1min sweep 更新 |

#### M-05 tuning_batch + tuning_batch_records 关联表（整定批次 + 前置阻塞）

**tuning_batch**：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | int | PK |  |
| batch_no | varchar(32) | UK, NOT NULL | 如 `B-20260820-001` |
| title | varchar(200) | NOT NULL | 批次名称 |
| scope_type | varchar(16) | NOT NULL | 关联装置/单元 |
| scope_id | int | NOT NULL |  |
| status | varchar(16) | NOT NULL, CK IN (BLOCKED/PENDING/READY/RUNNING/COMPLETED/CANCELLED) | BLOCKED=前置依赖未闭合 |
| prereq_order_ids | JSONB | NOT NULL DEFAULT '[]' | 必须先 CLOSED 的 handling_order.id 数组 |
| block_reason | varchar(500) | NULL | BLOCKED 时的文案 |
| scatters_before | JSONB | NULL | 批次内整定前散点快照 `[{loop_id,score,kpi1..6}]` |
| scatters_after | JSONB | NULL | 批次后快照（闭合时写） |
| owner_id | int | FK→user | 批次负责人 |
| expected_start_at | timestamptz | NULL | 计划开始 |
| actual_start_at | timestamptz | NULL | 实际开始 |
| completed_at | timestamptz | NULL |  |
| created_at | timestamptz | NOT NULL DEFAULT now() |  |

**tuning_batch_records**（N:M 关联）：

| 字段 | 类型 | 约束 |
|---|---|---|
| batch_id | int | PK FK→tuning_batch |
| tuning_record_id | int | PK FK→tuning_record |
| sort_order | int | NOT NULL | 批次内顺序 |

#### M-06 trend_flags（趋势标注点，支撑 W1/W3 flags 气泡）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigint | PK |  |
| scope_type | varchar(16) | NOT NULL |  |
| scope_id | int | NOT NULL |  |
| loop_id | int | FK NULLABLE | 可针对回路 |
| window | varchar(8) | NOT NULL | 24h/7d/30d |
| kind | varchar(16) | NOT NULL | dip/spike/deterioration/jump/oscillation_start/saturation_event |
| severity | varchar(8) | NOT NULL | INFO/WARN/ERROR/CRITICAL |
| flagged_at | timestamptz | NOT NULL | 时间点 |
| metric_name | varchar(32) | NULL | score/steady_rate/.. |
| prev_value | numeric | NULL |  |
| curr_value | numeric | NULL |  |
| delta_pct | numeric | NULL | 变化率（便于排序"最大恶化"） |
| description | varchar(500) | NULL |  |
| created_at | timestamptz | NOT NULL DEFAULT now() |  |

索引：`idx_tf_scope(scope_type,scope_id,window,flagged_at DESC)`；
来源：Celery 5min 扫描 `workbench_window_summary.score_trend` 差分 > 阈值即写入。

#### M-07 staff_workload（非物化版人员负载表，MV-01 另建物化）

> 本张表可直接写，也可只建 MV。**建议 MV 为主 + 本表作为冷备**。此处仅记录字段定义，实际落库走 MV-01。

| 字段 | 同 MV-01 列，见 §1.3 |
|---|---|

#### M-08 wb_cache_log（BFF 缓存命中/失效日志 · 性能调优观测）

> 运维用；小表，保留 7 天。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK |  |
| cache_key | varchar(200) | WBFF_CACHE:{tab}:{scope}:{window}:{ts5} |
| hit | boolean | 命中=TRUE 未命中=FALSE |
| build_ms | int | 构建耗时（ms） |
| endpoint | varchar(64) | A-01..A-13 代码 |
| user_id | int |  |
| created_at | timestamptz |  |

---

### 1.3 物化视图（MV-01 ~ MV-03 · 5min CONCURRENTLY 刷新）

> 原则：所有"首屏聚合 SQL ≥ 3 JOIN"都抽为 MV，避免 BFF 每次跑重查询。

#### MV-01 mv_staff_workload（人员 6 元组负载）

```sql
CREATE MATERIALIZED VIEW mv_staff_workload AS
SELECT
  u.id                                   AS user_id,
  u.real_name                            AS name,
  u.role_code                            AS role,
  COUNT(ho.id)                           AS total_tasks,
  COUNT(*) FILTER (WHERE ho.status='PENDING')    AS pending_cnt,
  COUNT(*) FILTER (WHERE ho.status='EXECUTING')  AS executing_cnt,
  COUNT(*) FILTER (WHERE ho.status='VERIFYING')  AS verifying_cnt,
  COUNT(*) FILTER (WHERE ho.status='CLOSED')     AS closed_cnt_7d,
  COUNT(*) FILTER (WHERE ho.sla_stage='BREACH'
                    AND ho.status NOT IN ('CLOSED','CANCELLED')) AS overdue_count,
  ROUND(
    COUNT(*) FILTER (WHERE ho.closed_at IS NOT NULL
                     AND ho.closed_at <= ho.sla_deadline_at)::numeric
    / NULLIF(COUNT(*) FILTER (WHERE ho.closed_at IS NOT NULL),0) * 100,
    1)                                   AS sla_ontime_rate,
  COUNT(*) FILTER (WHERE ho.reopen_count > 0) AS reopen_total
FROM users u
LEFT JOIN handling_order ho
  ON ho.handler_id = u.id
  AND ho.created_at >= now() - INTERVAL '14 days'
WHERE u.is_active = TRUE
  AND u.role_code IN ('IC_ENGINEER','PE_ENGINEER','SPONSOR','EXPERT')
GROUP BY u.id, u.real_name, u.role_code
WITH DATA;
CREATE UNIQUE INDEX idx_mv_staff_pk ON mv_staff_workload(user_id);
```

#### MV-02 mv_diagnosis_pareto（异常类型 Pareto）

```sql
CREATE MATERIALIZED VIEW mv_diagnosis_pareto AS
SELECT
  dt.category                          AS root_cause,
  COUNT(*)                             AS total,
  COUNT(*) FILTER (WHERE dt.status='ACTIVE')      AS active_cnt,
  COUNT(*) FILTER (WHERE dt.status='SUPPRESSED')  AS suppressed_cnt,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct,
  ROUND(SUM(CASE WHEN dl.score IS NOT NULL THEN (100-dl.score) ELSE 0 END)/NULLIF(COUNT(*),0),1) AS avg_impact
FROM diagnosis_tag dt
LEFT JOIN loop_ledger dl ON dl.id = dt.loop_id
WHERE dt.created_at >= now() - INTERVAL '30 days'
GROUP BY dt.category
ORDER BY total DESC
WITH DATA;
CREATE UNIQUE INDEX idx_mv_pareto_pk ON mv_diagnosis_pareto(root_cause);
```

#### MV-03 mv_handling_funnel（处置漏斗 4 泳道计数 + 超期数）

```sql
CREATE MATERIALIZED VIEW mv_handling_funnel AS
SELECT
  scope_type,
  scope_id,
  COUNT(*) FILTER (WHERE status='PENDING')     AS lane_pending,
  COUNT(*) FILTER (WHERE status='EXECUTING')   AS lane_executing,
  COUNT(*) FILTER (WHERE status='VERIFYING')   AS lane_verifying,
  COUNT(*) FILTER (WHERE status IN ('CLOSED','REOPENED','CANCELLED')) AS lane_done_7d,
  COUNT(*) FILTER (WHERE sla_stage='BREACH' AND status NOT IN ('CLOSED','CANCELLED')) AS overdue_total,
  COUNT(*) FILTER (WHERE reopen_count > 0)     AS reopen_cnt
FROM handling_order
WHERE created_at >= now() - INTERVAL '30 days'
GROUP BY GROUPING SETS ((scope_type, scope_id), ())  -- 全厂汇总行 scope_type='GLOBAL'
WITH DATA;
CREATE INDEX idx_mv_funnel_scope ON mv_handling_funnel(scope_type, scope_id);
```

**刷新调度**：Celery beat 新增 `refresh-workbench-mv@5min`：
```python
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_staff_workload;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_diagnosis_pareto;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_handling_funnel;
```
（PG 16+ CONCURRENTLY 需要 UNIQUE INDEX，上方三个 MV 已满足。）

---

### 1.4 现有模型字段调整（M-A ~ M-G · 均为 ADD COLUMN / ADD INDEX，无破坏性）

| ID | 表 | 变更 | 原因 |
|---|---|---|---|
| M-A | diagnosis_tag | ADD `disposition_state` varchar(16) CK (UNADDRESSED/CONVERTED/ACK_REVIEWED/IGNORED) DEFAULT 'UNADDRESSED' | 支撑 W9 CONCL 三态标签（原型 disposition 三态：已转任务 / 待确认 / 已采纳） |
| M-B | diagnosis_tag | ADD `sla_deadline_at` timestamptz, ADD `sla_stage` varchar(8) | 关键异常 Tag 自身 SLA；原型 LOOPS.sla_due 气泡 |
| M-C | diagnosis_result | ADD `recommended_category` varchar(32) — 和 Tag.category 对齐；ADD `evidence_summary` text | CONCL 时间线需要"诊断→建议→采纳"因果链简述 |
| M-D | handling_order | ADD `reopen_count` int NOT NULL DEFAULT 0 | TASKS.reopen 计数；REOPENED 状态流转时自增 |
| M-E | handling_order | ADD `reopen_reasons` JSONB NOT NULL DEFAULT '[]' | 每次重开记录 `[{at,by,reason,kpi_before_reopen}]` |
| M-F | kpi_node_snapshot_daily | ADD INDEX `idx_kpi_daily_scope_date(scope_type,scope_id,snapshot_date DESC)` | PLANTS/UNITS 排名/热力高频查询加速 |
| M-G | users | ADD `lane_capacity` int NOT NULL DEFAULT 6 | STAFF 泳道容量上限（看板拖拽时 UI 提示过载） |

---

### 1.5 数据模型总览 ER 关系（文字版）

```
PlantNode ──(scope_type/scope_id)──▶ workbench_window_summary
                                    ◀── trend_flags
                                    ◀── event_bus
LoopLedger ──┬── LoopTagMapping(×7)
             ├── DiagnosisTask ── DiagnosisResult ── DiagnosisTag (+disposition_state)
             │                                           └── LoopActionItem ──┐
             ├── TuningRecord ── tuning_batch_records ── TuningBatch ──▶ handling_order(prereq)
             │                                                                 │
             └── HandlingOrder (+sla*/reopen_*) ◀─── sla_policy ──────────────┘
                   ▲
Users ─────────────┴──────────▶ mv_staff_workload(MV) ── staff_workload_od_dot
                                                          (超期闪烁圆点)
MODULES (旧静态) ── seed ──▶ module_plugin(status CORE/ENABLED/MAINTENANCE/UNINSTALLED)
                                   │ 广播 MODULE_STATUS_CHANGED
                                   ▼
                              event_bus ── WS ──▶ 铃铛未读 + Toast
```

---

## 2. 业务逻辑优化（B-01 ~ B-12）

> 原则：不破坏现有业务状态机；新增均为"包装层 / 聚合层 / 调度层"。

| ID | 业务逻辑 | 现状 → 目标 | 实现要点 |
|---|---|---|---|
| B-01 | **全局范围-窗口联动** | 各页面独立 → 5 Tab 共享 `useWorkbenchStore` 全局 state：`{scopeType, scopeId, window, customStart, customEnd, lastRefreshAt}`；范围切换时 Tab 间重取数据但不换路由 | 前端 Pinia + A-11 aggregate 批量端点；后端所有 Tab 端点接受统一查询参数 `?scope_type=&scope_id=&window=` |
| B-02 | **模块 4 态状态机** | 仅 ENABLED/DISABLED 进程内布尔 → CORE/ENABLED/MAINTENANCE/UNINSTALLED + 维护横幅 + 面纱 veil + Tab 三色 dot (on/maint/off) + pill（count/maint/off） | M-01 表持久化；PUT /api/v1/modules/:key/status 新端点；MAINTENANCE 模式 PUT 回调 `progress_pct`（如 upgrade 脚本上报）；MAINTENANCE→ENABLED 时若依赖不满足自动回滚并告警；**进程内缓存仍然保留（invalidation 依赖 save 写库）** |
| B-03 | **事件归一推送** | Alert/诊断/处置/整定各自独立推送 → 所有业务状态变更必经 `EventBus.publish(Event)` 写 M-03 + 铃铛 WS `ws://host:17101/api/v1/ws/bell` 推送未读计数增量 + Toast 摘要；铃铛点击抽屉按模块分组 | `EventBus` class 落位 `app/core/event_bus.py`；publish 内部分两阶段：DB 写 + WS 广播；AlertEvent 变更时自动钩一份到 event_bus（AlertService 后调 event_bus.publish），但**避免循环**（加来源标识 ALERT_DUPLICATE 跳过） |
| B-04 | **SLA 到期两级升级** | 无 → 新建工单根据 action_type+priority 查 sla_policy 写 sla_deadline_at；Celery `sla-sweep@1min`：扫子集 `WHERE status IN (P,EX,V) AND sla_stage != 'BREACH' AND sla_deadline_at IS NOT NULL` → 进入 warn 窗口写 ORDER_SLA_WARN event → 过期写 ORDER_SLA_BREACH event + UI 红底 od-dot lampPulseRed 动画 | 1min sweep 必须走索引（已建 idx_handling_order_active_sla: `WHERE status IN (..) AND sla_deadline_at IS NOT NULL`）；一次 UPDATE 批量而非逐行 Python；避免对所有行扫 |
| B-05 | **工单重开闭环** | REOPENED 状态已存在但无可视化计数 → M-D `reopen_count` + M-E `reopen_reasons`；重开时自动：(1) 固化当前 kpi_after 作为 reopen_reasons[].kpi_before_reopen (2) 重置 sla_deadline_at 重新计时 (3) 写 ORDER_REOPENED event (4) 对比同回路历史 KPI 并在摘要带标记 `Δ=-3.2` | 加 `reopen_order(order_id, by, reason)` 专用 service，不允许直接 ORM update |
| B-06 | **整定批次前置阻塞** | TuningRecord 互相独立 → GET /tuning/pending-queue 返回批次；若 batch.prereq_order_ids 中有任一 ∈ {PENDING/EXECUTING/VERIFYING}，则 batch.status = BLOCKED 且 "开始整定" 按钮禁用 + block_reason 显示"前置工单 CL-2026-0819 未闭合" | M-05 tuning_batch；调度端批量查询而非 N+1；批次 COMPLETED 时：回写各 TuningRecord COMPLETED + 固化 batch.scatters_before/after 供 A-13 scatter 接口返回 |
| B-07 | **5 min 预计算** | BFF 每次聚合查 → Celery 5min 任务：`workbench-precalc@5min`：(1) 写入/更新 workbench_window_summary（scope=GLOBAL×3 / FACTORY×n×3 / AREA×n×3 / UNIT×n×3 + 重要回路 Top200×3）；(2) 差分检测写入 trend_flags；(3) REFRESH MATERIALIZED VIEW CONCURRENTLY mv_* 三个 | 计算顺序：KPI 汇总 → MV → flags；避免长事务：每 scope 一次 UPSERT，5min 任务超时限制 4min，否则杀 + 告警 |
| B-08 | **趋势 flags 差分检测** | 无 → 对 score_trend 数组做：点间差分 % > 阈值（24h/7d/30d 阈值不同）或相邻点二阶差分符号反转 2 次（oscillation）写入 trend_flags；severity 按百分位分档；event_bus TREND_FLAG_DETECTED CRITICAL 级只推送 TOP10 | 算法：纯 numpy scipy 差分；阈值配置入 sys_config（runtime 可调）：`trend_flag_*_threshold` |
| B-09 | **适用性分级漏斗 L0~L4** | 已有 L3 整定门禁 ERR_TUNING_FITNESS_INSUFFICIENT → 工作台三态卡片展示：(a) 诊断页 L0/L1 阻止 L2 的横幅 "诊断数据不足，无法进入根因分析" + (b) 整定页 L2/L3 红色徽章条 "未达整定准入（score=51.2 < 60）" + (c) L4 绿色徽章"完全满足整定" + (d) 适用性得分进度条 0..100 + 参评分母自洽展示 | 复用 fitness_service；UI 仅新增展示组件 FitnessBadge；后端 fitness 返回结构加 `gates_passed:[bool×4] gate_desc:[str×4]` 字段 |
| B-10 | **诊断结论 disposition 三态** | DiagnosisResult 产生 Tag 后无"采纳链路" → M-A `disposition_state`；Tag 创建=UNADDRESSED；审核 LoopActionItem=CONVERTED；人工确认不处理=ACK_REVIEWED；配置级忽略=IGNORED；UI CONCL 时间线每结论左侧三态色点 | 三态流转函数 `update_disposition(tag_id, new_state, by)`；CONVERTED / IGNORED 双终点不可逆 |
| B-11 | **人员负载实时聚合** | 查聚合表无索引慢 → MV-01 5min 刷新 + od-dot（BREACH 红圆点 lampPulseRed CSS 动画）+ 泳道容量 M-G `lane_capacity` 上限超过时 UI 黄条提示；看板拖拽时若目标 handler 已达 capacity 出确认框"该人员已超负载，确认分配？" | A-08 staff-workload 走 MV，TTL 60s 缓存；od-dot 用 CSS：`@keyframes lampPulseRed { 0%,100%{opacity:1} 50%{opacity:.25} }` |
| B-12 | **整定前后 Δ 对比固化** | TuningRecord.kpi_before/after 分散 → batch COMPLETED 时聚合 scatters_before/after + 单记录 COMPLETED 时 event_bus TUNE_COMPLETED + 报告页自动"本整定前后 Δ" 三窗口对比；若 ROLLED_BACK 则 TUNE_ROLLBACK event + 原因记录入 event.metadata | 散点 Δ 计算：after_score - before_score 为 Δ 值；A-13 tuning-scatters 返回 `points[{loop_id,before,after,delta,significance}]`；支持按批次过滤 |

---

## 3. API 接口完善（A-01 ~ A-13 新增 · A-E1 ~ A-E8 增强）

> 统一 envelope：`{code, message, data, paging:{total,page,size}, meta:{snapshot_at, cache_hit}}`；meta.cache_hit 供前端显示 "1s 前缓存 / 实时" 状态。
> RBAC 权限沿用现有 5 角色：ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR / EXPERT。

### 3.1 新增端点

| 编号 | 方法 · 路径 | 权限 | 参数 | 返回 data | 功能 |
|---|---|---|---|---|---|
| A-01 | GET /v1/workbench/overview | 全员登录 | scope_type, scope_id, window, custom_start?, custom_end? | `{summary:W1×3windows, plants:[W3], units:[W4], pareto:[W7], roots:[W8], events:recent 10, funnel:W14}` | Tab1 系统总览；3 窗口并行但只查一次 scope+window；用 `WBFF_CACHE:ov:{scope}:{window}:{ts5}` 30s TTL |
| A-02 | GET /v1/workbench/evaluation | 全员登录 | 同上 + metric_filter? | `{kpi_cards:W5×6, unit_heatmap:W4, loops_ranked:[score×6指标], scope_history×3window}` | Tab2 性能评估；热力矩阵 N/A 斜纹用 CSS 不查 |
| A-03 | GET /v1/workbench/diagnosis | 全员登录 | 同上 + operator_filter? + only_active? | `{open_tags:[W6], concl_timeline:[W9 disposition三态], pareto:[W7], rootcause_top:[W8], rule_stats:{rule_id,name,hits,resolved_rate}}` | Tab3 回路诊断；L0/L1 阻止 L2 横幅从 fitness 返回 |
| A-04 | GET /v1/workbench/tuning | IC/PE/ADMIN/SPONSOR | 同上 + batch_status? | `{batches:[W11], pending_queue:[W12 blocked/ready], scatters:[W13], fitness_gates:{×4 gates}}` | Tab4 参数整定；BLOCKED 批次 block_reason 返回 |
| A-05 | GET /v1/workbench/operations | IC/PE/ADMIN/SPONSOR/EXPERT | 同上 + lane_filter? + handler_id? | `{kanban:{pending:[],executing:[],verifying:[],done:[]}×W14, staff_load:[W15], sla_summary:{warn,breach,total_active}, reopen_list:[{order_id,count,reason,Δ}]}` | Tab5 问题处置；泳道每列 ≤50 条（分页预取），剩余用 "展开 +128" 按钮拉更多 A-09 |
| A-06 | GET /v1/workbench/window-summary | 全员登录 | scope_type,scope_id,window | `workbench_window_summary M-02` | 单窗口快速取；被 A-01..A-05 内部复用，也对外开放 |
| A-07 | GET /v1/workbench/trend-flags | 全员登录 | scope,window,kind?,severity? | `[{M-06 字段} × N]` | KPI 卡片气泡点数据源 |
| A-08 | GET /v1/workbench/staff-load | 除 EXPERT | — | `[mv_staff_workload ×N 含 od_dot 布尔]` | 人员负载 hbar-row 柱状图 |
| A-09 | GET /v1/workbench/operations/lane-more | 同 A-05 | lane, cursor_id, limit=50 | `{items:[], next_cursor?}` | 看板展开更多；cursor 分页避免 offset 深翻 |
| A-10 | GET /v1/workbench/plugins | 全员登录 | — | `[{M-01 module_plugin} × 8, global_meta:{last_refresh_at, data_trusted, data_delay_ms}]` | 状态栏 + 插件列表页 + 维护横幅 |
| A-11 | **POST /v1/workbench/aggregate** | 全员登录 | body: `{requests:[{tab,params}]*}` | `{results:[{tab, data, cache_hit, ms}]}` | **首屏批量预取**：首屏渲染 5Tab 并发聚合到 1 次 HTTP；单 Tab 仍可分别用 A-01~A-05 |
| A-12 | POST /v1/workbench/events/read | 全员登录 | body: `{event_ids:[]}` 或 `{all:true}` | `{marked: N}` | 铃铛批量已读；读操作写 event_bus.read_by_users JSONB 追加 |
| A-13 | GET /v1/workbench/tuning-scatters | 同 A-04 | batch_id?, scope?, window? | `points:[{loop_id, before, after, delta, significance, batch_no, order_no}]` | 散点 Δ 对比图（原型 SCATTER 11 点） |

### 3.2 现有端点增强

| 编号 | 原端点 | 增强 |
|---|---|---|
| A-E1 | GET /v1/handling-orders | 返回字段追加 sla_stage, sla_deadline_at, reopen_count；支持 `?sla_stage=BREACH` 过滤 |
| A-E2 | GET /v1/diagnosis/tags | 返回 disposition_state；支持 `?disposition_state=` 过滤 |
| A-E3 | GET /v1/tuning/records | 支持 `?batch_id=` 过滤；返回字段追加 `batch_no` 嵌入值 |
| A-E4 | GET /v1/system/modules | 保持只读兼容；**修改/开关只走 PUT /v1/workbench/modules/:key/status**（A-E6 明确定义） |
| A-E5 | GET /v1/auth/me | 返回追加 `unread_events_count`（铃铛红点）；WS 也推送 |
| A-E6 | PUT /v1/workbench/modules/:key/status | ADMIN 权限；body: `{status, maintenance_window?}`；返回 `{module_plugin, event_id}`；MAINTENANCE 模式下 30s 轮询 progress_pct |
| A-E7 | GET /v1/plant-nodes | 返回追加 `loops_count`（本树后代回路数），范围选择器展示数量徽标 |
| A-E8 | GET /v1/realtime/snapshot | scope 级批量快照：`?scope_type=&scope_id=` 返回本 scope 下所有回路 PV/SP/OP/MODE/P/I/D 最新值，供顶栏实时数字刷新（1s WS）批量取；减少 N 回路独立请求 |

### 3.3 WebSocket 通道补充

| 通道 | 路径 | 推送内容 |
|---|---|---|
| WB-BELL | /api/v1/ws/bell | `{type, unread_count, latest_event}` —— event_bus 写入后推送；订阅绑定 user_id |
| WB-SUMMARY | /api/v1/ws/summary | `{scope, field, value, ts}` —— 实时 PV/SP/OP/MODE/P/I/D 变化批量推送，复用现有 ws_realtime 但加 scope 订阅过滤 |

---

## 4. 系统架构调整

> 约束：**不引入新中间件**（复用 PG + Redis + Celery 三件套）；**不修改旧 API**（所有新端点 A-01~A-13 纯新增）；**兼容原 8 菜单多页面**（工作台是新的"单屏视图"，旧页面保留路由并存）。

### 4.1 架构图（四层横向 + 一条数据流垂直）

```
┌─────────────────────────────────────────────────────────────────────────┐
│  [前端 Vue3 + Vite + vue-vben-admin]                                    │
│   ├─ routes: /workbench 单屏 100vh (新增路由，独立于 /monitor)           │
│   ├─ stores/useWorkbenchStore.ts (Pinia, scope+window 全局)             │
│   ├─ components/workbench/  (5 Tab 组件 × 40+ 子卡片)                    │
│   └─ ws: /ws/bell + /ws/summary + /ws/realtime (复用)                   │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ HTTP / WS
┌──────────────────────────▼──────────────────────────────────────────────┐
│  [后端 BFF 层 WorkbenchBFF]  app/api/v1/workbench/  (新增模块)          │
│   ├─ endpoints: overview.py · evaluation.py · diagnosis.py · tuning.py  │
│   │            · operations.py · plugins.py · aggregate.py  (共7文件)    │
│   ├─ service:  wb_summary_builder.py · trend_flag_detector.py           │
│   │           · event_bus_publisher.py · wb_cache.py                    │
│   └─ 缓存层：WBFF_CACHE Redis (30s TTL, key=tab:scope:window:ts5)       │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ 查询 / 写入
┌──────────────────────────▼──────────────────────────────────────────────┐
│  [业务域层 Services] （复用现有，无破坏性修改）                           │
│   monitor / assessment / diagnosis_v2 / tuning / handling / alert /     │
│   fitness / modules_manager + 新增 EventBus 统一入口                     │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ ORM
┌──────────────────────────▼──────────────────────────────────────────────┐
│  [存储层 PostgreSQL 17102 + Redis 17103 + TDengine 17104/17115]          │
│   PG：新增 7 表 + 3 MV + 若干 INDEX / ALTER                              │
│   Redis：WBFF_CACHE 30s · EB:UNREAD user_id→count （1min TTL 同步 DB）  │
│   TDengine：无 schema 变更（仅计算，被 KpiSnapshot 复用）                │
└─────────────────────────────────────────────────────────────────────────┘
              │
              ▼ Celery (Worker + Beat 随后端 lifespan 启动，不另起)
    ┌───────────────────────────────────────────────────┐
    │ 4 条新增 beat 任务：                                │
    │  @5min workbench-precalc （M-02 + M-06 + MV×3）   │
    │  @1min sla-sweep         （SLA warn→breach）       │
    │  @daily event-archive     （>90d event_bus 归档）  │
    │  @1min wb-cache-cleanup   （清理 wb_cache_log 7d） │
    └───────────────────────────────────────────────────┘
```

### 4.2 最小侵入原则落地

1. **不修改 `core/modules.py` 字典结构**：仅在 save_enabled_modules 逻辑中**同步写 module_plugin** 表（M-01），兼容旧代码；读路径先查表空则 fallback 到静态字典，保证进程启动零风险。
2. **模块热插拔兼容**：现有 `is_module_enabled(key)` 函数签名不变，内部实现升级为 `(module_plugin.status in (CORE,ENABLED))`；MAINTENANCE 和 UNINSTALLED 都视为 enabled=False 返回。
3. **所有新增端点挂 `v1_router.include_router(workbench.router, prefix='/workbench', tags=['工作台'])`**，不修改任何旧路由文件；在 main.py create_app 末尾条件挂载（总是挂载，不受模块开关影响）。
4. **Celery 任务新文件**：新增 `app/tasks/workbench.py`，在现有 Celery app 下 `@celery_app.task` 注册，不改动旧任务文件。
5. **前后端隔离**：前端新增 `apps/web-antd/src/views/workbench/` 目录，`src/router/routes/modules/clpm.ts` 追加一条 `/workbench` 路由，原 8 菜单路由不改。

### 4.3 性能目标与估算

| 指标 | 目标 | 手段 |
|---|---|---|
| A-11 首屏 aggregate（5Tab 批量） | < 500ms（Redis 命中）；< 1.8s（未命中 + 预计算表读） | WBFF_CACHE 30s + 5min 预计算表 + MV |
| 铃铛未读 | WS 推送 < 200ms 延迟；手动刷新 < 80ms | Redis EB:UNREAD 计数器；DB GIN 索引 |
| 看板 A-05 | < 400ms | MV-03 + 泳道按 50 条 limit + cursor |
| 范围切换响应 | < 250ms（已有缓存窗口） | 多 window 一次 SQL IN 批量查 |
| 5min 预计算任务 | < 4 min 完成（留 1min 缓冲） | 每 scope upsert 单事务；按 UNIT 并行 celery group（10 并发） |

---

## 5. 功能模块补充（45 项 × P0/P1/P2）

> 编号规则：F-OV=总览 · F-EV=评估 · F-DG=诊断 · F-TN=整定 · F-OP=处置 · F-GL=全局

### 5.1 P0 功能（21 项 · 3 周交付 · M1+M2 前期）

| 编号 | 功能 | 原型映射 | 后端依赖 | 前端组件 |
|---|---|---|---|---|
| F-GL-01 | 单屏 100vh 工作台骨架（56+48+760+28）+ 5 Tab 路由 | 工作台全页 | — | WorkbenchLayout.vue × 1 |
| F-GL-02 | 顶栏：范围选择器 + 时间胶囊 + 可信徽章 + 铃铛 + 人员 | 顶栏 | A-E7 loops_count, A-E5 unread, A-12 batch-read | HeaderBar.vue + BellDrawer.vue |
| F-GL-03 | 状态栏：刷新时间 / 评估周期 / 时延 / 插件在线 | 底栏 | A-10 plugins | StatusBar.vue |
| F-GL-04 | useWorkbenchStore Pinia（scope+window 全局共享） | B-01 | — | stores/workbench.ts |
| F-GL-05 | 模块 4 态显示（Tab 三色 dot + pills + 面纱 veil + 维护横幅 banner） | B-02 dot/pill/veil/banner | A-10 plugins, A-E6 status PUT | ModuleStatusDot/Pill/Veil/Banner |
| F-OV-01 | 执行摘要：三窗口 KPI mini-kpi 卡（6指标×3=18）+ flags 气泡 | W1 WINDOWS | A-01 + A-07 | MiniKpiStrip.vue + FlagBubble.vue |
| F-OV-02 | 装置排名 risk-row（sparkline + lose_factors + alarm + overdue） | W3 PLANTS | A-01 plants[*] | RiskRow.vue + Spark.vue |
| F-OV-03 | 单元热力矩阵 heat（6指标 × N units，N/A 斜纹） | W4 UNITS | A-01 units[*] | HeatMatrix.vue + css N/A hatch |
| F-OV-04 | 异常 Pareto + 根因 TopN（双柱并排） | W7 W8 | A-01 pareto / roots | ParetoAndRoots.vue（柱 + 榜） |
| F-OV-05 | 处置看板漏斗（4 泳道计数 + 超期红底） | W14 TASKS | A-05 funnel | FunnelStat.vue |
| F-EV-01 | 6 项 KPI 卡片（数值 + 环比箭头 + sparkline） | W5 METRICS | A-02 kpi_cards | KpiCards.vue × 6 |
| F-EV-02 | 单元 × 6 指标热力（交互式悬浮 tooltip 数值） | W4 UNITS（评估版） | A-02 unit_heatmap | HeatMatrix 复用以 A-02 数据 |
| F-EV-03 | 回路排名表（按得分 + 6 指标 + 点击进入诊断 Tab） | W6 LOOPS 评估视角 | A-02 loops_ranked + router.push dg | LoopsRankTable.vue |
| F-DG-01 | 关键异常表（6 条 Top：spark + sla_due 倒计时 + conclusion 摘要） | W6 LOOPS 诊断版 | A-03 open_tags | AbnormalLoopsTable.vue |
| F-DG-02 | 诊断结论时间线（3 态 disposition 三态点标签） | W9 CONCL | A-03 concl_timeline | ConclTimeline.vue × disposition 组件 |
| F-DG-03 | 适用性 L0~L4 横幅（阻止 L2 红条 / 准入徽章 / 得分进度条） | B-09 漏斗 | A-03 fitness_gates + 诊断 v2 fitness | FitnessBadge.vue + GateBanner.vue |
| F-TN-01 | 整定批次列表（含 BLOCKED/READY/RUNNING 色点 + 前置依赖列表） | W11 BATCHES | A-04 batches | TuningBatchList.vue + DepsPillList.vue |
| F-TN-02 | 待整定队列（阻塞中灰化 / 可操作蓝绿） | W12 PENDING_TUNE | A-04 pending_queue + batch.block_reason | TuneQueueRow.vue |
| F-TN-03 | 整定前后散点（11 点 Δ 区分色：正绿负红） | W13 SCATTER | A-13 tuning-scatters | DeltaScatter.vue |
| F-OP-01 | 4 泳道看板（拖拽 + 展开更多 + 超期红底 od-dot 闪烁） | W14 TASKS kb 4-lane | A-05 kanban + A-09 lane-more | OpsKanban.vue × 4 LaneCol.vue |
| F-OP-02 | 人员负载 hbar-row（及时率 + 超期 od-dot + 容量条） | W15 STAFF | A-08 staff-load | StaffHBar.vue + od-dot (pulse CSS) |

### 5.2 P1 功能（16 项 · 3 周交付 · M2 中后期）

| 编号 | 功能 |
|---|---|
| F-GL-06 | A-11 aggregate 首屏批量预取 + loading 骨架屏（8 块骨架对应 A-11 results） |
| F-GL-07 | 铃铛抽屉：按模块分组列表 + 跳转目标 Tab 并高亮卡片（source_module→Tab 映射路由） |
| F-GL-08 | 维护模式：PUT A-E6 上传 maintenance_window.progress_pct → 面纱显示 `升级中 42%` 进度条 + 倒计时 |
| F-GL-09 | 自定义时间窗口（时间胶囊"自定义"按钮 → DatePicker → A-01 custom_start/end 下发） |
| F-OV-06 | 风险定位：装置点击 → 范围切换到该装置（store.setScope）+ 所有 Tab 自动刷新（全局联动） |
| F-OV-07 | 事件横条 ev-row：10 条近期归一事件，点击 → 铃铛抽屉定位项 |
| F-EV-04 | 评估得分趋势 slope-row（24h→7d→30d 三条斜率对比） |
| F-DG-04 | 诊断规则命中统计（A-03 rule_stats）+ 命中 Top3 规则名显示 |
| F-DG-05 | disposition 筛选器（三态三切换 + 汇总） |
| F-TN-04 | 整定批次详情抽屉：批次内所有 TuningRecord 列表 + 前置工单 tick/uncheck |
| F-TN-05 | 批次完成时自动Δ对比摘要带（绿 +12 条 / 红 -3 条 / 持平 1 条） |
| F-OP-03 | SLA 汇总卡：warn/breach/active（三色块 + 点击过滤看板） |
| F-OP-04 | 工单重开列表 reopen_list：每条显示原因 + ΔKPI 对比值（A-05 返回） |
| F-OP-05 | 看板泳道容量告警：拖拽到 handler > lane_capacity 时 yellow Toast 二次确认 |
| F-OP-06 | 工单卡片 detail drawer w480（点击卡片开抽屉）：full 字段 + 历史 KPI 对比 + 转整定按钮 |
| F-GL-10 | wb_cache_log 性能自观察页（系统→工作台缓存性能，仅 ADMIN 看） |

### 5.3 P2 功能（8 项 · 3 周交付 · M3）

| 编号 | 功能 |
|---|---|
| F-GL-11 | 深色主题切换（遵循现有 theme 规范，CSS 变量 1:1 覆盖） |
| F-GL-12 | 快捷键：Alt+1..5 切 Tab / Alt+R 刷新 / Alt+S 开范围选择器 |
| F-OV-08 | 模块插件管理页（A-10 + A-E6 完整版：版本/启停/维护/卸载向导 UI 四步） |
| F-EV-05 | 导出：评估排名 CSV/Excel（复用现有 reports 模块能力） |
| F-DG-06 | 诊断异常 Top 回路对比抽屉（3 回路并排放 6 指标雷达） |
| F-TN-06 | 整定散点回归拟合 + 置信区间（d3-regression） |
| F-OP-07 | 处置 SLA 策略管理界面（CRUD sla_policy，仅 ADMIN） |
| F-OP-08 | 看板多视图切换：泳道 / 甘特 / 表格（同一数据源三态） |

---

## 6. 实施路线图

### 6.1 阶段与里程碑

| 里程碑 | 周次 | 主题 | 交付验收门槛 |
|---|---|---|---|
| **M1 基础壳** | W01–W03 | 数据模型 + API 外壳 + 状态机 + 预计算 | 8 张新表 + 3 MV + alembic check 双向 OK；pytest 回归全通过（≥60% cov）；A-01..A-13 skeleton 端点 200 OK；Celery 4 条 beat 注册并单测触发 OK |
| **M2 P0 闭环** | W04–W06 | 21 项 P0 功能全闭环 + E2E 绿 | 5 Tab 全加载首屏 < 1.8s（未命中缓存） / < 500ms（命中）；E2E 场景 S1..S10 全部 PASS（见 §6.3）；21 项 P0 UI 人工走查通过；铃铛 WS 时延 < 200ms |
| **M3 增强优化** | W07–W09 | P1+P2 24 项 + 性能 + 上线 | P1 全量交付（16 项）；P2 交付 ≥6 项（F-OV-08/F-OP-07 必达，另 4 项 P2 可选精简）；Lighthouse Performance ≥85；ruff + eslint 无 error；正式部署 dev 环境；工作台菜单挂在导航顺序 0（首页替换为工作台） |

### 6.2 资源与工期估算（15.5 人周）

| 角色 | 人周 | 分解 |
|---|---|---|
| 后端工程师 | 6.5 | M1：3.0（8 models + alembic + 3 MV + 4 Celery + A-01..A-13 skeleton + EventBus + SLA）；M2：2.0（完善端点 A-01~A-05 全字段 + fitness_gates + tuning-scatters A-13 + RBAC）；M3：1.5（P1/P2 端点 + 性能调优 + 导出） |
| 前端工程师 | 7.0 | M1：1.0（骨架 + 路由 + store + HeaderBar/StatusBar + 4 态 dot/pill/veil/banner）；M2：3.5（21 项 P0 组件 × 1 天/项 约合 21 天 = 4.2w 压缩到 3.5w 可复用子卡）；M3：2.5（P1 16 项 + P2 6 项 = 22 项 × 0.6w/2 项 + E2E 协助） |
| QA | 1.5 | M1：0.3（后端单测审查 + alembic 双验证）；M2：0.7（E2E 场景 10 条 × 0.07w/条 + 人工走查）；M3：0.5（性能压测 + 回归） |
| 产品/设计 | 0.5 | M1：0.2（字段/规则对齐评审）；M2：0.2（P0 走查）；M3：0.1（增强验收） |
| **合计** | **15.5** | ≈ 4 人 × 4 周（实际 9 周日历，非满配并行） |

### 6.3 E2E 场景清单（M2 验收用 · S1~S10）

| ID | 场景 | 断言 |
|---|---|---|
| S1 | 登录后访问 `/workbench`，首屏渲染 ≤1.8s，5 Tab 无报错 | `perf.timeToInteractive < 1800`；console 无 ERR；所有 Tab 可点击 |
| S2 | 范围选择器选"装置A"，切换 Tab 保持范围 | Tab 间切换 scope_type/scope_id 不变；network 请求含相同参数 |
| S3 | 切换窗口 24h→7d→30d，三窗口 KPI 值不同且合法 | 3 次请求 score 值不全等；值域 [0,100] |
| S4 | 诊断 Tab 显示 6 条异常 + disposition 三态标签正确着色 | DOM 有 UNADDRESSED/CONVERTED/ACK_REVIEWED 三态 class 存在 |
| S5 | 整定 Tab 批次 BLOCKED → 前端"开始整定"按钮 disabled；满足前置后刷新变 READY | 前置工单 id 在 prereq_order_ids 中且 status=CLOSED 后 READY |
| S6 | 处置 Tab 看板 od-dot（超期工单）呈现红闪烁动画；拖拽到"已闭合"后 od-dot 消失 | `@keyframes lampPulseRed` 命中 BREACH 工单；拖拽后 status=CLOSED |
| S7 | 铃铛 WS：后台创建 AlertEvent → 1s 内前端 unread_count +1；点击"全部已读" → 计数清零 | WS push event 到达 <200ms；POST A-12 read all 返回 marked == N |
| S8 | 模块进入 MAINTENANCE → 对应 Tab 面纱显示 "升级中 42%"；回到 ENABLED → 面纱消失 | PUT A-E6 status=MAINTENANCE + progress_pct=42 → veil 渲染； |
| S9 | SLA 到期工单：模拟 sla_deadline_at 过期 → 1 分钟内 SLA stage BREACH + event_bus ORDER_SLA_BREACH 记录存在 | pytest monkeypatch 过期时间；Celery sweep 后断言 sla_stage='BREACH' |
| S10 | 首屏 aggregate 缓存命中：首次调用 cache_hit=false；30s 内第二次同参数 cache_hit=true，耗时降 ≥60% | A-11 两次调用 meta.cache_hit 对比；第二次 ms / 第一次 ms ≤ 0.4 |

### 6.4 风险与缓解（6 条）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| R1 5min 预计算任务超 4min | 中 | 高 | 先 scope 上限压测；超则拆 celery group 并限并发 10；MV REFRESH 独立任务隔离 |
| R2 event_bus 膨胀写慢 | 中 | 中 | 分区表按月；日归档；read_by_users GIN 索引；未读查询部分走 Redis 计数 |
| R3 模块 4 态切换与旧 modules.py 双写不一致 | 低 | 高 | 单入口 save_enabled_modules 同步写两边；启动自检对比两边差 10s 日志告警；health 端点暴露 discrepancy_count |
| R4 看板拖拽 SSE/WebSocket 多用户冲突 | 低 | 中 | 乐观锁 handling_order.version；后端 version 不匹配返回 409 + 前端 toast "工单已被他人更新" 并拉取最新 |
| R5 高密度 12 栅格 1600×900 在 1366×768 笔记版溢出 | 中 | 中 | 最小分辨率支持 1366：网格列降 10；卡片 padding 从 8→6；顶/底栏压缩到 48/24；状态栏滚动；设计评审 P0 前必测 |
| R6 首屏 BFF 未命中 > 2s（用户体感卡顿） | 中 | 高 | A-11 aggregate 前端并发超时兜底：先从 WBFF_CACHE 旧值（TTL 放宽到 60s stale-while-revalidate 语义）显示灰 overlay "更新中…" 后替换，避免白屏 |

### 6.5 W01 第一天起动手册（进入实施时的第一个 4 小时任务）

1. **backend/app/models/** 新建 7 文件：module_plugin.py / workbench_summary.py / event_bus.py / sla_policy.py / tuning_batch.py / trend_flags.py / wb_cache_log.py（§1.2 DDL → SQLAlchemy 2.0 declarative）
2. **backend/app/core/** 新增 `event_bus.py`（`publish()` 双阶段：DB+WS 存根）
3. **backend/app/tasks/** 新增 `workbench.py`（4 beat 任务 skeleton @celery_app.task）
4. `uv run alembic revision --autogenerate -m "add workbench 8 tables + 3 alters"` → 检查 upgrade/downgrade 双向脚本 → `uv run alembic upgrade head` → `uv run alembic downgrade -1` 回退 OK → 再升回
5. `uv run pytest -q` 全回归（350+ 用例）必须全绿
6. Commit message（此步需用户显式授权后才提交）：
   ```
   feat(workbench): M1-W01 7 tables + 3 alters + 3 MV + alembic migration

   - module_plugin 持久化 4 态状态机，替换静态字典读路径
   - workbench_window_summary 三窗口 KPI + flags 预计算表
   - event_bus 归一总线（铃铛/SLA/模块/趋势）
   - sla_policy + handling_order 3 列（sla_policy_id/deadline/stage）
   - tuning_batch + tuning_batch_records 批次+前置阻塞
   - trend_flags + wb_cache_log
   - diagnosis_tag disposition_state + diagnosis_result evidence
   - handling_order reopen_count/reasons + users lane_capacity
   - 3 MV: mv_staff_workload / mv_diagnosis_pareto / mv_handling_funnel
   ```

> ⚠️ **本方案交付完成（分析阶段）；未修改任何代码、未生成迁移、未启动测试。** 实施 M1~M3 请显式授权后按 §6.5 起手。

---

_文档结束 · v2.0 · 方案交付版_
