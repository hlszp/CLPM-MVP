# CLPM 性能评估算法优化总结

> 版本：v1.0 ｜ 日期：2026-07-15 ｜ 状态：阶段性优化完成，待最终验证

## 目录

1. [当前代码架构设计](#1-当前代码架构设计)
2. [算法优化目标](#2-算法优化目标)
3. [优化方案](#3-优化方案)
4. [已完成的整改工作](#4-已完成的整改工作)
5. [测试结果](#5-测试结果)
6. [下一步建议方向](#6-下一步建议方向)

---

## 1. 当前代码架构设计

### 1.1 v4.0 三层计算架构

CLPM v6.1 后端采用 v4.0 重构后的三层架构，核心组件如下：

| 层级 | 组件 | 路径 | 职责 |
|---|---|---|---|
| **数据编排层** | DataPlanner | [app/services/data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) | 指标驱动的数据获取：读契约 → 合并查询计划 → 查 L1 缓存 → 未命中查 TDengine + 8 步预处理 → 写缓存 → 组装 MetricDataBundle |
| **指标计算层** | MetricCalculator | [app/tasks/kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助），通过 `DataPlanner.request_bundles()` 获取数据 |
| **可信度评估层** | ConfidenceEvaluator | [app/services/confidence_evaluator.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/confidence_evaluator.py) | 可信度 A/B/C/D/E 评估（valid_rate 阈值 95/80/60/20%）+ 综合评分 P = (A·a + F·f + S·s)/(a+f+s) × R |

### 1.2 数据流链路

```
API / Celery Beat
    ↓
calculate_hourly_kpi (Celery Task)
    ↓
_do_calculate (async)
    ↓
[Phase 1: 预热] _prewarm_cache_for_loops  ← 当前已注释禁用
    ↓ (asyncio.Semaphore 并发)
DataPlanner.request_bundles()
    ↓
L1DataBlockCache.get()  →  命中？→ 组装 MetricDataBundle → L2BundleCache
    ↓ 未命中
_execute_query_plan (asyncio.gather 并行 tagGroup 查询)
    ↓
_query_and_preprocess
    ├── HTTP API 查询 (httpx.AsyncClient，连接池)
    └── PreprocessingPipeline.process (asyncio.to_thread 释放事件循环)
        ├── Step ① 质量码识别
        ├── Step ③ 量程归一化
        ├── Step ④ 异常值识别（8 类检测，纯 Python + numpy）
        ├── Step ② 有效性标记
        ├── Step ⑥ 连续性检查
        └── Step ⑧ QualitySummary
    ↓
L1DataBlockCache.set_many (zstd 压缩 + Redis Pipeline 批量写入)
    ↓
[Phase 2: 计算] _calc_with_sem (asyncio.Semaphore 并发)
    ↓
_calculate_loop_kpi
    ├── MetricCalculator 计算 12 个指标
    ├── ConfidenceEvaluator 综合评分
    └── UPSERT kpi_snapshot_hourly
```

### 1.3 缓存体系

| 层级 | 组件 | 存储位置 | 压缩格式 | TTL 策略 |
|---|---|---|---|---|
| L1 DataBlock | [L1DataBlockCache](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py) | Redis DB0 | zstd level=3 + base64 | BASE=3600s, HF=300s |
| L2 MetricDataBundle | [L2BundleCache](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l2_bundle.py) | Redis DB0 | zstd level=3 + base64 | 按指标配置 |

**缓存 Key 设计**：
```
pdb:{loopId}:{tagGroup}:{startEpoch}:{endEpoch}:{freq}:{qualityPolicy}:{preVer}:{cfgVer}
```
时间窗口通过 epoch 整数纳入 Key，确保相同窗口的请求命中同一 Key。

### 1.4 并发控制

| 参数 | 当前值 | 位置 | 说明 |
|---|---|---|---|
| `CONCURRENCY` | 5 | kpi_calc.py:60 | Phase 2 计算阶段并发数（v6.2 临时降低） |
| `_PREWARM_CONCURRENCY` | 27 | kpi_calc.py:509 | Phase 1 预热阶段并发数（与回路数一致） |
| `max_connections` | 50 | tdengine.py | httpx 连接池最大连接数 |
| `max_keepalive_connections` | 30 | tdengine.py | httpx 连接池保持活跃连接数 |
| `task_time_limit` | 1800s | celery_app.py:50 | Celery 任务硬超时（30 分钟） |

### 1.5 关键技术约束

- **数据源**：HTTP API（`http://192.168.100.2:81/api/services/v1/HistoryData/Get`），每个请求支持多 tagCodes 批量查询
- **采样策略**：KPI 计算路径不进行 LTTB 降采样，按控制类型阈值固定 1s 采样
- **GIL 限制**：预处理管道纯 Python 实现（`detect_frozen` 使用 O(n×win) 滑动窗口，`detect_hf_noise` 使用 numpy FFT），高并发下 GIL 竞争导致单任务从 0.6s 暴增到 12-29s
- **tagGroup 复用**：流量回路（FC）BASE 已是 1s，OP_HF/PVOP_HF/MODE_HF/QUALITY_HF 直接从 BASE DataBlock 派生，仅需 1 次 HTTP API 查询

---

## 2. 算法优化目标

### 2.1 业务目标

**27 个回路的性能指标计算在 1 小时计算周期内，整体处理时间严格控制在 16 秒以内。**

### 2.2 约束条件

| 约束 | 要求 |
|---|---|
| 计算精度 | 保持原有 KPI 计算准确性，不得为性能牺牲精度 |
| 可扩展性 | 优化方案需支持回路数横向扩展（未来 50+ 回路） |
| 稳定性 | 至少 10 次端到端测试在不同负载下均稳定达标 |
| 可维护性 | 优化不引入过度复杂的架构，便于后续维护 |

### 2.3 性能基线（优化前）

| 场景 | 计时边界 | 目标 | 当前结论 |
|---|---|---|---|
| 同窗口 L2 热缓存单大回路 | 任务开始至快照 UPSERT 完成 | p95 ≤ 0.6s | 待以专用基准验证 |
| 冷缓存单大回路 | 包含历史数据 HTTP、预处理、缓存、KPI、UPSERT | 独立分阶段 SLO | 外部 HTTP 已观测 0.7-0.9s，不能承诺完整路径 ≤0.6s |
| 1000 回路小时任务 | 队列等待、计算、落库至任务终态 | ≤600s | 待容量测试验证 |

---

## 3. 优化方案

### 3.1 已实施优化（按时间顺序）

#### 优化 1：L1/L2 缓存序列化异步化 ✅

**问题**：zstd 压缩/解压缩在事件循环中同步执行，阻塞其他协程。

**方案**：使用 `asyncio.to_thread` 将序列化/反序列化操作移至线程池。

**代码位置**：[l1_datablock.py:144,178,224](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py#L144)、[l2_bundle.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l2_bundle.py)

#### 优化 2：zstd 压缩器线程安全 ✅

**问题**：`ZstdCompressor` 实例非线程安全，多线程并发使用导致 SIGSEGV。

**方案**：使用 `threading.local()` 为每个线程维护独立的压缩器/解压器实例。

**代码位置**：[l1_datablock.py:357-371](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py#L357-L371)

#### 优化 3：两阶段计算架构 + Worker 启动预热 ✅（当前临时禁用）

**问题**：冷启动时取数+预处理+计算+UPSERT 串行执行，总耗时长。

**方案**：
- Phase 1：预热缓存（仅取数+预处理+写缓存，高并发，无计算无 UPSERT）
- Phase 2：计算（L2 缓存全命中，每回路 ~0.04s）
- `worker_ready` 信号：Worker 启动时自动预热上一个完整小时的数据

**代码位置**：[kpi_calc.py:512-568](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L512-L568)（`_prewarm_cache_for_loops`）、[kpi_calc.py:331-340](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L331-L340)（`_prewarm_on_worker_start`，当前禁用）

#### 优化 4：DataPlanner 预处理异步化 ✅

**问题**：8 步预处理管道在事件循环中同步执行，阻塞 HTTP API 查询。

**方案**：使用 `asyncio.to_thread(pipeline.process, raw, task.tag_group)` 将预处理移至线程池。

**代码位置**：[data_planner.py:602](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py#L602)

#### 优化 5：DataPlanner 查询计划并行化 ✅

**问题**：多个 tagGroup 查询串行执行。

**方案**：使用 `asyncio.gather` 并行执行所有非复用 task（tagGroup 查询）。

**代码位置**：[data_planner.py:514-516](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py#L514-L516)

#### 优化 6：HTTP 连接池调优（已回退）

**尝试**：将 `max_connections` 从 50 增大到 150，`max_keepalive_connections` 从 30 增大到 80。

**结果**：❌ 回退。HTTP API 服务端并发能力有限，过高并发（>50）导致请求排队，冷启动从 37.4s 暴增到 97.3s。

**代码位置**：[tdengine.py](file:///Users/zhangping/DEV/CLPM/backend/app/core/tdengine.py)（已回退为 50/30）

#### 优化 7：预热并发度提升（已回退）

**尝试**：将 `_PREWARM_CONCURRENCY` 从 27 增大到 100。

**结果**：❌ 回退。27 loop × 4 tagGroup = 108 个并发 HTTP API 请求同时发出，服务端排队严重。回退为 27。

#### 优化 8：降低计算并发度 ✅

**问题**：CONCURRENCY=20 时，HTTP API 排队（query=23-38s/请求）+ GIL 竞争（preprocess=0.5-29s）叠加。

**方案**：将 `CONCURRENCY` 从 20 降到 5。

**结果**：冷启动从 91.14s 降到 41.66s（改善 54%），但仍未达标。

**代码位置**：[kpi_calc.py:60](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L60)

### 3.2 已否决方案

#### Beat 提前 5 分钟预热下一完整小时 ❌

缓存键包含完整时间窗的 `start/end`。xx:55 时下一整点所需的一小时窗口尚未结束，`prewarm_cache(None)` 只能准备上一完整小时，不能命中下一整点的 L1/L2 缓存。

该方案不纳入生产优化路径。`prewarm_cache(ts_start)` 仅可用于已经结束窗口的回算或明确指定窗口的准备。若需增量预热，必须另行设计部分窗口缓存、末段补齐、迟到数据失效与数据血缘。

---

## 4. 已完成的整改工作

### 4.1 整改清单

| 编号 | 优化项 | 状态 | 提交方式 | 影响文件 |
|---|---|---|---|---|
| 1 | L1/L2 缓存序列化 asyncio.to_thread | ✅ 完成 | 未提交（本地） | l1_datablock.py, l2_bundle.py |
| 2 | zstd 压缩器 threading.local 线程安全 | ✅ 完成 | 未提交（本地） | l1_datablock.py, l2_bundle.py |
| 3 | 两阶段计算 + worker_ready 预热 | ✅ 完成（当前禁用） | 未提交（本地） | kpi_calc.py |
| 4 | DataPlanner 预处理 asyncio.to_thread | ✅ 完成 | 未提交（本地） | data_planner.py |
| 5 | DataPlanner 查询计划 asyncio.gather | ✅ 完成 | 未提交（本地） | data_planner.py |
| 6 | HTTP 连接池 50→150 | ❌ 回退 | — | tdengine.py |
| 7 | 预热并发度 27→100 | ❌ 回退 | — | kpi_calc.py |
| 8 | CONCURRENCY 20→5 | ✅ 完成 | 未提交（本地） | kpi_calc.py |
| 9 | Beat 提前 5 分钟预热 | ❌ 已否决 | — | — |
| 10 | 会话生命周期、预热统计、Layer2 依赖、冻结检测线性化 | ✅ 首轮完成 | 未提交（本地） | kpi_calc.py, tasks.py, outlier_detection.py |

### 4.2 当前代码状态（未提交的本地修改）

| 文件 | 修改内容 |
|---|---|
| [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) | `CONCURRENCY=5`；Phase 1 预热注释禁用；`worker_ready` 预热禁用；`_PREWARM_CONCURRENCY=27`；新增 `_prewarm_cache_for_loops`、`calculate_custom_batch_kpi`、时区修复 |
| [tdengine.py](file:///Users/zhangping/DEV/CLPM/backend/app/core/tdengine.py) | `max_connections=50`，`max_keepalive_connections=30`（回退后状态） |
| [data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) | `import asyncio`；`asyncio.to_thread` 预处理；`asyncio.gather` 并行化；新增计时日志 |
| [l1_datablock.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py) | `asyncio.to_thread` 序列化/反序列化；`threading.local()` zstd 压缩器 |
| [l2_bundle.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l2_bundle.py) | 与 l1_datablock.py 相同的修改模式 |

---

## 5. 测试结果

### 5.1 单元测试

| 测试范围 | 结果 | 回归状态 |
|---|---|---|
| 后端全量单元测试 | 1696 passed, 1 skipped | ✅ 零回归 |

### 5.2 端到端性能测试

#### 热启动场景（L2 缓存全命中）

| 并发度 | 测试次数 | 耗时范围 | 达标率 |
|---|---|---|---|
| CONCURRENCY=20 | 10 | 2.16-2.24s | 10/10 ✅ |
| CONCURRENCY=5 | 2 | 1.63-1.64s | 2/2 ✅ |

#### 冷启动场景（无缓存，跳过 Phase 1 预热）

| 并发度 | 测试次数 | 耗时 | 达标率 | 瓶颈分析 |
|---|---|---|---|---|
| CONCURRENCY=20 | 1 | 91.14s | 0/1 ❌ | query=23-38s（HTTP API 排队），preprocess=0.5-29s（GIL 竞争） |
| CONCURRENCY=5 | 1 | 41.66s | 0/1 ❌ | query=1-6s（改善），preprocess=2.5-12s（GIL 竞争仍严重） |

### 5.3 冷启动瓶颈分解（CONCURRENCY=5）

基于 [data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) 计时日志分析：

| 阶段 | 单任务耗时 | 瓶颈原因 |
|---|---|---|
| HTTP API 取数 | 1-6s | 服务端并发处理能力有限 |
| 8 步预处理 | 2.5-12s | GIL 限制纯 Python 预处理并行，高并发下单任务从 0.6s 暴增到 12s |
| 缓存写入 | ~0.3s | zstd 压缩 + Redis Pipeline |
| 指标计算 | ~0.04s | L2 缓存命中，CPU 计算极快 |

**关键发现**：
- 低并发末尾（无 GIL 竞争）：query=0.7-0.9s，preprocess=0.6-0.8s → 证明单任务本身性能良好
- GIL 是预处理并行的根本限制，`asyncio.to_thread` 释放事件循环但无法绕过 GIL

### 5.4 性能目标达成情况

| 场景 | 目标 | 当前 | 状态 |
|---|---|---|---|
| 热缓存（同窗口 L2 命中） | p95 ≤0.6s，任务开始至快照 UPSERT | 待专用基准验证 | 🔄 |
| 冷缓存（完整端到端） | 独立分阶段 SLO | 外部 HTTP 基线 0.7-0.9s，不能承诺 ≤0.6s | 🔄 |
| 1000 回路小时任务 | ≤600s，任务终态与快照数正确 | 待容量测试验证 | 🔄 |

---

## 6. 下一步建议方向

### 6.1 短期（优先级高，预计可达成 16s 目标）

#### 6.1.1 实施 Beat 提前 5 分钟预热方案

**实施步骤**：

1. **修复 Beat 配置冲突**（[kpi_calc.py:353-356](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L353-L356)）：
   - `_beat_entry` 的 `schedule` 从 `3600.0` 改为 `crontab(minute=0, hour='*')`
   - 修改 `_apply_rules_to_schedule()` 使 `cycle_minutes` 转为 crontab

2. **新增 prewarm-cache Beat 条目**（[kpi_calc.py:360](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L360) 后）：
   ```python
   _existing_beat["prewarm-cache"] = {
       "task": "app.tasks.kpi_calc.prewarm_cache",
       "schedule": crontab(minute=55, hour='*'),
   }
   ```

3. **恢复配置**：
   - `CONCURRENCY` 恢复为 20（[kpi_calc.py:60](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L60)）
   - `worker_ready` 预热恢复（[kpi_calc.py:338-340](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L338-L340)）
   - `_do_calculate` 保持跳过 Phase 1 预热（依赖 Beat 提前预热）

4. **添加兜底机制**：在 `_do_calculate` Phase 2 中检测 L2 缓存命中率，未命中时自动触发预热（避免 Beat 预热失败导致冷启动）

5. **运行 10+ 次 E2E 测试验证**：覆盖冷启动 + 热启动 + Beat 预热失败兜底场景

#### 6.1.2 预热失败告警

- 为 `prewarm_cache` 任务添加 `on_failure` 回调，失败时记录日志并发送通知
- 监控指标：prewarm 成功率、prewarm 耗时、calculate_hourly_kpi 耗时

### 6.2 中期（进一步提升性能与稳定性）

#### 6.2.1 批量 HTTP API 查询优化

**当前**：27 loop × 4 tagGroup = 108 次 HTTP API 请求（每个请求已支持多 tagCodes 批量查询，但按 tagGroup 分组）。

**优化方向**：探索跨 tagGroup 的查询合并，将 108 次请求降到 27 次（每回路 1 次请求获取所有 tagGroup 数据）。

**预期**：预热阶段 HTTP API 调用次数减少 75%，取数耗时从 15s 降到 ~4s。

#### 6.2.2 ProcessPoolExecutor 绕过 GIL

**问题**：纯 Python 预处理（`detect_frozen` O(n×win) 滑动窗口）受 GIL 限制，高并发下单任务从 0.6s 暴增到 12s。

**方案**：使用 `ProcessPoolExecutor` 替代 `asyncio.to_thread`，将预处理移至独立进程。

**风险**：
- 进程间数据序列化开销（DataBlock 较大，可能抵消并行收益）
- 进程池管理复杂度增加

**建议**：先评估 `detect_frozen` 算法优化（如使用 `numpy.lib.stride_tricks.sliding_window_view` 向量化），再考虑 ProcessPoolExecutor。

#### 6.2.3 预处理算法向量化优化

**目标**：将 `detect_frozen` 的 O(n×win) Python 循环改为 numpy 向量化操作。

**当前实现**（[outlier_detection.py:123-131](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L123-L131)）：
```python
for i in range(n - win + 1):
    window = arr[i : i + win]
    std = float(np.std(window[valid_mask]))
    if std < std_threshold:
        for j in range(i, i + win):
            frozen_flags[j] = True
```

**优化方向**：
```python
# 使用 sliding_window_view 向量化
windows = np.lib.stride_tricks.sliding_window_view(arr, win)
stds = np.std(windows, axis=1)  # 一次性计算所有窗口的 std
frozen_mask = stds < std_threshold
# 展开标记到原始点位
```

**预期**：单任务预处理从 0.6s 降到 ~0.1s，高并发下 GIL 竞争显著缓解。

### 6.3 长期（架构演进）

#### 6.3.1 性能监控机制

- 建立计算周期执行时间实时监控（Prometheus 指标 + Grafana 面板）
- 关键指标：prewarm 耗时、calculate 耗时、缓存命中率、HTTP API 响应时间、GIL 竞争指标
- 告警阈值：calculate_hourly_kpi > 10s 告警，prewarm 失败告警

#### 6.3.2 水平扩展能力

- 评估 Celery worker 多实例部署可行性（当前单 worker）
- 评估 Redis 集群部署（当前单 Redis 实例）
- 评估 HTTP API 数据源的多副本负载均衡

#### 6.3.3 缓存策略演进

- 评估 L1/L2 缓存的 TTL 策略是否最优
- 考虑引入 L3 持久化缓存（PostgreSQL 或 TDengine 本地缓存）
- 评估缓存预热任务的资源占用与业务任务的隔离方案（独立队列 + 独立 worker）

---

## 附录 A：关键文件索引

| 文件 | 路径 | 职责 |
|---|---|---|
| KPI 计算核心 | [backend/app/tasks/kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) | Celery 任务定义、两阶段计算、预热机制、Beat 调度 |
| 数据编排器 | [backend/app/services/data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) | 查询计划、缓存查询、HTTP API 调用、预处理编排 |
| L1 缓存 | [backend/app/services/cache/l1_datablock.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py) | DataBlock zstd 压缩 + Redis Pipeline |
| L2 缓存 | [backend/app/services/cache/l2_bundle.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l2_bundle.py) | MetricDataBundle zstd 压缩 |
| HTTP 连接池 | [backend/app/core/tdengine.py](file:///Users/zhangping/DEV/CLPM/backend/app/core/tdengine.py) | httpx.AsyncClient keep-alive 连接池 |
| 预处理管道 | [backend/app/services/preprocessing/pipeline.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/pipeline.py) | 8 步预处理 Pipeline |
| 异常值检测 | [backend/app/services/preprocessing/outlier_detection.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py) | 8 类异常值检测（性能瓶颈所在） |
| Celery 配置 | [backend/app/tasks/celery_app.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/celery_app.py) | Celery 应用实例、Beat 持久化、死信队列 |
| E2E 测试脚本 | [backend/scripts/e2e_perf_test_v2.py](file:///Users/zhangping/DEV/CLPM/backend/scripts/e2e_perf_test_v2.py) | 10 次端到端性能测试 |

## 附录 B：Beat 调度配置现状

| 条目名 | 任务 | 调度 | 冲突风险 |
|---|---|---|---|
| `kpi-calc-hourly` | `calculate_hourly_kpi` | `schedule=3600.0`（秒间隔） | ⚠️ 非固定整点，需改为 crontab |
| `node-kpi-daily` | `calculate_daily_kpi` | `crontab(hour=0, minute=5)` | 无 |
| `node-kpi-monthly` | `calculate_monthly_kpi` | `crontab(hour=0, minute=10, day_of_month=1)` | 无 |
| `audit-archive-daily-3am` | `audit_archive` | `crontab(hour=3, minute=0)` | 无 |
| `prewarm-cache`（待新增） | `prewarm_cache` | `crontab(minute=55, hour='*')` | 无 |

**注意**：`beat_init` 信号会从 DB 读取 `EVAL_CALC_CYCLE.cycle_minutes` 动态覆盖 `kpi-calc-hourly` 的 schedule，修改时需同步处理。

---

## 附录 C：测试命令参考

```bash
# 后端单元测试
cd backend && uv run pytest -q

# E2E 性能测试（10 次）
cd backend && uv run python scripts/e2e_perf_test_v2.py

# 清空 Redis 缓存（冷启动测试前）
docker exec clpm-redis redis-cli FLUSHDB

# 手动触发预热
cd backend && uv run python -c "
from app.tasks.kpi_calc import prewarm_cache
prewarm_cache.apply()

# 手动触发单次计算
curl -X POST http://localhost:7101/api/v1/tasks/standard-evaluation
```

---

**文档维护**：本文档随优化进展持续更新，最新版本位于 `docs/过程文档/kpi-perf-optimization-summary-2026-07-15.md`。
