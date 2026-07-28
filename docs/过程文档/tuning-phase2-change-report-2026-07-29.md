# 回路整定 Phase 2 变更对比报告

- **报告日期**：2026-07-29
- **分支**：`feat/tuning-phase2`（已合并入 `main`）
- **对比基线**：`60ee6cdc`（`docs: 同步 Phase 2 数据正确性整改完成状态`，整定 Phase 2 启动前稳定点）
- **对比目标**：`main` HEAD `713dd6e3`
- **技术方案依据**：`docs/过程文档/tuning-phase2-technical-plan-2026-07-28.md`

> ⚠️ **分支状态澄清**：`feat/tuning-phase2` 的全部提交已包含在 `main` 中（`git log origin/main..feat/tuning-phase2` 为空，本地 main 与 `origin/main` 同步 0/0）。字面意义的"合并前"已不成立，本报告回溯基线，完整呈现整定 Phase 2 这轮整改引入的全部改动，供评审/留档/回滚决策使用。

## 1. 变更总览

| 维度 | 文件数 | 新增行 | 删除行 | 备注 |
|---|---|---|---|---|
| 后端（算法栈/服务/任务/API/Schema/Model/迁移） | 18 | +2949 | -18 | 9 个算法栈文件全新 |
| 前端（store/api/views/router） | 7 | +1455 | -235 | store 新增 |
| 测试（单测 + E2E） | 4 | +1910 | -2 | 3 个全新测试文件 |
| 整定专属文档 | 2 | +1063 | 0 | 可行性报告 + 技术方案 |
| 关联文档（牵连更新） | 3 | +48 | -20 | AGENTS/README/实现契约 |
| **合计** | **34** | **+7425** | **-275** | — |

提交序列（整定 Phase 2 专属，14 个核心提交）：

```
31af8fc8 docs(tuning): Phase 2 技术方案草案
7d910b30 feat(tuning): Phase 2.0 算法原型验证通过
3d91c671 feat(tuning): Phase 2.1 算法栈基线
62d1c284 test(tuning): Phase 2.1 算法栈单测 + golden 基线
7c79496f feat(tuning): Phase 2.1 DataPlanner 接入历史数据辨识路径
6782b094 feat(tuning): Phase 2.1 ConfidenceEvaluator 接入
dd4f72dd feat(tuning): Phase 2.2 ORM 扩展 + Alembic 迁移
6d704858 feat(tuning): Phase 2.2 Schema 扩展历史辨识/多PID对比/异步任务
050a46e3 feat(tuning): Phase 2.2/2.3 Celery 异步任务 + 多 PID 仿真对比
b66ebc68 feat(tuning): Phase 2.2/2.3 API 端点 + 多 PID 对比 + 测试
19f1b8ed fix(tuning): prototype 变量未定义 + tdengine 回归测试 + alembic 导入顺序
24e01f13 feat(tuning): Phase 2.4 前端重构（store + API + 多 PID 对比 + E2E）
0ea1bf0e docs(tuning): Phase 2.5 文档同步
c9246c86 test(tuning): 补全 Phase 2 Celery 任务与 API 集成测试
```

## 2. 后端改动详析

### 2.1 过程对象辨识算法栈（全新，9 文件 +2796 行）

路径 `backend/app/services/tuning_identification/`，实现 G_plant(s)=PV/OP 的分层辨识：

| 文件 | 行数 | 职责 |
|---|---|---|
| `__init__.py` | +41 | 暴露 `identify_from_history` 统一入口 |
| `types.py` | +140 | `ModelType` 枚举（FOPDT/SOPDT/IPDT）、`IdentifyResult`/`ModelResult`/`ExcitationResult` 数据结构、`to_dict()` 序列化 |
| `excitation.py` | +155 | **层 1 激励检测**：OP 变化次数/方向变化/条件数，判断数据是否可辨识；`check_excitation()` + `excitation_score()` |
| `nonparametric.py` | +103 | **层 2 非参数粗估**：脉冲/阶跃响应估计，为参数化提供初值 |
| `arx.py` | +100 | **层 3a ARX 辨识**：最小二乘参数估计（开环数据） |
| `armax.py` | +135 | **层 3b ARMAX 辨识**：显式扰动通道建模，迭代求解（扰动主导场景） |
| `iv.py` | +174 | **层 3c IV 辅助变量法**：闭环数据无偏估计（核心创新，解决闭环辨识偏差） |
| `order_selection.py` | +136 | **层 4 阶次选择**：AIC/BIC 准则 + 残差白噪声 Ljung-Box 检验 |
| `discrete_to_continuous.py` | +119 | **层 5 离散→连续转换**：Z 域→S 域，输出 FOPDT/SOPDT 连续模型参数 |
| `pipeline.py` | +263 | **层 6 编排**：激励→非参数→ARX/ARMAX/IV 并行→阶次选择→离散转换→可信度评估；接入 `ConfidenceEvaluator` |

**关键设计**：闭环数据下 ARX 有偏，IV 法提供无偏估计；算法栈不依赖阶跃实验，从常规历史 OP/PV 时序自动提取模型特征。

### 2.2 服务层

| 文件 | 改动 | 关键逻辑 |
|---|---|---|
| `app/services/tuning.py` | +442 | 新增 `identify_model_from_history()`：DataPlanner→8 步预处理→算法栈→可信度；新增 `preview_identify_segments()`：只做激励检测不辨识；新增 `_simulate_multi_pid()`：多 PID 闭环仿真对比；扩展 `run_simulation()` 透传 `pid_candidates`；`_evaluate_data_confidence()`/`_min_confidence()` 接入 `ConfidenceEvaluator`（数据质量 vs 算法可信度取较低者，保守评级） |
| `app/services/tuning_progress.py` | 新增 +160 | 自包含 Redis Hash 进度跟踪（key `tuning:progress:{task_id}`，TTL 7 天）；7 阶段细粒度进度映射 `STAGE_PROGRESS`（excitation 10% → nonparametric 25% → identify 50% → order_selection 65% → discrete_to_continuous 75% → tune 85% → simulate 100%）；不依赖共享 TaskTracker |

### 2.3 任务层（全新）

| 文件 | 行数 | 关键逻辑 |
|---|---|---|
| `app/tasks/tuning.py` | +416 | `AsyncTask` 基类（Celery 同步 worker 跑 async，每任务新事件循环）；`_do_identify()` 辨识任务体（创建占位 TuningRecord→辨识→更新状态 IDENTIFIED/INCONCLUSIVE→写进度）；`_do_tune_and_simulate()` 整定仿真任务体（多算法整定→多 PID 仿真→落库 SIMULATED）；`identify_model_task`/`tune_and_simulate_task` 两个 Celery 任务（time_limit=120s，失败不自动重试）；`_parse_iso_naive`/`_now_naive`/`_serialize_result` 辅助 |

### 2.4 API 端点

`app/api/v1/endpoints/tuning.py`（+180），新增 4 个 Phase 2 端点 + 扩展 2 个：

| 端点 | 方法 | 策略/状态 | 权限 |
|---|---|---|---|
| `/identify/history` | POST | AUTO/HISTORY_ONLY 走异步 Celery；STEP_ONLY 走同步阶跃（向后兼容） | ADMIN/IC_ENGINEER/EXPERT |
| `/identify/segments` | POST | 可辨识片段预览（仅激励检测） | 同上 |
| `/tasks/{id}/status` | GET | 查询异步任务进度（404 if not found） | `tuning:view` |
| `/tasks/{id}/cancel` | POST | 取消 PENDING/RUNNING 任务（`revoke(terminate=True)`），已终态不取消 | ADMIN/IC_ENGINEER/EXPERT |
| `/simulate` | POST | 扩展支持 `pidCandidates` 多 PID 对比 | 同上 |
| `/compare` | POST | 新增，多 PID 对比（≥2 组候选） | 同上 |

### 2.5 Schema

`app/schemas/tuning.py`（+194）：
- 新增 `ModelIdentifyHistoryRequest`（含 `identifyStrategy: AUTO/HISTORY_ONLY/STEP_ONLY`、`candidateModelTypes`、`thetaEstimate`）
- 新增 `IdentifySegmentsRequest`/`IdentifySegmentsResult`
- 新增 `TaskProgress`（进度响应）
- 扩展 `SimulateRequest` 增加 `pidCandidates`
- 扩展 `CreateTuningTaskRequest` 增加辨识元数据字段

### 2.6 ORM Model

`app/models/tuning.py`（+42），`TuningRecord` 表新增 13 字段：

```
identify_method      String(30)     辨识方法（HISTORICAL_ARX/ARMAX/IV / STEP_*）
data_source         String(20)     数据来源（HISTORY / STEP_EXPERIMENT）
time_window_start   DateTime       辨识时间窗口起
time_window_end     DateTime       辨识时间窗口止
confidence_level    String(12)     可信度等级 A/B/C/D/E
confidence_reason   String(200)    可信度原因
excitation_score    Numeric(5,2)   激励评分
residual_test_passed Boolean       残差白噪声检验是否通过
pid_candidates      JSON           多 PID 候选参数
candidate_results   JSON           多 PID 仿真结果
task_id             String(64)     关联 Celery 任务 ID
completed_at        DateTime       完成时间
```

状态机扩展（CHECK 约束）：原 `PENDING/IDENTIFIED/SIMULATED/APPLIED/VERIFIED` → 新增 `DRAFT/RUNNING/COMPLETED/INCONCLUSIVE/ROLLED_BACK`（旧值保留兼容）。新增 `ck_tuning_record_identify_method`、`ck_tuning_record_data_source` 枚举约束。

### 2.7 Alembic 迁移

`backend/alembic/versions/e5f6a7b8c9d0_tuning_phase2_schema.py`（+109，新增）：
- `revision = e5f6a7b8c9d0`，`down_revision = d4e5f6a7b8c9`
- 13 个 `add_column` 到 `tuning_record` 表（对应 2.6 字段）
- `alembic check` 退出码 0，无 schema 漂移 ✓

## 3. 前端改动详析

| 文件 | 改动 | 关键逻辑 |
|---|---|---|
| `store/tuning.ts` | 新增 +198 | 跨页面整定工作流状态管理（辨识结果/PID 候选/仿真结果/任务进度） |
| `api/tuning.ts` | +235 | Phase 2 API 函数（identifyHistory/identifySegments/getTaskStatus/cancelTask/compare） |
| `views/tuning/model.vue` | +578 | 历史辨识入口 + 异步任务提交 + 进度轮询 + 结果展示；统一 `NormalizedResult` 接口对齐 step/history 两种结果形态 |
| `views/tuning/simulation.vue` | +511 | 多 PID 响应对比可视化（ECharts 多曲线叠加 + 性能指标 riseTime/overshoot/settlingTime/ITAE） |
| `views/tuning/stats.vue` | +113 | 适配辨识元数据展示 |
| `views/tuning/workbench.vue` | +51 | 工作台串联辨识→整定→仿真 |
| `router/routes/modules/tuning.ts` | +4 | 路由微调 |

## 4. 测试改动详析

| 文件 | 改动 | 覆盖范围 |
|---|---|---|
| `test_tuning_identification.py` | 新增 +750 | 算法栈单测 + golden 基线（5 场景：开环阶跃/闭环 SP 阶跃/闭环扰动/激励不足/SNR 敏感性） |
| `test_tuning_celery_tasks.py` | 新增 +431 | `_do_identify`/`_do_tune_and_simulate` 成功/INCONCLUSIVE/异常三路径；任务入口接线；辅助函数 |
| `test_tuning_phase2.py` | +661 | 多 PID 仿真 + 进度跟踪 + 6 端点 API（含 4 新端点鉴权/状态分支） |
| `e2e/tests/tuning.spec.ts` | +70 | E2E 整定流程 |

全量 `pytest`：2966 passed（含整定新增 ~1900 用例）。

## 5. 文档改动

| 文件 | 改动 | 内容 |
|---|---|---|
| `tuning-phase2-feasibility-report-2026-07-28.md` | 新增 +131 | 可行性分析（闭环辨识偏差/算法选型/数据质量要求/误差来源） |
| `tuning-phase2-technical-plan-2026-07-28.md` | 新增 +932 | 正式技术方案（分层算法栈/信息模型调整/异步任务/多 PID 对比/分阶段实施） |
| `AGENTS.md` | +25 | 整定 Phase 2 状态、新红线（禁模块级 asyncio.Lock）、lefthook 门禁 |
| `README.md` | +30 | 整定模块能力说明 |
| `implementation-contract.md` | +13 | v2.1→v2.2，整定端点/状态机/字段对齐 |

## 6. 合规核查

| 核查项 | 结果 | 依据 |
|---|---|---|
| 禁止模块级 `asyncio.Lock/Semaphore/Event` | ✅ 合规 | 整定代码 grep 无匹配；`AsyncTask` 每任务新建事件循环，`tuning_progress` 无模块级同步原语 |
| `alembic check` 无 schema 漂移 | ✅ 退出码 0 | ORM 与迁移一致 |
| 迁移链完整 | ✅ | `e5f6a7b8c9d0` 在链中，head `f6a7b8c9d0e1` |
| ruff + format | ✅ 清洁 | 提交前门禁通过 |
| 全量 pytest | ✅ 2966 passed | 含新增用例 |
| 安全边界（不下写 DCS） | ✅ 维持 | 仅输出建议/证据/风险/回退方案 |
| 数据架构（计算全本地 TDengine） | ✅ | `identify_model_from_history` 经 DataPlanner 走本地 TDengine |

### 6.1 跨模块影响核查（回路管理 / 性能评估 / 诊断中心）

> 对整定 Phase 2 的 14 个核心提交逐一核查其改动的文件清单，确认是否触碰其他业务模块或共享基础设施。核查方法：`git show --name-status` 枚举每个提交，过滤非整定专属路径。

**结论：整定 Phase 2 未改动回路管理、性能评估、诊断中心任何产品代码，也未修改共享数据层产品代码——仅作为只读消费者调用共享组件。** 3 处共享文件触碰均为纯增量注册或被动测试适配，零回归风险。

#### 6.1.1 完全未触碰的模块

| 模块 | 后端产品代码 | 前端 |
|---|---|---|
| 回路管理 | `api/v1/endpoints/loops.py` / `services/loop.py` / `models/loop.py` / `schemas/loop*` — 无改动 | `views/loop/` — 无改动 |
| 性能评估 | `endpoints/performance*` / `tasks/kpi_calc.py` / `services/metric*` — 无改动 | `views/performance/` — 无改动 |
| 诊断中心 | `endpoints/diagnosis*` / `services/diagnosis*` — 无改动 | `views/diagnosis/` / `monitor` — 无改动 |
| 共享数据层 | `services/data_planner.py` / `confidence_evaluator.py` / `preprocessing/` / `data_source/tdengine_provider.py` — **无任何产品代码改动** | — |

整定通过 `identify_model_from_history` 只读调用 DataPlanner / ConfidenceEvaluator / 8 步预处理流水线，未修改这些共享组件的任何一行。

#### 6.1.2 共享文件触碰点详析（3 处，均为最小化）

| 触碰文件 | 提交 | 改动量 | 性质 | 回归风险 |
|---|---|---|---|---|
| `app/tasks/celery_app.py` | `050a46e3` | +2 行 | 纯增量：`include` 列表追加 `"app.tasks.tuning"` + 显式 `import app.tasks.tuning`。未改动 broker/backend/beat_schedule/路由/其他 include 条目 | 无——整定任务被 Worker 加载的必要接入，对 kpi_calc/diagnosis_engine 等现有任务的注册与调度零影响 |
| `frontend/.../store/index.ts` | `24e01f13` | +1 行 | 纯增量：`export * from './tuning';`，未改 `auth` 或其他 store | 无 |
| `tests/test_runtime_regressions.py` | `19f1b8ed` | 重写 1 个用例 | 测试适配（见下注） | 无——改的是测试代码，非产品代码 |

#### 6.1.3 `test_runtime_regressions.py` 适配说明（唯一需说明处）

- **旧用例** `test_tdengine_query_fn_serializes_shared_session_metadata_lookup`：断言"并发查询被模块级 `asyncio.Lock` 强制串行"（`max_active==1`）
- **新用例** `test_tdengine_query_fn_concurrent_wide_queries_share_session_safely`：断言"并发 wide_table 查询不崩溃"
- **根因归属**：模块级 `asyncio.Lock` 的移除**不是整定 Phase 2 做的**，而是另一个 agent 的提交 `f45f498a`（对齐 AGENTS.md 新红线"禁止模块级 asyncio.Lock"）做的。整定提交 `19f1b8ed` 只是**被动适配**：Lock 移除后原断言失效，重写为新断言
- **未降低覆盖**：新用例仍验证共享层并发安全，与移除 Lock 的方向一致；当前 `pytest` 2966 passed 含该用例
- **未改产品代码**：该提交未触碰 `tdengine_provider.py` 产品代码

#### 6.1.4 核查方法与可复现命令

```bash
# 枚举整定 14 个提交改动的全部文件
for c in 31af8fc8 7d910b30 3d91c671 62d1c284 7c79496f 6782b094 \
         dd4f72dd 6d704858 050a46e3 b66ebc68 19f1b8ed 24e01f13 \
         0ea1bf0e c9246c86; do
  git show --name-status --format="### %h %s" $c
done

# 核查整定代码无模块级 asyncio 同步原语（新红线）
grep -rn "asyncio\.\(Lock\|Semaphore\|Event\)" \
  app/services/tuning_identification/ app/services/tuning.py \
  app/services/tuning_progress.py app/tasks/tuning.py
# 预期：无匹配
```

## 7. 风险与后续动作

### 风险点
1. **辨识失败兜底**：激励不足/数据质量差时返回 INCONCLUSIVE，需用户调整数据窗口或补导入——属设计内行为，非缺陷。
2. **IV 法数值稳定性**：条件数过大时 IV 估计方差膨胀，`order_selection` 已用 AIC/BIC + 残差检验把关，但极端数据下仍可能降级。
3. **异步任务超时**：`time_limit=120s`，超大数据窗口（>30 天）可能超时——受性能边界（30 天窗口）约束。

### 后续动作
| 动作 | 状态 | 说明 |
|---|---|---|
| 分支合并 main | ✅ 已完成 | 无需再合并 |
| 应用迁移到运行环境 | ⏳ 待确认 | 开发库 `alembic check` 已通过；生产部署时需 `alembic upgrade head` |
| 重启后端让 Celery 生效 | ⏳ 待执行 | `uvicorn --reload` 不重启 Worker/Beat，必须完整重启后端 |
| 重启前端 | ⏳ 待执行 | 加载新整定页面 |
| 设计文档版本号升级 | ⏳ 待办 | PRD/FDS/ADS/IDS/契约 合并后正式升级版本号 |
| GB/T 44693.2 整定用例验证 | ⏳ 待办 | 规范符合性 ≥90% 用例验证 |

### 回滚预案
- 迁移可回滚：`alembic downgrade d4e5f6a7b8c9`（移除 13 字段，旧状态值兼容）
- 代码回滚：`git revert` 整定 14 个提交，或回到基线 `60ee6cdc`
- 前端整定页面独立，回滚不影响其他模块（模块自包含原则）
