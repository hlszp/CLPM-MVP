# P4 复杂回路聚合全链路验证与清理总结报告

**日期**: 2026-07-24
**范围**: P4 S1-S4 端到端联调验证 + 测试环境清理恢复
**结论**: ✅ 验证全部通过，环境已恢复基线
**关联文档**:
- [P4 S1-S4 全链路端到端验证报告](./p4-complex-loop-e2e-test-report-2026-07-24.md)
- [复杂回路聚合 RFC](./complex-loop-aggregation-rfc-2026-07-24.md)

---

## 一、活动概述

本次工作分两个阶段：**端到端联调验证**（确认 P4 S1-S4 功能正确性）和**测试环境清理**（恢复数据库与运行时状态至基线）。

| 阶段 | 时段（本地） | 内容 |
|------|-------------|------|
| 端到端验证 | 17:15 ~ 17:22 | 后端重启 → 基线聚合 → 创建复杂分组 → 聚合验证 → 解除分组 → 恢复确认 |
| 环境清理 | 17:22 ~ 17:45 | DB 全表检查 → 快照污染排查 → ts_end 修复 → Redis/临时文件清理 → 最终验证 |

---

## 二、验证阶段结果

### 2.1 测试方案

| 项目 | 值 |
|------|------|
| 装置节点 | `3353a2b2`（脱甲烷精馏单元） |
| 时间窗 | 2026-07-24 07:00:00 ~ 07:01:00 |
| 参与回路 | 7 个（均有 07:00 SUCCESS 快照） |
| 分组回路 | 41FIC40504_PIDA (MAIN, level 1, weight 3.0) + 41FIC40519_PIDA (SUB, level 2, weight 2.0) |

### 2.2 核心验证结果

| 指标 | BEFORE（7 单回路） | AFTER（6 去重后） | 手算期望 | 结论 |
|------|-------------------|-------------------|---------|------|
| loop_count | 7 | **6** | 6 (7−1) | ✅ 去重生效 |
| score | 57.94 | **57.82** | 462.57/8=57.82 | ✅ MAIN 代表保留 |
| accuracy_rate | 87.50 | **87.12** | 696.95/8=87.12 | ✅ 加权正确 |
| steady_rate | 0.33 | **0.41** | 3.28/8=0.41 | ✅ 分母变化正确 |

### 2.3 DEBUG 日志确认

```
BEFORE: [节点级聚合-S3] 输入回路=7, 去重后代表=7, 复杂组=0
AFTER:  [节点级聚合-S3] 输入回路=7, 去重后代表=6, 复杂组=1
```

### 2.4 验证矩阵（9/9 通过）

| # | 验证点 | 阶段 | 结果 |
|---|--------|------|------|
| 1 | include_in_evaluation=False 回路不进入聚合 | S1 | ✅ |
| 2 | complex_loop_group_id + complex_role 持久化 | S2 | ✅ |
| 3 | 同组回路去重为 1 个代表 | S3 | ✅ |
| 4 | MAIN 角色优先作为代表 | S3 | ✅ |
| 5 | 加权平均使用 importance_level 权重 | S3 | ✅ |
| 6 | 批量分组 API 正确创建分组 | S4 | ✅ |
| 7 | 解除分组恢复原始状态 | S4 | ✅ |
| 8 | 单回路（无分组）不受影响 | S3 | ✅ |
| 9 | auto_loop_ratio 基于去重后代表计算 | S3 | ✅ |

详细验证过程见 [端到端验证报告](./p4-complex-loop-e2e-test-report-2026-07-24.md)。

---

## 三、清理阶段：发现与修复的问题

### 3.1 问题一：后端进程挂死（验证前发现）

**现象**：验证开始前检查发现后端（port 7101）接受 TCP 连接但不响应 HTTP 请求（curl 超时）。

**根因分析**：

```
进程树（异常状态）：
PID 73610 (uvicorn --reload watcher, 启动于 12:48)
  ├─ PID 29802 (multiprocessing spawn → celery 子进程管理)
  │    ├─ PID 29812 (celery beat)
  │    └─ PID 29814 (celery worker main)
  │         └─ PID 29817~29829 (celery worker pool × 13)
  └─ PID 73611 (resource tracker)
  [缺失] uvicorn HTTP worker 进程 — 实际处理请求的子进程不存在
```

uvicorn `--reload` 模式下，文件变更触发 worker 重载，但 HTTP worker 进程在重载过程中崩溃未恢复。watcher 父进程仍持有 socket（故端口可连接），但无 worker 处理请求（故 HTTP 超时）。

触发重载的文件变更：`frontend/apps/web-antd/src/api/dcs.ts` 和 `pid-template/index.vue` 的格式化修改（与 P4 无关，但触发了 uvicorn watcher 检测）。

**处置**：
1. `kill 73610`（SIGTERM）— uvicorn watcher 未响应（已挂死）
2. `pkill -f "celery -A app.tasks.celery_app"` — 清理残留 celery 进程
3. `kill -9 73610 73609` — 强制终止挂死的 uvicorn + uv wrapper
4. 重新启动：`uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload`
5. lifespan 自动拉起 Celery Worker + Beat 子进程

**影响**：无数据影响（进程挂死期间无请求被处理）。验证前已恢复正常。

**预防建议**：uvicorn `--reload` 模式在文件频繁变更时可能触发 worker 崩溃；生产环境应使用不带 `--reload` 的启动方式或 Gunicorn 管理进程。

---

### 3.2 问题二：节点快照 ts_end 被非标准时间窗口覆盖

**现象**：验证阶段的最终确认 API 调用（用于确认解除分组后恢复基线）使用了非标准的 1 分钟时间窗口 `tsEnd=07:01:00`，导致 `kpi_node_snapshot_hourly` 表 07:00 行的 `ts_end` 从标准值 `08:00:00` 被覆盖为 `07:01:00`，`score` 从 `57.23` 变为 `57.94`。

**根因分析**：

| 项目 | 定时任务（基线） | 测试最终确认调用 |
|------|-----------------|-----------------|
| ts_start | 07:00:00 | 07:00:00 |
| ts_end | **08:00:00**（标准 1 小时窗） | **07:01:00**（测试用 1 分钟窗） |
| KPI 快照选取范围 | 07:00 + 08:00 快照（DISTINCT ON 取最新） | 仅 07:00 快照 |
| 41LIC40201 选用快照 | 08:00（score=60.48，最新） | 07:00（score=59.61） |
| 聚合 score | **57.23** | **57.94** |

`save_node_snapshot` 函数使用 `(plant_node_id, ts_start)` 作为幂等键执行 upsert。相同 `ts_start=07:00` 的调用会覆盖已有行的所有字段（含 `ts_end` 和 `score`），导致标准 1 小时窗口的快照被 1 分钟窗口的结果覆盖。

**代码位置**：[node_performance.py:544-559](../../backend/app/services/node_performance.py#L544-L559)

```python
# save_node_snapshot — upsert 逻辑
existing = await db.execute(
    select(KpiNodeSnapshotHourly).where(
        KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
        KpiNodeSnapshotHourly.ts_start == ts_start,  # ← 仅按 ts_start 去重
    )
)
if existing:
    for key, val in node_snap_data.items():
        if hasattr(existing, key):
            setattr(existing, key, val)  # ← ts_end 也被覆盖
```

**处置**：用标准 1 小时窗口重新触发聚合，恢复基线值：

```bash
POST /api/v1/performance/nodes/3353a2b2.../calculate
{"tsStart":"2026-07-24T07:00:00","tsEnd":"2026-07-24T08:00:00"}
```

**修复后状态**：

| 字段 | 修复前（被污染） | 修复后（恢复基线） |
|------|----------------|-------------------|
| ts_end | 07:01:00 | **08:00:00** ✅ |
| score | 57.94 | **57.23** ✅ |
| loop_count | 7 | 7 |
| unit_kpi_summary.avg_score | 57.94 | **57.23** ✅ |

**影响范围**：仅 `kpi_node_snapshot_hourly` 和 `unit_kpi_summary` 表中 `plant_node_id=3353a2b2` 且 `ts_start=2026-07-24 07:00:00` 的各 1 行。其他时间窗口、其他节点均未受影响。

**预防建议**：
1. 测试调用节点聚合 API 时应使用与定时任务一致的标准时间窗口（`ts_end = ts_start + 1 小时`）
2. 可考虑在 `save_node_snapshot` 中增加 `ts_end` 一致性校验：若已有行 `ts_end` 与传入值不一致，拒绝覆盖或记录告警
3. 或在 upsert 条件中加入 `ts_end`：`(plant_node_id, ts_start, ts_end)` 三键幂等，不同窗口的快照各自独立存储

---

### 3.3 问题三：测试期间临时写入的快照被定时任务覆盖

**现象**：测试的 AFTER 分组调用（loop_count=6, score=57.82）理论上会写入 `kpi_node_snapshot_hourly` 的 07:00 行，但在清理阶段检查时该行已恢复为 loop_count=7。

**根因**：Celery Beat 定时任务在 17:34（本地）自动触发了节点聚合，使用当前 DB 状态（complex 字段已清空）重新计算，覆盖了测试期间的临时写入。

**影响**：正面影响 — 定时任务自动修复了测试期间的临时污染，减少了手动清理工作量。无需额外处置。

---

## 四、清理检查清单

### 4.1 数据库

| # | 表 | 检查内容 | 结果 | 处置 |
|---|-----|---------|------|------|
| 1 | `loop_ledger` | 全表 complex_loop_group_id / complex_role 是否均为 NULL | 27 回路 / 0 分组 ✅ | 无需处置（验证时已通过 API 解除分组） |
| 2 | `kpi_node_snapshot_hourly` | 测试节点是否有 loop_count=6 的污染行 | 全部 loop_count=7 ✅ | 无需处置（定时任务已覆盖） |
| 3 | `kpi_node_snapshot_hourly` | 测试节点 07:00 行 ts_end 是否为标准 1 小时 | ts_end=07:01 ❌ → 已修复为 08:00 ✅ | 用标准窗口重算恢复 |
| 4 | `unit_kpi_summary` | 测试节点 07:00 行 avg_score / evaluated_loops | avg_score=57.23, evaluated_loops=7 ✅ | 随 node 快照修复同步恢复 |
| 5 | `kpi_snapshot_custom` | 是否有测试产生的自定义快照 | 无 ✅ | 无需处置 |
| 6 | 其他节点 | 是否受测试影响 | 仅测试节点 3353a2b2 被操作，其他节点未受影响 ✅ | 无需处置 |

### 4.2 运行时环境

| # | 检查项 | 结果 | 处置 |
|---|--------|------|------|
| 1 | Redis 缓存 | 0 个匹配测试节点的残留键 ✅ | 无需处置 |
| 2 | 临时文件 | `/tmp/clpm_token.txt` + `/tmp/clpm_backend.log` 已删除 ✅ | 已清理 |
| 3 | 后端进程 | PID 83151, port 7101 LISTEN, 正常响应 ✅ | 验证前重启（修复挂死问题） |
| 4 | Celery Worker/Beat | 随后端 lifespan 自动启动，正常运行 ✅ | 无需处置 |
| 5 | 前端 dev server | port 5666 运行中 ✅ | 无需处置 |

---

## 五、最终基线确认

清理完成后，对测试装置节点执行最终聚合验证（标准 1 小时窗口）：

```
POST /api/v1/performance/nodes/3353a2b2.../calculate
{"tsStart":"2026-07-24T07:00:00","tsEnd":"2026-07-24T08:00:00"}
```

| 字段 | 实测值 | 基线期望 | 结论 |
|------|--------|---------|------|
| status | SUCCESS | SUCCESS | ✅ |
| loop_count | 7 | 7 | ✅ |
| score | 57.23 | 57.23 | ✅ |
| evaluated_loops | 7 | 7 | ✅ |
| ts_end（DB 行） | 08:00:00 | 08:00:00 | ✅ |

**结论**：数据库与运行时环境已完全恢复至测试前基线状态。

---

## 六、改进建议

### 6.1 测试规范

| 优先级 | 建议 | 理由 |
|--------|------|------|
| 高 | 测试调用节点聚合 API 时必须使用标准 1 小时窗口 | 避免 ts_end 被非标准值覆盖（本次问题二） |
| 中 | 测试创建的分组应在测试结束时立即通过 API 解除 | 减少 DB 污染窗口（本次已做到，但可文档化） |
| 低 | 编写自动化测试脚本，测试后自动验证 DB 恢复 | 减少手动检查遗漏风险 |

### 6.2 代码改进

| 优先级 | 建议 | 位置 | 理由 |
|--------|------|------|------|
| 中 | `save_node_snapshot` upsert 增加 ts_end 一致性校验 | [node_performance.py:544](../../backend/app/services/node_performance.py#L544) | 防止不同时间窗口的调用互相覆盖 |
| 低 | uvicorn `--reload` worker 崩溃后自动恢复机制 | 部署配置 | 本次后端挂死的根因（开发环境） |
| 低 | 节点聚合 API 文档标注 ts_end 应为 ts_start + 1 小时 | API schema / docstring | 指导正确使用 |

### 6.3 后续验证补充

| 优先级 | 场景 | 说明 |
|--------|------|------|
| 中 | 多组同时去重 | 本次仅验证 1 组（2 回路），应补充 3+ 组并行场景 |
| 中 | MAIN 缺席 → confidence 回退 | 本次 MAIN 存在，回退逻辑仅由单元测试覆盖 |
| 低 | 跨装置节点聚合 | 本次仅验证单装置，应补充 AREA/FACTORY 级递归聚合 |

---

## 七、提交记录

| 提交 | 内容 | 远端 |
|------|------|------|
| `b01ea26c` | feat(loop): 复杂回路分组配置 UI 与 API 全链路（P4 S4） | gitea ✅ github ✅ |
| `37ff9f52` | docs: P4 复杂回路聚合 S1-S4 全链路端到端验证报告 | gitea ✅ github ✅ |

---

## 附录：时间线

| 时间（本地） | 事件 |
|-------------|------|
| 17:10 | 开始验证，发现后端挂死 |
| 17:12 | 强制终止挂死进程，重启后端 |
| 17:15 | 后端启动完成，登录获取 Token |
| 17:20 | 基线聚合（BEFORE）：loop_count=7, score=57.94 |
| 17:21 | 创建复杂分组 + 聚合验证（AFTER）：loop_count=6, score=57.82 |
| 17:22 | 解除分组 + 最终验证：loop_count=7, score=57.94 |
| 17:25 | 提交验证报告文档 |
| 17:30 | 开始环境清理检查 |
| 17:34 | 定时任务自动覆盖 07:00 快照（恢复 loop_count=7） |
| 17:40 | 发现 ts_end 被非标准窗口覆盖（07:01 而非 08:00） |
| 17:43 | 用标准 1 小时窗口重算，恢复 ts_end=08:00, score=57.23 |
| 17:45 | 最终验证通过，清理完成 |
