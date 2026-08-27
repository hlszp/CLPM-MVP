"""扰动检测模块（P2 抗扰性分析）.

检测 PV 相对 SP 的扰动事件并计算恢复时间，作为 fast_rate 的可选输入。
当存在扰动事件时，平均恢复时间替代 ARMA 稳态时间，更直接反映回路抗扰能力。

设计依据：HiaMonitor 借鉴重构计划评审报告 P2-3
关键策略：
- SP 阶跃跟踪窗口排除（避免设定值变化误判为扰动）
- sigma 带判定扰动（band = disturbance_band_sigma × error_std）
- 恢复持续点数确认（recovery_persistence 连续点在带内）
- 最小扰动时长过滤（min_disturbance_duration）
- 窗口内未恢复事件标记 censored，不纳入恢复时间统计

本模块为纯函数，不依赖 MetricCalculatorBase，便于独立单元测试。
"""

from __future__ import annotations

import math
import statistics as stats
from dataclasses import dataclass, field


@dataclass
class DisturbanceEvent:
    """单次扰动事件。

    Attributes:
        onset_idx: 扰动起始点索引（PV 首次超出 band）
        end_idx: 扰动结束点索引（PV 最后一次超出 band）
        recovery_idx: 恢复确认点索引（连续 persistence 点回到 band 内的末尾）；
            删失事件为观测窗口末尾 n-1
        recovery_time: 从 onset 到 recovery 的时长（秒）；
            删失事件为到窗口末尾的时长（真实恢复时间的下界，不计入统计）
        censored: 窗口内未确认恢复（删失事件），不纳入恢复时间统计
    """

    onset_idx: int
    end_idx: int
    recovery_idx: int
    recovery_time: float
    censored: bool = False


@dataclass
class DisturbanceAnalysis:
    """扰动分析结果。

    events 为空时 t_disturb 为 None，调用方据此决定是否回落 ARMA。
    聚合统计从 events 现算，避免冗余字段与取整不一致。
    """

    events: list[DisturbanceEvent] = field(default_factory=list)

    @property
    def t_disturb(self) -> float | None:
        """平均恢复时间（秒）；仅统计已恢复事件，无已恢复事件时 None。"""
        recovered = [e for e in self.events if not e.censored]
        if not recovered:
            return None
        return stats.mean(e.recovery_time for e in recovered)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def censored_count(self) -> int:
        """删失事件数（观测窗口内未确认恢复）。"""
        return sum(1 for e in self.events if e.censored)

    def to_details(self) -> dict:
        """序列化为 MetricResult.details 片段（取整 2 位）。

        disturbance_count 为事件总数（含删失），恢复时间统计仅基于已恢复事件；
        删失事件单列 censored_count。
        """
        if not self.events:
            return {
                "disturbance_count": 0,
                "censored_count": 0,
                "mean_recovery_time": None,
                "max_recovery_time": None,
                "min_recovery_time": None,
                "std_recovery_time": None,
            }
        rts = [e.recovery_time for e in self.events if not e.censored]
        return {
            "disturbance_count": len(self.events),
            "censored_count": self.censored_count,
            "mean_recovery_time": round(stats.mean(rts), 2) if rts else None,
            "max_recovery_time": round(max(rts), 2) if rts else None,
            "min_recovery_time": round(min(rts), 2) if rts else None,
            "std_recovery_time": (round(stats.pstdev(rts), 2) if len(rts) >= 2 else 0.0)
            if rts
            else None,
        }


def _empty_analysis() -> DisturbanceAnalysis:
    """构造空分析结果（无扰动事件）。"""
    return DisturbanceAnalysis()


def detect_disturbances(
    pv: list[float],
    sp: list[float],
    point_durations: list[float],
    *,
    ideal_t: float,
    sample_interval: float,
    disturbance_band_sigma: float = 2.0,
    recovery_persistence: int = 5,
    min_disturbance_duration: float = 3.0,
    sp_step_sigma: float = 3.0,
) -> DisturbanceAnalysis:
    """扰动检测主函数（算法见模块文档与实现方案 §3.2）.

    Args:
        pv: 过程变量序列
        sp: 设定值序列
        point_durations: 每个采样点代表的时长（秒），长度应与 pv 一致
        ideal_t: 理想稳态时间（秒），用于确定 SP 阶跃跟踪窗口大小
        sample_interval: 平均采样间隔（秒），point_durations 不足时补齐
        disturbance_band_sigma: 扰动带宽度（error_std 的倍数）
        recovery_persistence: 确认恢复所需的连续带内点数
        min_disturbance_duration: 最小扰动持续时长（秒），短于此丢弃
        sp_step_sigma: SP 阶跃检测阈值（sp_diff_std 的倍数）

    Returns:
        DisturbanceAnalysis，含扰动事件列表与聚合统计
    """
    n = len(pv)
    if n < 3 or len(sp) < n:
        return _empty_analysis()

    # 对齐长度（防御性）
    sp = sp[:n]
    if len(point_durations) < n:
        pad = [sample_interval if sample_interval > 0 else 1.0] * (n - len(point_durations))
        point_durations = list(point_durations) + pad
    point_durations = point_durations[:n]

    # 1. 误差序列与总体标准差
    errors = [pv[i] - sp[i] for i in range(n)]
    error_std = stats.pstdev(errors)
    if error_std <= 0:
        return _empty_analysis()

    band = disturbance_band_sigma * error_std

    # 2. SP 阶跃检测 → 跟踪窗口掩码（排除设定值跟踪段）
    tracking = detect_sp_tracking_windows(sp, n, ideal_t, sample_interval, sp_step_sigma)

    # 3. 扰动点：|error| > band 且不在跟踪窗口内
    disturbed = [i for i in range(n) if not tracking[i] and abs(errors[i]) > band]
    if not disturbed:
        return _empty_analysis()

    # 4. 连续扰动点分组为事件
    events: list[DisturbanceEvent] = []
    group_start = disturbed[0]
    prev = disturbed[0]
    for idx in disturbed[1:]:
        if idx == prev + 1:
            prev = idx
        else:
            _append_event(
                events,
                group_start,
                prev,
                n,
                errors,
                band,
                point_durations,
                recovery_persistence,
                min_disturbance_duration,
            )
            group_start = idx
            prev = idx
    # 处理最后一组
    _append_event(
        events,
        group_start,
        prev,
        n,
        errors,
        band,
        point_durations,
        recovery_persistence,
        min_disturbance_duration,
    )

    if not events:
        return _empty_analysis()

    return DisturbanceAnalysis(events=events)


def detect_sp_tracking_windows(
    sp: list[float],
    n: int,
    ideal_t: float,
    sample_interval: float,
    sp_step_sigma: float,
    window_points: int | None = None,
) -> list[bool]:
    """检测 SP 阶跃并标记跟踪窗口（公共接口，fast_rate 扰动分析与稳定率剔除共用）。

    阶跃判定：|sp[i+1]-sp[i]| > sp_step_sigma × pstdev(sp_diffs)。
    跟踪窗口从阶跃点（新 SP 首个点）起持续：
    - window_points 显式给定时取该值（无 ideal_t 上下文的调用方，如稳定率）
    - 否则按约 ideal_t 时长换算（ideal_t<=0 时回退 60 点）

    Returns:
        布尔数组（长度 n），True 表示该点处于 SP 阶跃后的跟踪窗口内
    """
    tracking = [False] * n
    if n < 2:
        return tracking

    sp_diffs = [sp[i + 1] - sp[i] for i in range(n - 1)]
    # NaN/Inf 防护：statistics.pstdev 遇 NaN 抛 AttributeError（CPython 已知行为），
    # 含非有限值时阶跃检测不可信，直接返回无剔除（全 False）
    if any(not math.isfinite(d) for d in sp_diffs):
        return tracking
    sp_diff_std = stats.pstdev(sp_diffs) if len(sp_diffs) >= 2 else 0.0
    if sp_diff_std <= 0:
        return tracking

    step_threshold = sp_step_sigma * sp_diff_std
    if window_points is not None:
        window_size = max(1, window_points)
    elif ideal_t > 0 and sample_interval > 0:
        window_size = max(5, int(ideal_t / sample_interval))
    else:
        window_size = 60

    for i, d in enumerate(sp_diffs):
        if abs(d) > step_threshold:
            start = i + 1  # 新 SP 从 i+1 起生效
            end = min(n, start + window_size)
            for j in range(start, end):
                tracking[j] = True
    return tracking


def _append_event(
    events: list[DisturbanceEvent],
    onset_idx: int,
    end_idx: int,
    n: int,
    errors: list[float],
    band: float,
    point_durations: list[float],
    recovery_persistence: int,
    min_disturbance_duration: float,
) -> None:
    """构造扰动事件并按 min_disturbance_duration 过滤后追加。"""
    # 扰动持续时长（onset 到 end）
    disturbance_duration = sum(point_durations[onset_idx : end_idx + 1])
    if disturbance_duration < min_disturbance_duration:
        return

    # 恢复检测：从 end_idx+1 起找连续 recovery_persistence 个 |error|<=band 的窗口
    recovery_idx = _find_recovery(errors, end_idx + 1, n, band, recovery_persistence)
    if recovery_idx is None:
        # 窗口内未确认恢复（含扰动持续到窗口末尾）→ 删失事件，
        # recovery_time 记到窗口末尾作为下界，不纳入恢复时间统计
        recovery_idx = n - 1
        censored = True
    else:
        censored = False
    # recovery_time = onset 到恢复确认点的时长
    recovery_time = sum(point_durations[onset_idx : recovery_idx + 1])
    events.append(
        DisturbanceEvent(
            onset_idx=onset_idx,
            end_idx=end_idx,
            recovery_idx=recovery_idx,
            recovery_time=recovery_time,
            censored=censored,
        )
    )


def _find_recovery(
    errors: list[float],
    start: int,
    n: int,
    band: float,
    persistence: int,
) -> int | None:
    """从 start 起找连续 persistence 个 |error|<=band 的窗口末尾索引。

    Returns:
        恢复确认点索引；未在观测窗口内恢复时返回 None（删失）
    """
    if start >= n:
        return None
    run = 0
    for i in range(start, n):
        if abs(errors[i]) <= band:
            run += 1
            if run >= persistence:
                return i
        else:
            run = 0
    return None


__all__ = [
    "DisturbanceEvent",
    "DisturbanceAnalysis",
    "detect_disturbances",
    "detect_sp_tracking_windows",
]
