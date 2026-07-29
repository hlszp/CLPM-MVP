# CLPM v6.2 实施计划与任务清单

> 状态：执行中
> 集成分支：`codex/v6.2-integration`
> 基线提交：`e7ca7749`
> 方案来源：`clpm-v6.2-product-and-identification-optimization-plan-2026-07-29.md`
> 启动基线：`3409 passed, 1 skipped, 15 deselected, 33 xfailed`
> 原则：安全门禁优先、兼容迁移、阶段验收、独立复核、如非必要勿增实体。

## 1. 分支与交付策略

```text
main@e7ca7749
  └─ codex/v6.2-integration
       ├─ Phase 0：Truth First
       ├─ Phase 1：数据同轴与 IA 减负
       ├─ Phase 2：可信辨识
       ├─ Phase 3：模型生命周期与整改闭环
       └─ Phase 4：在线影子运行
```

本轮由同一集成分支持续交付，每个 Phase 使用独立逻辑提交。若需多人并行写代码，再从集成分支创建短分支；短分支只合回集成分支，不直接进入 `main`。

### 1.1 硬性规则

- [x] 从已验证的 `main@e7ca7749` 创建集成分支。
- [x] 修改业务代码前运行用户指定的全量后端基线。
- [ ] 每项缺陷先证明根因，再写修复。
- [ ] 每个修复必须有“无修复时失败、有修复时通过”的回归测试。
- [ ] 每个 Phase 完成后单独提交、独立审查、运行阶段门禁。
- [ ] 旧 API、路由、状态和历史数据至少兼容一个版本。
- [ ] 不自动下写 DCS，不允许在线影子候选触发整定。
- [ ] 不使用 force push，不重写共享历史。
- [ ] 最终只从集成分支发起 PR；合并前运行全量门禁和迁移演练。

### 1.2 产品与兼容不变量

- [ ] 顶级结构保持“工作台 + 5 个业务模块”，不新增资产、模板、辨识、仿真或 AI 顶级中心。
- [ ] `/dashboard`、`/loop`、`/metric`、`/diagnosis`、`/tuning`、`/system` 路由前缀不变。
- [ ] Phase 0–1 不物理删除公开旧路由，使用兼容壳、redirect 或 query/tab 映射。
- [ ] Phase 0–1 不删除或改名现有 API；新增响应字段默认 optional。
- [ ] Phase 0–1 不新增数据库业务实体。
- [ ] 页面合并不得扩大任何角色权限。
- [ ] 路由页面保持稳定元素根，覆盖 vben `v-show + Transition + KeepAlive`，防止详情页白屏回归。
- [ ] 3+1+8 正式评分公式不变；派生指标与正式评分指标分开标识。

## 2. 状态定义

| 标记 | 含义 |
|---|---|
| `[ ]` | 未开始 |
| `[~]` | 实施中 |
| `[x]` | 已完成并有验证证据 |
| `[!]` | 阻塞，必须记录原因与解除条件 |
| `[-]` | 经评审取消，必须记录理由 |

优先级：

- `P0`：会产生错误模型、误导安全结论或错误放行；
- `P1`：影响算法可信度、接口一致性或数据正确性；
- `P2`：影响认知负载、维护成本或后续扩展。

## 3. Phase 0：安全与事实收口

目标：停止生成、放行或展示“看似成功、实际不可信”的结果，并固化真实契约。

### 3.1 P0-01 AUTO fallback 必须验证真实阶跃

- [ ] `V62-P0-001` 记录生产路径与失败包装根因。
  - 文件：`backend/app/tasks/tuning.py`、`backend/app/services/tuning.py`、`backend/app/services/tuning_algorithms.py`
  - 根因假设：历史辨识失败后无条件用任意窗口走阶跃路径；参数为空仍可能包装为成功。
- [ ] `V62-P0-002` 定义最小阶跃适用性门禁。
  - 稳定基线；
  - MV/OP 有显著阶跃，禁止用 PV 变化代替 MV；
  - 阶跃后保持时间足够；
  - PV 响应显著；
  - K/τ/θ 全部 finite 且满足物理边界。
- [ ] `V62-P0-003` 修改 AUTO 策略：只有通过门禁的片段可 fallback，其余返回 INCONCLUSIVE 和 reason code。
- [ ] `V62-P0-004` 增加回归测试。
  - 常值 OP；
  - 纯噪声；
  - 缓慢漂移；
  - 多阶跃混合窗口；
  - 合格单阶跃；
  - 辨识返回空参数。
- [ ] `V62-P0-005` 验证历史辨识、任务落库、统计和前端状态没有伪成功。

验收：

```bash
cd backend
uv run pytest -q tests/test_tuning.py tests/test_tuning_identification.py tests/test_nan_inf.py
```

回退点：恢复原 AUTO 入口但保持“无有效阶跃不得成功”的安全门禁，不回退到盲 fallback。

### 3.2 P0-02 纯滞后不得伪称自动估计

- [ ] `V62-P0-006` 证明 `thetaEstimate=None` 被固定为 `2×Ts`，且显式 `theta=0` 被 `or` 吞掉。
- [ ] `V62-P0-007` Phase 0 临时收容：
  - API 不再宣称真正自动估计；
  - 返回 `delaySource=HEURISTIC_2TS`；
  - 未显式给 θ 时可信度最高 C；
  - 不能直接进入整定。
- [ ] `V62-P0-008` 显式 `theta=0` 必须被保留。
- [ ] `V62-P0-009` 增加 schema、service、pipeline、API 和前端放行回归测试。

验收：

```bash
cd backend
uv run pytest -q tests/test_tuning_identification.py tests/test_tuning_history_seam.py tests/test_tuning.py
```

完整延迟搜索属于 Phase 2；Phase 0 只关闭误导和错误放行。

### 3.3 P0-03 历史 IPDT 类型契约

- [ ] `V62-P0-010` 确认历史路径 IPDT 被 SOPDT 分支处理的根因。
- [ ] `V62-P0-011` 在 history schema/API/UI 暂时拒绝 IPDT，错误信息说明“历史 IPDT 尚未实现”。
- [ ] `V62-P0-012` STEP_ONLY 已有 IPDT 能力不得被误禁。
- [ ] `V62-P0-013` 增加 API、pipeline、前端选项和兼容测试。

验收：历史请求 IPDT 明确失败或不提供选项；任何路径都不得静默返回 SOPDT。

### 3.4 P0-04 可信度放行门禁

- [ ] `V62-P0-014` 后端建立统一放行规则：A/B 可继续，C 需显式人工确认，D/E/INCONCLUSIVE 禁止。
- [ ] `V62-P0-015` `/tune`、`/simulate` 推荐链携带并验证模型可信度。
- [ ] `V62-P0-016` 前端移除 D/E/INCONCLUSIVE 的“使用此模型整定”入口。
- [ ] `V62-P0-017` reason code、按钮状态、提示文案和审计日志一致。
- [ ] `V62-P0-018` 增加后端 contract 和前端/E2E 测试。

### 3.5 P0-05 闭环 IV 能力降级

- [ ] `V62-P0-019` 记录当前 IV/IV4 不满足闭环一致性的根因。
- [ ] `V62-P0-020` 主路径和 UI 不再宣称“闭环无偏/IV4”。
- [ ] `V62-P0-021` 当前名义 IV 结果标为 `EXPERIMENTAL`，不得成为版本化 CURRENT 或自动推荐依据。
- [ ] `V62-P0-022` 保留算法输出用于对比研究，不删除代码。
- [ ] `V62-P0-023` 增加能力标记和发布门禁测试。

真正闭环 IV/OE/PEM 属于 Phase 2。

### 3.6 P0-06 未知风险不得展示为 0

- [ ] `V62-P0-024` 删除整定工作台 `highRiskCount=0`、`overThresholdCount=0` 的伪数据。
- [ ] `V62-P0-025` 无数据时显示“未计算/暂不可用”，或隐藏卡片。
- [ ] `V62-P0-026` 增加组件和 E2E 断言：未知值不渲染为 0。

### 3.7 状态机、API、schema 与设计事实源

- [ ] `V62-P0-027` 盘点数据库现存整定状态分布。
- [ ] `V62-P0-028` 固化唯一目标状态机与旧值只读兼容映射。
- [ ] `V62-P0-029` 统一 `DataSource` 大小写与 typed response；兼容旧值一版。
- [ ] `V62-P0-030` 为 `/compare` 建独立请求 schema，不强制无关 PID 字段。
- [ ] `V62-P0-031` 记录现行生产 bootstrap：`01_schema.sql` 建表后，首次部署直接 `alembic stamp head`。
- [ ] `V62-P0-032` 用 ORM/Alembic 自动核对表清单，确认 ORM 37 张、基础 DDL 21 张、缺 16 张的 RED 基线。
- [ ] `V62-P0-033` 同步 PRD、实现契约、FDS、ADS、DDS、IDS、UIUX。
- [ ] `V62-P0-034` 增加 OpenAPI/路由/response contract 检查，防止文档再次漂移。
- [ ] `V62-P0-035` 将 `time_constant` 标为 `NOT_IMPLEMENTED`，决定补算或兼容废弃，不把 NULL 当无数据。
- [ ] `V62-P0-036` 生成当前可见菜单、隐藏路由、重定向和角色权限基线。
- [ ] `V62-P0-037` 为待合并旧路由建立直链、SPA、前进后退和硬刷新 E2E 基线。
- [ ] `V62-P0-038` 保存结构化 OpenAPI 基线，后续检查 breaking changes。
- [ ] `V62-P0-039` 增加安全边界静态检查：不存在 DCS 参数写端点或“自动实施”入口。
- [ ] `V62-P0-040` 补齐基础 DDL 缺失的 16 张 ORM 表及其索引、约束、默认值和外键。
- [ ] `V62-P0-041` 增加静态收敛测试：基础 DDL 表集合必须等于 ORM 表集合。
- [ ] `V62-P0-042` 用专用临时空 PostgreSQL 执行真实生产初始化，验证 37 表、seed、单一 head、无缺表/重复表。
- [ ] `V62-P0-043` 显式运行迁移 integration 测试，不允许把默认 deselected 当通过。
- [ ] `V62-P0-044` 另立 ADR 评估未来切换为 Alembic 唯一建库源；Phase 0 不在空首迁移上直接删除 DDL。

当前确认缺失的 16 张表：

```text
algorithm_parameter
clpm_metric_data_requirement
dcs_mode_mapping
dcs_model
dcs_pid_structure
dcs_vendor
diagnosis_config_change
diagnosis_rule
diagnosis_tag
diagnosis_task
diagnosis_threshold_override
kpi_snapshot_custom
loop_confidence_latest
mode_definition
report_config
unit_kpi_summary
```

### 3.8 Phase 0 门禁

- [ ] P0 定向测试全部通过。
- [ ] 后端全量 pytest 通过。
- [ ] ruff check、ruff format check 通过。
- [ ] alembic check 退出码 0。
- [ ] 专用临时 PostgreSQL 的生产 bootstrap 与 `test_alembic_convergence.py -m integration` 通过，不能是 skipped/deselected。
- [ ] 前端 typecheck 通过。
- [ ] 前端 vitest 全量通过。
- [ ] 关键 E2E：整定历史辨识、可信度门禁、未知风险显示、兼容路由通过。
- [ ] 独立代码审查无 P0/P1 未决。
- [ ] Phase 0 逻辑提交完成。

## 4. Phase 1：数据同轴与 IA 减负

### 4.1 PV/OP/SP/MODE 同轴

- [ ] `V62-P1-001` 以 PVOP 时间戳作为统一目标网格，消除 bundle 顺序依赖。
- [ ] `V62-P1-002` 读取 MODE 并与 PV/OP/SP 同轴。
- [ ] `V62-P1-003` 返回真实时间，不退化为数组索引。
- [ ] `V62-P1-004` 记录插值率、外推率、缺口和有效样本数。
- [ ] `V62-P1-005` 覆盖 1s PVOP + 10/30/60s BASE、乱序、缺口和边界外推。
- [ ] `V62-P1-006` 去除热路径逐点 naive datetime `.timestamp()`。

### 4.2 真实片段切分与激励门禁

- [ ] `V62-P1-007` 按 MODE、启停、缺口、饱和、人工干预和事件边界切片。
- [ ] `V62-P1-008` preview API 返回真实片段、排除原因和质量摘要。
- [ ] `V62-P1-009` OP 激励按量程或噪声归一化，消除 OP/PV 跨量纲比值。
- [ ] `V62-P1-010` 方向变化加入死区，不把零值/微噪声算作有效激励。
- [ ] `V62-P1-011` 回归矩阵标准化并增加单位缩放不变性测试。

### 4.3 API 与任务合同

- [ ] `V62-P1-012` 历史辨识使用 typed response model。
- [ ] `V62-P1-013` 统一整定异步任务与 TaskTracker 的状态、取消、通知和可观测合同。
- [ ] `V62-P1-014` 不新增任务实体；保留一版 `tuning_progress` 兼容适配。
- [ ] `V62-P1-015` 使用现有 DCS PID 结构完成 PB/Kp、秒/分钟、结构和滤波转换。
- [ ] `V62-P1-016` 增加 PID 转换往返性质测试。

### 4.4 页面与导航减法

- [ ] `V62-P1-017` 工作台变为跨模块待办门户，取消与装置性能的重复心智入口。
- [ ] `V62-P1-018` 诊断 tasks/records 合并为进行中/历史 Tabs。
- [ ] `V62-P1-019` 整定 model→algorithm→simulation 合并为可恢复 stepper。
- [ ] `V62-P1-020` 旧路由隐藏并兼容重定向至少一个版本。
- [ ] `V62-P1-021` 建立统一 Loop 上下文头，保留回路、时间窗和返回来源。
- [ ] `V62-P1-022` 配置归属业务模块，高级参数仅管理员可见。
- [ ] `V62-P1-023` 覆盖 loading、empty、error、partial、success 和权限状态。

### 4.5 Phase 1 门禁

- [ ] 数据同轴与片段集成测试通过。
- [ ] 后端全量门禁通过。
- [ ] 前端 typecheck、组件测试、关键 E2E 通过。
- [ ] 旧书签/深链兼容验证通过。
- [ ] UI/UX 独立审查通过。
- [ ] Phase 1 逻辑提交完成。

## 5. Phase 2：可信辨识

### 5.1 延迟和模型候选

- [ ] `V62-P2-001` 设计并实现 `d=0..d_max` 延迟候选搜索。
- [ ] `V62-P2-002` 使用时间顺序 validation/test 的 BIC 与自由仿真误差择优。
- [ ] `V62-P2-003` 覆盖 θ=0、2、5、20、60 Ts，测试不得传入真值。
- [ ] `V62-P2-004` 将非参数结果接入初值、符号和量级一致性检查。
- [ ] `V62-P2-005` Welch/相干只做辅助门禁，不宣称闭环对象频响无偏。

### 5.2 模型结构与闭环辨识

- [ ] `V62-P2-006` AIC/BIC/CV 接入主 pipeline。
- [ ] `V62-P2-007` SOPDT 仅在留出集显著优于 FOPDT 时升级。
- [ ] `V62-P2-008` 实现真实 IPDT 历史辨识并恢复 UI/API 选项。
- [ ] `V62-P2-009` 选择并实现可证明的闭环 IV/OE/PEM 方法。
- [ ] `V62-P2-010` Monte Carlo 覆盖测量噪声、负载扰动、控制器强度和弱 SP 激励。
- [ ] `V62-P2-011` 报告偏差、方差和弱工具统计量，并与 ARX 对比。

### 5.3 物理门禁与证据

- [ ] `V62-P2-012` 复极点、负根、不稳定模型不得伪装成稳定工程模型。
- [ ] `V62-P2-013` 输出 train/validation/test 观测、预测、残差和数据分区。
- [ ] `V62-P2-014` 输出自由仿真 NRMSE、BIC、残差检验和跨片段稳定性。
- [ ] `V62-P2-015` 输出 bootstrap 或等价不确定度摘要。
- [ ] `V62-P2-016` 记录算法版本、数据窗口、快照哈希和全部 reason code。
- [ ] `V62-P2-017` 建立 20+ 回路匿名人工标注集。

### 5.4 Phase 2 门禁

- [ ] 合成黄金集和 Monte Carlo 报告通过专家复核。
- [ ] 无留出证据、物理门禁或数据快照的模型无法进入整定。
- [ ] 算法性能和 Celery 容量基线达标。
- [ ] 后端、前端和 E2E 全量门禁通过。
- [ ] Phase 2 逻辑提交完成。

## 6. Phase 3：模型生命周期与整改闭环

- [ ] `V62-P3-001` 执行模型实体 ADR 七项准入评审。
- [ ] `V62-P3-002` 若不通过，继续使用单次 `tuning_record` 不可变证据快照。
- [ ] `V62-P3-003` 若通过，只新增一个最小 `process_model_version` 聚合。
- [ ] `V62-P3-004` 保证同一回路/工况只有一个 CURRENT 的并发一致性。
- [ ] `V62-P3-005` 采用一次性回填→影子读比对→切换读取→停止旧参数新写。
- [ ] `V62-P3-006` 整定记录引用模型版本，不再用 `algorithm=IMC` 表示纯辨识。
- [ ] `V62-P3-007` 人工实施清单包含当前值、建议值、单位转换、风险和回退值。
- [ ] `V62-P3-008` Tracker 记录负责人、MOC、执行时间和 A/B 效果验证。
- [ ] `V62-P3-009` 全程无 DCS 下写 API、按钮或隐含状态。
- [ ] `V62-P3-010` 完成迁移、回滚、并发和 fresh-install 演练。

## 7. Phase 4：在线影子运行

- [ ] `V62-P4-001` 每日/每周滑窗候选扫描。
- [ ] `V62-P4-002` 事件触发重辨识。
- [ ] `V62-P4-003` 当前人工模型与影子候选漂移比较。
- [ ] `V62-P4-004` 独立低优先队列、限流、超时和取消。
- [ ] `V62-P4-005` `shadow_identification` 能力开关。
- [ ] `V62-P4-006` 任务、拒绝原因、耗时和容量监控。
- [ ] `V62-P4-007` 人工复核队列和分层试点报表。
- [ ] `V62-P4-008` 影子候选不得发布 CURRENT、不得触发 PID 推荐。
- [ ] `V62-P4-009` 20+ 回路持续观测至少 4 周。
- [ ] `V62-P4-010` 输出可辨识率、误报率、拒绝原因、参数误差和审阅时间。

注意：代码与离线验证可以在本目标中交付；真实 4 周现场观测受自然时间和现场数据约束，完成前不能声称在线发布能力已验收。

## 8. 阶段通用门禁

每个 Phase 至少运行：

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run alembic check
# 需显式设置专用临时数据库，不得指向开发/生产库：
# TEST_DATABASE_URL=... uv run pytest tests/integration/test_alembic_convergence.py -v -m integration --no-header

cd ../frontend
pnpm run check:type
pnpm exec vitest run

cd ../e2e
pnpm exec playwright test
```

若改动仅影响部分模块，可先运行定向测试，但合并 Phase 和最终 PR 时必须运行全量门禁。

## 9. 最终交付清单

- [ ] 所有已承诺任务有状态、证据和提交。
- [ ] 所有取消/延期任务有决策记录和原因。
- [ ] 全量后端、前端、E2E 和迁移门禁通过。
- [ ] 设计文档、OpenAPI、ORM/Alembic、路由和状态机一致。
- [ ] 安全边界复核通过。
- [ ] 独立代码审查无未决 P0/P1。
- [ ] 集成分支推送至 `origin`。
- [ ] 创建并审查 PR。
- [ ] 合并到 `main` 后再次运行/核对门禁。
- [ ] `main` 推送 `origin`，随后同步 `github` 镜像。
- [ ] GitHub Actions 状态如因账户欠费不可运行，明确记录为外部阻塞，不伪报通过。

## 10. 执行日志

| 日期 | 阶段 | 事件 | 证据 |
|---|---|---|---|
| 2026-07-29 | Baseline | `main@e7ca7749` 后端全量基线通过 | 3409 passed，1 skipped，15 deselected，33 xfailed，153.03s |
| 2026-07-29 | Branch | 创建集成分支 | `codex/v6.2-integration` |
