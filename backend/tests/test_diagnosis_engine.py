"""诊断引擎 Celery 任务测试 (S4-DIAG-002).

测试覆盖：
- 纯函数：_detect_external_disturbance / _compute_sample_interval 等
- _diagnose_loop 核心诊断逻辑（mock DB + query_trend_fn）
- _do_run_diagnosis / _do_diagnose_single_loop 编排逻辑
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.tasks.diagnosis_engine import (
    _analyze_pid_params,
    _analyze_quality,
    _analyze_saturation,
    _build_scatter_plot_url,
    _compute_sample_interval,
    _dempster_shafer_fusion,
    _detect_external_disturbance,
    _detect_oscillation_fft,
    _detect_valve_stiction,
    _diagnose_loop,
    _do_diagnose_single_loop,
    _do_run_diagnosis,
    _get_tag_name,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_loop(
    loop_id: str = "loop-001",
    tag_name: str = "LIC-101",
    status: str = "READY",
    is_active: bool = True,
) -> MagicMock:
    """构造 mock LoopLedger。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "液位控制"
    loop.status = status
    loop.is_active = is_active
    loop.unit_id = "unit-001"
    return loop


def _make_mapping(
    loop_id: str = "loop-001",
    tag_role: str = "PV",
    tag_id: str = "tag-pv-001",
) -> MagicMock:
    """构造 mock LoopTagMapping。"""
    m = MagicMock()
    m.loop_id = loop_id
    m.tag_role = tag_role
    m.tag_id = tag_id
    return m


def _make_tag(
    tag_id: str = "tag-pv-001",
    tag_name: str = "LIC-101.PV",
    current_value: float = 50.0,
    quality: str = "GOOD",
) -> MagicMock:
    """构造 mock TagRegistry。"""
    tag = MagicMock()
    tag.id = tag_id
    tag.tag_name = tag_name
    tag.current_value = current_value
    tag.quality = quality
    tag.last_sync_at = datetime.now(UTC)
    return tag


def _make_diag_config(
    diag_code: str = "OSCILLATION",
    is_enabled: bool = True,
) -> MagicMock:
    """构造 mock DiagnosisConfig。"""
    c = MagicMock()
    c.diag_code = diag_code
    c.diag_name = diag_code
    c.is_enabled = is_enabled
    return c


def _make_trend_data(
    n: int = 50,
    base_value: float = 50.0,
    amplitude: float = 0.0,
    quality: str = "GOOD",
) -> list[dict[str, Any]]:
    """构造 TDengine 趋势数据。"""
    data: list[dict[str, Any]] = []
    for i in range(n):
        value = base_value + amplitude * float(np.sin(i * 0.5))
        data.append({"ts": float(i), "value": value, "quality": quality})
    return data


def _make_scalar_one_or_none_mock(value: Any) -> MagicMock:
    """构造 execute 返回的 mock，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_all_mock(items: list) -> MagicMock:
    """构造 execute 返回的 mock，支持 scalars().all()。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


# ===========================================================================
# 纯函数测试
# ===========================================================================


class TestDetectExternalDisturbance:
    """测试 _detect_external_disturbance() 外扰检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足时应返回未检测。"""
        pv = np.array([1.0, 2.0, 3.0], dtype=float)
        result = _detect_external_disturbance(pv)
        assert result["detected"] is False
        assert result["confidence"] == 0.0

    def test_no_disturbance(self) -> None:
        """低频信号（无外扰）应返回未检测。"""
        pv = np.array([50.0 + 0.1 * i for i in range(100)], dtype=float)
        result = _detect_external_disturbance(pv)
        assert result["detected"] is False

    def test_with_disturbance(self) -> None:
        """高频信号应检测到外扰。"""
        t = np.linspace(0, 10, 200)
        pv = 50.0 + 10.0 * np.sin(2 * np.pi * 5 * t)  # 5Hz 高频
        result = _detect_external_disturbance(pv)
        assert result["detected"] is True
        assert result["confidence"] > 0.0
        assert result["frequency"] > 0.0

    def test_empty_array(self) -> None:
        """空数组应返回未检测。"""
        pv = np.array([], dtype=float)
        result = _detect_external_disturbance(pv)
        assert result["detected"] is False

    def test_with_sample_interval(self) -> None:
        """指定采样间隔时应正确计算频率。"""
        t = np.linspace(0, 10, 200)
        pv = 50.0 + 10.0 * np.sin(2 * np.pi * 5 * t)
        result = _detect_external_disturbance(pv, sample_interval=0.5)
        assert result["detected"] is True


class TestComputeSampleInterval:
    """测试 _compute_sample_interval() 采样间隔计算。"""

    def test_empty_data(self) -> None:
        """空数据应返回默认 1.0。"""
        assert _compute_sample_interval([]) == 1.0

    def test_single_point(self) -> None:
        """单点数据应返回默认 1.0。"""
        assert _compute_sample_interval([{"ts": 100.0}]) == 1.0

    def test_numeric_ts(self) -> None:
        """数值时间戳应正确计算间隔。"""
        aligned = [{"ts": 100.0}, {"ts": 102.0}, {"ts": 104.0}]
        assert _compute_sample_interval(aligned) == 2.0

    def test_datetime_ts(self) -> None:
        """datetime 时间戳应正确计算间隔。"""
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        aligned = [
            {"ts": base},
            {"ts": base + timedelta(seconds=5)},
            {"ts": base + timedelta(seconds=10)},
        ]
        assert _compute_sample_interval(aligned) == 5.0

    def test_iso_string_ts(self) -> None:
        """ISO 字符串时间戳应正确计算间隔。"""
        aligned = [
            {"ts": "2026-01-01T00:00:00+00:00"},
            {"ts": "2026-01-01T00:00:03+00:00"},
        ]
        assert _compute_sample_interval(aligned) == 3.0

    def test_none_ts_skipped(self) -> None:
        """None ts 应被跳过。"""
        aligned = [{"ts": None}, {"ts": 100.0}, {"ts": 102.0}]
        assert _compute_sample_interval(aligned) == 2.0

    def test_invalid_ts_skipped(self) -> None:
        """无效 ts 应被跳过。"""
        aligned = [{"ts": "invalid"}, {"ts": 100.0}, {"ts": 102.0}]
        assert _compute_sample_interval(aligned) == 2.0

    def test_all_invalid_returns_default(self) -> None:
        """全部无效 ts 应返回默认 1.0。"""
        aligned = [{"ts": "invalid"}, {"ts": None}]
        assert _compute_sample_interval(aligned) == 1.0


class TestGetTagName:
    """测试 _get_tag_name() Tag 名称获取。"""

    def test_existing_role(self) -> None:
        """存在的角色应返回 tag_name。"""
        mapping = _make_mapping(tag_role="PV", tag_id="tag-001")
        tag = _make_tag(tag_id="tag-001", tag_name="LIC.PV")
        result = _get_tag_name({"PV": mapping}, {"tag-001": tag}, "PV")
        assert result == "LIC.PV"

    def test_missing_role(self) -> None:
        """不存在的角色应返回 None。"""
        result = _get_tag_name({}, {}, "PV")
        assert result is None

    def test_missing_tag(self) -> None:
        """mapping 存在但 tag 不存在应返回 None。"""
        mapping = _make_mapping(tag_role="PV", tag_id="tag-001")
        result = _get_tag_name({"PV": mapping}, {}, "PV")
        assert result is None


class TestBuildScatterPlotUrl:
    """测试 _build_scatter_plot_url() URL 构建。"""

    def test_url_format(self) -> None:
        """URL 应包含 loop_id 和时间范围。"""
        ts_start = datetime(2026, 1, 1, 0, 0, 0)
        ts_end = datetime(2026, 1, 1, 1, 0, 0)
        url = _build_scatter_plot_url("loop-001", ts_start, ts_end)
        assert "loop-001" in url
        assert "scatter" in url
        assert ts_start.isoformat() in url
        assert ts_end.isoformat() in url


# ===========================================================================
# _diagnose_loop 集成测试
# ===========================================================================


class TestDiagnoseLoop:
    """测试 _diagnose_loop() 单回路诊断逻辑。"""

    @pytest.mark.asyncio
    async def test_loop_not_found_returns_none(self) -> None:
        """回路不存在时应返回 None。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        result = await _diagnose_loop(
            db=db,
            loop_id="non-existent",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=AsyncMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_pv_tag_returns_none(self) -> None:
        """缺少 PV Tag 时应返回 None。"""
        loop = _make_loop()
        db = AsyncMock()
        # loop 查询返回 loop，mapping 查询返回空（无 PV）
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([]),  # 无 mapping
                _make_scalars_all_mock([]),  # 无 tags
            ]
        )

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=AsyncMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_tdengine_query_failure_returns_none(self) -> None:
        """TDengine 查询失败时应返回 None。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
            ]
        )

        async def _fail_query(*args, **kwargs):
            raise RuntimeError("TDengine 不可用")

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_fail_query,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_none(self) -> None:
        """数据点不足时应返回 None。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
            ]
        )

        # 仅 10 个点（< MIN_DATA_POINTS=32）
        short_data = _make_trend_data(n=10)

        async def _query_fn(*args, **kwargs):
            return short_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_normal_diagnosis_with_oscillation(self) -> None:
        """正常诊断流程（振荡信号）应返回诊断结果。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
                MagicMock(),  # delete(DiagnosisResult) 结果
            ]
        )
        db.add = MagicMock()

        # 50 个点的振荡信号
        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = [
            {"ts": float(i), "value": 50.0 + 10.0 * np.sin(ti), "quality": "GOOD"}
            for i, ti in enumerate(t)
        ]

        async def _query_fn(tag_name: str, *args, **kwargs):
            if tag_name == "LIC.PV":
                return osc_data
            return []

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )

        assert result is not None
        assert result["loopId"] == "loop-001"
        assert result["status"] == "SUCCESS"
        assert "OSCILLATION" in result["labels"]
        assert db.add.called  # 写入了诊断记录

    @pytest.mark.asyncio
    async def test_normal_diagnosis_no_anomaly(self) -> None:
        """无异常时应返回 MANUAL_REVIEW 标签。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
                MagicMock(),  # delete 结果
            ]
        )
        db.add = MagicMock()

        # 50 个点的稳定数据（无振荡）
        stable_data = [{"ts": float(i), "value": 50.0, "quality": "GOOD"} for i in range(50)]

        async def _query_fn(tag_name: str, *args, **kwargs):
            if tag_name == "LIC.PV":
                return stable_data
            return []

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )

        assert result is not None
        assert "MANUAL_REVIEW" in result["labels"]

    @pytest.mark.asyncio
    async def test_quality_bad_filtered(self) -> None:
        """PV 质量码为 Bad 的数据点应被过滤。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
                MagicMock(),
            ]
        )
        db.add = MagicMock()

        # 50 个点，部分 Bad
        data = _make_trend_data(n=50)
        for i in range(10):
            data[i]["quality"] = "BAD"

        async def _query_fn(tag_name: str, *args, **kwargs):
            if tag_name == "LIC.PV":
                return data
            return []

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )

        # 过滤后 40 点 >= 32，应正常诊断
        assert result is not None
        assert result["status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_all_quality_bad_returns_none(self) -> None:
        """全部 Bad 质量码过滤后数据不足应返回 None。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
            ]
        )

        # 50 个点全部 Bad
        data = _make_trend_data(n=50, quality="BAD")

        async def _query_fn(tag_name: str, *args, **kwargs):
            if tag_name == "LIC.PV":
                return data
            return []

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_full_tags_diagnosis(self) -> None:
        """完整 PV/SP/OP/MODE Tag 关联时应正常诊断。"""
        loop = _make_loop()
        pv_m = _make_mapping(tag_role="PV", tag_id="tag-pv")
        sp_m = _make_mapping(tag_role="SP", tag_id="tag-sp")
        op_m = _make_mapping(tag_role="OP", tag_id="tag-op")
        mode_m = _make_mapping(tag_role="MODE", tag_id="tag-mode")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC.SP")
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC.OP")
        mode_tag = _make_tag(tag_id="tag-mode", tag_name="LIC.MODE")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_m, sp_m, op_m, mode_m]),
                _make_scalars_all_mock([pv_tag, sp_tag, op_tag, mode_tag]),
                MagicMock(),
            ]
        )
        db.add = MagicMock()

        pv_data = _make_trend_data(n=50, base_value=50.0, amplitude=2.0)

        async def _query_fn(tag_name: str, *args, **kwargs):
            if tag_name == "LIC.PV":
                return pv_data
            if tag_name == "LIC.SP":
                return [{"ts": float(i), "value": 50.0} for i in range(50)]
            if tag_name == "LIC.OP":
                return [{"ts": float(i), "value": 50.0} for i in range(50)]
            if tag_name == "LIC.MODE":
                return [{"ts": float(i), "value": 1} for i in range(50)]
            return []

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_trend_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"


# ===========================================================================
# _do_run_diagnosis 编排测试
# ===========================================================================


class TestDoRunDiagnosis:
    """测试 _do_run_diagnosis() 全量诊断编排。"""

    @pytest.mark.asyncio
    async def test_no_snapshots_returns_empty(self) -> None:
        """无待诊断回路时应返回 total=0。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([]),  # 无 snapshot
            ]
        )

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_run_diagnosis()

        assert result["total"] == 0
        assert result["diagnosed"] == 0

    @pytest.mark.asyncio
    async def test_with_snapshots_diagnoses_loops(self) -> None:
        """有待诊断回路时应执行诊断。"""
        snapshot = MagicMock()
        snapshot.loop_id = "loop-001"

        diag_config = _make_diag_config()

        # 主 session：查询 snapshot + config
        main_session = AsyncMock()
        main_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([snapshot]),  # snapshot 查询
                _make_scalars_all_mock([diag_config]),  # config 查询
            ]
        )
        main_session.commit = AsyncMock()
        main_session.rollback = AsyncMock()

        # worker session：查询 loop + mapping（无 PV → 返回 None → failed）
        worker_session = AsyncMock()
        worker_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(_make_loop()),  # loop 查询
                _make_scalars_all_mock([]),  # 无 mapping → 缺少 PV → 返回 None
            ]
        )
        worker_session.commit = AsyncMock()
        worker_session.rollback = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            # 主 session 和 worker session 通过 __aenter__ side_effect 区分
            mock_session_local.return_value.__aenter__ = AsyncMock(
                side_effect=[main_session, worker_session]
            )
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await _do_run_diagnosis()

        assert result["total"] == 1
        # 缺少 PV Tag → 返回 None → failed_count += 1
        assert result["failed"] == 1


# ===========================================================================
# _do_diagnose_single_loop 测试
# ===========================================================================


class TestDoDiagnoseSingleLoop:
    """测试 _do_diagnose_single_loop() 单回路诊断。"""

    @pytest.mark.asyncio
    async def test_normal_diagnosis(self) -> None:
        """正常单回路诊断。"""
        loop = _make_loop()
        diag_config = _make_diag_config()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([diag_config]),  # config 查询
                _make_scalar_one_or_none_mock(loop),  # loop 查询
                _make_scalars_all_mock([]),  # 无 mapping
                _make_scalars_all_mock([]),  # 无 tags
            ]
        )
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_diagnose_single_loop("loop-001")

        # 缺少 PV Tag → _diagnose_loop 返回 None → 返回 FAILED
        assert result["loopId"] == "loop-001"
        assert result["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_ts_start_with_z_suffix(self) -> None:
        """ts_start 带 Z 后缀应正确解析。"""
        loop = _make_loop()
        diag_config = _make_diag_config()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([diag_config]),
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([]),
                _make_scalars_all_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_diagnose_single_loop("loop-001", ts_start="2026-01-01T00:00:00Z")

        assert result["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_ts_start_without_z(self) -> None:
        """ts_start 不带 Z 应正确解析。"""
        loop = _make_loop()
        diag_config = _make_diag_config()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([diag_config]),
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([]),
                _make_scalars_all_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_diagnose_single_loop(
                "loop-001", ts_start="2026-01-01T00:00:00+00:00"
            )

        assert result["status"] == "FAILED"


# ===========================================================================
# 算法函数深入测试
# ===========================================================================


class TestDetectValveStiction:
    """测试 _detect_valve_stiction() 阀门粘滞检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0, 2.0], dtype=float)
        op = np.array([1.0, 2.0], dtype=float)
        result = _detect_valve_stiction(pv, op)
        assert result["detected"] is False
        assert result["fitting_score"] == 0.0

    def test_no_stiction_linear(self) -> None:
        """线性 PV-OP 关系（无粘滞）应返回未检测。"""
        # 完全线性关系，无椭圆轨迹
        op = np.linspace(0, 100, 100)
        pv = op * 0.5 + 10  # 完全线性
        result = _detect_valve_stiction(pv, op)
        assert result["detected"] is False

    def test_with_stiction_ellipse(self) -> None:
        """PV-OP 呈椭圆轨迹应检测到粘滞。"""
        # 构造椭圆轨迹：PV 滞后于 OP 形成椭圆
        t = np.linspace(0, 2 * np.pi, 200)
        op = 50.0 + 20.0 * np.cos(t)
        pv = 50.0 + 20.0 * np.cos(t - np.pi / 2)  # 相位差 90 度形成椭圆
        result = _detect_valve_stiction(pv, op)
        # 椭圆轨迹应检测到粘滞
        assert result["fitting_score"] > 0.0

    def test_constant_op_with_pv_variation(self) -> None:
        """OP 不动 PV 变化应检测到粘滞特征。"""
        # OP 基本不变，PV 大幅波动
        op = np.full(100, 50.0)
        op += np.random.RandomState(42).normal(0, 0.001, 100)  # 微小噪声
        pv = 50.0 + 10.0 * np.sin(np.linspace(0, 10, 100))
        result = _detect_valve_stiction(pv, op)
        # 应该能计算 stiction_index
        assert "stiction_index" in result
        assert "fitting_score" in result


class TestAnalyzePidParams:
    """测试 _analyze_pid_params() PID 增益分析。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回默认值。"""
        pv = np.array([1.0, 2.0], dtype=float)
        sp = np.array([1.0, 2.0], dtype=float)
        result = _analyze_pid_params(pv, sp)
        assert result["overaggressive"] is False
        assert result["overconservative"] is False

    def test_steady_state_no_overshoot(self) -> None:
        """稳态数据（无 SP 阶跃）应无过冲。"""
        n = 100
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        result = _analyze_pid_params(pv, sp)
        assert result["overaggressive"] is False
        assert result["overshoot"] == 0.0

    def test_overaggressive_with_overshoot(self) -> None:
        """SP 阶跃后 PV 过冲应检测到过激。"""
        n = 100
        sp = np.zeros(n)
        sp[50:] = 100.0  # SP 阶跃
        # PV 过冲：超过 SP 目标值
        pv = np.zeros(n)
        pv[50:] = 100.0
        pv[60:70] = 130.0  # 过冲 30%
        result = _analyze_pid_params(pv, sp)
        assert result["overaggressive"] is True
        assert result["overshoot"] > 0.2

    def test_overconservative_slow_response(self) -> None:
        """响应缓慢且稳态误差大应检测到过保守。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0  # SP 阶跃
        # PV 响应非常慢，且稳态误差大
        pv = np.zeros(n)
        for i in range(50, n):
            pv[i] = 80.0 + 0.001 * (i - 50)  # 缓慢上升，稳态误差 20
        result = _analyze_pid_params(pv, sp)
        # 响应时间长 + 稳态误差大 → 过保守
        assert "overconservative" in result
        assert "response_time" in result

    def test_downward_step(self) -> None:
        """下降阶跃应正确计算过冲。"""
        n = 100
        sp = np.full(n, 100.0)
        sp[50:] = 0.0  # 下降阶跃
        pv = np.full(n, 100.0)
        pv[50:] = 0.0
        pv[60:70] = -30.0  # 下冲
        result = _analyze_pid_params(pv, sp)
        assert result["overshoot"] > 0.0


class TestAnalyzeSaturation:
    """测试 _analyze_saturation() OP 饱和率分析。"""

    def test_empty_data(self) -> None:
        """空数据应返回未检测。"""
        result = _analyze_saturation(np.array([], dtype=float))
        assert result["detected"] is False
        assert result["saturation_rate"] == 0.0

    def test_no_saturation(self) -> None:
        """OP 在中间范围应无饱和。"""
        op = np.full(100, 50.0)
        result = _analyze_saturation(op)
        assert result["detected"] is False

    def test_high_saturation(self) -> None:
        """OP 长时间高饱和应检测到。"""
        op = np.full(100, 100.0)
        op[:10] = 50.0  # 少量非饱和
        result = _analyze_saturation(op)
        assert result["detected"] is True
        assert result["high_count"] > 0

    def test_low_saturation(self) -> None:
        """OP 长时间低饱和应检测到。"""
        op = np.full(100, 0.0)
        op[:10] = 50.0
        result = _analyze_saturation(op)
        assert result["detected"] is True
        assert result["low_count"] > 0

    def test_zero_range(self) -> None:
        """OP 范围为 0 应返回未检测。"""
        op = np.full(100, 50.0)
        result = _analyze_saturation(op)
        assert result["detected"] is False


class TestAnalyzeQuality:
    """测试 _analyze_quality() 质量码统计。"""

    def test_all_good(self) -> None:
        """全部 GOOD 质量码。"""
        data = [{"quality": "GOOD"} for _ in range(50)]
        result = _analyze_quality(data)
        assert result["bad_rate"] == 0.0
        assert result["total"] == 50

    def test_all_bad(self) -> None:
        """全部 BAD 质量码。"""
        data = [{"quality": "BAD"} for _ in range(50)]
        result = _analyze_quality(data)
        assert result["bad_rate"] == 1.0
        assert result["bad_count"] == 50

    def test_mixed_quality(self) -> None:
        """混合质量码。"""
        data = [{"quality": "GOOD"} for _ in range(30)] + [
            {"quality": "BAD"} for _ in range(20)
        ]
        result = _analyze_quality(data)
        assert result["bad_rate"] == 0.4

    def test_empty_data(self) -> None:
        """空数据。"""
        result = _analyze_quality([])
        assert result["total"] == 0


class TestDempsterShaferFusion:
    """测试 _dempster_shafer_fusion() 证据融合。"""

    def test_empty_evidence(self) -> None:
        """空证据列表应返回 0。"""
        assert _dempster_shafer_fusion([]) == 0.0

    def test_single_evidence(self) -> None:
        """单条证据应返回该置信度。"""
        assert _dempster_shafer_fusion([("OSCILLATION", 0.8)]) == 0.8

    def test_multiple_evidence(self) -> None:
        """多条证据应通过 noisy-OR 融合。"""
        fused = _dempster_shafer_fusion(
            [("OSCILLATION", 0.5), ("VALVE_STICTION", 0.6)]
        )
        # noisy-OR: 1 - (1-0.5)*(1-0.6) = 1 - 0.2 = 0.8
        assert abs(fused - 0.8) < 0.001

    def test_zero_confidence(self) -> None:
        """零置信度证据。"""
        fused = _dempster_shafer_fusion([("A", 0.0), ("B", 0.0)])
        assert fused == 0.0


class TestDetectOscillationFft:
    """测试 _detect_oscillation_fft() FFT 振荡检测。"""

    def test_short_data(self) -> None:
        """数据不足应返回未检测。"""
        pv = np.array([1.0, 2.0, 3.0], dtype=float)
        result = _detect_oscillation_fft(pv, 1.0)
        assert result["detected"] is False

    def test_no_oscillation(self) -> None:
        """平稳数据应无振荡。"""
        pv = np.full(100, 50.0)
        pv += np.random.RandomState(42).normal(0, 0.01, 100)
        result = _detect_oscillation_fft(pv, 1.0)
        assert result["detected"] is False

    def test_with_oscillation(self) -> None:
        """正弦波应检测到振荡。"""
        t = np.linspace(0, 10, 200)
        pv = 50.0 + 10.0 * np.sin(2 * np.pi * 1.0 * t)  # 1Hz 振荡
        result = _detect_oscillation_fft(pv, 0.05)
        assert result["detected"] is True
        assert result["frequency"] > 0.0


class TestComputeSampleIntervalEdgeCases:
    """测试 _compute_sample_interval() 边界场景。"""

    def test_all_same_timestamps(self) -> None:
        """所有时间戳相同应返回默认 1.0。"""
        aligned = [{"ts": 100.0}, {"ts": 100.0}, {"ts": 100.0}]
        assert _compute_sample_interval(aligned) == 1.0

    def test_negative_diffs_filtered(self) -> None:
        """负差值应被过滤。"""
        aligned = [{"ts": 100.0}, {"ts": 98.0}, {"ts": 96.0}]
        # 所有 diff 为负 → 过滤后为空 → 返回 1.0
        assert _compute_sample_interval(aligned) == 1.0
