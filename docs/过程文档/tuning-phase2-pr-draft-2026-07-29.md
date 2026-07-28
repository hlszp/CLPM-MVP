# 合并请求（PR）草稿 — 回路整定 Phase 2

> **状态说明**：`feat/tuning-phase2` 分支已合并入 `main`（tip `713dd6e3`，本地 main 与 `origin/main` 同步）。本草稿为**评审留档**用途，完整呈现该轮整改的 PR 描述要素，可作为后续类似改动的模板与回溯依据。
>
> - **源分支**：`feat/tuning-phase2`
> - **目标分支**：`main`
> - **对比基线**：`60ee6cdc`（整定 Phase 2 启动前稳定点）
> - **合并后 HEAD**：`713dd6e3`
> - **规模**：34 文件，+7425 / -275
> - **技术方案**：`docs/过程文档/tuning-phase2-technical-plan-2026-07-28.md`
> - **变更对比报告**：`docs/过程文档/tuning-phase2-change-report-2026-07-29.md`

---

## PR 标题

```
feat(tuning): Phase 2 历史数据辨识+异步任务+多PID对比
```

## Summary

- **历史数据过程对象辨识**：基于常规历史 OP/PV 时序自动辨识 G_plant(s)=PV/OP（ARX/ARMAX/**IV 闭环无偏估计**分层算法栈），无需人工阶跃实验即可提取 FOPDT/SOPDT 模型，接入 DataPlanner 8 步预处理 + ConfidenceEvaluator A/B/C/D/E 可信度评级。
- **辨识/整定异步任务化**：新增 Celery 任务 `identify_model_task` / `tune_and_simulate_task`，自包含 Redis 细粒度进度跟踪（7 阶段），前端轮询 + 异步进度条。
- **多 PID 参数闭环仿真对比**：支持多组候选 PID 在同一过程对象上的响应叠加可视化（ECharts 多曲线 + riseTime/overshoot/settlingTime/ITAE 指标），辅助整定决策。

## 动机与背景

回路整定模块原仅支持人工阶跃实验辨识（`identify_fopdt` 两点法），存在两个痛点：
1. **阶跃实验成本高**：需工艺配合、扰动生产、人工记录，难以规模化覆盖企业数百回路。
2. **闭环辨识偏差**：常规历史数据是闭环运行记录，ARX 最小二乘在闭环下有偏，直接用会失真。

Phase 2 引入 IV（辅助变量）法解决闭环无偏估计，配合激励检测/阶次选择/残差检验，使历史数据辨识工程可行；并异步化长耗时任务、支持多 PID 对比，形成"自动辨识→自动整定→多方案对比"闭环。**仍只输出建议、证据、风险和回退方案，不下写 DCS 参数**（安全边界维持）。

## 关键改动说明

### 1. 过程对象辨识算法栈（全新，9 文件）

`backend/app/services/tuning_identification/`，6 层分层辨识：

| 文件 | 层 | 职责 |
|---|---|---|
| `excitation.py` | 1 | 激励检测：OP 变化次数/方向变化/条件数，判断数据是否可辨识 |
| `nonparametric.py` | 2 | 非参数粗估：脉冲/阶跃响应估计，为参数化提供初值 |
| `arx.py` / `armax.py` / `iv.py` | 3 | 参数化辨识：ARX（开环）/ ARMAX（扰动建模）/ **IV（闭环无偏，核心）** |
| `order_selection.py` | 4 | 阶次选择：AIC/BIC + 残差白噪声 Ljung-Box 检验 |
| `discrete_to_continuous.py` | 5 | 离散 Z 域→连续 S 域，输出 FOPDT/SOPDT 参数 |
| `pipeline.py` | 6 | 编排 + 接入 ConfidenceEvaluator 可信度评估 |
| `types.py` | — | ModelType 枚举 + 结果数据结构 |

**为什么**：分层解耦使每层可独立单测，IV 法是闭环辨识工程可行的关键技术突破。

### 2. 服务层

| 文件 | 改动 | 说明 |
|---|---|---|
| `services/tuning.py` | +442 | `identify_model_from_history()`（DataPlanner→预处理→算法栈→可信度）、`preview_identify_segments()`（仅激励检测预览）、`_simulate_multi_pid()`（多 PID 对比）、扩展 `run_simulation()` 透传 `pid_candidates`；`_evaluate_data_confidence()`/`_min_confidence()` 数据质量与算法可信度取较低者（保守评级） |
| `services/tuning_progress.py` | 新增 +160 | 自包含 Redis Hash 进度跟踪（`tuning:progress:{task_id}`，TTL 7 天），7 阶段细粒度进度映射，不依赖共享 TaskTracker |

### 3. Celery 异步任务（全新）

`backend/app/tasks/tuning.py`（+416）：
- `AsyncTask` 基类：Celery 同步 worker 跑 async，**每任务新建事件循环**（遵守"禁模块级 asyncio.Lock"红线）
- `_do_identify()` / `_do_tune_and_simulate()`：任务体（创建占位 record→执行→更新状态 IDENTIFIED/SIMULATED/INCONCLUSIVE→写进度）
- `identify_model_task` / `tune_and_simulate_task`：两个 Celery 任务（time_limit=120s，失败不自动重试→INCONCLUSIVE，需用户调整数据窗口）

`celery_app.py`：`include` 列表 + 显式 import 追加 `app.tasks.tuning`（+2 行纯增量，未改其他配置）。

### 4. API 端点（新增 4 + 扩展 2）

| 端点 | 方法 | 说明 | 权限 |
|---|---|---|---|
| `/identify/history` | POST | **新**：AUTO/HISTORY_ONLY 走异步；STEP_ONLY 走同步阶跃（向后兼容） | ADMIN/IC_ENGINEER/EXPERT |
| `/identify/segments` | POST | **新**：可辨识片段预览（仅激励检测） | 同上 |
| `/tasks/{id}/status` | GET | **新**：异步任务进度查询（404 if not found） | `tuning:view` |
| `/tasks/{id}/cancel` | POST | **新**：取消 PENDING/RUNNING（revoke terminate），已终态不取消 | ADMIN/IC_ENGINEER/EXPERT |
| `/simulate` | POST | 扩展支持 `pidCandidates` 多 PID 对比 | 同上 |
| `/compare` | POST | **新**：多 PID 对比（≥2 组候选） | 同上 |

### 5. Schema / Model / 迁移

- `schemas/tuning.py`（+194）：`ModelIdentifyHistoryRequest`（含 `identifyStrategy`）、`IdentifySegmentsRequest/Result`、`TaskProgress`；扩展 `SimulateRequest`（pidCandidates）、`CreateTuningTaskRequest`（辨识元数据）
- `models/tuning.py`（+42）：`TuningRecord` 新增 **13 字段**（identify_method/data_source/time_window/confidence_level/confidence_reason/excitation_score/residual_test_passed/pid_candidates/candidate_results/task_id/completed_at）；状态机扩展（新增 RUNNING/INCONCLUSIVE/ROLLED_BACK 等，旧值兼容）；新增 identify_method/data_source 枚举 CHECK 约束
- `alembic/versions/e5f6a7b8c9d0_tuning_phase2_schema.py`（+109，新）：13 个 `add_column`，`down_revision=d4e5f6a7b8c9`，`alembic check` 退出码 0 无漂移

### 6. 前端

| 文件 | 改动 | 说明 |
|---|---|---|
| `store/tuning.ts` | 新增 +198 | 跨页面工作流状态（辨识结果/PID 候选/仿真结果/任务进度） |
| `api/tuning.ts` | +235 | Phase 2 API 函数 + TypeScript 类型 |
| `views/tuning/model.vue` | +578 | 辨识策略选择 + 异步进度条 + 可信度徽章 + 片段预览 + 候选模型对比 |
| `views/tuning/simulation.vue` | +511 | 多 PID 对比模式 + 动态候选管理 + ECharts 多曲线叠加 + 性能指标表 |
| `views/tuning/stats.vue` / `workbench.vue` | +164 | 状态机对齐 Phase 2 新枚举 |
| `router/routes/modules/tuning.ts` | +4 | 路由微调 |
| `store/index.ts` | +1 | 纯增量 re-export `./tuning` |

### 7. 测试

| 文件 | 改动 | 覆盖 |
|---|---|---|
| `test_tuning_identification.py` | 新增 +750 | 算法栈单测 + golden 基线（5 场景） |
| `test_tuning_celery_tasks.py` | 新增 +431 | Celery 任务三路径（成功/INCONCLUSIVE/异常）+ 入口接线 |
| `test_tuning_phase2.py` | +661 | 多 PID + 进度 + 6 端点 API（含 4 新端点鉴权/状态分支） |
| `e2e/tests/tuning.spec.ts` | +70 | E2E-TUNE-006/007 |

### 8. 文档

- `tuning-phase2-feasibility-report-2026-07-28.md`（+131）：可行性分析
- `tuning-phase2-technical-plan-2026-07-28.md`（+932）：正式技术方案
- `AGENTS.md` / `README.md` / `implementation-contract.md`（+68）：牵连更新（契约 v2.1→v2.2）

## 跨模块影响声明

**未改动回路管理、性能评估、诊断中心任何产品代码，也未修改共享数据层产品代码**（DataPlanner / ConfidenceEvaluator / 预处理流水线 / TDengine provider）——整定仅作为只读消费者调用共享组件。

3 处共享文件触碰均为最小化（详见变更报告 §6.1）：
- `celery_app.py` +2 行：纯增量任务注册
- `store/index.ts` +1 行：纯增量 re-export
- `test_runtime_regressions.py`：重写 1 用例，被动适配另一 agent（`f45f498a`）移除模块级 asyncio.Lock 后失效的断言，未改产品代码

## 数据库迁移

```bash
cd backend && uv run alembic upgrade head   # 应用 e5f6a7b8c9d0（13 字段）
cd backend && uv run alembic check          # 退出码 0，无漂移
```

回滚：`alembic downgrade d4e5f6a7b8c9`（移除 13 字段，旧状态值兼容）。

## 测试验证

| 项 | 结果 |
|---|---|
| 全量 `pytest` | ✅ 2966 passed（含整定新增 ~1900 用例） |
| `ruff check` + `format --check` | ✅ 清洁 |
| `alembic check` | ✅ 退出码 0 |
| 模块级 asyncio.Lock 核查 | ✅ 整定代码无匹配（合规新红线） |
| 前端 `check:type` | ✅ 通过 |
| E2E | ✅ 含 E2E-TUNE-006/007 |

## 风险与回滚

**风险**：
1. 辨识失败兜底：激励不足/数据质量差→INCONCLUSIVE（设计内行为，需用户调整窗口或补导入）
2. IV 法数值稳定性：条件数过大时方差膨胀，已用 AIC/BIC + 残差检验把关
3. 异步任务超时：`time_limit=120s`，受 30 天窗口性能边界约束

**回滚预案**：
- 迁移：`alembic downgrade d4e5f6a7b8c9`
- 代码：`git revert` 14 个整定提交，或回到基线 `60ee6cdc`
- 前端整定页面独立，回滚不影响其他模块（模块自包含）

## 部署步骤

1. ✅ 分支已合并 main（无需再合并）
2. 应用迁移：`alembic upgrade head`
3. **重启后端**（关键，非 reload）：`uvicorn --reload` 不重启 Worker/Beat，必须完整重启让 Celery 注册新任务
4. 重启前端：加载新整定页面

## Checklist

- [x] 算法栈分层实现 + 单测 + golden 基线
- [x] DataPlanner + ConfidenceEvaluator 接入（只读，未改共享组件）
- [x] Celery 异步任务 + 进度跟踪（无模块级 asyncio.Lock）
- [x] 多 PID 仿真对比 + 前端可视化
- [x] 6 端点 API + 鉴权 + 状态分支测试
- [x] ORM 扩展 + Alembic 迁移（alembic check 通过）
- [x] 跨模块影响核查（未触碰回路管理/性能评估/诊断中心产品代码）
- [x] 全量门禁通过（pytest 2966 / ruff / check:type / E2E）
- [x] 安全边界维持（不下写 DCS，仅输出建议/证据/风险/回退）
- [ ] 设计文档版本号正式升级（PRD/FDS/ADS/IDS/契约）
- [ ] GB/T 44693.2 整定用例验证（≥90%）

## 评审关注点

1. **IV 法正确性**：建议重点 review `iv.py` 的辅助变量构造与闭环无偏性证明（golden 基线已覆盖 5 场景）
2. **保守评级**：`_min_confidence` 取数据质量与算法可信度较低者，确认评级口径符合产品预期
3. **异步任务超时**：`time_limit=120s` 是否覆盖典型数据窗口（30 天 × 1s 采样约 260 万点，DataPlanner 已降采样）
4. **状态机兼容**：新增 RUNNING/INCONCLUSIVE/ROLLED_BACK 与旧 PENDING/APPLIED/VERIFIED 共存，确认前端/报表无硬编码旧枚举
