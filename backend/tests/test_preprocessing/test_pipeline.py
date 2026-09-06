"""8 步预处理 Pipeline 端到端单元测试.

测试 PreprocessingPipeline 从 RawTimeSeries 到 DataBlock 的完整处理流程：
    ① 质量码识别 → ② 有效性标记 → ③ 量程归一化 → ④ 异常值识别 →
    ⑤ 缺失率统计 → ⑥ 连续性检查 → ⑦ Metric Mask → ⑧ QualitySummary

核心验收标准（KEEP_ALL_WITH_VALIDITY）：
    - 不删除任何数据点（point_count 不变）
    - valid 标记正确（Good→True, Bad→False）
    - TS_ANOMALY 和 HF_NOISE 仅标记不置 valid=False
    - 归一化后 PV/SP/OP 为百分比（0~100）

设计依据：算法说明 §3.4.2, PRD §5.5
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from app.contracts.data_types import (
    ControlType,
    LoopPreprocessConfig,
    OutlierReason,
    RawTimeSeries,
    TagGroup,
)
from app.services.preprocessing.pipeline import PREPROCESS_VERSION, PreprocessingPipeline

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_timestamps(n: int, interval_s: float = 1.0) -> list[datetime]:
    """生成等间隔时间戳。"""
    base = datetime(2024, 1, 1)
    return [base + timedelta(seconds=i * interval_s) for i in range(n)]


def _make_config(
    control_type: ControlType = ControlType.FLOW,
    range_min: float = 0.0,
    range_max: float = 100.0,
    loop_id: str = "L001",
    op_range_min: float = 0.0,
    op_range_max: float = 100.0,
) -> LoopPreprocessConfig:
    """构造预处理配置。"""
    return LoopPreprocessConfig(
        loop_id=loop_id,
        control_type=control_type,
        range_min=range_min,
        range_max=range_max,
        op_range_min=op_range_min,
        op_range_max=op_range_max,
    )


# ---------------------------------------------------------------------------
# 基本流程测试
# ---------------------------------------------------------------------------


class TestPipelineBasic:
    """Pipeline 基本流程测试。"""

    def test_clean_data_all_valid(self):
        """干净数据：全部 valid=True，valid_rate=1.0。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={
                "pv": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
                "sp": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
                "op": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # KEEP_ALL_WITH_VALIDITY：不删除任何数据点
        assert block.point_count == n
        assert len(block.timestamps) == n

        # 全部 valid=True
        for tag in ["pv", "sp", "op"]:
            assert all(block.validity[f"{tag}_valid"]), f"{tag} 应全部有效"

        # 质量摘要
        assert block.quality_summary.total_count == n
        assert block.quality_summary.valid_count == n
        assert block.quality_summary.valid_rate == 1.0
        assert block.quality_summary.bad_count == 0

    def test_data_block_id_format(self):
        """DataBlock ID 格式：db_{loopId}_{tagGroup}_{freq}。"""
        config = _make_config(ControlType.FLOW, loop_id="L001")
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        assert block.data_block_id == "db_L001_BASE_1s"
        assert block.loop_id == "L001"
        assert block.tag_group == "BASE"
        assert block.sampling_freq == "1s"

    def test_preprocess_version(self):
        """DataBlock 携带预处理版本号。"""
        config = _make_config()
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        assert block.preprocess_version == PREPROCESS_VERSION
        assert block.config_version == "v1"


# ---------------------------------------------------------------------------
# KEEP_ALL_WITH_VALIDITY 策略
# ---------------------------------------------------------------------------


class TestKeepAllWithValidity:
    """KEEP_ALL_WITH_VALIDITY：不删除任何数据点。"""

    def test_point_count_unchanged_with_bad_quality(self):
        """有 Bad 质量码时不删除数据点。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={
                "pv": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
            },
            quality_codes={
                "pv_quality": [1, 1, 0, 1, 1, 1, 1],  # index 2 = Bad
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # point_count 不变
        assert block.point_count == n
        assert len(block.timestamps) == n
        assert len(block.validity["pv_valid"]) == n

    def test_point_count_unchanged_with_outliers(self):
        """有异常值时不删除数据点。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={
                "pv": [50.0, 50.1, 200.0, 50.3, 50.4, 50.5, 50.6],  # 200 超量程
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # point_count 不变
        assert block.point_count == n
        # 超量程点 valid=False 但不删除
        assert block.validity["pv_valid"][2] is False

    def test_point_count_unchanged_with_nan(self):
        """有 NaN 值时不删除数据点。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={
                "pv": [50.0, 50.1, float("nan"), 50.3, 50.4, 50.5, 50.6],
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert block.point_count == n
        assert block.validity["pv_valid"][2] is False


# ---------------------------------------------------------------------------
# valid 标记测试
# ---------------------------------------------------------------------------


class TestValidityMarking:
    """valid 标记正确性测试（Good→True, Bad→False）。"""

    def test_bad_quality_code_marks_invalid(self):
        """Bad 质量码 → valid=False。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
            quality_codes={"pv_quality": [1, 1, 0, 1, 1, 1, 1]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        pv_valid = block.validity["pv_valid"]
        assert pv_valid[0] is True
        assert pv_valid[2] is False  # Bad quality → False
        assert pv_valid[3] is True

    def test_unknown_quality_code_marks_invalid(self):
        """Unknown 质量码 → valid=False。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
            quality_codes={"pv_quality": [1, 999, 1, 1, 1, 1, 1]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert block.validity["pv_valid"][1] is False

    def test_out_of_range_marks_invalid(self):
        """超量程值 → valid=False + OUT_OF_RANGE 原因码。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0, 50.1, 150.0, 50.3, 50.4, 50.5, 50.6]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert block.validity["pv_valid"][2] is False
        assert OutlierReason.OUT_OF_RANGE.value in block.outlier_reasons["pv"][2]

    def test_nan_marks_invalid(self):
        """NaN 值 → valid=False + NaN 原因码。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0, 50.1, float("nan"), 50.3, 50.4, 50.5, 50.6]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert block.validity["pv_valid"][2] is False
        assert OutlierReason.NAN.value in block.outlier_reasons["pv"][2]

    def test_ts_anomaly_only_marks_not_invalidate(self):
        """TS_ANOMALY 仅标记不置 valid=False。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        # 包含重复时间戳
        base = datetime(2024, 1, 1)
        timestamps = [
            base,
            base + timedelta(seconds=1),
            base + timedelta(seconds=1),  # 重复 → TS_ANOMALY
            base + timedelta(seconds=2),
            base + timedelta(seconds=3),
            base + timedelta(seconds=4),
            base + timedelta(seconds=5),
        ]
        raw = RawTimeSeries(
            timestamps=timestamps,
            signals={"pv": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # index 2 有 TS_ANOMALY 标记
        assert OutlierReason.TS_ANOMALY.value in block.outlier_reasons["pv"][2]
        # 但 valid 仍为 True（TS_ANOMALY 仅标记）
        assert block.validity["pv_valid"][2] is True

    def test_qc_bad_reason_recorded(self):
        """Bad 质量码 → outlier_reasons 含 QC_BAD。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
            quality_codes={"pv_quality": [1, 1, 0, 1, 1, 1, 1]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert OutlierReason.QC_BAD.value in block.outlier_reasons["pv"][2]
        assert block.validity["pv_valid"][2] is False


# ---------------------------------------------------------------------------
# 归一化测试
# ---------------------------------------------------------------------------


class TestNormalization:
    """PV/SP/OP 量程归一化测试。"""

    def test_pv_normalized_to_percentage(self):
        """PV/SP 用 PV 量程、OP 用 OP 量程归一化为 0~100 百分比。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=200.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={
                "pv": [100.0, 100.4, 100.8, 101.2, 101.6, 102.0, 102.4],
                "sp": [100.0, 100.4, 100.8, 101.2, 101.6, 102.0, 102.4],
                "op": [50.0, 50.2, 50.4, 50.6, 50.8, 51.0, 51.2],
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # PV/SP 用 PV 量程（0-200）归一化：100→50, 100.4→50.2
        assert math.isclose(block.signals["pv"][0], 50.0, abs_tol=1e-6)
        assert math.isclose(block.signals["pv"][1], 50.2, abs_tol=1e-6)
        assert math.isclose(block.signals["sp"][0], 50.0, abs_tol=1e-6)
        # OP 用 OP 量程（0-100）归一化：50→50, 50.2→50.2（恒等变换）
        assert math.isclose(block.signals["op"][0], 50.0, abs_tol=1e-6)
        assert math.isclose(block.signals["op"][1], 50.2, abs_tol=1e-6)

    def test_nan_preserved_in_normalization(self):
        """NaN 值在归一化后保持原样。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=200.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [100.0, 100.4, float("nan"), 101.2, 101.6, 102.0, 102.4]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # NaN 保持不变
        assert math.isnan(block.signals["pv"][2])
        # 非 NaN 值正常归一化
        assert math.isclose(block.signals["pv"][0], 50.0, abs_tol=1e-6)

    def test_identity_normalization(self):
        """range 0-100 时归一化为恒等变换。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # 归一化后值不变
        for i in range(7):
            assert math.isclose(block.signals["pv"][i], 50.0 + i * 0.1, abs_tol=1e-6)

    def test_op_uses_own_range_not_pv_range(self):
        """OP 用 OP 自身量程归一化，不受 PV 量程影响（90PIC 场景）。

        PV 量程 0-5 MPa，OP 量程 0-100%：
        OP=49 归一化后应为 49（用 OP 量程），而非 980（用 PV 量程）。
        """
        config = _make_config(
            ControlType.PRESSURE,
            range_min=0.0,
            range_max=5.0,
            op_range_min=0.0,
            op_range_max=100.0,
        )
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(5),
            signals={
                "pv": [2.5, 2.5, 2.5, 2.5, 2.5],
                "sp": [2.5, 2.5, 2.5, 2.5, 2.5],
                "op": [49.0, 49.0, 49.0, 49.0, 49.0],
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)
        # PV/SP 用 PV 量程归一化：2.5/5*100=50
        assert math.isclose(block.signals["pv"][0], 50.0, abs_tol=1e-6)
        assert math.isclose(block.signals["sp"][0], 50.0, abs_tol=1e-6)
        # OP 用 OP 量程归一化：49/100*100=49（不是 980）
        assert math.isclose(block.signals["op"][0], 49.0, abs_tol=1e-6)

    def test_op_not_out_of_range_when_pv_range_is_small(self):
        """PV 量程小时 OP 不应被误标 OUT_OF_RANGE（90PIC51212A 场景复现）。"""
        config = _make_config(
            ControlType.PRESSURE,
            range_min=0.0,
            range_max=5.0,
            op_range_min=0.0,
            op_range_max=100.0,
        )
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={
                "pv": [2.5 + i * 0.01 for i in range(7)],
                "sp": [2.5 + i * 0.01 for i in range(7)],
                "op": [49.0 + i * 0.1 for i in range(7)],
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)
        # OP 全部有效，不应有 OUT_OF_RANGE
        assert all(block.validity["op_valid"])
        for reasons in block.outlier_reasons["op"]:
            assert OutlierReason.OUT_OF_RANGE.value not in reasons

    def test_op_out_of_range_uses_op_range(self):
        """OP 超 OP 自身量程仍被标记 OUT_OF_RANGE（回归保护）。"""
        config = _make_config(
            ControlType.PRESSURE,
            range_min=0.0,
            range_max=5.0,
            op_range_min=0.0,
            op_range_max=100.0,
        )
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(5),
            signals={"op": [50.0, 50.0, 120.0, 50.0, 50.0]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        # OP=120 归一化后 120 > 100 → OUT_OF_RANGE
        assert OutlierReason.OUT_OF_RANGE.value in block.outlier_reasons["op"][2]
        assert block.validity["op_valid"][2] is False

    def test_op_normalized_with_non_standard_range(self):
        """OP 非标准量程（0-50）正确归一化：OP=25 → 50。"""
        config = _make_config(
            ControlType.PRESSURE,
            range_min=0.0,
            range_max=5.0,
            op_range_min=0.0,
            op_range_max=50.0,
        )
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(3),
            signals={"op": [25.0, 30.0, 40.0]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        assert math.isclose(block.signals["op"][0], 50.0, abs_tol=1e-6)
        assert math.isclose(block.signals["op"][1], 60.0, abs_tol=1e-6)
        assert math.isclose(block.signals["op"][2], 80.0, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# 质量摘要测试
# ---------------------------------------------------------------------------


class TestPipelineQualitySummary:
    """Pipeline 输出的 QualitySummary 测试。"""

    def test_valid_rate_with_bad_quality(self):
        """有 Bad 质量码时 valid_rate 正确计算。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={"pv": [50.0 + i * 0.1 for i in range(n)]},
            quality_codes={"pv_quality": [1, 1, 0, 1, 1, 1, 1]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # 7 点中 1 点 Bad → valid_rate = 6/7
        assert block.quality_summary.valid_count == 6
        assert block.quality_summary.bad_count == 1
        assert block.quality_summary.valid_rate == round(6 / 7, 4)

    def test_good_value_rate_computed(self):
        """有质量码时 good_value_rate 被计算。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
            quality_codes={"pv_quality": [1, 0, 1, 1, 1, 1, 1]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # 6 Good / 7 total
        assert block.quality_summary.good_value_rate == round(6 / 7, 4)

    def test_good_value_rate_none_without_qc(self):
        """无质量码时 good_value_rate=None。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        assert block.quality_summary.good_value_rate is None


# ---------------------------------------------------------------------------
# Metric Mask 生成
# ---------------------------------------------------------------------------


class TestPipelineMetricMask:
    """Pipeline 的 generate_metric_mask 方法测试。"""

    def test_mask_returns_both_valid_indices(self):
        """pv_valid && sp_valid 返回两者都有效的索引。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={
                "pv": [50.0 + i * 0.1 for i in range(7)],
                "sp": [50.0 + i * 0.1 for i in range(7)],
            },
            quality_codes={
                "pv_quality": [1, 1, 0, 1, 1, 1, 1],  # pv index 2 无效
            },
        )
        block = pipeline.process(raw, TagGroup.BASE)

        indices = pipeline.generate_metric_mask(block, "pv_valid && sp_valid")
        # index 2 pv_valid=False → 被排除
        assert 2 not in indices
        assert 0 in indices
        assert 1 in indices
        assert 3 in indices

    def test_mask_none_returns_all_indices(self):
        """None 表达式返回全部索引。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={"pv": [50.0 + i * 0.1 for i in range(n)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        indices = pipeline.generate_metric_mask(block, None)
        assert indices == list(range(n))


# ---------------------------------------------------------------------------
# 不同控制类型测试
# ---------------------------------------------------------------------------


class TestPipelineControlTypes:
    """Pipeline 对不同控制类型的支持测试。"""

    @pytest.mark.parametrize(
        "control_type,expected_freq",
        [
            (ControlType.FLOW, "1s"),
            (ControlType.PRESSURE, "2s"),
            (ControlType.TEMPERATURE, "5s"),
            (ControlType.LEVEL, "5s"),
            (ControlType.COMPOSITION, "10s"),
        ],
    )
    def test_sampling_freq_reflects_actual_interval(self, control_type, expected_freq):
        """R14-1：sampling_freq 反映**实际**采样（相邻 ts 中位间隔）。

        数据按契约间隔（expected_freq）等间隔铺排时，标签与名义一致。
        修复前标签恒为控制类型名义值，稀疏数据（如 30s 间隔）被标 "1s"
        会让 ARMA 类算法按错误时间尺度计算。
        """
        config = _make_config(control_type, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        # 使用足够多的点避免 FROZEN（值要有变化）
        n = 12
        interval = float(expected_freq.rstrip("s"))
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n, interval),
            signals={"pv": [50.0 + i * 0.2 for i in range(n)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        assert block.sampling_freq == expected_freq

    def test_sampling_freq_sparse_data_labelled_with_actual_interval(self):
        """R14-1 核心场景：名义 1s（FLOW）契约下实际 30s 稀疏采样 → 标 "30s"。

        修复前标签沿用名义 "1s"（缺陷行为），settling_time 等按 1s 计算
        时间尺度错误 30 倍。
        """
        config = _make_config(ControlType.FLOW)
        pipeline = PreprocessingPipeline(config)
        n = 120
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n, 30.0),
            signals={"pv": [50.0 + i * 0.2 for i in range(n)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)
        assert block.sampling_freq == "30s"


# ---------------------------------------------------------------------------
# 连续段测试
# ---------------------------------------------------------------------------


class TestPipelineConsecutiveSegments:
    """Pipeline 连续有效段测试。"""

    def test_no_segments_when_too_short(self):
        """数据点数不足 min_consecutive_points → 无连续段。"""
        config = _make_config(ControlType.FLOW)  # min_consecutive_points=30
        pipeline = PreprocessingPipeline(config)
        raw = RawTimeSeries(
            timestamps=_make_timestamps(7),
            signals={"pv": [50.0 + i * 0.1 for i in range(7)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # 7 < 30 → 无连续段
        assert block.consecutive_segments == []

    def test_segments_with_all_valid_long_enough(self):
        """足够长的全有效数据 → 有连续段。"""
        # TC: min_consecutive_points=15
        config = _make_config(ControlType.TEMPERATURE, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 20
        # 5s 间隔，值变化以避免 FROZEN
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n, 5.0),
            signals={"pv": [50.0 + i * 0.5 for i in range(n)]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # 全有效，长度 20 >= 15 → 单段 [0, 19]
        assert len(block.consecutive_segments) >= 1
        assert block.consecutive_segments[0] == (0, n - 1)

    def test_segments_cut_by_invalid_point(self):
        """无效点切断连续段。"""
        # LC: min_consecutive_points=15
        config = _make_config(ControlType.LEVEL, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 35
        # 中间有一个 Bad 质量码
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n, 5.0),
            signals={"pv": [50.0 + i * 0.3 for i in range(n)]},
            quality_codes={"pv_quality": [1] * 10 + [0] + [1] * (n - 11)},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # index 10 无效 → 段被切分为 [0,9] 和 [11,34]
        # 但 [0,9] 长度 10 < 15 → 丢弃
        # [11,34] 长度 24 >= 15 → 保留
        assert len(block.consecutive_segments) == 1
        start, end = block.consecutive_segments[0]
        assert start == 11
        assert end == n - 1


# ---------------------------------------------------------------------------
# P1 整改：FROZEN 仅标记 + JUMP/SPIKE 量纲一致
# ---------------------------------------------------------------------------


class TestFrozenMarkOnlyPipeline:
    """FROZEN 仅标记不置 invalid：平稳良好回路 valid_rate 不被拖零。"""

    def test_steady_loop_frozen_marked_but_valid(self):
        """控制良好的平稳回路（PV 恒定）→ FROZEN 标记，但全点 valid=True。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 30
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={"pv": [50.0] * n},  # 平稳回路：PV 长期不动
        )
        block = pipeline.process(raw, TagGroup.BASE)

        # FROZEN 仍被标记（供仪表故障率复合判据/展示使用）
        assert any(OutlierReason.FROZEN.value in reasons for reasons in block.outlier_reasons["pv"])
        # 但不置 invalid：valid_rate 不被拖零，避免全 KPI INCONCLUSIVE
        assert all(block.validity["pv_valid"])
        assert block.quality_summary.valid_rate == 1.0

    def test_sensor_stuck_still_marked_frozen(self):
        """传感器真卡死（PV 死值）仍被 FROZEN 标记（供下游复合判据识别）。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=100.0)
        pipeline = PreprocessingPipeline(config)
        n = 10
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            signals={"pv": [50.0, 50.1, 50.2] + [66.6] * (n - 3)},  # 后段卡死
        )
        block = pipeline.process(raw, TagGroup.BASE)

        frozen_indices = [
            i
            for i, reasons in enumerate(block.outlier_reasons["pv"])
            if OutlierReason.FROZEN.value in reasons
        ]
        # 卡死段（index 3 起）被 FROZEN 覆盖
        assert set(range(3, n)).issubset(set(frozen_indices))


class TestJumpSpikePipelineWideRange:
    """Pipeline 端到端：量程 0~800 下归一化数据的 JUMP/SPIKE 能触发。"""

    def test_jump_and_spike_triggered_with_range_800(self):
        """量程 0~800：归一化后 diff=85 > 80 → JUMP + SPIKE 触发且置 invalid。"""
        config = _make_config(ControlType.FLOW, range_min=0.0, range_max=800.0)
        pipeline = PreprocessingPipeline(config)
        n = 7
        raw = RawTimeSeries(
            timestamps=_make_timestamps(n),
            # 原始值 780 → 归一化 97.5，与前后 12.5 的 diff=85
            signals={"pv": [100.0, 100.0, 780.0, 100.0, 100.0, 100.4, 100.8]},
        )
        block = pipeline.process(raw, TagGroup.BASE)

        reasons_2 = block.outlier_reasons["pv"][2]
        assert OutlierReason.JUMP.value in reasons_2
        assert OutlierReason.SPIKE.value in reasons_2
        # JUMP/SPIKE 仍置 invalid（非 MARK_ONLY）
        assert block.validity["pv_valid"][2] is False
