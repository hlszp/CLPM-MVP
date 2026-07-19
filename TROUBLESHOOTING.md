# CLPM 故障排查状态（2026-07-17 10:35）

> ⚠️ **已解决（2026-07-19）**：本文描述的 KPI 批量回填失败问题已随 PR #72 解决（回填按"1 窗口 = 1 个 chord 子任务"派发 + 写库优化，实测 27 回路 × 24h ≈ 52s，整点任务 27 回路 × 1h ≈ 1.9s）。
> 本文保留作历史排查记录，**不再反映当前系统状态**。当前系统状态与性能口径以 `AGENTS.md` 为准。

> 本文为**交接文档**，供其他 AI 智能体快速了解当前系统状态和未解决问题。
> 分支：`mb/feat-data-architecture-optimization`

---

## 1. 当前系统状态

| 组件 | 端口 | 状态 | 备注 |
|------|------|------|------|
| PostgreSQL | 5432 | ✅ 运行中 | 本地 Docker |
| Redis | 7103 | ✅ 运行中 | 本地 Docker |
| TDengine | 7104/6030 | ✅ 运行中 | 本地原生安装 |
| **后端 FastAPI** | **7101** | **❌ 已停止** | 需重启 |
| **前端 Vite** | **5666** | **❌ 已停止** | 需重启（注意：AGENTS.md 写 7100，实际 .env.development 配置 5666） |
| **Celery Worker** | - | **❌ 已停止** | 需重启 |
| Celery Beat | - | ✅ 运行中 | PID 92617，但无 Worker 消费任务 |

### 系统资源
- **磁盘**：3.1TB 可用，充足
- **内存**：128GB，充足
- **CPU 负载**：9.08（16 核 M3 Max），偏高但可接受

### 网络外连
检测到大量外部 ESTABLISHED 连接（端口 443），但这些来自 IDE（Trae/VSCode）、浏览器、ChatGPT 等应用。
**CLPM 应用本身的外部连接**（`ws://192.168.100.2:81/signalr`）在后端停止后已断开。
后端运行时的正常外连：
- `ws://192.168.100.2:81/signalr/realValueForClpmHub` — SignalR 实时数据订阅（COV 推送）
- `http://192.168.100.2:81` — 历史数据导入 API（仅数据管理模块使用）

---

## 2. 未解决的核心问题：KPI 批量回填任务失败

### 2.1 问题描述
用户在 `/loop/monitor` 页面提交"重算"任务（27 回路 × 24-68 小时窗口），任务持续失败。

### 2.2 错误链与修复历程

| 序号 | Commit | 方案 | 失败原因 |
|------|--------|------|---------|
| 1 | - | asyncio `Semaphore(20)` 串行优化 | 单核 CPU 打满，11 分钟仅完成 19%（27×68 窗口） |
| 2 | `1e87710` | Celery `group` + `group_result.get()` 同步等待 | `RuntimeError: Never call result.get() within a task!`（Celery 禁止在 task 内同步等待子任务） |
| 3 | `f78fa22` | `ProcessPoolExecutor` 多进程 | `AssertionError: daemonic processes are not allowed to have children`（Celery worker 是 daemon 进程，禁止 fork 子进程） |
| 4 | `d662d6c` | Celery `group` + `asyncio.sleep(2)` 异步轮询 + `group_result.get()` 收集 | 68 窗口任务跑到 29% 后失败：同样的 `Never call result.get() within a task!` |
| 5 | `99b1569` | 用 `r.result` 属性替代 `group_result.get()` 收集结果 | **修复了 .get() 错误**，但尚未验证完整流程（系统崩溃前未完成测试） |
| 6 | `f531b55` | 全链路详细日志埋点 | 已提交，Worker 已重启加载，但系统随后崩溃 |

### 2.3 当前代码状态

**关键文件**：[kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py)

**核心架构（Phase 2 — Celery group 并行）**：
```
backfill_kpi_range (Celery task, ForkPoolWorker)
  └─ _do_backfill (asyncio event loop via AsyncTask.run_async)
       ├─ Phase 1: 快速校验回路数
       ├─ Phase 2: Celery group 并行
       │    ├─ job = group(_backfill_window_batch.s(batch) for batch in batches)
       │    ├─ group_result = job.apply_async()  # 非阻塞派发
       │    ├─ while not group_result.ready():   # 异步轮询
       │    │    await asyncio.sleep(2)
       │    └─ for r in group_result.results:    # 用 .result 属性收集（不用 .get()）
       │         batch_result = r.result
       └─ Phase 3: 节点级聚合（统一执行一次）
```

**子任务 `_backfill_window_batch`**：
- 每个 batch 处理 4 个小时窗口
- 每个窗口内：27 回路 → `_run_batch_loop_calculations(bundle_cache=False)`
- `bundle_cache=False` 禁用 L1/L2 缓存（backfill 场景缓存不可复用）

**关键参数**：
- `_BACKFILL_PROCESS_BATCH = 4`（每批 4 窗口）
- Celery `concurrency=16`（prefork），`--max-tasks-per-child=50`
- 16 进程：1 个运行主任务，最多 15 个并行子任务

### 2.4 可能的崩溃原因（待排查）

1. **Celery Worker OOM**：15 个并行子任务 × 每个子任务独立加载 27 回路配置 + TDengine 查询结果，内存可能暴涨
2. **PG 连接池耗尽**：15 个并行子任务各自创建独立 DB session，可能超过 PG `max_connections`
3. **TDengine 连接数过多**：15 个子任务同时查询 TDengine，可能触发连接拒绝
4. **Celery prefork 进程异常退出**：`--max-tasks-per-child=50` 可能在任务执行中触发子进程回收
5. **后端 `--reload` 热重启**：文件保存触发 uvicorn reload，导致后端进程意外终止

---

## 3. 本次会话已完成的工作

### 3.1 数据架构优化（已验证有效）
| 改动 | 文件 | 状态 |
|------|------|------|
| COV 前向填充（query_last_values_before） | `tdengine_provider.py` | ✅ 已验证 |
| 存储端保持 COV 压缩，查询端展开 | `tdengine_provider.py` | ✅ 已验证 |
| 历史数据导入并发优化（5 路并发 + 连接池） | `data_import.py` | ✅ 已提交 |
| 导入任务删除功能 | `loop_data.py` + `data.vue` | ✅ 已提交 |

### 3.2 数据管理页面优化（已验证有效）
| 改动 | 文件 | 状态 |
|------|------|------|
| 左侧回路列表改为 Table（位号/名称列） | `data.vue` | ✅ 已验证 |
| 工厂模型节点树筛选（TreeSelect 多选） | `data.vue` | ✅ 已验证 |
| 分页 10/20/50/100 + 滚动条 | `data.vue` | ✅ 已验证 |
| 导入任务进度条（Progress 组件） | `data.vue` | ✅ 已验证 |
| 导入任务删除按钮 | `data.vue` | ✅ 已验证 |

### 3.3 KPI 批量回填性能优化（部分完成，核心问题未解决）
| 改动 | 文件 | 状态 |
|------|------|------|
| 预加载回路/配置（1 次而非 N 次） | `kpi_calc.py` | ✅ |
| 跳过 L2 缓存检查 + `bundle_cache=False` | `kpi_calc.py` | ✅ |
| 节点聚合改为最后统一执行 | `kpi_calc.py` | ✅ |
| Celery group 多进程并行 | `kpi_calc.py` | ⚠️ 架构正确但稳定性未验证 |
| 全链路详细日志 | `kpi_calc.py` | ✅ |

---

## 4. 重启命令

```bash
# 1. 后端 API (port 7101)
cd /Users/zhangping/DEV/CLPM/backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload >> /tmp/clpm-backend.log 2>&1 &

# 2. Celery Worker
cd /Users/zhangping/DEV/CLPM/backend
.venv/bin/celery -A app.tasks.celery_app worker -l info -Q default --max-tasks-per-child=50

# 3. 前端 (port 5666)
cd /Users/zhangping/DEV/CLPM/frontend
pnpm run dev:antd
```

---

## 5. 建议排查方向

### 5.1 首先恢复服务
重启后端 + Celery Worker + 前端，确认系统恢复。

### 5.2 验证 backfill 修复
提交一个**小规模**重算任务（如 3 回路 × 2 小时），确认 Celery group 并行 + `.result` 收集能正常完成。
如果成功，再逐步扩大规模。

### 5.3 如果仍然失败
检查 Celery Worker 日志中的 `[子任务]` 和 `[Phase2]` 日志，定位失败点：
- 子任务是否被 Worker pickup？（`[子任务] 启动` 日志是否出现）
- 子任务是否成功完成？（`[子任务] 全部完成` 日志）
- 收集阶段是否报错？（`[Phase2] 收集 batchN 失败` 日志）

### 5.4 备选方案
如果 Celery group 在 task 内嵌套仍有问题，可考虑：
- **方案 A**：将 backfill 拆为两层 Celery 任务：父任务提交后立即返回，子任务完成后通过 callback 触发聚合
- **方案 B**：放弃多进程并行，回到 asyncio 单进程但优化单窗口耗时（跨窗口批量预取 TDengine 数据）
- **方案 C**：使用 `billiard.Pool` 替代 `multiprocessing.Pool`（Celery 的 fork 实现，可能绕过 daemon 限制）
