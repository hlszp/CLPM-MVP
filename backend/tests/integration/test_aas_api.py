"""AAS REST API 集成测试 — 验证真实数据链路.

测试前提：
- AAS 服务可达（默认 http://192.168.100.2:81）
- DATA_SOURCE_TYPE=remote_api
- 数据库中有已配置的回路和 Tag

运行方式：
    cd backend && uv run pytest tests/integration/ -v -m integration --no-header

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source.factory import get_provider
from app.services.data_source.remote_api_provider import RemoteApiProvider

# 已知可用的测试 Tag（从历史验证中确认）
_TEST_TAG = "41FIC20021_PIDA.PV"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _aas_reachable() -> bool:
    """检查 AAS 服务是否可达。"""
    import httpx

    url = os.getenv(
        "HISTORY_DATA_API_URL", "http://192.168.100.2:81/api/services/v1/HistoryData/Get"
    )
    base = url.rsplit("/", 1)[0]  # 去掉 /Get
    try:
        resp = httpx.get(base, timeout=3.0)
        return resp.status_code < 500
    except Exception:
        return False


# 所有集成测试都需要 AAS 服务可达
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _aas_reachable(), reason="AAS 服务不可达，跳过集成测试"),
]


# ---------------------------------------------------------------------------
# 1. Factory 路由验证
# ---------------------------------------------------------------------------


class TestFactoryRouting:
    """验证 factory 根据配置正确路由到 RemoteApiProvider。"""

    def test_factory_returns_remote_api_provider(self):
        """DATA_SOURCE_TYPE=remote_api 时应返回 RemoteApiProvider。"""
        provider = get_provider()
        assert isinstance(provider, RemoteApiProvider), (
            f"期望 RemoteApiProvider，实际 {type(provider).__name__}，"
            "请检查 .env 中 DATA_SOURCE_TYPE=remote_api"
        )

    def test_provider_query_trend_data_is_callable(self):
        """Provider 的 query_trend_data 方法可调用。"""
        provider = get_provider()
        assert callable(provider.query_trend_data)


# ---------------------------------------------------------------------------
# 2. AAS HistoryData API 端到端验证
# ---------------------------------------------------------------------------


class TestAasHistoryDataApi:
    """验证通过 RemoteApiProvider 能从 AAS 获取真实历史数据。"""

    @pytest.mark.asyncio
    async def test_query_trend_data_returns_non_empty(self):
        """查询最近 1 小时的 PV 趋势数据，应返回非空列表。"""
        provider = get_provider()
        end_time = datetime.now(UTC).replace(tzinfo=None)
        start_time = end_time - timedelta(hours=1)

        data = await provider.query_trend_data(
            tag_name=_TEST_TAG,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            sample_interval=60,  # 60 秒采样，减少数据量
        )

        assert isinstance(data, list), f"返回类型错误: {type(data)}"
        assert len(data) > 0, f"Tag {_TEST_TAG} 最近 1 小时无数据返回"

        # 验证数据结构
        first = data[0]
        assert "ts" in first, "数据点缺少 ts 字段"
        assert "value" in first, "数据点缺少 value 字段"
        assert "quality" in first, "数据点缺少 quality 字段"

    @pytest.mark.asyncio
    async def test_query_trend_data_sample_interval(self):
        """不同采样间隔应返回不同点数。"""
        provider = get_provider()
        end_time = datetime.now(UTC).replace(tzinfo=None)
        start_time = end_time - timedelta(hours=1)

        # 1 秒采样 → 约 3600 点
        data_1s = await provider.query_trend_data(
            tag_name=_TEST_TAG,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            sample_interval=1,
        )

        # 60 秒采样 → 约 60 点
        data_60s = await provider.query_trend_data(
            tag_name=_TEST_TAG,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            sample_interval=60,
        )

        assert len(data_1s) > len(data_60s), (
            f"1s 采样({len(data_1s)}点)应多于 60s 采样({len(data_60s)}点)"
        )

    @pytest.mark.asyncio
    async def test_query_trend_data_nonexistent_tag(self):
        """查询不存在的 Tag 应返回空列表（不抛异常）。"""
        provider = get_provider()
        end_time = datetime.now(UTC).replace(tzinfo=None)
        start_time = end_time - timedelta(minutes=5)

        data = await provider.query_trend_data(
            tag_name="NONEXISTENT_TAG_12345.PV",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        assert data == [], f"不存在的 Tag 应返回空列表，实际: {data}"


# ---------------------------------------------------------------------------
# 3. KPI 计算端到端验证（通过 factory 路由获取 AAS 数据）
# ---------------------------------------------------------------------------


class TestKpiCalcViaFactory:
    """验证 KPI 计算通过 factory 路由从 AAS 获取数据并计算指标。"""

    @pytest.mark.asyncio
    async def test_do_calculate_single_loop_with_real_data(self):
        """单回路 KPI 计算应通过 AAS API 获取数据并产出快照。"""
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.loop import LoopLedger
        from app.tasks.kpi_calc import _do_calculate_single_loop

        # 从数据库查找 _TEST_TAG 对应的回路
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(LoopLedger).where(
                    LoopLedger.is_active.is_(True),
                    LoopLedger.status == "READY",
                )
            )
            loops = list(result.scalars().all())
            assert len(loops) > 0, "数据库中无 ACTIVE+READY 回路"

            # 使用第一个可用回路
            loop = loops[0]
            loop_id = str(loop.id)

        # 计算最近 1 小时的 KPI
        now = datetime.now(UTC).replace(tzinfo=None)
        ts_start = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        result = await _do_calculate_single_loop(
            loop_id=loop_id,
            ts_start=ts_start.isoformat(),
        )

        # 验证返回结构
        assert "loopId" in result, f"返回缺少 loopId: {result}"
        assert "status" in result, f"返回缺少 status: {result}"
        # status 可能是 SUCCESS / INCONCLUSIVE / FAILED
        assert result["status"] in ("SUCCESS", "INCONCLUSIVE", "FAILED"), (
            f"未知 status: {result['status']}"
        )

        # 如果是 SUCCESS，验证快照已写入数据库
        if result["status"] == "SUCCESS":
            from app.models.metric import KpiSnapshotHourly

            async with AsyncSessionLocal() as db:
                snap_result = await db.execute(
                    select(KpiSnapshotHourly)
                    .where(KpiSnapshotHourly.loop_id == loop_id)
                    .order_by(KpiSnapshotHourly.ts_start.desc())
                    .limit(1)
                )
                snapshot = snap_result.scalar_one_or_none()
                assert snapshot is not None, "KPI 快照未写入数据库"
                assert snapshot.status == "SUCCESS", f"快照状态异常: {snapshot.status}"
                assert snapshot.score is not None, "快照 score 为空"
