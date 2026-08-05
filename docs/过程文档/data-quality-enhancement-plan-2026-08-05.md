# 数据质量增强方案（任务 5/7 + 实时回写现状）

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-08-05 |
| 背景 | 7/30 之前数据已清理（66.2M 行删除，保留 15.4M 行）；粘滞系数平稳回路已改返回 0；定时巡检任务已新增 |
| 范围 | 实时回写机制说明（任务 1）、列表数据健康体现方案（任务 5）、存储空间评估与优化（任务 7） |

---

## 1. 实时数据落库机制现状（任务 1 检查结论）

### 1.1 当前机制

| 环节 | 配置 | 状态 |
|---|---|---|
| SignalR 订阅 | `SIGNALR_ENABLED=True`，`SIGNALR_HUB_URL=ws://192.168.100.2:81/...` | ✅ 运行中 |
| Redis 实时缓存 | `realtime:{tagCode}`，TTL 1h + Pub/Sub 广播 | ✅ 运行中 |
| **TDengine 实时直写** | `REALTIME_WRITEBACK_ENABLED=False` | ❌ **已关闭**（注释"已弃用"） |
| **Gap Backfill 断点续传** | `GAP_BACKFILL_ENABLED=True`，最小缺口 60s | ✅ 运行中 |
| 最新落库时间戳 | 2026-08-05 01:45:00（实测） | ✅ 近实时（延迟 ~1min） |

### 1.2 数据流路径

```
AAS SignalR Hub
    ├─→ Redis 缓存（realtime:{tag}，TTL 1h）→ 前端实时展示  ✅ 直连
    └─→ RealtimeSubscriber._buffer（内存）
            └─→ _flush_loop（每 1s）
                    ├─→ Redis 历史缓存（realtime:history:{loop}，4500 点）  ✅
                    └─→ TDengine 直写  ❌ 当前关闭（REALTIME_WRITEBACK_ENABLED=False）

落库实际路径（gap backfill）：
    连接/重连成功 → _maybe_trigger_gap_backfill
        → 检测 _last_flushed_at 缺口 > 60s
        → import_history_data（远端 AAS 历史接口，skip 策略）
        → 写入 TDengine st_loop_data
        → 触发受影响小时的 KPI 回算
```

### 1.3 影响分析

| 维度 | 当前（gap backfill） | 开启直写（writeback） |
|---|---|---|
| TDengine 延迟 | ~1min（补数触发间隔 + 拉取） | ~1s（flush 间隔） |
| 依赖远端 AAS | 是（AAS 不可用则不落库） | 否（SignalR 推送即落库） |
| 重复写入风险 | 低（skip 策略） | 中（直写与 backfill 撞时间戳，skip 可处理） |
| 性能压力 | 低（批量拉取） | 中（每秒 flush，27 回路 ~27 行/s） |
| 断点续传能力 | 强（缺口检测 + 重试） | 弱（进程停机期间无 checkpoint） |

### 1.4 建议

**推荐方案：保持 gap backfill 为主 + 可选开启直写兜底**

- 现状已能满足评估需求（KPI 整点计算窗口远大于 1min 延迟）
- 如需亚秒级实时落库（如实时报警），可开启 `REALTIME_WRITEBACK_ENABLED=True`
- 开启后需观察：① 是否与 gap backfill 产生重复写入告警；② TDengine 写入压力

---

## 2. 列表数据健康体现方案（任务 5）

### 2.1 现状

| 页面 | API | 现有字段 | 缺失字段 |
|---|---|---|---|
| 回路监控列表 | `GET /loops/monitor` | 实时 PV/SP/OP/MODE/质量码/评分 | 可信度、有效数据率、数据完整度 |
| 回路管理列表 | `GET /loops` | 基础信息/类型/状态 | 可信度、完整度、预处理状态 |
| 性能评估列表 | `GET /loops/performance` | 评分/A/F/S/R/**可信度** | 数据完整度 |
| 可信度详情 | `GET /loops/{id}/confidence-latest` | confidence_level/valid_rate/metrics | —（已有，但需单独查） |
| 完整性检查 | `POST /loops/data-import/integrity-check` | completeness/colDetails/missingColumns | —（已有，但需手动触发） |

**关键发现**：可信度/完整度数据后端已具备，只是未在列表页联表展示。

### 2.2 方案 A（推荐）：回路监控列表增加「数据健康」列组

**后端改造**（`list_loop_monitor` 服务）：

```python
# LEFT JOIN LoopConfidenceLatest（取最新一条）
# 增加返回字段：
{
    ...现有字段...,
    "confidenceLevel": "A",          # 可信度等级 A/B/C/D/E
    "validRate": 0.9723,             # 有效数据率
    "pvCompleteness": 0.978,         # PV 完整度（来自最近巡检快照）
    "lastIntegrityCheck": "2026-08-05 02:00",  # 最近巡检时间
}
```

**完整度数据来源**：
- 不在列表请求中实时查 TDengine（会拖慢列表，27 回路 × 7 列 COUNT 需 ~3s）
- 改为读取「最近一次完整性巡检快照」（每日 02:00 巡检结果持久化到 `loop_integrity_snapshot` 表）
- 列表展示快照值 + 巡检时间，点击回路可查看实时明细

**前端改造**（`loop-performance.vue` / 回路监控页）：

| 新增列 | 展示形式 | 说明 |
|---|---|---|
| 可信度 | A/B/C/D/E 彩色标签（绿/蓝/黄/橙/红） | 复用现有可信度组件 |
| 有效数据率 | 百分比 + 迷你进度条 | <80% 标红 |
| 数据完整度 | 百分比 + 迷你进度条 | <95% 标红（对齐巡检阈值） |
| 预处理状态 | 图标（✅/⚠️/❌） | 聚合：E 级→❌，D 级→⚠️，A/B/C→✅ |

### 2.3 方案 B：新增「数据健康」独立 Tab

在回路管理模块新增「数据健康」子页：
- 顶部：全局完整度概览（环形图 A/B/C/D/E 分布）
- 中部：按回路表格（含每列完整度明细 colDetails）
- 底部：时间缺口热力图（哪些时段缺数据）

**适用场景**：需要深度排查数据质量时使用，列表页保持简洁。

### 2.4 方案 C：测点配置页增加 tag 级质量统计

在测点配置列表增加每行 tag 的：
- 最近 24h 好值率（Good 质量码占比）
- 最近 24h 有效率（valid 占比）
- 质量码分布（Good/Bad/Unknown 饼图）

**适用场景**：排查单个 tag 数据质量问题。

### 2.5 实施建议

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P0 | 方案 A：列表增加可信度列（联表 LoopConfidenceLatest） | 0.5d |
| P0 | 完整性巡检快照持久化表 + 列表展示 | 1d |
| P1 | 方案 B：数据健康独立 Tab | 2d |
| P2 | 方案 C：测点级质量统计 | 1.5d |

---

## 3. 存储空间评估与优化方案（任务 7）

### 3.1 当前实测

| 指标 | 实测值 |
|---|---|
| 当前回路数 | 27 |
| 数据时间跨度 | 33 天（7/2–8/5，删除前） |
| 总行数 | 81,630,491 行（删除前）/ 15,415,612 行（删除后） |
| TDengine vnode 占用 | 2.9 GB |
| 单回路单天 | ~3.25 MB（2.9GB / 27 / 33） |
| 行平均大小 | ~37 字节（2.9GB / 81.6M，含压缩） |
| 磁盘 | 3.0T 总，128G 已用，2.9T 可用 |

### 3.2 1000 回路 × 1 年推算

| 场景 | 行数 | 存储 |
|---|---|---|
| 1000 回路 × 365 天 × 86400 点/天 | 31,536,000,000 行（31.5B） | **~1,186 GB ≈ 1.19 TB** |
| 含超密写入（×1.2 安全系数） | 37.8B 行 | ~1.43 TB |
| 3TB 磁盘可承载 | — | ~2.5 年（纯秒级） |

**结论**：1000 回路 1 年 ~1.2 TB，3TB 磁盘可承载，但需规划降采样释放空间。

### 3.3 CLPM 核心功能的数据时间窗口需求

| 功能 | 时间窗口 | 精度要求 |
|---|---|---|
| KPI 性能评估 | 30 天（可配） | **秒级**（1Hz，计算准确性） |
| 诊断中心整点评估 | 上一完整小时 | **秒级** |
| 回路整定辨识 | 30 天 | **秒级**（ARMA 辨识需高频） |
| 数据完整性检查 | 任意窗口 | 秒级（点数判定） |
| 历史趋势分析 | 3-12 个月 | 分钟级足够 |
| 年度报表 | 12 个月 | 小时级足够 |

**关键约束**：KPI 评估窗口 30 天，秒级数据保留 30 天即可满足核心计算。

### 3.4 优化方案

#### 方案 1（推荐）：TDengine TTL + Continuous Query 三级降采样

```
秒级原始数据（st_loop_data）    保留 30 天    → 评估与辨识
    ↓ CQ 每分钟聚合
分钟级数据（st_loop_data_1min）  保留 1 年     → 趋势分析
    ↓ CQ 每小时聚合
小时级数据（st_loop_data_1h）    保留 5 年     → 年度报表
```

**TDengine DDL 示例**：
```sql
-- 1. 创建降采样超级表
CREATE STABLE IF NOT EXISTS signal_sim.st_loop_data_1min (
    ts TIMESTAMP,
    pv_avg FLOAT, pv_min FLOAT, pv_max FLOAT, pv_cnt INT,
    sp_avg FLOAT, op_avg FLOAT, op_min FLOAT, op_max FLOAT,
    mode_auto_time INT  -- AUTO 模式时长（秒）
) TAGS (loop_id BINARY(64), unit_id BINARY(64));

-- 2. Continuous Query：每分钟聚合上一分钟的秒级数据
CREATE CONTINUOUS QUERY IF NOT EXISTS cq_loop_1min
ON signal_sim.st_loop_data
RESAMPLE EVERY 1m
SELECT
    FIRST(ts) AS ts,
    AVG(pv) AS pv_avg, MIN(pv) AS pv_min, MAX(pv) AS pv_max, COUNT(pv) AS pv_cnt,
    AVG(sp) AS sp_avg, AVG(op) AS op_avg, MIN(op) AS op_min, MAX(op) AS op_max,
    SUM(CASE WHEN mode IN (1,2,3,4) THEN 1 ELSE 0 END) AS mode_auto_time
FROM signal_sim.st_loop_data
INTERVAL(1m)
GROUP BY tbname;

-- 3. 原始表 TTL：30 天自动过期
ALTER TABLE signal_sim.st_loop_data MODIFY TTL 30;
-- 或建库时指定：CREATE DATABASE signal_sim KEEP 30,365,1825 ...
```

**存储预估（1000 回路 × 1 年）**：
| 层级 | 行数 | 存储 |
|---|---|---|
| 秒级 30 天 | 1000×30×86400 = 2.6B 行 | ~96 GB |
| 分钟级 1 年 | 1000×365×1440 = 0.5B 行 | ~19 GB |
| 小时级 5 年 | 1000×5×365×24 = 44M 行 | ~1.6 GB |
| **合计** | — | **~117 GB**（vs 秒级全留 1.19 TB，节省 90%） |

#### 方案 2：按回路配置 `data_retention_days`

LoopLedger 已有 `data_retention_days` 字段（可按回路配置保留天数）：
- 关键回路（importance_level=1）：保留 90 天秒级
- 普通回路：保留 30 天秒级
- 辅助回路：保留 7 天秒级

**适用**：差异化保留策略，但需定期清理任务执行 DELETE。

#### 方案 3：定期归档冷数据

- 30 天前数据导出为 Parquet/CSV 归档到对象存储
- TDengine 仅保留热数据
- 归档数据可按需重新导入回算

**适用**：合规要求长期保留但很少访问的场景。

### 3.5 推荐实施路径

| 优先级 | 内容 | 收益 |
|---|---|---|
| P0 | 实施方案 1：CQ 降采样 + TTL 30 天 | 存储从 1.19TB 降至 ~117GB（1000 回路/年） |
| P0 | 验证 CQ 聚合结果与秒级计算一致性（KPI 回算对比） | 确保降采样不影响评估准确性 |
| P1 | 方案 2：按 importance_level 差异化 TTL | 关键回路保留更久 |
| P2 | 方案 3：冷数据归档 | 合规长存 |

**注意**：降采样后，30 天前的秒级数据将不可用于回算。需在 CQ 生效前完成历史数据回算，或保留一份秒级归档。

---

## 4. 待决策事项

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| 1 | 实时落库机制 | A. 保持 gap backfill / B. 开启直写 / C. 两者结合 | A（现状满足评估需求） |
| 2 | 列表数据健康体现 | A. 方案 A 列表增列 / B. 方案 B 独立 Tab / C. 全做 | 先 A 后 B |
| 3 | 存储降采样 | A. 方案 1 CQ+TTL / B. 方案 2 差异化 TTL / C. 暂不实施 | A（1000 回路前必须实施） |
