"""Tag registry endpoints — 测点清单 (IDS §测点管理).

路由顺序：固定路径（/export、/import）必须在 {tag_id} 之前声明。

- GET    /api/v1/tags          — 分页查询测点列表
- GET    /api/v1/tags/export   — 导出测点 Excel
- POST   /api/v1/tags/import   — 批量导入测点 Excel
- GET    /api/v1/tags/{tagId}  — 测点详情
- PUT    /api/v1/tags/{tagId}  — 更新测点
- DELETE /api/v1/tags/{tagId}  — 删除测点

v4.0 扩展（Phase 5 Track A — IDS §2.4.5）：
- GET    /api/v1/timeseries/{loopId}/waveform   — 波形数据（扩展 tagGroup/valid_mask）
- POST   /api/v1/timeseries/batch/waveform      — 批量波形查询
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.tag import (
    BatchWaveformFailure,
    BatchWaveformRequest,
    BatchWaveformResponse,
    TagBatchDeleteRequest,
    TagBatchDeleteResult,
    TagDeleteResult,
    TagDetail,
    TagImportResult,
    TagListData,
    TagUpdate,
    WaveformPoint,
    WaveformResponse,
    WaveformTimeRange,
)
from app.services.tag import (
    batch_delete_tags,
    delete_tag,
    export_tags,
    get_tag_detail,
    import_tags,
    list_tags,
    update_tag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tags", tags=["tag"])

# v4.0 波形数据路由（独立前缀，主代理负责在 main.py 中注册）
timeseries_router = APIRouter(prefix="/timeseries", tags=["timeseries"])


# ---------------------------------------------------------------------------
# Tag List (固定路径优先)
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[TagListData])
async def list_tags_endpoint(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=10000),
    keyword: str | None = Query(None, description="按位号模糊搜索"),
    measureType: str | None = Query(
        None,
        description="按测点类型筛选: TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/POSITION/OTHER",
    ),
    tagType: str | None = Query(None, description="按参数类型筛选: PV/SP/OP/MODE/KP/TI/TD"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选，支持层级查询"),
    isLinked: bool | None = Query(None, description="按关联状态筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """分页查询测点列表。"""
    data = await list_tags(
        db=db,
        keyword=keyword,
        measure_type=measureType,
        tag_type=tagType,
        plant_node_id=plantNodeId,
        is_linked=isLinked,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# Tag Export / Import (固定路径，必须在 {tag_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_tags_endpoint(
    keyword: str | None = Query(None, description="按位号模糊搜索"),
    measureType: str | None = Query(None, description="按测点类型筛选"),
    tagType: str | None = Query(None, description="按参数类型筛选"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选，支持层级查询"),
    isLinked: bool | None = Query(None, description="按关联状态筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> StreamingResponse:
    """导出测点清单为 Excel 文件（.xlsx）。"""
    content = await export_tags(
        db=db,
        keyword=keyword,
        measure_type=measureType,
        tag_type=tagType,
        plant_node_id=plantNodeId,
        is_linked=isLinked,
    )
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=tags_export.xlsx",
        },
    )


@router.post("/import", response_model=ApiResponse[TagImportResult])
async def import_tags_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """批量导入测点清单（Excel .xlsx）。

    逐行处理：位号已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await import_tags(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


@router.post("/batch-delete", response_model=ApiResponse[TagBatchDeleteResult])
async def batch_delete_tags_endpoint(
    body: TagBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量删除测点（仅 ADMIN）。

    已关联回路的测点跳过并记入 failures，不影响其他测点删除。
    返回 {deleted, failed, failures[]}。
    """
    data = await batch_delete_tags(db=db, tag_ids=body.tagIds, operator=user.username)
    return success(data=data, message=f"已删除 {data['deleted']} 个测点")


# ---------------------------------------------------------------------------
# Tag CRUD by ID
# ---------------------------------------------------------------------------


@router.get("/match-loop", response_model=ApiResponse[list])
async def match_tags_for_loop_endpoint(
    loopTagName: str = Query(..., description="回路位号，如 T-HDS-001"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """根据回路位号自动匹配测点。

    返回匹配的测点列表，用于自动关联功能。
    匹配规则：测点位号 = 回路位号 + 分隔符 + 参数类型（PV/SP/OP/MODE/PID_P/PID_I/PID_D）

    P3 #45 修复：
    - 原 `["PV","SP","OP","MODE","KP","TI","TD"]` 与 schema/seed data 的
      `PID_P/PID_I/PID_D` 不一致，导致 PID 参数永远无法自动匹配。
    - 同时支持 `_` 和 `-` 两种分隔符（不同工厂命名约定不同）。
    """
    from sqlalchemy import select

    from app.models.tag import TagRegistry

    # P3 #45: 与 loop_tag_mapping.tag_role CHECK 约束保持一致
    # （来源：db/postgresql/01_schema.sql:168 + AGENTS.md AAS 数据模型）
    loop_tag_roles = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
    matched_tags = []

    for role in loop_tag_roles:
        # 同时尝试 `_` 和 `-` 分隔符，兼容不同工厂命名约定
        # （seed data 用 `T-HDS-001-PV`，部分 DCS 用 `80PIC31306_PV`）
        candidates = [f"{loopTagName}_{role}", f"{loopTagName}-{role}"]
        result = await db.execute(
            select(TagRegistry).where(TagRegistry.tag_name.in_(candidates))
        )
        tag = result.scalar_one_or_none()
        if tag:
            matched_tags.append(
                {
                    "role": role,
                    "tagId": str(tag.id),
                    "tagName": tag.tag_name,
                    "tagDescription": tag.tag_description,
                    "tagType": tag.tag_type,
                    "measureType": tag.measure_type,
                    "unit": tag.unit,
                }
            )

    return success(data=matched_tags)


@router.get("/{tag_id}", response_model=ApiResponse[TagDetail])
async def get_tag_detail_endpoint(
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取测点详情。"""
    data = await get_tag_detail(db=db, tag_id=tag_id)
    return success(data=data)


@router.put("/{tag_id}", response_model=ApiResponse[TagDetail])
async def update_tag_endpoint(
    tag_id: str,
    body: TagUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """更新测点（描述/量程/单位/测点类型/TDengine tag ID）。"""
    data = await update_tag(
        db=db,
        tag_id=tag_id,
        operator=user.username,
        tag_description=body.tagDescription,
        range_min=body.rangeMin,
        range_max=body.rangeMax,
        unit=body.unit,
        measure_type=body.measureType,
        tdengine_tag_id=body.tdengineTagId,
    )
    return success(data=data, message="更新成功")


@router.delete("/{tag_id}", response_model=ApiResponse[TagDeleteResult])
async def delete_tag_endpoint(
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除测点（仅 ADMIN）。

    校验：已关联的测点不能删除（返回 ERR_TAG_LINKED）。
    """
    data = await delete_tag(db=db, tag_id=tag_id, operator=user.username)
    return success(data=data, message="删除成功")


# ===========================================================================
# v4.0 波形数据接口（Phase 5 Track A — IDS §2.4.5）
# 设计依据：IDS §2.4.5, 算法说明 §3.4-3.7, 数据流程图 §7
# ===========================================================================

# 最大时间窗（30 天），与现有 waveform service 一致
_MAX_TIME_WINDOW_DAYS = 30

# tagGroup → 代表性 metric_code 列表（用于 DataPlanner.request_bundles）
# DataPlanner 会合并同 tagGroup 的指标，取 tag 角色并集查询
_TAG_GROUP_METRICS: dict[str, list[str]] = {
    "BASE": ["accuracy_rate", "fast_response_rate", "steady_rate", "oscillation_rate"],
    "OP_HF": ["saturation_rate"],
    "PVOP_HF": ["stiction_coeff"],
    "MODE_HF": ["effective_auto_rate"],
    "QUALITY_HF": ["good_value_rate"],
}

_DEFAULT_TAG_GROUP = "BASE"


def _loop_type_to_control_type(loop_type: str | None) -> str:
    """将 LoopLedger.loop_type 映射为 ControlType 枚举值。

    LoopLedger.loop_type: TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER
    ControlType: FC/PC/TC/LC/CC

    SPEED/OTHER 回退为 FLOW（采样率 1s，最宽松）。
    """
    mapping = {
        "TEMPERATURE": "TC",
        "PRESSURE": "PC",
        "LEVEL": "LC",
        "FLOW": "FC",
        "ANALYSIS": "CC",
    }
    if not loop_type:
        return "FC"
    return mapping.get(loop_type, "FC")


def _quality_policy_for(tag_group: str) -> str:
    """根据 tagGroup 返回质量策略标签。

    QUALITY_HF 使用 KEEP_ALL（好值率不删除行），其余默认 KEEP_ALL_WITH_VALIDITY。
    """
    if tag_group == "QUALITY_HF":
        return "KEEP_ALL"
    return "KEEP_ALL_WITH_VALIDITY"


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串为 datetime 对象。"""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # 统一转为 naive UTC（与现有 waveform service 一致）
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except ValueError:
        return datetime.fromisoformat(s)


def _build_data_planner(db: AsyncSession) -> Any:
    """构造 DataPlanner 实例（复用 Phase 2 架构）。

    通过数据源工厂获取 Provider（tdengine / remote_api），
    支持 DATA_SOURCE_TYPE 配置切换数据源（与 kpi_calc._build_data_planner 一致）。

    - L1DataBlockCache（Redis zstd 压缩缓存）
    - provider.make_query_fn（tdengine 或 remote_api 查询适配器）
    - MetricDataBundleAssembler（数据血缘组装器）
    """
    from app.core.redis import redis_client
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.data_planner import DataPlanner
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


def _get_signal_value(signals: dict[str, list[Any]], key: str, idx: int) -> float | None:
    """从 DataBlock.signals 中安全获取指定索引的信号值。"""
    vals = signals.get(key)
    if vals is None or idx >= len(vals):
        return None
    v = vals[idx]
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_point_validity(validity: dict[str, list[bool]], idx: int) -> bool:
    """计算单个数据点的整体有效性。

    取所有 ``{tag}_valid`` 标记的逻辑与；无有效性信息时默认 True。
    """
    if not validity:
        return True
    for key, vals in validity.items():
        if not key.endswith("_valid"):
            continue
        if idx < len(vals) and not vals[idx]:
            return False
    return True


def _compute_outlier_reason(outlier_reasons: dict[str, list[list[str]]], idx: int) -> str | None:
    """汇总单个数据点所有信号的异常原因码。

    多个原因码以逗号分隔（如 ``"FROZEN,JUMP"``）。
    """
    reasons: list[str] = []
    for _key, vals in outlier_reasons.items():
        if idx < len(vals) and vals[idx]:
            for r in vals[idx]:
                if r and r not in reasons:
                    reasons.append(r)
    return ",".join(reasons) if reasons else None


def _datablock_to_waveform_response(
    data_block: Any | None,
    loop_id: str,
    tag_name: str | None,
    start_time: str,
    end_time: str,
    include_valid_mask: bool,
    max_points: int,
) -> WaveformResponse:
    """将 DataBlock 转换为 WaveformResponse。

    DataBlock 包含 timestamps/signals/validity/outlier_reasons/quality_summary，
    本函数将其转换为前端友好的 WaveformPoint 列表，并应用 LTTB 降采样。
    """
    time_range = WaveformTimeRange(startTime=start_time, endTime=end_time)

    if data_block is None or not data_block.timestamps:
        return WaveformResponse(
            loopId=loop_id,
            tagName=tag_name,
            timeRange=time_range,
            points=[],
            samplingFreq=getattr(data_block, "sampling_freq", "1s") if data_block else "1s",
            qualityPolicy=_quality_policy_for(
                getattr(data_block, "tag_group", _DEFAULT_TAG_GROUP)
                if data_block
                else _DEFAULT_TAG_GROUP
            ),
            validRate=1.0,
            downsampled=False,
            pointCount=0,
        )

    timestamps: list[datetime] = data_block.timestamps
    signals: dict[str, list[Any]] = data_block.signals
    validity: dict[str, list[bool]] = data_block.validity
    outlier_reasons: dict[str, list[list[str]]] = data_block.outlier_reasons
    n = len(timestamps)

    # LTTB 降采样（超 max_points 时触发）
    downsampled = False
    if n > max_points and n > 2:
        timestamps, signals, validity, outlier_reasons = _lttb_downsample_datablock(
            timestamps, signals, validity, outlier_reasons, max_points
        )
        n = len(timestamps)
        downsampled = True

    # 构建 WaveformPoint 列表
    points: list[WaveformPoint] = []
    for i in range(n):
        ts = timestamps[i]
        ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)

        mode_raw = _get_signal_value(signals, "mode", i)
        mode_val = int(mode_raw) if mode_raw is not None else None

        pv_q_raw = _get_signal_value(signals, "pv_quality", i)
        pv_quality = int(pv_q_raw) if pv_q_raw is not None else None

        if include_valid_mask:
            valid = _compute_point_validity(validity, i)
            outlier = _compute_outlier_reason(outlier_reasons, i)
        else:
            valid = True
            outlier = None

        points.append(
            WaveformPoint(
                timestamp=ts_str,
                pv=_get_signal_value(signals, "pv", i),
                sp=_get_signal_value(signals, "sp", i),
                op=_get_signal_value(signals, "op", i),
                mode=mode_val,
                pvQuality=pv_quality,
                valid=valid,
                outlierReason=outlier,
            )
        )

    # 有效数据率
    qs = data_block.quality_summary
    valid_rate = float(qs.valid_rate) if qs and qs.valid_rate is not None else 1.0

    return WaveformResponse(
        loopId=loop_id,
        tagName=tag_name,
        timeRange=time_range,
        points=points,
        samplingFreq=data_block.sampling_freq,
        qualityPolicy=_quality_policy_for(data_block.tag_group),
        validRate=valid_rate,
        downsampled=downsampled,
        pointCount=len(points),
    )


def _lttb_downsample_datablock(
    timestamps: list[datetime],
    signals: dict[str, list[Any]],
    validity: dict[str, list[bool]],
    outlier_reasons: dict[str, list[list[str]]],
    target_points: int,
) -> tuple[
    list[datetime],
    dict[str, list[Any]],
    dict[str, list[bool]],
    dict[str, list[list[str]]],
]:
    """对 DataBlock 数据应用 LTTB 降采样（多序列共享时间戳）。

    复用现有 ``app.services.waveform.lttb_downsample_multi_series`` 函数，
    以 PV 序列为参考进行降采样，其他信号/有效性/异常原因码按相同索引采样。
    """
    from app.services.waveform import lttb_downsample_multi_series

    # datetime → 毫秒时间戳（LTTB 需要数值）
    ts_millis: list[int] = []
    for ts in timestamps:
        if isinstance(ts, datetime):
            ts_millis.append(int(ts.timestamp() * 1000))
        else:
            ts_millis.append(int(ts))

    # 构建降采样输入（signals + validity + outlier_reasons 统一处理）
    series_map: dict[str, list[Any]] = {}
    for k, v in signals.items():
        series_map[k] = v
    for k, v in validity.items():
        series_map[k] = v
    for k, v in outlier_reasons.items():
        series_map[f"{k}_reasons"] = v

    new_ts_millis, new_series_map = lttb_downsample_multi_series(
        ts_millis, series_map, target_points
    )

    # 还原 datetime
    new_timestamps: list[datetime] = []
    for ms in new_ts_millis:
        new_timestamps.append(datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None))

    # 拆分回三类字典
    new_signals: dict[str, list[Any]] = {}
    new_validity: dict[str, list[bool]] = {}
    new_outlier_reasons: dict[str, list[list[str]]] = {}

    for k, v in new_series_map.items():
        if k.endswith("_reasons"):
            new_outlier_reasons[k[:-8]] = v
        elif k.endswith("_valid"):
            new_validity[k] = v
        else:
            new_signals[k] = v

    return new_timestamps, new_signals, new_validity, new_outlier_reasons


async def _fetch_waveform_for_loop(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    tag_group: str | None,
    include_valid_mask: bool,
    max_points: int,
) -> WaveformResponse:
    """通过 DataPlanner 获取单个回路的波形数据。

    流程（数据流程图 §7）：
        1. 校验回路存在 + 时间窗
        2. 构建 DataPlanner 实例
        3. 按 tagGroup 映射到代表性 metric_code
        4. 调用 request_bundles 获取 DataBlock（含 valid_mask / outlier_reasons）
        5. 转换为 WaveformResponse

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TS_001 / ERR_INVALID_TAG_GROUP
    """
    # 解析时间
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)

    if end_dt <= start_dt:
        raise BizError(
            code="ERR_TS_002",
            message="结束时间必须晚于开始时间",
            status_code=400,
        )

    if end_dt - start_dt > timedelta(days=_MAX_TIME_WINDOW_DAYS):
        raise BizError(
            code="ERR_TS_001",
            message=f"时间窗不能超过 {_MAX_TIME_WINDOW_DAYS} 天",
            status_code=400,
        )

    # 校验 tagGroup
    tg = (tag_group or _DEFAULT_TAG_GROUP).upper()
    metrics = _TAG_GROUP_METRICS.get(tg)
    if not metrics:
        raise BizError(
            code="ERR_INVALID_TAG_GROUP",
            message=(f"无效的 tag_group: {tg}，可选值: {', '.join(_TAG_GROUP_METRICS.keys())}"),
            status_code=400,
        )

    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 构建 DataPlanner 并获取 DataBlock
    from app.contracts.data_types import ControlType, TimeWindow

    control_type = ControlType(_loop_type_to_control_type(loop.loop_type))
    time_window = TimeWindow(start=start_dt, end=end_dt)

    try:
        planner = _build_data_planner(db)
        bundles = await planner.request_bundles(
            loop_id=loop_id,
            metrics=metrics,
            time_window=time_window,
            control_type=control_type,
        )
    except BizError:
        raise
    except Exception as exc:
        logger.exception("DataPlanner 获取波形数据失败: loop=%s", loop_id)
        raise BizError(
            code="ERR_WAVEFORM_FETCH",
            message=f"获取波形数据失败: {exc}",
            status_code=500,
        ) from exc

    # 提取 DataBlock（取第一个 bundle）
    data_block = bundles[0].data_block if bundles else None

    return _datablock_to_waveform_response(
        data_block=data_block,
        loop_id=loop_id,
        tag_name=loop.tag_name,
        start_time=start_time,
        end_time=end_time,
        include_valid_mask=include_valid_mask,
        max_points=max_points,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/timeseries/{loopId}/waveform — 波形数据（扩展 tagGroup/valid_mask）
# ---------------------------------------------------------------------------


@timeseries_router.get("/{loop_id}/waveform", response_model=ApiResponse[WaveformResponse])
async def get_waveform_v2_endpoint(
    loop_id: str,
    startTime: str = Query(..., description="开始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    tagGroup: str | None = Query(
        None,
        description="按标签组筛选: BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF（默认 BASE）",
    ),
    includeValidMask: bool = Query(True, description="是否返回 valid_mask（默认 true）"),
    maxPoints: int = Query(5000, ge=100, le=50000, description="最大数据点数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """波形数据（v4.0 扩展 — 含 valid_mask + tagGroup 筛选）。

    - 通过 DataPlanner 获取数据（复用 Phase 2 架构，不直接查 TDengine）
    - 返回 ``WaveformResponse``（含 points 列表，每个点带 ``valid`` 和 ``outlierReason``）
    - 支持 ``tagGroup`` 筛选（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）
    - 超过 ``maxPoints`` 触发 LTTB 降采样
    - 时间窗超过 30 天返回 ERR_TS_001
    - 响应时间阈值：2000ms 以内（L1 缓存命中时 <5ms）
    """
    data = await _fetch_waveform_for_loop(
        db=db,
        loop_id=loop_id,
        start_time=startTime,
        end_time=endTime,
        tag_group=tagGroup,
        include_valid_mask=includeValidMask,
        max_points=maxPoints,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# POST /api/v1/timeseries/batch/waveform — 批量波形查询
# ---------------------------------------------------------------------------


@timeseries_router.post("/batch/waveform", response_model=ApiResponse[BatchWaveformResponse])
async def batch_waveform_endpoint(
    body: BatchWaveformRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """批量波形查询（并行获取多个回路数据）。

    - 使用 ``asyncio.gather`` 并行获取多个回路波形
    - 单个回路失败不影响其他回路（失败信息放入 ``failed`` 列表）
    - 每个回路独立应用 LTTB 降采样
    - 最多 50 个回路（防止资源滥用）
    """
    logger.info(
        "批量波形查询: loops=%d, tagGroup=%s, range=%s~%s",
        len(body.loopIds),
        body.tagGroup,
        body.startTime,
        body.endTime,
    )

    async def _safe_fetch(lid: str) -> WaveformResponse | BatchWaveformFailure:
        """单个回路查询，异常转为失败信息。"""
        try:
            return await _fetch_waveform_for_loop(
                db=db,
                loop_id=lid,
                start_time=body.startTime,
                end_time=body.endTime,
                tag_group=body.tagGroup,
                include_valid_mask=body.includeValidMask,
                max_points=body.maxPoints,
            )
        except BizError as exc:
            logger.warning("批量波形查询失败: loop=%s, code=%s, msg=%s", lid, exc.code, exc.message)
            return BatchWaveformFailure(loopId=lid, error=f"{exc.code}: {exc.message}")
        except Exception as exc:
            logger.exception("批量波形查询异常: loop=%s", lid)
            return BatchWaveformFailure(loopId=lid, error=str(exc))

    # 并行查询所有回路
    results = await asyncio.gather(*[_safe_fetch(lid) for lid in body.loopIds])

    items: list[WaveformResponse] = []
    failed: list[BatchWaveformFailure] = []
    for r in results:
        if isinstance(r, WaveformResponse):
            items.append(r)
        else:
            failed.append(r)

    data = BatchWaveformResponse(items=items, failed=failed, total=len(items))
    return success(data=data)


__all__ = ["router", "timeseries_router"]
