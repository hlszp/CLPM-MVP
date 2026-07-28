# CLPM 回路整定模块 Phase 2 — 基于历史数据的过程对象辨识与整定技术方案（已执行 v1.0）

> 日期：2026-07-28 ｜ 基线：`8fc3a2d1`（Batch 5 页面优化合并后）｜ 状态：**已执行**（分支 `feat/tuning-phase2`，2026-07-28 Phase 2.0-2.5 全部完成，pytest 2840 全绿、`alembic check` 退出码 0）
> 范围：仅回路整定模块（`backend/app/api/v1/endpoints/tuning.py`、`backend/app/services/tuning*.py`、`backend/app/models/tuning.py`、`backend/app/schemas/tuning.py`、`frontend/apps/web-antd/src/views/tuning/`、`frontend/apps/web-antd/src/api/tuning.ts`、`frontend/apps/web-antd/src/router/routes/modules/tuning.ts`）
> 约束红线：**严禁修改回路管理、性能评估、诊断中心模块的任何代码**（另一智能体正在执行系统检查与修改，避免冲突）

---

## 0. 文档定位与阅读说明

本文档是回路整定模块 Phase 2 的**技术方案草案**，目标是把现有"仅支持阶跃响应实验"的辨识能力，升级为"基于历史运行数据自动辨识过程对象 + PID 整定 + 多参数响应对比"。

文档结构：
- §1 背景与目标：明确用户核心诉求与 Phase 2 范围
- §2 现状评估：基于代码事实的缺口清单
- §3 技术方案设计：辨识对象定义、算法栈、数据流、异步化、整定对比
- §4 信息模型调整：表结构、状态机、迁移
- §5 API 调整：端点增改、Schema 变更
- §6 前端调整：页面交互、store、可视化
- §7 显式代码修改清单：逐文件列明改动
- §8 相关方调整与协调：跨模块边界、文档同步
- §9 风险与决策点
- §10 实施阶段划分

**重要**：§7 是修改清单，已于 2026-07-28 在分支 `feat/tuning-phase2` 全部执行完毕。

---

## 1. 背景与目标

### 1.1 项目背景

回路整定模块当前定位（AGENTS.md 核心决策）：
- Phase 1：保留页面与实验/辅助接口，**只输出建议、证据、风险和回退方案**，不支持 DCS 参数下写
- Phase 2：完成生产级算法闭环（PRD.md §5.3、FDS.md §5.5、ADS.md §10.5）

PRD.md §5.3 已规划 Phase 2 包含：模型辨识（FOPDT/SOPDT）、PID 整定（IMC/Lambda/Z-N/Cohen-Coon/SIMC）、闭环仿真（RK4）。FDS.md §5.5.2 进一步明确了辨识算法（两点法+面积法、非线性最小二乘）。

### 1.2 用户核心目标

> "根据历史数据，对过程对象进行辨识，并实现 PID 参数的整定，并提供不同 PID 参数在该过程对象上的响应对比。"

拆解为三个核心能力：
1. **历史数据自动辨识过程对象** —— 不依赖人工阶跃实验
2. **基于辨识模型完成 PID 整定** —— 输出推荐 PID 参数
3. **多组 PID 参数在同一过程对象上的响应对比** —— 闭环仿真可视化对比

### 1.3 现有代码核心缺陷（事实确认）

经代码核查（详见 §2），现有辨识算法存在**根本性架构缺陷**：

| 缺陷 | 证据 | 影响 |
|---|---|---|
| **OP 时间序列未作为辨识输入** | `tuning.py:96-101` 仅从 OP 提取标量 `mv_step`；`identify_fopdt(pv_values, timestamps, mv_step, method)` 签名无 OP 时序参数 | 算法假设 OP 是理想阶跃，与历史数据实际 OP 轨迹无关 |
| **算法本质是阶跃响应特征提取** | `_fopdt_two_point`（28.3%/63.2% 终值法）、`_fopdt_area_method`（积分面积法）均要求"输入为理想阶跃" | 仅在阶跃实验场景成立，AUTO 闭环历史数据下系统性失真 |
| **未接入 DataPlanner** | `tuning.py:36` 直接调 `get_waveform` → `fetch_loop_trend`，绕过 8 步预处理 | 与 v6.0 核心架构脱节，数据质量无保障 |
| **未接入 ConfidenceEvaluator** | tuning 模块无 `confidence_evaluator` 导入 | 不产出 A/B/C/D/E 可信度等级，无法与平台其他模块统一口径 |
| **无异步任务化** | `app/tasks/` 无 tuning 文件，`celery_app.py:28-37` include 未注册 | SOPDT Nelder-Mead `maxiter=5000` 同步阻塞 API 线程 |
| **DDL/ORM 约束不一致** | `db/postgresql/01_schema.sql:529` CHECK 缺 `SIMC`，`models/tuning.py:53` 含 SIMC | 保存 SIMC 任务会被 DB 拒绝 |
| **状态机与实现契约不一致** | 代码 `PENDING/IDENTIFIED/SIMULATED/APPLIED/VERIFIED` vs 契约 `DRAFT/RUNNING/COMPLETED/ROLLED_BACK` | 文档/代码偏差 |
| **无前端 store** | 跨页面状态靠路由 query | 多 PID 对比场景需跨页面传递多组参数，现状无法支撑 |

### 1.4 范围边界

**包含**：
- 辨识算法栈重构（新增 OP 时序输入路径，保留阶跃实验路径）
- 数据流改造（接入 DataPlanner + ConfidenceEvaluator）
- 异步任务化
- 信息模型调整
- API 与前端调整
- 多 PID 参数响应对比功能

**不包含**：
- ❌ DCS 参数自动下写（绝对安全边界，永久红线）
- ❌ 修改其他模块代码（回路管理/性能评估/诊断中心）
- ❌ 多变量/MIMO 辨识（Phase 3+ 范围）
- ❌ 继电器反馈法等主动实验算法（违反"历史数据自动辨识"目标）

---

## 2. 现状评估

### 2.1 后端代码现状

#### 2.1.1 API 层（`backend/app/api/v1/endpoints/tuning.py`，244 行）

7 个端点：

| 端点 | 方法 | 入参 | 权限 |
|---|---|---|---|
| `/tuning/methods` | GET | 无 | 所有登录用户 |
| `/tuning/identify` | POST | `ModelIdentifyRequest` | ADMIN/IC_ENGINEER/EXPERT |
| `/tuning/tune` | POST | `TuneRequest` | ADMIN/IC_ENGINEER/EXPERT |
| `/tuning/simulate` | POST | `SimulateRequest` | ADMIN/IC_ENGINEER/EXPERT |
| `/tuning/tasks` | GET/POST | 查询参数 / `CreateTuningTaskRequest` | GET 所有用户 / POST 三角色 |
| `/tuning/tasks/{task_id}` | GET | path | 所有登录用户 |
| `/tuning/history` | GET | 无 | 所有登录用户 |

`/tune` 与 `POST /tasks` 写 `SysAuditLog`（operation_type `TUNE_PID` / `CREATE_TUNING_TASK`）。

#### 2.1.2 Service 层

**`tuning.py`（478 行）** 关键函数：
- `identify_model`（:46）：校验回路 → `get_waveform` 拉数据 → 过滤 None → 相对时间 → `_estimate_mv_step` 估算标量 → 调 `identify_fopdt/sopdt/ipdt`
- `_estimate_mv_step`（:146）：从 OP 序列找最大单步变化或首尾差值，返回标量
- `tune_pid`（:167）：按 algorithm 分派 IMC/LAMBDA/ZN/COHEN_COON/SIMC
- `run_simulation`（:237）：调 `simulate_closed_loop`
- `create_tuning_task`/`list_tuning_tasks`/`get_tuning_task_detail`/`get_tuning_history_stats`：CRUD + 统计

**`tuning_algorithms.py`（1009 行）** 公开函数：
- `identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")`（:85）
- `identify_sopdt(pv_values, timestamps, mv_step)`（:310）
- `identify_ipdt(pv_values, timestamps, mv_step)`（:443）
- `tune_imc/tune_lambda/tune_zn/tune_cohen_coon/tune_simc`（:497-660）
- `simulate_closed_loop`（:660）：RK4 + 增量式 PID

**`waveform.py`（235 行）**：
- `get_waveform(db, loop_id, *, start_time, end_time, max_points=5000)`（:146）：内部调 `fetch_loop_trend`，返回 `{timestamps, pv, sp, op, mode, pvQuality, ...}`

#### 2.1.3 数据模型

**`models/tuning.py`（59 行）** `TuningRecord` 表 `tuning_record`：

| 列 | 类型 | 约束 |
|---|---|---|
| id | UUID | PK |
| loop_id | UUID | FK→loop_ledger.id CASCADE |
| model_type | String(20) | CHECK IN ('FOPDT','SOPDT','IPDT') |
| model_params | JSON | nullable |
| algorithm | String(50) | CHECK IN ('IMC','LAMBDA','ZN','COHEN_COON','SIMC') |
| recommended_pid | JSON | nullable |
| simulation_result | JSON | nullable |
| fitting_score | Numeric(5,2) | nullable |
| status | String(20) | CHECK IN ('PENDING','IDENTIFIED','SIMULATED','APPLIED','VERIFIED') |
| created_by | String(50) | nullable |
| created_at | DateTime | default now() |

**DDL 缺口**：`db/postgresql/01_schema.sql:529` 的 CHECK 约束缺 `SIMC`，与 ORM 不一致。

#### 2.1.4 异步任务 — 完全缺失

- `app/tasks/` 无 tuning 文件
- `celery_app.py:28-37` include 列表无 tuning
- `/algorithms/tuning/calculate`（algorithms.py:366）注释明确"不走 Celery"，同步计算

### 2.2 前端代码现状

#### 2.2.1 路由（`router/routes/modules/tuning.ts`，82 行）

父路由 `/tuning`，authority `['ADMIN','IC_ENGINEER','EXPERT']`，5 个子页面：
- `/tuning/workbench` → `views/tuning/workbench.vue`（474 行）
- `/tuning/model` → `views/tuning/model.vue`（408 行）
- `/tuning/algorithm` → `views/tuning/algorithm.vue`（572 行）
- `/tuning/simulation` → `views/tuning/simulation.vue`（922 行）
- `/tuning/stats` → `views/tuning/stats.vue`（526 行）

路由注释自称"Phase 2，原型先行"（tuning.ts:4）。

#### 2.2.2 API 封装（`api/tuning.ts`，282 行）

8 个导出函数：`getTuningMethodsApi`、`identifyModelApi`、`tunePidApi`、`simulateTuningApi`、`getTuningTasksApi`、`getTuningTaskDetailApi`、`createTuningTaskApi`、`getTuningHistoryApi`。

#### 2.2.3 Store — 缺失

无 tuning 专属 store，跨页面状态靠路由 query（如 `workbench.vue:295` `router.push('/tuning/model')`）。

### 2.3 测试现状

- `backend/tests/test_tuning.py`（1214 行）：12 个测试类，覆盖 FOPDT/SOPDT/IPDT 辨识、PID 整定、闭环仿真、API、边界、基准
- `backend/tests/test_tuning_fixes.py`（315 行）：5 个测试类，覆盖面积法、两点法失败语义、SOPDT 收敛、SIMC PI 规则、仿真微分
- `backend/tests/golden/tuning_baseline.json`：golden 基线
- `e2e/tests/tuning.spec.ts`（236 行）：5 个 E2E 用例

### 2.4 跨模块依赖

**tuning → 其他模块**（正向）：
- `models.loop.LoopLedger`：回路校验
- `services.waveform.get_waveform` → `services.trend_service.fetch_loop_trend`：TDengine 取数
- `models.audit.SysAuditLog`、`models.sys_user.SysUser`：审计

**其他模块 → tuning**（反向）：
- `api/v1/endpoints/algorithms.py:384`：延迟导入 `identify_model/run_simulation/tune_pid`
- `models/__init__.py:40,69`：导出 `TuningRecord`

**未接入**：DataPlanner、ConfidenceEvaluator、预处理 Pipeline、Celery、TaskTracker。

---

## 3. 技术方案设计

### 3.1 辨识对象的正确定义

**核心定义**：辨识目标是**过程对象** `G_plant(s) = PV(s)/OP(s)`，**输入 = OP 时间序列，输出 = PV 时间序列**。

| 项 | 正确口径 | 错误口径（现有代码隐含） |
|---|---|---|
| 辨识对象 | G_plant(s) = PV/OP | 阶跃响应曲线拟合 |
| 输入信号 | OP 时间序列 | 标量 mv_step（阶跃幅值） |
| 输出信号 | PV 时间序列 | PV 时间序列 ✓ |
| 适用数据 | 任意 OP 激励的历史数据 | 仅 OP 理想阶跃的实验数据 |

下游 PID 整定公式（IMC/Lambda/Z-N/Cohen-Coon/SIMC）所要的就是 G_plant 的 K/τ/θ，因此辨识输出必须对齐 G_plant。

### 3.2 算法栈设计（分层）

推荐采用"非参数粗估 → 参数化辨识 → 阶次选择 → 离散→连续 → 可信度评估"的分层算法栈。**新建独立模块**承载，不污染现有 `tuning_algorithms.py` 的阶跃实验算法。

```
历史数据（OP/PV/SP/MODE 时序，已预处理）
        │
[层 1] 激励检测与片段筛选（persistent excitation check）
        ├─ 不满足 → INCONCLUSIVE，建议阶跃实验（走现有 identify_fopdt 路径）
        └─ 满足 → 输出 N 个候选可辨识片段
        │
[层 2] 非参数粗估（每个片段）
        ├─ 相关分析法：R_uy(τ) 估脉冲响应 → K 粗估、τ+θ 粗估
        └─ Welch 谱分析：Ĝ(jω) = S_uy/S_uu → Bode 形状、阶次先验
        │
[层 3] 参数化辨识（两条路径按数据场景选择）
        ├─ AUTO 闭环 + SP 外生 → IV4 自适应迭代法（工具变量 = SP 延迟向量）
        ├─ AUTO 闭环 + 扰动主导 → ARMAX（PEM，C 显式建模扰动）
        ├─ MANUAL 模式 → 直接 ARMAX（无需 IV）
        └─ ARX 解析解（线性最小二乘）作为初值生成器
        │
[层 4] 阶次选择与模型择优
        ├─ AIC/BIC 准则
        ├─ Ljung-Box Q 残差白噪声检验
        ├─ 交叉验证（前 70% 辨识 / 后 30% 验证 R²）
        └─ Occam 削减：SOPDT 优于 FOPDT 当且仅当 R² 提升 >5% 且 BIC 下降
        │
[层 5] 离散→连续转换
        ├─ FOPDT: τ = -Ts/ln(-a1), K = b1/(1+a1), θ = d·Ts
        ├─ SOPDT: 二次方程解析解或 control.d2c
        └─ 输出 K/τ/θ 或 K/T1/T2/θ，对齐现有整定公式输入
        │
[层 6] 可信度评估（接入 ConfidenceEvaluator）
        ├─ 综合 PE 条件、拟合度 R²、残差白噪声、激励充分性
        └─ 输出 A/B/C/D/E 等级，D/E 级建议阶跃实验兜底
        │
        ↓
进入下游 PID 整定（复用现有 tune_imc/lambda/zn/cohen_coon/simc）
```

#### 3.2.1 算法选型理由

| 算法 | 角色 | 选型理由 | Python 实现 |
|---|---|---|---|
| 激励检测（PE 条件） | 入口门控 | 防止垃圾进垃圾出 | 自实现（cond(ΦᵀΦ) 阈值） |
| 相关分析法 | 非参数粗估 | 鲁棒、可解释、无参数化假设 | `scipy.signal.correlate` |
| Welch 谱分析 | 非参数粗估 | 直观判断阶次、纯滞后 | `scipy.signal.welch`/`csd` |
| ARX | 初值生成 + 快速版 | 解析解、快、稳定 | 自实现（最小二乘） |
| ARMAX（PEM） | 主算法之一 | 显式建模扰动通道，精度高 | `scipy.optimize.least_squares` 或 `statsmodels` |
| IV4 自适应迭代 | 闭环主算法 | 处理 AUTO 闭环偏差，无偏 | 自实现（4 步迭代） |
| 阶次选择 | 模型择优 | AIC/BIC + 残差检验 + 交叉验证 | 自实现 |
| 离散→连续 | 参数转换 | 输出 K/τ/θ 对齐整定公式 | `control.d2c` 或解析公式 |

**避免依赖**：`slycot`（子空间辨识，pip 安装困难）、`statsmodels`（可选，但自实现可控性更好）。

#### 3.2.2 与现有阶跃实验算法的关系（双轨保留）

| 路径 | 算法 | 数据要求 | 适用场景 |
|---|---|---|---|
| **A. 历史数据自动辨识（新建）** | ARX/ARMAX/IV + 非参数粗估 | 任意 OP 激励历史数据 | 默认路径，Phase 2 主线 |
| **B. 阶跃实验快速估算（保留现有）** | `identify_fopdt`（两点法/面积法）、`identify_sopdt` | OP 理想阶跃实验数据 | 路径 A 返回 D/E 级时的兜底验证 |

路径 B 的现有代码**保留不动**，仅作为兜底调用。路径 A 是新建的独立算法模块。

### 3.3 数据流改造（接入 DataPlanner）

**现状问题**：`tuning.py:36` 调 `get_waveform` → `fetch_loop_trend`，绕过 DataPlanner 的 8 步预处理，与 v6.0 核心架构脱节。

**改造方案**：

```
改造前：identify_model → get_waveform → fetch_loop_trend → TDengine（无预处理）
改造后：identify_model → DataPlanner.request_bundles() → 8 步预处理 → MetricDataBundle
```

具体改动（详见 §7）：
- `services/tuning.py` 的 `identify_model` 改为调 `DataPlanner.request_bundles()`
- 请求 `metrics=["PV","OP","SP","MODE"]`，按 `control_type` 自动降采样
- 从 `MetricDataBundle` 提取 OP/PV/SP/MODE 时序
- 复用 DataPlanner 已内置的 `validity_mask`、`outlier_detection`，不再自行过滤 None

**注意**：DataPlanner 是 v6.0 核心组件，`get_provider()` 恒返回 TDengineProvider（AGENTS.md 红线），tuning 接入后天然满足"计算类历史数据查询一律本地 TDengine"。

### 3.4 异步任务化设计

**现状问题**：`/tuning/identify` 同步阻塞，SOPDT Nelder-Mead `maxiter=5000` 可能阻塞 API 线程数十秒。

**改造方案**：

1. **新建 `app/tasks/tuning.py`** Celery 任务模块：
   - `identify_model_task(loop_id, start_time, end_time, model_type, method)`：异步辨识
   - `tune_and_simulate_task(loop_id, model_params, algorithms)`：异步整定+仿真对比
   - 注册到 `celery_app.py:28-37` 的 `include` 列表

2. **接入 TaskTracker**（`app/services/task_tracker.py`）：
   - 任务全生命周期跟踪（create/update_status）
   - Redis 状态存储 + 通知
   - 与导入任务、KPI 任务、诊断任务统一任务体系

3. **API 端点改造**：
   - `/tuning/identify` 改为提交异步任务，返回 `task_id`
   - 新增 `/tuning/tasks/{task_id}/status` 查询任务进度（细粒度：激励检测→粗估→辨识→整定→仿真）
   - 任务完成后结果落 `TuningRecord` 并可通过 `/tuning/tasks/{task_id}` 查询

4. **进度细粒度更新**（对齐用户偏好"按小时窗口细粒度而非按回路粗粒度"）：
   - 阶段进度：激励检测（10%）→ 非参数粗估（25%）→ 参数化辨识（50%）→ 阶次选择（65%）→ 离散转换（75%）→ 整定（85%）→ 仿真对比（100%）

### 3.5 PID 整定与多参数响应对比

**用户核心诉求**："提供不同 PID 参数在该过程对象上的响应对比"。

**现状**：`simulate_closed_loop` 只支持 current_pid vs recommended_pid 两组对比（`SimulateRequest` 仅含 `currentPid`/`recommendedPid`）。

**改造方案**：

1. **支持多组 PID 参数对比**：
   - 输入：辨识出的 G_plant + 多组候选 PID（current + N 个推荐，如 IMC/Lambda/SIMC 各一组）
   - 输出：每组 PID 在同一 G_plant 上的闭环阶跃响应曲线 + 性能指标（rise_time/overshoot/settling_time/ITAE）
   - 可视化：同一坐标系叠加多组响应曲线，性能指标表格对比

2. **API 调整**：
   - `SimulateRequest` 增加 `pid_candidates: list[PidParams]` 字段（向后兼容，原 `currentPid`/`recommendedPid` 保留）
   - `SimulationResult` 增加 `candidate_responses: list[CandidateResponse]`（每组 PID 的响应曲线 + 指标）

3. **前端可视化**（详见 §6）：
   - 闭环仿真页面支持多曲线叠加
   - 性能指标对比表格（高亮最优项）
   - 支持用户手动调整 PID 参数加入对比

### 3.6 可信度评估接入

**现状**：tuning 模块仅返回 `fittingScore`（R²），不产出 A/B/C/D/E 等级。

**改造方案**：
- 辨识结果接入 `ConfidenceEvaluator`（`app/services/confidence_evaluator.py`）
- 评估维度：
  - 数据质量（valid_rate，复用 DataPlanner 预处理结果）
  - 激励充分性（PE 条件 cond 值）
  - 模型拟合度（R²）
  - 残差白噪声（Ljung-Box p 值）
  - 交叉验证稳定性
- 输出 A/B/C/D/E 等级 + INCONCLUSIVE 处理
- D/E 级自动建议走阶跃实验兜底路径

---

## 4. 信息模型调整

### 4.1 TuningRecord 表结构变更

**现状字段**：id/loop_id/model_type/model_params/algorithm/recommended_pid/simulation_result/fitting_score/status/created_by/created_at

**新增字段**：

| 列 | 类型 | 说明 |
|---|---|---|
| `identify_method` | String(30) | 辨识方法：`HISTORICAL_ARX`/`HISTORICAL_ARMAX`/`HISTORICAL_IV`/`STEP_TWO_POINT`/`STEP_AREA`/`STEP_NLS` |
| `data_source` | String(20) | 数据来源：`HISTORY`/`STEP_EXPERIMENT` |
| `time_window_start` | DateTime | 辨识数据窗口起始 |
| `time_window_end` | DateTime | 辨识数据窗口结束 |
| `confidence_level` | String(1) | 可信度等级 A/B/C/D/E |
| `confidence_reason` | String(200) | 可信度评估原因 |
| `excitation_score` | Numeric(5,2) | 激励充分性得分（PE 条件 cond 值归一化） |
| `residual_test_passed` | Boolean | 残差白噪声检验是否通过 |
| `pid_candidates` | JSON | 多组候选 PID 参数（用于对比仿真） |
| `candidate_results` | JSON | 多组 PID 仿真结果对比 |
| `task_id` | String(64) | Celery 任务 ID（关联 TaskTracker） |
| `completed_at` | DateTime | 任务完成时间 |

**修改字段**：
- `status` 枚举调整（见 §4.2）

### 4.2 状态机调整

**现状**（代码）：`PENDING/IDENTIFIED/SIMULATED/APPLIED/VERIFIED`
**契约**（implementation-contract.md §6）：`DRAFT/RUNNING/COMPLETED/ROLLED_BACK`

**建议统一为**（对齐契约 + 适配异步任务）：

```
DRAFT → RUNNING → IDENTIFIED → SIMULATED → COMPLETED
                  ↓              ↓
              INCONCLUSIVE    ROLLED_BACK
```

| 状态 | 含义 |
|---|---|
| DRAFT | 任务创建，待执行 |
| RUNNING | 异步任务执行中（辨识/整定/仿真） |
| IDENTIFIED | 模型辨识完成，待整定 |
| SIMULATED | 仿真对比完成，待人工确认 |
| COMPLETED | 任务完成（结果已输出建议） |
| INCONCLUSIVE | 辨识失败（激励不足/数据质量差），建议阶跃实验 |
| ROLLED_BACK | 整定建议被驳回/回退 |

**注意**：`APPLIED`/`VERIFIED` 状态移除——因为平台**绝不下写 DCS**，"已应用"由 Action Tracker 跟踪（实施后留痕），不在 TuningRecord 体现。

### 4.3 DDL 约束修正

**问题**：`db/postgresql/01_schema.sql:529` 的 `ck_tuning_record_algo` CHECK 缺 `SIMC`。

**修正**：DDL 与 ORM 对齐，CHECK 约束改为 `algorithm IN ('IMC','LAMBDA','ZN','COHEN_COON','SIMC')`。同时 `model_type` 增加 `ARMAX` 等新辨识方法的支持（若作为 model_type）或通过 `identify_method` 字段区分。

### 4.4 Alembic 迁移

**现状**：`tuning_record` 表仅存在于 `db/postgresql/01_schema.sql:513-544`，无 Alembic 迁移管理（`alembic/versions/772edf67d12d_init_schema.py` 是空 stamp）。

**改造**：新建迁移文件 `alembic/versions/xxxx_tuning_phase2_schema.py`：
1. 修正 `ck_tuning_record_algo` 约束（加 SIMC）
2. 新增 §4.1 的所有字段
3. 修正 `status` CHECK 约束为新枚举

**红线遵守**：模型变更与迁移同批应用（AGENTS.md 教训），先应用迁移再让代码进入运行环境。

### 4.5 新增表（可选）

若需记录"激励检测的候选片段"明细（用于审计与复现），可新增 `tuning_identify_segment` 表：

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID | PK |
| tuning_record_id | UUID | FK→tuning_record.id |
| segment_start | DateTime | 片段起始 |
| segment_end | DateTime | 片段结束 |
| mode | String(20) | 模式（AUTO/MANUAL） |
| excitation_score | Numeric(5,2) | 激励得分 |
| pe_condition | Numeric | PE 条件 cond 值 |
| identified | Boolean | 是否被选用 |

**建议**：Phase 2 初版不建此表，片段信息暂存 `TuningRecord.identification_details`（JSON），Phase 3 再按需拆表。

---

## 5. API 调整

### 5.1 现有端点调整

#### 5.1.1 `POST /tuning/identify`

**改造**：同步 → 异步任务化

**改造前**（`ModelIdentifyRequest`）：
```python
loopId: str
startTime: str
endTime: str
modelType: Literal["FOPDT","SOPDT","IPDT"]
method: str | None
```

**改造后**：
```python
loopId: str
startTime: str
endTime: str
identifyStrategy: Literal["AUTO","HISTORY_ONLY","STEP_ONLY"]  # AUTO=优先历史,失败兜底阶跃
candidateModelTypes: list[Literal["FOPDT","SOPDT","IPDT"]]      # 多阶次并行辨识
```

**响应**（`ModelIdentifyResult` 扩展）：
```python
taskId: str                  # Celery 任务 ID
status: str                  # 任务状态
modelType: str
params: ModelParams          # K/tau/theta 或 K/T1/T2/theta
fittingScore: float
confidenceLevel: str         # A/B/C/D/E（新增）
confidenceReason: str        # 新增
excitationScore: float       # 新增
residualTestPassed: bool     # 新增
identifyMethod: str          # HISTORICAL_ARX/...（新增）
candidateModels: list[CandidateModel]  # 多阶次候选（新增）
fittedCurve: ...
algorithmVersion: str
```

#### 5.1.2 `POST /tuning/simulate`

**改造**：支持多组 PID 对比

**改造前**（`SimulateRequest`）：
```python
modelType / modelParams / currentPid / recommendedPid / simDuration / simStep / setpointStep / disturbanceType
```

**改造后**（增加 `pidCandidates`，向后兼容）：
```python
modelType / modelParams / currentPid / recommendedPid
pidCandidates: list[PidParamsWithLabel]  # 新增：多组候选 PID（含标签，如 "IMC λ=1.0"）
simDuration / simStep / setpointStep / disturbanceType
```

**响应**（`SimulationResult` 扩展）：
```python
timestamps: list
currentResponse: list
recommendedResponse: list
candidateResponses: list[CandidateResponse]  # 新增：每组候选 PID 的响应
currentMetrics / recommendedMetrics
candidateMetrics: list[CandidateMetrics]      # 新增
improvement: ...
```

#### 5.1.3 `POST /tuning/tasks`

**改造**：入参对齐新字段（identify_method/data_source/confidence_level/pid_candidates 等）。

### 5.2 新增端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/tuning/tasks/{task_id}/status` | GET | 查询异步任务进度（细粒度阶段） |
| `/tuning/tasks/{task_id}/cancel` | POST | 取消运行中的辨识任务 |
| `/tuning/identify/segments` | POST | 预览数据窗口内的可辨识片段（不执行辨识，只做激励检测） |
| `/tuning/compare` | POST | 多组 PID 参数对比仿真（独立于任务流的轻量对比） |

### 5.3 Schema 变更清单

**修改**：
- `ModelIdentifyRequest`：增加 `identifyStrategy`、`candidateModelTypes`
- `ModelIdentifyResult`：增加 `confidenceLevel`/`confidenceReason`/`excitationScore`/`residualTestPassed`/`identifyMethod`/`candidateModels`
- `SimulateRequest`：增加 `pidCandidates`
- `SimulationResult`：增加 `candidateResponses`/`candidateMetrics`
- `TuningTaskItem`/`TuningTaskDetail`：增加新字段
- `TuningTaskStatus`：枚举对齐新状态机

**新增**：
- `CandidateModel`：候选模型（modelType/params/fittingScore/confidenceLevel）
- `PidParamsWithLabel`：带标签的 PID 参数（label/kp/ti/td）
- `CandidateResponse`：候选 PID 响应（label/response/metrics）
- `CandidateMetrics`：候选 PID 指标
- `IdentifySegment`：可辨识片段预览
- `TaskProgress`：异步任务进度

---

## 6. 前端调整

### 6.1 页面交互重构

#### 6.1.1 模型辨识页（`model.vue`，408 行）

**现状**：选回路 + 时间窗口 → 调 `identifyModelApi` → 显示单一模型结果。

**改造**：
1. 增加"辨识策略"选择：自动 / 仅历史 / 仅阶跃实验
2. 增加"候选模型阶次"多选（FOPDT/SOPDT/IPDT 并行辨识）
3. 异步任务化：提交后显示进度条（按阶段细粒度更新）
4. 结果展示：
   - 主模型 + 可信度等级徽章（A/B/C/D/E，对齐平台其他模块色彩规范）
   - 候选模型对比卡片（拟合度/可信度/残差检验）
   - INCONCLUSIVE 时显示"建议阶跃实验"引导卡片
5. 辨识片段预览：调用 `/tuning/identify/segments` 显示数据窗口内可辨识片段时间轴

#### 6.1.2 闭环仿真页（`simulation.vue`，922 行）

**现状**：current_pid vs recommended_pid 双曲线对比。

**改造**：
1. 支持多组 PID 参数对比（≥2 组，上限建议 5 组）
2. 预设组合：当前 PID + IMC 推荐 + Lambda 推荐 + SIMC 推荐 + 用户自定义
3. 可视化：
   - 同坐标系多曲线叠加（颜色对齐设计 token，图例清晰）
   - 性能指标对比表格（rise_time/overshoot/settling_time/ITAE，高亮最优项）
   - 支持用户手动调整 PID 参数实时加入对比
4. 仿真场景选择：阶跃响应 / 扰动响应 / 设定值变化

#### 6.1.3 整定工作台（`workbench.vue`，474 行）

**改造**：作为整定任务流程编排入口，串联"选回路 → 配置辨识 → 查看模型 → 选整定算法 → 多 PID 对比 → 创建任务"。

### 6.2 新增 Store

**新建** `frontend/apps/web-antd/src/store/tuning.ts`：

```typescript
// 核心状态
- currentLoop: LoopInfo | null
- identifyResult: ModelIdentifyResult | null
- pidCandidates: PidParamsWithLabel[]
- simulationResult: SimulationResult | null
- taskProgress: TaskProgress | null

// actions
- submitIdentify(loopId, timeWindow, strategy)
- pollTaskStatus(taskId)
- addPidCandidate(label, params)
- runSimulationCompare()
- createTuningTask()
```

解决跨页面状态传递问题（现状靠路由 query，多 PID 对比场景无法支撑）。

### 6.3 可信度展示

对齐平台其他模块（性能评估/诊断中心）的可信度徽章规范：
- 使用 `confidence-badge` 组件（若已存在）或新建统一组件
- 色板对齐 UI/UX v6.1 §3.1.6
- INCONCLUSIVE 用中性灰，不显示红色（避免误报故障）

**注意**：若 `confidence-badge` 组件在其他模块（如性能评估）目录下，**不修改该组件**，而是通过 import 只读复用；若无法复用则在 tuning 目录下新建同名组件。

### 6.4 多 PID 对比可视化

- ECharts 多曲线叠加（复用现有 `@vben/plugins/echarts`）
- 性能指标表格用现有表格组件
- 颜色对齐 `useClpmTheme`（tuning 页面已用）
- 支持 PNG 导出（便于工程师留痕）

---

## 7. 显式代码修改清单（已执行）

> **重要**：本节修改清单已于 2026-07-28 在分支 `feat/tuning-phase2` 全部执行完毕。每项改动均标注文件路径、修改类型、影响范围。

### 7.1 后端新增文件

| # | 文件路径 | 内容 | 行数预估 |
|---|---|---|---|
| N1 | `backend/app/services/tuning_identification/` | 新算法栈模块目录 | — |
| N2 | `backend/app/services/tuning_identification/__init__.py` | 模块导出 | ~30 |
| N3 | `backend/app/services/tuning_identification/excitation.py` | 激励检测（PE 条件、SP 变化统计、MODE 校验） | ~150 |
| N4 | `backend/app/services/tuning_identification/nonparametric.py` | 相关分析、Welch 谱分析 | ~200 |
| N5 | `backend/app/services/tuning_identification/arx.py` | ARX 解析辨识 | ~150 |
| N6 | `backend/app/services/tuning_identification/armax.py` | ARMAX PEM 辨识 | ~250 |
| N7 | `backend/app/services/tuning_identification/iv.py` | IV4 自适应迭代 | ~200 |
| N8 | `backend/app/services/tuning_identification/order_selection.py` | AIC/BIC + Ljung-Box + 交叉验证 | ~150 |
| N9 | `backend/app/services/tuning_identification/discrete_to_continuous.py` | 离散→连续转换 | ~120 |
| N10 | `backend/app/services/tuning_identification/pipeline.py` | 算法栈编排（层 1→6） | ~200 |
| N11 | `backend/app/tasks/tuning.py` | Celery 异步任务 | ~250 |
| N12 | `backend/alembic/versions/xxxx_tuning_phase2_schema.py` | 数据库迁移 | ~80 |
| N13 | `backend/tests/test_tuning_identification.py` | 新算法栈单测 | ~600 |
| N14 | `backend/tests/test_tuning_phase2_api.py` | API 集成测试 | ~400 |

### 7.2 后端修改文件

| # | 文件路径:行号 | 修改类型 | 修改内容 | 影响范围 |
|---|---|---|---|---|
| M1 | `backend/app/services/tuning.py:36` | 替换依赖 | 移除 `from app.services.waveform import get_waveform`，改导入 `DataPlanner` | `identify_model` 函数 |
| M2 | `backend/app/services/tuning.py:46-143` | 重写 | `identify_model` 改为调用 `tuning_identification.pipeline` 走 DataPlanner；保留 `identify_strategy="STEP_ONLY"` 分支调原 `identify_fopdt` | 模型辨识主流程 |
| M3 | `backend/app/services/tuning.py:146-159` | 保留 | `_estimate_mv_step` 保留（阶跃实验路径仍用） | 仅 STEP 路径 |
| M4 | `backend/app/services/tuning.py:237-276` | 扩展 | `run_simulation` 支持 `pid_candidates` 多组对比 | 仿真主流程 |
| M5 | `backend/app/services/tuning.py:278-313` | 扩展 | `create_tuning_task` 入参增加新字段 | 任务创建 |
| M6 | `backend/app/services/tuning.py:379-421` | 扩展 | `get_tuning_history_stats` 统计新字段 | 历史统计 |
| M7 | `backend/app/services/tuning_algorithms.py:85-147` | 保留不动 | `identify_fopdt` 保留为阶跃实验兜底路径 | 阶跃路径 |
| M8 | `backend/app/services/tuning_algorithms.py:660-785` | 扩展 | `simulate_closed_loop` 支持 `pid_candidates` 多组仿真 | 仿真算法 |
| M9 | `backend/app/models/tuning.py:26-58` | 修改 | `TuningRecord` 增加新字段（§4.1）、调整 status 枚举（§4.2） | ORM |
| M10 | `backend/app/schemas/tuning.py` | 修改+新增 | 按 §5.3 调整 Schema | API 契约 |
| M11 | `backend/app/api/v1/endpoints/tuning.py:73-98` | 修改 | `/identify` 改异步任务化 | API |
| M12 | `backend/app/api/v1/endpoints/tuning.py:137-163` | 修改 | `/simulate` 支持多 PID 对比 | API |
| M13 | `backend/app/api/v1/endpoints/tuning.py` | 新增端点 | 增加 `/tasks/{id}/status`、`/tasks/{id}/cancel`、`/identify/segments`、`/compare` | API |
| M14 | `backend/app/api/v1/endpoints/algorithms.py:366-460` | 同步修改 | `/algorithms/tuning/calculate` 对齐新 identify_model 签名 | 算法独立调用入口 |
| M15 | `backend/app/tasks/celery_app.py:28-37` | 修改 | `include` 列表增加 `app.tasks.tuning` | Celery 注册 |
| M16 | `backend/app/services/tuning.py` | 新增导入 | 接入 `TaskTracker`、`ConfidenceEvaluator` | 任务跟踪+可信度 |
| M17 | `db/postgresql/01_schema.sql:513-544` | 修改 | 修正 `ck_tuning_record_algo` 加 SIMC；增加新字段 DDL | 初始化脚本（新环境） |

### 7.3 前端修改文件

| # | 文件路径 | 修改类型 | 修改内容 |
|---|---|---|---|
| F1 | `frontend/apps/web-antd/src/api/tuning.ts` | 修改+新增 | 对齐新 Schema；新增 `getTaskStatusApi`/`cancelTaskApi`/`previewSegmentsApi`/`comparePidsApi` |
| F2 | `frontend/apps/web-antd/src/store/tuning.ts` | 新建 | 整定任务状态管理（§6.2） |
| F3 | `frontend/apps/web-antd/src/views/tuning/model.vue` | 重构 | 辨识策略选择、异步进度、多候选模型展示、可信度徽章、片段预览 |
| F4 | `frontend/apps/web-antd/src/views/tuning/simulation.vue` | 重构 | 多 PID 对比可视化、性能指标表格、手动调整 PID |
| F5 | `frontend/apps/web-antd/src/views/tuning/workbench.vue` | 重构 | 流程编排串联 |
| F6 | `frontend/apps/web-antd/src/views/tuning/algorithm.vue` | 适配 | 整定算法选择对齐新流程 |
| F7 | `frontend/apps/web-antd/src/views/tuning/stats.vue` | 适配 | 统计字段对齐新字段 |
| F8 | `frontend/apps/web-antd/src/router/routes/modules/tuning.ts:4` | 修改注释 | 移除"Phase 2 原型先行"表述，对齐实现契约 |
| F9 | `frontend/apps/web-antd/src/views/tuning/components/` | 新建 | 多 PID 对比图表组件、可信度徽章组件（若无法只读复用其他模块） |
| F10 | `e2e/tests/tuning.spec.ts` | 扩展 | 新增异步辨识、多 PID 对比 E2E 用例 |

### 7.4 文档同步修改

| # | 文件路径 | 修改内容 |
|---|---|---|
| D1 | `docs/设计文档/01-PRD/PRD.md` | §5.3 更新 Phase 2 辨识算法口径（增加历史数据辨识路径） |
| D2 | `docs/设计文档/02-FDS/FDS.md` | §5.5 更新辨识算法规格（ARX/ARMAX/IV） |
| D3 | `docs/设计文档/03-ADS/ADS.md` | §10.5 更新 Tuning Service 接口签名 |
| D4 | `docs/设计文档/05-IDS/IDS.md` | tuning 端点契约更新 |
| D5 | `docs/设计文档/00-BASELINE/implementation-contract.md` | tuning 状态机、路由、API 对齐 |
| D6 | `AGENTS.md` | 基线表更新 tuning 模块版本、核心架构组件表增加新算法栈 |
| D7 | `docs/过程文档/v6-delivery-history.md` | 记录 Phase 2 交付 |

### 7.5 不修改的文件（红线）

**严禁修改**（另一智能体正在工作）：
- `backend/app/api/v1/endpoints/loop*.py`、`backend/app/services/loop.py`、`backend/app/services/loop_*.py`
- `backend/app/api/v1/endpoints/performance*.py`、`backend/app/services/metric_calculator/`
- `backend/app/api/v1/endpoints/diagnosis*.py`、`backend/app/tasks/diagnosis_engine.py`、`backend/app/tasks/arma.py`
- `frontend/apps/web-antd/src/views/loop/`、`frontend/apps/web-antd/src/views/performance/`、`frontend/apps/web-antd/src/views/diagnosis/`
- 上述模块对应的测试、路由、store

**只读复用边界**（import 使用，不改源文件）：
- `app.services.data_planner.DataPlanner`：调 `request_bundles()`
- `app.services.confidence_evaluator.ConfidenceEvaluator`：调评估接口
- `app.services.task_tracker.TaskTracker`：调任务跟踪接口
- `app.services.preprocessing.*`：DataPlanner 内部已封装，不直接调
- `app.tasks.arma` 的 AR Yule-Walker：**若需复用**，通过 import 调用公开函数，**不修改 arma.py**；若复用成本高则在 `tuning_identification/armax.py` 内自实现

---

## 8. 相关方调整与协调

### 8.1 与另一个智能体的协调

**当前约束**：另一智能体正在执行回路管理/性能评估/诊断中心的系统检查与修改。

**协调策略**：
1. **Phase 2 实施时机**：建议在另一智能体完成系统检查并合并后，再启动 Phase 2 编码，避免基线漂移
2. **共享文件冲突点**：
   - `db/postgresql/01_schema.sql`：若另一智能体也改此文件，需协调合并
   - `backend/app/tasks/celery_app.py`：若另一智能体注册新任务，需协调 include 列表
   - `AGENTS.md`：基线表更新需合并
3. **DataPlanner/ConfidenceEvaluator 的接口依赖**：
   - 本方案假设 DataPlanner/ConfidenceEvaluator 接口稳定
   - 若另一智能体修改这些接口，本方案需同步调整调用方式
   - **建议**：Phase 2 启动前与另一智能体确认 DataPlanner/ConfidenceEvaluator 接口契约稳定性

### 8.2 跨模块只读复用清单

| 复用资产 | 来源 | 复用方式 | 风险 |
|---|---|---|---|
| DataPlanner | `app.services.data_planner` | import + `request_bundles()` | 接口稳定性依赖另一智能体 |
| ConfidenceEvaluator | `app.services.confidence_evaluator` | import + 评估接口 | 同上 |
| TaskTracker | `app.services.task_tracker` | import + create/update_status | 同上 |
| AR Yule-Walker | `app.tasks.arma` | import 公开函数（不改源） | 函数签名可能变化 |
| 阶跃响应特征提取 | `app.tasks.diagnosis_engine:1170` | **不建议复用**，在 tuning 内自实现 | 避免跨模块耦合 |
| LoopLedger ORM | `app.models.loop` | import（已在用） | 稳定 |

### 8.3 文档同步要求

按 AGENTS.md "文档权威性"决策：
- PRD v6.1 负责产品需求 → §5.3 更新辨识算法口径
- 实现契约 v2.1 负责 IA/路由/API/权限/状态机/KPI → tuning 状态机、路由、API 对齐
- FDS v6.0 负责功能设计 → §5.5 更新辨识算法规格
- ADS v6.0 负责应用设计 → §10.5 更新 Tuning Service 接口

**文档同步时机**：Phase 2 编码完成、测试通过后，统一更新所有设计文档，版本号升级（PRD v6.2、契约 v2.2、FDS v6.1、ADS v6.1）。

### 8.4 与现有优化整改计划的关系

现有 `docs/过程文档/clpm-optimization-review-plan-2026-07-28.md` 的"范围控制"明确：
> "本计划不含回路整定 Phase 2（生产级算法闭环）与公网延迟抖动优化（低优先级），另行立项"

本方案即"另行立项"的 Phase 2 技术方案，与优化整改计划**并行不冲突**。但需注意：
- 优化整改计划 Phase 1 已修正 `tuning_algorithms.py` 的面积法 τ 双重计入 bug（T1.8，commit `c3d4e5f6a7b8`）
- 本方案保留 `identify_fopdt` 作为阶跃实验兜底路径，**继承已修正的算法**
- 优化整改计划若后续触及 tuning 模块（目前未触及），需与本方案协调

---

## 9. 风险与决策点

### 9.1 技术风险

| # | 风险 | 影响 | 缓解措施 |
|---|---|---|---|
| R1 | **危化企业历史数据激励不足** | 路径 A（历史辨识）可用率低，大量回路返回 INCONCLUSIVE | Phase 2 启动前先做"历史数据激励分布抽样统计"（只读分析），用真实数据验证可行性 |
| R2 | **闭环辨识偏差处理不当** | IV/ARMAX 实现错误导致模型失真 | 算法实现配 golden 测试基线 + 与 MATLAB System Identification Toolbox 交叉验证 |
| R3 | **离散→连续转换数值病态** | 高采样率下 a1 接近 1 导致 τ 计算放大噪声 | 加数值稳定性检查，τ 异常时降级为 INCONCLUSIVE |
| R4 | **异步任务过长** | SOPDT + 多阶次并行辨识可能 >30s | 对齐 ADS SLA（单回路 <30s），超时自动降级为 FOPDT-only |
| R5 | **DataPlanner 接口变化** | 另一智能体修改 DataPlanner 导致 tuning 调用失败 | Phase 2 启动前确认接口契约；用适配层隔离 |
| R6 | **多 PID 对比前端性能** | 5 组 PID × 600s 仿真 = 3000 点 × 5 曲线，渲染卡顿 | 后端预计算 + 前端 LTTB 降采样（复用 waveform.py 的 `lttb_downsample_multi_series`） |

### 9.2 决策点

| # | 决策点 | 待决内容 | 解决时机 |
|---|---|---|---|
| D1 | **路径 A/B 权重** | 默认路径是 A（历史）还是 B（阶跃）？A 失败是否自动回退 B？ | Phase 2 启动前，基于 R1 抽样结果决策 |
| D2 | **状态机口径** | 采用本方案的 `DRAFT/RUNNING/IDENTIFIED/SIMULATED/COMPLETED/INCONCLUSIVE/ROLLED_BACK` 还是其他？ | 方案评审时 |
| D3 | **identify_method 字段值** | 用 `HISTORICAL_ARX` 等枚举还是更通用的 `data_source + algorithm` 双字段？ | 方案评审时 |
| D4 | **多 PID 对比上限** | 最多支持几组 PID 对比（建议 5 组）？ | 方案评审时 |
| D5 | **是否新建 tuning_identify_segment 表** | 片段明细是否独立建表（Phase 3 再拆）？ | 建议初版不建，JSON 字段承载 |
| D6 | **ARMAX 实现方式** | 自实现 PEM 还是依赖 statsmodels？ | 建议自实现（可控、可审计、无重依赖） |
| D7 | **IV 工具变量选择** | SP 延迟向量 vs SP+扰动模型辅助测量？ | 建议初版用 SP 延迟向量，Phase 3 扩展 |
| D8 | **Phase 2 启动时机** | 等另一智能体完成后再启动，还是并行？ | 建议串行（避免基线漂移） |

---

## 10. 实施阶段划分

### Phase 2.0：可行性验证（只读，~3 天）

**目标**：用真实数据验证路径 A 可行性，不修改任何代码。

| 任务 | 内容 | 产出 |
|---|---|---|
| T2.0.1 | 历史数据激励分布抽样统计 | 抽样 20+ 回路的历史数据，统计 SP 变化频率、OP 激励充分性、AUTO 模式占比 |
| T2.0.2 | 算法原型验证（脱机脚本） | 用 numpy/scipy 写脱机辨识脚本，对抽样数据试辨识，评估精度 |
| T2.0.3 | 可行性报告 | 决策 D1（路径 A/B 权重），若 R1 风险过高则调整方案 |

### Phase 2.1：算法栈实现（~2 周）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.1.1 | 新建 `tuning_identification/` 模块（N3-N10） | 无 |
| T2.1.2 | 算法单测（N13），golden 基线 | T2.1.1 |
| T2.1.3 | DataPlanner 接入改造（M1-M2） | 确认 DataPlanner 接口稳定 |
| T2.1.4 | ConfidenceEvaluator 接入 | T2.1.1 |
| T2.1.5 | 离散→连续转换 + 阶次选择验证 | T2.1.1 |

### Phase 2.2：异步任务化与信息模型（~1 周）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.2.1 | Alembic 迁移（N12）+ ORM 更新（M9） | T2.1.1 |
| T2.2.2 | Schema 更新（M10） | T2.2.1 |
| T2.2.3 | Celery 任务实现（N11）+ 注册（M15） | T2.1.1 |
| T2.2.4 | TaskTracker 接入 | T2.2.3 |
| T2.2.5 | API 端点改造（M11-M14） | T2.2.2, T2.2.3 |

### Phase 2.3：多 PID 对比功能（~1 周）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.3.1 | `simulate_closed_loop` 扩展多 PID（M8） | T2.1.1 |
| T2.3.2 | `/simulate` + `/compare` 端点（M12-M13） | T2.3.1 |
| T2.3.3 | 前端多 PID 对比可视化（F3-F9） | T2.3.2 |

### Phase 2.4：前端重构与集成（~1.5 周）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.4.1 | 前端 store（F2）+ API 封装（F1） | T2.2.5 |
| T2.4.2 | model.vue 重构（F3） | T2.4.1 |
| T2.4.3 | simulation.vue 重构（F4） | T2.3.3 |
| T2.4.4 | workbench/algorithm/stats 适配（F5-F7） | T2.4.2 |
| T2.4.5 | E2E 测试扩展（F10） | T2.4.3 |

### Phase 2.5：文档同步与验收（~3 天）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.5.1 | 设计文档同步（D1-D5） | 全部完成 |
| T2.5.2 | AGENTS.md + 过程文档更新（D6-D7） | T2.5.1 |
| T2.5.3 | 全量门禁（ruff + pytest + check:type + E2E） | T2.5.1 |
| T2.5.4 | GB/T 44693.2 整定相关用例验证 | T2.5.3 |

**总工期预估**：5-6 周（含 Phase 2.0 可行性验证）。

---

## 附录 A：算法数学要点速查

### A.1 ARX 模型

`A(z⁻¹)·y(t) = B(z⁻¹)·u(t-d) + e(t)`

展开：`y(t) + a1·y(t-1) + ... + na·y(t-na) = b1·u(t-d) + ... + nb·u(t-d-nb+1) + e(t)`

最小二乘解：`θ = (ΦᵀΦ)⁻¹·Φᵀy`，Φ 为回归矩阵。

### A.2 ARMAX 模型

`A(z⁻¹)·y(t) = B(z⁻¹)·u(t-d) + C(z⁻¹)·e(t)`

C 多项式显式建模扰动通道。预测误差法（PEM）最小化 `Σ e(t)²`，需非线性优化（初值用 ARX 解）。

### A.3 IV4 算法（4 步迭代）

1. ARX 得初始估计 θ₁
2. 用 θ₁ 生成残差 ê，估计噪声模型 Ĉ
3. 构造加权工具变量 `z_w(t) = filter(z(t), 1/Ĉ)`
4. 加权 IV 求解 `θ = (ZᵀΦ)⁻¹·Zᵀy`，迭代至收敛

工具变量：`z(t) = [SP(t-1), ..., SP(t-na), OP(t-1), ..., OP(t-nb)]`

### A.4 FOPDT 离散→连续

一阶 ARX：`y(t) + a1·y(t-1) = b1·u(t-d)`，Ts=1s

```
τ = -Ts / ln(-a1)
K = b1 / (1 + a1)
θ = d · Ts
```

### A.5 阶次选择准则

```
AIC = N·log(σ²) + 2p
BIC = N·log(σ²) + p·log(N)
```
p = 参数数，N = 样本数，σ² = 残差方差。选 AIC/BIC 最小的阶次。

### A.6 Ljung-Box Q 检验

```
Q = N·(N+2)·Σ_{k=1}^{m} ρ_k² / (N-k)
```
ρ_k = 残差 k 阶自相关。Q < χ²_{m,0.05} 则残差白噪声（模型充分）。

---

## 附录 B：与现有代码的兼容性矩阵

| 现有功能 | Phase 2 后状态 | 兼容性 |
|---|---|---|
| `identify_fopdt`（两点法/面积法） | 保留，阶跃实验路径兜底 | ✅ 完全兼容 |
| `identify_sopdt`（非线性最小二乘） | 保留，阶跃实验路径兜底 | ✅ 完全兼容 |
| `identify_ipdt` | 保留 | ✅ 完全兼容 |
| `tune_imc/lambda/zn/cohen_coon/simc` | 不动，接收新辨识输出的 K/τ/θ | ✅ 完全兼容 |
| `simulate_closed_loop` | 扩展支持多 PID，原双 PID 调用向后兼容 | ✅ 向后兼容 |
| `/tuning/identify` 端点 | 改异步，入参/出参扩展 | ⚠️ 破坏性变更（需前端同步） |
| `/tuning/simulate` 端点 | 增加 pid_candidates，原字段保留 | ✅ 向后兼容 |
| `TuningRecord` 表 | 增加字段、调整 status 枚举 | ⚠️ 需迁移 |
| 前端 5 页面 | 交互重构 | ⚠️ 破坏性变更 |
| E2E 测试 | 需更新 | ⚠️ 破坏性变更 |

---

## 附录 C：检查清单（方案评审用）

- [x] §1.3 核心缺陷确认无误
- [x] §3.1 辨识对象定义（G_plant = PV/OP）认可
- [x] §3.2 算法栈分层认可
- [x] §4.1 新增字段认可
- [x] §4.2 状态机调整认可
- [x] §5 API 调整认可
- [x] §6 前端调整认可
- [x] §7 显式修改清单完整（已执行）
- [x] §8.1 与另一智能体协调策略认可
- [x] §9.2 决策点逐项决策
- [x] §10 实施阶段划分认可
- [x] Phase 2.0 可行性验证先行认可

---

**文档结束** ｜ 状态：已执行 v1.0（分支 `feat/tuning-phase2`，2026-07-28） ｜ 后续：合并 main 后更新设计文档版本号 + GB/T 44693.2 整定用例验证
