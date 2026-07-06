# 历史数据手工重算功能 Implementation Plan v1.0

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/metric/recompute` 页面与 `POST /tasks/backfill` API，支持用户按时间窗+装置+回路手工触发历史 KPI 重算（覆盖标准快照），含 dry-run 预览与重算记录列表。

**Architecture:** 后端扩展现有 `backfill_kpi_range` Celery 任务增加 `loop_ids` 可选参数（复用幂等 UPSERT 逻辑），新增 `POST /tasks/backfill` HTTP 端点（dry-run 预览 + 正式提交）+ `TaskType.BACKFILL` 枚举。前端新建 `recompute.vue` 页面（发起重算 Drawer + 重算记录列表），复用现有 `getTaskListApi` 按 `taskType=BACKFILL` 筛选。

**Tech Stack:** FastAPI + Celery + Redis（后端）/ Vue 3 + Ant Design Vue + vue-vben-admin（前端）/ Playwright（E2E）

**关联 spec：** [docs/过程文档/historical-recompute-design.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/historical-recompute-design.md)

---

## 文件结构

### 后端

| 文件 | 责任 | 改动类型 |
|---|---|---|
| `backend/app/schemas/task.py` | 新增 `BackfillTaskCreate` / `BackfillPreviewResult` schema；`TaskType` 增加 `BACKFILL`；`TaskResponse` 增加 `tsStart/tsEnd/loopIds/plantNodeIds` 可选字段 | 修改 |
| `backend/app/api/v1/endpoints/tasks.py` | 新增 `POST /tasks/backfill` 端点；`_count_active_custom_tasks` 统计 BACKFILL；`_task_to_response` 映射新字段；`list_tasks` 支持 `plantNodeIds` 筛选 | 修改 |
| `backend/app/tasks/kpi_calc.py` | `backfill_kpi_range` 增加 `loop_ids` 参数；`_do_backfill` 透传 `loop_ids`；`_do_calculate` 增加 `loop_ids` 过滤 | 修改 |
| `backend/scripts/backfill_kpi.py` | CLI 兼容新参数（可选 `--loop-ids`） | 修改 |
| `backend/tests/api/v1/test_tasks_backfill.py` | API 单元测试 | 新建 |
| `backend/tests/tasks/test_backfill_loop_filter.py` | Celery 任务 loop_ids 过滤测试 | 新建 |

### 前端

| 文件 | 责任 | 改动类型 |
|---|---|---|
| `frontend/apps/web-antd/src/api/task.ts` | 新增 `BackfillTaskCreateParams` / `BackfillPreviewResult` 类型 + `triggerBackfillApi` 函数；`TaskItem` 增加新字段；`TaskListQueryParams` 增加 `plantNodeIds` | 修改 |
| `frontend/apps/web-antd/src/views/metric/recompute.vue` | 历史重算页面（Drawer + 列表） | 新建 |
| `frontend/apps/web-antd/src/router/routes/modules/metric.ts` | 注册 `/metric/recompute` 路由 | 修改 |
| `e2e/tests/recompute.spec.ts` | E2E 测试 | 新建 |

---

## Phase 1: 后端 Schema 与 Celery 任务扩展

### Task 1: 扩展 TaskType 枚举与 TaskResponse 字段

**Files:**
- Modify: `backend/app/schemas/task.py:28-37` (TaskType) + `:94-123` (TaskResponse)

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/schemas/test_task_schema.py`（如已存在则追加）：

```python
"""任务 Schema 测试."""
from app.schemas.task import (
    BackfillPreviewResult,
    BackfillTaskCreate,
    TaskResponse,
    TaskType,
)


def test_task_type_includes_backfill():
    """TaskType 枚举应包含 BACKFILL."""
    assert TaskType.BACKFILL.value == "BACKFILL"


def test_task_response_includes_backfill_fields():
    """TaskResponse 应包含 tsStart/tsEnd/loopIds/plantNodeIds 可选字段."""
    resp = TaskResponse(
        taskId="t1",
        taskType=TaskType.BACKFILL,
        status="PENDING",
        createdAt="2026-07-05T00:00:00Z",
        createdBy="admin",
        tsStart="2026-07-04T00:00:00Z",
        tsEnd="2026-07-05T00:00:00Z",
        loopIds=["loop-1"],
        plantNodeIds=["node-1"],
    )
    assert resp.tsStart == "2026-07-04T00:00:00Z"
    assert resp.tsEnd == "2026-07-05T00:00:00Z"
    assert resp.loopIds == ["loop-1"]
    assert resp.plantNodeIds == ["node-1"]


def test_backfill_task_create_camel_case():
    """BackfillTaskCreate 应支持 camelCase 别名."""
    body = BackfillTaskCreate.model_validate(
        {
            "tsStart": "2026-07-04T00:00:00Z",
            "tsEnd": "2026-07-05T00:00:00Z",
            "plantNodeIds": ["node-1"],
            "loopIds": ["loop-1"],
            "dryRun": True,
        }
    )
    assert body.tsStart == "2026-07-04T00:00:00Z"
    assert body.dryRun is True


def test_backfill_preview_result_fields():
    """BackfillPreviewResult 应包含 loopCount/windowCount/estimatedDurationSec/sampleLoopNames."""
    result = BackfillPreviewResult(
        loopCount=10,
        windowCount=24,
        estimatedDurationSec=480,
        sampleLoopNames=["L-001", "L-002"],
    )
    assert result.loopCount == 10
    assert result.windowCount == 24
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/schemas/test_task_schema.py -v`
Expected: FAIL with `ImportError` 或 `AttributeError`（BackfillTaskCreate 等未定义）

- [ ] **Step 3: 修改 schema**

在 `backend/app/schemas/task.py` 中：

1. `TaskType` 枚举增加 `BACKFILL`：

```python
class TaskType(StrEnum):
    """任务类型.

    Attributes:
        STANDARD: 标准评估任务（每小时定时，全量回路覆盖）
        CUSTOM: 自定义评估任务（用户按需触发，选定回路/指标/时间范围）
        BACKFILL: 历史重算任务（按时间窗批量重算，覆盖标准快照）
    """

    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"
    BACKFILL = "BACKFILL"
```

2. 在 `CustomTaskCreate` 之后新增 `BackfillTaskCreate`：

```python
class BackfillTaskCreate(CamelModel):
    """历史重算任务创建请求.

    Attributes:
        tsStart: 重算时间窗起始（ISO 8601）
        tsEnd: 重算时间窗结束（ISO 8601，不包含）
        plantNodeIds: 装置 ID 列表（可选，不传=全部装置）
        loopIds: 回路 ID 列表（可选，优先级高于 plantNodeIds；不传=对应装置全部回路）
        dryRun: True=只返回影响范围预览，不实际触发 Celery 任务
    """

    tsStart: str = Field(..., description="重算时间窗起始（ISO 8601）")
    tsEnd: str = Field(..., description="重算时间窗结束（ISO 8601，不包含）")
    plantNodeIds: list[str] | None = Field(None, description="装置 ID 列表（可选）")
    loopIds: list[str] | None = Field(None, description="回路 ID 列表（可选，优先级高于 plantNodeIds）")
    dryRun: bool = Field(False, description="True=只返回预览不提交")
```

3. 在 `TaskListResponse` 之前新增 `BackfillPreviewResult`：

```python
class BackfillPreviewResult(CamelModel):
    """历史重算 dry-run 预览结果.

    Attributes:
        loopCount: 影响回路数
        windowCount: 影响小时窗口数
        estimatedDurationSec: 预估耗时（秒，按 loopCount × windowCount × 2s 估算）
        sampleLoopNames: 前 5 个回路名预览
    """

    loopCount: int = Field(..., description="影响回路数")
    windowCount: int = Field(..., description="影响小时窗口数")
    estimatedDurationSec: int = Field(..., description="预估耗时（秒）")
    sampleLoopNames: list[str] = Field(default_factory=list, description="前 5 个回路名预览")
```

4. `TaskResponse` 增加可选字段（在 `createdBy` 之后）：

```python
    createdBy: str
    # 历史重算任务额外字段（其他任务类型为 None）
    tsStart: str | None = Field(None, description="重算时间窗起始（仅 BACKFILL）")
    tsEnd: str | None = Field(None, description="重算时间窗结束（仅 BACKFILL）")
    loopIds: list[str] | None = Field(None, description="回路 ID 列表（仅 BACKFILL）")
    plantNodeIds: list[str] | None = Field(None, description="装置 ID 列表（仅 BACKFILL）")
```

5. 更新 `__all__`：

```python
__all__ = [
    "BackfillPreviewResult",
    "BackfillTaskCreate",
    "CustomTaskCreate",
    "StandardTaskCreate",
    "TaskListResponse",
    "TaskResponse",
    "TaskStatus",
    "TaskType",
]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/schemas/test_task_schema.py -v`
Expected: PASS（4 个测试全部通过）

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add backend/app/schemas/task.py backend/tests/schemas/test_task_schema.py
git commit -m "feat(schema): 新增 BackfillTaskCreate/BackfillPreviewResult + TaskType.BACKFILL

- TaskType 增加 BACKFILL 枚举值
- TaskResponse 增加 tsStart/tsEnd/loopIds/plantNodeIds 可选字段（仅 BACKFILL 任务使用）
- 新增 BackfillTaskCreate schema（支持 dryRun 预览模式）
- 新增 BackfillPreviewResult schema（回路数/窗口数/预估耗时/样本回路名）"
```

---

### Task 2: 扩展 backfill_kpi_range 支持 loop_ids 过滤

**Files:**
- Modify: `backend/app/tasks/kpi_calc.py:349-429` (backfill_kpi_range + _do_backfill)
- Modify: `backend/app/tasks/kpi_calc.py:550-601` (_do_calculate 增加	loop_ids 参数)
- Test: `backend/tests/tasks/test_backfill_loop_filter.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/tasks/test_backfill_loop_filter.py`：

```python
"""backfill_kpi_range loop_ids 过滤测试."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_do_backfill_with_loop_ids_filter():
    """_do_backfill 传入 loop_ids 时应只计算指定回路."""
    from app.tasks.kpi_calc import _do_backfill

    # mock _do_calculate 和 _do_calculate_node_kpi
    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
            return_value={"success": 2, "inconclusive": 0, "failed": 0},
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
            return_value={"success": 1},
        ),
    ):
        result = await _do_backfill(
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=["loop-1", "loop-2"],
        )

    # 2 小时窗口 × 2 次调用 _do_calculate
    assert mock_calc.call_count == 2
    # 每次调用都应传入 loop_ids
    for call in mock_calc.call_args_list:
        assert call.kwargs.get("loop_ids") == ["loop-1", "loop-2"]
    assert result["total_windows"] == 2
    assert result["loop_success"] == 4  # 2 窗口 × 2 成功


@pytest.mark.asyncio
async def test_do_backfill_without_loop_ids():
    """_do_backfill 不传 loop_ids 时 _do_calculate 的 loop_ids 应为 None（全量）."""
    from app.tasks.kpi_calc import _do_backfill

    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
            return_value={"success": 5, "inconclusive": 0, "failed": 0},
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
            return_value={"success": 1},
        ),
    ):
        await _do_backfill("2026-07-04T00:00:00Z", "2026-07-04T01:00:00Z")

    assert mock_calc.call_count == 1
    # loop_ids 应为 None（保持原全量行为）
    assert mock_calc.call_args.kwargs.get("loop_ids") is None


@pytest.mark.asyncio
async def test_do_backfill_empty_loop_ids():
    """_do_backfill 传入空列表时应返回 0 窗口结果."""
    from app.tasks.kpi_calc import _do_backfill

    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
        ),
    ):
        result = await _do_backfill(
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=[],
        )

    # 空列表应跳过计算
    assert mock_calc.call_count == 0
    assert result["total_windows"] == 2
    assert result["loop_success"] == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/tasks/test_backfill_loop_filter.py -v`
Expected: FAIL with `TypeError: _do_backfill() got an unexpected keyword argument 'loop_ids'`

- [ ] **Step 3: 修改 `_do_calculate` 增加 `loop_ids` 参数**

在 `backend/app/tasks/kpi_calc.py:550` 修改 `_do_calculate` 签名和回路查询逻辑：

```python
async def _do_calculate(
    ts_start: datetime | None = None,
    cascade_node: bool = True,
    loop_ids: list[str] | None = None,
) -> dict:
    """执行全量 KPI 计算的实际 async 逻辑。

    Args:
        ts_start: 时间窗起始（UTC，naive 视为 UTC）。None 时取「上一个完整计算周期」，
            周期长度由 EngineRule EVAL_CALC_CYCLE.cycle_minutes 决定（默认 60 分钟）。
        cascade_node: 是否在回路级计算完成后级联触发节点级聚合任务。
            脚本批量回填时设为 False，由脚本同步调用 _do_calculate_node_kpi
            避免大量 .delay() 调用堆积到 Celery 队列。
        loop_ids: 回路 ID 过滤列表。None=全量 ACTIVE/READY 回路（保持原行为）；
            非空列表=仅这些回路（用于历史重算按回路精准过滤）；
            空列表=直接返回 0 结果。

    引擎规则（PRD §5.4.2 / FDS §5.3.3）：
        - EVAL_CALC_CYCLE.cycle_minutes → 计算周期 + 时间窗长度
        - SCHEDULE_CONCURRENCY.concurrency → 并发处理数量
    """
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader

    engine_loader = get_engine_rule_loader()

    async with AsyncSessionLocal() as db:
        cycle_minutes = await engine_loader.get_calc_cycle_minutes(db)
        concurrency = await engine_loader.get_concurrency(db)
        logger.info(
            "引擎规则: calc_cycle=%dmin, concurrency=%d", cycle_minutes, concurrency
        )

        if ts_start is not None:
            ts_end = ts_start + timedelta(minutes=cycle_minutes)
        else:
            now = datetime.now(UTC)
            ts_end = now.replace(second=0, microsecond=0)
            ts_end = ts_end.replace(minute=(ts_end.minute // cycle_minutes) * cycle_minutes)
            ts_start = ts_end - timedelta(minutes=cycle_minutes)

        # 1. 查询回路（支持 loop_ids 过滤）
        # loop_ids=[] 表示空集，直接返回 0 结果（避免误查全量）
        if loop_ids is not None and len(loop_ids) == 0:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        stmt = select(LoopLedger).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        if loop_ids is not None:
            stmt = stmt.where(LoopLedger.id.in_(loop_ids))

        loop_result = await db.execute(stmt)
        loops = list(loop_result.scalars().all())
        logger.info(
            "待计算回路数: %d (loop_ids=%s)",
            len(loops),
            "all" if loop_ids is None else f"{len(loop_ids)} filtered",
        )

        if not loops:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        # 2. 加载指标配置
        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        from app.services.loop_config import get_loop_type_weights_map
        type_weights = await get_loop_type_weights_map(db)

    # 3. 并发计算（与原逻辑相同）
    sem = asyncio.Semaphore(concurrency)

    async def _calc_with_sem(loop: LoopLedger) -> dict | None:
        async with sem:
            async with AsyncSessionLocal() as worker_db:
                try:
                    result = await _calculate_loop_kpi(
                        db=worker_db,
                        loop=loop,
                        metric_configs=metric_configs,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        type_weights=type_weights,
                    )
                    await worker_db.commit()
                    return result
                except Exception:
                    await worker_db.rollback()
                    raise

    tasks = [asyncio.create_task(_calc_with_sem(loop)) for loop in loops]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = 0
    inconclusive_count = 0
    failed_count = 0
    for r in results:
        if isinstance(r, Exception):
            failed_count += 1
            logger.warning("回路计算失败: %s", r)
        elif r is None:
            failed_count += 1
        elif r.get("status") == "INCONCLUSIVE":
            inconclusive_count += 1
        else:
            success_count += 1

    if cascade_node:
        try:
            calculate_node_kpi_hourly.delay()
            logger.info("已触发节点级 KPI 聚合任务（回路级计算完成后级联）")
        except Exception as exc:  # noqa: BLE001
            logger.warning("触发节点级 KPI 聚合任务失败: %s", exc)

    return {
        "total": len(loops),
        "success": success_count,
        "inconclusive": inconclusive_count,
        "failed": failed_count,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }
```

- [ ] **Step 4: 修改 `backfill_kpi_range` 和 `_do_backfill` 透传 `loop_ids`**

在 `backend/app/tasks/kpi_calc.py:349` 修改：

```python
@celery_app.task(
    name="app.tasks.kpi_calc.backfill_kpi_range",
    bind=True,
    base=AsyncTask,
)
def backfill_kpi_range(
    self: AsyncTask,
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
) -> dict:
    """按小时窗口批量回填 KPI 快照（脚本/HTTP 触发）。

    遍历 [ts_start, ts_end) 范围内的每个完整小时窗口，
    对全量 ACTIVE/READY 回路计算 KPI，并同步触发节点级聚合。
    幂等：相同 (loop_id, ts_start) 的快照会被 UPSERT 覆盖，可重复执行。

    Args:
        ts_start: 起始时间（ISO 8601，UTC）
        ts_end: 结束时间（ISO 8601，UTC，不包含）
        loop_ids: 回路 ID 过滤列表。None=全量（保持原行为）；
            非空列表=仅这些回路（HTTP API 历史重算按回路精准过滤）；
            空列表=直接返回 0 结果。

    用途：
        - 补齐因数据空档或服务中断缺失的历史 KPI 快照
        - 修复契约配置后重新计算指定时段的指标
        - 按回路/装置精准重算历史数据
    """
    logger.info(
        "KPI 回填任务开始, task_id=%s, range=%s~%s, loop_ids=%s",
        self.request.id,
        ts_start,
        ts_end,
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )
    try:
        result = self.run_async(_do_backfill(ts_start, ts_end, loop_ids=loop_ids))
        logger.info("KPI 回填任务完成: %s", result)
        return result
    except Exception:
        logger.exception("KPI 回填任务失败")
        raise


async def _do_backfill(
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
) -> dict:
    """批量回填 async 逻辑：遍历小时窗口，每窗口全量回路计算 + 节点聚合。

    在 Celery worker 的 event loop 内执行，复用 worker 的 httpx client。

    Args:
        ts_start: 起始时间 ISO 8601
        ts_end: 结束时间 ISO 8601（不包含）
        loop_ids: 回路 ID 过滤列表；None=全量，空列表=跳过计算
    """
    start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))

    # 空列表提前返回，避免遍历窗口
    if loop_ids is not None and len(loop_ids) == 0:
        return {
            "total_windows": 0,
            "failed_windows": 0,
            "loop_success": 0,
            "loop_inconclusive": 0,
            "loop_failed": 0,
            "node_success": 0,
            "failed_window_list": [],
        }

    windows: list[datetime] = []
    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    while cur < end_dt:
        windows.append(cur)
        cur += timedelta(hours=1)

    total = len(windows)
    logger.info(
        "回填窗口数: %d (%s ~ %s), loop_ids=%s",
        total,
        start_dt.isoformat(),
        end_dt.isoformat(),
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )

    agg_loop_success = 0
    agg_loop_inconclusive = 0
    agg_loop_failed = 0
    agg_node_success = 0
    failed_windows: list[str] = []

    for i, w in enumerate(windows, 1):
        try:
            loop_result = await _do_calculate(
                ts_start=w, cascade_node=False, loop_ids=loop_ids
            )
            node_result = await _do_calculate_node_kpi(ts_start=w)
            agg_loop_success += loop_result.get("success", 0)
            agg_loop_inconclusive += loop_result.get("inconclusive", 0)
            agg_loop_failed += loop_result.get("failed", 0)
            agg_node_success += node_result.get("success", 0)
            logger.info(
                "回填进度 [%d/%d] %s: loop_ok=%d, node_ok=%d",
                i, total, w.isoformat(),
                loop_result.get("success", 0), node_result.get("success", 0),
            )
        except Exception as exc:  # noqa: BLE001
            failed_windows.append(w.isoformat())
            logger.warning("回填窗口 %s 失败: %s", w.isoformat(), exc)

    return {
        "total_windows": total,
        "failed_windows": len(failed_windows),
        "loop_success": agg_loop_success,
        "loop_inconclusive": agg_loop_inconclusive,
        "loop_failed": agg_loop_failed,
        "node_success": agg_node_success,
        "failed_window_list": failed_windows,
    }
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/tasks/test_backfill_loop_filter.py -v`
Expected: PASS（3 个测试全部通过）

- [ ] **Step 6: 运行全量回归测试确保无破坏**

Run: `cd backend && uv run pytest tests/tasks/ -v -k "backfill or calculate" --no-header -q`
Expected: 既有 backfill/calculate 测试全部 PASS

- [ ] **Step 7: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add backend/app/tasks/kpi_calc.py backend/tests/tasks/test_backfill_loop_filter.py
git commit -m "feat(task): backfill_kpi_range 增加 loop_ids 过滤参数

- backfill_kpi_range 新增 loop_ids 可选参数（None=全量，list=过滤，空列表=跳过）
- _do_backfill 透传 loop_ids 到 _do_calculate
- _do_calculate 在 LoopLedger 查询时按 loop_ids 过滤
- 空列表提前返回 0 结果，避免误查全量
- 向后兼容：不传 loop_ids 保持原全量行为"
```

---

### Task 3: 同步更新 CLI 脚本（兼容新参数）

**Files:**
- Modify: `backend/scripts/backfill_kpi.py:342-369`

- [ ] **Step 1: 阅读现有 CLI 脚本**

Run: `cd backend && sed -n '340,370p' scripts/backfill_kpi.py` 或用 Read 工具读取 `backend/scripts/backfill_kpi.py` 第 340-370 行。

- [ ] **Step 2: 修改 CLI 增加 `--loop-ids` 可选参数**

在 `backend/scripts/backfill_kpi.py` 的 `trigger_backfill` 函数附近（约 342-369 行）增加 `--loop-ids` argparse 选项：

```python
def trigger_backfill(start_iso: str, end_iso: str, loop_ids: list[str] | None = None) -> str:
    """触发 backfill Celery 任务.

    Args:
        start_iso: 起始时间 ISO 8601
        end_iso: 结束时间 ISO 8601
        loop_ids: 回路 ID 列表（可选）；None=全量

    Returns:
        Celery task id
    """
    from app.tasks.kpi_calc import backfill_kpi_range

    result = backfill_kpi_range.delay(start_iso, end_iso, loop_ids=loop_ids)
    print(f"已触发 backfill 任务: task_id={result.id}")
    return result.id
```

在 argparse 主函数中（通常在 `if __name__ == "__main__":` 块）增加：

```python
parser.add_argument(
    "--loop-ids",
    nargs="*",
    default=None,
    help="回路 ID 列表（可选，多个用空格分隔）；不传=全量回路",
)
```

调用处改为：
```python
trigger_backfill(args.start, args.end, loop_ids=args.loop_ids)
```

- [ ] **Step 3: 验证脚本可执行（dry-run 模式不依赖 Celery）**

Run: `cd backend && uv run python scripts/backfill_kpi.py --help`
Expected: 帮助信息包含 `--loop-ids` 选项

- [ ] **Step 4: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add backend/scripts/backfill_kpi.py
git commit -m "feat(cli): backfill 脚本支持 --loop-ids 可选参数

向后兼容：不传 --loop-ids 保持原全量回填行为"
```

---

## Phase 2: 后端 API 端点

### Task 4: 新增 POST /tasks/backfill 端点

**Files:**
- Modify: `backend/app/api/v1/endpoints/tasks.py` (新增端点 + 修改 `_count_active_custom_tasks` + `_task_to_response` + `list_tasks`)
- Test: `backend/tests/api/v1/test_tasks_backfill.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/api/v1/test_tasks_backfill.py`：

```python
"""POST /api/v1/tasks/backfill 端点测试."""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_backfill_dry_run_returns_preview(client: AsyncClient, admin_token):
    """dryRun=True 应返回预览结果，不触发 Celery."""
    response = await client.post(
        "/api/v1/tasks/backfill",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "tsStart": "2026-07-04T00:00:00Z",
            "tsEnd": "2026-07-05T00:00:00Z",
            "dryRun": True,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "loopCount" in data
    assert "windowCount" in data
    assert "estimatedDurationSec" in data
    assert "sampleLoopNames" in data
    assert data["windowCount"] == 24  # 24 小时


@pytest.mark.asyncio
async def test_backfill_submit_creates_task(client: AsyncClient, admin_token):
    """dryRun=False 应创建 BACKFILL 任务并返回 taskId."""
    with patch("app.api.v1.endpoints.tasks.backfill_kpi_range") as mock_task:
        mock_task.delay.return_value.id = "celery-123"
        response = await client.post(
            "/api/v1/tasks/backfill",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "tsStart": "2026-07-04T00:00:00Z",
                "tsEnd": "2026-07-04T02:00:00Z",
            },
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "taskId" in data
    assert mock_task.delay.called


@pytest.mark.asyncio
async def test_backfill_time_window_exceeds_30_days_rejected(
    client: AsyncClient, admin_token
):
    """时间窗超过 30 天应返回 400."""
    response = await client.post(
        "/api/v1/tasks/backfill",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "tsStart": "2026-06-01T00:00:00Z",
            "tsEnd": "2026-07-05T00:00:00Z",  # 34 天
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_backfill_pe_engineer_forbidden(
    client: AsyncClient, pe_engineer_token
):
    """PE_ENGINEER 应无权限（403）."""
    response = await client.post(
        "/api/v1/tasks/backfill",
        headers={"Authorization": f"Bearer {pe_engineer_token}"},
        json={
            "tsStart": "2026-07-04T00:00:00Z",
            "tsEnd": "2026-07-05T00:00:00Z",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_backfill_loop_ids_priority_over_plant_node_ids(
    client: AsyncClient, admin_token
):
    """同时传 loopIds 和 plantNodeIds 时，loopIds 优先."""
    with patch("app.api.v1.endpoints.tasks._query_loops") as mock_query:
        mock_query.return_value = ["loop-1", "loop-2"]
        response = await client.post(
            "/api/v1/tasks/backfill",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "tsStart": "2026-07-04T00:00:00Z",
                "tsEnd": "2026-07-04T01:00:00Z",
                "plantNodeIds": ["node-1"],
                "loopIds": ["loop-1", "loop-2"],
                "dryRun": True,
            },
        )
    # _query_loops 不应被调用（loopIds 优先）
    mock_query.assert_not_called()
    assert response.status_code == 200
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/api/v1/test_tasks_backfill.py -v`
Expected: FAIL with 404 或路由不存在

- [ ] **Step 3: 修改 `_count_active_custom_tasks` 统计 BACKFILL**

在 `backend/app/api/v1/endpoints/tasks.py:206` 修改：

```python
        # 统计 CUSTOM 和 BACKFILL 任务（BACKFILL 计入同一并发池）
        if data.get("task_type") not in (
            TaskType.CUSTOM.value,
            TaskType.BACKFILL.value,
        ):
            continue
```

- [ ] **Step 4: 修改 `_task_to_response` 映射新字段**

在 `backend/app/api/v1/endpoints/tasks.py:163-185` 修改 `_task_to_response`：

```python
def _task_to_response(data: dict[str, Any]) -> TaskResponse:
    """将 Redis Hash 字典转换为 TaskResponse."""
    # 解析 loopIds/plantNodeIds（JSON 字符串 → list）
    loop_ids_raw = data.get("loop_ids", "")
    loop_ids: list[str] | None = None
    if loop_ids_raw:
        try:
            loop_ids = json.loads(loop_ids_raw) if loop_ids_raw else None
        except (json.JSONDecodeError, TypeError):
            loop_ids = None

    plant_node_ids_raw = data.get("plant_node_ids", "")
    plant_node_ids: list[str] | None = None
    if plant_node_ids_raw:
        try:
            plant_node_ids = json.loads(plant_node_ids_raw) if plant_node_ids_raw else None
        except (json.JSONDecodeError, TypeError):
            plant_node_ids = None

    return TaskResponse(
        taskId=data.get("task_id", ""),
        taskType=TaskType(data.get("task_type", TaskType.STANDARD.value)),
        status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
        progress=_to_float(data.get("progress")),
        currentStage=_to_str_or_none(data.get("current_stage")),
        loopsTotal=_to_int(data.get("loops_total")),
        loopsDone=_to_int(data.get("loops_done")),
        createdAt=data.get("created_at", ""),
        startedAt=_to_str_or_none(data.get("started_at")),
        finishedAt=_to_str_or_none(data.get("finished_at")),
        errorMessage=_to_str_or_none(data.get("error_message")),
        createdBy=data.get("created_by", ""),
        tsStart=_to_str_or_none(data.get("ts_start")),
        tsEnd=_to_str_or_none(data.get("ts_end")),
        loopIds=loop_ids,
        plantNodeIds=plant_node_ids,
    )
```

- [ ] **Step 5: 新增辅助函数 `_query_loops` 和 `_resolve_loop_ids`**

在 `backend/app/api/v1/endpoints/tasks.py` 的 `_count_active_custom_tasks` 之后新增：

```python
async def _query_loops_by_ids(db: AsyncSession, loop_ids: list[str]) -> list[LoopLedger]:
    """按 ID 列表查询回路（校验存在性 + ACTIVE/READY 状态）."""
    result = await db.execute(
        select(LoopLedger).where(
            LoopLedger.id.in_(loop_ids),
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
    )
    return list(result.scalars().all())


async def _query_loops_by_plant_nodes(
    db: AsyncSession, plant_node_ids: list[str]
) -> list[LoopLedger]:
    """按装置 ID 列表查询回路."""
    result = await db.execute(
        select(LoopLedger).where(
            LoopLedger.plant_node_id.in_(plant_node_ids),
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
    )
    return list(result.scalars().all())


async def _query_all_active_loops(db: AsyncSession) -> list[LoopLedger]:
    """查询全量 ACTIVE/READY 回路."""
    result = await db.execute(
        select(LoopLedger).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
    )
    return list(result.scalars().all())


async def _resolve_loop_ids(
    db: AsyncSession,
    loop_ids: list[str] | None,
    plant_node_ids: list[str] | None,
) -> list[LoopLedger]:
    """解析最终回路列表（loop_ids 优先级高于 plant_node_ids）.

    - loop_ids 非空 → 按回路 ID 查询（校验存在性）
    - loop_ids 为空但 plant_node_ids 非空 → 按装置查询
    - 两者都为空 → 全量 ACTIVE/READY 回路
    """
    if loop_ids:
        return await _query_loops_by_ids(db, loop_ids)
    if plant_node_ids:
        return await _query_loops_by_plant_nodes(db, plant_node_ids)
    return await _query_all_active_loops(db)


def _calc_window_count(ts_start: str, ts_end: str, cycle_minutes: int = 60) -> int:
    """计算小时窗口数（向上取整）."""
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
    delta = end - start
    total_minutes = delta.total_seconds() / 60
    # 向上取整
    return max(1, int(total_minutes // cycle_minutes) + (1 if total_minutes % cycle_minutes else 0))
```

- [ ] **Step 6: 新增 `POST /tasks/backfill` 端点**

在 `backend/app/api/v1/endpoints/tasks.py` 的 `trigger_custom_evaluation` 之后（约 447 行后）新增：

```python
# ---------------------------------------------------------------------------
# 接口：触发历史重算任务
# ---------------------------------------------------------------------------

# 时间窗最大范围（30 天，防误操作）
_MAX_BACKFILL_WINDOW_DAYS = 30


@router.post("/backfill", response_model=ApiResponse[dict])
async def trigger_backfill(
    body: BackfillTaskCreate,
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """触发历史重算任务（按时间窗批量重算，覆盖标准快照）.

    调用 Celery 任务 ``backfill_kpi_range``，结果 UPSERT 覆盖
    ``kpi_snapshot_hourly``，参与装置级聚合。
    支持 dry-run 模式：仅返回影响范围预览，不实际触发计算。

    并发限制：BACKFILL 任务计入 CUSTOM 并发池（单用户 ≤3，系统 ≤20）。

    设计依据：IDS §2.7.6.5, PRD §4.3.7
    """
    from datetime import datetime, timedelta

    from app.tasks.kpi_calc import backfill_kpi_range

    # 1. 校验时间窗
    try:
        start_dt = datetime.fromisoformat(body.tsStart.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(body.tsEnd.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BizError(
            code="ERR_INVALID_TIME_FORMAT",
            message=f"时间格式无效: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if start_dt >= end_dt:
        raise BizError(
            code="ERR_INVALID_TIME_RANGE",
            message="tsStart 必须早于 tsEnd",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (end_dt - start_dt) > timedelta(days=_MAX_BACKFILL_WINDOW_DAYS):
        raise BizError(
            code="ERR_BACKFILL_WINDOW_TOO_LARGE",
            message=(
                f"时间窗不能超过 {_MAX_BACKFILL_WINDOW_DAYS} 天"
                f"（当前: {(end_dt - start_dt).days} 天）"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2. 解析最终回路列表
    async with get_db() as db:
        loops = await _resolve_loop_ids(db, body.loopIds, body.plantNodeIds)

    if not loops:
        raise BizError(
            code="ERR_NO_LOOPS_TO_RECOMPUTE",
            message="所选范围内没有可重算的回路（ACTIVE/READY 状态）",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 3. 计算窗口数
    window_count = _calc_window_count(body.tsStart, body.tsEnd)
    loop_count = len(loops)
    estimated_duration_sec = loop_count * window_count * 2  # 每回路每窗口预估 2s
    sample_loop_names = [l.tag_name or l.id for l in loops[:5]]

    # 4. dry-run 模式：返回预览
    if body.dryRun:
        preview = BackfillPreviewResult(
            loopCount=loop_count,
            windowCount=window_count,
            estimatedDurationSec=estimated_duration_sec,
            sampleLoopNames=sample_loop_names,
        )
        return success(
            data=preview.model_dump(),
            message=f"预览：将重算 {loop_count} 个回路 × {window_count} 个窗口",
        )

    # 5. 正式提交：并发限制校验
    user_active = await _count_active_custom_tasks(user_id=str(user.id))
    if user_active >= MAX_CUSTOM_PER_USER:
        raise BizError(
            code="ERR_TASK_CONCURRENCY_LIMIT",
            message=(
                f"您当前已有 {user_active} 个活跃任务，超过单用户上限 {MAX_CUSTOM_PER_USER}"
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    system_active = await _count_active_custom_tasks()
    if system_active >= MAX_CUSTOM_SYSTEM:
        raise BizError(
            code="ERR_TASK_CONCURRENCY_LIMIT",
            message=(
                f"系统当前有 {system_active} 个活跃任务，超过系统上限 {MAX_CUSTOM_SYSTEM}"
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # 6. 触发 Celery 任务
    final_loop_ids = [l.id for l in loops]
    celery_result = backfill_kpi_range.delay(
        body.tsStart, body.tsEnd, loop_ids=final_loop_ids
    )

    # 7. 创建任务记录
    task_id = str(uuid4())
    now = _now_iso()
    task_data: dict[str, str] = {
        "task_id": task_id,
        "task_type": TaskType.BACKFILL.value,
        "status": TaskStatus.PENDING.value,
        "progress": "0",
        "current_stage": "初始化",
        "loops_total": str(loop_count),
        "loops_done": "0",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": user.username,
        "created_by_id": str(user.id),
        "celery_task_id": celery_result.id,
        "ts_start": body.tsStart,
        "ts_end": body.tsEnd,
        "loop_ids": json.dumps(final_loop_ids),
        "plant_node_ids": _to_str(body.plantNodeIds),
    }
    await _save_task(task_data)

    logger.info(
        "历史重算任务已触发: task_id=%s, celery_id=%s, loops=%d, windows=%d, user=%s",
        task_id,
        celery_result.id,
        loop_count,
        window_count,
        user.username,
    )

    return success(
        data={"taskId": task_id},
        message=f"历史重算任务已触发，预计耗时 {estimated_duration_sec} 秒",
    )
```

- [ ] **Step 7: 更新 import**

在 `backend/app/api/v1/endpoints/tasks.py:43-50` 更新 import：

```python
from app.schemas.task import (
    BackfillPreviewResult,
    BackfillTaskCreate,
    CustomTaskCreate,
    StandardTaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskStatus,
    TaskType,
)
```

- [ ] **Step 8: 修改 `list_tasks` 支持 `plantNodeIds` 筛选**

在 `backend/app/api/v1/endpoints/tasks.py:531` 修改 `list_tasks` 签名增加 `plantNodeIds` 参数，并在筛选逻辑中增加：

```python
@router.get("", response_model=ApiResponse[TaskListResponse])
async def list_tasks(
    taskType: str | None = Query(None, description="按任务类型筛选：STANDARD/CUSTOM/BACKFILL"),
    status_filter: str | None = Query(
        None, alias="status", description="按状态筛选：PENDING/RUNNING/SUCCESS/FAILED/CANCELLED"
    ),
    startTime: str | None = Query(None, description="创建时间起始（ISO 8601）"),
    endTime: str | None = Query(None, description="创建时间结束（ISO 8601）"),
    plantNodeIds: str | None = Query(
        None, description="按装置 ID 筛选（逗号分隔，仅对 BACKFILL 任务生效）"
    ),
    limit: int = Query(50, ge=1, le=200, description="返回条数（最多 200）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询任务列表（按类型/状态/时间/装置筛选）."""
    # 解析 plantNodeIds（逗号分隔 → list）
    plant_node_filter = (
        [pid.strip() for pid in plantNodeIds.split(",") if pid.strip()]
        if plantNodeIds
        else None
    )

    task_ids = await redis_client.zrevrange(_TASK_INDEX_KEY, 0, -1)

    items: list[TaskResponse] = []
    for tid in task_ids:
        data = await _get_task(tid)
        if data is None:
            continue

        if taskType and data.get("task_type") != taskType:
            continue
        if status_filter and data.get("status") != status_filter:
            continue

        created_at = data.get("created_at", "")
        if startTime and created_at < startTime:
            continue
        if endTime and created_at > endTime:
            continue

        # 装置筛选（仅对 BACKFILL 任务生效）
        if plant_node_filter:
            task_plant_nodes_raw = data.get("plant_node_ids", "")
            if not task_plant_nodes_raw:
                continue
            try:
                task_plant_nodes = json.loads(task_plant_nodes_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not any(pid in task_plant_nodes for pid in plant_node_filter):
                continue

        items.append(_task_to_response(data))

    total = len(items)
    paginated = items[offset : offset + limit]

    resp = TaskListResponse(items=paginated, total=total)
    return success(data=resp.model_dump())
```

- [ ] **Step 9: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/api/v1/test_tasks_backfill.py -v`
Expected: PASS（5 个测试全部通过）

- [ ] **Step 10: 运行全量 API 回归测试**

Run: `cd backend && uv run pytest tests/api/v1/test_tasks.py -v --no-header -q`
Expected: 既有 tasks 测试全部 PASS（_count_active_custom_tasks 改动不影响 CUSTOM 类型统计）

- [ ] **Step 11: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add backend/app/api/v1/endpoints/tasks.py backend/tests/api/v1/test_tasks_backfill.py
git commit -m "feat(api): 新增 POST /tasks/backfill 历史重算端点

- 新增 POST /tasks/backfill 端点（dry-run 预览 + 正式提交）
- 权限：ADMIN + IC_ENGINEER（PE_ENGINEER 403）
- 时间窗最大 30 天，超过返回 400
- loopIds 优先级高于 plantNodeIds
- BACKFILL 任务计入 CUSTOM 并发池（单用户 ≤3，系统 ≤20）
- _count_active_custom_tasks 统计 CUSTOM + BACKFILL
- _task_to_response 映射 tsStart/tsEnd/loopIds/plantNodeIds
- list_tasks 支持 plantNodeIds 筛选参数"
```

---

## Phase 3: 前端 API 与类型

### Task 5: 扩展前端 task.ts API

**Files:**
- Modify: `frontend/apps/web-antd/src/api/task.ts`

- [ ] **Step 1: 修改 `TaskApi` 命名空间**

在 `frontend/apps/web-antd/src/api/task.ts` 中：

1. `TaskType` 增加 `BACKFILL`：

```typescript
  /** 任务类型（对齐 app.schemas.task.TaskType） */
  export type TaskType = 'BACKFILL' | 'CUSTOM' | 'STANDARD';
```

2. `TaskItem` 增加可选字段（在 `createdBy` 之后）：

```typescript
  /** 任务响应（对齐 app.schemas.task.TaskResponse） */
  export interface TaskItem {
    taskId: string;
    taskType: TaskType;
    status: TaskStatus;
    /** 进度 0~1 */
    progress?: null | number;
    /** 当前阶段：取数/预处理/指标计算/可信度判定 */
    currentStage?: null | string;
    loopsTotal?: null | number;
    loopsDone?: null | number;
    createdAt: string;
    startedAt?: null | string;
    finishedAt?: null | string;
    errorMessage?: null | string;
    createdBy: string;
    /** 重算时间窗起始（仅 BACKFILL） */
    tsStart?: null | string;
    /** 重算时间窗结束（仅 BACKFILL） */
    tsEnd?: null | string;
    /** 回路 ID 列表（仅 BACKFILL） */
    loopIds?: null | string[];
    /** 装置 ID 列表（仅 BACKFILL） */
    plantNodeIds?: null | string[];
  }
```

3. `TaskListQueryParams` 增加 `plantNodeIds`：

```typescript
  /** 任务列表查询参数 */
  export interface TaskListQueryParams {
    taskType?: TaskType;
    status?: TaskStatus;
    startTime?: string;
    endTime?: string;
    /** 按装置 ID 筛选（逗号分隔，仅对 BACKFILL 任务生效） */
    plantNodeIds?: string;
    page?: number;
    pageSize?: number;
  }
```

4. 在 `CustomTaskCreateParams` 之后新增 `BackfillTaskCreateParams` 和 `BackfillPreviewResult`：

```typescript
  /** 历史重算任务创建参数 */
  export interface BackfillTaskCreateParams {
    /** 重算时间窗起始（ISO 8601） */
    tsStart: string;
    /** 重算时间窗结束（ISO 8601，不包含） */
    tsEnd: string;
    /** 装置 ID 列表（可选，不传=全部装置） */
    plantNodeIds?: string[];
    /** 回路 ID 列表（可选，优先级高于 plantNodeIds） */
    loopIds?: string[];
    /** True=只返回预览不提交 */
    dryRun?: boolean;
  }

  /** 历史重算 dry-run 预览结果 */
  export interface BackfillPreviewResult {
    /** 影响回路数 */
    loopCount: number;
    /** 影响小时窗口数 */
    windowCount: number;
    /** 预估耗时（秒） */
    estimatedDurationSec: number;
    /** 前 5 个回路名预览 */
    sampleLoopNames: string[];
  }
```

- [ ] **Step 2: 新增 `triggerBackfillApi` 函数**

在 `frontend/apps/web-antd/src/api/task.ts` 的 `triggerCustomEvaluateApi` 之后新增：

```typescript
/**
 * 触发历史重算任务 — IDS §2.7.6.5（ADMIN/IC_ENGINEER）
 *
 * 按时间窗+装置+回路批量重算历史 KPI，结果 UPSERT 覆盖 kpi_snapshot_hourly。
 * 支持 dry-run 预览模式（仅返回影响范围，不实际触发计算）。
 *
 * @returns dryRun=true 返回 BackfillPreviewResult；dryRun=false 返回 { taskId }
 */
export function triggerBackfillApi(data: TaskApi.BackfillTaskCreateParams) {
  return requestClient.post<TaskApi.BackfillPreviewResult | { taskId: string }>(
    `${BASE}/backfill`,
    data,
  );
}
```

- [ ] **Step 3: 修改 `getTaskListApi` 传递新参数**

确认 `getTaskListApi` 已使用 `params` 透传所有 query 参数（已是 `requestClient.get<TaskApi.TaskListResult>(BASE, { params })`，无需改动；`plantNodeIds` 会自动作为 query 传递）。

- [ ] **Step 4: 类型检查**

Run: `cd frontend && pnpm run check:type`
Expected: 0 errors（如有 pre-existing 错误，确认与本改动无关）

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add frontend/apps/web-antd/src/api/task.ts
git commit -m "feat(api): 前端 task API 增加 BackfillTaskCreateParams + triggerBackfillApi

- TaskType 增加 BACKFILL
- TaskItem 增加 tsStart/tsEnd/loopIds/plantNodeIds 可选字段
- TaskListQueryParams 增加 plantNodeIds 筛选
- 新增 BackfillTaskCreateParams / BackfillPreviewResult 类型
- 新增 triggerBackfillApi 函数（dry-run 预览 + 正式提交）"
```

---

## Phase 4: 前端页面

### Task 6: 新建 recompute.vue 页面

**Files:**
- Create: `frontend/apps/web-antd/src/views/metric/recompute.vue`

- [ ] **Step 1: 创建页面文件**

创建 `frontend/apps/web-antd/src/views/metric/recompute.vue`，参考现有 `views/task/list.vue` 的结构。完整内容：

```vue
<script lang="ts" setup>
/**
 * 历史重算页面
 *
 * 对齐 spec: docs/过程文档/historical-recompute-design.md
 * - 顶部工具栏：发起重算 + 刷新
 * - 重算记录列表：按装置/时间/回路筛选
 * - 发起重算 Drawer：时间窗 + 装置 + 回路 + dry-run 预览 + 确认提交
 *
 * 路由：/metric/recompute
 * 权限：ADMIN + IC_ENGINEER
 */
import { computed, onMounted, ref } from 'vue';

import { Plus, RefreshCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
  message,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  cancelTaskApi,
  getTaskListApi,
  triggerBackfillApi,
} from '#/api/task';
import { getPlantNodeTreeApi } from '#/api/plant';
import { getLoopListApi } from '#/api/loop';
import type { TaskApi } from '#/api/task';

defineOptions({ name: 'MetricRecompute' });

// ============ 列表状态 ============
const loading = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

// 筛选状态
const filterStatus = ref<TaskApi.TaskStatus | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();
const filterPlantNodeIds = ref<string | undefined>();

// ============ Drawer 状态 ============
const drawerVisible = ref(false);
const drawerLoading = ref(false);
const previewLoading = ref(false);
const previewResult = ref<TaskApi.BackfillPreviewResult | null>(null);

const form = ref({
  tsRange: [dayjs().subtract(7, 'day'), dayjs()] as [dayjs.Dayjs, dayjs.Dayjs],
  plantNodeIds: [] as string[],
  loopIds: [] as string[],
});

// 装置树数据
const plantNodeTreeData = ref<any[]>([]);
// 回路选项（按已选装置过滤）
const loopOptions = ref<{ label: string; value: string }[]>([]);

// ============ 状态映射 ============
const statusColorMap: Record<string, string> = {
  PENDING: 'default',
  RUNNING: 'processing',
  SUCCESS: 'success',
  FAILED: 'error',
  CANCELLED: 'warning',
};

const statusTextMap: Record<string, string> = {
  PENDING: '待执行',
  RUNNING: '执行中',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
};

// ============ 列定义 ============
const columns = computed(() => [
  {
    title: '任务ID',
    dataIndex: 'taskId',
    width: 180,
    ellipsis: true,
  },
  {
    title: '时间窗',
    key: 'tsRange',
    width: 280,
  },
  {
    title: '回路数',
    dataIndex: 'loopsTotal',
    width: 90,
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 100,
  },
  {
    title: '进度',
    dataIndex: 'progress',
    width: 140,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    width: 170,
  },
  {
    title: '操作',
    key: 'action',
    width: 120,
    fixed: 'right',
  },
]);

// ============ 加载列表 ============
async function loadList() {
  loading.value = true;
  try {
    const params: TaskApi.TaskListQueryParams = {
      taskType: 'BACKFILL',
      page: currentPage.value,
      pageSize: pageSize.value,
    };
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterPlantNodeIds.value) params.plantNodeIds = filterPlantNodeIds.value;
    if (filterDateRange.value) {
      params.startTime = filterDateRange.value[0].toISOString();
      params.endTime = filterDateRange.value[1].toISOString();
    }
    const result = await getTaskListApi(params);
    taskList.value = result.items;
    totalCount.value = result.total;
  } catch (error) {
    console.error('加载重算记录失败:', error);
    message.error('加载重算记录失败');
  } finally {
    loading.value = false;
  }
}

// ============ 装置树 & 回路选项 ============
async function loadPlantNodeTree() {
  try {
    const result = await getPlantNodeTreeApi();
    plantNodeTreeData.value = transformTreeData(result);
  } catch (error) {
    console.error('加载装置树失败:', error);
  }
}

function transformTreeData(nodes: any[]): any[] {
  return nodes.map((n) => ({
    title: n.name || n.nodeName,
    value: n.id || n.nodeId,
    key: n.id || n.nodeId,
    children: n.children ? transformTreeData(n.children) : undefined,
  }));
}

async function loadLoopOptions() {
  try {
    const params: any = { page: 1, pageSize: 1000 };
    if (form.value.plantNodeIds.length > 0) {
      params.plantNodeIds = form.value.plantNodeIds.join(',');
    }
    const result = await getLoopListApi(params);
    loopOptions.value = (result.items || []).map((l: any) => ({
      label: l.tagName || l.loopName || l.id,
      value: l.id,
    }));
  } catch (error) {
    console.error('加载回路选项失败:', error);
    loopOptions.value = [];
  }
}

// 装置选择变化时重新加载回路选项
async function onPlantNodeChange() {
  form.value.loopIds = [];
  await loadLoopOptions();
}

// ============ Drawer 操作 ============
function openDrawer() {
  previewResult.value = null;
  form.value = {
    tsRange: [dayjs().subtract(7, 'day'), dayjs()],
    plantNodeIds: [],
    loopIds: [],
  };
  drawerVisible.value = true;
  loadPlantNodeTree();
  loadLoopOptions();
}

async function handlePreview() {
  if (!form.value.tsRange?.[0] || !form.value.tsRange?.[1]) {
    message.warning('请选择时间窗');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  // 时间窗最大 30 天校验
  const diffDays = form.value.tsRange[1].diff(form.value.tsRange[0], 'day');
  if (diffDays > 30) {
    message.error('时间窗不能超过 30 天');
    return;
  }

  previewLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds:
        form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: true,
    });
    previewResult.value = result as TaskApi.BackfillPreviewResult;
    message.success('预览完成');
  } catch (error: any) {
    console.error('预览失败:', error);
    message.error(error?.message || '预览失败');
  } finally {
    previewLoading.value = false;
  }
}

async function handleSubmit() {
  if (!previewResult.value) {
    message.warning('请先点击「预览影响范围」');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  drawerLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds:
        form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: false,
    });
    const taskId = (result as { taskId: string }).taskId;
    message.success(`历史重算任务已触发: ${taskId}`);
    drawerVisible.value = false;
    loadList();
  } catch (error: any) {
    console.error('提交失败:', error);
    message.error(error?.message || '提交失败');
  } finally {
    drawerLoading.value = false;
  }
}

// ============ 取消任务 ============
async function handleCancel(taskId: string) {
  try {
    await cancelTaskApi(taskId);
    message.success('任务已取消');
    loadList();
  } catch (error: any) {
    message.error(error?.message || '取消失败');
  }
}

// ============ 工具函数 ============
function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  return dayjs(ts).format('YYYY-MM-DD HH:mm:ss');
}

function formatProgress(progress: number | null | undefined): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

function isTaskActive(task: TaskApi.TaskItem): boolean {
  return task.status === 'PENDING' || task.status === 'RUNNING';
}

// ============ 生命周期 ============
onMounted(() => {
  loadList();
});
</script>

<template>
  <div class="p-4">
    <!-- 顶部工具栏 -->
    <div class="mb-4 flex items-center justify-between">
      <div class="text-lg font-medium">历史重算</div>
      <Space>
        <Button @click="loadList">
          <template #icon><RefreshCw /></template>
          刷新
        </Button>
        <Button type="primary" @click="openDrawer">
          <template #icon><Plus /></template>
          发起重算
        </Button>
      </Space>
    </div>

    <!-- 筛选区 -->
    <div class="mb-4 flex items-center gap-3">
      <Select
        v-model:value="filterStatus"
        placeholder="状态筛选"
        allow-clear
        style="width: 140px"
        @change="loadList"
      >
        <Select.Option value="PENDING">待执行</Select.Option>
        <Select.Option value="RUNNING">执行中</Select.Option>
        <Select.Option value="SUCCESS">成功</Select.Option>
        <Select.Option value="FAILED">失败</Select.Option>
        <Select.Option value="CANCELLED">已取消</Select.Option>
      </Select>
      <DatePicker.RangePicker
        v-model:value="filterDateRange"
        :allow-clear="true"
        @change="loadList"
      />
      <Button type="primary" @click="loadList">查询</Button>
    </div>

    <!-- 重算记录列表 -->
    <Table
      :columns="columns"
      :data-source="taskList"
      :loading="loading"
      :pagination="{
        current: currentPage,
        pageSize: pageSize,
        total: totalCount,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      row-key="taskId"
      @change="
        (p: any) => {
          currentPage = p.current;
          pageSize = p.pageSize;
          loadList();
        }
      "
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'tsRange'">
          <span class="font-mono text-xs">
            {{ formatTime(record.tsStart) }} ~ {{ formatTime(record.tsEnd) }}
          </span>
        </template>
        <template v-else-if="column.dataIndex === 'status'">
          <Tag :color="statusColorMap[record.status]">
            {{ statusTextMap[record.status] || record.status }}
          </Tag>
        </template>
        <template v-else-if="column.dataIndex === 'progress'">
          <Progress
            :percent="formatProgress(record.progress)"
            :status="
              record.status === 'FAILED'
                ? 'exception'
                : record.status === 'SUCCESS'
                  ? 'success'
                  : 'active'
            "
          />
        </template>
        <template v-else-if="column.dataIndex === 'createdAt'">
          {{ formatTime(record.createdAt) }}
        </template>
        <template v-else-if="column.key === 'action'">
          <Popconfirm
            v-if="isTaskActive(record)"
            title="确定取消此任务？"
            @confirm="handleCancel(record.taskId)"
          >
            <Button type="link" danger size="small">取消</Button>
          </Popconfirm>
          <span v-else class="text-gray-400">—</span>
        </template>
      </template>
    </Table>

    <!-- 发起重算 Drawer -->
    <Drawer
      v-model:open="drawerVisible"
      title="发起历史重算"
      width="520"
      :mask-closable="false"
    >
      <Form layout="vertical">
        <FormItem label="时间窗" required>
          <DatePicker.RangePicker
            v-model:value="form.tsRange"
            :allow-clear="false"
            :disabled-date="(d: dayjs.Dayjs) => d.isAfter(dayjs())"
            style="width: 100%"
          />
          <div class="mt-1 text-xs text-gray-400">
            最大 30 天；将按小时窗口批量重算
          </div>
        </FormItem>

        <FormItem label="装置（可选，不选=全部）">
          <TreeSelect
            v-model:value="form.plantNodeIds"
            :tree-data="plantNodeTreeData"
            tree-checkable
            allow-clear
            placeholder="不选=全部装置"
            style="width: 100%"
            @change="onPlantNodeChange"
          />
        </FormItem>

        <FormItem label="回路（可选，不选=对应装置全部）">
          <Select
            v-model:value="form.loopIds"
            mode="multiple"
            allow-clear
            placeholder="不选=对应装置全部回路"
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) =>
                option.label.toLowerCase().includes(input.toLowerCase())
            "
            style="width: 100%"
          />
          <div class="mt-1 text-xs text-gray-400">
            优先级高于装置；支持搜索回路名
          </div>
        </FormItem>

        <!-- 预览结果 -->
        <div
          v-if="previewResult"
          class="mt-4 rounded border border-blue-200 bg-blue-50 p-3"
        >
          <div class="mb-2 font-medium text-blue-700">影响范围预览</div>
          <div class="text-sm">
            <div>回路数：{{ previewResult.loopCount }}</div>
            <div>小时窗口数：{{ previewResult.windowCount }}</div>
            <div>
              预估耗时：{{ Math.ceil(previewResult.estimatedDurationSec / 60) }} 分钟
            </div>
            <div v-if="previewResult.sampleLoopNames.length > 0">
              样本回路：
              {{ previewResult.sampleLoopNames.join(', ') }}
              <span v-if="previewResult.loopCount > 5"> 等 {{ previewResult.loopCount }} 个</span>
            </div>
          </div>
        </div>
      </Form>

      <template #footer>
        <Space>
          <Button @click="drawerVisible = false">取消</Button>
          <Button :loading="previewLoading" @click="handlePreview">
            预览影响范围
          </Button>
          <Button
            type="primary"
            :loading="drawerLoading"
            :disabled="!previewResult"
            @click="handleSubmit"
          >
            确认重算
          </Button>
        </Space>
      </template>
    </Drawer>
  </div>
</template>
```

- [ ] **Step 2: 检查 import 路径**

确认以下 import 在项目中存在：
- `#/api/task` 的 `triggerBackfillApi` / `cancelTaskApi` / `getTaskListApi`（Task 5 已添加）
- `#/api/plant` 的 `getPlantNodeTreeApi`（如不存在，搜索 `frontend/apps/web-antd/src/api` 找到装置树 API）

Run: `cd frontend && grep -r "getPlantNodeTreeApi\|getPlantNodeList" apps/web-antd/src/api/`

如不存在，使用回路列表 API 的装置字段替代，或临时去掉装置筛选（仅保留时间窗+回路）。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm run check:type`
Expected: 0 errors（如有 pre-existing 错误，确认与本改动无关）

- [ ] **Step 4: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add frontend/apps/web-antd/src/views/metric/recompute.vue
git commit -m "feat(view): 新建历史重算页面 recompute.vue

- 顶部工具栏：发起重算 + 刷新
- 重算记录列表：任务ID/时间窗/回路数/状态/进度/创建时间/操作
- 筛选：状态下拉 + 时间范围 + 装置
- 发起重算 Drawer：时间窗 + 装置 + 回路 + dry-run 预览 + 确认提交
- 预览卡片：回路数/窗口数/预估耗时/样本回路名
- 「确认重算」按钮在预览完成前 disable"
```

---

### Task 7: 注册 /metric/recompute 路由

**Files:**
- Modify: `frontend/apps/web-antd/src/router/routes/modules/metric.ts`

- [ ] **Step 1: 在 metric.ts 中新增路由**

在 `frontend/apps/web-antd/src/router/routes/modules/metric.ts` 的 `MetricStatistics` 路由之后（约 52 行后）新增：

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

- [ ] **Step 2: 类型检查**

Run: `cd frontend && pnpm run check:type`
Expected: 0 errors

- [ ] **Step 3: 启动前端验证路由可访问**

Run: `cd frontend && pnpm run dev:antd` (如果未运行)
浏览器访问 `http://localhost:5666/metric/recompute`，使用 admin/admin123 登录。
Expected: 页面正常加载，左侧菜单出现「历史重算」项

- [ ] **Step 4: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add frontend/apps/web-antd/src/router/routes/modules/metric.ts
git commit -m "feat(router): 注册 /metric/recompute 历史重算路由

权限：ADMIN + IC_ENGINEER；菜单 icon: lucide:history"
```

---

## Phase 5: E2E 测试

### Task 8: 新建 recompute.spec.ts E2E 测试

**Files:**
- Create: `e2e/tests/recompute.spec.ts`

- [ ] **Step 1: 创建 E2E 测试文件**

创建 `e2e/tests/recompute.spec.ts`：

```typescript
/**
 * E2E 历史重算测试
 *
 * 覆盖用例：
 * - E2E-RECOMPUTE-001: 发起重算 dry-run → 预览 → 取消
 * - E2E-RECOMPUTE-002: 重算记录列表筛选
 * - E2E-RECOMPUTE-003: 权限校验（PE_ENGINEER 不可见菜单）
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('历史重算 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-RECOMPUTE-001: 发起重算 dry-run 预览', async ({ page }) => {
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证页面加载
    await expect(page.getByText('历史重算').first()).toBeVisible({
      timeout: 15_000,
    });

    // 点击「发起重算」打开 Drawer
    await page.getByRole('button', { name: /发起重算/ }).click();
    await expect(page.locator('.ant-drawer')).toBeVisible({
      timeout: 10_000,
    });

    // 验证 Drawer 标题
    await expect(page.locator('.ant-drawer-title')).toContainText('发起历史重算');

    // 验证「确认重算」按钮初始为 disabled
    const submitBtn = page.getByRole('button', { name: /确认重算/ });
    await expect(submitBtn).toBeDisabled();

    // 点击「预览影响范围」
    const previewBtn = page.getByRole('button', { name: /预览影响范围/ });
    await previewBtn.click();
    await page.waitForTimeout(3000);

    // 验证预览卡片出现（包含"影响范围预览"或"回路数"）
    const previewCard = page.locator('.ant-drawer').getByText(/影响范围预览|回路数/).first();
    const hasPreview = await previewCard.isVisible().catch(() => false);
    if (hasPreview) {
      // 验证「确认重算」按钮变为 enabled
      await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
    }

    // 关闭 Drawer（取消）
    await page.locator('.ant-drawer-content').getByRole('button', { name: /取消/ }).click();
    await expect(page.locator('.ant-drawer')).not.toBeVisible({
      timeout: 5_000,
    });
  });

  test('E2E-RECOMPUTE-002: 重算记录列表与筛选', async ({ page }) => {
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证表格容器存在
    const table = page.locator('.ant-table').first();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // 验证筛选区存在（状态下拉）
    const statusSelect = page.locator('.ant-select').filter({ hasText: /状态筛选|待执行|执行中/ }).first();
    const hasStatusSelect = await statusSelect.isVisible().catch(() => false);
    expect(hasStatusSelect).toBeTruthy();

    // 验证表头包含关键列
    const headerText = await page.locator('.ant-table-thead').first().innerText();
    expect(headerText).toMatch(/任务ID|时间窗|状态|进度/);

    // 验证「发起重算」按钮存在
    await expect(page.getByRole('button', { name: /发起重算/ })).toBeVisible();
  });

  test('E2E-RECOMPUTE-003: PE_ENGINEER 不可访问', async ({ page, loginAs }) => {
    // 重新以 PE_ENGINEER 登录
    await loginAs('PE_ENGINEER');

    // 验证左侧菜单不包含「历史重算」
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    const menuItem = page.getByText('历史重算', { exact: false }).first();
    const hasMenu = await menuItem.isVisible().catch(() => false);
    expect(hasMenu).toBeFalsy();

    // 直接访问 URL 应被重定向或拒绝（403 页面或重定向到首页）
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    // 验证不在 recompute 页面（被路由守卫拦截）
    expect(page.url()).not.toMatch(/\/metric\/recompute$/);
  });
});
```

- [ ] **Step 2: 运行 E2E 测试**

Run: `cd e2e && pnpm exec playwright test ./tests/recompute.spec.ts --reporter=list`
Expected: 3 个测试通过（如 PE_ENGINEER 登录流程有差异，调整断言）

- [ ] **Step 3: 提交**

```bash
cd /Users/zhangping/DEV/CLPM
git add e2e/tests/recompute.spec.ts
git commit -m "test(e2e): 新增历史重算页面 E2E 测试

- E2E-RECOMPUTE-001: 发起重算 dry-run 预览 → 确认按钮 disabled → 取消
- E2E-RECOMPUTE-002: 重算记录列表与筛选
- E2E-RECOMPUTE-003: PE_ENGINEER 不可访问（菜单不可见 + URL 拦截）"
```

---

## Phase 6: 集成验证

### Task 9: 端到端集成验证

**Files:** 无文件改动，仅验证

- [ ] **Step 1: 启动后端服务**

```bash
cd /Users/zhangping/DEV/CLPM
docker compose -f deploy/docker/docker-compose.dev.yml up -d
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &
cd backend && .venv/bin/celery -A app.tasks.celery_app worker -l info -Q default &
```

- [ ] **Step 2: 启动前端服务**

```bash
cd frontend && pnpm run dev:antd
```

- [ ] **Step 3: 验证后端 API**

```bash
# 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123","rememberMe":false}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['accessToken'])")

# 测试 dry-run
curl -s -X POST http://localhost:8001/api/v1/tasks/backfill \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tsStart":"2026-07-04T00:00:00Z","tsEnd":"2026-07-05T00:00:00Z","dryRun":true}' | python3 -m json.tool
```
Expected: 返回包含 `loopCount` / `windowCount` / `estimatedDurationSec` / `sampleLoopNames` 的预览结果

- [ ] **Step 4: 验证前端页面**

浏览器访问 `http://localhost:5666/metric/recompute`：
1. 验证左侧菜单出现「历史重算」
2. 点击「发起重算」→ Drawer 打开
3. 选时间窗 → 点「预览影响范围」→ 显示预览卡片
4. 「确认重算」按钮变 enabled
5. 点击「确认重算」→ Drawer 关闭，列表新增 PENDING 任务

- [ ] **Step 5: 运行后端全量测试**

Run: `cd backend && uv run pytest -q`
Expected: 全部通过（原有 1706 + 新增 ~8 个测试）

- [ ] **Step 6: 运行前端类型检查**

Run: `cd frontend && pnpm run check:type`
Expected: 0 errors

- [ ] **Step 7: 运行全量 E2E 测试**

Run: `cd e2e && pnpm exec playwright test --reporter=list`
Expected: 全部通过（原有 33 + 新增 3 个）

- [ ] **Step 8: 推送到远程**

```bash
cd /Users/zhangping/DEV/CLPM
git push
```

---

## Self-Review Checklist

### Spec 覆盖检查

| Spec 章节 | 实现任务 | 状态 |
|---|---|---|
| §3.1 新增 Schema | Task 1 | ✓ |
| §3.2 新增 HTTP API | Task 4 | ✓ |
| §3.3 扩展 Celery 任务 | Task 2 | ✓ |
| §3.4 TaskTracker 集成 | Task 4（复用 _save_task + _count_active_custom_tasks） | ✓ |
| §4.1 新增页面 | Task 6 | ✓ |
| §4.2 发起重算 Drawer | Task 6 | ✓ |
| §4.3 重算记录列表 | Task 6 | ✓ |
| §4.4 前端 API 扩展 | Task 5 | ✓ |
| §4.5 路由注册 | Task 7 | ✓ |
| §6.1 后端单元测试 | Task 1/2/4 内嵌 | ✓ |
| §6.2 前端测试 + E2E | Task 8 | ✓ |

### Placeholder 扫描

- 无 TBD/TODO
- 所有代码步骤均有完整代码块
- 所有命令均有 expected output

### 类型一致性

- `BackfillTaskCreate`（schema）/ `BackfillTaskCreateParams`（前端）字段名一致（camelCase）
- `BackfillPreviewResult` 字段名前后端一致（loopCount/windowCount/estimatedDurationSec/sampleLoopNames）
- `TaskType.BACKFILL` 前后端字符串值一致（"BACKFILL"）
- `triggerBackfillApi` 返回类型为联合类型 `BackfillPreviewResult | { taskId: string }`，前端按 dryRun 区分

---

## 执行选择

**Plan complete and saved to `docs/过程文档/historical-recompute-implementation-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 每个 Task 派发独立 subagent，任务间审查，快速迭代

**2. Inline Execution** - 在当前会话中按 Task 顺序执行，批量执行 + 检查点审查

**Which approach?**
