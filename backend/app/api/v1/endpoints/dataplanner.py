"""DataPlanner 内部管理接口 (IDS v3.2 §2.7.5).

仅限 ADMIN 角色访问，用于系统管理和调试。
不返回完整时序数据，仅返回摘要信息。

路由清单：
- POST   /api/v1/algorithms/dataplanner/plan           — 提交查询计划
- POST   /api/v1/algorithms/dataplanner/bundle         — 执行查询计划，返回 Bundle 摘要
- GET    /api/v1/algorithms/dataplanner/cache/stats    — 查看缓存命中率/大小
- DELETE /api/v1/algorithms/dataplanner/cache/{loopId} — 失效指定回路缓存

设计依据：IDS §2.7.5, PRD §8.1-8.3
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.contracts.data_types import ControlType, TimeWindow
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.dataplanner import (
    BundleRequest,
    BundleResponse,
    BundleSummary,
    CacheStatsResponse,
    PlanRequest,
    QueryPlanResponse,
    QueryTaskSchema,
)
from app.services.cache.invalidation import CacheInvalidator
from app.services.data_planner import DataPlanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/algorithms/dataplanner", tags=["dataplanner"])

# L1 DataBlock 缓存 Key 前缀（与 l1_datablock.py 保持一致）
_L1_KEY_PREFIX = "pdb"

# SCAN 每批返回的 Key 数量
_SCAN_COUNT = 200


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_data_planner(db: AsyncSession) -> DataPlanner:
    """构造 DataPlanner 实例.

    复用 ``app.tasks.kpi_calc._build_data_planner`` 的构造逻辑，
    将现有 TDengine 查询层适配为 DataPlanner 所需的查询函数签名。

    Args:
        db: 异步数据库会话

    Returns:
        DataPlanner 实例
    """
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.data_source.factory import get_provider
    from app.services.metric_data_bundle import MetricDataBundleAssembler

    provider = get_provider()
    query_fn = provider.make_query_fn(db)
    cache = L1DataBlockCache(redis_client)
    assembler = MetricDataBundleAssembler()
    return DataPlanner(
        cache=cache,
        tdengine_query_fn=query_fn,
        assembler=assembler,
        db=db,
        config_loader=None,  # 使用 DataPlanner 默认配置加载器
    )


def _parse_control_type(value: str) -> ControlType:
    """解析控制类型字符串为枚举.

    Args:
        value: 控制类型代码（FC/PC/TC/LC/CC）

    Returns:
        ControlType 枚举值

    Raises:
        BizError: 无效的控制类型
    """
    try:
        return ControlType(value)
    except ValueError:
        valid = [ct.value for ct in ControlType]
        raise BizError(
            code="ERR_INVALID_CONTROL_TYPE",
            message=f"无效的控制类型: {value}，可选值: {', '.join(valid)}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from None


def _parse_time_window(start: str, end: str) -> TimeWindow:
    """解析 ISO 8601 时间字符串为 TimeWindow.

    Args:
        start: 起始时间（ISO 8601）
        end: 结束时间（ISO 8601）

    Returns:
        TimeWindow 实例

    Raises:
        BizError: 时间格式无效或起始时间不早于结束时间
    """
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_INVALID_TIME",
            message=f"无效的起始时间格式: {start}（需 ISO 8601）",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from None
    try:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_INVALID_TIME",
            message=f"无效的结束时间格式: {end}（需 ISO 8601）",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from None
    if start_dt >= end_dt:
        raise BizError(
            code="ERR_INVALID_TIME",
            message="起始时间必须早于结束时间",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return TimeWindow(start=start_dt, end=end_dt)


def _confidence_from_valid_rate(valid_rate: float) -> str:
    """根据有效数据率推断可信度等级（算法说明 §3.7.2）.

    Args:
        valid_rate: 有效数据率 0~1

    Returns:
        可信度等级 A/B/C/D/E
    """
    if valid_rate >= 0.95:
        return "A"
    if valid_rate >= 0.80:
        return "B"
    if valid_rate >= 0.60:
        return "C"
    if valid_rate >= 0.20:
        return "D"
    return "E"


# ---------------------------------------------------------------------------
# 接口：提交查询计划
# ---------------------------------------------------------------------------


@router.post("/plan", response_model=ApiResponse[QueryPlanResponse])
async def submit_plan(
    body: PlanRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """提交查询计划（仅 ADMIN）。

    根据指标数据需求契约，构建合并后的查询计划。
    不执行实际数据查询，仅返回查询计划摘要，用于调试和验证。

    设计依据：IDS §2.7.5.1
    """
    control_type = _parse_control_type(body.controlType)
    _parse_time_window(body.start, body.end)  # 仅校验时间格式
    planner = _build_data_planner(db)

    # 读取指标数据需求契约（调用 DataPlanner 内部方法，仅用于计划预览）
    requirements = await planner._load_requirements(body.metrics)  # noqa: SLF001
    if not requirements:
        raise BizError(
            code="ERR_METRIC_NOT_FOUND",
            message=f"未找到任何指标契约: metrics={body.metrics}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 构建合并查询计划
    query_plan = planner._build_query_plan(requirements, control_type)  # noqa: SLF001

    tasks = [
        QueryTaskSchema(
            tagGroup=t.tag_group.value,
            metrics=t.metrics,
            tagRoles=t.tag_roles,
            intervalS=t.interval_s,
            reusedFrom=t.reused_from.value if t.reused_from else None,
        )
        for t in query_plan
    ]

    resp = QueryPlanResponse(
        loopId=body.loopId,
        queryTasks=tasks,
        totalTagGroups=len(tasks),
    )
    logger.info(
        "DataPlanner 查询计划: loop=%s, metrics=%d, tagGroups=%d",
        body.loopId,
        len(body.metrics),
        len(tasks),
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# 接口：获取 Bundle 摘要
# ---------------------------------------------------------------------------


@router.post("/bundle", response_model=ApiResponse[BundleResponse])
async def get_bundle(
    body: BundleRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """执行查询计划并返回 Bundle 摘要（仅 ADMIN）。

    调用 DataPlanner.request_bundles 执行完整取数流程（查缓存 → 未命中查
    TDengine + 预处理 → 写缓存 → 组装 Bundle），但仅返回摘要信息，
    不包含完整时序数据（数据量过大）。

    设计依据：IDS §2.7.5.2
    """
    control_type = _parse_control_type(body.controlType)
    time_window = _parse_time_window(body.start, body.end)
    planner = _build_data_planner(db)

    try:
        bundles = await planner.request_bundles(
            loop_id=body.loopId,
            metrics=body.metrics,
            time_window=time_window,
            control_type=control_type,
        )
    except Exception:
        logger.exception(
            "DataPlanner 取数失败: loop=%s, metrics=%s",
            body.loopId,
            body.metrics,
        )
        raise BizError(
            code="ERR_DATAPLANNER_FAILED",
            message=f"DataPlanner 取数失败: loop={body.loopId}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    if not bundles:
        logger.warning("DataPlanner 返回空 Bundle 列表: loop=%s", body.loopId)
        resp = BundleResponse(
            loopId=body.loopId,
            bundles=[],
            validRate=0.0,
            confidenceLevel="E",
        )
        return success(data=resp.model_dump())

    summaries: list[BundleSummary] = []
    valid_rates: list[float] = []
    for bundle in bundles:
        vr = bundle.lineage.valid_rate
        valid_rates.append(vr)
        summaries.append(
            BundleSummary(
                metricCode=bundle.metric_code,
                tagGroup=bundle.data_block.tag_group,
                samplingFreq=bundle.data_block.sampling_freq,
                pointCount=bundle.data_block.point_count,
                validRate=vr,
                dataBlockId=bundle.data_block.data_block_id,
            )
        )

    avg_valid_rate = sum(valid_rates) / len(valid_rates) if valid_rates else 0.0
    confidence = _confidence_from_valid_rate(avg_valid_rate)

    resp = BundleResponse(
        loopId=body.loopId,
        bundles=summaries,
        validRate=round(avg_valid_rate, 4),
        confidenceLevel=confidence,
    )
    logger.info(
        "DataPlanner Bundle: loop=%s, bundles=%d, valid_rate=%.4f, confidence=%s",
        body.loopId,
        len(summaries),
        avg_valid_rate,
        confidence,
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# 接口：缓存统计
# ---------------------------------------------------------------------------


@router.get("/cache/stats", response_model=ApiResponse[CacheStatsResponse])
async def get_cache_stats(
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查看缓存统计（仅 ADMIN）。

    返回 L1 DataBlock 缓存的命中率、内存占用和按 tagGroup 的键数分布。
    通过 Redis SCAN 遍历 ``pdb:*`` Key 统计，不阻塞 Redis 主线程。

    设计依据：ADS §10.7.1-10.7.2
    """
    total_keys = 0
    by_tag_group: dict[str, int] = {}
    cursor: int | str = 0

    while True:
        cursor, keys = await redis_client.scan(
            cursor=cursor, match=f"{_L1_KEY_PREFIX}:*", count=_SCAN_COUNT
        )
        for key in keys:
            parts = str(key).split(":")
            # pdb:{loopId}:{tagGroup}:{startEpoch}:{endEpoch}:{freq}:{policy}:{preVer}:{cfgVer}
            if len(parts) >= 3:
                tag_group = parts[2]
                by_tag_group[tag_group] = by_tag_group.get(tag_group, 0) + 1
            total_keys += 1
        if cursor in (0, "0"):
            break

    # Redis 内存占用
    memory_usage_mb = 0.0
    try:
        info = await redis_client.info("memory")
        memory_usage_mb = float(info.get("used_memory", 0)) / (1024 * 1024)
    except Exception:  # noqa: BLE001
        logger.warning("获取 Redis 内存信息失败", exc_info=True)

    # 缓存命中率（Redis INFO stats）
    hit_rate = 0.0
    try:
        stats_info = await redis_client.info("stats")
        hits = int(stats_info.get("keyspace_hits", 0))
        misses = int(stats_info.get("keyspace_misses", 0))
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
    except Exception:  # noqa: BLE001
        logger.warning("获取 Redis 命中率统计失败", exc_info=True)

    resp = CacheStatsResponse(
        totalKeys=total_keys,
        hitRate=round(hit_rate, 4),
        memoryUsageMb=round(memory_usage_mb, 2),
        byTagGroup=by_tag_group,
    )
    logger.info(
        "DataPlanner 缓存统计: keys=%d, hit_rate=%.4f, tag_groups=%s",
        total_keys,
        hit_rate,
        by_tag_group,
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# 接口：缓存失效
# ---------------------------------------------------------------------------


@router.delete("/cache/{loop_id}", response_model=ApiResponse[dict])
async def invalidate_cache(
    loop_id: str,
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """失效指定回路的缓存（仅 ADMIN）。

    通过 SCAN + DEL 批量删除该回路的所有 L1 DataBlock 缓存。
    用于回路配置变更（量程/控制类型）后主动清除脏数据。

    设计依据：ADS §10.7.3
    """
    invalidator = CacheInvalidator(redis_client)
    deleted = await invalidator.invalidate_loop(loop_id)
    data = {"loopId": loop_id, "deletedKeys": deleted}
    logger.info("DataPlanner 缓存失效: loop=%s, deleted=%d", loop_id, deleted)
    return success(data=data, message=f"已失效 {deleted} 个缓存键")


__all__ = ["router"]
