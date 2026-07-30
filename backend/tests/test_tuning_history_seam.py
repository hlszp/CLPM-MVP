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
from datetime import UTC, datetime, timedelta
from typing import Any
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
    _resample_mode_to_grid,
    _resample_to_grid,
    _to_rel_seconds,
    identify_model_from_history,
    preview_identify_segments,
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


def _simulate_open_loop_fopdt_biased(
    n: int = 1800,
    K: float = 2.0,
    tau: float = 60.0,
    theta: float = 5.0,
    u0: float = 60.0,
    load: float = 330.0,
    ts: float = 1.0,
    noise_std: float = 0.1,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """开环 FOPDT 带恒定负载偏置：y_ss = K·u_ss + load（PV≈450/OP≈60 工业形态）.

    OP 采用多段阶跃（≥2 次方向变化）以通过激励检测；SP 不参与，
    pipeline 走开环 ARX/ARMAX 路径，用于在 seam 层验证 P0-1+P0-2。
    """
    rng = np.random.default_rng(seed)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    # 多段阶跃 OP：60→65→58→63→60，保证方向变化≥2
    u = _sp_steps(n, [(0, u0), (300, u0 + 5.0), (700, u0 - 2.0), (1100, u0 + 3.0), (1500, u0)])
    y_ss0 = K * u0 + load  # 450
    y = np.full(n, y_ss0)
    for k in range(1, n):
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d] + (1 - a) * load
        else:
            y[k] = y[k - 1]
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
    async def test_biased_closed_loop_rejects_unverified_iv(self):
        """闭环 SP 激励：实验性 IV 不作为发布依据，主路径返回 INCONCLUSIVE（P0-05）.

        PV≈450/OP≈60 偏置闭环数据此前由 IV 路径处理；P0-05 关闭未经验证的
        闭环辨识后，seam 主路径不得再产出可放行模型，必须带
        CLOSED_LOOP_METHOD_UNVERIFIED reason 退出。
        """
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

        # P0-05：闭环 SP 激励不再走实验性 IV，安全失败
        assert result["success"] is False
        assert "CLOSED_LOOP_METHOD_UNVERIFIED" in (result.get("reason") or "")
        # seam 失败形态仍保留可追溯字段
        assert result["tagName"] == "TIC-4501"
        assert result["dataPoints"] == 1800
        assert result["validRate"] == 1.0
        assert result["algorithmVersion"]

    @pytest.mark.asyncio
    async def test_biased_open_loop_identifies_gain(self):
        """开环偏置数据：seam 主路径不崩且 K=2.0 误差 <10%（P0-1+P0-2）.

        SP 不参与，pipeline 走开环 ARX/ARMAX；OP 多段阶跃提供足够激励。
        """
        y, u = _simulate_open_loop_fopdt_biased(n=1800)
        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block("PVOP_HF", {"pv": y.tolist(), "op": u.tolist()}, _START),
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
        assert result["params"]["K"] < 3.0
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


# ---------------------------------------------------------------------------
# V62-P1-001~006：PV/OP/SP/MODE 同轴与重采样质量（Phase 1 数据同轴）
# ---------------------------------------------------------------------------


def _make_block_ts(
    tag_group: str,
    signals: dict[str, list[Any]],
    timestamps: list[datetime],
    sampling_freq: str = "1s",
) -> DataBlock:
    """构造真实 DataBlock（自定义 timestamps，支持异采样率/乱序/缺口场景）."""
    n = len(next(iter(signals.values())))
    validity = {f"{tag}_valid": [True] * n for tag in signals}
    return DataBlock(
        data_block_id=f"db_loop-1_{tag_group}_{sampling_freq}",
        loop_id="loop-1",
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        quality_summary=QualitySummary(total_count=n, valid_count=n, valid_rate=1.0),
    )


class TestV62P1DataAlignment:
    """V62-P1-001~006：PV/OP/SP/MODE 同轴 + 重采样质量 + 去数组索引退化."""

    @pytest.mark.asyncio
    async def test_sp_aligned_to_pvop_grid_when_diff_sampling_rate(self):
        """V62-P1-001: PVOP 1s(600点) + BASE 10s(60点) 时，SP 必须重采样到
        PVOP 网格（600 点），而非停留在 BASE 网格（60 点）。
        修复前 ``_resample_to_grid`` 误传 BASE 自身 timestamps 为目标网格。"""
        n_pvop = 600
        pv = [450.0 + 0.01 * i for i in range(n_pvop)]
        op = [60.0 + 0.005 * i for i in range(n_pvop)]
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]

        n_base = 60
        sp_base = [450.0 + 0.1 * i for i in range(n_base)]
        ts_base = [_START + timedelta(seconds=10 * i) for i in range(n_base)]

        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s"),
            ),
            _make_bundle(
                "error_mean",
                _make_block_ts("BASE", {"sp": sp_base}, ts_base, "10s"),
            ),
        ]
        planner = _make_planner(bundles)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:10:00Z", "TC"
            )
        assert len(signals["pv"]) == n_pvop
        # 修复前 SP 停留在 BASE 网格（60 点）
        assert len(signals["sp"]) == n_pvop
        # SP 应基于 PVOP 网格线性插值：t=0s → sp_base[0]=450.0，t=10s → sp_base[1]=450.1
        assert signals["sp"][0] == pytest.approx(450.0, abs=1e-6)
        assert signals["sp"][10] == pytest.approx(450.1, abs=1e-6)
        # t=5s（PVOP 网格点，介于 sp_base[0]@0s 与 sp_base[1]@10s 之间）线性插值 → 450.05
        assert signals["sp"][5] == pytest.approx(450.05, abs=1e-6)

    @pytest.mark.asyncio
    async def test_bundle_iteration_order_independence(self):
        """V62-P1-001: bundles 迭代顺序不影响结果（消除顺序依赖）。
        修复前 BASE 先于 PVOP_HF 时 timestamps 为空，SP 走降级分支不对齐。"""
        n_pvop = 300
        pv = [450.0 + 0.01 * i for i in range(n_pvop)]
        op = [60.0 + 0.005 * i for i in range(n_pvop)]
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]

        n_base = 30
        sp_base = [450.0 + 0.1 * i for i in range(n_base)]
        ts_base = [_START + timedelta(seconds=10 * i) for i in range(n_base)]

        pvop_b = _make_bundle(
            "valve_linearity",
            _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s"),
        )
        base_b = _make_bundle(
            "error_mean",
            _make_block_ts("BASE", {"sp": sp_base}, ts_base, "10s"),
        )

        db = AsyncMock()
        with patch(
            "app.services.tuning._build_data_planner",
            AsyncMock(return_value=_make_planner([pvop_b, base_b])),
        ):
            a = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        with patch(
            "app.services.tuning._build_data_planner",
            AsyncMock(return_value=_make_planner([base_b, pvop_b])),
        ):
            b = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        assert a["sp"] == b["sp"]
        assert a["pv"] == b["pv"]
        assert a["op"] == b["op"]
        assert len(a["sp"]) == n_pvop

    @pytest.mark.asyncio
    async def test_30s_base_aligned_to_pvop(self):
        """V62-P1-005: PVOP 1s + BASE 30s 异采样率对齐 + 质量指标记录."""
        n_pvop = 300
        pv = [450.0] * n_pvop
        op = [60.0] * n_pvop
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]
        n_base = 10
        sp_base = [450.0 + i for i in range(n_base)]
        ts_base = [_START + timedelta(seconds=30 * i) for i in range(n_base)]
        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s"),
            ),
            _make_bundle(
                "error_mean",
                _make_block_ts("BASE", {"sp": sp_base}, ts_base, "30s"),
            ),
        ]
        planner = _make_planner(bundles)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        assert len(signals["sp"]) == n_pvop
        assert signals["sp"][0] == pytest.approx(450.0)
        assert signals["sp"][30] == pytest.approx(451.0)  # sp_base[1] @ 30s
        assert signals["resample_quality"]["interpolated_count"] > 0
        assert signals["resample_quality"]["effective_samples"] == n_base


class TestV62P1ResampleQuality:
    """V62-P1-004/005: 重采样质量指标与乱序/缺口/边界外推矩阵."""

    def test_quality_metrics_recorded(self):
        """V62-P1-004: 返回插值/外推/缺口/有效样本四项指标."""
        src_ts = [_START + timedelta(seconds=10 * i) for i in range(10)]  # 0..90s
        dst_ts = [_START + timedelta(seconds=i) for i in range(100)]  # 0..99s
        values = [float(i) for i in range(10)]
        result, q = _resample_to_grid(values, src_ts, dst_ts)
        assert len(result) == 100
        # dst 0..90s 在 src 范围内（91 点），91..99s 外推（9 点）
        assert q["interpolated_count"] == 91
        assert q["extrapolated_count"] == 9
        assert q["gap_count"] == 0
        assert q["effective_samples"] == 10

    def test_src_unordered_supported(self):
        """V62-P1-005: src 乱序时先排序再插值，结果与有序一致."""
        # 乱序 ts + 对应值
        src_ts = [_START + timedelta(seconds=i) for i in [9, 0, 5, 2, 7]]
        values = [9.0, 0.0, 5.0, 2.0, 7.0]
        dst_ts = [_START + timedelta(seconds=i) for i in range(10)]
        result, _ = _resample_to_grid(values, src_ts, dst_ts)
        # 排序后 src: (0,0),(2,2),(5,5),(7,7),(9,9) → dst 整数点即原值
        assert result[0] == pytest.approx(0.0, abs=1e-6)
        assert result[5] == pytest.approx(5.0, abs=1e-6)
        assert result[9] == pytest.approx(9.0, abs=1e-6)

    def test_dst_extrapolation_uses_boundary_values(self):
        """V62-P1-005: dst 在 src 范围外用边界值（left/right 外推）."""
        src_ts = [_START + timedelta(seconds=5 * i) for i in range(3)]  # 0,5,10s
        values = [10.0, 20.0, 30.0]
        dst_ts = [_START + timedelta(seconds=s) for s in [-3, 0, 5, 10, 15]]
        result, q = _resample_to_grid(values, src_ts, dst_ts)
        assert result[0] == pytest.approx(10.0)  # left 外推用 values[0]
        assert result[4] == pytest.approx(30.0)  # right 外推用 values[-1]
        assert q["extrapolated_count"] == 2
        assert q["interpolated_count"] == 3

    def test_src_nan_inf_gap_counted(self):
        """V62-P1-005: src 中 NaN/inf 计入 gap_count，effective_samples 排除缺失."""
        src_ts = [_START + timedelta(seconds=i) for i in range(5)]
        values = [1.0, float("nan"), 3.0, float("inf"), 5.0]
        dst_ts = [_START + timedelta(seconds=i) for i in range(5)]
        _, q = _resample_to_grid(values, src_ts, dst_ts)
        assert q["gap_count"] == 2
        assert q["effective_samples"] == 3

    def test_to_rel_seconds_no_naive_timestamp_call(self):
        """V62-P1-006: _to_rel_seconds 对 naive datetime 不调 .timestamp()."""
        naive_ts = [datetime(2026, 7, 28, 0, 0, 0) + timedelta(seconds=i) for i in range(5)]
        rel = _to_rel_seconds(naive_ts, naive_ts[0])
        assert rel == [0.0, 1.0, 2.0, 3.0, 4.0]
        # aware datetime 与 naive 混用也能算（统一补 UTC）
        aware_ts = datetime(2026, 7, 28, 0, 0, 0, tzinfo=UTC)
        rel2 = _to_rel_seconds([aware_ts + timedelta(seconds=i) for i in range(3)], naive_ts[0])
        assert rel2 == [0.0, 1.0, 2.0]


# ---------------------------------------------------------------------------
# V62-P1-002：MODE 同轴（零阶保持，禁线性插值）
# ---------------------------------------------------------------------------


class TestV62P1ModeAlignment:
    """V62-P1-002: MODE 与 PV/OP/SP 同轴对齐到 PVOP 网格."""

    @pytest.mark.asyncio
    async def test_mode_aligned_to_pvop_grid_when_diff_sampling_rate(self):
        """V62-P1-002: PVOP 1s(600点) + MODE_HF 10s(60点) 时，MODE 必须零阶保持
        重采样到 PVOP 网格（600 点），不产生线性插值中间值。"""
        n_pvop = 600
        pv = [450.0 + 0.01 * i for i in range(n_pvop)]
        op = [60.0 + 0.005 * i for i in range(n_pvop)]
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]

        n_mode = 60
        # mode_raw[i] 表示第 i 个 10s 区间的模式：偶数=AUTO(1)，奇数=MANUAL(2)
        mode_raw = [1 if i % 2 == 0 else 2 for i in range(n_mode)]
        ts_mode = [_START + timedelta(seconds=10 * i) for i in range(n_mode)]

        bundles = [
            _make_bundle(
                "valve_linearity",
                _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s"),
            ),
            _make_bundle(
                "error_mean",
                _make_block_ts("BASE", {"sp": [450.0] * n_pvop}, ts_pvop, "1s"),
            ),
            _make_bundle(
                "auto_mode_rate",
                _make_block_ts("MODE_HF", {"mode": mode_raw}, ts_mode, "10s"),
            ),
        ]
        planner = _make_planner(bundles)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:10:00Z", "TC"
            )
        # MODE 对齐到 PVOP 网格（600 点），而非停留在 MODE_HF 10s 网格（60 点）
        assert len(signals["mode"]) == n_pvop
        # 零阶保持：0..9s → mode_raw[0]=AUTO(1)；10..19s → mode_raw[1]=MANUAL(2)
        assert signals["mode"][0] == 1
        assert signals["mode"][9] == 1
        assert signals["mode"][10] == 2
        assert signals["mode"][19] == 2
        assert signals["mode"][20] == 1
        # 关键：离散状态量不得出现线性插值中间值（如 1.5）
        assert all(v in (1, 2) for v in signals["mode"])
        # 质量指标记录
        q = signals["mode_resample_quality"]
        assert q["effective_samples"] == n_mode
        assert q["gap_count"] == 0

    @pytest.mark.asyncio
    async def test_mode_bundle_order_independence(self):
        """V62-P1-002: bundles 迭代顺序不影响 MODE 对齐结果."""
        n_pvop = 300
        pv = [450.0] * n_pvop
        op = [60.0] * n_pvop
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]
        n_mode = 30
        mode_raw = [1 if i % 2 == 0 else 2 for i in range(n_mode)]
        ts_mode = [_START + timedelta(seconds=10 * i) for i in range(n_mode)]

        pvop_b = _make_bundle(
            "valve_linearity", _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s")
        )
        base_b = _make_bundle(
            "error_mean", _make_block_ts("BASE", {"sp": [450.0] * n_pvop}, ts_pvop, "1s")
        )
        mode_b = _make_bundle(
            "auto_mode_rate", _make_block_ts("MODE_HF", {"mode": mode_raw}, ts_mode, "10s")
        )
        db = AsyncMock()
        with patch(
            "app.services.tuning._build_data_planner",
            AsyncMock(return_value=_make_planner([pvop_b, base_b, mode_b])),
        ):
            a = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        with patch(
            "app.services.tuning._build_data_planner",
            AsyncMock(return_value=_make_planner([mode_b, base_b, pvop_b])),
        ):
            b = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        assert a["mode"] == b["mode"]
        assert len(a["mode"]) == n_pvop

    @pytest.mark.asyncio
    async def test_mode_missing_block_returns_empty(self):
        """V62-P1-002: 无 MODE_HF bundle 时返回空 mode 列表，不崩溃."""
        n_pvop = 300
        pv = [450.0] * n_pvop
        op = [60.0] * n_pvop
        ts_pvop = [_START + timedelta(seconds=i) for i in range(n_pvop)]
        bundles = [
            _make_bundle(
                "valve_linearity", _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts_pvop, "1s")
            ),
        ]
        planner = _make_planner(bundles)
        db = AsyncMock()
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            signals = await _fetch_preprocessed_signals(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:05:00Z", "TC"
            )
        assert signals["mode"] == []
        assert signals["mode_resample_quality"] == {}


class TestV62P1ModeResampleQuality:
    """V62-P1-002: MODE 零阶保持重采样质量指标矩阵."""

    def test_zero_order_hold_no_interpolation(self):
        """MODE 不产生线性插值中间值：src [1, 3] → dst 中间点取 1 或 3，无 2.0."""
        src_ts = [_START + timedelta(seconds=10 * i) for i in range(2)]  # 0, 10s
        values = [1.0, 3.0]  # AUTO=1, CASCADE=3
        dst_ts = [_START + timedelta(seconds=i) for i in range(11)]  # 0..10s
        result, _ = _resample_mode_to_grid(values, src_ts, dst_ts)
        # 0..9s → 1（前向填充），10s → 3
        assert result[0] == 1
        assert result[9] == 1
        assert result[10] == 3
        # 关键：中间点不得出现 2.0（线性插值会产出）
        assert 2 not in result

    def test_mode_unordered_supported(self):
        """MODE 乱序时... 实际零阶保持要求 src 单调；乱序由调用方保证。
        本测试验证 src 已单调时 searchsorted 行为正确。"""
        src_ts = [_START + timedelta(seconds=i * 10) for i in range(3)]  # 0,10,20s
        values = [1.0, 2.0, 1.0]
        dst_ts = [_START + timedelta(seconds=i) for i in range(21)]  # 0..20s
        result, _ = _resample_mode_to_grid(values, src_ts, dst_ts)
        assert result[0] == 1  # 0..9s → 1
        assert result[10] == 2  # 10..19s → 2
        assert result[20] == 1  # 20s → 1

    def test_mode_extrapolation_uses_boundary_values(self):
        """dst 在 src 范围外用边界值（前向取 src[0]，后向取 src[-1]）."""
        src_ts = [_START + timedelta(seconds=5 * i) for i in range(3)]  # 0,5,10s
        values = [1.0, 2.0, 3.0]
        dst_ts = [_START + timedelta(seconds=s) for s in [-3, 0, 5, 10, 15]]
        result, q = _resample_mode_to_grid(values, src_ts, dst_ts)
        assert result[0] == 1  # 前向外推用 src[0]=1
        assert result[4] == 3  # 后向外推用 src[-1]=3
        assert q["extrapolated_count"] == 2
        assert q["interpolated_count"] == 3

    def test_mode_nan_gap_counted(self):
        """src 中 NaN 计入 gap_count，effective_samples 排除缺失."""
        src_ts = [_START + timedelta(seconds=i) for i in range(5)]
        values = [1.0, float("nan"), 3.0, float("inf"), 5.0]
        dst_ts = [_START + timedelta(seconds=i) for i in range(5)]
        result, q = _resample_mode_to_grid(values, src_ts, dst_ts)
        assert q["gap_count"] == 2
        assert q["effective_samples"] == 3
        # dst[1]（NaN 处）：零阶保持取前一个有效值 src[0]=1
        assert result[1] == 1

    def test_mode_all_nan_fills_zero(self):
        """src 全部缺失时填 0 并标记全外推."""
        src_ts = [_START + timedelta(seconds=i) for i in range(3)]
        values = [float("nan"), float("nan"), float("nan")]
        dst_ts = [_START + timedelta(seconds=i) for i in range(5)]
        result, q = _resample_mode_to_grid(values, src_ts, dst_ts)
        assert result == [0, 0, 0, 0, 0]
        assert q["effective_samples"] == 0
        assert q["gap_count"] == 3
        assert q["extrapolated_count"] == 5

    def test_mode_returns_int_list(self):
        """重采样结果为 int 列表（离散状态码），非 float."""
        src_ts = [_START + timedelta(seconds=i) for i in range(3)]
        values = [1.0, 2.0, 3.0]
        dst_ts = [_START + timedelta(seconds=i) for i in range(3)]
        result, _ = _resample_mode_to_grid(values, src_ts, dst_ts)
        assert all(isinstance(v, int) for v in result)


# ---------------------------------------------------------------------------
# V62-P1-008：preview API 返回真实片段、排除原因和质量摘要
# ---------------------------------------------------------------------------


class TestV62P1PreviewSegments:
    """V62-P1-008: preview_identify_segments 返回真实事件切片."""

    @pytest.mark.asyncio
    async def test_preview_returns_real_segments_with_exclusion_reason(self):
        """AUTO(100点) + MANUAL(100点) → 2 段，MANUAL 段标注 exclusionReason.

        修复前：整窗硬编码成单个 mode="AUTO" 片段，MANUAL 段污染结果。
        """
        n = 200
        pv = [450.0 + 0.01 * i for i in range(n)]
        op = [60.0 + 0.005 * i for i in range(n)]
        ts = [_START + timedelta(seconds=i) for i in range(n)]
        mode = [1] * 100 + [0] * 100  # AUTO → MANUAL

        bundles = [
            _make_bundle(
                "valve_linearity", _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts, "1s")
            ),
            _make_bundle("error_mean", _make_block_ts("BASE", {"sp": [450.0] * n}, ts, "1s")),
            _make_bundle("auto_mode_rate", _make_block_ts("MODE_HF", {"mode": mode}, ts, "1s")),
        ]
        planner = _make_planner(bundles)
        db = _make_db_with_loop(_LOOP)
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            result = await preview_identify_segments(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:03:20Z"
            )

        assert result["loopId"] == "loop-1"
        assert result["totalSegments"] == 2
        # 第一段 AUTO，可辨识
        seg0 = result["segments"][0]
        assert seg0["mode"] == "AUTO"
        assert seg0["exclusionReason"] is None
        assert seg0["pointCount"] == 100
        assert seg0["validSampleRatio"] == 1.0
        # 第二段 MANUAL，排除
        seg1 = result["segments"][1]
        assert seg1["mode"] == "MANUAL"
        assert seg1["exclusionReason"] == "MANUAL_MODE"
        assert seg1["isSufficient"] is False
        assert seg1["excitationScore"] is None

    @pytest.mark.asyncio
    async def test_preview_all_auto_single_segment_with_excitation(self):
        """全 AUTO + 方波 OP → 1 段，exclusionReason=None，含激励评分."""
        n = 200
        # 方波 OP 提供激励（方向变化 ≥ 1）
        pv = [450.0 + 0.1 * (i % 20) for i in range(n)]
        op = [50.0 + 10.0 * ((i // 50) % 2) for i in range(n)]
        ts = [_START + timedelta(seconds=i) for i in range(n)]

        bundles = [
            _make_bundle(
                "valve_linearity", _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts, "1s")
            ),
        ]
        planner = _make_planner(bundles)
        db = _make_db_with_loop(_LOOP)
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            result = await preview_identify_segments(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:03:20Z"
            )

        assert result["totalSegments"] == 1
        seg = result["segments"][0]
        assert seg["exclusionReason"] is None
        assert seg["excitationScore"] is not None
        assert seg["conditionNumber"] is not None
        assert seg["mode"] == "UNKNOWN"  # 无 MODE_HF bundle 时

    @pytest.mark.asyncio
    async def test_preview_empty_window(self):
        """数据不足时返回 0 片段."""
        n = 5
        pv = [450.0] * n
        op = [60.0] * n
        ts = [_START + timedelta(seconds=i) for i in range(n)]

        bundles = [
            _make_bundle(
                "valve_linearity", _make_block_ts("PVOP_HF", {"pv": pv, "op": op}, ts, "1s")
            ),
        ]
        planner = _make_planner(bundles)
        db = _make_db_with_loop(_LOOP)
        with patch("app.services.tuning._build_data_planner", AsyncMock(return_value=planner)):
            result = await preview_identify_segments(
                db, "loop-1", "2026-07-28T00:00:00Z", "2026-07-28T00:00:05Z"
            )

        assert result["totalSegments"] == 0
        assert result["segments"] == []
        assert result["sufficientCount"] == 0
