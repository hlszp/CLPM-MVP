# CLPM v6.2 合并前最终门禁检查清单

> **合并目标**：`codex/v6.2-integration` → `main`
> **生成时间**：2026-07-31
> **集成分支 HEAD**：`458a2657`（61 commits ahead of main，110 files changed，+20049/-686）
> **迁移 HEAD**：`p3e5f6g7h8i9`（Phase 3 tracker_assignee_planned_at）
> **ORM 表数**：38（37 基线 + process_model_version）
> **覆盖阶段**：Phase 0 Truth First → Phase 1 数据同轴 → Phase 2 可信辨识 → Phase 3 模型生命周期

---

## 使用说明

- 本清单为合并 main 前的**最后一道防线**，所有"必须"项必须 ✅ 方可发起 PR。
- "建议"项不阻塞合并，但需在合并后立即跟进。
- 每项检查需附**证据**（命令输出/报告路径/提交哈希），不允许"目测通过"。
- 执行人独立于开发者（独立复核原则）。

---

## 1. 代码质量门禁（必须全绿）

### 1.1 后端

| # | 检查项 | 命令 | 通过标准 | 结果 |
|---|---|---|---|---|
| B1 | ruff check | `cd backend && uv run ruff check .` | 退出码 0，无错误 | ⬜ |
| B2 | ruff format check | `cd backend && uv run ruff format --check .` | 退出码 0，477 files already formatted | ⬜ |
| B3 | pytest 全量 | `cd backend && uv run pytest -q` | 3666 passed, 1 skipped, 33 xfailed（基线） | ⬜ |
| B4 | alembic check（schema 漂移） | `cd backend && uv run alembic check` | 退出码 0，无漂移 | ⬜ |
| B5 | alembic 单一 head | `cd backend && uv run alembic heads` | 仅 `p3e5f6g7h8i9` 一个 head | ⬜ |

**B3 通过基线说明**：当前基线为 `3666 passed, 1 skipped, 33 xfailed`。若新增测试导致数字变化，需确认增量符合预期，不得出现 `failed` 或 `error`。

### 1.2 前端

| # | 检查项 | 命令 | 通过标准 | 结果 |
|---|---|---|---|---|
| F1 | TypeScript 类型检查 | `cd frontend && pnpm run check:type` | 2/2 packages 通过（web-antd + playground） | ⬜ |
| F2 | vitest 单元测试 | `cd frontend && pnpm exec vitest run` | 456 passed（54 文件），0 failed | ⬜ |
| F3 | 前端格式化 | `cd frontend && pnpm run format` | 无未格式化文件（可选，CI 非强制） | ⬜ |

### 1.3 E2E

| # | 检查项 | 命令 | 通过标准 | 结果 |
|---|---|---|---|---|
| E1 | 整定专项 E2E | `cd e2e && pnpm exec playwright test tests/tuning.spec.ts` | E2E-TUNE-001~007 全通过 7/7 | ⬜ |
| E2 | 全量 E2E（可选） | `cd e2e && pnpm exec playwright test` | Phase 3 相关用例零回归；失败用例需逐项确认非 Phase 3 回归 | ⬜ |

**E2 豁免说明**：全量 E2E 允许存在登录超时级联/数据状态依赖导致的偶发失败，但必须满足：
- 所有 `tuning.spec.ts` 用例必须通过（Phase 3 核心验证）
- `confidence.spec.ts` 中可信度门禁相关用例必须通过（Phase 0 安全验证）
- 失败用例需在 `e2e-test-report-phase3-2026-07-31.md` 中逐项标注根因，确认非回归

---

## 2. 迁移与数据库安全（必须全绿）

### 2.1 迁移链完整性

| # | 检查项 | 方法 | 通过标准 | 结果 |
|---|---|---|---|---|
| M1 | upgrade 循环 | `alembic upgrade head` 从空库 | 38 表 + seed + 单一 head | ⬜ |
| M2 | downgrade 循环 | `alembic downgrade base` 后再 `upgrade head` | 无报错，表结构一致 | ⬜ |
| M3 | fresh-install bootstrap | 专用临时空 PostgreSQL 执行 `db/postgresql/01_schema.sql` + `alembic stamp head` | 38 表，与 ORM 表集合完全相等 | ⬜ |
| M4 | 生产 bootstrap 收敛测试 | `pytest -m integration tests/test_alembic_convergence.py` | 非 skipped/deselected，真实通过 | ⬜ |
| M5 | DDL 与 ORM 收敛 | `pytest tests/test_p0_schema_convergence.py`（静态收敛测试） | 基础 DDL 表集合 = ORM 表集合 | ⬜ |

### 2.2 Phase 3 关键迁移专项验证

| # | 检查项 | 方法 | 通过标准 | 结果 |
|---|---|---|---|---|
| M6 | process_model_version 表创建 | upgrade 后 `\d process_model_version` | 28 字段 + 5 CHECK 约束 + 3 索引 | ⬜ |
| M7 | 并发一致性 | `pytest tests/test_p3_004_concurrency.py` | FOR UPDATE + 部分唯一索引双层防护生效 | ⬜ |
| M8 | 回填→影子读→切换读取 | `pytest tests/test_p3_005_model_version_migration.py` | 旧 tuning_record 引用正确回填，读取切换无损 | ⬜ |
| M9 | 降级验证 | `alembic downgrade h8b9c0d1e2f3` | process_model_version 表删除，字段移除，数据无损 | ⬜ |
| M10 | 升级恢复 | 降级后 `alembic upgrade head` + 冒烟测试 | IDENTIFICATION_ONLY 记录恢复，功能正常 | ⬜ |

**回滚方案**：详见 [phase3-migration-rollback-plan-2026-07-31.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/phase3-migration-rollback-plan-2026-07-31.md)

---

## 3. 安全红线门禁（必须全绿）

> 这是 v6.2 的核心价值主张——"不直接修改 DCS，只输出建议、证据、风险和回退方案"。

| # | 检查项 | 方法 | 通过标准 | 结果 |
|---|---|---|---|---|
| S1 | 无 DCS 参数写端点 | `pytest tests/test_p3_009_no_dcs_write.py` | 5 守卫测试全通过 | ⬜ |
| S2 | 无"自动实施"按钮/路由 | 全局搜索 `grep -ri "auto.*implement\|dcs.*write\|自动下写" frontend/ backend/` | 无业务入口（仅文档/注释提及边界） | ⬜ |
| S3 | 可信度放行门禁 | `pytest tests/test_p0_014_confidence_gate.py` | A/B 放行、C 显式确认、D/E/INCONCLUSIVE 禁止整定 | ⬜ |
| S4 | AUTO fallback 安全门禁 | `pytest tests/test_tuning.py -k "auto_fallback or inconclusive"` | 无有效阶跃不得成功，返回 INCONCLUSIVE + reason | ⬜ |
| S5 | 未知风险不得展示为 0 | `pytest tests/test_p0_024_unknown_risk.py` + E2E-CONF 断言 | 无数据时显示"暂不可用"，不渲染 0 | ⬜ |
| S6 | 影子候选不得触发整定 | 静态检查 `grep -ri "shadow.*tune\|shadow.*recommend" backend/app/` | Phase 4 未实现，当前无影子触发路径 | ⬜ |

---

## 4. 产品兼容不变量（必须逐项确认）

> 来自 checklist §1.2，Phase 0-1 承诺的兼容性边界。

| # | 不变量 | 验证方法 | 结果 |
|---|---|---|---|
| C1 | 顶级结构保持"工作台 + 5 个业务模块" | 核对菜单/路由配置，无新增顶级中心 | ⬜ |
| C2 | 路由前缀不变：`/dashboard` `/loop` `/metric` `/diagnosis` `/tuning` `/system` | `grep -r "path:" frontend/apps/web-antd/src/router/` 核对 | ⬜ |
| C3 | 旧路由不物理删除，使用 redirect/兼容壳 | `/tuning/model` `/tuning/algorithm` `/tuning/simulation` `/diagnosis/records` 均 redirect + hideInMenu | ⬜ |
| C4 | 不删除/改名现有 API | OpenAPI diff 对比 main（新增字段 optional，无删除/改名） | ⬜ |
| C5 | 不新增数据库业务实体（除 ADR 审批的 process_model_version） | ORM 表清单 diff，仅新增 process_model_version | ⬜ |
| C6 | 页面合并不扩大角色权限 | `routes-authority.test.ts` 全通过，无权限扩大 | ⬜ |
| C7 | 路由稳定元素根，防详情页白屏 | `pytest tests/test_route_stable_root.py` + E2E 硬刷新验证 | ⬜ |
| C8 | 3+1+8 正式评分公式不变 | `pytest tests/test_kpi_formula.py` 核心公式断言 | ⬜ |
| C9 | PID 参数只读从 tag 读取，不新增下写 | 与 S1/S2 交叉验证 | ⬜ |
| C10 | 计算类历史数据查询恒走本地 TDengine | `grep -r "get_provider" backend/app/tasks/` 确认无远端降级 | ⬜ |

---

## 5. Phase 交付完整性（必须逐项确认）

### 5.1 Phase 0：Truth First（安全收口）

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| P0-1 | AUTO fallback 不再盲成功 | P0-001~005，`test_tuning.py` 回归测试 | ⬜ |
| P0-2 | 纯滞后标为 HEURISTIC_2TS，不伪称自动估计 | P0-006~009，`thetaSource` 枚举 | ⬜ |
| P0-3 | 历史 IPDT 明确拒绝，不静默返回 SOPDT | P0-010~013，API/pipeline 测试 | ⬜ |
| P0-4 | 可信度放行门禁全链路一致 | P0-014~018，见 S3 | ⬜ |
| P0-5 | IV 能力降级为 EXPERIMENTAL | P0-019~023，不宣称闭环无偏 | ⬜ |
| P0-6 | 未知风险不展示为 0 | P0-024~026，见 S5 | ⬜ |
| P0-7 | 状态机/37 表/bootstrap 收敛 | P0-027~044，见 M1-M5 | ⬜ |
| P0-8 | 安全边界静态检查 | P0-039，见 S1 | ⬜ |

### 5.2 Phase 1：数据同轴与 IA 减负

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| P1-1 | PV/OP/SP/MODE 同轴 | P1-001~006，DataPlanner 集成测试 105 passed | ⬜ |
| P1-2 | 真实片段切分 + 激励门禁 | P1-007~011，preview API 真实片段 | ⬜ |
| P1-3 | API/任务合同统一 | P1-012~016，typed response + TaskTracker 桥接 | ⬜ |
| P1-4 | 工作台→跨模块待办门户 | P1-017，workbench.vue 重构 | ⬜ |
| P1-5 | 诊断 tasks/records 合并 Tabs | P1-018，task-center.vue | ⬜ |
| P1-6 | 整定三页合并为可恢复 stepper | P1-019，flow.vue + sessionStorage 持久化 | ⬜ |
| P1-7 | 旧路由兼容重定向 | P1-020，见 C3 | ⬜ |
| P1-8 | 统一 Loop 上下文头 | P1-021，loop-context-header.vue | ⬜ |
| P1-9 | 高级参数仅管理员可见 | P1-022，useClpmRoles composable | ⬜ |
| P1-10 | 状态覆盖组件四态 | P1-023，state-overlay.vue + 22 新测试 | ⬜ |

### 5.3 Phase 2：可信辨识

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| P2-1 | 延迟候选搜索（BIC 准则） | P2-001~003，θ=0/2/5/20/60 Ts 参数化测试 | ⬜ |
| P2-2 | 非参数一致性检查 | P2-004，符号/量级交叉校验 | ⬜ |
| P2-3 | AIC/BIC/CV + Occam 削减 | P2-006~007，SOPDT 显著优于才升级 | ⬜ |
| P2-4 | 真实 IPDT 历史辨识 | P2-008，恢复 Phase 0 暂禁的选项 | ⬜ |
| P2-5 | 闭环 IV（CLIVC） | P2-009~011，Monte Carlo K 偏差比 ARX 低 96%+ | ⬜ |
| P2-6 | 物理门禁 + 证据快照 | P2-012~016，复极点/负根拒绝，残差/快照哈希 | ⬜ |
| P2-7 | 22 回路人工标注集 94.4% 通过率 | P2-017，≥85% 门禁达标 | ⬜ |
| P2-8 | 连接池监控基础设施 | P2-018，`/health/db-connections` + 监控脚本 | ⬜ |
| P2-9 | 历史辨识坏点清洗 | P2-019，小缺口插值+大缺口取段，14 测试 | ⬜ |

### 5.4 Phase 3：模型生命周期与整改闭环

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| P3-1 | ADR 七项准入评审通过 | P3-001~002，决定新增 process_model_version | ⬜ |
| P3-2 | 最小 process_model_version 聚合 | P3-003，28 字段 + 5 CHECK + 3 索引，见 M6 | ⬜ |
| P3-3 | 同一回路至多一个 CURRENT 并发一致性 | P3-004，FOR UPDATE + 部分唯一索引，见 M7 | ⬜ |
| P3-4 | 模型版本迁移（回填→影子读→切换读取） | P3-005，见 M8 | ⬜ |
| P3-5 | IDENTIFICATION_ONLY 算法值 | P3-006，纯辨识不再用 IMC 表示 | ⬜ |
| P3-6 | 人工实施清单（当前/建议/风险/回退/单位转换） | P3-007，algorithm.vue 清单组件 | ⬜ |
| P3-7 | Tracker 闭环字段（assignee/planned_at） | P3-008，迁移 `p3e5f6g7h8i9` | ⬜ |
| P3-8 | 全程无 DCS 下写 | P3-009，见 S1/S2 | ⬜ |
| P3-9 | 迁移/回滚/并发/fresh-install 演练 | P3-010，见 M9/M10 | ⬜ |

---

## 6. 文档同步（必须全绿）

| # | 文档 | 版本/状态 | 结果 |
|---|---|---|---|
| D1 | 实现契约 `implementation-contract.md` | v2.3（状态机/模型门禁/37 表/安全边界） | ⬜ |
| D2 | PRD `PRD.md` | v6.1（含 Phase 0 对齐说明） | ⬜ |
| D3 | FDS / ADS / DDS / IDS | v6.0（各加 Phase 0 对齐说明指向契约 v2.3） | ⬜ |
| D4 | UI/UX 设计规范 | v6.1（含 ZL 工业设计规范） | ⬜ |
| D5 | 实施任务清单 | Phase 0-3 全部标记完成，门禁记录完整 | ⬜ |
| D6 | E2E 测试报告 | `e2e-test-report-phase3-2026-07-31.md` 已生成 | ⬜ |
| D7 | 迁移回滚方案 | `phase3-migration-rollback-plan-2026-07-31.md` 已生成 | ⬜ |
| D8 | 完整任务清单 | `clpm-v6.2-full-task-list.md` 状态同步 | ⬜ |
| D9 | 进度总览 | `clpm-v6.2-progress-tracker.md` 更新 | ⬜ |

---

## 7. 已知残留风险（需确认接受）

| # | 风险项 | 等级 | 缓解措施 | 接受确认 |
|---|---|---|---|---|
| R1 | Phase 0 延后项 P0-034/037/038（OpenAPI 基线/E2E 基线） | 低 | 不阻塞安全门禁；合并后立即补 | ⬜ |
| R2 | P0-030 `/compare` 独立 schema 延后 | 低 | 当前复用 SimulateRequest，无功能错误，仅接口洁净度 | ⬜ |
| R3 | SOPDT T1/T2 个体精度需 SRIVC 修复 | 低 | Phase 2 核心门禁 FOPDT+IPDT 94.4% 已达标；SOPDT 精度后续优化 | ⬜ |
| R4 | 全量 E2E 偶发登录超时 | 低 | 整定专项 7/7 稳定通过；失败为环境级联非回归 | ⬜ |
| R5 | 并发双 CURRENT 残留概率 | 极低 | FOR UPDATE + 部分唯一索引双层防护 | ⬜ |
| R6 | Phase 4 在线影子运行未开始 | 预期 | v6.2 不含在线发布能力，Phase 4 独立交付 | ⬜ |

---

## 8. Git 工作流（合并操作）

| # | 步骤 | 命令/方法 | 结果 |
|---|---|---|---|
| G1 | 确认集成分支与远程同步 | `git status` → up to date with origin | ⬜ |
| G2 | 确认无未提交变更 | `git status` → working tree clean | ⬜ |
| G3 | 拉取最新 main | `git fetch origin && git checkout main && git pull origin main` | ⬜ |
| G4 | 合并演练（不推送） | `git checkout codex/v6.2-integration && git merge main --no-ff --no-commit` 后检查冲突 | ⬜ |
| G5 | 通过 gitea API 创建 PR | `gh` 等价 API 调用，PR 标题 ≤70 字符 | ⬜ |
| G6 | PR 合并方式 | `--no-ff` merge commit（保留阶段历史） | ⬜ |
| G7 | 合并后同步镜像 | `git push github main` | ⬜ |
| G8 | 合并后冒烟 | 后端 `/health` + 前端首页 + 关键路由可访问 | ⬜ |

**红线**：
- 禁止 `git push --force` 共享分支
- 禁止 `git reset --hard` 后推送
- 禁止 `--no-verify` 跳过 hooks
- PR 必须经过全量门禁验证后方可合并

---

## 9. 合并后立即跟进（建议，不阻塞合并）

| # | 事项 | 负责人 | 期限 |
|---|---|---|---|
| A1 | 补齐 P0-034 OpenAPI/路由/response contract 检查 | — | 合并后 1 周 |
| A2 | 补齐 P0-037 旧路由 E2E 基线 | — | 合并后 1 周 |
| A3 | 补齐 P0-038 结构化 OpenAPI 基线 | — | 合并后 1 周 |
| A4 | 评估 P0-030 `/compare` 独立 schema | — | 合并后 2 周 |
| A5 | 启动 Phase 4 在线影子运行开发 | — | v6.2 发布后 |
| A6 | 生产部署 + 实弹验证（R1-R6） | — | 合并后立即 |

---

## 10. 签署

| 角色 | 确认 | 日期 |
|---|---|---|
| 开发负责人 | ⬜ 全部"必须"项已通过 | ________ |
| 独立复核人 | ⬜ 已独立验证，无 P0/P1 未决 | ________ |
| 产品负责人 | ⬜ 残留风险已确认接受 | ________ |

> **合并准入条件**：§1-§6 所有"必须"项 ✅ + §7 残留风险全部确认接受 + §8 Git 工作流执行完毕。
> 任一"必须"项未通过，**不得合并**。

---

## 附录：一键执行门禁脚本

```bash
# ============================================================
# CLPM v6.2 合并前最终门禁（一键执行）
# 用法：bash docs/过程文档/pre-merge-gate-run.sh
# ============================================================
set -euo pipefail

echo "=== [1/5] 后端门禁 ==="
cd backend
uv run ruff check . && echo "B1 ruff check ✅"
uv run ruff format --check . && echo "B2 ruff format ✅"
uv run pytest -q && echo "B3 pytest ✅"
uv run alembic check && echo "B4 alembic check ✅"
uv run alembic heads | grep -q "p3e5f6g7h8i9" && echo "B5 single head ✅"
cd ..

echo "=== [2/5] 前端门禁 ==="
cd frontend
pnpm run check:type && echo "F1 typecheck ✅"
pnpm exec vitest run && echo "F2 vitest ✅"
cd ..

echo "=== [3/5] E2E 整定专项 ==="
cd e2e
pnpm exec playwright test tests/tuning.spec.ts && echo "E1 tuning E2E ✅"
cd ..

echo "=== [4/5] 迁移专项 ==="
cd backend
uv run pytest -q tests/test_p3_009_no_dcs_write.py tests/test_p3_004_concurrency.py -m integration tests/test_alembic_convergence.py && echo "M 迁移专项 ✅"
cd ..

echo "=== [5/5] Git 状态 ==="
git status --short | grep -q . && { echo "G2 工作树未清理"; exit 1; } || echo "G2 工作树干净 ✅"

echo ""
echo "=== 全部门禁通过，可发起 PR ==="
```

---

> **文档版本**：v1.0
> **生成依据**：`clpm-v6.2-implementation-task-checklist-2026-07-29.md` + `clpm-v6.2-full-task-list.md` + `AGENTS.md` CI 门禁规范
> **有效期**：本次合并（`codex/v6.2-integration` → `main`）专用
