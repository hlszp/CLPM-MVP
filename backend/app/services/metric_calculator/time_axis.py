"""时间轴薄层（P3-08b，O-2 定稿落地）——UTC 半开区间的唯一适配落点.

设计依据（正本）：
- D0+P3-08a 公共契约草案 §2.1（一份时间契约，08b/c 与 D1 共同消费）；
- 10 号文 §3.1（一个时间视图，分别表达已知、无效、未知）；
- O-2 定稿（P3-08a 交接）：**新建本薄层**，内部复用 ``contracts/series_context``
  既有 ``RoleCoverage``（半开游程编码）/``SegmentBoundary``，不重复实现游程
  编码，**不建第二套原始时序存储**；``TimeWindow``（含尾）语义不改——
  含尾→半开的适配收敛在**本文件一处**。

契约要点（本层强制）：
1. 窗口 = UTC 半开区间 ``[start, end)``；含尾 ``TimeWindow(start, end_incl)``
   → 半开 ``end = end_incl + 1s``（秒为最小包含单位）；含尾网格
   （``SeriesContext.grid_start/grid_end``）→ ``[grid_start, grid_end + period)``。
2. 每槽有效区间为半开 ``[ts_i, ts_{i+1})``，**末槽截到窗口 end**——不得复制
   上一段时长越过窗口（C04-EA1：末点 1s 不按 3s 越窗）。
3. 逐角色三态：OBSERVED（本槽时长内有新观测依据）/ HELD（合法 COV 保持，
   有状态依据、可用于比例统计但不是每秒新观测）/ UNKNOWN（无依据区间，
   归因六因：gap/no_initial/conflict/unbound/rebind/held_too_long）。
4. 比率口径：分母 = 完整请求窗口秒数；分子只计有依据且条件成立的秒数；
   未知段不补时、不缩分母、不当手动/未饱和（C03-EA2：不报确定 60%）。
5. 覆盖 1/2/5/10 秒网格。

降级口径（视图不可用 = 数据块既无查询窗口又无 point 上下文，如旧缓存/
手工构造的测试 bundle）：``build_metric_time_view`` 返回 ``None``，调用方
（R/Auto/Sat/S 计算器）回落各自 legacy 时长口径——旧路径行为完全不变。
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.contracts import series_context as sc
from app.contracts.data_types import MetricDataBundle, TimeWindow

logger = logging.getLogger(__name__)

#: 时间/质量契约策略版本（缓存身份分量）：本层语义（半开/未知/分段切割）
#: 变更时递增；DataPlanner 将其纳入 L1/L2 缓存键，策略演进旧缓存自然失效
#: （10§3.2"缓存身份纳入数据策略、网格、窗口、源修订、绑定与解释版本"）。
TIME_QUALITY_POLICY_VERSION = "tqpv1"

#: 契约覆盖的网格周期（秒）
SUPPORTED_GRID_PERIODS: frozenset[float] = frozenset({1.0, 2.0, 5.0, 10.0})

#: 分母口径常量（details.denominatorBasis，契约 §2.1"和分母口径"）
DENOMINATOR_FULL_REQUEST_WINDOW = "FULL_REQUEST_WINDOW"

#: 窗口内数据未覆盖部分（头/尾整段缺数据）的归因标记（区别于槽级六因；
#: 不进 RoleCoverage 六因码表，仅在视图摘要中单列）
UNCOVERED_WINDOW = "uncovered_window"


class SlotState(StrEnum):
    """槽状态三态（契约 §2.2）。"""

    OBSERVED = "OBSERVED"  # 本槽时长内有新观测依据（事件新鲜度 ≤ 网格周期）
    HELD = "HELD"  # 合法 COV 保持/覆盖契约内的状态依据（非每秒新观测）
    UNKNOWN = "UNKNOWN"  # 无依据区间（六因归因）


def _utc(dt: datetime) -> datetime:
    """naive 视为 UTC；aware 归一化到 UTC（与 series_context._utc 同口径）。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_half_open(start: datetime, end_inclusive: datetime) -> tuple[datetime, datetime]:
    """含尾窗口 → 半开 ``[start, end_inclusive + 1s)``（唯一适配点，C04-EA1）。"""
    s = _utc(start)
    e = _utc(end_inclusive)
    if e < s:
        e = s
    return s, e + timedelta(seconds=1)


@dataclass(frozen=True)
class RatioAccounting:
    """比率口径账（契约 §2.1 建议输出字段）。

    - ``denominator_seconds``：完整请求窗口秒数（R/Auto/Sat 分母口径）；
    - ``numerator_seconds``：有依据且条件成立的秒数（已证实分子）；
    - ``unknown_seconds``：无法判定的秒数（槽级六因未知 + 窗口未覆盖），
      **不补时、不缩分母**；
    - ``known_seconds``：有状态依据的秒数（OBSERVED+HELD，含已知 BAD/手动）；
    - ``coverage_ratio``：known / denominator；
    - ``possible_low/high``：已证实比例与可能区间上界
      （C03-EA2：40% 已证实，范围 40%～60%）。
    """

    denominator_seconds: float
    numerator_seconds: float
    unknown_seconds: float
    known_seconds: float
    denominator_basis: str = DENOMINATOR_FULL_REQUEST_WINDOW

    @property
    def coverage_ratio(self) -> float | None:
        if self.denominator_seconds <= 0:
            return None
        return self.known_seconds / self.denominator_seconds

    @property
    def proven_ratio(self) -> float | None:
        """已证实比例（0~1）；分母为 0 → None（不可判定，不冒充 0）。"""
        if self.denominator_seconds <= 0:
            return None
        return self.numerator_seconds / self.denominator_seconds

    @property
    def possible_low(self) -> float | None:
        return self.proven_ratio

    @property
    def possible_high(self) -> float | None:
        if self.denominator_seconds <= 0:
            return None
        return (self.numerator_seconds + self.unknown_seconds) / self.denominator_seconds

    def to_details(self, *, prefix: str = "") -> dict[str, Any]:
        """契约字段名输出（unknownSeconds/knownSeconds/coverageRatio/
        numeratorSeconds/denominatorSeconds + 分母口径与可能区间）。"""
        return {
            f"{prefix}numeratorSeconds": round(self.numerator_seconds, 3),
            f"{prefix}denominatorSeconds": round(self.denominator_seconds, 3),
            f"{prefix}unknownSeconds": round(self.unknown_seconds, 3),
            f"{prefix}knownSeconds": round(self.known_seconds, 3),
            f"{prefix}coverageRatio": (
                round(self.coverage_ratio, 4) if self.coverage_ratio is not None else None
            ),
            f"{prefix}possibleRange": (
                [
                    (
                        round(self.possible_low * 100.0, 2)
                        if self.possible_low is not None
                        else None
                    ),
                    (
                        round(self.possible_high * 100.0, 2)
                        if self.possible_high is not None
                        else None
                    ),
                ]
                if self.denominator_seconds > 0
                else None
            ),
            f"{prefix}denominatorBasis": self.denominator_basis,
        }


@dataclass
class MetricTimeView:
    """指标时间视图（契约 §2.1 推荐接口责任）。

    由 :func:`build_metric_time_view` 产出：原槽索引、每槽有效区间/秒数、
    逐角色资格（三态+六因+数值资格）、连续片段（公共已知且数值有效、
    不跨分段边界——跨 gap 禁拼接的段目录）、覆盖摘要。
    """

    window_start: datetime  # 半开窗口起点（UTC aware，含）
    window_end: datetime  # 半开窗口终点（UTC aware，不含）
    grid_period_s: float
    #: 原槽索引（DataBlock 原始索引；视图不压缩、不重排——禁止拼接）
    indices: list[int]
    #: 每槽半开区间起点（与 indices 对齐）
    slot_starts: list[datetime]
    #: 每槽窗口内秒数（末槽截到 window_end；C04-EA1）
    slot_seconds: list[float]
    #: 窗口内数据未覆盖秒数（头/尾整段缺失；计入 unknown、单列可查）
    uncovered_window_seconds: float
    #: 逐角色三态（角色名小写；仅含视图构建时声明的角色）
    role_states: dict[str, list[SlotState]] = field(default_factory=dict)
    #: 逐角色未知归因（UNKNOWN 槽的六因；非 UNKNOWN 为 None）
    role_unknown_reasons: dict[str, list[str | None]] = field(default_factory=dict)
    #: 逐角色数值资格（validity[{role}_valid]；known-BAD = 有状态依据但不满足
    #: 数值资格——留在分母、不进分子，不当未知）
    role_numerically_valid: dict[str, list[bool]] = field(default_factory=dict)
    #: 视图构建所需角色（unknown 判定 = 任一所需角色 UNKNOWN）
    required_roles: tuple[str, ...] = ()
    #: 连续片段（原槽索引闭区间对）：所需角色全部已知+数值有效+不跨分段边界
    segments: list[tuple[int, int]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 窗口与覆盖摘要
    # ------------------------------------------------------------------

    @property
    def window_seconds(self) -> float:
        return (self.window_end - self.window_start).total_seconds()

    @property
    def covered_seconds(self) -> float:
        """窗口内被数据槽覆盖的秒数（≤ window_seconds）。"""
        return sum(self.slot_seconds)

    def roles_known(self, i: int, roles: tuple[str, ...] | None = None) -> bool:
        """槽 i 的指定角色（默认全部所需角色）均已知（OBSERVED/HELD）。"""
        check = roles if roles is not None else self.required_roles
        for role in check:
            states = self.role_states.get(role)
            if states is None or i >= len(states) or states[i] is SlotState.UNKNOWN:
                return False
        return True

    def role_valid(self, role: str, i: int) -> bool:
        """槽 i 角色 role 的数值资格（角色缺该槽 → False，不冒充有效）。"""
        arr = self.role_numerically_valid.get(role)
        if arr is None or i >= len(arr):
            return False
        return arr[i]

    def unknown_reason_breakdown(self) -> dict[str, float]:
        """未知归因的秒数分布（六因 + 窗口未覆盖单列）。"""
        out: dict[str, float] = {}
        if self.uncovered_window_seconds > 0:
            out[UNCOVERED_WINDOW] = round(self.uncovered_window_seconds, 3)
        for role in self.required_roles:
            states = self.role_states.get(role, [])
            reasons = self.role_unknown_reasons.get(role, [])
            for i, st in enumerate(states):
                if st is SlotState.UNKNOWN and i < len(reasons) and reasons[i]:
                    key = reasons[i]
                    out[key] = out.get(key, 0.0) + self._slot_sec(i)
        return {k: round(v, 3) for k, v in out.items() if v > 0}

    def _slot_sec(self, i: int) -> float:
        return self.slot_seconds[i] if i < len(self.slot_seconds) else 0.0

    def masked_slot_seconds(self, masked_indices: list[int]) -> list[float]:
        """按原始索引取槽秒数（与 masked 点对齐；索引不在窗口/无槽 → 0.0）。"""
        sec_by_orig = dict(zip(self.indices, self.slot_seconds, strict=False))
        return [sec_by_orig.get(i, 0.0) for i in masked_indices]

    # ------------------------------------------------------------------
    # 比率账（R/Auto/Sat 及带内类共用；契约 §2.1 比率规则）
    # ------------------------------------------------------------------

    def ratio_accounting(
        self,
        eligible: Callable[[int], bool],
        *,
        roles: tuple[str, ...] | None = None,
    ) -> RatioAccounting:
        """按契约计算比率账。

        Args:
            eligible: 槽级条件判定（调用方语义：模式/限位/偏差等），仅在槽的
                全部所需角色**已知**时被调用——未知槽不进判定（不补时）；
            roles: 覆盖 ``required_roles`` 的判定角色子集（默认全部所需角色）。

        槽分类（互斥）：
        - 任一所需角色 UNKNOWN → unknown_seconds（不判分子，不缩分母）；
        - 已知且 eligible → numerator_seconds；
        - 已知且不满足（含 known-BAD：数值资格不过/手动/未饱和不成立）→
          仅计 known_seconds。
        """
        check_roles = roles if roles is not None else self.required_roles
        numerator = 0.0
        unknown_slots = 0.0
        for i in range(len(self.indices)):
            sec = self._slot_sec(i)
            if sec <= 0:
                continue
            if any(
                (
                    (states := self.role_states.get(r)) is None
                    or i >= len(states)
                    or states[i] is SlotState.UNKNOWN
                )
                for r in check_roles
            ):
                unknown_slots += sec
            elif eligible(i):
                numerator += sec
        denominator = self.window_seconds
        unknown_total = unknown_slots + self.uncovered_window_seconds
        known = max(0.0, denominator - unknown_total)
        return RatioAccounting(
            denominator_seconds=denominator,
            numerator_seconds=numerator,
            unknown_seconds=unknown_total,
            known_seconds=known,
        )


def _runs_contain(runs: list[sc.IntervalRun], ts: datetime) -> sc.IntervalRun | None:
    """ts 所在的游程（runs 按 start 升序；半开语义；未命中 → None）。"""
    if not runs:
        return None
    starts = [r.start for r in runs]
    idx = bisect.bisect_right(starts, ts) - 1
    if idx < 0:
        return None
    r = runs[idx]
    return r if r.start <= ts < r.end else None


def _classify_coverage(
    coverage: sc.RoleCoverage | None,
    ts: datetime,
) -> tuple[bool, bool, str | None]:
    """按 RoleCoverage 游程分类槽 → (known, observed, unknown_reason)。

    - unknown 游程覆盖 → (False, False, 游程六因 reason)；
    - observed 游程覆盖 → (True, True, None)；
    - 其余 known（合法 HELD；旧上下文无 observed 细分时 observed=False，
      不影响"已知"判定——HELD 与 OBSERVED 都是有状态依据）；
    - 无覆盖信息（legacy）→ (True, False, None)：不判未知，保守沿用 known。
    """
    if coverage is None:
        return True, False, None
    run = _runs_contain(coverage.unknown, ts)
    if run is not None:
        return False, False, run.reason
    if coverage.observed and _runs_contain(coverage.observed, ts) is not None:
        return True, True, None
    if coverage.known and _runs_contain(coverage.known, ts) is not None:
        return True, False, None
    # known/unknown 游程都不含该槽（网格外）：按未知处理（无归因）
    return False, False, None


def _grid_period(bundle_block: Any) -> float:
    """网格周期（秒）：优先 series_context.grid_period_s，回退中位间隔→1s。"""
    ctx = getattr(bundle_block, "series_context", None)
    if ctx is not None and getattr(ctx, "grid_period_s", 0):
        return float(ctx.grid_period_s)
    ts = list(getattr(bundle_block, "timestamps", []) or [])
    if len(ts) >= 2:
        try:
            deltas = sorted((b - a).total_seconds() for a, b in zip(ts, ts[1:], strict=False))
            med = deltas[len(deltas) // 2]
            if med > 0:
                return float(med)
        except TypeError:
            pass
    return 1.0


def _resolve_window(
    bundle: MetricDataBundle, window: Any | None
) -> tuple[datetime, datetime] | None:
    """解析半开窗口：显式 window（含尾 TimeWindow/二元组）> DataBlock 查询窗
    （含尾）> series_context 含尾网格（+ 周期）。均不可得 → None（legacy 回退）。
    """
    if window is not None:
        if isinstance(window, TimeWindow):
            return to_half_open(window.start, window.end)
        try:
            s, e = window
            return to_half_open(s, e)
        except (TypeError, ValueError):
            return None
    block = bundle.data_block
    qs = getattr(block, "query_window_start", None)
    qe = getattr(block, "query_window_end", None)
    if qs is not None and qe is not None:
        return to_half_open(qs, qe)
    ctx = getattr(block, "series_context", None)
    if ctx is not None and getattr(ctx, "grid_start", None) is not None:
        # 含尾网格 → 半开：end = grid_end + 网格周期
        return _utc(ctx.grid_start), _utc(ctx.grid_end) + timedelta(seconds=_grid_period(block))
    return None


def build_metric_time_view(
    bundle: MetricDataBundle,
    window: Any | None = None,
    required_roles: tuple[str, ...] | None = None,
) -> MetricTimeView | None:
    """构建指标时间视图（契约 §2.1 推荐接口；08b O-2 唯一适配落点）.

    Args:
        bundle: 指标数据包（DataBlock 携带 timestamps/validity/series_context/
            query_window_*）；
        window: 显式窗口（含尾 ``TimeWindow`` 或 ``(start, end)`` 二元组）；
            None 时按 DataBlock 查询窗 → series_context 网格回退；
        required_roles: 该指标判定所需的信号角色（小写；未知判定=任一所需
            角色 UNKNOWN）。None 时取 block 信号中存在的核心角色。

    Returns:
        ``MetricTimeView``；窗口信息不可得（无查询窗且无 point 上下文，
        legacy/旧缓存/手工 bundle）时返回 ``None``——调用方回落 legacy 口径。
    """
    block = bundle.data_block
    half_open = _resolve_window(bundle, window)
    if half_open is None:
        return None
    w_start, w_end = half_open

    timestamps = list(block.timestamps)
    if not timestamps:
        return None
    period = _grid_period(block)
    roles = required_roles or tuple(r for r in ("pv", "sp", "op", "mode") if r in block.signals)

    # 每槽半开区间（窗口内交集；末槽截到 w_end——不越窗）
    indices: list[int] = []
    starts: list[datetime] = []
    seconds: list[float] = []
    for i, raw_ts in enumerate(timestamps):
        s = _utc(raw_ts)
        e = _utc(timestamps[i + 1]) if i + 1 < len(timestamps) else w_end
        e = min(e, w_end)
        s = max(s, w_start)
        sec = (e - s).total_seconds()
        if sec <= 0:
            continue
        indices.append(i)
        starts.append(s)
        seconds.append(max(0.0, sec))
    uncovered = max(0.0, (w_end - w_start).total_seconds() - sum(seconds))

    # 逐角色资格（三态 + 六因 + 数值资格）
    ctx = getattr(block, "series_context", None)
    coverage_map: dict[str, sc.RoleCoverage] = {}
    if ctx is not None:
        coverage_map = {str(r).lower(): cov for r, cov in (ctx.role_coverage or {}).items()}
    role_states: dict[str, list[SlotState]] = {}
    role_reasons: dict[str, list[str | None]] = {}
    role_valid: dict[str, list[bool]] = {}
    for role in roles:
        cov = coverage_map.get(role)
        sig = block.signals.get(role)
        valid_arr = block.validity.get(f"{role}_valid")
        states: list[SlotState] = []
        reasons: list[str | None] = []
        valids: list[bool] = []
        for _slot_i, orig_i in enumerate(indices):
            ts = _utc(timestamps[orig_i])
            value = sig[orig_i] if sig is not None and orig_i < len(sig) else None
            known, observed, unknown_reason = _classify_coverage(cov, ts)
            if known and value is None:
                # 覆盖已知但值缺失（防御口径）：无数值依据 → 未知
                known = False
                unknown_reason = unknown_reason or "no_value"
            if not known:
                states.append(SlotState.UNKNOWN)
                reasons.append(unknown_reason)
            elif observed:
                states.append(SlotState.OBSERVED)
                reasons.append(None)
            else:
                states.append(SlotState.HELD)
                reasons.append(None)
            has_valid = valid_arr is not None and orig_i < len(valid_arr)
            valids.append(bool(valid_arr[orig_i]) if has_valid else False)
        role_states[role] = states
        role_reasons[role] = reasons
        role_valid[role] = valids

    # 连续片段：所需角色全部已知 + 数值有效 + 不跨分段边界（跨 gap 禁拼接）
    boundaries: list[datetime] = []
    if ctx is not None:
        boundaries = sorted(_utc(b.at) for b in (ctx.segment_boundaries or []))

    def _boundary_between(prev_ts: datetime, cur_ts: datetime) -> bool:
        return any(prev_ts < b <= cur_ts for b in boundaries)

    segments: list[tuple[int, int]] = []
    seg_start: int | None = None
    for slot_i, orig_i in enumerate(indices):
        ok = all(
            role_states[r][slot_i] is not SlotState.UNKNOWN and role_valid[r][slot_i]
            for r in roles
            if slot_i < len(role_states.get(r, []))
        )
        cut_by_boundary = False
        if slot_i > 0:
            cut_by_boundary = _boundary_between(
                _utc(timestamps[indices[slot_i - 1]]), _utc(timestamps[orig_i])
            )
        if ok and not cut_by_boundary:
            if seg_start is None:
                seg_start = slot_i
        elif seg_start is not None:
            segments.append((seg_start, slot_i - 1))
            seg_start = None
    if seg_start is not None:
        segments.append((seg_start, len(indices) - 1))

    return MetricTimeView(
        window_start=w_start,
        window_end=w_end,
        grid_period_s=period,
        indices=indices,
        slot_starts=starts,
        slot_seconds=seconds,
        uncovered_window_seconds=uncovered,
        role_states=role_states,
        role_unknown_reasons=role_reasons,
        role_numerically_valid=role_valid,
        required_roles=tuple(roles),
        segments=segments,
    )


def _unknown_reason_at(
    ctx: sc.SeriesContext | None,
    cov: sc.RoleCoverage | None,
    ts: datetime,
) -> str | None:
    """槽级未知归因（六因；保留供网格级回退场景使用）。"""
    if cov is None:
        return None
    run = _runs_contain(cov.unknown, ts)
    return run.reason if run is not None else None


def view_or_none(bundle: MetricDataBundle, roles: tuple[str, ...]) -> MetricTimeView | None:
    """计算器侧便捷入口：构建失败/不可得时静默回落（None=legacy 口径）。"""
    try:
        return build_metric_time_view(bundle, None, roles)
    except Exception:  # noqa: BLE001 — 视图为增量口径，构建异常不得阻断指标
        logger.warning("build_metric_time_view 构建异常，回落 legacy 口径", exc_info=True)
        return None


#: 公共别名（预处理管道等消费方使用；实现见上文私有函数）
utc = _utc
classify_coverage = _classify_coverage


__all__ = [
    "DENOMINATOR_FULL_REQUEST_WINDOW",
    "MetricTimeView",
    "RatioAccounting",
    "SUPPORTED_GRID_PERIODS",
    "SlotState",
    "TIME_QUALITY_POLICY_VERSION",
    "UNCOVERED_WINDOW",
    "build_metric_time_view",
    "classify_coverage",
    "to_half_open",
    "utc",
    "view_or_none",
]
