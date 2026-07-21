# 回路管理 + 性能评估整改通盘执行计划（2026-07-21）

> **Goal:** 将两份整改计划（回路管理 10 阶段、性能评估体检 14 项）合并为一套可多代理团队并行推进的执行方案，消除文件级冲突与口径冲突，按波次合入 main。
> **Architecture:** 以「文件所有权」划分 7 个工作流（WS-A~G），按依赖关系编排 4 个波次（Wave 0-3）；每个 WS 独立分支、独立 PR，合并门禁统一为 pytest + ruff + check:type（+ 数值三方核对）。
> **Tech Stack:** FastAPI + Celery + Redis + TDengine + PG（后端）；Vue3 + vben-admin（前端）；gitea API PR 流程。
> **源文档（技术细节不在本文重复，逐项映射见 §8）：**
> - 回路：`loop-management-module-review-plan-2026-07-21.md`（审查证据 file:line）、`loop-management-10phase-execution-plan-2026-07-21.md`（阶段划分 + D1-D6 决策）
> - 性能：`metric-module-health-check-plan-2026-07-21.md`（#1-#14 根因与修复思路）

---

## 1. 现状基线（2026-07-21 核实）

### 1.1 分支与工作区

- `main` = `origin/main` = `b0cc1560`（两份计划文档已入库）。
- 当前检出分支 `zp/fix-metric-p0-data-integrity`，**无领先 commit**，全部改动未提交，工作区自洽但混合两批工作：

| 改动组 | 文件 | 对应计划项 | 状态 |
|---|---|---|---|
| 回路 P0 修复 | `services/loop_batch.py`、`services/loop.py`、`tests/test_loop_batch.py`、`tests/test_loop.py` | 回路 阶段 1 | 已完成并验收（2056 passed / 1 skipped） |
| 孤儿页下线半成品 | `frontend/.../components/loop/quality-tag.vue`（Quality 类型已内联） | 回路 阶段 2 | 半成品；待删 `views/loop/tag-mapping.vue`、`api/aas.ts`，待改 `monitor.vue:869` 死链 |
| 性能 P0 #1 根因修复 | `tasks/kpi_calc.py`、`services/task_tracker.py`（V2 Lua：`work_items_total/done` 独立字段）、`endpoints/tasks.py`（移除展示层还原补丁，改按 progress 折算 loopsDone）、`schemas/task.py`、`tests/test_api_tasks.py`、`tests/tasks/test_backfill_orchestration.py` | 性能 #1 | 代码已完成，**尚未跑全量测试验收** |

- 残留 `worktree-agent-*` 分支 ×5（历史 agent 工作树，无合入价值）→ Wave 0 清理。

### 1.2 已闭环、不再列入的项

性能侧：PR #81/#82/#84/#79/#80/#83/#85/#90、A/B 对比接口、理想稳态时间/异常参数/可信度最新表、取消确认弹框统一。回路侧：阶段 1（待提交入库即闭环）。

### 1.3 运行环境

后端 7101 / 前端 5666 / worker 运行中；`--reload` 已热载后端改动；**worker 未重启**，`kpi_calc.py` 改动对 worker 未生效（提交合并后需重启 worker 验证回填进度显示）。

---

## 2. 整改总目标

1. **数据正确性**：批量链路/导入不再 500；回填 `loops_total` 恒为回路数；断点续传失败不丢缺口、可重试、可观测；INCONCLUSIVE 快照可信度落 'E'。
2. **契约一致性**：回路监控/配置/测点/链路四个子模块前后端契约对齐；权限矩阵前后端统一口径（决策门 R1）。
3. **韧性**：实时链路停滞可检测、补数可重试不压垮远端；导入任务生命周期可控（取消/超时清扫/TTL）。
4. **文档口径落地**：PRD v6.0→v6.1 落地 D1-D6 + 权重口径裁决（R2），全库版本口径统一。

## 3. 依赖与冲突分析

### 3.1 数据流与模块关联

```
SignalR Hub ──► realtime_subscriber ──► TDengine ──► kpi_calc（本地计算）──► PG 快照 ──► monitor/board API ──► 前端
                    │ gap backfill ──► data_import（conflict=skip 红线）          ▲
远端 History API ◄──┴── data_import（手工导入，唯一远端调用方）                     │
task_tracker(Redis) ◄── kpi_calc / data_import / 【新增】auto-backfill 登记（共用同一任务态存储）
```

关联要点：
- `task_tracker.py` 是性能 #1 与回路 阶段 4（补数登记）的**共同依赖** → 必须 WS-A 先落地，WS-B 在其上加登记逻辑。
- `kpi_calc.py` 归性能侧独占（#1 在树、#3 待做）；回路 阶段 4 仅作为 `backfill_kpi_range` 调用方，不改其内部。
- gap backfill 复用 `import_history_data` 必须 `conflict_strategy="skip"`（AGENTS.md 红线），WS-B 改动不得破坏。

### 3.2 文件所有权冲突矩阵（多分支并行的核心约束）

| 文件 | 改动来源 | 归属 WS | 冲突解决 |
|---|---|---|---|
| `services/monitor.py` | 回路 阶段 5（契约）+ 阶段 10（DISTINCT ON/CTE） | **WS-D → WS-G** | 严格串行：D 合并后 G 才动 |
| `views/loop/monitor.vue` | 回路 阶段 2（:869 死链）+ 阶段 5 + 性能 #7（SPONSOR 门控） | **WS-D 独占** | 三项合并为一次改动，消除原计划最大冲突点 |
| `services/task_tracker.py` | 性能 #1（在树 V2）+ 回路 阶段 4（补数任务登记） | **WS-A → WS-B** | A 先合并，B rebase 后追加 |
| `tasks/kpi_calc.py` | 性能 #1/#3 | **WS-A 独占** | 回路侧禁止改 |
| `services/loop.py` | 阶段 1（在树）→ 阶段 6（mode 映射/级联删除）→ 阶段 10（CTE） | PR-L1 → **WS-C → WS-G** | 按波次串行 |
| `realtime_subscriber.py`、`data_import.py`、`data_link_monitor.py`、`remote_api_provider*` | 回路 阶段 3/4/9 | **WS-B 独占** | 阶段 4（B1）与阶段 9 订阅器项（B2）同团队顺序做 |
| `schemas/loop.py`、`services/tag*.py`、`api/tag.ts`、manage.vue、tag 页 | 回路 阶段 6/7 | **WS-C 独占** | — |
| `datasource_config.py`、`aas.vue`、网络模式回滚 | 回路 阶段 8 | **WS-E 独占** | — |
| `board/aggregate`、`auto-rate-rt`、`/performance/rules`、metric 前端页 | 性能 #5/#6/#8/#9/#10 | **WS-F 独占** | #7 涉及的诊断/回路性能页不归 F |
| PRD/契约/FDS/AGENTS.md | 回路 阶段 10（D1-D6）+ 性能 #2 | **WS-G 独占** | 最后一批，等 R1/R2 裁决 |
| `endpoints/loops.py`（权限）、诊断详情类端点（require_roles） | 回路 阶段 6 权限对齐 + 性能 #7 后端防线 | **WS-D**（权限统一实施） | C 不动权限行 |

### 3.3 口径冲突（需决策门裁决，见 §7）

1. **权限矩阵**：性能 #7 收紧 SPONSOR vs 回路 D3 维持现状 + 阶段 6 放开 PE_ENGINEER 入口 → R1 统一裁决后由 WS-D 一处实施。
2. **权重权威源**：契约说类型模板 vs 代码 MetricConfig.weight 优先（30/20/30）→ R2 裁决，WS-G 改文档 + 一次性数据初始化。
3. **文档版本**：PRD 引"契约 v2.1" vs 基线自称 v2.0（D5：以 v2.0 为准）→ WS-G。

## 4. 多分支并行方案（7 个工作流）

> 每个 WS 由独立代理团队承担，独立 worktree 开发；任务技术细节（file:line、修复方向）以源文档为准，本表只做映射与排序。

| WS | 分支名 | 内容（源文档锚点） | 依赖 | 波次 |
|---|---|---|---|---|
| **PR-L1** | `zp/fix-loop-p0-batch-crash` | 回路 阶段 1（在树 4 文件）+ 阶段 2 收尾：删 `tag-mapping.vue`/`api/aas.ts`（不含 monitor.vue:869） | 无 | **W0** |
| **WS-A** | `zp/fix-metric-p0-data-integrity`（现分支） | PR-A1 = 性能 #1（在树 6 文件）；PR-A2 = #3（INCONCLUSIVE 落 'E'）+ #4（重复任务清理脚本 `backend/scripts/`，先 dry-run） | 无；**先于 WS-B 合并** | W0→W1 |
| **WS-B** | `zp/feat-loop-datalink-hardening` | B1=回路 阶段 4（checkpoint 条件推进/重试退避/任务登记接 WS-A 的 task_tracker V2/共享熔断限流）；B2=阶段 3（时区实测，受阻可顺延）+ 阶段 9（看门狗/落库 checkpoint 分离/WS 参数 30-60-15/data_link_monitor 接 beat/导入任务生命周期/SETNX 分布式锁） | **PR-A1 合并后启动** | W1(B1)→W2(B2) |
| **WS-C** | `zp/fix-loop-config-contracts` | 回路 阶段 6（unitId 落库/extra=forbid 评估/mode 映射回退链/删回路先解绑 7 Tag/PID 只读区）+ 阶段 7（枚举对齐/TagUpdate 参数类型/unitName/is_linked 派生/导入忽略启用列/AAS 不回冲描述）；**不动权限行** | PR-L1 合并 | W1 |
| **WS-D** | `zp/feat-monitor-contract-permission` | 回路 阶段 5（loopStatus/kpiStatus 拆分/unit/去 GOOD/last_7_days/is_active 口径/非法 trendWindow 400/WS MODE 映射统一）+ 性能 #7（SPONSOR 门控前后端）+ monitor.vue:869 死链改跳 `/loop/manage` + R1 权限矩阵落地 | **R1 裁决**；建议 W1 准备、W2 实施 | W2 |
| **WS-E** | `zp/fix-datasource-config-security` | 回路 阶段 8（Token 打码/不填即不变/订阅器真实状态/_cast_value 容错/合并 IN 查询/可清空字段/测试连接先存提示/网络模式二次确认/Tailscale 回滚） | 无 | W1 |
| **WS-F** | `zp/feat-metric-p1-ux` | 性能 #5（aggregate timeWindow）/#6（readAt 过期提示）/#8（rules 裸数组，R3）/#9（注释）/#10（confidence-badge 收敛试点 loop-performance，失败则删，R4） | 无 | W1 |
| **WS-G** | `zp/perf-docs-final` | 回路 阶段 10（性能：DISTINCT ON/CTE/GROUP BY 计数/合并 IN/stats 缓存；UX 包）+ 文档 D1-D6 落地 + 性能 #2 文档与初始化（R2）+ #11（时间工具）/#12（ranking 全量分页）/#14（e2e 补盲）；#13 生产部署走查作为上线 gate | **WS-D 合并** + R2 裁决 | W3 |

团队建议（可按实际代理数调整）：团队甲=WS-A→WS-F；团队乙=WS-B；团队丙=PR-L1→WS-C；团队丁=WS-E→WS-D→WS-G。

## 5. 波次编排与目标节点

| 波次 | 内容 | 启动条件 | 目标合并节点 |
|---|---|---|---|
| **W0** | PR-L1 + PR-A1 拆分提交、跑门禁、合并；清理 5 个 worktree-agent 残留分支；worker 重启验证回填进度 | 立即（本日） | 2026-07-21 |
| **W1** | WS-B1、WS-C、WS-E、WS-F、PR-A2 并行 | PR-A1 已合并（WS-B 硬依赖）；其余 W0 合并后 | 2026-07-24 |
| **W2** | WS-D（待 R1）、WS-B2 | R1 裁决；B1 合并 | 2026-07-27 |
| **W3** | WS-G + #13 部署走查 | WS-D 合并 + R2 裁决 | 2026-07-29 |

节点为协调目标而非承诺；阶段 3 时区实测若受远端环境限制按源文档既定策略顺延，不阻塞其他项。

> **W1 执行状态（2026-07-21 回写，终态）**：PR-A2（#3）、WS-E（#4）、WS-F（#5）、WS-B1（#6）、WS-C（#7，aaa24790）**全部 5 项合并完成**并同步 github 镜像；合并后门禁全绿（ruff 零告警 / pytest 2118 passed，较基线 +62 / check:type 零错误）。WS-C 由续作代理+主线验收完成（首代理未产出）。运行时验证待办：性能-3 PG 抽查、性能-4 清理脚本 dry-run/执行、回路-4 worker 重启加载新码（随 W1 合并统一重启）。

## 6. 分支合并校验标准与流程

### 6.1 PR 门禁（逐项打勾，缺一不合）

1. `cd backend && uv run pytest -q` 全绿（基线 2056 passed / 1 skipped，只允许新增 passed）。
2. `uv run ruff check . && uv run ruff format --check .` 零告警。
3. 涉前端：`cd frontend && pnpm run check:type` 零错误（+ `pnpm run format`）。
4. 数值类改动（KPI/进度/权重/可信度）：PG 复算 vs API 响应 vs 页面展示三方核对，结果贴 PR 描述。
5. 模型变更与 alembic 迁移同批，且先应用迁移再部署代码。
6. 核心路径（阶段 4 补数、阶段 5 监控、#7 门控）补/更新 e2e 用例。
7. PR 描述含「验收对照表」：本 PR 关闭 §8 总表中哪些行、每行验收证据。
8. commit 遵循 Conventional Commits，单 commit ≤500 行，按逻辑单元拆分。

### 6.2 流程

1. 从最新 `main` 切分支；跨 WS 改他人所有文件**必须先修订本计划 §3.2 矩阵**。
2. 小步提交、本地门禁全绿 → push → 对话中显式提出后经 gitea API 开 PR。
3. 合并前 rebase 到最新 main（禁止 force-push 共享分支）；合并后 `git push github main` 同步镜像、删分支。
4. 合并顺序硬约束：**PR-A1 → WS-B**；**WS-D → WS-G**；PR-L1/WS-A 与其余互不阻塞。
5. 每个 WS 合并后立即在 §8 总表勾选并更新源文档状态标注。

## 7. 决策门（阻塞项，需用户拍板）

> **裁决结果（2026-07-21 用户拍板）：R1 全套采纳建议口径；R2 采纳 metric_config 为唯一入口；R3/R4 按建议执行。**

| # | 议题 | 裁决口径 | 阻塞 |
|---|---|---|---|
| **R1** ✅ | 权限矩阵统一：①SPONSOR 只读工作台、禁下钻诊断/详情（前后端双防线）；②PE_ENGINEER 放开回路配置入口（对齐后端现状）；③数据管理维持允许导入/删除，契约标注"待统一完善" | **全套采纳 ①②③** | WS-D（已解锁） |
| **R2** ✅ | 权重权威源：`metric_config`（权重配置管理页）为唯一用户入口；控制类型模板降级为出厂默认，一次性写入 metric_config；FDS/算法说明对齐；`kpi_calc` 优先级链（MetricConfig > LoopTypeWeight > None）代码不变 | **采纳** | WS-G（已解锁） |
| **R3** | `/performance/rules` 契约统一为裸数组，改 `api/metric.ts` 类型、删前端兼容分支 | 按建议采纳 | WS-F |
| **R4** | `confidence-badge.vue` 收敛试点（loop-performance 可信度列先替换），渲染一致后推广；试点失败则删除组件 | 按建议采纳 | WS-F |

## 8. 总验收对照表（24 项映射）

| 项 | 内容 | WS/PR | 波次 | 验收标准 |
|---|---|---|---|---|
| 回路-1 ✅ | P0 批量/导入崩溃 | PR-L1（gitea PR #1，已合并 4e37b7a7） | W0 | 已验收（2056 passed）；回归测试在库 |
| 回路-2 🔶 | 孤儿页下线 | PR-L1 删页+死API ✅（PR #1）/ WS-D（:869 死链，待做） | W0/W2 | check:type 无残留引用；监控页跳转可达 |
| 回路-3 | 时区口径 | WS-B2 | W2 | 远端实测比对记录；导入段与实时段 ts 连续抽查一致 |
| 回路-4 ✅ | 断点续传加固 | WS-B1（PR #6，已合并 4aae0389） | W1 | 新单测：部分失败不推进/重试定时器/任务记录 ✅（PR 内含）；补数进任务列表且标记 auto-backfill ✅；**待 worker 重启加载新码** |
| 回路-5 | 监控契约对齐 | WS-D | W2 | 接口级测试 + check:type；loopStatus/kpiStatus 分离，无 PARTIAL 撞名 |
| 回路-6 ✅ | 回路配置契约 | WS-C（PR #7，已合并 aaa24790）/ WS-D（权限） | W1/W2 | unitId 更新落库 ✅；删回路级联解绑 ✅；PID 只读区 ✅；extra=forbid 已启用 |
| 回路-7 ✅ | 测点配置契约 | WS-C（PR #7） | W1 | 枚举对齐（PID_P/I/D、SPEED）✅；TagUpdate 参数类型 ✅；unitName ✅；is_linked 仅映射派生（导入忽略启用列 + 解关联查引用）✅；AAS 不回冲描述 ✅ |
| 回路-8 ✅ | 链路配置安全 | WS-E（PR #4，已合并 a6822d85） | W1 | 审计/GET Token 打码 ✅；Tailscale 失败回滚 sys_config ✅；可清空字段 ✅（405 行新单测在库） |
| 回路-9 | 韧性增强包 | WS-B2 | W2 | 看门狗模拟停滞触发重连；RUNNING 超时清扫生效；任务 TTL 30 天 |
| 回路-10 | 性能 UX + 文档 | WS-G | W3 | DISTINCT ON/CTE 查询计划验证；PRD v6.1 发布，D1-D6 全落地 |
| 性能-1 ✅ | loops_total 根因 | PR-A1（gitea PR #2，已合并 f62e2275；worker 已重启加载新码） | W0 | 单测回填后 loops_total=回路数；e2e F7 断言保持 |
| 性能-2 | 权重口径裁决 | WS-G | W3 | 指定回路改权重→回填分数按新权重；文档 grep 无旧口径 |
| 性能-3 🔶 | INCONCLUSIVE 落 'E' | PR-A2（PR #3，已合并 5e378712） | W1 | 代码 ✅（显式传入不覆盖）；**待 PG 抽查新快照 confidence_level='E'** |
| 性能-4 🔶 | 重复任务清理 | PR-A2（PR #3，脚本已入库 `backend/scripts/cleanup_duplicate_standard_tasks.py`） | W1 | **待执行 dry-run 核对 16 组 → --execute 后重复组=0** |
| 性能-5 ✅ | gauges 时间窗 | WS-F（PR #5，已合并 012cd23b） | W1 | `/board/aggregate` 支持 timeWindow ✅；**待页面联调抽验** |
| 性能-6 ✅ | 实时过期提示 | WS-F（PR #5） | W1 | pid-dashboard 已实现；**待断流场景抽验** |
| 性能-7 | SPONSOR 门控 | WS-D | W2 | e2e：sponsor 不见入口、直访被拒 |
| 性能-8 ✅ | rules 契约 | WS-F（PR #5） | W1 | 裸数组契约已落地（api/metric.ts 删 RuleListResult） |
| 性能-9 ✅ | 注释修正 | WS-F（PR #5） | W1 | 已随 PR 修正 |
| 性能-10 🔶 | badge 死代码 | WS-F（PR #5，loop-performance 试点已合） | W1 | **待渲染一致性抽验，决定推广或删除组件** |
| 性能-11 | +8h hack | WS-G | W3 | 统一时间工具替换，渲染不变 |
| 性能-12 | ranking 全量 | WS-G | W3 | >100 回路时饼图计数完整 |
| 性能-13 | 部署走查 | WS-G | W3 | 测试机全流程跑通记录（上线 gate） |
| 性能-14 | e2e 补盲 | WS-G | W3 | 新增 spec 全绿 |

## 9. Wave 0 立即执行步骤（命令级）

```bash
# 0. 基线确认（当前工作树整体先验）
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd ../frontend && pnpm run check:type

# 1. PR-L1：切分支，提交回路阶段1 + 阶段2收尾（当前分支与 main 同点，未提交改动随切换携带）
git switch -c zp/fix-loop-p0-batch-crash
rm frontend/apps/web-antd/src/views/loop/tag-mapping.vue frontend/apps/web-antd/src/api/aas.ts
git add backend/app/services/loop_batch.py backend/app/services/loop.py \
        backend/tests/test_loop_batch.py backend/tests/test_loop.py \
        frontend/apps/web-antd/src/components/loop/quality-tag.vue \
        frontend/apps/web-antd/src/views/loop/tag-mapping.vue frontend/apps/web-antd/src/api/aas.ts
pnpm run check:type   # 确认 aas.ts/tag-mapping.vue 无残留引用
git commit -m "fix(loop): 修复批量配置/删除与导入缺省值崩溃，下线孤儿页 tag-mapping"
git push -u origin zp/fix-loop-p0-batch-crash   # 随后 gitea API 开 PR → 门禁 → 合并

# 2. PR-A1：回到性能分支提交 #1（工作区剩余文件即 #1 全集）
git switch zp/fix-metric-p0-data-integrity
git add backend/app/tasks/kpi_calc.py backend/app/services/task_tracker.py \
        backend/app/api/v1/endpoints/tasks.py backend/app/schemas/task.py \
        backend/tests/test_api_tasks.py backend/tests/tasks/test_backfill_orchestration.py
cd backend && uv run pytest -q   # 重点过 test_api_tasks / test_backfill_orchestration
git commit -m "fix(metric): 回填进度改记 work_items 独立字段，loops_total 恒为回路数"
git push -u origin zp/fix-metric-p0-data-integrity   # gitea API 开 PR → 合并 → 重启 worker 验证

# 3. 清理残留工作树分支
git worktree list   # 确认无占用后
git branch -D worktree-agent-a14ae08b8580fe7ca worktree-agent-a4d6575ff7267df7f \
  worktree-agent-a551fa7b2e13f5355 worktree-agent-a767c686758d46971 worktree-agent-ab32c6280cabc4f7f
```

## 10. 风险登记册

| 风险 | 概率/影响 | 应对 |
|---|---|---|
| R1/R2 裁决延迟阻塞 WS-D/WS-G | 中/高 | 决策门单列（§7）；WS-D 先行准备分支，裁决后仅填权限矩阵；超期未裁决按建议口径执行并记录 |
| 工作树拆分误提交/漏提交 | 中/中 | W0 先全量门禁再拆；两次 commit 后 `git status` 必须为空；PR diff 对照 §1.1 表逐文件核对 |
| task_tracker V2 切换期旧 RUNNING 任务 loops_total 脏值 | 中/低 | 合并时确认无长跑回填任务；如有，一次性脚本修正或忽略旧记录（PR-A1 描述中注明） |
| 补数加固改动致实时链路回归 | 低/高 | B1 单测覆盖 checkpoint 推进全分支；合并后观察 24h 补数日志；保留重试退避参数 sys_config 可调 |
| WS-B 改动破坏 gap backfill `conflict=skip` 红线 | 低/高 | PR 审查专项检查点；补数路径集成测试断言不 DELETE 实时行 |
| 阶段 3 时区实测受远端环境限制 | 中/低 | 按源文档策略顺延，不阻塞 B2 其余项 |
| 多 worktree 并发改同文件 | 低/中 | §3.2 所有权矩阵为唯一真相源；每日 rebase main |
| #4 清理脚本误删非重复任务 | 低/中 | dry-run 输出 16 组清单经人工确认后方执行；脚本仅删终态重复记录 |

## 11. 范围外

诊断中心整改（按计划 `diagnosis-module-review-rectification-plan-2026-07-19.md` Phase C/D/E 独立推进）；KPI 报表图表化（产品形态决策，另议）。
