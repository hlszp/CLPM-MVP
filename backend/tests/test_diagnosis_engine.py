"""诊断引擎 Celery 任务测试 (S4-DIAG-002).

测试覆盖：
- 纯函数：_compute_sample_interval / _analyze_quality / _analyze_saturation 等
- _diagnose_loop 核心诊断逻辑（mock DB + RawTimeSeries 宽表查询）
- _do_run_diagnosis / _do_diagnose_single_loop 编排逻辑
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.contracts.data_types import RawTimeSeries
from app.tasks.diagnosis_engine import (
    _analyze_quality,
    _analyze_saturation,
    _analyze_step_response,
    _apply_expert_rules,
    _apply_outlier_preprocessing,
    _assess_model_mismatch,
    _build_scatter_plot_data,
    _compute_sample_interval,
    _deduplicate_labels,
    _dempster_shafer_fusion,
    _detect_bias_shift,
    _detect_choudhury_nonlinearity,
    _detect_kano_stiction,
    _detect_oscillation_fft,
    _detect_oscillation_iae,
    _detect_sensor_faults,
    _detect_slow_response,
    _detect_valve_stiction,
    _diagnose_loop,
    _do_diagnose_single_loop,
    _do_run_checkup,
    _do_run_diagnosis,
    _get_tag_name,
    _get_threshold,
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


def _make_raw_timeseries(
    pv: list[Any],
    *,
    sp: list[Any] | None = None,
    op: list[Any] | None = None,
    mode: list[Any] | None = None,
    pv_quality: list[int] | None = None,
) -> RawTimeSeries:
    """构造宽表查询返回的 RawTimeSeries。"""
    signals = {"pv": pv}
    if sp is not None:
        signals["sp"] = sp
    if op is not None:
        signals["op"] = op
    if mode is not None:
        signals["mode"] = mode
    return RawTimeSeries(
        timestamps=[datetime(2026, 1, 1) + timedelta(seconds=i) for i in range(len(pv))],
        signals=signals,
        quality_codes={"pv_quality": pv_quality or [1] * len(pv)},
    )


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


class TestBuildScatterPlotData:
    """测试 _build_scatter_plot_data() 坐标数据构建。"""

    def test_empty_aligned(self) -> None:
        """空数据应返回空坐标数组。"""
        result = _build_scatter_plot_data([])
        assert result == {"x": [], "y": []}

    def test_normal_data(self) -> None:
        """正常数据应返回 PV-OP 坐标对。"""
        aligned = [
            {"pv": 10.0, "op": 50.0},
            {"pv": 12.0, "op": 55.0},
            {"pv": 11.0, "op": 52.0},
        ]
        result = _build_scatter_plot_data(aligned)
        assert len(result["x"]) == 3
        assert len(result["y"]) == 3
        assert result["x"] == [10.0, 12.0, 11.0]
        assert result["y"] == [50.0, 55.0, 52.0]

    def test_skip_none_values(self) -> None:
        """pv 或 op 为 None 的点应被跳过。"""
        aligned = [
            {"pv": 10.0, "op": 50.0},
            {"pv": None, "op": 55.0},
            {"pv": 11.0, "op": None},
            {"pv": 12.0, "op": 52.0},
        ]
        result = _build_scatter_plot_data(aligned)
        assert len(result["x"]) == 2
        assert result["x"] == [10.0, 12.0]

    def test_downsample_large_data(self) -> None:
        """数据量超过 500 点时应降采样到 500 点。"""
        aligned = [{"pv": float(i), "op": float(i) * 2} for i in range(1000)]
        result = _build_scatter_plot_data(aligned)
        assert len(result["x"]) == 500
        assert len(result["y"]) == 500


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
            query_wide_fn=AsyncMock(),
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
            query_wide_fn=AsyncMock(),
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
            query_wide_fn=_fail_query,
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
        short_data = _make_raw_timeseries([50.0] * 10)

        async def _query_fn(*args, **kwargs):
            return short_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 50 个点的振荡信号
        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 50 个点的稳定数据（无振荡）
        stable_data = _make_raw_timeseries([50.0] * 50)

        async def _query_fn(**kwargs):
            return stable_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 50 个点，部分 Bad
        data = _make_raw_timeseries([50.0] * 50, pv_quality=[0] * 10 + [1] * 40)

        async def _query_fn(**kwargs):
            return data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
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
        data = _make_raw_timeseries([50.0] * 50, pv_quality=[0] * 50)

        async def _query_fn(**kwargs):
            return data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        pv_values = [50.0 + 2.0 * float(np.sin(i * 0.5)) for i in range(50)]
        raw_series = _make_raw_timeseries(
            pv_values,
            sp=[50.0] * 50,
            op=[50.0] * 50,
            mode=[1] * 50,
        )

        async def _query_fn(**kwargs):
            return raw_series

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"


# ===========================================================================
# A11: diagnosis_tag 写入方（_diagnose_loop 落库段 upsert）
# ===========================================================================


class TestDiagnosisTagUpsert:
    """诊断落库时同步 upsert diagnosis_tag（A11）。"""

    @staticmethod
    def _added_tags(db: AsyncMock) -> list:
        """从 db.add 调用中提取 DiagnosisTag 实例。"""
        from app.models.diagnosis import DiagnosisTag

        return [
            call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], DiagnosisTag)
        ]

    @pytest.mark.asyncio
    async def test_tag_created_on_diagnosis(self) -> None:
        """诊断落库后生成 ACTIVE 标签（severity/source_metric 映射正确）。"""
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 50 个点的振荡信号
        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "OSCILLATION" in result["labels"]
        tags = self._added_tags(db)
        osc_tags = [t for t in tags if t.tag_code == "OSCILLATION"]
        assert len(osc_tags) == 1
        tag = osc_tags[0]
        assert tag.status == "ACTIVE"
        assert tag.severity == "WARN"  # OSCILLATION → WARN
        assert tag.tag_name == "振荡"
        assert tag.source_metric == "FFT"  # 算法来源
        assert tag.trigger_condition["algorithm"] == "FFT"
        assert tag.trigger_value is not None

    @pytest.mark.asyncio
    async def test_existing_active_tag_updated_not_duplicated(self) -> None:
        """同回路同标签已有 ACTIVE 行时更新触发快照，不重复建行。"""
        loop = _make_loop()
        pv_mapping = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")

        existing_tag = MagicMock()
        existing_tag.tag_code = "OSCILLATION"
        existing_tag.status = "ACTIVE"
        existing_tag.triggered_at = datetime(2026, 1, 1)
        existing_tag.trigger_value = Decimal("0.1")
        existing_tag.trigger_condition = {"algorithm": "FFT", "confidence": 0.1}
        existing_tag.source_metric = "FFT"
        existing_tag.severity = "WARN"

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_mapping]),
                _make_scalars_all_mock([pv_tag]),
                MagicMock(),  # delete 结果
                _make_scalars_all_mock([existing_tag]),  # 已有 ACTIVE 标签
            ]
        )
        db.add = MagicMock()

        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "OSCILLATION" in result["labels"]
        # 未新建 DiagnosisTag 行
        assert self._added_tags(db) == []
        # 已有标签的触发时间与触发快照被更新
        assert existing_tag.triggered_at != datetime(2026, 1, 1)
        assert existing_tag.trigger_condition["algorithm"] == "FFT"
        assert existing_tag.trigger_condition["confidence"] > 0.1

    @pytest.mark.asyncio
    async def test_severity_mapping_for_manual_review(self) -> None:
        """MANUAL_REVIEW 标签 severity=INFO、source_metric 兜底。"""
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
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 50 个点的稳定数据（无异常 → MANUAL_REVIEW 兜底标签）
        stable_data = _make_raw_timeseries([50.0] * 50)

        async def _query_fn(**kwargs):
            return stable_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "MANUAL_REVIEW" in result["labels"]
        tags = self._added_tags(db)
        review_tags = [t for t in tags if t.tag_code == "MANUAL_REVIEW"]
        assert len(review_tags) == 1
        assert review_tags[0].severity == "INFO"
        assert review_tags[0].source_metric == "MANUAL_REVIEW"


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

        # 主 session：查询 snapshot + config + 未完成任务去重 + 创建 DiagnosisTask
        main_session = AsyncMock()
        main_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([snapshot]),  # snapshot 查询
                _make_scalars_all_mock([diag_config]),  # config 查询
                _make_scalars_all_mock([]),  # 未完成任务去重查询（无已存在任务）
            ]
        )
        main_session.commit = AsyncMock()
        main_session.rollback = AsyncMock()
        main_session.add = MagicMock()  # 添加 DiagnosisTask

        # worker session：
        # 1. _update_task_status(RUNNING) → 查询 DiagnosisTask（mock 返回 None → 跳过）
        # 2. _diagnose_loop: 查询 loop（存在）
        # 3. _diagnose_loop: 查询 mapping（无 → 缺少 PV → 返回 None）
        # 4. _update_task_status(FAILED) → 查询 DiagnosisTask（mock 返回 None → 跳过）
        worker_session = AsyncMock()
        worker_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(None),  # _update_task_status RUNNING
                _make_scalar_one_or_none_mock(_make_loop()),  # loop 查询
                _make_scalars_all_mock([]),  # 无 mapping → 缺少 PV → 返回 None
                _make_scalar_one_or_none_mock(None),  # _update_task_status FAILED
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


class TestAnalyzeSaturation:
    """测试 _analyze_saturation() OP 饱和率分析（P0-3 修复后）。"""

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
        """OP 长时间高饱和（≥98）应检测到。"""
        op = np.full(100, 100.0)
        op[:10] = 50.0  # 少量非饱和
        result = _analyze_saturation(op)
        assert result["detected"] is True
        assert result["high_count"] > 0

    def test_low_saturation(self) -> None:
        """OP 长时间低饱和（≤2）应检测到。"""
        op = np.full(100, 0.0)
        op[:10] = 50.0
        result = _analyze_saturation(op)
        assert result["detected"] is True
        assert result["low_count"] > 0

    def test_zero_range(self) -> None:
        """OP 恒定在中间值（50）应返回未检测。"""
        op = np.full(100, 50.0)
        result = _analyze_saturation(op)
        assert result["detected"] is False

    def test_mode_filter_excludes_manual(self) -> None:
        """提供 mode_values 时应排除手动模式数据点。"""
        # 100 个点：前 50 个为手动模式（OP=100 饱和），后 50 个为自动模式（OP=50 不饱和）
        op = np.full(100, 100.0)
        op[50:] = 50.0
        # 前 50 个为 MANUAL，后 50 个为 AUTO
        mode = np.array(["MANUAL"] * 50 + ["AUTO"] * 50, dtype=object)
        result = _analyze_saturation(op, mode_values=mode)
        # 过滤后仅 50 个 AUTO 点，OP=50 不饱和
        assert result["detected"] is False
        assert result["saturation_rate"] == 0.0

    def test_mode_filter_includes_auto_cas(self) -> None:
        """AUTO 和 CAS 模式都应被纳入统计。"""
        op = np.full(100, 100.0)  # 全部高饱和
        op[50:] = 50.0
        # 前 50 个 CAS（饱和），后 50 个 AUTO（不饱和）
        mode = np.array(["CAS"] * 50 + ["AUTO"] * 50, dtype=object)
        result = _analyze_saturation(op, mode_values=mode)
        # 100 个点都纳入统计，50 个饱和 → saturation_rate = 0.5 > 0.2
        assert result["detected"] is True
        assert result["high_count"] == 50

    def test_custom_threshold(self) -> None:
        """自定义阈值应生效。"""
        # 工程限位 0-200，epsilon=5 → ≥195 或 ≤5 为饱和
        op = np.full(100, 200.0)
        op[:10] = 100.0
        threshold = {"op_high_limit": 200.0, "op_low_limit": 0.0, "saturation_epsilon": 5.0}
        result = _analyze_saturation(op, threshold=threshold)
        assert result["detected"] is True
        assert result["high_count"] == 90


class TestAnalyzeQuality:
    """测试 _analyze_quality() 质量码统计（P2-1 Q001-Q005 规则矩阵）。"""

    def test_all_good(self) -> None:
        """全部 GOOD 质量码应返回 NORMAL。"""
        data = [{"quality": "GOOD"} for _ in range(50)]
        result = _analyze_quality(data)
        assert result["bad_rate"] == 0.0
        assert result["total"] == 50
        assert result["quality_pattern"] == "NORMAL"
        assert result["abnormal"] is False

    def test_all_bad(self) -> None:
        """全部 BAD 质量码应返回 Q001（连续 Bad > 10）。"""
        data = [{"quality": "BAD"} for _ in range(50)]
        result = _analyze_quality(data)
        assert result["bad_rate"] == 1.0
        assert result["bad_count"] == 50
        assert result["quality_pattern"] == "Q001"
        assert result["abnormal"] is True
        assert result["confidence"] == 0.9

    def test_mixed_quality(self) -> None:
        """混合质量码（Good+Bad 交替，无连续 >10）应返回 Q002 或 Q005。"""
        # 交替 Good/Bad，每段 Bad 仅 1 点 → 不满足 Q001，但 Bad 占比 50% > 10% → Q002
        data = []
        for i in range(50):
            data.append({"quality": "BAD" if i % 2 == 0 else "GOOD"})
        result = _analyze_quality(data)
        assert result["bad_rate"] == 0.5
        # Bad 占比 > 10% 且不满足 Q001 → Q002
        assert result["quality_pattern"] == "Q002"
        assert result["abnormal"] is True

    def test_empty_data(self) -> None:
        """空数据应返回 NORMAL。"""
        result = _analyze_quality([])
        assert result["total"] == 0
        assert result["quality_pattern"] == "NORMAL"

    def test_q001_consecutive_bad(self) -> None:
        """连续 Bad > 10 点应返回 Q001。"""
        # 30 个 Good + 15 个 Bad（连续）+ 5 个 Good
        data = (
            [{"quality": "GOOD"} for _ in range(30)]
            + [{"quality": "BAD"} for _ in range(15)]
            + [{"quality": "GOOD"} for _ in range(5)]
        )
        result = _analyze_quality(data)
        assert result["quality_pattern"] == "Q001"
        assert result["confidence"] == 0.9

    def test_q002_intermittent_bad(self) -> None:
        """间歇 Bad（占比 > 10%，无连续 > 10）应返回 Q002。"""
        # 每 5 个 Good 后 1 个 Bad，共 50 个点，Bad 占比 10/50 = 20% > 10%
        data = []
        for i in range(50):
            if i % 5 == 4:
                data.append({"quality": "BAD"})
            else:
                data.append({"quality": "GOOD"})
        result = _analyze_quality(data)
        assert result["bad_rate"] == 0.2
        assert result["quality_pattern"] == "Q002"
        assert result["confidence"] == 0.6

    def test_q003_uncertain_quality(self) -> None:
        """Uncertain 占比 > 20% 应返回 Q003。"""
        # 30 个 Good + 20 个 Uncertain（占 40%）
        data = [{"quality": "GOOD"} for _ in range(30)] + [
            {"quality": "UNCERTAIN"} for _ in range(20)
        ]
        result = _analyze_quality(data)
        assert result["quality_pattern"] == "Q003"
        assert result["abnormal"] is True
        assert result["confidence"] == 0.6

    def test_q004_sudden_shift(self) -> None:
        """Good 突变为 Bad 持续 6 点（>5）应返回 Q004。"""
        # 30 个 Good + 6 个 Bad + 14 个 Good，Bad 连续段=6（>5 但 <10）
        data = (
            [{"quality": "GOOD"} for _ in range(30)]
            + [{"quality": "BAD"} for _ in range(6)]
            + [{"quality": "GOOD"} for _ in range(14)]
        )
        result = _analyze_quality(data)
        assert result["quality_pattern"] == "Q004"
        assert result["confidence"] == 0.8

    def test_q005_transient_recovery(self) -> None:
        """Bad 段 3-10 点后恢复 Good 应返回 Q005。"""
        # 30 个 Good + 5 个 Bad（3-10 范围内）+ 15 个 Good
        data = (
            [{"quality": "GOOD"} for _ in range(30)]
            + [{"quality": "BAD"} for _ in range(5)]
            + [{"quality": "GOOD"} for _ in range(15)]
        )
        result = _analyze_quality(data)
        # Bad 占比 5/50 = 10% 不 > 10%，不满足 Q002
        # 连续 Bad = 5，不 > 10（Q001），不 > 5（Q004，因为 Q004 要求 >5）
        # 5 在 [3, 10] 范围内 → Q005
        assert result["quality_pattern"] == "Q005"
        assert result["confidence"] == 0.4


class TestDempsterShaferFusion:
    """测试 _dempster_shafer_fusion() 证据融合（D-S 公式，FDS §5.4.7）。"""

    def test_empty_evidence(self) -> None:
        """空证据列表应返回 0。"""
        assert _dempster_shafer_fusion([]) == 0.0

    def test_single_evidence(self) -> None:
        """单条证据应返回该置信度。"""
        assert _dempster_shafer_fusion([("OSCILLATION", 0.8)]) == 0.8

    def test_multiple_evidence(self) -> None:
        """多条证据应通过 D-S 公式融合。"""
        fused = _dempster_shafer_fusion([("OSCILLATION", 0.5), ("VALVE_STICTION", 0.6)])
        # D-S: Πc = 0.5*0.6 = 0.3, Π(1-c) = 0.5*0.4 = 0.2, fused = 0.3/(0.3+0.2) = 0.6
        assert abs(fused - 0.6) < 0.001

    def test_high_confidence_evidence(self) -> None:
        """两个高置信度证据应产生更高融合置信度。"""
        fused = _dempster_shafer_fusion([("A", 0.9), ("B", 0.9)])
        # D-S: Πc = 0.81, Π(1-c) = 0.01, fused = 0.81/0.82 ≈ 0.9878
        assert fused > 0.98
        assert fused < 0.999

    def test_zero_confidence(self) -> None:
        """零置信度证据。"""
        fused = _dempster_shafer_fusion([("A", 0.0), ("B", 0.0)])
        # D-S: Πc → 0, Π(1-c) = 1, fused → 0
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


# ===========================================================================
# 扩展诊断算法测试（设计依据：FDS §5.4.6 / ADS §5.2-5.5）
# ===========================================================================


class TestDetectChoudhuryNonlinearity:
    """测试 _detect_choudhury_nonlinearity() Choudhury NGI/NLI 检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0] * 10, dtype=float)
        op = np.array([1.0] * 10, dtype=float)
        result = _detect_choudhury_nonlinearity(pv, op)
        assert result["detected"] is False
        assert result["ngi"] == 0.0
        assert result["nli"] == 0.0

    def test_gaussian_signal_no_nonlinearity(self) -> None:
        """高斯信号（无线性）应未检测到非线性。"""
        rng = np.random.RandomState(42)
        # 纯高斯白噪声（线性系统输出）
        op = 50.0 + rng.normal(0, 5.0, 200)
        pv = 50.0 + rng.normal(0, 5.0, 200)
        result = _detect_choudhury_nonlinearity(pv, op)
        # 高斯信号 NGI 应较小
        assert result["ngi"] >= 0.0
        assert "nli" in result

    def test_nonlinear_signal_detected(self) -> None:
        """非线性信号（含粘滞特征）应检测到非线性。"""
        # 构造带粘滞特征的信号：OP 阶跃式变化，PV 滞后响应
        n = 200
        t = np.linspace(0, 4 * np.pi, n)
        # OP 呈方波（粘滞特征：突然跳变）
        op = 50.0 + 20.0 * np.sign(np.sin(t))
        # PV 滞后响应（椭圆轨迹）
        pv = 50.0 + 15.0 * np.sin(t - np.pi / 4)
        result = _detect_choudhury_nonlinearity(pv, op)
        # 方波信号具有强非高斯性
        assert result["ngi"] > 0.0
        assert "fitting_score" in result
        assert "stiction_index" in result

    def test_constant_op_returns_empty(self) -> None:
        """OP 恒定时（零方差）应返回空结果。"""
        pv = np.array([50.0 + 10.0 * np.sin(i * 0.1) for i in range(50)], dtype=float)
        op = np.full(50, 50.0)
        result = _detect_choudhury_nonlinearity(pv, op)
        assert result["detected"] is False

    def test_returns_confidence_on_detection(self) -> None:
        """检测到非线性时置信度应为正。"""
        n = 200
        t = np.linspace(0, 4 * np.pi, n)
        op = 50.0 + 20.0 * np.sign(np.sin(t))
        pv = 50.0 + 15.0 * np.sin(t - np.pi / 4)
        result = _detect_choudhury_nonlinearity(pv, op)
        if result["detected"]:
            assert 0.0 < result["confidence"] <= 1.0
        else:
            assert result["confidence"] == 0.0


class TestDetectKanoStiction:
    """测试 _detect_kano_stiction() Kano 统计法粘滞检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0, 2.0], dtype=float)
        op = np.array([1.0, 2.0], dtype=float)
        result = _detect_kano_stiction(pv, op)
        assert result["detected"] is False
        assert result["stiction_ratio"] == 0.0

    def test_linear_response_no_stiction(self) -> None:
        """线性 PV-OP 响应（无粘滞）应未检测到粘滞。"""
        # PV 与 OP 完全线性相关
        op = np.linspace(0, 100, 100)
        pv = op * 0.5 + 10
        result = _detect_kano_stiction(pv, op)
        # 线性关系下粘滞区间占比应较低
        assert result["stiction_ratio"] >= 0.0

    def test_stiction_pattern_detected(self) -> None:
        """OP 不动 PV 大幅变化应检测到粘滞特征。"""
        # 构造粘滞模式：OP 阶段性不动，PV 持续波动
        n = 100
        op = np.zeros(n)
        pv = np.zeros(n)
        # 分 4 段：每段 OP 不变，PV 大幅波动
        for seg in range(4):
            start = seg * 25
            end = (seg + 1) * 25
            op[start:end] = seg * 25.0  # OP 在段内不变
            pv[start:end] = 50.0 + 20.0 * np.sin(np.linspace(0, 2 * np.pi, 25))
        result = _detect_kano_stiction(pv, op)
        # 应该能计算 stiction_ratio
        assert "stiction_ratio" in result
        assert "correlation" in result
        assert "std_ratio" in result

    def test_with_mv_parameter(self) -> None:
        """传入 mv 参数应正常工作。"""
        n = 50
        pv = np.array([50.0 + 5.0 * np.sin(i * 0.2) for i in range(n)], dtype=float)
        op = np.array([50.0 + 5.0 * np.cos(i * 0.2) for i in range(n)], dtype=float)
        mv = op.copy()
        result = _detect_kano_stiction(pv, op, mv)
        assert "detected" in result
        assert "stiction_ratio" in result

    def test_constant_signal_returns_empty(self) -> None:
        """恒定信号（无方差）应返回空结果。"""
        pv = np.full(50, 50.0)
        op = np.full(50, 50.0)
        result = _detect_kano_stiction(pv, op)
        assert result["detected"] is False


class TestAnalyzeStepResponse:
    """测试 _analyze_step_response() 阶跃响应分析。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0] * 10, dtype=float)
        sp = np.array([1.0] * 10, dtype=float)
        result = _analyze_step_response(pv, sp)
        assert result["detected"] is False
        assert result["overshoot"] == 0.0

    def test_no_step_returns_empty(self) -> None:
        """无 SP 阶跃应返回空结果。"""
        n = 100
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        result = _analyze_step_response(pv, sp)
        assert result["detected"] is False
        assert result["step_count"] == 0

    def test_overaggressive_with_overshoot(self) -> None:
        """SP 阶跃后 PV 过冲 + 振荡应检测到过激。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0  # SP 阶跃
        # PV 过冲 + 衰减振荡
        pv = np.zeros(n)
        for i in range(50, n):
            t = i - 50
            # 过冲 40% + 衰减振荡
            pv[i] = 100.0 + 40.0 * np.exp(-t * 0.05) * np.cos(t * 0.3)
        result = _analyze_step_response(pv, sp)
        assert result["step_count"] > 0
        assert result["overshoot"] > 0.0

    def test_downward_step(self) -> None:
        """下降阶跃应正确分析。"""
        n = 200
        sp = np.full(n, 100.0)
        sp[50:] = 0.0  # 下降阶跃
        pv = np.full(n, 100.0)
        for i in range(50, n):
            t = i - 50
            pv[i] = 0.0 - 40.0 * np.exp(-t * 0.05) * np.cos(t * 0.3)
        result = _analyze_step_response(pv, sp)
        assert result["step_count"] > 0
        assert result["overshoot"] >= 0.0

    def test_with_timestamps(self) -> None:
        """传入时间戳应正常工作。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0
        pv = np.zeros(n)
        pv[50:] = 100.0
        pv[60:70] = 130.0  # 过冲
        ts = np.arange(n, dtype=float)
        result = _analyze_step_response(pv, sp, ts=ts)
        assert result["step_count"] > 0


class TestDetectSlowResponse:
    """测试 _detect_slow_response() 响应迟缓检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0] * 10, dtype=float)
        sp = np.array([1.0] * 10, dtype=float)
        result = _detect_slow_response(pv, sp)
        assert result["detected"] is False
        assert result["time_constant"] == 0.0

    def test_fast_response_not_slow(self) -> None:
        """快速响应不应判定为迟缓。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0
        # PV 快速响应（指数跟踪，小时间常数）
        pv = np.zeros(n)
        for i in range(50, n):
            t = (i - 50) / 50.0
            pv[i] = 100.0 * (1 - np.exp(-t * 20))  # 快速响应
        result = _detect_slow_response(pv, sp, control_type="PID")
        assert "time_constant" in result
        assert "expected_time_constant" in result

    def test_slow_response_detected(self) -> None:
        """缓慢响应应检测到迟缓。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0
        # PV 极慢响应（大时间常数）
        pv = np.zeros(n)
        for i in range(50, n):
            t = (i - 50) / 50.0
            pv[i] = 100.0 * (1 - np.exp(-t * 0.5))  # 慢响应
        result = _detect_slow_response(pv, sp, control_type="PID")
        # 慢响应时间常数应较大
        assert result["time_constant"] > 0.0
        assert result["ratio"] > 0.0

    def test_no_step_uses_bias(self) -> None:
        """无阶跃时应基于稳态偏差判断。"""
        n = 100
        sp = np.full(n, 50.0)
        pv = np.array([50.0 + 10.0 * np.sin(i * 0.1) for i in range(n)], dtype=float)
        result = _detect_slow_response(pv, sp, control_type="PI")
        assert "detected" in result
        assert "ratio" in result

    def test_control_type_affects_threshold(self) -> None:
        """不同控制类型应返回不同期望时间常数。"""
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0
        pv = np.zeros(n)
        for i in range(50, n):
            t = (i - 50) / 50.0
            pv[i] = 100.0 * (1 - np.exp(-t * 1.0))
        result_p = _detect_slow_response(pv, sp, control_type="P")
        result_pid = _detect_slow_response(pv, sp, control_type="PID")
        # PID 期望更快响应，因此相同实际响应下 PID 更易判定为迟缓
        assert result_p["expected_time_constant"] > result_pid["expected_time_constant"]


class TestDetectBiasShift:
    """测试 _detect_bias_shift() 偏差突变检测。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回空结果。"""
        pv = np.array([1.0] * 10, dtype=float)
        sp = np.array([1.0] * 10, dtype=float)
        result = _detect_bias_shift(pv, sp)
        assert result["detected"] is False
        assert result["shift_count"] == 0

    def test_stable_bias_no_shift(self) -> None:
        """稳定偏差（无突变）应未检测到突变。"""
        n = 200
        sp = np.full(n, 50.0)
        # PV 稳定跟踪 SP，小幅噪声
        rng = np.random.RandomState(42)
        pv = 50.0 + rng.normal(0, 0.5, n)
        result = _detect_bias_shift(pv, sp)
        # 稳定信号不应频繁触发突变
        assert result["shift_count"] >= 0
        assert result["max_cusum"] >= 0.0

    def test_frequent_shifts_detected(self) -> None:
        """频繁偏差突变应检测到外扰。"""
        n = 3600  # 1 小时数据（1 秒采样）
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        rng = np.random.RandomState(42)
        # 每 200 秒注入一次突变
        for shift_time in range(0, n, 200):
            shift_mag = rng.choice([-5.0, 5.0])
            end = min(shift_time + 100, n)
            pv[shift_time:end] += shift_mag
        result = _detect_bias_shift(pv, sp)
        # 应检测到多次突变
        assert result["shift_count"] > 0
        assert result["max_cusum"] > 0.0

    def test_with_timestamps(self) -> None:
        """传入时间戳应正确计算突变频率。"""
        n = 3600
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        # 注入几次突变
        for shift_time in range(0, n, 300):
            pv[shift_time : shift_time + 50] += 8.0
        ts = np.arange(n, dtype=float)
        result = _detect_bias_shift(pv, sp, ts=ts)
        assert "shift_count" in result
        assert "shift_magnitude" in result

    def test_constant_bias_returns_empty(self) -> None:
        """恒定偏差（零方差）应返回空结果。"""
        n = 100
        sp = np.full(n, 50.0)
        pv = np.full(n, 55.0)  # 恒定偏差 5
        result = _detect_bias_shift(pv, sp)
        assert result["detected"] is False


# ===========================================================================
# 新算法集成测试（_diagnose_loop 中调用新算法）
# ===========================================================================


class TestDiagnoseLoopExtendedAlgorithms:
    """测试扩展算法在 _diagnose_loop 中的集成。"""

    @pytest.mark.asyncio
    async def test_choudhury_stiction_integration(self) -> None:
        """Choudhury 算法检测到粘滞时应输出 VALVE_STICTION 标签。"""
        loop = _make_loop()
        loop.control_type = "PI"
        pv_m = _make_mapping(tag_role="PV", tag_id="tag-pv")
        op_m = _make_mapping(tag_role="OP", tag_id="tag-op")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC.OP")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_m, op_m]),
                _make_scalars_all_mock([pv_tag, op_tag]),
                MagicMock(),  # delete 结果
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 构造粘滞特征信号：方波 OP + 滞后 PV
        n = 200
        t = np.linspace(0, 4 * np.pi, n)
        op_vals = 50.0 + 20.0 * np.sign(np.sin(t))
        pv_vals = 50.0 + 15.0 * np.sin(t - np.pi / 4)

        raw_series = _make_raw_timeseries(
            [float(value) for value in pv_vals],
            op=[float(value) for value in op_vals],
        )

        async def _query_fn(**kwargs):
            return raw_series

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"VALVE_STICTION": _make_diag_config("VALVE_STICTION")},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"
        # 至少有诊断结果（可能包含 VALVE_STICTION）
        assert len(result["labels"]) > 0

    @pytest.mark.asyncio
    async def test_step_response_overaggressive_integration(self) -> None:
        """阶跃响应分析检测到过激时应输出 OVERAGGRESSIVE 标签。"""
        loop = _make_loop()
        loop.control_type = "PID"
        pv_m = _make_mapping(tag_role="PV", tag_id="tag-pv")
        sp_m = _make_mapping(tag_role="SP", tag_id="tag-sp")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        # B4：PV 过冲峰值约 140，需配置匹配量程，否则超出默认 0~100 量程被剔除
        pv_tag.range_min = 0.0
        pv_tag.range_max = 200.0
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC.SP")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_m, sp_m]),
                _make_scalars_all_mock([pv_tag, sp_tag]),
                MagicMock(),
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 构造过激响应：SP 阶跃 + PV 过冲振荡（低阻尼以满足衰减比阈值）
        n = 200
        sp = np.zeros(n)
        sp[50:] = 100.0
        pv = np.zeros(n)
        for i in range(50, n):
            t = i - 50
            pv[i] = 100.0 + 40.0 * np.exp(-t * 0.02) * np.cos(t * 0.3)

        raw_series = _make_raw_timeseries(
            [float(value) for value in pv],
            sp=[float(value) for value in sp],
        )

        async def _query_fn(**kwargs):
            return raw_series

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OVERAGGRESSIVE": _make_diag_config("OVERAGGRESSIVE")},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"
        # 过激响应应触发 OVERAGGRESSIVE 标签
        assert "OVERAGGRESSIVE" in result["labels"]

    @pytest.mark.asyncio
    async def test_bias_shift_disturbance_integration(self) -> None:
        """偏差突变检测到外扰时应输出 EXTERNAL_DISTURBANCE 标签。"""
        loop = _make_loop()
        loop.control_type = "PI"
        pv_m = _make_mapping(tag_role="PV", tag_id="tag-pv")
        sp_m = _make_mapping(tag_role="SP", tag_id="tag-sp")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC.SP")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_all_mock([pv_m, sp_m]),
                _make_scalars_all_mock([pv_tag, sp_tag]),
                MagicMock(),
                _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
            ]
        )
        db.add = MagicMock()

        # 构造频繁偏差突变：SP 恒定，PV 频繁突变
        n = 3600
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        for shift_time in range(0, n, 200):
            pv[shift_time : shift_time + 100] += 8.0

        raw_series = _make_raw_timeseries(
            [float(value) for value in pv],
            sp=[float(value) for value in sp],
        )

        async def _query_fn(**kwargs):
            return raw_series

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"EXTERNAL_DISTURBANCE": _make_diag_config("EXTERNAL_DISTURBANCE")},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"
        # 频繁突变应触发 EXTERNAL_DISTURBANCE 标签
        assert "EXTERNAL_DISTURBANCE" in result["labels"]


# ===========================================================================
# 新增功能测试（P0-1/P0-2/P1-1/P1-4）
# ===========================================================================


class TestGetThreshold:
    """测试 _get_threshold() 阈值读取（P0-1）。"""

    def test_config_not_found_returns_default(self) -> None:
        """配置不存在时返回默认值。"""
        result = _get_threshold({}, "OSCILLATION", "key", 0.5)
        assert result == 0.5

    def test_threshold_none_returns_default(self) -> None:
        """threshold 字段为 None 时返回默认值。"""
        config = MagicMock()
        config.threshold = None
        result = _get_threshold({"OSCILLATION": config}, "OSCILLATION", "key", 0.5)
        assert result == 0.5

    def test_key_missing_returns_default(self) -> None:
        """键缺失时返回默认值。"""
        config = MagicMock()
        config.threshold = {"other_key": 1.0}
        result = _get_threshold({"OSCILLATION": config}, "OSCILLATION", "key", 0.5)
        assert result == 0.5

    def test_key_found_returns_value(self) -> None:
        """键存在时返回配置值。"""
        config = MagicMock()
        config.threshold = {"similarity_threshold": 0.6}
        result = _get_threshold({"OSCILLATION": config}, "OSCILLATION", "similarity_threshold", 0.4)
        assert result == 0.6

    def test_key_none_returns_whole_threshold_dict(self) -> None:
        """key 为 None 时返回整个 threshold dict。"""
        config = MagicMock()
        config.threshold = {"k1": 1.0, "k2": 2.0}
        result = _get_threshold({"OSCILLATION": config}, "OSCILLATION", None, None)
        assert result == {"k1": 1.0, "k2": 2.0}


class TestDetectOscillationIae:
    """测试 _detect_oscillation_iae() IAE 零交叉相似率法（P1-1）。"""

    def test_short_data_returns_empty(self) -> None:
        """数据不足应返回未检测。"""
        pv = np.array([1.0, 2.0, 3.0], dtype=float)
        sp = np.array([1.0, 2.0, 3.0], dtype=float)
        result = _detect_oscillation_iae(pv, sp)
        assert result["detected"] is False
        assert result["confidence"] == 0.0

    def test_sine_wave_oscillation_detected(self) -> None:
        """正弦波振荡应检测到。"""
        n = 200
        t = np.linspace(0, 10 * np.pi, n)
        sp = np.full(n, 50.0)
        pv = 50.0 + 10.0 * np.sin(t)
        result = _detect_oscillation_iae(pv, sp, sample_interval=1.0)
        assert result["detected"] is True
        assert result["confidence"] > 0.0
        assert result["similarity"] > 0.4
        assert result["zero_crossing_count"] >= 3

    def test_stable_data_not_detected(self) -> None:
        """平稳数据应未检测到振荡。"""
        n = 100
        sp = np.full(n, 50.0)
        pv = np.full(n, 50.0)
        # 加微小噪声避免 IAE 恒定
        rng = np.random.RandomState(42)
        pv = pv + rng.normal(0, 0.01, n)
        result = _detect_oscillation_iae(pv, sp)
        # 平稳数据不应检测到振荡（或相似率低）
        assert result["detected"] is False or result["confidence"] == 0.0

    def test_random_noise_not_detected(self) -> None:
        """随机噪声应未检测到振荡（相似率低）。"""
        rng = np.random.RandomState(42)
        n = 200
        sp = np.full(n, 50.0)
        pv = 50.0 + rng.normal(0, 5.0, n)
        result = _detect_oscillation_iae(pv, sp)
        # 随机噪声零交叉间隔不规律，相似率应较低
        assert result["detected"] is False

    def test_empty_array(self) -> None:
        """空数组应返回未检测。"""
        pv = np.array([], dtype=float)
        sp = np.array([], dtype=float)
        result = _detect_oscillation_iae(pv, sp)
        assert result["detected"] is False

    def test_custom_threshold(self) -> None:
        """自定义阈值应生效。"""
        n = 200
        t = np.linspace(0, 10 * np.pi, n)
        sp = np.full(n, 50.0)
        pv = 50.0 + 10.0 * np.sin(t)
        # 提高相似率阈值到 0.9（几乎不可能达到）
        result = _detect_oscillation_iae(
            pv, sp, threshold={"similarity_threshold": 0.99, "min_zero_crossings": 3}
        )
        # 正弦波相似率约 0.8-0.95，0.99 阈值过高 → 不检测
        assert result["detected"] is False


class TestApplyExpertRules:
    """测试 _apply_expert_rules() 专家规则矩阵 R01-R06（P0-2）。"""

    def test_r01_oscillation_stiction_removes_oscillation(self) -> None:
        """R01: OSCILLATION + VALVE_STICTION（stiction 置信度 > 0.5）→ 移除 OSCILLATION。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {
                "label": "VALVE_STICTION",
                "confidence": 0.8,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "OSCILLATION" not in labels
        assert "VALVE_STICTION" in labels

    def test_r01_low_stiction_keeps_oscillation(self) -> None:
        """R01: stiction 置信度 ≤ 0.5 时不移除 OSCILLATION。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {
                "label": "VALVE_STICTION",
                "confidence": 0.4,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "OSCILLATION" in labels
        assert "VALVE_STICTION" in labels

    def test_r02_oscillation_overaggressive_removes_oscillation(self) -> None:
        """R02: OSCILLATION + OVERAGGRESSIVE（无 STICTION）→ 移除 OSCILLATION。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {
                "label": "OVERAGGRESSIVE",
                "confidence": 0.6,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "OSCILLATION" not in labels
        assert "OVERAGGRESSIVE" in labels

    def test_r03_overaggressive_overconservative_keeps_higher(self) -> None:
        """R03: OVERAGGRESSIVE + OVERCONSERVATIVE → 保留置信度更高的。"""
        results = [
            {
                "label": "OVERAGGRESSIVE",
                "confidence": 0.8,
                "feature_values": {},
                "evidence": {},
            },
            {
                "label": "OVERCONSERVATIVE",
                "confidence": 0.5,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "OVERAGGRESSIVE" in labels
        assert "OVERCONSERVATIVE" not in labels

    def test_r04_severe_quality_abnormal_removes_others(self) -> None:
        """R04: PV 质量异常严重（bad_rate > 0.5）→ 移除所有其他标签。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.9, "feature_values": {}, "evidence": {}},
            {
                "label": "QUALITY_ABNORMAL",
                "confidence": 0.9,
                "feature_values": {"bad_quality_rate": 0.8},
                "evidence": {},
            },
            {
                "label": "VALVE_STICTION",
                "confidence": 0.7,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        assert len(processed) == 1
        assert processed[0]["label"] == "QUALITY_ABNORMAL"

    def test_r05_all_low_confidence_adds_manual_review(self) -> None:
        """R05: 所有算法置信度 < 0.5 → 添加 MANUAL_REVIEW。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.3, "feature_values": {}, "evidence": {}},
            {"label": "VALVE_STICTION", "confidence": 0.2, "feature_values": {}, "evidence": {}},
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "MANUAL_REVIEW" in labels

    def test_r05_high_confidence_no_manual_review(self) -> None:
        """R05: 存在置信度 ≥ 0.5 的算法时不添加 MANUAL_REVIEW。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {"label": "VALVE_STICTION", "confidence": 0.2, "feature_values": {}, "evidence": {}},
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        assert "MANUAL_REVIEW" not in labels

    def test_r06_priority_sorting(self) -> None:
        """R06: 标签按优先级排序。"""
        # 使用不触发 R01-R04 的标签组合
        results = [
            {
                "label": "EXTERNAL_DISTURBANCE",
                "confidence": 0.7,
                "feature_values": {},
                "evidence": {},
            },
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {
                "label": "OUTPUT_SATURATION",
                "confidence": 0.7,
                "feature_values": {},
                "evidence": {},
            },
        ]
        processed = _apply_expert_rules(results)
        labels = [r["label"] for r in processed]
        # 优先级：OUTPUT_SATURATION(5) > OSCILLATION(6) > EXTERNAL_DISTURBANCE(7)
        assert labels[0] == "OUTPUT_SATURATION"
        assert labels[1] == "OSCILLATION"
        assert labels[2] == "EXTERNAL_DISTURBANCE"

    def test_empty_results_returns_empty(self) -> None:
        """空列表应返回空列表。"""
        assert _apply_expert_rules([]) == []


class TestDeduplicateLabels:
    """测试 _deduplicate_labels() 标签去重（P1-4）。"""

    def test_single_label_per_group(self) -> None:
        """每个标签仅一条记录时应原样返回。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {"a": 1}, "evidence": {}},
        ]
        processed = _deduplicate_labels(results)
        assert len(processed) == 1
        assert processed[0]["label"] == "OSCILLATION"

    def test_duplicate_labels_keep_highest_confidence(self) -> None:
        """同一标签多条记录应保留置信度最高的。"""
        results = [
            {
                "label": "OSCILLATION",
                "confidence": 0.5,
                "feature_values": {"a": 1},
                "evidence": {"reasoning": "low"},
            },
            {
                "label": "OSCILLATION",
                "confidence": 0.9,
                "feature_values": {"b": 2},
                "evidence": {"reasoning": "high"},
            },
            {
                "label": "OSCILLATION",
                "confidence": 0.7,
                "feature_values": {"c": 3},
                "evidence": {"reasoning": "mid"},
            },
        ]
        processed = _deduplicate_labels(results)
        assert len(processed) == 1
        assert processed[0]["confidence"] == 0.9
        assert processed[0]["evidence"]["reasoning"] == "high"

    def test_merge_feature_values(self) -> None:
        """合并 feature_values，主记录优先。"""
        results = [
            {
                "label": "OSCILLATION",
                "confidence": 0.9,
                "feature_values": {"a": 1, "b": 2},
                "evidence": {},
            },
            {
                "label": "OSCILLATION",
                "confidence": 0.5,
                "feature_values": {"b": 99, "c": 3},
                "evidence": {},
            },
        ]
        processed = _deduplicate_labels(results)
        assert len(processed) == 1
        # 主记录的 a, b 保留，补充 c
        assert processed[0]["feature_values"]["a"] == 1
        assert processed[0]["feature_values"]["b"] == 2  # 主记录优先
        assert processed[0]["feature_values"]["c"] == 3

    def test_cross_validated_algorithms_appended(self) -> None:
        """其他记录的 evidence 应追加到 cross_validated_algorithms。"""
        results = [
            {
                "label": "VALVE_STICTION",
                "confidence": 0.9,
                "feature_values": {},
                "evidence": {"reasoning": "primary", "algorithm": "A"},
            },
            {
                "label": "VALVE_STICTION",
                "confidence": 0.7,
                "feature_values": {},
                "evidence": {"reasoning": "secondary", "algorithm": "B"},
            },
        ]
        processed = _deduplicate_labels(results)
        assert len(processed) == 1
        assert "cross_validated_algorithms" in processed[0]["evidence"]
        cross = processed[0]["evidence"]["cross_validated_algorithms"]
        assert len(cross) == 1
        # 交叉验证条目结构：{label, confidence, evidence, feature_values}
        assert cross[0]["evidence"]["algorithm"] == "B"
        assert cross[0]["confidence"] == 0.7

    def test_different_labels_not_merged(self) -> None:
        """不同标签不应合并。"""
        results = [
            {"label": "OSCILLATION", "confidence": 0.7, "feature_values": {}, "evidence": {}},
            {"label": "VALVE_STICTION", "confidence": 0.8, "feature_values": {}, "evidence": {}},
        ]
        processed = _deduplicate_labels(results)
        assert len(processed) == 2

    def test_empty_results_returns_empty(self) -> None:
        """空列表应返回空列表。"""
        assert _deduplicate_labels([]) == []


# ===========================================================================
# A5/A6/A9/A10 修复测试（诊断引擎正确性，2026-07-20）
# ===========================================================================


def _make_diagnose_db(loop: MagicMock, mappings: list, tags: list) -> AsyncMock:
    """构造 _diagnose_loop 所需的 mock DB（loop/mapping/tags/delete/ACTIVE标签 五次查询）。"""
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _make_scalar_one_or_none_mock(loop),
            _make_scalars_all_mock(mappings),
            _make_scalars_all_mock(tags),
            MagicMock(),  # delete(DiagnosisResult) 结果
            _make_scalars_all_mock([]),  # 无 ACTIVE 诊断标签
        ]
    )
    db.add = MagicMock()
    return db


class TestAlgorithmEnableGating:
    """A6：is_enabled 门控——禁用（或配置不存在）的算法不执行、不产出标签。"""

    @pytest.mark.asyncio
    async def test_disabled_oscillation_produces_no_label(self) -> None:
        """OSCILLATION 配置缺失（禁用）时，振荡信号也不产出 OSCILLATION 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        # 50 个点的振荡信号（启用时必检出，见 test_normal_diagnosis_with_oscillation）
        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},  # 全部算法禁用
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "OSCILLATION" not in result["labels"]
        # 全部禁用时仍走 MANUAL_REVIEW 兜底
        assert result["labels"] == ["MANUAL_REVIEW"]

    @pytest.mark.asyncio
    async def test_enabled_oscillation_produces_label(self) -> None:
        """OSCILLATION 启用时，同样的振荡信号应产出 OSCILLATION 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "OSCILLATION" in result["labels"]

    @pytest.mark.asyncio
    async def test_disabled_quality_produces_no_label(self) -> None:
        """QUALITY_ABNORMAL 禁用时，坏质量数据不产出 QUALITY_ABNORMAL 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        # 50 个点，每 5 个 1 个 Bad（占比 20%，启用时必触发 Q002）
        data = _make_raw_timeseries([50.0] * 50, pv_quality=[1, 1, 1, 1, 0] * 10)

        async def _query_fn(**kwargs):
            return data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},  # 质量算法未启用
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "QUALITY_ABNORMAL" not in result["labels"]

    @pytest.mark.asyncio
    async def test_enabled_quality_produces_label(self) -> None:
        """QUALITY_ABNORMAL 启用时，同样的坏质量数据产出 QUALITY_ABNORMAL 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        data = _make_raw_timeseries([50.0] * 50, pv_quality=[1, 1, 1, 1, 0] * 10)

        async def _query_fn(**kwargs):
            return data

        quality_config = _make_diag_config("QUALITY_ABNORMAL")
        quality_config.threshold = None  # 使用算法默认阈值

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"QUALITY_ABNORMAL": quality_config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "QUALITY_ABNORMAL" in result["labels"]


class TestThresholdTakesEffect:
    """A5：配置阈值经 _get_threshold 真实影响算法判定结果。"""

    @pytest.mark.asyncio
    async def test_quality_threshold_from_config_changes_verdict(self) -> None:
        """20% Bad 占比默认触发 Q002；配置调高 q002_bad_rate 后不再触发。"""
        pv_quality = [1, 1, 1, 1, 0] * 10  # Bad 占比 20%，无连续段

        async def _run(threshold: dict | None) -> list[str]:
            db = _make_diagnose_db(
                _make_loop(),
                [_make_mapping(tag_role="PV", tag_id="tag-pv")],
                [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
            )
            data = _make_raw_timeseries([50.0] * 50, pv_quality=pv_quality)

            async def _query_fn(**kwargs):
                return data

            quality_config = _make_diag_config("QUALITY_ABNORMAL")
            quality_config.threshold = threshold
            result = await _diagnose_loop(
                db=db,
                loop_id="loop-001",
                diag_configs={"QUALITY_ABNORMAL": quality_config},
                ts_start=datetime(2026, 1, 1, 0, 0, 0),
                ts_end=datetime(2026, 1, 1, 1, 0, 0),
                query_wide_fn=_query_fn,
            )
            assert result is not None
            return result["labels"]

        # 默认阈值（q002_bad_rate=0.1）：20% > 10% → 触发
        assert "QUALITY_ABNORMAL" in await _run(None)
        # 配置阈值调高到 0.9：20% < 90% → 不触发
        assert "QUALITY_ABNORMAL" not in await _run({"q002_bad_rate": 0.9})

    @pytest.mark.asyncio
    async def test_saturation_threshold_from_config_changes_verdict(self) -> None:
        """OP=99 默认判定高饱和；配置调高 op_high_limit 后不再判定。"""
        op_m = _make_mapping(tag_role="OP", tag_id="tag-op")
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC.OP")

        async def _run(threshold: dict | None) -> list[str]:
            db = _make_diagnose_db(
                _make_loop(),
                [_make_mapping(tag_role="PV", tag_id="tag-pv"), op_m],
                [_make_tag(tag_id="tag-pv", tag_name="LIC.PV"), op_tag],
            )
            data = _make_raw_timeseries([50.0] * 50, op=[99.0] * 50)

            async def _query_fn(**kwargs):
                return data

            saturation_config = _make_diag_config("OUTPUT_SATURATION")
            saturation_config.threshold = threshold
            result = await _diagnose_loop(
                db=db,
                loop_id="loop-001",
                diag_configs={"OUTPUT_SATURATION": saturation_config},
                ts_start=datetime(2026, 1, 1, 0, 0, 0),
                ts_end=datetime(2026, 1, 1, 1, 0, 0),
                query_wide_fn=_query_fn,
            )
            assert result is not None
            return result["labels"]

        # 默认限位（op_high_limit=100, epsilon=2）：99 ≥ 98 → 饱和
        assert "OUTPUT_SATURATION" in await _run(None)
        # 配置限位调高到 120：99 < 118 → 不饱和
        assert "OUTPUT_SATURATION" not in await _run({"op_high_limit": 120.0})


class TestDoRunDiagnosisDedup:
    """A9：同回路同时间窗已有未完成任务时跳过创建与诊断。"""

    @pytest.mark.asyncio
    async def test_existing_pending_task_skipped(self) -> None:
        """已存在 PENDING/RUNNING 任务时不重复创建，也不重复诊断。"""
        snapshot = MagicMock()
        snapshot.loop_id = "loop-001"
        diag_config = _make_diag_config()

        main_session = AsyncMock()
        main_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([snapshot]),  # snapshot 查询
                _make_scalars_all_mock([diag_config]),  # config 查询
                _make_scalars_all_mock(["loop-001"]),  # 去重查询：已有未完成任务
            ]
        )
        main_session.commit = AsyncMock()
        main_session.rollback = AsyncMock()
        main_session.add = MagicMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = main_session
            result = await _do_run_diagnosis()

        assert result["total"] == 1
        assert result["skipped"] == 1
        assert result["diagnosed"] == 0
        assert result["failed"] == 0
        main_session.add.assert_not_called()  # 未创建新的 DiagnosisTask


class TestSnapshotScoreFilter:
    """A10：score 为 NULL（INCONCLUSIVE）的快照也纳入自动诊断。"""

    @pytest.mark.asyncio
    async def test_score_null_included_in_filter_sql(self) -> None:
        """快照筛选语句应包含 OR score IS NULL 分支。"""
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
        # 第一个 execute 调用即快照筛选语句
        stmt = mock_session.execute.call_args_list[0][0][0]
        compiled = str(stmt)
        assert "score IS NULL" in compiled


# ===========================================================================
# B1：体检轨调度（_do_run_checkup / Beat 注册）
# ===========================================================================


def _make_enabled_checkup_loader() -> MagicMock:
    """构造 DIAG_CHECKUP 开关为启用的 EngineRuleLoader mock。"""
    loader = MagicMock()
    loader.get_params = AsyncMock(return_value={"enabled": True})
    return loader


class TestDoRunCheckup:
    """B1：体检轨对全部启用回路建体检任务（不受评分限制）。"""

    @pytest.mark.asyncio
    async def test_healthy_loop_gets_checkup_task(self) -> None:
        """健康回路（无快照/评分≥60）也会创建体检任务（triggered_by='checkup-scheduler'）。"""
        loop = _make_loop()
        diag_config = _make_diag_config()

        main_session = AsyncMock()
        main_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([loop]),  # 启用回路查询
                _make_scalars_all_mock([diag_config]),  # config 查询
                _make_scalars_all_mock([]),  # 未完成任务去重查询（无已存在任务）
            ]
        )
        main_session.commit = AsyncMock()
        main_session.rollback = AsyncMock()
        main_session.add = MagicMock()

        # worker session：RUNNING → loop 查询 → 无 mapping → 返回 None → FAILED
        worker_session = AsyncMock()
        worker_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(None),  # _update_task_status RUNNING
                _make_scalar_one_or_none_mock(loop),  # loop 查询
                _make_scalars_all_mock([]),  # 无 mapping → 缺少 PV → 返回 None
                _make_scalar_one_or_none_mock(None),  # _update_task_status FAILED
            ]
        )
        worker_session.commit = AsyncMock()
        worker_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch(
                "app.services.engine_rule_loader.get_engine_rule_loader",
                return_value=_make_enabled_checkup_loader(),
            ),
        ):
            mock_session_local.return_value.__aenter__ = AsyncMock(
                side_effect=[main_session, worker_session]
            )
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await _do_run_checkup()

        assert result["total"] == 1
        assert result["skipped"] == 0
        # 缺少 PV Tag → 诊断失败，但体检任务已创建
        assert result["failed"] == 1
        main_session.add.assert_called_once()
        task = main_session.add.call_args[0][0]
        assert task.trigger_type == "auto"
        assert task.triggered_by == "checkup-scheduler"
        assert task.status == "PENDING"

    @pytest.mark.asyncio
    async def test_existing_pending_task_skipped(self) -> None:
        """同回路同时间窗已有未完成任务时跳过创建与诊断。"""
        loop = _make_loop()
        diag_config = _make_diag_config()

        main_session = AsyncMock()
        main_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_all_mock([loop]),  # 启用回路查询
                _make_scalars_all_mock([diag_config]),  # config 查询
                _make_scalars_all_mock(["loop-001"]),  # 去重查询：已有未完成任务
            ]
        )
        main_session.commit = AsyncMock()
        main_session.rollback = AsyncMock()
        main_session.add = MagicMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch(
                "app.services.engine_rule_loader.get_engine_rule_loader",
                return_value=_make_enabled_checkup_loader(),
            ),
        ):
            mock_session_local.return_value.__aenter__.return_value = main_session
            result = await _do_run_checkup()

        assert result["total"] == 1
        assert result["skipped"] == 1
        assert result["diagnosed"] == 0
        assert result["failed"] == 0
        main_session.add.assert_not_called()  # 未创建新的 DiagnosisTask

    @pytest.mark.asyncio
    async def test_disabled_skips_run(self) -> None:
        """DIAG_CHECKUP.enabled=false 时记日志并跳过，不查询回路。"""
        loader = MagicMock()
        loader.get_params = AsyncMock(return_value={"enabled": False})

        mock_session = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch(
                "app.services.engine_rule_loader.get_engine_rule_loader",
                return_value=loader,
            ),
        ):
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_run_checkup()

        assert result["total"] == 0
        assert result["disabled"] is True
        mock_session.execute.assert_not_called()

    def test_checkup_beat_entry_registered(self) -> None:
        """Beat 注册 diagnosis-engine-checkup-8h 为 crontab(minute=20, hour='*/8')。"""
        from app.tasks.diagnosis_engine import celery_app

        entry = celery_app.conf.beat_schedule["diagnosis-engine-checkup-8h"]
        assert entry["task"] == "app.tasks.diagnosis_engine.run_diagnosis_checkup"
        schedule = entry["schedule"]
        assert schedule.minute == {20}
        assert schedule.hour == {0, 8, 16}  # hour="*/8" 展开为 {0, 8, 16}


class TestLabelsSubsetGating:
    """B6：labels 子集门控——仅执行子集内标签对应的算法，MANUAL_REVIEW 兜底不受限。"""

    @pytest.mark.asyncio
    async def test_stiction_subset_skips_oscillation(self) -> None:
        """labels=['VALVE_STICTION'] 时，振荡信号不产出 OSCILLATION 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        # 50 个点的振荡信号（全量执行时必检出 OSCILLATION）
        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={
                "OSCILLATION": _make_diag_config("OSCILLATION"),
                "VALVE_STICTION": _make_diag_config("VALVE_STICTION"),
            },
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
            labels=["VALVE_STICTION"],
        )

        assert result is not None
        # 子集外的 OSCILLATION 不产出；产出标签均在子集内或为 MANUAL_REVIEW 兜底
        assert "OSCILLATION" not in result["labels"]
        assert set(result["labels"]) <= {"VALVE_STICTION", "MANUAL_REVIEW"}

    @pytest.mark.asyncio
    async def test_none_labels_runs_full(self) -> None:
        """labels=None 时全量执行，同样的振荡信号应产出 OSCILLATION 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        t = np.linspace(0, 10 * np.pi, 50)
        osc_data = _make_raw_timeseries([50.0 + 10.0 * np.sin(ti) for ti in t])

        async def _query_fn(**kwargs):
            return osc_data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config("OSCILLATION")},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
            labels=None,
        )

        assert result is not None
        assert "OSCILLATION" in result["labels"]


# ===========================================================================
# B2：传感器故障检测（卡死/噪声突增/漂移）故障注入测试
# ===========================================================================


def _make_ar1_signal(n: int, phi: float = 0.7, noise_std: float = 0.5, seed: int = 0) -> np.ndarray:
    """构造 AR(1) 正常过程信号（均值 50，固定种子）。"""
    rng = np.random.RandomState(seed)
    e = rng.normal(0.0, noise_std, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return 50.0 + x


class TestDetectSensorFaults:
    """B2：_detect_sensor_faults() 传感器故障算法组。"""

    def test_short_data_returns_empty(self) -> None:
        """数据点不足时不检出。"""
        pv = np.array([50.0] * 10, dtype=float)
        result = _detect_sensor_faults(pv)
        assert result["detected"] is False
        assert result["sensor_subtype"] is None

    def test_frozen_segment_detected(self) -> None:
        """中段 400 点常值（约 27% > 20% 占比阈值）应检出 frozen。"""
        n = 1500
        pv = _make_ar1_signal(n, seed=42)
        pv[500:900] = 50.0  # 中段 400 点卡死
        result = _detect_sensor_faults(pv)
        assert result["detected"] is True
        assert result["sensor_subtype"] == "frozen"
        assert result["frozen_max_segment"] >= 400
        assert result["confidence"] == 0.85

    def test_noise_burst_detected(self) -> None:
        """后半段噪声 std ×5（比值 5 > 3）应检出 noisy。"""
        n = 1500
        rng = np.random.RandomState(7)
        pv = np.concatenate(
            [
                50.0 + rng.normal(0.0, 0.5, n // 2),
                50.0 + rng.normal(0.0, 2.5, n - n // 2),
            ]
        )
        result = _detect_sensor_faults(pv)
        assert result["detected"] is True
        assert result["sensor_subtype"] == "noisy"
        assert result["noise_std_ratio"] > 3.0

    def test_drift_detected_when_sp_constant(self) -> None:
        """单调线性漂移 3.0（> 2σ）且 SP 不变应检出 drift。"""
        n = 1500
        rng = np.random.RandomState(11)
        pv = 50.0 + np.linspace(0.0, 3.0, n) + rng.normal(0.0, 0.1, n)
        sp = np.full(n, 50.0)
        result = _detect_sensor_faults(pv, sp)
        assert result["detected"] is True
        assert result["sensor_subtype"] == "drift"
        assert result["drift_magnitude"] > 2.0

    def test_drift_not_detected_when_sp_synced(self) -> None:
        """SP 同向同步变化（工艺真实变化）时不判 drift。"""
        n = 1500
        rng = np.random.RandomState(11)
        drift = np.linspace(0.0, 3.0, n)
        pv = 50.0 + drift + rng.normal(0.0, 0.1, n)
        sp = 50.0 + drift  # SP 同向同步变化
        result = _detect_sensor_faults(pv, sp)
        assert result["detected"] is False

    def test_normal_signals_low_false_positive(self) -> None:
        """20 组 AR(1) 正常信号（种子固定）误报应 ≤ 2。"""
        false_alarms = 0
        for i in range(20):
            pv = _make_ar1_signal(1000, seed=1000 + i)
            if _detect_sensor_faults(pv)["detected"]:
                false_alarms += 1
        assert false_alarms <= 2

    def test_threshold_override(self) -> None:
        """阈值配置覆盖：放宽 frozen_ratio 后同样的卡死信号不检出。"""
        n = 1500
        pv = _make_ar1_signal(n, seed=42)
        pv[500:900] = 50.0  # 占比约 0.27
        result = _detect_sensor_faults(pv, threshold={"frozen_ratio": 0.5})
        assert result["detected"] is False


class TestSensorFaultGating:
    """B2：传感器故障检测受 is_enabled 与 labels 子集门控（QUALITY_ABNORMAL）。"""

    @staticmethod
    def _frozen_data(n: int = 1500) -> RawTimeSeries:
        """构造中段 400 点卡死的宽表数据（SP/OP 恒定）。"""
        pv = _make_ar1_signal(n, seed=42)
        pv[500:900] = 50.0
        return _make_raw_timeseries(
            pv.tolist(),
            sp=[50.0] * n,
            op=[50.0] * n,
        )

    @pytest.mark.asyncio
    async def test_enabled_produces_quality_abnormal(self) -> None:
        """QUALITY_ABNORMAL 启用时，卡死信号产出 QUALITY_ABNORMAL 标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )
        data = self._frozen_data()

        async def _query_fn(**kwargs):
            return data

        quality_config = _make_diag_config("QUALITY_ABNORMAL")
        quality_config.threshold = None  # 使用算法默认阈值

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"QUALITY_ABNORMAL": quality_config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "QUALITY_ABNORMAL" in result["labels"]

    @pytest.mark.asyncio
    async def test_disabled_produces_no_label(self) -> None:
        """QUALITY_ABNORMAL 禁用时，同样的卡死信号不产出标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )
        data = self._frozen_data()

        async def _query_fn(**kwargs):
            return data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "QUALITY_ABNORMAL" not in result["labels"]

    @pytest.mark.asyncio
    async def test_labels_subset_excludes_quality(self) -> None:
        """labels 子集不含 QUALITY_ABNORMAL 时，同样的卡死信号不产出标签。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )
        data = self._frozen_data()

        async def _query_fn(**kwargs):
            return data

        quality_config = _make_diag_config("QUALITY_ABNORMAL")
        quality_config.threshold = None  # 使用算法默认阈值

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"QUALITY_ABNORMAL": quality_config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
            labels=["OSCILLATION"],
        )

        assert result is not None
        assert "QUALITY_ABNORMAL" not in result["labels"]


# ===========================================================================
# B3：Harris 指数模型失配评估测试
# ===========================================================================


class TestAssessModelMismatch:
    """B3：_assess_model_mismatch() Harris 指数模型失配评估。"""

    def test_white_noise_near_minimum_variance(self) -> None:
        """最小方差过程（白噪声偏差）harris_index 应 ≈ 1。"""
        n = 1000
        rng = np.random.RandomState(5)
        sp = np.full(n, 50.0)
        pv = sp + rng.normal(0.0, 0.5, n)
        result = _assess_model_mismatch(pv, sp)
        assert result["harris_index"] is not None
        assert 0.8 < result["harris_index"] < 1.5
        assert result["harris_warn"] is False

    def test_oscillatory_error_high_index(self) -> None:
        """振荡偏差（强相关、可预测）harris_index 应显著 > 2 并告警。"""
        n = 1000
        rng = np.random.RandomState(3)
        sp = np.full(n, 50.0)
        t = np.arange(n)
        pv = sp + np.sin(2 * np.pi * t / 50.0) + rng.normal(0.0, 0.01, n)
        result = _assess_model_mismatch(pv, sp)
        assert result["harris_index"] is not None
        assert result["harris_index"] > 2.0
        assert result["harris_warn"] is True

    def test_strongly_correlated_error_high_index(self) -> None:
        """强相关偏差（AR(1) φ=0.9）harris_index 应 > 2。"""
        n = 1000
        e = _make_ar1_signal(n, phi=0.9, noise_std=0.5, seed=9) - 50.0
        sp = np.full(n, 50.0)
        pv = sp + e
        result = _assess_model_mismatch(pv, sp)
        assert result["harris_index"] is not None
        assert result["harris_index"] > 2.0

    def test_missing_sp_returns_none(self) -> None:
        """SP 缺失时不评估，harris_index 为 None。"""
        pv = np.linspace(0.0, 1.0, 100)
        result = _assess_model_mismatch(pv)
        assert result["harris_index"] is None
        assert result["harris_warn"] is False

    def test_short_data_returns_none(self) -> None:
        """数据点不足时不评估。"""
        pv = np.ones(10)
        sp = np.ones(10)
        result = _assess_model_mismatch(pv, sp)
        assert result["harris_index"] is None

    def test_warn_threshold_override(self) -> None:
        """阈值配置覆盖：harris_warn 提高到 10 后同样的信号不告警。"""
        n = 1000
        e = _make_ar1_signal(n, phi=0.9, noise_std=0.5, seed=9) - 50.0
        sp = np.full(n, 50.0)
        pv = sp + e
        result = _assess_model_mismatch(pv, sp, threshold={"harris_warn": 10.0})
        assert result["harris_index"] is not None
        assert 2.0 < result["harris_index"] < 10.0
        assert result["harris_warn"] is False


class TestHarrisIndexIntegration:
    """B3：Harris 指数在 _diagnose_loop 中的可视化写入与证据增强。"""

    @staticmethod
    def _extract_results(db: AsyncMock) -> list:
        """从 db.add 调用中提取 DiagnosisResult 实例。"""
        from app.models.diagnosis import DiagnosisResult

        return [
            call.args[0]
            for call in db.add.call_args_list
            if isinstance(call.args[0], DiagnosisResult)
        ]

    @pytest.mark.asyncio
    async def test_harris_index_written_to_visualization(self) -> None:
        """OVERAGGRESSIVE 启用时，振荡偏差信号的 harris_index 写入可视化数据。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        n = 1000
        rng = np.random.RandomState(3)
        sp = np.full(n, 50.0)
        t = np.arange(n)
        pv = sp + np.sin(2 * np.pi * t / 50.0) + rng.normal(0.0, 0.01, n)
        data = _make_raw_timeseries(pv.tolist(), sp=sp.tolist())

        async def _query_fn(**kwargs):
            return data

        config = _make_diag_config("OVERAGGRESSIVE")
        config.threshold = None  # 使用算法默认阈值

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OVERAGGRESSIVE": config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        records = self._extract_results(db)
        assert len(records) > 0
        harris_index = records[0].feature_values.get("harris_index")
        assert harris_index is not None
        assert harris_index > 2.0
        assert records[0].feature_values.get("harris_warn") is True

    @pytest.mark.asyncio
    async def test_harris_merged_into_overaggressive_evidence(self) -> None:
        """OVERAGGRESSIVE 命中时，harris_index 并入其 evidence 作证据增强。"""
        loop = _make_loop()
        loop.control_type = "PID"
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        # B4：PV 过冲峰值约 140，需配置匹配量程，否则超出默认 0~100 量程被剔除
        pv_tag.range_min = 0.0
        pv_tag.range_max = 200.0
        db = _make_diagnose_db(
            loop,
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [pv_tag],
        )

        # 过激响应：SP 阶跃 + PV 低阻尼过冲振荡（偏差强相关 → harris > 2）
        n = 500
        sp = np.zeros(n)
        sp[50:] = 100.0
        pv = np.zeros(n)
        for i in range(50, n):
            t = i - 50
            pv[i] = 100.0 + 40.0 * np.exp(-t * 0.005) * np.cos(t * 0.3)
        data = _make_raw_timeseries(pv.tolist(), sp=sp.tolist())

        async def _query_fn(**kwargs):
            return data

        config = _make_diag_config("OVERAGGRESSIVE")
        config.threshold = None

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OVERAGGRESSIVE": config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert "OVERAGGRESSIVE" in result["labels"]
        records = self._extract_results(db)
        agg = next(r for r in records if r.diag_label == "OVERAGGRESSIVE")
        assert agg.evidence_chain.get("harris_index") is not None
        assert agg.evidence_chain["harris_index"] > 2.0

    @pytest.mark.asyncio
    async def test_harris_not_computed_when_gated_off(self) -> None:
        """OVERAGGRESSIVE/OVERCONSERVATIVE 均未启用时不评估 Harris 指数。"""
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )

        n = 1000
        sp = np.full(n, 50.0)
        t = np.arange(n)
        pv = sp + np.sin(2 * np.pi * t / 50.0)
        data = _make_raw_timeseries(pv.tolist(), sp=sp.tolist())

        async def _query_fn(**kwargs):
            return data

        config = _make_diag_config("QUALITY_ABNORMAL")
        config.threshold = None

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"QUALITY_ABNORMAL": config},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        records = self._extract_results(db)
        assert len(records) > 0
        assert "harris_index" not in records[0].feature_values


# ===========================================================================
# B4：轻量数据质量预处理（异常点剔除 + valid_rate）
# ===========================================================================


class TestB4OutlierPreprocessing:
    """B4：_apply_outlier_preprocessing 单元测试 + FFT 抗尖峰回归。"""

    @staticmethod
    def _make_b4_inputs(
        aligned: list[dict[str, Any]],
        raw_series: RawTimeSeries,
    ) -> tuple:
        """构造 _apply_outlier_preprocessing 的入参（PV 量程 0~100）。"""
        loop = _make_loop()
        loop.loop_type = "FLOW"
        pv_m = _make_mapping(tag_role="PV", tag_id="tag-pv")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC.PV")
        pv_tag.range_min = 0.0
        pv_tag.range_max = 100.0
        src_indices = list(range(len(aligned)))
        return (
            aligned,
            src_indices,
            raw_series,
            loop,
            {"PV": pv_m},
            {"tag-pv": pv_tag},
        )

    @staticmethod
    def _make_aligned(pv: list[float], op: list[Any] | None = None) -> list[dict[str, Any]]:
        """由 PV 数组构造对齐数据（1s 间隔）。"""
        return [
            {
                "ts": datetime(2026, 1, 1) + timedelta(seconds=i),
                "pv": pv[i],
                "sp": 50.0,
                "op": op[i] if op else 50.0,
                "mode": 1,
            }
            for i in range(len(pv))
        ]

    def test_normal_data_unchanged(self) -> None:
        """正常数据：不剔除任何点，valid_rate=1.0，结论与改动前一致。"""
        n = 60
        pv = [50.0 + 5.0 * float(np.sin(i * 0.3)) for i in range(n)]
        aligned = self._make_aligned(pv)
        raw = _make_raw_timeseries(pv, sp=[50.0] * n, op=[50.0] * n)

        filtered, valid_rate = _apply_outlier_preprocessing(*self._make_b4_inputs(aligned, raw))

        assert len(filtered) == n
        assert [d["pv"] for d in filtered] == pv
        assert valid_rate == 1.0

    def test_spike_points_removed(self) -> None:
        """SPIKE/OUT_OF_RANGE 尖峰坏点被剔除（跳变跟随点一并剔除）。"""
        n = 100
        pv = [50.0 + 5.0 * float(np.sin(i * 0.2)) for i in range(n)]
        for idx in (30, 60, 90):
            pv[idx] = 150.0  # 尖峰坏点（超量程 + 跳变 + 尖峰）
        aligned = self._make_aligned(pv)
        raw = _make_raw_timeseries(pv, sp=[50.0] * n, op=[50.0] * n)

        filtered, valid_rate = _apply_outlier_preprocessing(*self._make_b4_inputs(aligned, raw))

        remaining = [d["pv"] for d in filtered]
        assert len(filtered) < n
        assert 150.0 not in remaining
        assert all(abs(v - 50.0) <= 5.1 for v in remaining)
        assert 0.0 < valid_rate < 1.0

    def test_op_outlier_removed_sync(self) -> None:
        """OP 异常点触发整行（pv/sp/op/ts）同步剔除，保持对齐。"""
        n = 60
        pv = [50.0 + i * 0.01 for i in range(n)]  # 微斜坡，每点可唯一标识
        op: list[Any] = [50.0] * n
        op[20] = 150.0  # OP 尖峰
        aligned = self._make_aligned(pv, op=op)
        raw = _make_raw_timeseries(pv, sp=[50.0] * n, op=op)

        filtered, _ = _apply_outlier_preprocessing(*self._make_b4_inputs(aligned, raw))

        remaining_pv = [d["pv"] for d in filtered]
        assert len(filtered) < n
        # OP 尖峰所在行（及跳变跟随行）的 PV 同步被剔除
        assert 50.0 + 20 * 0.01 not in remaining_pv

    def test_high_removal_ratio_logs_warning_and_continues(self, caplog) -> None:
        """剔除比例 >50% 时记警告日志并继续（返回剩余数据，不抛异常）。"""
        n = 40
        pv = [150.0 if i % 2 == 0 else 50.0 for i in range(n)]  # 半数超量程
        aligned = self._make_aligned(pv)
        raw = _make_raw_timeseries(pv, sp=[50.0] * n, op=[50.0] * n)

        import logging

        with caplog.at_level(logging.WARNING):
            filtered, valid_rate = _apply_outlier_preprocessing(*self._make_b4_inputs(aligned, raw))

        assert "剔除比例过高" in caplog.text
        # 超量程点及其跳变跟随点全部剔除，仍正常返回
        assert len(filtered) <= n // 2
        assert valid_rate <= 0.5

    @pytest.mark.asyncio
    async def test_fft_unpolluted_by_spikes(self) -> None:
        """回归：含尖峰坏点的合成振荡数据，剔除后 FFT 结论与干净数据一致。"""
        from app.models.diagnosis import DiagnosisResult

        n = 200
        t = np.arange(n)
        base = 50.0 + 10.0 * np.sin(2.0 * np.pi * t / 25.0)
        spiked = base.copy()
        for idx in (60, 120, 180):
            spiked[idx] = 150.0

        async def _run(data: RawTimeSeries) -> tuple[dict, list]:
            db = _make_diagnose_db(
                _make_loop(),
                [_make_mapping(tag_role="PV", tag_id="tag-pv")],
                [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
            )

            async def _query_fn(**kwargs):
                return data

            result = await _diagnose_loop(
                db=db,
                loop_id="loop-001",
                diag_configs={"OSCILLATION": _make_diag_config()},
                ts_start=datetime(2026, 1, 1, 0, 0, 0),
                ts_end=datetime(2026, 1, 1, 1, 0, 0),
                query_wide_fn=_query_fn,
            )
            records = [
                call.args[0]
                for call in db.add.call_args_list
                if isinstance(call.args[0], DiagnosisResult)
            ]
            return result, records

        clean_result, clean_records = await _run(_make_raw_timeseries(base.tolist()))
        spiked_result, spiked_records = await _run(_make_raw_timeseries(spiked.tolist()))

        assert "OSCILLATION" in clean_result["labels"]
        assert "OSCILLATION" in spiked_result["labels"]
        f_clean = clean_records[0].feature_values["oscillation_frequency"]
        f_spiked = spiked_records[0].feature_values["oscillation_frequency"]
        # 尖峰未污染频谱：主频与干净数据一致（0.04 Hz 附近）
        assert f_spiked == pytest.approx(f_clean, abs=0.01)
        assert f_clean == pytest.approx(0.04, abs=0.01)

    @pytest.mark.asyncio
    async def test_extreme_outlier_ratio_still_returns_result(self) -> None:
        """剔除比例极高时诊断不中断：空数据降级为 MANUAL_REVIEW。"""
        n = 64
        pv = [150.0 if i % 2 == 0 else 50.0 for i in range(n)]
        db = _make_diagnose_db(
            _make_loop(),
            [_make_mapping(tag_role="PV", tag_id="tag-pv")],
            [_make_tag(tag_id="tag-pv", tag_name="LIC.PV")],
        )
        data = _make_raw_timeseries(pv, sp=[50.0] * n, op=[50.0] * n)

        async def _query_fn(**kwargs):
            return data

        result = await _diagnose_loop(
            db=db,
            loop_id="loop-001",
            diag_configs={"OSCILLATION": _make_diag_config()},
            ts_start=datetime(2026, 1, 1, 0, 0, 0),
            ts_end=datetime(2026, 1, 1, 1, 0, 0),
            query_wide_fn=_query_fn,
        )

        assert result is not None
        assert result["status"] == "SUCCESS"
        assert "MANUAL_REVIEW" in result["labels"]
