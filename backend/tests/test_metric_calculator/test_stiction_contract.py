"""粘滞系数（Stiction Index）契约与量纲测试（S1-c 补齐 + G19 续）。

事实来源：`docs/设计文档/03-ADS/关键算法设计说明.md` §4.8
- §4.8.3 输入规范把 `pv_range`/`op_range` 列为**必填**（"PV/OP 量程范围"）；
- §4.8.4 步骤 4-5：`pv_norm = (pv - min(pv)) / pv_range`、`op_norm = (op - min(op)) / op_range`
  —— 各自除以**本轴量程**，目的是把两轴都映到 0~1 满量程；
- §4.8.4 步骤 8：`IF R2 < 0.5 THEN RETURN (0, NONE, R2, INCONCLUSIVE)`
  —— **R² 门控是规格自身要求**，故"圆团散点（无主导方向）不检出"是设计意图；
- §4.8.2 判定规则：St<5 无 / 5~15 轻微 / 15~30 中度 / ≥30 严重。

G19 复核结论：原判"双门控方向相反、正圆（最严重粘滞）恒不检出"属**误诊**——
正圆在椭圆法中意味着"无主导方向"（与噪声不可分），规格步骤 8 正是为排除它而设；
纯滞后会被互相关 θ 补偿压薄，故椭圆法测的是**滞环**而非相位滞后。见收口报告 §17。

本文件同时固化一个**真实**缺陷的修复（G19 续）：诊断侧 `assess_stiction_features`
原缺省用"数据自身极差"归一化，与 KPI 侧 `_read_range` 的归一化满量程口径分叉，
而 `b/a` 并非尺度不变量，导致同一信号两条"同内核"路径给出不同 St
（实测 10.69% vs 10.80%）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.metric_calculator.stiction import (
    MIN_FITTING_SCORE,
    StictionIndexCalculator,
    assess_stiction_features,
)

from .conftest import make_bundle

#: 合成极限环：600 点、50 采样点/周期 → 12 周期、23 次零交叉（过 4 次下限）
N = 600
_OMEGA = 2.0 * math.pi / 50.0


def _limit_cycle_pv() -> np.ndarray:
    return np.sin(_OMEGA * np.arange(N))


def _hysteresis_op(pv: np.ndarray, stickband: float) -> np.ndarray:
    """滞环算子：op = pv + δ·sign(d(pv)/dt).

    方向相关分支使 (pv, op) 散点成为**闭合回环**（真粘滞的几何特征），
    而非单值曲线——互相关 θ 补偿无法把它压薄。
    """
    return pv + stickband * np.sign(np.cos(_OMEGA * np.arange(len(pv))))


def _kpi(pv: np.ndarray, op: np.ndarray, *, pv_range=None, op_range=None):
    signals: dict[str, list[float]] = {
        "pv": [float(v) for v in pv],
        "op": [float(v) for v in op],
    }
    if pv_range is not None:
        signals["pv_range"] = pv_range
    if op_range is not None:
        signals["op_range"] = op_range
    bundle = make_bundle(
        signals, mask_expression="pv_valid && op_valid", metric_code="stiction_index"
    )
    return StictionIndexCalculator().calculate(bundle)


class TestStictionSpecGates:
    """规格 §4.8.4 门控语义。"""

    def test_round_scatter_is_inconclusive_by_r2_gate(self) -> None:
        """圆团散点（|ρ|≈0）→ INCONCLUSIVE：规格步骤 8 的 R² 门控。

        G19 曾判此行为为缺陷（"正圆=最严重粘滞却恒不检出"）。复核确认：
        正圆表示散点**无主导方向**，与噪声不可分，b/a 宽度比此时不具粘滞
        物理含义；规格步骤 8 明文要求此时返回 INCONCLUSIVE。
        """
        rng = np.random.default_rng(7)
        pv = _limit_cycle_pv()
        op = rng.normal(0.0, 1.0, N)  # 与 PV 无关
        result = _kpi(pv, op)

        assert result.value is None
        assert result.details["reason"] == "low_correlation"
        assert result.details["fitting_score"] < MIN_FITTING_SCORE

    def test_pure_lag_is_compensated_and_reads_as_no_stiction(self) -> None:
        """单值相位滞后经互相关 θ 补偿后不得计为粘滞（椭圆法测滞环不测滞后）。"""
        phi = math.acos(0.8)
        pv = np.sin(_OMEGA * np.arange(N))
        op = np.sin(_OMEGA * np.arange(N) - phi)
        result = _kpi(pv, op)

        assert result.value is not None
        assert result.value < 5.0, "纯滞后被误判为粘滞"
        assert result.details["stiction_level"] == "NONE"
        # 补偿后应接近完全线性相关
        assert result.details["fitting_score"] > 0.99

    def test_hysteresis_maps_to_declared_levels(self) -> None:
        """闭合回环的 St 落在规格 §4.8.2 声明的等级区间内。

        锚点为刻画值（合成信号经本实现实测定标），用于防回归；
        等级边界语义（<5/5~15/15~30/≥30）来自规格表格。
        """
        pv = _limit_cycle_pv()
        mild = _kpi(pv, _hysteresis_op(pv, 0.2))
        moderate = _kpi(pv, _hysteresis_op(pv, 0.8))

        assert mild.value is not None and moderate.value is not None
        assert 5.0 <= mild.value < 15.0
        assert mild.details["stiction_level"] == "MILD"
        assert 15.0 <= moderate.value < 30.0
        assert moderate.details["stiction_level"] == "MODERATE"
        assert moderate.value > mild.value, "滞环加宽 St 应单调增（该区间内）"

    def test_stiction_is_capped_by_r2_gate(self) -> None:
        """结构性上限：凡通过 R²≥0.5 门控者，St ≤ (√2−1)×100 ≈ 41.4214%。

        证明：对任意二维协方差，PCA 轴比满足
            b/a ≤ sqrt((1-|ρ|)/(1+|ρ|))，
        故 |ρ| ≥ 1/√2 时 b/a ≤ sqrt((1-1/√2)/(1+1/√2)) = √2−1。

        这是 G19 的**合理内核**的量化版本：规格步骤 8 的门控把 §4.8.2 表格中
        St∈[41.42%, 100%] 的"严重粘滞"区间压成了不可达区。属**规格内部张力**
        （门控与分级表不自洽），非实现缺陷——同 G18 处置，登记不擅改。
        """
        cap = (math.sqrt(2.0) - 1.0) * 100.0
        assert cap == pytest.approx(41.4214, abs=1e-4)

        rng = np.random.default_rng(11)
        checked = 0
        for _ in range(300):
            pv = _limit_cycle_pv() + rng.normal(0.0, 0.30, N)
            op = np.sin(_OMEGA * np.arange(N) - rng.uniform(0.0, math.pi)) + rng.normal(
                0.0, 0.30, N
            )
            result = _kpi(pv, op)
            fitting = result.details.get("fitting_score") or 0.0
            if result.value is not None and fitting >= MIN_FITTING_SCORE:
                checked += 1
                assert result.value <= cap, (
                    f"St={result.value} 超过 R²≥0.5 时的解析上界 {cap}（R²={fitting}）"
                )
        assert checked > 0, "样本族未覆盖任何通过门控的点，上界断言未被检验"


class TestStictionRangeContract:
    """量程口径契约：诊断侧与 KPI 侧必须同判据（规格 §4.8.3 量程必填）。"""

    def test_two_paths_agree_on_same_signal(self) -> None:
        """同一信号：诊断侧 features（0~1）与 KPI 侧 value（0~100）必须一致。

        回归目标（G19 续）：诊断侧原缺省用数据极差归一化，与 KPI 侧
        归一化满量程分叉，比值实测 101.01 而非 100。
        """
        pv = _limit_cycle_pv()
        op = _hysteresis_op(pv, 0.4)

        feat = assess_stiction_features(pv, op)
        kpi = _kpi(pv, op)

        assert kpi.value is not None
        # KPI 侧 _make_result 出口保留两位小数，故按该精度比对
        assert round(feat["stiction_index"] * 100.0, 2) == pytest.approx(kpi.value, abs=1e-9)

    def test_explicit_range_equals_normalized_default(self) -> None:
        """显式传归一化满量程与缺省路径结果一致（缺省即归一化满量程口径）。"""
        pv = _limit_cycle_pv()
        op = _hysteresis_op(pv, 0.4)

        implicit = assess_stiction_features(pv, op)
        explicit = assess_stiction_features(pv, op, pv_range=100.0, op_range=100.0)

        assert implicit["range_source"] == {"pv": "normalized_default", "op": "normalized_default"}
        assert explicit["range_source"] == {"pv": "explicit", "op": "explicit"}
        assert implicit["stiction_index"] == pytest.approx(explicit["stiction_index"], abs=1e-12)

    def test_explicit_range_changes_shape_as_spec_intends(self) -> None:
        """量程必须参与形状：b/a 非尺度不变量，两轴量程不等则 St 改变。

        这条锁住"为什么量程在 §4.8.3 是必填"——若改成尺度不变量实现，
        本用例会失败，提示规格假设已被改动。
        """
        pv = _limit_cycle_pv()
        op = _hysteresis_op(pv, 0.4)

        equal = assess_stiction_features(pv, op, pv_range=100.0, op_range=100.0)
        unequal = assess_stiction_features(pv, op, pv_range=100.0, op_range=200.0)

        assert unequal["stiction_index"] != pytest.approx(equal["stiction_index"], abs=1e-6)

    def test_insufficient_data_and_no_limit_cycle_paths(self) -> None:
        """数据不足 → INCONCLUSIVE；平稳非极限环 → St=0 且 NONE（有意义的结论）。"""
        short = _kpi(np.sin(_OMEGA * np.arange(50)), np.sin(_OMEGA * np.arange(50)))
        assert short.value is None
        assert short.details["reason"] == "insufficient_data"

        flat = _kpi(np.full(N, 50.0), np.full(N, 50.0))
        assert flat.value == 0.0
        assert flat.details["stiction_level"] == "NONE"
        assert flat.details["reason"] == "no_limit_cycle"
