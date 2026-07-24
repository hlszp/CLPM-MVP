"""扰动检测模块单元测试（P2 抗扰性分析）.

测试 detect_disturbances 纯函数，覆盖：
- 无扰动 / 单次扰动恢复 / 多次扰动
- SP 阶跃排除 / 最小时长过滤
- 数据不足 / 零方差 / 非均匀采样

设计依据：实现方案 §3.2 算法步骤
"""

from __future__ import annotations

from app.services.metric_calculator.disturbance import detect_disturbances

#: 默认测试参数
_DEFAULTS = {
    "ideal_t": 60.0,
    "sample_interval": 1.0,
    "disturbance_band_sigma": 2.0,
    "recovery_persistence": 5,
    "min_disturbance_duration": 3.0,
    "sp_step_sigma": 3.0,
}


def _uniform_durations(n: int, interval: float = 1.0) -> list[float]:
    """生成 n 个等间隔时长。"""
    return [interval] * n


class TestDetectDisturbances:
    """detect_disturbances 测试。"""

    def test_no_disturbance_pv_tracks_sp(self):
        """PV 全程贴近 SP（小波动在 band 内）→ 无扰动。"""
        n = 100
        sp = [50.0] * n
        # PV 在 SP 附近 ±0.1 微小波动
        pv = [50.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(n)]
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        assert result.t_disturb is None
        assert result.count == 0

    def test_single_disturbance_recovery(self):
        """单次扰动后恢复 → 1 事件，recovery_time 合理。"""
        n = 100
        sp = [50.0] * n
        # PV 在 30-39 偏离 SP 10，其余贴近 SP
        pv = [50.0] * 30 + [60.0] * 10 + [50.0] * 60
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        assert result.count == 1
        assert result.t_disturb is not None
        # onset=30, recovery 在 30+10(偏离段)+5(持续)=45 附近
        # recovery_time = durations[30:46] = 16 秒（1s 采样）
        assert 14.0 <= result.t_disturb <= 18.0
        details = result.to_details()
        assert details["disturbance_count"] == 1

    def test_multiple_disturbances(self):
        """3 次独立扰动 → count=3，t_disturb=mean。"""
        n = 120
        sp = [50.0] * n
        # 3 段偏离，每段 8 个点，间隔 20 点
        pv = [50.0] * n
        for start in (10, 50, 90):
            for i in range(start, start + 8):
                pv[i] = 65.0
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        assert result.count == 3
        assert result.t_disturb is not None
        details = result.to_details()
        assert details["disturbance_count"] == 3
        # 三次恢复时间应相近，std 较小
        assert details["std_recovery_time"] is not None

    def test_sp_step_excluded(self):
        """SP 阶跃引发的 PV 跟踪不计为扰动。"""
        n = 100
        # SP 在 30 处从 50 阶跃到 70
        sp = [50.0] * 30 + [70.0] * 70
        # PV 跟踪：前 30 点 50，后 70 点逐步跟踪到 70（有偏离但属跟踪）
        pv = [50.0] * 30
        pv += [55.0, 60.0, 65.0, 68.0, 69.0]  # 跟踪过渡 5 点
        pv += [70.0] * 65
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        # 跟踪窗口内的偏离被排除 → 无扰动
        assert result.t_disturb is None
        assert result.count == 0

    def test_min_duration_filter(self):
        """短暂毛刺（持续时长 < min_disturbance_duration）被过滤。"""
        n = 100
        sp = [50.0] * n
        # 单点毛刺：1 秒 < 3 秒阈值
        pv = [50.0] * 50 + [80.0] + [50.0] * 49
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        assert result.count == 0
        assert result.t_disturb is None

    def test_min_duration_filter_disabled(self):
        """min_disturbance_duration=0 时不过滤，单点毛刺也计入。"""
        n = 100
        sp = [50.0] * n
        pv = [50.0] * 50 + [80.0] + [50.0] * 49
        result = detect_disturbances(
            pv,
            sp,
            _uniform_durations(n),
            ideal_t=60.0,
            sample_interval=1.0,
            disturbance_band_sigma=2.0,
            recovery_persistence=5,
            min_disturbance_duration=0.0,
            sp_step_sigma=3.0,
        )
        assert result.count == 1

    def test_insufficient_data(self):
        """n < 3 → 空分析。"""
        result = detect_disturbances([50.0, 60.0], [50.0, 50.0], [1.0, 1.0], **_DEFAULTS)
        assert result.t_disturb is None
        assert result.count == 0

    def test_zero_error_std(self):
        """PV=SP 恒定 → error_std=0 → 空分析。"""
        n = 50
        pv = [50.0] * n
        sp = [50.0] * n
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        assert result.t_disturb is None
        assert result.count == 0

    def test_nonuniform_sampling(self):
        """非均匀采样 → recovery_time 用实际 point_durations 而非点数。"""
        n = 20
        sp = [50.0] * n
        # 扰动在 5-7，偏离 10
        pv = [50.0] * 5 + [60.0] * 3 + [50.0] * 12
        # 非均匀：扰动段每点 2 秒，其余 1 秒
        durations = [1.0] * 5 + [2.0] * 3 + [1.0] * 12
        result = detect_disturbances(pv, sp, durations, **_DEFAULTS)
        assert result.count == 1
        assert result.t_disturb is not None
        # onset=5, recovery=12（5 持续点从 8 起：8,9,10,11,12）
        # recovery_time = durations[5:13] = 2+2+2+1+1+1+1+1 = 11.0
        # 若按均匀 1s 计算则为 8.0，此处验证非均匀生效
        assert result.t_disturb == 11.0

    def test_to_details_empty(self):
        """空分析的 to_details 返回全 None。"""
        result = detect_disturbances(
            [50.0, 50.0, 50.0], [50.0, 50.0, 50.0], [1.0, 1.0, 1.0], **_DEFAULTS
        )
        details = result.to_details()
        assert details["disturbance_count"] == 0
        assert details["mean_recovery_time"] is None

    def test_to_details_with_events(self):
        """有事件时 to_details 返回统计值。"""
        n = 100
        sp = [50.0] * n
        pv = [50.0] * 30 + [60.0] * 10 + [50.0] * 60
        result = detect_disturbances(pv, sp, _uniform_durations(n), **_DEFAULTS)
        details = result.to_details()
        assert details["disturbance_count"] == 1
        assert details["mean_recovery_time"] is not None
        assert details["max_recovery_time"] == details["mean_recovery_time"]
        assert details["min_recovery_time"] == details["mean_recovery_time"]
        # 单事件 std=0
        assert details["std_recovery_time"] == 0.0
