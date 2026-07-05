# 历史数据手工重算功能设计 spec v1.0

> 日期：2026-07-05
> 状态：已确认，待生成实现计划
> 关联：PRD v3.1 §5.3.7 / FDS v5.1 §5.3 / 实现契约 v1.0 §6 / UIUX v5.3

## 1. 背景与目标

### 1.1 现状缺口

CLPM v4.0 KPI 计算体系已支持定时整点评估、自定义评估、CLI 回填脚本，但**缺少通过 Web UI 触发历史数据手工重算的能力**：

- 后端 `backfill_kpi_range` Celery 任务（[kpi_calc.py:349-429](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L349-L429)）已实现完整的按时间窗批量重算逻辑（幂等 UPSERT 覆盖 `kpi_snapshot_hourly` + 级联节点级聚合），但**仅能通过 CLI 脚本 `scripts/backfill_kpi.py` 触发，没有 HTTP API**。
- 前端任务列表页（`/metric/tasks`）已有「触发标准评估」「新建任务」按钮，但**无历史重算入口**。
- 看板页发现数据异常、配置变更后验证效果、数据补传后回填等场景均无法在 UI 内完成。

### 1.2 目标

新增"历史重算"功能，支持：

1. **数据补传/异常修正后回填**：按时间窗批量重算该时段所有回路。
2. **看板发现数据异常后立即重算**：针对当前视图的装置/时间窗重算。
3. **修改指标配置后立即应用新配置**：按新配置重算历史数据验证效果。
4. **针对特定回路精准重算**：只重算指定回路子集，节省资源。

### 1.3 非目标

- 不支持 DCS 参数下写（安全边界，由 PRD §3 约束）。
- 不改变现有定时整点评估、自定义评估任务的语义与表结构。
- 不重构 `kpi_snapshot_hourly` 表的 UPSERT 幂等行为。
- 不改变 `kpi_snapshot_custom` 表的"只查看不参与聚合"语义。

## 2. 关键决策

| 决策项 | 选择 | 理由 |
|---|---|---|
| 覆盖语义 | 默认覆盖标准快照（`kpi_snapshot_hourly`），UPSERT 替换重复 `(loop_id, ts_start)` | 复用 backfill_kpi_range 现有幂等行为；满足"看板立即看到新结果"预期 |
| dry-run 交互 | 两步确认：先调 dry-run 接口返回预览 → 用户确认后再调正式接口提交 | 防误操作触发超大任务；预览影响范围/耗时 |
| 入口位置 | 新建独立页面 `/metric/recompute` | 语义清晰；不污染看板只读视图；与任务列表解耦 |
| 重算粒度 | 时间窗（必选）+ 装置多选（可选）+ 回路多选（可选） | 覆盖全部 4 个场景；不选装置/回路=全量 |
| 权限 | ADMIN + IC_ENGINEER | 与现有 `_TASK_CREATOR_ROLES` 一致；PE_ENGINEER 只读 |
| 任务类型 | 新增 `TaskType.BACKFILL` | 与 STANDARD/CUSTOM 区分；便于列表筛选与统计 |
| 后端实现 | 扩展现有 `backfill_kpi_range` 增加 `loop_ids` 可选参数 | 复用已验证逻辑；最小改动；YAGNI |

## 3. 后端设计

### 3.1 新增 Schema（`app/schemas/task.py`）

```python
class TaskType(StrEnum):
    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"
    BACKFILL = "BACKFILL"          # 新增

class BackfillTaskCreate(CamelModel):
    tsStart: str                              # 必填，ISO 8601
    tsEnd: str                                # 必填
    plantNodeIds: list[str] | None = None     # 可选，装置过滤
    loopIds: list[str] | None = None          # 可选，回路过滤（优先级高于 plantNodeIds）
    dryRun: bool = False                      # True=只返回影响范围不提交

class BackfillPreviewResult(CamelModel):
    loopCount: int                            # 影响回路数
    windowCount: int                          # 影响小时窗口数
    estimatedDurationSec: int                 # 预估耗时（loopCount × windowCount × 2s）
    sampleLoopNames: list[str]                # 前 5 个回路名预览
```

### 3.2 新增 HTTP API（`app/api/v1/endpoints/tasks.py`）

`POST /api/v1/tasks/backfill` — 触发或预览历史重算

**权限**：`ADMIN + IC_ENGINEER`（复用 `_TASK_CREATOR_ROLES`）

**并发限制**：复用 `MAX_CUSTOM_PER_USER=3 / MAX_CUSTOM_SYSTEM=20`（BACKFILL 任务计入同一限额池）

**逻辑**：

```python
async def trigger_backfill(
    body: BackfillTaskCreate,
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiResponse[BackfillPreviewResult | TaskCreateResult]:
    # 1. 校验时间窗：tsStart < tsEnd，且 (tsEnd - tsStart) <= 30 天
    # 2. 解析最终 loop_ids：
    #    - body.loopIds 非空 → 直接使用（校验存在性 + ACTIVE/READY 状态）
    #    - body.loopIds 为空但 plantNodeIds 非空 → 查 LoopLedger 按装置过滤
    #    - 两者都为空 → 查全量 ACTIVE/READY 回路
    # 3. 计算窗口数 = ceil((tsEnd - tsStart) / cycle_minutes)
    # 4. dryRun=True → 返回 BackfillPreviewResult（不触发 Celery）
    # 5. dryRun=False：
    #    a. 并发限制校验（count_active_custom_tasks）
    #    b. TaskTracker.create_task(task_type=BACKFILL, ...)
    #    c. backfill_kpi_range.delay(ts_start, ts_end, loop_ids)
    #    d. 返回 { taskId }
```

### 3.3 扩展 Celery 任务（`app/tasks/kpi_calc.py`）

[backfill_kpi_range](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L349-L429) 增加可选参数：

```python
@celery_app.task(bind=True, base=AsyncTask, name="app.tasks.kpi_calc.backfill_kpi_range")
def backfill_kpi_range(
    self,
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,   # 新增：None=全量，list=仅这些回路
) -> dict:
    # loop_ids=None → 保持原行为（全量 ACTIVE/READY 回路）
    # loop_ids=list → 在 _do_backfill 内部对 loop_ids 做过滤
    # 空列表 → 直接返回 {total_windows: 0, ...}
```

**改动点**：
- `_do_backfill` 内部获取回路列表的逻辑：`if loop_ids is None: loops = query_all_active_loops() else: loops = query_loops_by_ids(loop_ids)`
- 同步更新 [scripts/backfill_kpi.py](file:///Users/zhangping/DEV/CLPM/backend/scripts/backfill_kpi.py#L342-L369) 调用方（新参数可选，向后兼容）

### 3.4 TaskTracker 集成

- `task_type=BACKFILL` 在 Redis Hash 中存储 `tsStart/tsEnd/loopIds/plantNodeIds/loopsTotal`
- 任务完成通知机制复用现有 `_send_notification`
- `GET /api/v1/tasks` 支持 `taskType=BACKFILL` 筛选

### 3.5 路由注册

`app/api/v1/router.py` 无需改动，`tasks.py` 内部新增端点自动注册。

## 4. 前端设计

### 4.1 新增页面 `/metric/recompute`

**文件**：`frontend/apps/web-antd/src/views/metric/recompute.vue`

**页面结构**：

```
┌─────────────────────────────────────────────────┐
│ ClpmPageToolbar                                 │
│  [发起重算] [刷新]                  筛选: ...   │
├─────────────────────────────────────────────────┤
│ 重算记录列表（表格）                            │
│ ┌─────────────────────────────────────────────┐ │
│ │任务ID|时间窗|装置|回路数|状态|进度|创建时间│ │
│ │      |      |    |      |    |    |        │ │
│ └─────────────────────────────────────────────┘ │
│ 筛选: 装置下拉 / 时间范围 / 回路搜索            │
└─────────────────────────────────────────────────┘
```

### 4.2 发起重算 Drawer

点击「发起重算」弹出 Drawer：

| 字段 | 控件 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| 时间窗 | RangePicker | 是 | 近 7 天 | 限制最大 30 天 |
| 装置 | TreeSelect（多选） | 否 | 空=全部 | 按工厂树结构展示 |
| 回路 | Select（多选，支持搜索） | 否 | 空=该装置全部 | 按已选装置过滤；未选装置时为全量 |
| 预览按钮 | Button | — | — | 调 dry-run 接口 |
| 预览卡片 | 静态展示 | — | — | 显示回路数/窗口数/预估耗时/样本回路名 |
| 确认重算 | Button primary | — | — | **disable 直到预览完成**；调正式接口 |

**交互流程**：
1. 用户选时间窗 + 装置 + 回路
2. 点「预览影响范围」→ 调 `triggerBackfillApi({ dryRun: true })` → 显示预览卡片
3. 用户查看预览，点「确认重算」→ 调 `triggerBackfillApi({ dryRun: false })` → 关闭 Drawer，列表新增一行 PENDING 任务
4. 任务完成后自动刷新列表（复用任务通知机制）

### 4.3 重算记录列表

**数据源**：`GET /api/v1/tasks?taskType=BACKFILL`（复用现有列表接口）

**列**：

| 列 | dataIndex | 渲染 |
|---|---|---|
| 任务ID | taskId | font-mono text-xs |
| 时间窗 | tsRange | `{tsStart} ~ {tsEnd}` |
| 装置 | plantNodeNames | Tag 列表（多个用 `,` 拼接，全量显示"全部装置"） |
| 回路数 | loopsTotal | 数字 |
| 状态 | status | Tag + statusColorMap（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED） |
| 进度 | progress | Progress 条 |
| 创建时间 | createdAt | YYYY-MM-DD HH:mm:ss |
| 操作 | action | 详情 / 取消（Popconfirm，仅活跃态） |

**筛选**：
- 装置下拉：`plantNodeIds` query 参数（后端扩展 TaskListQuery 支持）
- 时间范围：`startTime/endTime` query 参数（已存在）
- 回路搜索：前端过滤（按 loopIds 字段匹配）

**行点击**：展开右侧详情抽屉（复用 task/list.vue 的详情抽屉模式）

### 4.4 前端 API 扩展（`api/task.ts`）

```typescript
export interface BackfillTaskCreate {
  tsStart: string;
  tsEnd: string;
  plantNodeIds?: string[];
  loopIds?: string[];
  dryRun?: boolean;
}

export interface BackfillPreviewResult {
  loopCount: number;
  windowCount: number;
  estimatedDurationSec: number;
  sampleLoopNames: string[];
}

export async function triggerBackfillApi(
  data: BackfillTaskCreate,
): Promise<BackfillPreviewResult | { taskId: string }>;

// 扩展 getTaskListApi 支持 taskType=BACKFILL（已支持，无需改动）
// 扩展 TaskListParams 增加 plantNodeIds?: string[]（后端 query 参数）
```

### 4.5 路由注册（`router/routes/modules/metric.ts`）

新增独立菜单项（不在 MetricConfigGroup 内，权限是 ADMIN+IC_ENGINEER 而非仅 ADMIN）：

```typescript
{
  name: 'MetricRecompute',
  path: '/metric/recompute',
  component: () => import('#/views/metric/recompute.vue'),
  meta: {
    authority: ['ADMIN', 'IC_ENGINEER'],
    icon: 'lucide:history',
    title: '历史重算',
  },
},
```

## 5. 关键约束

| 约束 | 值 | 说明 |
|---|---|---|
| 时间窗最大范围 | 30 天 | 防误操作触发超大任务 |
| dry-run 必须先执行 | 是 | 前端「确认重算」按钮 disable 直到预览完成 |
| UPSERT 覆盖 | `kpi_snapshot_hourly` | 复用 backfill_kpi_range 现有幂等行为 |
| 节点级聚合级联 | 自动 | 复用 backfill_kpi_range 内 `calculate_node_kpi_hourly.delay()` |
| 并发限制 | 单用户 ≤3 / 系统 ≤20 | 复用 MAX_CUSTOM_PER_USER/MAX_CUSTOM_SYSTEM（BACKFILL 计入同一池） |
| 权限 | ADMIN + IC_ENGINEER | PE_ENGINEER 只读，SPONSOR/EXPERT 不可见 |
| 回路过滤优先级 | loopIds > plantNodeIds | 同时传时 loopIds 优先 |

## 6. 测试策略

### 6.1 后端单元测试

- `tests/api/v1/test_tasks_backfill.py`：
  - dry-run 返回正确预览（回路数/窗口数/预估耗时/样本回路名）
  - 正式提交创建 BACKFILL 任务并返回 taskId
  - 权限校验（PE_ENGINEER 403）
  - 并发限制校验（超过 MAX_CUSTOM_PER_USER 429）
  - 时间窗校验（>30 天 400）
  - loopIds 优先级高于 plantNodeIds
  - loopIds 不存在时 400
- `tests/tasks/test_backfill_loop_filter.py`：
  - loop_ids=None → 全量回路
  - loop_ids=list → 仅这些回路
  - loop_ids=[] → 空结果
  - 跨小时窗口逐个调用 `_do_calculate`

### 6.2 前端测试

- `views/metric/recompute.vue` 组件测试：
  - Drawer 打开/关闭
  - dry-run 预览渲染
  - 「确认重算」按钮 disable 状态
- E2E `e2e/tests/recompute.spec.ts`：
  - E2E-RECOMPUTE-001: 发起重算 dry-run → 预览 → 取消
  - E2E-RECOMPUTE-002: 重算记录列表筛选（装置/时间/回路）
  - E2E-RECOMPUTE-003: 权限校验（PE_ENGINEER 不可见菜单）

## 7. 文件改动清单

### 后端

| 文件 | 改动 |
|---|---|
| `app/schemas/task.py` | 新增 BackfillTaskCreate / BackfillPreviewResult / TaskType.BACKFILL；扩展 TaskListQuery 增加 plantNodeIds 可选字段 |
| `app/api/v1/endpoints/tasks.py` | 新增 POST /tasks/backfill 端点；GET /tasks 支持 plantNodeIds 筛选 |
| `app/tasks/kpi_calc.py` | backfill_kpi_range 增加 loop_ids 参数；_do_backfill 内部过滤逻辑 |
| `scripts/backfill_kpi.py` | 同步更新调用（兼容新参数，可选） |
| `tests/api/v1/test_tasks_backfill.py` | 新增 |
| `tests/tasks/test_backfill_loop_filter.py` | 新增 |

### 前端

| 文件 | 改动 |
|---|---|
| `src/api/task.ts` | 新增 triggerBackfillApi + 类型定义 |
| `src/views/metric/recompute.vue` | 新建页面 |
| `src/router/routes/modules/metric.ts` | 新增 /metric/recompute 路由 |
| `e2e/tests/recompute.spec.ts` | 新增 E2E 测试 |

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 30 天时间窗 × 全量回路可能产生数千个 Celery 子任务，压垮 worker | dry-run 预览显示预估耗时；并发限制复用 MAX_CUSTOM_SYSTEM=20 |
| 用户误以为重算会立即在看板生效，实际节点级聚合是级联异步 | 预览卡片明确提示"任务完成后自动触发节点级聚合，看板数据将在 N 分钟内更新" |
| backfill_kpi_range 签名变更影响 CLI 脚本 | 新参数可选且默认 None=原行为；同步更新 scripts/backfill_kpi.py |
| BACKFILL 任务计入 CUSTOM 并发池可能挤占自定义评估配额 | 文档说明；后续可考虑独立配额池（v2 优化） |

## 9. 验收标准

1. ADMIN/IC_ENGINEER 可在 `/metric/recompute` 页面发起历史重算
2. 发起重算必须先 dry-run 预览，确认影响范围后再提交
3. 重算任务结果 UPSERT 覆盖 `kpi_snapshot_hourly`，看板/统计报表反映新结果
4. 重算记录列表支持按装置/时间/回路筛选
5. PE_ENGINEER 不可见菜单，SPONSOR/EXPERT 不可访问
6. 时间窗超过 30 天被拒绝
7. 并发超过限制被拒绝
8. 后端单元测试覆盖率 ≥85%
9. E2E 测试 3 个用例全部通过
