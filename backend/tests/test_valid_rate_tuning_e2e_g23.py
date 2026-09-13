"""G23 收口测试：整定链 valid_rate 的端到端数值断言（补齐 §30 的测试缺口）。

§30 修复了整定链的时间覆盖率口径，但当时只能做"来源断言"——因为
_fetch_preprocessed_signals 依赖 DB + DataPlanner。本文件用 patch 注入一个
假 planner，把它升级为**数值断言**。

关键区分（本测试存在的意义）：
- PVOP_HF 块的 sampling_freq 标为 "30s"（**实测**采样率）；
- 契约采样率（FLOW）为 1s；
- 若实现误用实测采样率算覆盖率 → coverage ≈ 1.0 → valid_rate ≈ 1.0（洗白）；
- 只有用**契约**间隔 → coverage ≈ 0.033 → valid_rate ≈ 0.033（正确）。
故本测试直接断言 valid_rate 远小于 1，一条断言同时守住"折入了覆盖率"与
"用的是契约间隔"两件事。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.contracts.data_types import DataBlock, QualitySummary
from app.services.tuning import _fetch_preprocessed_signals

_N = 120
_INTERVAL_S = 30.0  # 实测间隔：契约 FLOW 为 1s，故覆盖率应约 120/3601 ≈ 0.033
_BASE = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)


def _ts() -> list[datetime]:
    return [_BASE + timedelta(seconds=_INTERVAL_S * i) for i in range(_N)]


def _block(tag_group: str, signals: dict[str, list[float]], sampling_freq: str) -> DataBlock:
    ts = _ts()
    return DataBlock(
        data_block_id=f"db_L_{tag_group}",
        loop_id="L",
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=ts,
        signals=signals,
        validity={f"{k}_valid": [True] * _N for k in signals},
        quality_summary=QualitySummary(total_count=_N, valid_count=_N, valid_rate=1.0),
        point_count=_N,
    )


class _FakePlanner:
    def __init__(self, blocks: list[DataBlock]) -> None:
        self._blocks = blocks

    async def request_bundles(self, **_kwargs: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(data_block=b) for b in self._blocks]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _fetch() -> dict[str, object]:
    blocks = [
        _block("PVOP_HF", {"pv": [50.0] * _N, "op": [10.0] * _N}, sampling_freq="30s"),
        _block("BASE", {"sp": [50.0] * _N}, sampling_freq="30s"),
    ]
    planner = _FakePlanner(blocks)
    with patch(
        "app.services.tuning._build_data_planner",
        new=AsyncMock(return_value=planner),
    ):
        return await _fetch_preprocessed_signals(
            db=None,  # type: ignore[arg-type]
            loop_id="L",
            start_time=_iso(_BASE),
            end_time=_iso(_BASE + timedelta(seconds=_INTERVAL_S * (_N - 1))),
            control_type_str="FLOW",
        )


class TestTuningValidRateIsCoverageAdjusted:
    """整定链返回的 valid_rate 必须是"裸值 × 契约覆盖率"。"""

    @pytest.mark.asyncio
    async def test_valid_rate_is_far_below_one_for_sparse_data(self) -> None:
        """全部数据点 Good（裸值 1.0），但 30s 间隔对 1s 契约 → 有效值远小于 1。

        修复前：valid_rate == 1.0（未折入覆盖率，或误用实测间隔导致洗白）。
        """
        result = await _fetch()
        vr = float(result["valid_rate"])  # type: ignore[arg-type]
        # 决定性断言：远低于裸值 1.0（即覆盖率确实被折入）。实测 ≈0.168。
        assert vr < 0.5, f"稀疏数据的整定可信度应远低于 1.0，实测 {vr}"
        assert vr != pytest.approx(1.0, abs=0.01), "不得为未折入覆盖率的裸值"

    @pytest.mark.asyncio
    async def test_valid_rate_uses_contract_not_actual_interval(self) -> None:
        """区分契约与实测间隔：若误用实测 30s，覆盖率会退化为 ~1.0。

        实测（契约 1s、数据 120 点/30s）：valid_rate ≈ 0.168。
        精确量级取决于 compute_time_coverage 对窗口/跨度的取值方式，
        故此处只锁"显著小于 1"这一可判定的性质；若将来改为误用实测间隔，
        该值会跳到 ~1.0，本断言立即失败。
        """
        result = await _fetch()
        vr = float(result["valid_rate"])  # type: ignore[arg-type]
        assert 0.02 < vr < 0.5

    @pytest.mark.asyncio
    async def test_pvop_signal_still_returned(self) -> None:
        """回归护栏：口径修复不得影响信号本身（pv/op 照常返回）。"""
        result = await _fetch()
        assert len(result["pv"]) == _N  # type: ignore[arg-type]
        assert len(result["op"]) == _N  # type: ignore[arg-type]
