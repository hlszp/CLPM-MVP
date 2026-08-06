# CLPM 智能预警规则引擎需求与技术方案

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 编制日期 | 2026-08-06 |
| 文档状态 | 待评审（作为 PRD v6.2 与实现契约 v2.6 的输入文档） |
| 适用范围 | CLPM v6.1 新增"智能预警规则引擎"能力规划 |
| 关联基线 | PRD v6.1、实现契约 v2.5、UX/IA 审计报告 2026-08-05、KPI 计算审查报告 2026-08-05、诊断整改方案 2026-07-19、ops-runbook |
| 上游约束 | 数据架构"导入走远端、计算全本地"；不直写 DCS；安全边界与审计留痕；模块内聚自包含 |
| 落地分支建议 | `feat/alert-rule-engine`（>500 行 + DB schema 变更，按 AGENTS.md 走分支） |

---

## 1. 背景与目标

### 1.1 现状盘点

CLPM v6.1 已具备完整的"事后体检"诊断能力：

- **诊断中心双轨调度**（实现契约 v2.5 §6，ops-runbook §诊断调度细节）：
  - 事件轨 `diagnosis-engine-hourly`（crontab minute=10，整点后 10 分对 score<60 或 score NULL 即 INCONCLUSIVE 回路触发深诊）
  - 体检轨 `diagnosis-engine-checkup-8h`（crontab minute=20, hour="*/8"，0/8/16 点 20 分对全部 READY 回路 1h 窗口体检）
- **诊断引擎**：8 类诊断标签（OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW）+ 10+ 算法矩阵 + Dempster-Shafer 融合 + 专家规则（`DiagnosisRule` 表，simpleeval 沙箱）。
- **Action Tracker 子模块**：状态机 `PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED`（已扩展至 `VERIFYING/CLOSED/REOPENED`），D1 自动建单（诊断命中即建单），D3 MOC 强制关联，D4 T+7d A/B 自动验证。
- **告警出口**：`app/services/alerting.py` 提供 `send_alert(title, message, severity)`，仅 webhook 一通道，info/warning/critical 三级，webhook URL 为空时仅记日志（**未与业务规则关联**）。
- **配置审批流**：`DiagnosisConfigChange` 双人确认（PENDING → APPROVED/REJECTED），C4 阈值版本回滚已具备。
- **实时数据流**：`app/services/data_source/realtime_subscriber.py` 订阅 AAS SignalR Hub（189 个 tag），数据落 Redis 滚动缓存（`realtime:history:*`，75 分钟 ×1Hz=4500 点）+ 本地 TDengine 宽表，已实现断点续传。
- **可信度评估**：`ConfidenceEvaluator` A/B/C/D/E 五级（valid_rate 阈值 95/80/60/20%）。

### 1.2 痛点分析

| # | 痛点 | 表现 | 业务影响 |
|---|---|---|---|
| P1 | **诊断是"事后体检"** | 体检轨 8h 间隔，事件轨依赖 KPI 整点快照（小时级），无法感知秒级异常征兆 | 振荡/饱和/质量异常等已在 1 小时内造成工艺波动才被发现，丧失"预警"窗口 |
| P2 | **无独立预警规则配置** | 现有 `alerting.send_alert` 是裸函数，调用点散落在 `data_integrity_check.py` / `data_link_monitor.py` / `realtime_subscriber.py` 三处硬编码，无规则表、无审计、无订阅、无可配置阈值 | 阈值/动作/接收人都靠改代码，违背"产品化、配置驱动"原则 |
| P3 | **无可配置规则引擎** | 诊断 `DiagnosisRule` 仅作用于"诊断标签后处理"，不对实时数据流求值；阈值规则/统计漂移/组合条件/时效窗口/可信度联动均缺失 | 工厂常见需求（如"PV 超量程持续 5 分钟且 OP 饱和 10 分钟即告警"）无法自助配置 |
| P4 | **预警与处置脱节** | `alerting` 仅发 webhook，不落库、不生成 Action Tracker 工单、不可追溯处置闭环 | 告警无留痕、无闭环，事后审计无证据 |
| P5 | **误报与噪声不可控** | 无冷却期、无去抖、无可信度联动，数据质量差（D/E 级）时段会狂发告警 | 工程师告警疲劳，最终关闭告警通道 |
| P6 | **UX/IA 审计断点** | 异常跟踪表格列不足、跨模块一键跳转缺失（UX/IA 报告 P0-1/P0-2） | 预警事件无法在工作台与诊断详情间联动 |

### 1.3 目标

建立**独立于诊断模块**的智能预警规则引擎，与诊断形成"事前预警 → 事后体检"双闭环：

| 编号 | 目标 | 验证指标 |
|---|---|---|
| O1 | 实时预警 | SignalR 实时数据流触发规则求值，规则命中到事件落库 P95 < 2s |
| O2 | 规则可配置 | 5 类规则（阈值/统计漂移/组合/可信度联动/时效窗口）DSL 化，UI 可视化编辑，0 代码新增 |
| O3 | 可解释 | 每条预警事件携带触发条件快照、数据窗口、规则版本、可信度等级 |
| O4 | 可审计 | 规则 CRUD 全留痕（`alert_rule_audit_log`），事件全生命周期可追溯 |
| O5 | 可处置 | 预警事件可一键转 Action Tracker 工单（复用现有状态机），闭环率纳入统计 |
| O6 | 可抑制 | 冷却期 + 去抖 + 可信度联动 + 同源去重，误报率 < 5%（目标值，需上线后实测校准） |
| O7 | 与诊断解耦 | 预警规则引擎独立模块，不修改诊断引擎代码；预警可"升级"为诊断任务，但二者数据/调度/状态机独立 |

### 1.4 边界与非目标

- **不是** APC/SIS 替代品，**不直写 DCS**（对齐 PRD §2.3 安全边界）。
- **不是** 通用 EAM 工单系统，仅生成轻量 Action Tracker 记录（对齐 PRD §2.3）。
- **不引入外部 IM**（飞书/钉钉/邮件）作为 Phase 1-3 通道；Phase 4 再扩展（避免复杂度与外部依赖）。
- **不替代诊断**：诊断是"评分驱动 + 算法矩阵 + 8 标签"的体检；预警是"规则驱动 + 实时流 + 自定义条件"的告警。二者互补不互替。
- **不重构 KPI/可信度**：复用 `ConfidenceEvaluator` 与 `kpi_snapshot_hourly`，不改其算法。

---

## 2. 需求清单（按用户角色）

### 2.1 管理员（ADMIN）

| 编号 | 需求 | 优先级 |
|---|---|---|
| AR-A1 | 规则模板管理：内置 5 类规则模板（阈值/漂移/组合/可信度/时效），可基于模板创建规则 | P0 |
| AR-A2 | 规则启用/停用：批量启停、按模板分类筛选 | P0 |
| AR-A3 | 审计日志查询：按操作人/时间/规则 ID/操作类型筛选，支持 before/after JSON 对比 | P0 |
| AR-A4 | 关键规则变更双人审批：复用 `DiagnosisConfigChange` 模式，危化企业关键阈值变更需第二人确认 | P1 |
| AR-A5 | 规则优先级配置：数值越小越先求值，冲突时按优先级取最高严重度 | P1 |
| AR-A6 | 全局预警开关：`sys_config` 键 `alert.engine.enabled`（默认 true），关闭后所有规则停止求值 | P0 |

### 2.2 仪控工程师（IC_ENGINEER）

| 编号 | 需求 | 优先级 |
|---|---|---|
| AR-E1 | 规则配置：可视化条件构建器 + DSL 预览（YAML/JSON 切换），支持 dry-run 回放验证 | P0 |
| AR-E2 | 订阅回路：按回路/装置/控制类型订阅规则，支持"全部回路"快捷订阅 | P0 |
| AR-E3 | 预警事件处置：列表筛选（时间/回路/严重度/规则）、详情查看、一键转 Action Tracker 工单 | P0 |
| AR-E4 | 预警抑制：对指定回路 × 规则在指定时段内手动抑制（注明原因 + 到期时间），抑制期内不告警 | P1 |
| AR-E5 | 误报标记：在事件详情标记"误报"，反馈给规则作者用于阈值调优 | P1 |
| AR-E6 | 工作台徽章：未读预警事件计数徽章，点击跳转事件列表 | P0 |

### 2.3 工艺/设备工程师（PE_ENGINEER）

| 编号 | 需求 | 优先级 |
|---|---|---|
| AR-P1 | 预警事件查看：只读权限，按装置/单元筛选 | P0 |
| AR-P2 | 转工单参与：可参与 Action Tracker 处置（与诊断 tracker 同口径） | P1 |

### 2.4 外部专家（EXPERT）

| 编号 | 需求 | 优先级 |
|---|---|---|
| AR-X1 | 预警事件只读 + 转工单参与（与诊断 tracker 同口径） | P1 |

### 2.5 系统自动

| 编号 | 需求 | 优先级 |
|---|---|---|
| AR-S1 | 实时规则求值：SignalR Hub 数据更新触发规则匹配 → 抑制/去抖 → 严重度判定 → 动作分发 | P0 |
| AR-S2 | 周期巡检：Celery Beat 周期任务（默认 1 分钟）对统计漂移/组合条件规则求值（这类规则需窗口数据，不适合逐点求值） | P0 |
| AR-S3 | 自动建单：规则配置 `action.create_tracker=true` 时，预警事件自动生成 Action Tracker 工单（`trigger_type='auto'`, `triggered_by='alert-engine'`） | P0 |
| AR-S4 | 通知分发：站内信（Redis pub/sub 推 WebSocket）+ 工作台徽章 | P0 |
| AR-S5 | 事件归档：已处置（IMPLEMENTED/CLOSED）或超过保留期（默认 90 天）的事件自动归档 | P1 |
| AR-S6 | 严重度自动升级：同一回路同一规则在冷却期内重复触发 N 次自动升级严重度（INFO → WARN → ERROR → CRITICAL） | P2 |

---

## 3. 规则类型与表达式 DSL

### 3.1 规则类型总览

| 类型 | code | 求值时机 | 数据源 | 典型场景 |
|---|---|---|---|---|
| 阈值规则 | `THRESHOLD` | 实时（SignalR 逐点） + 周期 | 实时缓存 / TDengine | PV 超量程、OP 饱和、MODE 切手动 |
| 统计漂移规则 | `DRIFT` | 周期（1-5 分钟） | TDengine 滑动窗口 | 均值/方差/分位数偏离基线 |
| 组合条件规则 | `COMPOSITE` | 周期（1-5 分钟） | TDengine 窗口 + 实时缓存 | "PV 超限持续 N 分钟 且 OP 饱和" |
| 可信度联动规则 | `CONFIDENCE` | 事件触发（可信度变更） | `ConfidenceEvaluator` 输出 | 数据 D/E 级时降级或抑制预警 |
| 时效窗口规则 | `TIME_WINDOW` | 规则求值前置过滤 | `sys_config` 时段表 | 仅班次/开停车工况下生效 |

### 3.2 DSL 通用结构

所有规则统一为如下 JSON 结构（存储于 `alert_rule.dsl` JSONB 字段）：

```json
{
  "ruleType": "THRESHOLD",
  "scope": {
    "loopSelector": { "type": "ALL | LOOP | PLANT | CONTROL_TYPE", "value": "<id 或枚举>" }
  },
  "condition": { "...见各类型..." },
  "durationSeconds": 300,
  "cooldownSeconds": 1800,
  "severity": "WARN",
  "confidencePolicy": { "minLevel": "C", "action": "SUPPRESS | DOWNGRADE" },
  "timeWindow": { "enabled": false, "cron": "0 8-20 * * *", "tz": "Asia/Shanghai" },
  "actions": [
    { "type": "CREATE_EVENT" },
    { "type": "CREATE_TRACKER", "params": { "label": "OUTPUT_SATURATION" } },
    { "type": "NOTIFY", "params": { "channels": ["in_app"], "roles": ["IC_ENGINEER"] } }
  ],
  "priority": 100,
  "dedupKey": "loop_id + rule_id"
}
```

字段说明：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `ruleType` | enum | 是 | THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE/TIME_WINDOW |
| `scope.loopSelector` | object | 是 | 订阅范围；ALL 全回路，LOOP 单回路，PLANT 装置级，CONTROL_TYPE 控制类型 |
| `condition` | object | 是 | 类型特定条件（见 §3.3-3.7） |
| `durationSeconds` | int | 否 | 持续时长，0 表示瞬时触发；>0 时需持续满足才告警（去抖） |
| `cooldownSeconds` | int | 否 | 冷却期，默认 1800s；同一 `dedupKey` 在冷却期内重复触发不告警（计数累加用于升级） |
| `severity` | enum | 是 | INFO/WARN/ERROR/CRITICAL（对齐 PRD §5.6.1） |
| `confidencePolicy` | object | 否 | 可信度联动策略；`minLevel` 为 A/B/C/D/E，低于此级别时 `action=SUPPRESS`（抑制）或 `DOWNGRADE`（降一级严重度） |
| `timeWindow` | object | 否 | 时效窗口；`cron` 定义生效时段（如 `0 8-20 * * *` 仅白天 8-20 点生效），`enabled=false` 全天生效 |
| `actions` | array | 是 | 动作列表，按顺序执行；CREATE_EVENT 默认必填 |
| `priority` | int | 否 | 规则优先级，数值越小越先求值；同回路多规则命中时取最高优先级规则的严重度 |
| `dedupKey` | string | 否 | 去重键模板，支持变量 `${loop_id}` `${rule_id}` `${tag_code}`；默认 `loop_id + rule_id` |

### 3.3 阈值规则（THRESHOLD）

**场景**：PV 超量程、OP 饱和、MODE 切手动、SP 阶跃。

```json
{
  "ruleType": "THRESHOLD",
  "scope": { "loopSelector": { "type": "CONTROL_TYPE", "value": "TEMPERATURE" } },
  "condition": {
    "metric": "PV",
    "operator": ">",
    "value": 380,
    "orCondition": { "metric": "PV", "operator": "<", "value": -10 }
  },
  "durationSeconds": 60,
  "cooldownSeconds": 600,
  "severity": "ERROR",
  "actions": [
    { "type": "CREATE_EVENT" },
    { "type": "CREATE_TRACKER", "params": { "label": "QUALITY_ABNORMAL" } }
  ]
}
```

**支持的 metric**：`PV` / `SP` / `OP` / `MODE` / `PID_P` / `PID_I` / `PID_D`（对齐 PRD §1.3 7 tag）。
**支持的 operator**：`>` / `>=` / `<` / `<=` / `==` / `!=` / `IN` / `NOT_IN`（IN/NOT_IN 用于 MODE 枚举）。
**变化率规则**：`operator=RATE_OF_CHANGE`，`value` 为每秒变化率阈值（如 `0.5` 表示 0.5%/s）。

### 3.4 统计漂移规则（DRIFT）

**场景**：均值漂移、方差增大、分位数偏离基线。

```json
{
  "ruleType": "DRIFT",
  "scope": { "loopSelector": { "type": "LOOP", "value": "<loop_id>" } },
  "condition": {
    "metric": "PV",
    "statistic": "MEAN",
    "windowSeconds": 1800,
    "baseline": { "type": "STATIC", "value": 120.5 },
    "deviationThreshold": 5.0,
    "deviationType": "ABSOLUTE"
  },
  "cooldownSeconds": 3600,
  "severity": "WARN",
  "actions": [{ "type": "CREATE_EVENT" }]
}
```

**字段说明**：

| 字段 | 取值 | 含义 |
|---|---|---|
| `statistic` | `MEAN` / `STDDEV` / `P95` / `P99` / `MIN` / `MAX` | 滑动窗口统计量 |
| `windowSeconds` | 300-86400 | 滑动窗口长度，默认 1800（30min） |
| `baseline.type` | `STATIC` / `HISTORICAL` / `RULE_BASED` | 静态值 / 历史窗口均值 / 规则计算 |
| `baseline.historyWindow` | string | 当 `type=HISTORICAL` 时，如 `P7D`（过去 7 天同时段均值） |
| `deviationThreshold` | float | 偏离阈值 |
| `deviationType` | `ABSOLUTE` / `RELATIVE` / `SIGMA` | 绝对值 / 相对百分比 / 标准差倍数（如 3σ） |

### 3.5 组合条件规则（COMPOSITE）

**场景**：多条件 AND/OR + 时序先后。

```json
{
  "ruleType": "COMPOSITE",
  "scope": { "loopSelector": { "type": "ALL" } },
  "condition": {
    "logic": "AND",
    "operands": [
      { "type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 380, "durationSeconds": 300 },
      { "type": "THRESHOLD", "metric": "OP", "operator": ">=", "value": 95, "durationSeconds": 600 },
      {
        "type": "SEQUENCE",
        "first": { "type": "THRESHOLD", "metric": "SP", "operator": "RATE_OF_CHANGE", "value": 0.1 },
        "then": { "type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 380 },
        "withinSeconds": 600
      }
    ]
  },
  "cooldownSeconds": 1800,
  "severity": "CRITICAL",
  "actions": [
    { "type": "CREATE_EVENT" },
    { "type": "CREATE_TRACKER", "params": { "label": "OUTPUT_SATURATION" } },
    { "type": "NOTIFY", "params": { "channels": ["in_app"], "roles": ["IC_ENGINEER", "ADMIN"] } }
  ]
}
```

**`logic` 支持**：`AND` / `OR` / `NOT` / `SEQUENCE`（时序）。
**`operands[].type` 支持**：`THRESHOLD` / `DRIFT` / `CONFIDENCE` / 嵌套 `COMPOSITE`（最多 3 层嵌套，防爆炸）。
**`SEQUENCE` 语义**：`first` 触发后 `withinSeconds` 秒内 `then` 触发才告警。

### 3.6 可信度联动规则（CONFIDENCE）

**场景**：数据 D/E 级时降级或抑制其他规则。

```json
{
  "ruleType": "CONFIDENCE",
  "scope": { "loopSelector": { "type": "ALL" } },
  "condition": {
    "maxConfidenceLevel": "D",
    "effect": "SUPPRESS"
  },
  "durationSeconds": 300,
  "severity": "INFO",
  "actions": [
    { "type": "CREATE_EVENT" },
    { "type": "NOTIFY", "params": { "channels": ["in_app"], "roles": ["IC_ENGINEER"] } }
  ]
}
```

**语义**：当回路可信度 ≤ D（即 D 或 E）持续 `durationSeconds` 秒时，生成"数据质量低"预警事件，并在该回路所有其他规则上应用 `effect=SUPPRESS`（抑制）或 `DOWNGRADE`（降级）。

**与其他规则的关系**：本规则不替代每条规则的 `confidencePolicy`（§3.2 通用字段），而是**全局抑制器**——任何规则求值前先检查回路当前可信度，若 ≤ `confidencePolicy.minLevel` 则按 `action` 处理。`CONFIDENCE` 规则本身负责把"数据质量低"事件显式告知用户。

### 3.7 时效窗口规则（TIME_WINDOW）

**`timeWindow` 通用字段**作用于所有规则类型（非独立规则类型），示例：

```json
{
  "ruleType": "THRESHOLD",
  "timeWindow": {
    "enabled": true,
    "cron": "0 8-20 * * 1-5",
    "tz": "Asia/Shanghai",
    "orCondition": { "plantState": "STARTUP | SHUTDOWN" }
  },
  "..."
}
```

**`cron` 语义**：参考 cron 表达式，`0 8-20 * * 1-5` 表示工作日 8-20 点生效。
**`plantState`**（Phase 2）：工厂工况标签，对接未来 MES/DCS 工况信号；Phase 1 不实现，仅留 DSL 字段。

### 3.8 DSL 校验规则

规则保存前服务端必须校验：

| 校验项 | 规则 |
|---|---|
| 必填字段 | `ruleType` / `scope` / `condition` / `severity` / `actions` |
| `metric` 枚举 | 必须为 PV/SP/OP/MODE/PID_P/PID_I/PID_D |
| `operator` 枚举 | 必须为 §3.3 支持的运算符 |
| `durationSeconds` | 0-86400（≤1 天） |
| `cooldownSeconds` | 0-86400 |
| `windowSeconds`（DRIFT） | 300-86400 |
| 嵌套深度（COMPOSITE） | ≤ 3 层 |
| 表达式长度 | DSL JSON 序列化后 ≤ 4000 字符 |
| `actions` 至少 1 个 | 必须含 `CREATE_EVENT` |
| `dedupKey` 变量 | 必须为白名单变量（`loop_id` / `rule_id` / `tag_code` / `severity`） |

校验失败返回 HTTP 422 + 字段级错误消息（对齐现有 `BizError` 模式）。

---

## 4. 系统架构

### 4.1 组件位置

```
backend/app/
├── services/alert_rule_engine/           # 规则引擎核心（新增）
│   ├── __init__.py
│   ├── dsl.py                             # DSL 解析与校验
│   ├── evaluator.py                       # 规则求值（阈值/漂移/组合/可信度/时效）
│   ├── suppressor.py                      # 抑制/去抖/冷却/去重
│   ├── dispatcher.py                      # 动作分发（CREATE_EVENT/CREATE_TRACKER/NOTIFY）
│   ├── cache.py                           # 规则缓存（内存 + Redis 双层，对齐 diagnosis_rule.py 模式）
│   ├── audit.py                           # 规则变更审计
│   └── dry_run.py                         # 规则回放验证
├── models/alert.py                        # 4 张 ORM 表（新增）
├── api/v1/endpoints/alerts.py             # API 端点（新增）
├── tasks/alert_engine.py                  # Celery 周期巡检任务（新增）
└── services/data_source/realtime_subscriber.py  # 现有 SignalR 订阅器，新增 hook 调用 evaluator
```

### 4.2 触发源：双轨架构（对齐诊断双轨模式）

| 轨道 | 触发源 | 求值时机 | 适用规则类型 | 实现位置 |
|---|---|---|---|---|
| 实时轨 | SignalR Hub 数据流 | 逐点更新（节流 1s） | THRESHOLD | `realtime_subscriber.py` 新增 hook 调用 `evaluator.evaluate_realtime()` |
| 周期轨 | Celery Beat | crontab `*/1 * * * *`（默认 1 分钟，可配） | DRIFT / COMPOSITE / CONFIDENCE | `app/tasks/alert_engine.py` 注册 beat 条目 |

**实时轨节流**：SignalR 数据可能高频（189 tag × 1Hz），逐点求值性能风险大。设计为：
- 每回路维护 Redis 滑动窗口（`alert:window:<loop_id>`，ZSET，保留近 `windowSeconds` 数据点）。
- 实时轨仅更新窗口，不立即求值；按"事件触发 + 节流"求值（每回路每 5s 最多求值 1 次）。
- 周期轨每分钟批量求值所有订阅规则。

**双轨与诊断双轨对齐**：诊断双轨是 `diagnosis-engine-hourly`（小时级）+ `diagnosis-engine-checkup-8h`（8 小时级）；预警双轨是 `alert-engine-realtime`（秒级）+ `alert-engine-periodic`（分钟级）。两者独立调度，互不干扰。

### 4.3 评估执行流程

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. 触发源（SignalR 实时点 / Celery 周期巡检）                          │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. 规则匹配：按 loop_id 从规则缓存取出订阅该回路的启用规则（按 priority 排序）│
│    规则缓存：内存（进程级） → Redis（5min TTL） → DB                    │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. 时效窗口过滤：timeWindow.enabled=true 时按 cron 判断当前是否生效        │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. 可信度门禁：读取 LoopConfidenceLatest.confidence_level                │
│    若 ≤ confidencePolicy.minLevel → SUPPRESS 跳过 / DOWNGRADE 降一级    │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. 条件求值：THRESHOLD 简单比较 / DRIFT 窗口统计 / COMPOSITE 递归求值     │
│    simpleeval 沙箱（复用 diagnosis_rule.py 的 EvalWithCompoundTypes）    │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. 持续时长检查：durationSeconds>0 时检查 Redis 窗口内是否持续满足条件     │
│    不满足则重置持续计数；满足且未达阈值则等待；达到阈值则进入下一步        │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 7. 冷却期检查：dedupKey 在 alert:cooldown:<dedupKey> 有未过期记录？        │
│    有 → 计入重复触发次数（用于严重度升级），但不告警                       │
│    无 → 进入动作分发                                                     │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 8. 动作分发（dispatcher.py）：                                           │
│    a. CREATE_EVENT：写 alert_event 表（含触发条件快照 + 数据窗口 + 规则版本）│
│    b. CREATE_TRACKER：写 action_tracker 表（trigger_type='auto',           │
│       triggered_by='alert-engine', severity 从规则继承）                  │
│    c. NOTIFY：Redis pub/sub → WebSocket → 站内信 + 工作台徽章             │
│    d. （Phase 4）EXTERNAL_NOTIFY：飞书/邮件                               │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.4 数据存储

| 表 | 用途 | 行级别 |
|---|---|---|
| `alert_rule` | 规则定义 | 1 行/规则 |
| `alert_rule_subscription` | 回路-规则订阅关系 | N 行/规则（按订阅范围展开） |
| `alert_event` | 预警事件 | 1 行/次触发 |
| `alert_rule_audit_log` | 规则变更审计 | 1 行/次 CRUD |
| `alert_suppression` | 抑制记录 | 1 行/次手动抑制 |
| Redis `alert:window:<loop_id>` | 滑动窗口数据 | ZSET |
| Redis `alert:cooldown:<dedupKey>` | 冷却期标记 | STRING with TTL |
| Redis `alert:duration:<dedupKey>` | 持续时长计数 | HASH |
| Redis `alert:badge:<user_id>` | 未读事件计数 | STRING |

### 4.5 与诊断模块的关系

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│   预警规则引擎（新增）   │         │      诊断模块（现有）         │
│  ─────────────────────  │         │  ──────────────────────────  │
│  • 规则驱动              │         │  • 评分驱动                  │
│  • 实时流 + 周期巡检     │         │  • KPI 快照触发              │
│  • 自定义 DSL            │         │  • 8 标签 + 算法矩阵         │
│  • 独立表与状态机        │         │  • 独立表与状态机            │
│  • CREATE_TRACKER 动作   │ ──升级──→ │  • 自动建单（D1）           │
└─────────────────────────┘         │  • Action Tracker 共享表     │
         │                          └──────────────────────────────┘
         │ 一键转诊断任务
         ▼
    POST /diagnosis/trigger（loopIds=..., labels=...）
```

**解耦点**：

1. **数据独立**：`alert_event` 与 `diagnosis_result` / `diagnosis_tag` 物理隔离，不互查。
2. **调度独立**：预警 Beat `alert-engine-periodic` 与诊断 Beat `diagnosis-engine-hourly` / `checkup-8h` 各自注册，互不影响。
3. **状态机独立**：预警事件状态机（§5.3）与诊断状态机（Diagnosis Tag ACTIVE/RESOLVED/SUPPRESSED；DiagnosisTask PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）独立。
4. **共享点**：Action Tracker 表（`action_tracker`）共享，预警转工单与诊断自动建单写入同一表，由 `triggered_by` 区分（`alert-engine` vs `checkup-scheduler` vs `auto-diagnosis`）。
5. **升级路径**：预警事件详情页提供"转诊断任务"按钮，调用 `POST /api/v1/diagnosis/trigger`（现有接口），传入 loopIds 与相关 labels，触发诊断引擎深诊。

---

## 5. 数据模型设计

对齐项目现有 SQLAlchemy 2.0 + alembic 模式（参考 `app/models/tracker.py` / `app/models/diagnosis.py`）。新增 5 张表，登记入实现契约 v2.6 §10 ORM 清单（当前 v2.5 为 38 张，新增后 43 张）。

### 5.1 `alert_rule`（规则定义）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK, default uuid4 | 规则 ID |
| `rule_code` | String(50) | NOT NULL, UNIQUE | 规则代码（如 `R-THRESHOLD-PV-OVER-RANGE`） |
| `rule_name` | String(100) | NOT NULL | 规则名称（中文显示） |
| `rule_type` | String(20) | NOT NULL, CHECK IN (THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE/TIME_WINDOW) | 规则类型 |
| `template_id` | UUID | FK → alert_rule_template.id, NULL | 基于模板创建时引用 |
| `dsl` | JSONB | NOT NULL | DSL 定义（§3.2 结构） |
| `description` | Text | NULL | 规则说明 |
| `priority` | Integer | NOT NULL, default 100 | 优先级，数值越小越先求值 |
| `is_enabled` | Boolean | NOT NULL, default true | 是否启用 |
| `version` | Integer | NOT NULL, default 1 | 版本号（每次更新 +1，用于事件留痕） |
| `created_by` | String(50) | NOT NULL | 创建人 |
| `created_at` | DateTime | NOT NULL, server_default now() | 创建时间（UTC） |
| `updated_by` | String(50) | NULL | 最近修改人 |
| `updated_at` | DateTime | NULL | 最近修改时间（UTC） |

**索引**：
- `uk_alert_rule_code` UNIQUE on (`rule_code`)
- `idx_alert_rule_type` on (`rule_type`)
- `idx_alert_rule_enabled_priority` on (`is_enabled`, `priority`)

**CheckConstraint**：
```sql
rule_type IN ('THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE', 'TIME_WINDOW')
```

### 5.2 `alert_rule_subscription`（回路-规则订阅）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 订阅 ID |
| `rule_id` | UUID | FK → alert_rule.id ON DELETE CASCADE, NOT NULL | 规则 ID |
| `loop_id` | UUID | FK → loop_ledger.id ON DELETE CASCADE, NOT NULL | 回路 ID |
| `scope_type` | String(20) | NOT NULL, CHECK IN (ALL/LOOP/PLANT/CONTROL_TYPE) | 订阅范围类型 |
| `scope_value` | String(100) | NULL | 范围值（LOOP 时为 loop_id，PLANT 时为 plant_node_id，CONTROL_TYPE 时为 FLOW/TEMPERATURE/...，ALL 时 NULL） |
| `is_active` | Boolean | NOT NULL, default true | 订阅是否活跃（回路停用时自动置 false） |
| `created_by` | String(50) | NOT NULL | 订阅人 |
| `created_at` | DateTime | NOT NULL, server_default now() | 订阅时间 |

**索引**：
- `uk_alert_subscription_rule_loop` UNIQUE on (`rule_id`, `loop_id`) WHERE `is_active = true`
- `idx_alert_subscription_loop` on (`loop_id`)
- `idx_alert_subscription_scope` on (`scope_type`, `scope_value`)

**展开逻辑**：订阅 `scope_type=PLANT` 时，由后台任务展开为该装置下所有回路的多行订阅记录（物化展开，避免运行时递归查询）。

### 5.3 `alert_event`（预警事件）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 事件 ID |
| `rule_id` | UUID | FK → alert_rule.id ON DELETE SET NULL, NULL | 规则 ID（规则删除时保留事件） |
| `rule_code` | String(50) | NOT NULL | 规则代码快照（冗余，规则删除后可查） |
| `rule_version` | Integer | NOT NULL | 规则版本快照 |
| `loop_id` | UUID | FK → loop_ledger.id ON DELETE CASCADE, NOT NULL | 回路 ID |
| `severity` | String(20) | NOT NULL, CHECK IN (INFO/WARN/ERROR/CRITICAL) | 严重度 |
| `status` | String(20) | NOT NULL, default 'ACTIVE', CHECK IN (ACTIVE/ACKNOWLEDGED/RESOLVED/SUPPRESSED/ARCHIVED) | 事件状态 |
| `trigger_condition_snapshot` | JSONB | NOT NULL | 触发条件快照（DSL condition 部分 + 实际值） |
| `data_window` | JSONB | NULL | 数据窗口快照（window_start/window_end/采样点数/统计值） |
| `triggered_value` | Numeric(10,4) | NULL | 触发时的实际值 |
| `confidence_level` | String(1) | NULL, CHECK IN (A/B/C/D/E) | 触发时回路可信度等级 |
| `rule_dsl_snapshot` | JSONB | NOT NULL | 规则 DSL 完整快照（用于审计回放） |
| `tracker_id` | UUID | FK → action_tracker.id ON DELETE SET NULL, NULL | 关联 Action Tracker 工单（CREATE_TRACKER 动作生成） |
| `is_false_positive` | Boolean | NULL | 是否标记为误报（工程师反馈） |
| `trigger_count` | Integer | NOT NULL, default 1 | 冷却期内重复触发次数（用于严重度升级） |
| `triggered_at` | DateTime | NOT NULL, server_default now() | 触发时间（UTC） |
| `acknowledged_by` | String(50) | NULL | 确认人 |
| `acknowledged_at` | DateTime | NULL | 确认时间 |
| `resolved_by` | String(50) | NULL | 处置人 |
| `resolved_at` | DateTime | NULL | 处置时间 |
| `resolution_note` | Text | NULL | 处置备注 |

**索引**：
- `idx_alert_event_loop_time` on (`loop_id`, `triggered_at` DESC)
- `idx_alert_event_severity_status` on (`severity`, `status`)
- `idx_alert_event_rule` on (`rule_id`, `triggered_at` DESC)
- `idx_alert_event_status` on (`status`)
- `idx_alert_event_tracker` on (`tracker_id`)

**CheckConstraint**：
```sql
severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')
status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED', 'ARCHIVED')
confidence_level IN ('A', 'B', 'C', 'D', 'E')
```

**状态机**：`ACTIVE → ACKNOWLEDGED → RESOLVED → ARCHIVED`；分支 `SUPPRESSED`（手动抑制，到期自动回 ACTIVE）。

### 5.4 `alert_rule_audit_log`（规则变更审计）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 日志 ID |
| `rule_id` | UUID | FK → alert_rule.id ON DELETE SET NULL, NULL | 规则 ID |
| `rule_code` | String(50) | NOT NULL | 规则代码快照 |
| `operation_type` | String(20) | NOT NULL, CHECK IN (CREATE/UPDATE/ENABLE/DISABLE/DELETE) | 操作类型 |
| `before_value` | Text | NULL | 变更前 JSON |
| `after_value` | Text | NULL | 变更后 JSON |
| `operator` | String(50) | NOT NULL | 操作人 |
| `operated_at` | DateTime | NOT NULL, server_default now() | 操作时间（UTC） |

**索引**：
- `idx_alert_audit_rule` on (`rule_id`, `operated_at` DESC)
- `idx_alert_audit_operator` on (`operator`, `operated_at` DESC)
- `idx_alert_audit_type` on (`operation_type`, `operated_at` DESC)

> **审计落库模式对齐**：参考 `app/services/diagnosis_rule.py:update_rule` 已有的 `SysAuditLog` 模式，但预警规则变更不写入 `sys_audit_log`（避免污染系统级审计），而是写入专用 `alert_rule_audit_log` 表，便于按规则维度查询。

### 5.5 `alert_suppression`（手动抑制记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 抑制 ID |
| `rule_id` | UUID | FK → alert_rule.id ON DELETE CASCADE, NULL | 规则 ID（NULL 表示全规则抑制） |
| `loop_id` | UUID | FK → loop_ledger.id ON DELETE CASCADE, NULL | 回路 ID（NULL 表示全回路抑制） |
| `reason` | String(500) | NOT NULL | 抑制原因 |
| `suppressed_by` | String(50) | NOT NULL | 抑制人 |
| `start_at` | DateTime | NOT NULL | 抑制开始时间 |
| `end_at` | DateTime | NOT NULL | 抑制结束时间 |
| `is_active` | Boolean | NOT NULL, default true | 是否生效（到期自动置 false） |
| `created_at` | DateTime | NOT NULL, server_default now() | 创建时间 |

**索引**：
- `uk_alert_suppression_active` UNIQUE on (`rule_id`, `loop_id`) WHERE `is_active = true AND end_at > now()`
- `idx_alert_suppression_loop` on (`loop_id`)
- `idx_alert_suppression_expiry` on (`end_at`, `is_active`)

### 5.6 `alert_rule_template`（规则模板，可选）

> Phase 1 可不建表，模板硬编码在 `dsl.py` 常量；Phase 2 迁入数据库支持自定义模板。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 模板 ID |
| `template_code` | String(50) | NOT NULL, UNIQUE | 模板代码 |
| `template_name` | String(100) | NOT NULL | 模板名称 |
| `rule_type` | String(20) | NOT NULL | 规则类型 |
| `dsl_template` | JSONB | NOT NULL | DSL 模板（含占位符 `${metric}` `${value}`） |
| `description` | Text | NULL | 模板说明 |

### 5.7 ORM 清单更新（实现契约 v2.6 §10）

| # | 表名 | 模型 | 来源 |
|---|---|---|---|
| 39 | `alert_rule` | `AlertRule` | 本方案新增 |
| 40 | `alert_rule_subscription` | `AlertRuleSubscription` | 本方案新增 |
| 41 | `alert_event` | `AlertEvent` | 本方案新增 |
| 42 | `alert_rule_audit_log` | `AlertRuleAuditLog` | 本方案新增 |
| 43 | `alert_suppression` | `AlertSuppression` | 本方案新增 |
| 44 | `alert_rule_template` | `AlertRuleTemplate` | Phase 2 新增 |

> 当前 v2.5 ORM 表数为 38 张（实现契约 §10）。本方案新增 5 张（Phase 1）+ 1 张（Phase 2），总计 43-44 张。

### 5.8 Alembic 迁移纪律

- 迁移文件命名：`<revision>_<描述>.py`，对齐现有命名风格。
- 模型改动与迁移**同批提交**，且**先应用迁移再让代码进入运行环境**（AGENTS.md 关键注意事项，2026-07-21 教训）。
- 迁移必须包含 `downgrade()` 路径，支持回滚。
- 上线前 `uv run alembic check` 退出码必须为 0（AGENTS.md CI 门禁）。

---

## 6. API 设计

所有 API 默认前缀 `/api/v1`（实现契约 §4.4），新增领域登记入实现契约 v2.6 §4。

### 6.1 规则 CRUD（管理员）

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `GET` | `/alerts/rules` | 规则列表（分页 + 筛选） | ADMIN | `ruleType`, `isEnabled`, `page`, `pageSize` | `{items: [Rule], total, page, pageSize}` |
| `GET` | `/alerts/rules/{ruleId}` | 规则详情 | ADMIN, IC_ENGINEER(读) | path | `Rule` |
| `POST` | `/alerts/rules` | 创建规则 | ADMIN | `RuleCreatePayload` | `Rule`（201） |
| `PUT` | `/alerts/rules/{ruleId}` | 更新规则 | ADMIN | `RuleUpdatePayload` | `Rule` |
| `PATCH` | `/alerts/rules/{ruleId}/status` | 启用/停用 | ADMIN | `{isEnabled: bool}` | `Rule` |
| `DELETE` | `/alerts/rules/{ruleId}` | 删除规则（软删：is_enabled=false + 审计） | ADMIN | path | 204 |
| `POST` | `/alerts/rules/validate` | DSL 校验（不保存） | ADMIN, IC_ENGINEER | `{dsl: object}` | `{valid: bool, errors: [{field, message}]}` |

**RuleCreatePayload**：
```json
{
  "ruleCode": "R-THRESHOLD-PV-OVER-RANGE",
  "ruleName": "PV 超量程告警",
  "ruleType": "THRESHOLD",
  "dsl": { /* §3.2 结构 */ },
  "description": "温度回路 PV 超量程持续 60s 告警",
  "priority": 100
}
```

### 6.2 订阅管理（工程师）

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `GET` | `/alerts/subscriptions` | 订阅列表（按规则/回路筛选） | ADMIN, IC_ENGINEER | `ruleId`, `loopId`, `scopeType`, `page`, `pageSize` | `{items, total}` |
| `POST` | `/alerts/subscriptions` | 创建订阅 | ADMIN, IC_ENGINEER | `SubscriptionCreatePayload` | `Subscription`（201） |
| `POST` | `/alerts/subscriptions/batch` | 批量订阅（按装置/控制类型展开） | ADMIN, IC_ENGINEER | `{ruleId, scopeType, scopeValue}` | `{createdCount, items}` |
| `DELETE` | `/alerts/subscriptions/{subscriptionId}` | 取消订阅 | ADMIN, IC_ENGINEER | path | 204 |
| `GET` | `/alerts/rules/{ruleId}/subscribed-loops` | 查询规则订阅的回路列表 | ADMIN, IC_ENGINEER | path, `page`, `pageSize` | `{items, total}` |

**SubscriptionCreatePayload**：
```json
{
  "ruleId": "<uuid>",
  "scopeType": "LOOP",
  "scopeValue": "<loop_id>",
  "loopIds": ["<loop_id_1>", "<loop_id_2>"]
}
```

> `scopeType=ALL` 时 `loopIds` 可为空，系统在规则求值时按 `loop_ledger.is_active=true` 全量展开；`scopeType=PLANT/CONTROL_TYPE` 时后台批量展开为多行订阅。

### 6.3 预警事件查询

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `GET` | `/alerts/events` | 事件列表（分页 + 多维筛选） | ADMIN, IC_ENGINEER, PE_ENGINEER(只读), EXPERT(只读) | `loopId`, `ruleId`, `severity`, `status`, `startTime`, `endTime`, `plantNodeId`, `page`, `pageSize` | `{items, total}` |
| `GET` | `/alerts/events/{eventId}` | 事件详情（含触发条件快照 + 数据窗口 + 关联 tracker） | ADMIN, IC_ENGINEER, PE_ENGINEER(只读), EXPERT(只读) | path | `EventDetail` |
| `PATCH` | `/alerts/events/{eventId}/acknowledge` | 确认事件（ACTIVE → ACKNOWLEDGED） | ADMIN, IC_ENGINEER, PE_ENGINEER, EXPERT | path, `{note?}` | `Event` |
| `PATCH` | `/alerts/events/{eventId}/resolve` | 处置事件（ACKNOWLEDGED → RESOLVED） | ADMIN, IC_ENGINEER, PE_ENGINEER, EXPERT | path, `{resolutionNote}` | `Event` |
| `PATCH` | `/alerts/events/{eventId}/false-positive` | 标记误报 | ADMIN, IC_ENGINEER | path, `{reason}` | `Event` |
| `POST` | `/alerts/events/{eventId}/convert-tracker` | 转为 Action Tracker 工单 | ADMIN, IC_ENGINEER | path, `{label?, mocRef?, mocNotApplicable?, mocReason?}` | `{trackerId}` |
| `POST` | `/alerts/events/{eventId}/convert-diagnosis` | 升级为诊断任务（调用 `/diagnosis/trigger`） | ADMIN, IC_ENGINEER | path, `{labels?}` | `{taskId}` |
| `GET` | `/alerts/events/badge` | 未读事件计数（工作台徽章） | ADMIN, IC_ENGINEER, PE_ENGINEER, EXPERT | - | `{count}` |

**EventDetail 出参**：
```json
{
  "eventId": "<uuid>",
  "ruleId": "<uuid>",
  "ruleCode": "R-THRESHOLD-PV-OVER-RANGE",
  "ruleName": "PV 超量程告警",
  "ruleVersion": 3,
  "loopId": "<uuid>",
  "loopCode": "TIC-101",
  "severity": "ERROR",
  "status": "ACTIVE",
  "triggerConditionSnapshot": {
    "metric": "PV",
    "operator": ">",
    "value": 380,
    "actualValue": 385.2
  },
  "dataWindow": {
    "windowStart": "2026-08-06T10:00:00Z",
    "windowEnd": "2026-08-06T10:05:00Z",
    "sampleCount": 300,
    "statistics": {"mean": 382.1, "max": 385.2, "min": 379.8}
  },
  "confidenceLevel": "B",
  "triggeredAt": "2026-08-06T10:05:12Z",
  "trackerId": null,
  "isFalsePositive": false,
  "triggerCount": 1,
  "loopContext": {
    "plantNodeName": "裂解装置",
    "controlType": "TEMPERATURE",
    "currentKpi": {"compositeScore": 78.5, "grade": "GOOD"}
  }
}
```

### 6.4 审计日志查询

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `GET` | `/alerts/audit-logs` | 审计日志列表 | ADMIN | `ruleId`, `operator`, `operationType`, `startTime`, `endTime`, `page`, `pageSize` | `{items, total}` |
| `GET` | `/alerts/audit-logs/{logId}` | 审计日志详情（含 before/after JSON 对比） | ADMIN | path | `AuditLogDetail` |

### 6.5 抑制管理

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `POST` | `/alerts/suppressions` | 创建抑制 | ADMIN, IC_ENGINEER | `{ruleId?, loopId?, reason, endAt}` | `Suppression`（201） |
| `GET` | `/alerts/suppressions` | 抑制列表 | ADMIN, IC_ENGINEER | `ruleId`, `loopId`, `isActive`, `page`, `pageSize` | `{items, total}` |
| `DELETE` | `/alerts/suppressions/{suppressionId}` | 取消抑制 | ADMIN, IC_ENGINEER | path | 204 |

### 6.6 测试规则（dry-run 回放）

| 方法 | 路径 | 功能 | 权限 | 入参 | 出参 |
|---|---|---|---|---|---|
| `POST` | `/alerts/rules/dry-run` | 规则回放验证 | ADMIN, IC_ENGINEER | `DryRunPayload` | `DryRunResult` |

**DryRunPayload**：
```json
{
  "dsl": { /* 规则 DSL，不要求已保存 */ },
  "loopIds": ["<loop_id_1>", "<loop_id_2>"],
  "startTime": "2026-08-05T00:00:00Z",
  "endTime": "2026-08-06T00:00:00Z",
  "includeDataWindow": true
}
```

**DryRunResult**：
```json
{
  "totalTriggers": 3,
  "triggers": [
    {
      "loopId": "<uuid>",
      "loopCode": "TIC-101",
      "triggeredAt": "2026-08-05T14:23:11Z",
      "severity": "WARN",
      "triggerValue": 381.5,
      "dataWindow": { /* 数据快照 */ },
      "confidenceLevel": "B"
    }
  ],
  "evaluatedPoints": 86400,
  "evaluationDurationMs": 1234
}
```

**dry-run 实现**：复用 `evaluator.evaluate()` 但替换数据源为 TDengine 历史查询（本地 TDengine，对齐"计算全本地"原则），不写 `alert_event` 表，不触发动作分发。

### 6.7 API 契约规则（对齐实现契约 §4.4）

- 所有 API 默认前缀 `/api/v1`。
- 新增领域 `/api/v1/alerts/*` 必须先在契约 §4 登记路径与说明，再落地代码与测试。
- 权限码：`alert:manage`（ADMIN）/ `alert:view`（IC_ENGINEER/PE_ENGINEER/EXPERT）；纳入 `ROLE_PERMISSIONS` 矩阵与 `require_perms()` 装饰器。
- 错误响应统一格式（对齐 `BizError`）：`{"code": "ERR_XXX", "message": "...", "status_code": 4xx/5xx}`。

---

## 7. 前端 IA 与交互

### 7.1 入口位置权衡

| 方案 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| **A. 独立"预警中心"一级菜单** | 预警独立性强，未来可扩展（外部通知/报表）；与诊断"事后体检"形成对等的"事前预警" | 新增一级菜单，IA 重构 Phase A 已完成 7 菜单，再增一级破坏 IA 一致性；UX/IA 审计主张菜单收敛 | ❌ 不推荐 |
| **B. 诊断中心子菜单"预警规则" + "预警事件"** | 复用诊断中心 IA；预警与诊断天然关联（事前/事后双轨）；不新增一级菜单 | 诊断中心菜单项增多（当前 6 项 → 8 项） | ⚠️ 备选 |
| **C. "监控"模块下子菜单（IA 重构 Phase A 已规划"监控"一级菜单）** | 对齐 IA 重构方案"监控顶层"范式；预警属监控范畴；与回路监控、装置监控同模块内聚 | 监控模块尚未落地（IA 重构 Phase A 未含） | ⚠️ 备选 |

**推荐方案 B**（Phase 1 落地）+ 后续迁移至方案 C（Phase 2，随 IA 重构监控模块落地）：

- Phase 1：在"诊断中心"一级菜单下新增两个子菜单：
  - `/diagnosis/alert-rules`（预警规则）
  - `/diagnosis/alert-events`（预警事件）
- Phase 2（IA 重构监控模块落地后）：迁移至 `/monitor/alert-rules` 与 `/monitor/alert-events`，原路径保留 301 重定向。

**理由**：
1. 诊断与预警天然关联（事前/事后），用户认知一致。
2. 不破坏 IA 重构 Phase A 已完成的 7 菜单结构。
3. 预警事件转诊断任务（§4.5）路径短，同模块内跳转流畅。
4. 预留向"监控"模块迁移的路径，对齐 IA 重构长期方向。

### 7.2 规则配置页（`/diagnosis/alert-rules`）

**布局**：左侧规则列表（树形按规则类型分组） + 右侧规则编辑器。

**规则编辑器**：
- **可视化条件构建器**（参考 LowCode 规则编辑器）：
  - 条件块拖拽组合（THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE/TIME_WINDOW）
  - 每个条件块表单化输入（metric 下拉、operator 下拉、value 输入、duration 滑块）
  - AND/OR/SEQUENCE 逻辑连接符可视化（连线 + 颜色区分）
- **DSL 预览**（右侧抽屉，YAML/JSON 切换）：实时同步可视化构建器，支持双向编辑（高级用户直接改 DSL，校验通过后回填可视化构建器）
- **dry-run 面板**（底部）：选择回路 + 时间范围，点击"测试规则"展示触发次数与触发点列表
- **审计历史**（折叠面板）：展示该规则的变更历史（before/after diff）

**交互细节**：
- 规则保存前必须通过 `POST /alerts/rules/validate`，校验失败时字段级错误高亮。
- 关键阈值变更（severity=CRITICAL 或 priority<50）触发双人审批弹窗（复用 `DiagnosisConfigChange` 模式）。
- DSL 预览支持"复制 JSON"快捷键。

### 7.3 预警事件列表（`/diagnosis/alert-events`）

**布局**：顶部筛选栏 + 事件表格 + 右侧详情抽屉。

**筛选栏**：时间范围 / 装置 / 回路 / 规则 / 严重度 / 状态 / 可信度等级。

**事件表格列**（对齐 UX/IA 审计 P0-1 "异常跟踪表格扩列"）：

| 列 | 内容 | 备注 |
|---|---|---|
| 触发时间 | `triggered_at`（本地时区） | 默认倒序 |
| 回路 | `loopCode` + `plantNodeName` | 点击跳转回路详情 |
| 规则 | `ruleName`（hover 显示 ruleCode） | 点击跳转规则编辑 |
| 严重度 | 色标徽章（INFO 灰/WARN 黄/ERROR 橙/CRITICAL 红） | 对齐 PRD §5.6.1 |
| 触发值 | `triggeredValue` + 单位 | |
| 可信度 | A-E 色标（对齐 ConfidenceEvaluator） | |
| 状态 | ACTIVE/ACKNOWLEDGED/RESOLVED/SUPPRESSED/ARCHIVED | |
| 重复次数 | `triggerCount`（hover 显示冷却期内触发历史） | >1 时高亮（严重度升级提示） |
| 关联工单 | `trackerId` 链接 | 点击跳转 Action Tracker 详情 |
| 操作 | 下拉：确认/处置/转工单/转诊断/标记误报/抑制 | |

**右侧详情抽屉**：
- 触发条件快照（DSL condition + 实际值对比）
- 数据窗口折线图（PV/SP/OP 时序，高亮触发点）
- 可信度等级与 valid_rate
- 关联 Action Tracker 工单（如有）
- 操作按钮组：确认 / 处置 / 转工单 / 转诊断任务 / 标记误报 / 创建抑制

**色标规范**（对齐 UI/UX v6.1 设计 Tokens）：

| 严重度 | 背景色 | 文字色 | 用途 |
|---|---|---|---|
| INFO | `#E6F4FF` | `#1677FF` | 信息提示 |
| WARN | `#FFFBE6` | `#FAAD14` | 需关注 |
| ERROR | `#FFF2E8` | `#FA541C` | 需处置 |
| CRITICAL | `#FFF1F0` | `#F5222D` | 立即处置 |

### 7.4 通知渠道

| 渠道 | 实现方式 | Phase |
|---|---|---|
| 站内信 | Redis pub/sub → WebSocket `/api/v1/ws/alerts` 推送，前端消息中心 | P0 |
| 工作台徽章 | Redis 计数器 `alert:badge:<user_id>`，WebSocket 推送增量 | P0 |
| 事件列表 | 前端轮询 `/alerts/events?status=ACTIVE`（30s） + WebSocket 实时推送 | P0 |
| 飞书/钉钉 | 复用 `alerting.send_alert` webhook（已存在），扩展为多 webhook 配置 | P4 |
| 邮件 | SMTP 配置 + 异步任务发送 | P4 |

**不引入外部 IM 的理由**（Phase 1-3）：
1. 危化企业内网部署，外部 IM 通道不稳定。
2. 飞书/钉钉需额外配置与企业账号，增加交付复杂度。
3. 站内信 + 工作台徽章已满足日常作业需求。
4. Phase 4 作为可选项，按客户需求开通。

---

## 8. 与现有系统的集成点

### 8.1 实时数据：SignalR Hub（已存在）

**集成点**：`app/services/data_source/realtime_subscriber.py`

**改动方式**：在 `_handle_signalr_message()`（处理 SignalR 消息）末尾新增 hook：

```python
# 伪代码，不实际写入
from app.services.alert_rule_engine.evaluator import evaluate_realtime

async def _handle_signalr_message(self, msg):
    # ... 现有逻辑（更新 Redis 缓存、写 TDengine）...
    
    # 新增：触发预警规则实时轨求值（节流 5s/回路）
    if self._should_evaluate_alerts(loop_id):
        await evaluate_realtime(loop_id, tag_code, value, timestamp)
```

**节流策略**：每回路维护 Redis 键 `alert:throttle:<loop_id>`（TTL 5s），存在则跳过本次求值。

**性能保护**：hook 异常不影响 SignalR 主链路（try/except + 日志告警），对齐 `alerting.send_alert` 现有"发送失败不影响主流程"模式。

### 8.2 历史数据：本地 TDengine（计算全本地原则）

**集成点**：`app/services/data_source/` 下的 TDengineProvider

**改动方式**：`alert_rule_engine/evaluator.py` 通过 `get_provider()`（恒返回 TDengineProvider）查询滑动窗口数据，**禁止降级到远端 API**（对齐 ops-runbook §数据链路）。

**查询优化**：
- 滑动窗口查询缓存到 Redis `alert:window:<loop_id>` ZSET（TTL = windowSeconds + 60s），避免重复查询。
- dry-run 历史回放复用 DataPlanner 的 MetricDataBundle 缓存（L1 DataBlock Cache），对齐实现契约 §7.6 缓存接入口径。

### 8.3 Action Tracker：复用诊断中心子模块

**集成点**：`app/models/tracker.py` 的 `ActionTracker` 表

**改动方式**：
- 预警规则 `CREATE_TRACKER` 动作直接写入 `action_tracker` 表，字段填充：
  - `trigger_type = 'auto'`
  - `triggered_by = 'alert-engine'`
  - `severity` 从规则 `severity` 继承
  - `diagnosis_label` 从规则 `actions[].params.label` 取（如 `OUTPUT_SATURATION`）
  - `loop_id` 从事件 `loop_id` 取
- **唯一约束兼容**：`uk_action_tracker_open` 要求 (loop_id, diagnosis_label) 在 PENDING/IN_PROGRESS/VERIFYING 状态下唯一。预警转工单前需检查同回路同标签是否已有开放态工单，有则更新 severity 与 trigger_count 而非新建。

**状态机共享**：预警事件转工单后，工单走现有 Action Tracker 状态机（PENDING → IN_PROGRESS → IMPLEMENTED → VERIFYING → CLOSED / REOPENED），预警事件状态机独立流转（ACTIVE → ACKNOWLEDGED → RESOLVED）。

### 8.4 可信度：复用 ConfidenceEvaluator

**集成点**：`app/services/confidence_evaluator.py` + `app/models/metric.py:LoopConfidenceLatest`

**改动方式**：`evaluator.py` 在条件求值前调用 `LoopConfidenceLatest` 读取回路当前可信度等级（A/B/C/D/E），按规则 `confidencePolicy` 处理：
- `SUPPRESS`：直接跳过该规则求值，记审计日志。
- `DOWNGRADE`：将规则 `severity` 降一级（CRITICAL → ERROR → WARN → INFO；INFO 降级为跳过）。

### 8.5 权限：复用现有 RBAC

**集成点**：`app/core/auth.py` 的 `require_perms()` 装饰器

**权限码新增**（登记入实现契约 v2.6 §5 `ROLE_PERMISSIONS`）：

| 权限码 | ADMIN | IC_ENGINEER | PE_ENGINEER | EXPERT | SPONSOR |
|---|---|---|---|---|---|
| `alert:manage` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `alert:view` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `alert:event:handle` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `alert:event:false-positive` | ✅ | ✅ | ❌ | ❌ | ❌ |

> SPONSOR 不可查看预警事件（对齐 PRD §3 SPONSOR "只看汇总视图"边界）。

### 8.6 站内信与 WebSocket

**集成点**：现有 `/api/v1/ws/*` WebSocket 端点

**改动方式**：新增 WebSocket 命名空间 `/api/v1/ws/alerts`，订阅模式：
- 客户端连接时按用户 ID 订阅 `alert:user:<user_id>` Redis 频道
- 服务端 `dispatcher.NOTIFY` 动作通过 Redis pub/sub 发布消息
- 客户端收到消息后更新消息中心 + 工作台徽章

### 8.7 Celery Beat 调度

**集成点**：`app/tasks/celery_app.py` 的 `beat_schedule`

**改动方式**（对齐 `diagnosis_engine.py` 注册模式）：

```python
# 伪代码，不实际写入
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["alert-engine-periodic"] = {
    "task": "app.tasks.alert_engine.run_periodic_evaluation",
    "schedule": crontab(minute="*/1"),  # 每分钟
}
celery_app.conf.beat_schedule = _existing_beat
```

**调度时间错开**：与诊断双轨错开（诊断整点 10 分 / 8 小时 20 分），预警每分钟执行，整点 10 分时预警与诊断可能并行但不冲突（数据源不同）。

### 8.8 sys_config 全局开关

**新增 sys_config 键**（对齐断点续传 `gapBackfillEnabled` 模式）：

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `alert.engine.enabled` | bool | true | 预警引擎总开关 |
| `alert.engine.periodic_interval_seconds` | int | 60 | 周期巡检间隔（秒） |
| `alert.engine.realtime_enabled` | bool | true | 实时轨开关（关闭后仅周期轨求值） |
| `alert.engine.max_rules_per_loop` | int | 50 | 单回路最大订阅规则数（防爆炸） |
| `alert.engine.event_retention_days` | int | 90 | 事件保留天数（超过自动归档） |
| `alert.engine.cooldown_default_seconds` | int | 1800 | 默认冷却期（秒） |
| `alert.engine.severity_escalation_threshold` | int | 3 | 重复触发 N 次后升级严重度 |

**运行时可调**：纳入 `sys_config` 表，UI 链路配置页修改即时生效（订阅器/周期任务每次触发读 settings，对齐 `gapBackfillEnabled` 模式），无需重启后端。

---

## 9. 分阶段实施路线图

### Phase 1：MVP（阈值规则 + 站内通知 + 事件列表）

**交付物**：
- 5 张 ORM 表 + alembic 迁移
- `alert_rule_engine/` 服务（dsl/evaluator/suppressor/dispatcher/cache/audit）
- THRESHOLD 规则类型完整实现
- API：规则 CRUD + 订阅 + 事件查询 + dry-run
- 前端：规则配置页（可视化 + DSL 预览）+ 事件列表 + 事件详情抽屉
- 站内信 + 工作台徽章（WebSocket）
- Celery Beat 周期巡检（仅 THRESHOLD 周期求值，实时轨 Phase 1 暂不接入 SignalR）
- 单元测试 ≥ 80 用例（DSL 校验 + evaluator + suppressor + dispatcher + API）

**依赖**：
- SignalR 订阅器 hook 改造（实时轨，可延后至 Phase 2）
- WebSocket `/api/v1/ws/alerts` 新增
- sys_config 全局开关

**验收标准**：
- 阈值规则可 UI 创建、保存、启用、dry-run、触发、查看事件
- 冷却期 + 持续时长 + 可信度联动生效
- 事件可确认、处置、转工单、标记误报
- 工作台徽章实时更新
- pytest 全绿，ruff/alembic check 退出码 0，check:type 通过

**预估工作量**：12-15 人日

### Phase 2：统计漂移 + 组合条件 + 实时轨

**交付物**：
- DRIFT 规则类型实现（滑动窗口统计）
- COMPOSITE 规则类型实现（AND/OR/SEQUENCE）
- SignalR 实时轨接入（`realtime_subscriber.py` hook）
- Redis 滑动窗口数据结构
- dry-run 历史回放增强（多回路批量）
- 规则模板表 `alert_rule_template` + 内置 5 类模板

**依赖**：
- Phase 1 全部交付物
- TDengine 滑动窗口查询性能验证（单回路 30 天 1Hz 数据查询 < 2s）

**验收标准**：
- DRIFT 规则可配置均值/方差/分位数漂移检测
- COMPOSITE 规则支持 3 层嵌套
- 实时轨从 SignalR 数据点到事件落库 P95 < 2s
- 误报率 < 10%（目标值，需实测校准）

**预估工作量**：10-12 人日

### Phase 3：可信度联动 + 审计 + 订阅管理

**交付物**：
- CONFIDENCE 规则类型实现
- 审计日志查询 UI（before/after diff 对比）
- 抑制管理 UI
- 订阅批量展开（PLANT/CONTROL_TYPE → 多回路）
- 严重度自动升级（重复触发 N 次升级）
- 事件归档任务（>90 天自动归档）
- 误报标记反馈机制（标记后规则作者收到通知）

**依赖**：
- Phase 1-2 全部交付物
- `LoopConfidenceLatest` 表（已存在）

**验收标准**：
- 可信度 D/E 级回路预警被正确抑制/降级
- 规则变更全留痕，审计可查询
- 批量订阅展开正确
- 严重度升级逻辑正确

**预估工作量**：8-10 人日

### Phase 4：外部通知 + 严重度自动升级增强

**交付物**：
- 飞书 webhook 通知（扩展 `alerting.py` 为多 webhook 配置）
- 邮件通知（SMTP + 异步任务）
- 通知模板管理（按严重度/规则类型配置模板）
- 通知频率控制（同用户每小时最多 N 条）
- 严重度自动升级增强（基于历史误报率自适应阈值）

**依赖**：
- Phase 1-3 全部交付物
- 客户提供飞书/钉钉 webhook URL 与 SMTP 配置

**验收标准**：
- 飞书/邮件通知按模板发送
- 频率控制生效
- 自适应阈值上线后误报率 < 5%

**预估工作量**：6-8 人日

**总预估**：36-45 人日

---

## 10. 风险与对策

### 10.1 误报率

**风险**：阈值设置不合理或数据噪声大，导致告警风暴，工程师告警疲劳。

**对策**：
- **持续时长去抖**：`durationSeconds>0` 时需持续满足才告警，过滤瞬时抖动。
- **冷却期**：`cooldownSeconds` 默认 1800s，同一 `dedupKey` 在冷却期内不重复告警。
- **可信度联动**：数据 D/E 级时自动抑制或降级（§3.6）。
- **手动抑制**：工程师可对指定回路 × 规则在指定时段抑制（§5.5）。
- **误报标记反馈**：工程师标记误报后，规则作者收到通知调优阈值。
- **dry-run 上线前验证**：规则上线前必须 dry-run 回放过去 7 天数据，确认触发次数合理（建议 < 10 次/天/回路）。
- **目标值**：Phase 1-2 误报率 < 10%，Phase 3-4 < 5%（上线后实测校准）。

### 10.2 性能

**风险**：规则数 × 回路数评估开销大，影响 SignalR 实时链路或 Celery 调度。

**对策**：
- **批量评估**：周期轨批量取所有订阅规则 × 所有回路，一次 TDengine 查询 + 内存求值，避免 N×M 次 DB 查询。
- **Redis 缓存窗口数据**：滑动窗口 ZSET 缓存，避免重复查询 TDengine。
- **实时轨节流**：每回路每 5s 最多求值 1 次（§8.1）。
- **规则缓存**：内存 + Redis 双层缓存（对齐 `diagnosis_rule.py` 模式），CRUD 后失效。
- **单回路最大规则数**：`alert.engine.max_rules_per_loop` 默认 50，超过拒绝订阅。
- **异步分发**：动作分发（CREATE_TRACKER / NOTIFY）走 Celery 异步任务，不阻塞求值主链路。
- **性能指标化**：单规则单回路求值延迟 < 50ms；100 回路 × 20 规则批量求值 < 5s（目标值）。

### 10.3 规则冲突

**风险**：多条规则对同一回路同一条件告警，产生重复事件。

**对策**：
- **规则优先级**：`priority` 字段数值越小越先求值，同回路多规则命中时取最高优先级规则的严重度。
- **同源去重**：`dedupKey` 默认 `loop_id + rule_id`，可配置为 `loop_id + tag_code` 实现同源去重（同回路同 tag 的多条规则只告警一次）。
- **冷却期共享**：相同 `dedupKey` 的规则共享冷却期。
- **冲突检测**：规则保存时检查是否与现有规则 `dedupKey` 冲突，提示用户。

### 10.4 审计完整性

**风险**：规则变更无留痕，事后无法追溯。

**对策**：
- **全量审计**：规则 CRUD（CREATE/UPDATE/ENABLE/DISABLE/DELETE）全部写入 `alert_rule_audit_log`，含 before/after JSON。
- **不可物理删除**：规则 DELETE 实际为软删（`is_enabled=false` + 审计），保留规则定义供历史事件查询。
- **事件规则快照**：`alert_event.rule_dsl_snapshot` 保存触发时规则 DSL 完整快照，即使规则后续变更或删除，历史事件仍可回溯触发条件。
- **双人审批**：关键规则变更（severity=CRITICAL 或 priority<50）走 `DiagnosisConfigChange` 双人确认模式（复用现有审批流）。

### 10.5 实时轨与 SignalR 主链路解耦

**风险**：预警规则求值异常影响 SignalR 数据接收与落库。

**对策**：
- **hook 异常隔离**：`evaluate_realtime()` 调用包裹 try/except，异常仅记日志 + 告警，不影响 SignalR 主链路（对齐 `alerting.send_alert` 现有模式）。
- **异步执行**：实时轨求值走 asyncio.create_task()，不阻塞 SignalR 消息处理。
- **熔断机制**：连续异常 > 10 次时自动熔断实时轨（仅周期轨求值），熔断状态写入 Redis，管理员可手动恢复。

### 10.6 数据架构边界

**风险**：预警规则求值误调用远端历史 API，违反"计算全本地"原则。

**对策**：
- **强制本地 TDengine**：`evaluator.py` 通过 `get_provider()` 获取 TDengineProvider，禁止 fallback 到远端（对齐 ops-runbook §数据链路）。
- **dry-run 限定本地**：dry-run 回放也走本地 TDengine，本地数据不完整时返回 `INCONCLUSIVE` 提示用户导入补齐。
- **代码审查门禁**：PR 审查时检查 `alert_rule_engine/` 下无 `remote_api` / `data_import` 调用。

---

## 11. 与 PRD/契约的差异说明

### 11.1 新增能力声明

本方案为 CLPM v6.1 之上的**新增能力**，不属于 PRD v6.1 或实现契约 v2.5 的现有范围。需在以下文档中补充章节：

| 文档 | 当前版本 | 目标版本 | 需补充章节 |
|---|---|---|---|
| PRD | v6.1 | v6.2 | §4.4 诊断中心下新增 §4.4.6 智能预警规则引擎子模块；§5.7 新增"预警规则类型与 DSL"小节；§7.1 算法性能需求新增预警求值延迟要求 |
| 实现契约 | v2.5 | v2.6 | §2 IA 新增 `/diagnosis/alert-rules` 与 `/diagnosis/alert-events` 路由；§4 API 新增 `/api/v1/alerts/*` 领域；§5 权限矩阵新增 `alert:manage`/`alert:view`/`alert:event:handle`/`alert:event:false-positive` 码；§6 状态机新增预警事件状态机；§10 ORM 清单新增 5-6 张表（总计 43-44 张） |
| FDS | v6.0 | v6.1 | 新增 §5.5 智能预警规则引擎功能设计 |
| DDS | v6.0 | v6.1 | 新增 §3.X alert_rule/alert_rule_subscription/alert_event/alert_rule_audit_log/alert_suppression 表设计 |
| IDS | v6.0 | v6.1 | 新增 §2.X 预警规则引擎 API 设计 |
| UI/UX | v6.1 | v6.2 | 新增预警规则配置页与事件列表页设计规范；色标规范对齐 §7.3 |

### 11.2 与现有模块的边界

| 模块 | 关系 | 边界 |
|---|---|---|
| 诊断中心 | 互补 | 预警"事前"+诊断"事后"；共享 Action Tracker 表；预警可升级为诊断任务；调度独立 |
| 性能评估 | 数据消费 | 预警规则求值读取 `LoopConfidenceLatest` 与 `kpi_snapshot_hourly`（只读），不改 KPI 算法 |
| 回路管理 | 数据消费 | 预警订阅按 `loop_ledger` 展开订阅范围；回路停用时自动置 `alert_rule_subscription.is_active=false` |
| 系统管理 | 复用 | 复用 `sys_config` 全局开关、`SysAuditLog` 系统级审计（规则审计写入专用 `alert_rule_audit_log`）、RBAC 权限码 |
| 回路整定 | 无直接关系 | 预警事件可手动触发整定（通过 Action Tracker 关联），但无自动调用 |

### 11.3 不破坏的边界

- **不直写 DCS**：预警仅生成事件与建议性工单，不涉及任何参数下写（对齐 PRD §2.3 安全边界）。
- **不替代诊断**：预警规则引擎与诊断引擎独立，不修改诊断代码。
- **不引入外部 IM**（Phase 1-3）：仅站内信 + 工作台徽章。
- **不破坏 KPI/可信度算法**：复用 `ConfidenceEvaluator` 与 `kpi_snapshot_hourly`，只读消费。
- **不破坏 IA 重构 Phase A**：预警子菜单挂在"诊断中心"下，不新增一级菜单。

### 11.4 后续文档同步清单

本方案评审通过后，需按以下顺序同步文档（对齐 AGENTS.md 文档权威性）：

1. PRD v6.2：补充 §4.4.6 与 §5.7
2. 实现契约 v2.6：补充 §2/§4/§5/§6/§10
3. FDS v6.1：补充 §5.5
4. DDS v6.1：补充表设计
5. IDS v6.1：补充 API 设计
6. UI/UX v6.2：补充页面设计规范
7. `docs/过程文档/v6-delivery-history.md`：登记 Phase 1-4 交付记录
8. `AGENTS.md`：更新 ORM 表数（38 → 43-44）与模块边界

---

## 附录 A：DSL 校验规则清单（AI 可消费）

```yaml
validation_rules:
  - rule: "ruleType required"
    field: "ruleType"
    check: "in ['THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE', 'TIME_WINDOW']"
  - rule: "scope.loopSelector.type required"
    field: "scope.loopSelector.type"
    check: "in ['ALL', 'LOOP', 'PLANT', 'CONTROL_TYPE']"
  - rule: "scope.loopSelector.value required when type != ALL"
    field: "scope.loopSelector.value"
    check: "not null when type != 'ALL'"
  - rule: "condition.metric valid"
    field: "condition.metric"
    check: "in ['PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D']"
  - rule: "condition.operator valid"
    field: "condition.operator"
    check: "in ['>', '>=', '<', '<=', '==', '!=', 'IN', 'NOT_IN', 'RATE_OF_CHANGE']"
  - rule: "durationSeconds range"
    field: "durationSeconds"
    check: "0 <= value <= 86400"
  - rule: "cooldownSeconds range"
    field: "cooldownSeconds"
    check: "0 <= value <= 86400"
  - rule: "windowSeconds range (DRIFT)"
    field: "condition.windowSeconds"
    check: "300 <= value <= 86400"
  - rule: "composite nesting depth"
    field: "condition"
    check: "nesting_depth <= 3"
  - rule: "dsl json length"
    field: "dsl"
    check: "json_length <= 4000"
  - rule: "actions non-empty"
    field: "actions"
    check: "length >= 1 and contains(type == 'CREATE_EVENT')"
  - rule: "dedupKey variables"
    field: "dedupKey"
    check: "variables in ['loop_id', 'rule_id', 'tag_code', 'severity']"
  - rule: "severity valid"
    field: "severity"
    check: "in ['INFO', 'WARN', 'ERROR', 'CRITICAL']"
  - rule: "confidencePolicy.minLevel valid"
    field: "confidencePolicy.minLevel"
    check: "in ['A', 'B', 'C', 'D', 'E']"
  - rule: "confidencePolicy.action valid"
    field: "confidencePolicy.action"
    check: "in ['SUPPRESS', 'DOWNGRADE']"
```

## 附录 B：术语表

| 术语 | 含义 |
|---|---|
| 预警规则引擎 | 本方案新增的独立模块，基于实时数据流与周期巡检对自定义规则求值并生成预警事件 |
| DSL | Domain-Specific Language，规则定义的结构化 JSON 格式 |
| dry-run | 规则回放验证，给定历史时间范围模拟规则触发 |
| 冷却期 | 同一去重键在指定秒数内不重复告警 |
| 持续时长 | 条件需持续满足指定秒数才告警（去抖） |
| dedupKey | 去重键，默认 `loop_id + rule_id`，可配置为 `loop_id + tag_code` |
| 严重度 | INFO/WARN/ERROR/CRITICAL 四级，对齐 PRD §5.6.1 |
| 可信度联动 | 数据可信度 D/E 级时自动抑制或降级预警 |
| 时效窗口 | 规则仅在指定 cron 时段内生效 |
| 升级（转诊断） | 预警事件手动触发诊断任务，调用 `/diagnosis/trigger` |

## 附录 C：关联文档

- PRD v6.1：`docs/设计文档/01-PRD/PRD.md`
- 实现契约 v2.5：`docs/设计文档/00-BASELINE/implementation-contract.md`
- KPI 计算审查报告：`docs/过程文档/kpi-calculation-review-2026-08-05.md`
- 数据质量评估报告：`docs/过程文档/data-quality-assessment-report-2026-08-05.md`
- UX/IA 审查报告：`docs/过程文档/clpm-ux-ia-audit-report-2026-08-05.md`
- IA 重构方案：`docs/过程文档/clpm-ia-refactor-and-optimization-plan-2026-08-06.md`
- 诊断整改方案：`docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md`
- ops-runbook：`docs/过程文档/ops-runbook.md`
- 数据架构决策：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`
