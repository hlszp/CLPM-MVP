# PG 连接风暴回归——诊断报告（2026-10-13）

> 诊断智能体（只读排查）产出。范围：diff `5ec4e284..40854489`（已部署 = 批 α/β + P5-01/P5-03）+ worktree `71554d2d`（含未部署 P5-04）。
> 现场环境：dev（PG `clpm-mvp-postgres`，**max_connections=100**；worker **prefork concurrency=4**；worker/uvicorn 均运行 worktree 新代码，`app_name=clpm-api`）。
> 结论置信度分层标注；未做任何修复/重启。

---

## 0. 结论速览

**风暴不是"连接数被正常业务挤爆"，而是一次 session 毒化（asyncpg/SQLAlchemy 并发误用）引发的连接泄漏雪崩：**

1. **[高置信] 毒化首发**：每小时整点批在开跑后 **T+1.0~1.4s** 出现首批 `This session is provisioning a new connection; concurrent operations are not permitted`（SQLAlchemy）与 `cannot perform operation: another operation is in progress`（asyncpg）——同一 AsyncSession/连接被两个协程交叠驱动。**旧代码 07:00/08:00 两窗为 0 次，新代码 09/10/11 三窗各 87/90/96 次**——回归由本次部署引入，且回归点在 KPI 批内每回路 session 链上（热路径改动仅 P5-02 批 β；读链 data_planner/data_source 字节级未变）。
2. **[高置信] 泄漏机制**：被毒化的 session 在异常退出路径上 `rollback()` 与 `async with` 的 `close()` 依次失败（`Method 'close()' can't be called here; method '_connection_for_bind()' is already in progress…`），底层 asyncpg 连接**永不归还**。铁证：
   - worker 日志 09:07:55 集中出现 **200+ 条 `sqlalchemy.pool.impl.NullPool | Exception terminating connection … RuntimeError: Event loop is closed`**；
   - PG 侧 **09:05:01~09:05:14 恰好 100 条 `terminating connection due to idle-in-transaction timeout`**（= 连接创建于 09:00:01~09:00:14，被服务端 5min 超时兜底回收）——100 = max_connections 全占满。
3. **[高置信] 风暴与失败**：100 连接占满后所有新建连接 FATAL（09:00~09:01 **26,434** 次；10:00~10:02 19,682；11:00~11:01 ~22,600——每小时复现）。放大器：每回路"连接耗尽 3 次重试"每次重跑 manifest/元数据独立短会话（~21 次建连/回路）；G29 熔断异常被"task_tracker 回退直算"兜底捕获导致**整批第二次全量重跑**；预警巡检（每分钟一发、失败窗引发 868 条 CONFIDENCE_DEGRADED 后耗时 80~130s，常态 2-3 个巡检实例重叠）。
4. **[高置信] 任务失败链**：~90s 后批量失败率 80%（823/1030）→ `_assert_batch_health` 熔断 → 回退直算再失败 → Celery attempt2/3 在风暴中 **"Retry in 0s"** 紧贴重试（2^1 退避 + full_jitter 抽到 0）→ 终态 FAILED，残留 208/365 行。
5. **[中置信] 毒化精确首发代码行未钉死**：`DataPlanner 取数失败` 只记 message 不记堆栈（日志缺口），静态审未在部署 diff 中找到显式的同 session 双协程交叠点；已排除项与钉死方案见 §3。**修复不必等首发点钉死**——§5 的止血项 1/2 对任意首发点均有效。

**次要三项**（§6）：进度计数器 2109/1030 = 跨 attempt + 回退双跑累加不重置（缺陷）；每 ~6 分钟两个无父 `calculate_node_kpi_hourly` = 失败窗的某条修复/重算链在进程内直调 `_do_calculate` 级联（风暴伴生症状，触发源待钉）；Beat 路径 source 恒落 MANUAL_STANDARD = 判定顺序既有缺陷（非本次引入，登记）。

---

## 1. 时序重建（2026-10-13 09:00 窗，全部有日志/PG 证据）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 08:32/08:33 | worker 重启（新代码 40854489 生效） | γ 批测量；日志中 TDengine/契约缓存冷启 |
| 08:55~09:00 | 巡检已达 129~162s/次、triggered 768~1641 | worker 日志（CONFIDENCE_DEGRADED 大面积触发先于风暴） |
| 09:00:00.004 | `calculate_hourly_kpi` 收包启动（attempt 1，priority=1） | worker L515702 |
| 09:00:00.345 | 批量元信息预载完成（回路 1030；fitness键=0 模板键=0） | worker L515721 |
| 09:00:00.53~.60 | 首批 12 回路契约加载×2 次刷新、L1/L2 全 MISS、LC/TC/PC 回路 5 个 tagGroup 并发回源 | worker L515732+ |
| 09:00:01.118~.122 | TDengine REST 池突发建连 #2~#8（专用线程池 max_workers=8） | worker 日志 |
| **09:00:01.378** | **首个 session 毒化**：`DataPlanner 取数失败（回路 PERF-T0007）: This session is provisioning…`；1ms 后 81a7ded8 的 record 追加同错 | worker L515853 |
| 09:00:01~09:00:14 | 毒化累计 → **100 条连接泄漏为 idle-in-transaction**（其创建时刻 = 终止时刻-300s 反推） | PG 日志 09:05:01~09:05:14 恰 100 条 idle-in-transaction timeout |
| 09:00:32.685 | PG 首个 `FATAL: sorry, too many clients already` | PG 日志 |
| 09:00:32~09:01:26 | FATAL 风暴 26,434 次；每回路 3 次重试 × 独立短会话反复撞墙；巡检大面积"巡检异常"（10:01:04 亦见） | PG/worker |
| 09:01:26.397 | 批量收尾：failed_detail 三类聚齐（close 失败/provisioning/asyncpg InterfaceError） | worker L1057950 |
| 09:01:26.502 | G29 熔断（80%）→ **被"回退到直接计算"兜底捕获 → 整批第二次全量重跑** | worker L1058780 |
| 09:01:26.52/.55 | attempt2/3 背靠背 "Retry in 0s"，2ms 内再失败（TooManyConnections）→ 终态 FAILED | worker L1058870/1059053 |
| 09:05:01~14 | PG 服务端回收 100 条泄漏连接，系统恢复 | PG 日志 |
| 09:07:55 | NullPool 批量报 `Exception terminating connection … Event loop is closed`（ abandoned 连接的延迟终结失败） | worker 日志 200+ 条 |
| 10:00 / 11:00 | 同构复演（首毒化均在 T+1s；FATAL 19,682 / ~22,600） | PG/worker |

**10:00 窗差异**：无 idle-in-transaction 终止（0 条）——该窗占满 100 槽的成分为"已 ROLLBACK 未关闭的 idle 连接 + 毒化中连接 + 巡检/预计算并发"的瞬态组合（现网 11:02 仍可观察到 `ROLLBACK;` 态 idle 泄漏对），机制同源、风味不同；不影响结论。

---

## 2. 为什么是"回归"——A/B 对照（同数据量）

| 窗 | 代码 | 回路数 | 结果 | provisioning 毒化 | FATAL | idle-in-tx 终止 |
|---|---|---|---|---|---|---|
| 07:00 | 旧 5ec4e284 | 1030 | SUCCESS 570.2s | **0** | 0 | 0（全天 00:00-08:59 均 0） |
| 08:00 | 旧 5ec4e284 | 1030 | SUCCESS 580.6s | **0** | 0 | 0 |
| 09:00 | 新 40854489 | 1030 | FAILED 80%（~90s） | 87 | 26,434 | 100 |
| 10:00 | 新 | 1030 | FAILED 65% | 90 | 19,682 | 0 |
| 11:00 | 新 | 1030 | FAILED（同构） | 96 | ~22,600 | — |

基线有效（同为 1030 回路），排除"α 的 1000 回路数据集改变数据量"的解释。

---

## 3. 毒化首发点：已排除项、残留嫌疑与钉死方案

### 已排除（证据）
- **引擎/池配置变化**：`app/core/db.py` 在范围内零 diff（NullPool + command_timeout=60 + idle_in_transaction_session_timeout=300s 均为既有）。
- **读链代码变化**：`data_planner.py`、`data_source/`（含 G14 独立短会话元数据查询、manifest 60s 缓存）、`cache/` 在范围内零 diff；`_calculate_once`/`_run_batch_loop_calculations` 的 session 结构逐行等价（新增仅 P5-02 参数透传 + final_attempt 分支）。
- **wait_for 取消路径**：微复现实验（本机，SELECT pg_sleep）证明 wait_for 超时取消产生的是 `PendingRollbackError` 且 close 正常，**不产生**本事故签名；热路径上 worker_db 相关代码无 wait_for 包装（`_load_manifest_rows` 的 wait_for 只作用于独立短会话）。
- **模块级 asyncio 原语**：部署 diff 中无新增 `asyncio.Lock/Semaphore/Event`；P5-03 single-flight 明确用"当前循环 Future 合并表"避红线。
- **arma/stiction（P5-01）**：纯 numpy 计算，无 DB。
- **fire-and-forget 协程**：alert/kpi 链路无裸 `create_task`。
- **12 并发各自延迟首查的 session**：微复现 12 并发全部干净。

### 残留嫌疑（按优先级）
1. **P5-02 批预载改变了 worker_db 的连接获取时机**（最可能的相关性）：旧码每回路先在 worker_db 上做 fitness LIKE 查询等（连接在回路早期获取并持有）；新码预载后这些查询消失，worker_db 首次建连推迟到契约加载/persist 阶段，与 12 路并发的 TDengine 读取/`to_thread` 预处理/巡检-预计算整点叠加窗交叠。时序证据：首毒化恒在 "TDengine REST 池突发建连 #2~#8" 后 60~90ms（09:00 与 11:00 两窗完全同构）。
2. **attempt 账本独立短会话与批内 session 的交叠**（`start/finish_calculation_attempt`；09:01:26.526 有其失败堆栈，但发生在风暴期，更像受害者而非凶手）。
3. **P5-03 precalc 整点并发**（:00 同时跑、11:00:01.406 "写入 102 行"与首毒化仅 1ms 之差；跨进程 PG 争用可拉长 provisioning 窗口，但单独不构成同 session 并发）。

### 钉死方案（交 REWORK，30 分钟量级）
1. `kpi_calc.py` "DataPlanner 取数失败" 加 `exc_info=True`（当前只记 message——这就是首发点无法钉死的直接原因）。
2. 二分部署：仅回退 β 的 kpi_calc 热路径 hunks（保留 α/P5-01/P5-03）跑一个整点窗。
3. `CONCURRENCY=1` 试跑一个整点窗：毒化消失 → 回路内/回路间并发问题；仍在 → 跨任务/跨进程问题。
4. 临时 `echo_pool=True` + asyncpg `server_settings.application_name` 按回路标记，从 PG 侧看毒化连接的最近语句。

---

## 4. 触发条件与影响面

**触发条件**（每小时整点自动满足）：① 整点 KPI 全量批（1030 回路，sem 12）；② 整点同步任务（precalc :00、巡检每分钟、sweep）；③ worker 重启后首个整点（各类进程内缓存冷）最烈，但 10:00/11:00（缓存已热）同构复演——**缓存冷热不是必要条件**。

**影响面**：
- 09:00 起**每小时整点整批评估失败**（80%/65%/…），09/10 两个窗数据缺口（残留 208/365 行），衍生 868 回路 CONFIDENCE_DEGRADED 预警风暴、巡检 2-3 实例常态重叠；
- PG 在每整点后 ~90s 内拒绝一切新建连接（含 API 侧，`app_name=clpm-api` 同源受害）；
- **每小时泄漏 ~100 连接中，idle（非事务）风味无服务端超时兜底**，靠进程 GC 自愈（不稳定）；
- 巡检链存在**独立的低速率持续泄漏**（见 §6.3），现网 10:57:26 / 10:59:52 / 11:02:26 反复出现 `alert_rule` 查询型 idle-in-transaction 连接对——旧代码时代全天为 0，同为本次部署后出现。

---

## 5. 修法建议（最小改动，按优先级；实施归 REWORK）

1. **[止血-立即] 毒化 session 的关闭兜底**：`_calculate_once`、`_metadata_execute`、巡检 per-loop `async with AsyncSessionLocal()` 的退出路径，对 close/rollback 抛出的 `InvalidRequestError`（provisioning 态）捕获后改用**延迟重试关闭**（`asyncio.create_task` 挂到当前 loop 稍后 close）或直接 `session.sync_session.connection().invalidated` 标记 + 允许 NullPool 丢弃；目标：任何路径下 asyncpg 连接必被终止，**砍断"毒化→泄漏→占满"链条**（对任意首发点均有效）。
2. **[止血-立即] 退避下限**：`calculate_hourly_kpi` 等 autoretry 的 countdown 加下限（≥30s，例如 `retry_backoff=30` 或手动 `self.retry(countdown=max(30, computed))`），消除 "Retry in 0s" 紧贴重试。
3. **[止血-立即] 禁止系统性失败触发"回退直算"全量重跑**：`_body` 的 `except Exception` 需放行 G29 熔断异常（识别 `_assert_batch_health` 的 RuntimeError 或改为专用异常类型），只对真正的 task_tracker 故障回退。
4. **[缓解] 整点错峰**：巡检 Beat 从 `*/1` 放宽到 `*/5`（风暴态下它本就跑不完）；或 KPI 批 CONCURRENCY 12→6 观察。
5. **[修复] §3 钉死首发点后针对性修复**（预期是一处 session 交叠/生命周期错误）。
6. **[修复] 进度重置**：attempt 开始时清零任务进度计数（§6.1）。
7. **[登记] 巡检泄漏、source 判定顺序**（§6.2/6.4）。
8. **[加固-可选] PG max_connections 100→200 或引入 pgbouncer**：不治本，但把"100 泄漏即全站拒连"的爆炸半径缩小。

---

## 6. 次要三项定性

### 6.1 进度计数器跨 attempt 不重置（回路[2109/1030]）——**缺陷（P5-02 attempt 链未带重置）**
2109 = 1030（attempt1 全批完成计数，含失败回路）+ 1030（"回退直算"全量重跑）+ 49（后续推进），进度经任务记录/Redis 累加、attempt 边界与回退边界均不重置。与 §5.3（禁止双跑）联动修复；仅修显示则 attempt 开始时清零。

### 6.2 每 ~6 分钟两个无父 `calculate_node_kpi_hourly`——**风暴伴生症状（缺陷），触发源待钉**
事实：09:52/10:05/10:10/10:17/10:23/10:29/10:36 成对出现，窗口恒为失败的 09:00 窗（01:00-02:00Z），0.5s 内成功（26 节点）；Beat 日志确认**非 Beat 派发**；全仓唯一投递点为 `kpi_calc.py:1338`（`_do_calculate` 级联）而 worker 日志同刻无任何 KPI 任务启动——投递者只能是**进程内直调 `_do_calculate` 的代码**（uvicorn 侧或 lifespan 后台循环中的失败窗修复/重算链，候选：gap backfill 修复、失败窗重算逻辑）。每小时 2 个×幂等聚合，直接危害小；但它是"失败窗被反复重算"的信号，风暴修复后应消失，不消失则单独立案。

### 6.3 巡检链持续泄漏（本次诊断新发现）——**缺陷，登记并交 REWORK**
现网反复出现成对泄漏连接（一为 `SELECT alert_rule…` idle-in-transaction，一为 `BEGIN;`/`ROLLBACK;` idle），每轮巡检 1-2 条，旧代码全天 0。与 KPI 批毒化同签名不同现场（巡检 per-loop session 顺序使用、理论上安全），疑与同一底层诱因相关（或巡检迭代内某 await 被遗弃）。量级小（每分钟 1-2 条、5min 自愈/超时清理），单独不致灾，但佐证"毒化诱因常驻"。

### 6.4 Beat 路径 source 恒落 MANUAL_STANDARD——**既有缺陷（判定顺序），非本次引入，登记**
`_do_hourly_with_tracking` 以 `task_id` 是否存在判定来源，而 Beat 路径先创建 TaskRecord（task_id 恒非空）→ 恒 MANUAL_STANDARD。该行在部署范围内为未改动的上下文行。修法：以触发通道（triggered_by/调用方显式参数）判定，而非 task_id 存在性。

---

## 7. 证据索引（复查命令）

```bash
# FATAL 按分钟分布（09/10/11 三窗）
docker logs clpm-mvp-postgres --since "2026-10-13T08:55:00+08:00" 2>&1 | grep "too many clients" | cut -c1-16 | sort | uniq -c
# 泄漏回收证据（恰好 100 条，且仅 09 时段）
docker logs clpm-mvp-postgres --since "2026-10-13T00:00:00+08:00" 2>&1 | grep "idle-in-transaction timeout" | cut -c1-13 | sort | uniq -c
# 现存泄漏连接（诊断时点可见，含 query 文本）
docker exec clpm-mvp-postgres psql -U clpm -d clpm -x -c "SELECT pid,backend_start,state,query FROM pg_stat_activity WHERE application_name='clpm-api';"
# 毒化时间分布（旧码 0 / 新码每窗 ~90）
grep "provisioning a new connection" backend/logs/celery-worker.log | cut -c12-16 | sort | uniq -c
# 首毒化上下文（09:00 窗）
sed -n '515835,515860p' backend/logs/celery-worker.log
# NullPool 终结失败（Event loop is closed）
grep -B25 "RuntimeError: Event loop is closed" backend/logs/celery-worker.log | grep "NullPool" | head
# "Retry in 0s" 双开始
grep -n "09:01:26,5[0-9][0-9].*KPI 计算任务开始" backend/logs/celery-worker.log
# G29 熔断被回退捕获 + failed_detail 三类聚类
sed -n '1058780,1058790p' backend/logs/celery-worker.log
# 巡检时长/触发数演变（08:55 起恶化）
grep "run_alert_patrol.*succeeded" backend/logs/celery-worker.log | grep -E "2026-10-13 (08:5|09:0)" | sed -E 's/.*(succeeded in [0-9.]+s).*(triggered.: [0-9]+).*/\1 \2/'
```

关键文件（worktree `/Users/zhangping/DEV/CLPM-MVP-p1int`）：`backend/app/tasks/kpi_calc.py`（L2365 取数失败无堆栈；L1058-1075 except 链；L1338 级联）、`backend/app/services/result_ledger.py`（attempt 账本）、`backend/app/core/db.py`（NullPool）、`backend/app/core/tdengine_native.py`（REST 池 + 8 线程执行器）、`backend/app/tasks/alert_patrol.py`（每分钟巡检）。
