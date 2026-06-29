"""模拟远端数据服务 — 历史 API 端点.

实现 ``POST /api/services/v1/HistoryData/Get`` 接口。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from mock_data_server.schemas import ApiResponse, HistoryDataDto, HistoryDataRequest, TagHistoryValueDto
from mock_data_server.services.tdengine_query import query_history_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/services/v1/HistoryData/Get",
    response_model=ApiResponse,
    summary="获取历史数据采样结果",
)
async def get_history_data(request: HistoryDataRequest) -> ApiResponse:
    """按固定时间间隔对指定标签集合进行时间序列采样.

    遵循 HisDATA_API.md 规范：
    - 请求体: tagCodes / startTime / endTime / sampleInterval
    - 响应体: code / message / data{timestamps, series[]}
    - 质量码: 0=未知, 1=Good, 2=Bad, 3=离线
    """
    logger.info(
        "历史数据查询: tagCodes=%s, start=%s, end=%s, interval=%d",
        request.tagCodes,
        request.startTime,
        request.endTime,
        request.sampleInterval,
    )

    result = await query_history_data(
        tag_codes=request.tagCodes,
        start_time=request.startTime,
        end_time=request.endTime,
    )

    series = [
        TagHistoryValueDto(
            tagCode=s["tagCode"],
            values=s["values"],
            qualities=s["qualities"],
        )
        for s in result["series"]
    ]

    data = HistoryDataDto(
        timestamps=result["timestamps"],
        series=series,
    )

    return ApiResponse(code=200, message="Success", data=data)
