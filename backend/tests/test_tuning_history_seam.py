"""P0-1/P0-2 集成接缝测试：DataPlanner → 辨识主路径（仅最外边界 mock）.

此前 174 个测试全绿却漏掉 P0-1（DataBlock.sampling_freq 字符串标签
在主路径 ``> 0`` 比较时 TypeError），因为旧测试把 DataBlock 也 mock 成了
float。本测试只 mock 最外边界：
- ``_build_data_planner`` 返回的 planner（其 request_bundles 返回
  **真实** DataBlock/MetricDataBundle 对象，sampling_freq="1s" 字符串标签）
- db.execute（返回真实 LoopLedger 实例）

让字符串标签与带偏置工业数据真实流过
``_fetch_preprocessed_signals`` → ``identify_from_history`` 主路径。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    QualitySummary,
)
from app.models.loop import LoopLedger
from app.services.tuning import (
    _fetch_preprocessed_signals,
    _parse_sampling_freq_hz,
    identify_model_from_history,
)

# ---------------------------------------------------------------------------
# 仿真辅助（与 test_tuning_identification.py 同构，自包含避免跨测试文件依赖）
# ---------------------------------------------------------------------------


def _sp_steps(n: int, steps: list[tuple[int, float]]) -> np.ndarray:
    """生成 SP 阶跃信号。"""
    sp = np.zeros(n)
    for idx, val in steps:
        sp[idx:] = val
    return sp


def _simulate_closed_loop_fopdt_biased(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    load: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """闭环 FOPDT 带恒定负载偏置：y_ss = K·u_ss + load（PV≈450/OP≈60 工业形态）."""
    rng = np.random.default_rng(seed)
    n = len(sp)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    u0 = (sp[0] - load) / K
    y0 = sp[0]
    y = np.full(n, y0)
    u = np.zeros(n)
    e_prev = 0.0
    u_prev = u0
    ki = kp * ts / ti
    for k in range(n):
        # 真闭环：控制器读取上一拍测量值 y[k-1]（y[k] 本拍末才由对象方程写入）
        y_meas = y[k - 1] if k > 0 else y0
        e = sp[k] - y_meas
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d] + (1 - a) * load
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y, u


# ---------------------------------------------------------------------------
# 接缝桩：真实 DataBlock/MetricDataBundle + 最小 mock
# ---------------------------------------------------------------------------


def _make_block(
    tag_group: str,
    signals: dict[str, list[float]],
    start: datetime,
    sampling_freq: str = "1s",
) -> DataBlock:
    """构造真实 DataBlock（sampling_freq 为字符串标签，复刻 P0-1 现场）."""
    n = len(next(iter(signals.values())))
    ts_list = [start + timedelta(seconds=i) for i in range(n)]
    validity = {f"{tag}_valid": [True] * n for tag in signals}
    return DataBlock(
        data_block_id=f"db_loop-1_{tag_group}_{sampling_freq}",
        loop_id="loop-1",
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=ts_list,
        signals=signals,
        validity=validity,
        quality_summary=QualitySummary(
            total_count=n,
            valid_count=n,
            valid_rate=1.0,
        ),
    )


def _make_bundle(metric_code: str, block: DataBlock) -> MetricDataBundle:
    """构造真实 MetricDataBundle（仅包裹，不做掩码筛选）."""
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression="",
        masked_indices=list(range(block.point_count)),
        lineage=DataLineage(sampling_freq=block.sampling_freq, tag_group=block.tag_group),
    )


def _make_db_with_loop(loop: LoopLedger) -> AsyncMock:
    """db mock：execute 返回 scalar_one_or_none → 真实 LoopLedger."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = loop
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    return db


def _make_planner(bundles: list[MetricDataBundle]) -> MagicMock:
    planner = MagicMock()
    planner.request_bundles = AsyncMock(return_value=bundles)
    return planner


_LOOP = LoopLedger(id="loop-1", tag_name="TIC-4501", control_type="TC")
_START = datetime(2026, 7, 28, 0, 0, 0)


def _biased_signals(n: int = 1800, seed: int = 42):
    """生成带偏置闭环数据：K=2.0/τ=60s/θ=5s，PV≈450，OP≈60."""
    sp = _sp_steps(n, [(0, 450.0), (300, 455.0), (700, 447.0), (1100, 452.0), (1500, 449.0)])
    y, u = _simulate_closed_loop_fopdt_biased(
        sp,
        K=2.0,
        tau=60.0,
        theta=5.0,
        kp=0.5,
        ti=30.0,
        load=330.0,
        noise_std=0.1,
        seed=seed,
    )
    return sp, y, u


# ---------------------------------------------------------------------------
# P0-1：sampling_freq 标签解析单测
# ---------------------------------------------------------------------------


class TestParseSamplingFreqHz:
    """_parse_sampling_freq_hz：DataBlock 字符串标签 → 数值 Hz."""

    def test_label_1s(self):
        assert _parse_sampling_freq_hz("1s") == 1.0

    def test_label_10s(self):
        assert _parse_sampling_freq_hz("10s") == pytest.approx(0.1)

    def test_label_5s(self):
        assert _parse_sampling_freq_hz("5s") == pytest.approx(0.2)

    def test_empty_and_none_fallback(self):
        assert _parse_sampling_freq_hz("") == 1.0
        assert _parse_sampling_freq_hz(None) == 1.0

    def test_garbage_and_zero_fallback(self):
        assert _parse_sampling_freq_hz("garbage") == 1.0
        assert _parse_sampling_freq_hz("0s") == 1.0

    def test_numeric_passthrough(self):
        """容错：若上游某天直接给数值，也不应崩。"""
        assert _parse_sampling_freq_hz(2.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# P0-1 接缝：_fetch_preprocessed_signals 返回数值 sampling_freq
# ---------------------------------------------------------------------------


class TestFetchPreprocessedSignalsSeam:
    """DataBlock(sampling_freq="1s") 真实流过信号装配接缝."""

    @pytest.mark.asyncio
    async def test_sampling_freq_parsed_to_float(self):
        """字符串标签 '1s' 应解析为 float 1.0（修复前原样透传导致下游 TypeError）."""
        sp, y, u = _biased_signals(n=600)
        blocks = [
            _make_bundle(
                "valve_linearity",
                _make_block("PVOP_HF", {"pv": y.tolist(), "op": u.tolist()}, _START),
            ),
            _make_bundle(
                "error_mean",
                _make_block("BASE", {"sp": sp.tolist()}, _START),
            ),
        ]
        planner = _make_planner(blocks)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:10:00Z", "TC"
            )
        assert isinstance(signals["sampling_freq"], float)
        assert signals["sampling_freq"] == 1.0
        # 数值化后下游比较/除法不再 TypeError
        assert signals["sampling_freq"] > 0
        assert 1.0 / signals["sampling_freq"] == 1.0
        assert len(signals["pv"]) == 600
        assert len(signals["op"]) == 600
        assert len(signals["sp"]) == 600
        assert signals["valid_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_sampling_freq_10s_label(self):
        """'10s' 标签应解析为 0.1 Hz（采样周期 10s）."""
        sp, y, u = _biased_signals(n=600)
        blocks = [
            _make_bundle(
                "valve_linearity",
                _make_block(
                    "PVOP_HF", {"pv": y.tolist(), "op": u.tolist()}, _START, sampling_freq="10s"
                ),
            ),
        ]
        planner = _make_planner(blocks)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T01:40:00Z", "TC"
            )
        assert signals["sampling_freq"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# P0-1 + P0-2 主路径接缝：identify_model_from_history 端到端
# ---------------------------------------------------------------------------


class TestIdentifyFromHistoryMainPathSeam:
    """DataPlanner → 辨识主路径端到端（带偏置工业数据）."""

    @pytest.mark.asyncio
    async def test_biased_closed_loop_identifies_gain(self):
        """PV≈450/OP≈60 偏置数据：主路径不崩且 K=2.0 误差 <10%（P0-1+P0-2）."""
        sp, y, u = _biased_signals(n=1800)
        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block("PVOP_HF", {"pv": y.tolist(), "op": u.tolist()}, _START),
            ),
            _make_bundle(
                "error_mean",
                _make_block("BASE", {"sp": sp.tolist()}, _START),
            ),
        ]
        planner = _make_planner(bundles)
        db = _make_db_with_loop(_LOOP)

        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            result = await identify_model_from_history(
                db,
                "loop-1",
                "2026-07-28T00:00:00Z",
                "2026-07-28T00:30:00Z",
                theta_estimate=5.0,
            )

        # P0-1：主路径不再 TypeError，成功出结果
        assert result["success"] is True
        # P0-2：去均值后增量增益恢复（割线 7.5 已被排除）
        assert abs(result["params"]["K"] - 2.0) / 2.0 < 0.10
        # samplingFreq 为数值（修复前透传字符串 "1s"）
        assert isinstance(result["samplingFreq"], float)
        assert result["samplingFreq"] == 1.0
        assert result["dataPoints"] == 1800
        assert result["validRate"] == 1.0
        assert result["confidenceLevel"] in {"A", "B", "C", "D", "E"}
        assert result["tagName"] == "TIC-4501"
        # P0-2：偏置量记录可追溯
        assert "去均值偏置" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_insufficient_data_raises_biz_error(self):
        """数据点 <50 时应抛 ERR_TUNING_DATA_INSUFFICIENT（而非 TypeError）."""
        from app.core.exceptions import BizError

        sp, y, u = _biased_signals(n=40)
        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block("PVOP_HF", {"pv": y.tolist(), "op": u.tolist()}, _START),
            ),
        ]
        planner = _make_planner(bundles)
        db = _make_db_with_loop(_LOOP)

        with (
            patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)),
            pytest.raises(BizError) as exc_info,
        ):
            await identify_model_from_history(
                db,
                "loop-1",
                "2026-07-28T00:00:00Z",
                "2026-07-28T00:00:40Z",
            )
        assert exc_info.value.code == "ERR_TUNING_DATA_INSUFFICIENT"
