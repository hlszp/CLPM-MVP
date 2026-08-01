"""PID 参数转换往返性质测试（V62-P1-016）.

验证 ``to_standard_pid`` 与 ``from_standard_pid`` 的往返一致性：
    from_standard_pid(to_standard_pid(dcs_pid, s), s) == dcs_pid
    to_standard_pid(from_standard_pid(standard_pid, s), s) == standard_pid

覆盖维度：
- 比例项：PROPORTION(Kp) / PROPORTION_BAND(PB)
- 时间单位：SECONDS / MINUTES
- 积分/微分的组合
- 边界：Td=0、大增益/小增益
"""

from __future__ import annotations

import pytest

from app.models.dcs_pid_structure import (
    P_TYPE_PROPORTION,
    P_TYPE_PROPORTION_BAND,
    UNIT_MINUTES,
    UNIT_SECONDS,
    DcsPidStructure,
)
from app.services.pid_conversion import (
    DcsPid,
    StandardPid,
    convert_pid_dict,
    from_standard_pid,
    to_standard_pid,
)

# ---------------------------------------------------------------------------
# 辅助：构造 DcsPidStructure（不依赖数据库）
# ---------------------------------------------------------------------------


def _make_structure(
    p_type: str = P_TYPE_PROPORTION,
    i_unit: str = UNIT_SECONDS,
    d_unit: str = UNIT_SECONDS,
    d_filter_enabled: bool = False,
) -> DcsPidStructure:
    """构造内存中的 DcsPidStructure 实例（不写库）."""
    return DcsPidStructure(
        id="test-struct",
        dcs_model_id="test-model",
        p_type=p_type,
        i_unit=i_unit,
        d_unit=d_unit,
        d_filter_enabled=d_filter_enabled,
        d_filter_unit=UNIT_SECONDS if d_filter_enabled else None,
        d_filter_multiplier=False,
    )


# ---------------------------------------------------------------------------
# 往返性质测试：DCS → 标准 → DCS
# ---------------------------------------------------------------------------


class TestRoundTripDcsToStandardToDcs:
    """from_standard_pid(to_standard_pid(dcs, s), s) == dcs."""

    @pytest.mark.parametrize(
        "p_type,i_unit,d_unit",
        [
            (P_TYPE_PROPORTION, UNIT_SECONDS, UNIT_SECONDS),
            (P_TYPE_PROPORTION_BAND, UNIT_SECONDS, UNIT_SECONDS),
            (P_TYPE_PROPORTION, UNIT_MINUTES, UNIT_SECONDS),
            (P_TYPE_PROPORTION, UNIT_SECONDS, UNIT_MINUTES),
            (P_TYPE_PROPORTION, UNIT_MINUTES, UNIT_MINUTES),
            (P_TYPE_PROPORTION_BAND, UNIT_MINUTES, UNIT_MINUTES),
            (P_TYPE_PROPORTION_BAND, UNIT_MINUTES, UNIT_SECONDS),
            (P_TYPE_PROPORTION_BAND, UNIT_SECONDS, UNIT_MINUTES),
        ],
    )
    def test_round_trip_all_combinations(self, p_type, i_unit, d_unit):
        """所有 p_type × i_unit × d_unit 组合的往返一致性."""
        s = _make_structure(p_type=p_type, i_unit=i_unit, d_unit=d_unit)
        original = DcsPid(p=50.0, i=30.0, d=10.0)

        standard = to_standard_pid(original, s)
        restored = from_standard_pid(standard, s)

        assert restored.p == pytest.approx(original.p, rel=1e-9)
        assert restored.i == pytest.approx(original.i, rel=1e-9)
        assert restored.d == pytest.approx(original.d, rel=1e-9)

    def test_round_trip_with_zero_td(self):
        """Td=0 的往返一致性（常见场景：PI 控制）."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_MINUTES, d_unit=UNIT_SECONDS)
        original = DcsPid(p=100.0, i=0.5, d=0.0)

        standard = to_standard_pid(original, s)
        restored = from_standard_pid(standard, s)

        assert restored.p == pytest.approx(original.p)
        assert restored.i == pytest.approx(original.i)
        assert restored.d == pytest.approx(original.d)

    def test_round_trip_large_gain(self):
        """大增益 Kp=500（小 PB=0.2）的往返一致性."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_SECONDS, d_unit=UNIT_SECONDS)
        original = DcsPid(p=0.2, i=10.0, d=2.0)

        standard = to_standard_pid(original, s)
        assert standard.kp == pytest.approx(500.0)
        restored = from_standard_pid(standard, s)
        assert restored.p == pytest.approx(original.p, rel=1e-9)

    def test_round_trip_small_gain(self):
        """小增益 Kp=0.1（大 PB=1000）的往返一致性."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_SECONDS, d_unit=UNIT_SECONDS)
        original = DcsPid(p=1000.0, i=120.0, d=30.0)

        standard = to_standard_pid(original, s)
        assert standard.kp == pytest.approx(0.1)
        restored = from_standard_pid(standard, s)
        assert restored.p == pytest.approx(original.p, rel=1e-9)


# ---------------------------------------------------------------------------
# 往返性质测试：标准 → DCS → 标准
# ---------------------------------------------------------------------------


class TestRoundTripStandardToDcsToStandard:
    """to_standard_pid(from_standard_pid(standard, s), s) == standard."""

    @pytest.mark.parametrize(
        "p_type,i_unit,d_unit",
        [
            (P_TYPE_PROPORTION, UNIT_SECONDS, UNIT_SECONDS),
            (P_TYPE_PROPORTION_BAND, UNIT_SECONDS, UNIT_SECONDS),
            (P_TYPE_PROPORTION, UNIT_MINUTES, UNIT_MINUTES),
            (P_TYPE_PROPORTION_BAND, UNIT_MINUTES, UNIT_MINUTES),
            (P_TYPE_PROPORTION_BAND, UNIT_MINUTES, UNIT_SECONDS),
        ],
    )
    def test_round_trip_all_combinations(self, p_type, i_unit, d_unit):
        """所有 p_type × i_unit × d_unit 组合的往返一致性."""
        s = _make_structure(p_type=p_type, i_unit=i_unit, d_unit=d_unit)
        original = StandardPid(kp=2.5, ti=60.0, td=15.0)

        dcs = from_standard_pid(original, s)
        restored = to_standard_pid(dcs, s)

        assert restored.kp == pytest.approx(original.kp, rel=1e-9)
        assert restored.ti == pytest.approx(original.ti, rel=1e-9)
        assert restored.td == pytest.approx(original.td, rel=1e-9)

    def test_round_trip_zero_td(self):
        """Td=0 的往返一致性."""
        s = _make_structure(p_type=P_TYPE_PROPORTION, i_unit=UNIT_MINUTES, d_unit=UNIT_SECONDS)
        original = StandardPid(kp=1.5, ti=30.0, td=0.0)

        dcs = from_standard_pid(original, s)
        restored = to_standard_pid(dcs, s)

        assert restored.kp == pytest.approx(original.kp)
        assert restored.ti == pytest.approx(original.ti)
        assert restored.td == pytest.approx(original.td)


# ---------------------------------------------------------------------------
# 单向转换正确性测试
# ---------------------------------------------------------------------------


class TestConversionCorrectness:
    """验证具体转换数值的正确性."""

    def test_pb_to_kp(self):
        """PB=50 → Kp=2.0."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND)
        dcs = DcsPid(p=50.0, i=10.0, d=0.0)
        standard = to_standard_pid(dcs, s)
        assert standard.kp == pytest.approx(2.0)

    def test_kp_to_pb(self):
        """Kp=2.0 → PB=50."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND)
        standard = StandardPid(kp=2.0, ti=10.0, td=0.0)
        dcs = from_standard_pid(standard, s)
        assert dcs.p == pytest.approx(50.0)

    def test_minutes_to_seconds(self):
        """i=0.5min → Ti=30s; d=0.25min → Td=15s."""
        s = _make_structure(i_unit=UNIT_MINUTES, d_unit=UNIT_MINUTES)
        dcs = DcsPid(p=1.0, i=0.5, d=0.25)
        standard = to_standard_pid(dcs, s)
        assert standard.ti == pytest.approx(30.0)
        assert standard.td == pytest.approx(15.0)

    def test_seconds_to_minutes(self):
        """Ti=30s → i=0.5min; Td=15s → d=0.25min."""
        s = _make_structure(i_unit=UNIT_MINUTES, d_unit=UNIT_MINUTES)
        standard = StandardPid(kp=1.0, ti=30.0, td=15.0)
        dcs = from_standard_pid(standard, s)
        assert dcs.i == pytest.approx(0.5)
        assert dcs.d == pytest.approx(0.25)

    def test_proportion_passthrough(self):
        """PROPORTION 类型：Kp 直接透传."""
        s = _make_structure(p_type=P_TYPE_PROPORTION)
        dcs = DcsPid(p=3.5, i=10.0, d=2.0)
        standard = to_standard_pid(dcs, s)
        assert standard.kp == pytest.approx(3.5)

    def test_seconds_passthrough(self):
        """SECONDS 单位：时间直接透传."""
        s = _make_structure(i_unit=UNIT_SECONDS, d_unit=UNIT_SECONDS)
        dcs = DcsPid(p=1.0, i=45.0, d=12.0)
        standard = to_standard_pid(dcs, s)
        assert standard.ti == pytest.approx(45.0)
        assert standard.td == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# 边界与异常测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件与异常场景."""

    def test_pb_zero_raises(self):
        """PB=0 转换为 Kp 应抛 ValueError（除零）."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND)
        dcs = DcsPid(p=0.0, i=10.0, d=0.0)
        with pytest.raises(ValueError, match="除零"):
            to_standard_pid(dcs, s)

    def test_kp_zero_to_pb_raises(self):
        """Kp=0 转换为 PB 应抛 ValueError（除零）."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND)
        standard = StandardPid(kp=0.0, ti=10.0, td=0.0)
        with pytest.raises(ValueError, match="除零"):
            from_standard_pid(standard, s)

    def test_filter_enabled_does_not_affect_td(self):
        """微分滤波启用不影响标准 Td（DCS 实现细节）."""
        s = _make_structure(d_filter_enabled=True)
        dcs = DcsPid(p=1.0, i=10.0, d=5.0, d_filter=0.1)
        standard = to_standard_pid(dcs, s)
        assert standard.td == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 字典便捷转换测试
# ---------------------------------------------------------------------------


class TestConvertPidDict:
    """convert_pid_dict 字典形式转换."""

    def test_to_standard_from_dcs_dict(self):
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_MINUTES)
        dcs_dict = {"p": 50.0, "i": 0.5, "d": 0.0}
        result = convert_pid_dict(dcs_dict, s, to_standard=True)
        assert result["kp"] == pytest.approx(2.0)
        assert result["ti"] == pytest.approx(30.0)
        assert result["td"] == pytest.approx(0.0)

    def test_from_standard_to_dcs_dict(self):
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_MINUTES)
        standard_dict = {"kp": 2.0, "ti": 30.0, "td": 0.0}
        result = convert_pid_dict(standard_dict, s, to_standard=False)
        assert result["p"] == pytest.approx(50.0)
        assert result["i"] == pytest.approx(0.5)
        assert result["d"] == pytest.approx(0.0)

    def test_dict_round_trip(self):
        """字典形式往返一致."""
        s = _make_structure(p_type=P_TYPE_PROPORTION_BAND, i_unit=UNIT_MINUTES, d_unit=UNIT_MINUTES)
        original = {"p": 100.0, "i": 0.5, "d": 0.25}
        standard = convert_pid_dict(original, s, to_standard=True)
        restored = convert_pid_dict(standard, s, to_standard=False)
        assert restored["p"] == pytest.approx(original["p"], rel=1e-9)
        assert restored["i"] == pytest.approx(original["i"], rel=1e-9)
        assert restored["d"] == pytest.approx(original["d"], rel=1e-9)
