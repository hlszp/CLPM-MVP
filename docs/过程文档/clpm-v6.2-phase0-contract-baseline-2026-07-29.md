# CLPM v6.2 Phase 0 契约与兼容基线

> 日期：2026-07-29  
> 状态：执行中基线；Phase 0 门禁完成后冻结  
> 目的：把状态机、数据来源、菜单/路由、OpenAPI、安全边界和未实现字段放在
> 同一事实表中，防止实现与设计文档再次漂移。

## 1. 不变量

- 保持“1 个工作台门户 + 5 个业务模块”的产品结构，不新增顶级中心。
- 现有公开路由在 v6.2 Phase 0–1 不物理删除；旧入口至少兼容一个版本。
- 现有 API 不删除、不改名；新增请求/响应字段默认 optional。
- 计算只读本地 TDengine；远端历史接口只供数据导入。
- 整定只输出建议、证据、风险和回退方案，不存在 DCS PID 参数下写。
- `3 核心 + 1 综合 + 8 扩展` 的正式评分契约不因本轮改造改变。

## 2. 整定状态事实

### 2.1 目标写入状态机

```text
DRAFT → RUNNING → IDENTIFIED → SIMULATED → COMPLETED
            └───────────────→ INCONCLUSIVE
SIMULATED / COMPLETED ──────→ ROLLED_BACK
```

语义：

| 状态 | 唯一语义 |
|---|---|
| `DRAFT` | 已保存但尚未运行 |
| `RUNNING` | 辨识、整定或仿真正在运行 |
| `IDENTIFIED` | 模型证据已生成，尚未形成可比较的仿真结果 |
| `SIMULATED` | 推荐参数已完成离线仿真，仍不代表现场实施 |
| `COMPLETED` | 本次离线建议流程完成，仍不代表平台下写 DCS |
| `INCONCLUSIVE` | 数据、激励、模型或安全门禁不足，不能形成建议 |
| `ROLLED_BACK` | 建议被人工撤回或现场按回退方案处理 |

### 2.2 旧值兼容

`PENDING`、`APPLIED`、`VERIFIED` 只读兼容一个版本，不再作为新写入值：

| 旧值 | 兼容显示 | 处理规则 |
|---|---|---|
| `PENDING` | 待运行（历史） | 查询/筛选保留；新建改用 `DRAFT` |
| `APPLIED` | 已人工应用（历史） | 不推断为平台自动实施，不自动改写 |
| `VERIFIED` | 已人工验证（历史） | 不自动折算成 `COMPLETED`，保留原始审计语义 |

2026-07-29 对本地开发库只读盘点：`tuning_record` 共 4 条，全部为
`INCONCLUSIVE + HISTORY`，`identify_method` 与 `confidence_level` 均为空。
该数字只是执行时快照，不是生产数据结论；它证明旧值迁移当前没有本地数据阻塞，
但代码仍必须保留兼容读取。

## 3. 数据来源契约

目标 canonical 值：

| 值 | 语义 |
|---|---|
| `HISTORY` | 历史数据辨识 |
| `STEP_EXPERIMENT` | 用户选择的受控阶跃实验 |
| `FALLBACK_STEP` | AUTO 历史路径失败后，经真实单阶跃门禁通过的兜底 |

旧值 `fallback_step` 兼容读取一个版本；新写入统一为 `FALLBACK_STEP`。任何
`FALLBACK_STEP` 记录都必须包含 `stepValidationPassed=true` 的服务端证据，
禁止把 PV 变化冒充 MV 阶跃。

## 4. 模型使用门禁

| 来源/可信度 | 辨识结果展示 | PID 整定/推荐仿真 |
|---|---|---|
| 版本化记录 A/B | 允许 | 允许 |
| 版本化记录 C | 允许 | 仅显式人工风险确认后允许 |
| D/E/INCONCLUSIVE/空 | 允许解释原因 | 禁止 |
| `HEURISTIC_2TS` | 允许，明确标为 2Ts 启发值 | 禁止 |
| 实验性 IV/“IV4” | 只供对比研究 | 禁止发布和推荐 |
| 受控阶跃实验 | 允许 | 仅服务端阶跃门禁通过后允许 |
| 人工模型 | 明确标为人工输入 | 仅显式风险确认后允许 |

后端必须按 `sourceRecordId` 查询持久化记录并使用服务端模型参数；客户端传入的
置信度、辨识方法和替代参数都不能提升放行等级。

## 5. 菜单、隐藏路由与权限基线

### 5.1 顶级菜单

| 顺序 | 模块 | 路由前缀 | 角色 |
|---|---|---|---|
| 1 | 工作台 | `/dashboard` | ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR |
| 2 | 回路管理 | `/loop`（另含 `/tag/list`） | ADMIN / IC_ENGINEER / PE_ENGINEER |
| 3 | 性能评估 | `/metric` | ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR |
| 4 | 诊断中心 | `/diagnosis` | 全角色；详情与编辑按子路由收紧 |
| 5 | 回路整定 | `/tuning` | ADMIN / IC_ENGINEER / EXPERT |
| 6 | 系统管理 | `/system` | 当前父路由为 ADMIN / IC_ENGINEER；子路由继续按权限收紧 |

### 5.2 隐藏/兼容路由

| 路由 | 类型 | 兼容目标/用途 |
|---|---|---|
| `/loop/factory` | 隐藏重定向 | `/loop/manage` |
| `/loop/ledger` | 隐藏重定向 | `/loop/manage` |
| `/loop/detail/:id` | 隐藏业务路由 | 回路详情 |
| `/tasks/:taskId` | 隐藏兼容路由 | 评估任务详情 |
| `/diagnosis/detail/:loopId` | 隐藏业务路由 | 诊断详情 |
| `/diagnosis/visualization` | 隐藏兼容路由 | 能力已并入回路分析 |
| `/system/pid-template` | 隐藏重定向 | `/loop/aas-sync` |

这些路由需覆盖直链、SPA 导航、浏览器前进/后退和硬刷新。详情页动态根组件必须
始终向 vben `content.vue` 的 `Transition/KeepAlive` 提供稳定 Element 根节点。

## 6. OpenAPI 基线

2026-07-29 Phase 0 修改前的机械统计：

- 177 个 paths；
- 212 个 HTTP operations；
- 381 个 component schemas。

Phase 0 完成后保存 path + method + request/response schema 的结构化快照并做
breaking-change 检查。允许新增 optional 字段；禁止无兼容层删除 path/method、
把 optional 改 required、收窄既有成功响应或移除既有枚举值。

## 7. `time_constant` 语义

必须区分两个同名概念：

- KPI 快照表的 `time_constant`：当前只有 nullable 持久化列，没有
  MetricCalculator，状态为 `NOT_IMPLEMENTED`；NULL 不能显示成 0，也不能解释为
  “无数据”或“计算结果为零”。
- 诊断慢响应证据中的 `time_constant`：由诊断算法计算，是独立的诊断特征，
  不等同于 KPI 字段。

Phase 0 保持 KPI 列兼容但不补造数值；Phase 1 完成指标语义清单后再决定补算或
兼容废弃。

## 8. PostgreSQL bootstrap

生产首次部署仍采用：

```text
01_schema.sql → 02_seed_data.sql → alembic stamp head
```

Phase 0 将基础 DDL 补齐到 37 张 ORM 表，并用空临时 PostgreSQL 验证结构与 seed。
长期 Alembic-only bootstrap 决策见
`adr-production-postgresql-bootstrap-2026-07-29.md`。

## 9. 安全边界静态门禁

- 不允许出现 DCS 运行时 PID 参数 write/apply/deploy/implement 端点。
- DCS vendor/model/PID structure API 只管理离线适配配置，不连接控制站下写。
- `COMPLETED`、`APPLIED` 或“已实施”文案不能被解释为平台自动下写。
- 所有模型与 PID 建议必须保留来源、算法版本、可信度、reason code 和人工确认
  审计。
