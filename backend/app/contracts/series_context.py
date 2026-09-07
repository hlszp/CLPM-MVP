"""SeriesContext（AD01）：逻辑宽表数据的显式上下文契约.

设计依据：《算法与数据接口核查及兼容改进方案》§4.1（一个显式上下文，集中传递）。

定位与兼容规则：
- RawTimeSeries / DataBlock 以**默认 None 的可选字段**携带本上下文；
  None = 旧路径/旧数据/旧调用，语义完全维持 legacy；
- **point 路径必须有上下文，缺失即拒绝作为 point 计算**（方案 §4.1）；
- 上下文只承载 quality_codes（逐角色原质量）与 validity（逐点可计算性）
  无法表达的来源、覆盖原因和分段边界；禁止每派生组复制七套逐秒源时间
  （覆盖用区间/游程编码）。

序列化：to_dict/from_dict 与 L1/L2 缓存编解码共用；新增字段必须带默认值，
旧缓存 from_dict 不炸。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: 上下文 schema 版本（结构变化时递增；缓存键组成部分）
SERIES_CONTEXT_SCHEMA_VERSION = 1

#: 布局语义
LAYOUT_LEGACY = "legacy"
LAYOUT_POINT = "point"
LAYOUT_MIXED = "mixed"

#: 数据策略版本：网格/覆盖/未知语义规则的版本（区别于算法版本）
DATA_POLICY_VERSION = "dpv1"

#: 未知原因类别（unknown 槽的归因；方案 §4.1"覆盖与状态原因"）
UNKNOWN_REASON_GAP = "gap"  # 会话断线/队列满登记的硬缺口
UNKNOWN_REASON_NO_INITIAL = "no_initial"  # 窗口前无初值（首事件前）
UNKNOWN_REASON_CONFLICT = "conflict"  # 同 ts 冲突按不确定处理
UNKNOWN_REASON_UNBOUND = "unbound"  # 该时刻无绑定（未知过去/改绑空窗）
UNKNOWN_REASON_REBIND = "rebind"  # 改绑后新点无锚点


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class IntervalRun:
    """区间游程（半开 [start, end)，aware UTC；序列化友好）。"""

    start: datetime
    end: datetime

    def to_dict(self) -> dict[str, str]:
        return {"s": _utc(self.start).isoformat(), "e": _utc(self.end).isoformat()}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> IntervalRun:
        return cls(
            start=datetime.fromisoformat(data["s"]),
            end=datetime.fromisoformat(data["e"]),
        )


@dataclass
class RoleCoverage:
    """单角色的已知覆盖/未知区间（游程编码；网格内）.

    known：有状态依据的槽（OBSERVED 新观测 + HELD 正常 COV 保持）；
    unknown：无依据槽（原因见 unknown_reasons 归因计数，网格级）。
    """

    known: list[IntervalRun] = field(default_factory=list)
    unknown: list[IntervalRun] = field(default_factory=list)

    def known_slots(self, grid_start: datetime, grid_end: datetime) -> int:
        """已知覆盖槽数（与 [grid_start, grid_end] 整秒网格交集）。"""
        return _count_grid_slots(self.known, grid_start, grid_end)

    def unknown_slots(self, grid_start: datetime, grid_end: datetime) -> int:
        return _count_grid_slots(self.unknown, grid_start, grid_end)

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {
            "known": [r.to_dict() for r in self.known],
            "unknown": [r.to_dict() for r in self.unknown],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoleCoverage:
        return cls(
            known=[IntervalRun.from_dict(r) for r in data.get("known", [])],
            unknown=[IntervalRun.from_dict(r) for r in data.get("unknown", [])],
        )


@dataclass
class SegmentBoundary:
    """分段边界：改绑 / 量程单位解释变化 / 布局切换（方案 §4.1"分段边界"）.

    边界不等于坏质量——不得用伪造一个坏点来表示；跨边界不能无依据拼接
    动态输入（I07）。
    """

    at: datetime  # 边界生效时刻（aware UTC；该时刻起新段）
    kind: str  # rebind / interpretation_change / layout_switch
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"at": _utc(self.at).isoformat(), "kind": self.kind, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SegmentBoundary:
        return cls(
            at=datetime.fromisoformat(data["at"]),
            kind=data["kind"],
            detail=data.get("detail", {}),
        )


def _count_grid_slots(runs: list[IntervalRun], grid_start: datetime, grid_end: datetime) -> int:
    """区间游程与 [grid_start, grid_end] 整秒网格的交集槽数（去重）。"""
    gs, ge = _utc(grid_start), _utc(grid_end)
    merged: list[tuple[datetime, datetime]] = []
    for r in sorted(runs, key=lambda x: _utc(x.start)):
        s, e = _utc(r.start), _utc(r.end)
        s = max(s, gs)
        e = min(e, ge + _slot_unit())
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    total = 0
    for s, e in merged:
        total += _slots_between(s, e)
    return total


def _slot_unit():
    from datetime import timedelta

    return timedelta(seconds=1)


def _slots_between(s: datetime, e: datetime) -> int:
    """[s, e) 内整秒网格槽数（对齐到整秒边界；不足一槽按包含的整秒计）。"""
    from datetime import timedelta

    s2 = s.replace(microsecond=0)
    if s.microsecond:
        s2 += timedelta(seconds=1)
    e2 = e.replace(microsecond=0)
    n = int((e2 - s2).total_seconds())
    return max(0, n)


@dataclass
class SeriesContext:
    """逻辑宽表上下文（完整字段见方案 §4.1 表）."""

    layout: str  # legacy / point / mixed
    # 网格（§4.2）：查询窗、UTC 边界、周期、期望格点数
    grid_start: datetime  # 含（UTC 整秒）
    grid_end: datetime  # 含（UTC 整秒）
    grid_period_s: float = 1.0
    expected_slots: int = 0
    # 数据身份（缓存/在途校验/结果复算；方案 §4.1"数据身份"）
    dataset_ref: str = ""  # 布局+绑定+解释配置+数据/覆盖 revision 摘要
    binding_version: str | None = None
    interpretation_version: str | None = None
    data_revision: str | None = None
    coverage_revision: str | None = None
    # 覆盖与状态原因（按角色游程 + 网格级未知归因计数）
    role_coverage: dict[str, RoleCoverage] = field(default_factory=dict)
    unknown_reasons: dict[str, int] = field(default_factory=dict)
    # 分段边界（改绑/解释变化/布局切换）
    segment_boundaries: list[SegmentBoundary] = field(default_factory=list)
    # 窗口内解释配置是否一致（False = 跨量程/单位/绑定段，动态输入须分段/拒绝）
    interpretation_consistent: bool = True
    schema_version: int = SERIES_CONTEXT_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # 派生口径（§4.2）
    # ------------------------------------------------------------------

    @property
    def is_point(self) -> bool:
        return self.layout == LAYOUT_POINT

    def unknown_slots(self, role: str) -> int:
        cov = self.role_coverage.get(role)
        if cov is None:
            return 0
        return cov.unknown_slots(self.grid_start, self.grid_end)

    def known_slots(self, role: str) -> int:
        cov = self.role_coverage.get(role)
        if cov is None:
            return self.expected_slots
        return cov.known_slots(self.grid_start, self.grid_end)

    def source_coverage_ratio(self, role: str = "pv") -> float:
        """来源覆盖 = 已知覆盖槽数 / N（正常 COV 保持计入；断线/无初值/冲突不计）."""
        if self.expected_slots <= 0:
            return 0.0
        return min(self.known_slots(role) / self.expected_slots, 1.0)

    def has_boundary_in_window(self, kinds: tuple[str, ...] | None = None) -> bool:
        gs, ge = self.grid_start, self.grid_end
        for b in self.segment_boundaries:
            if kinds and b.kind not in kinds:
                continue
            if gs < _utc(b.at) <= ge:
                return True
        return False

    # ------------------------------------------------------------------
    # 序列化（L1/L2 共用；新增字段必须带默认值）
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "layout": self.layout,
            "grid_start": _utc(self.grid_start).isoformat(),
            "grid_end": _utc(self.grid_end).isoformat(),
            "grid_period_s": self.grid_period_s,
            "expected_slots": self.expected_slots,
            "dataset_ref": self.dataset_ref,
            "binding_version": self.binding_version,
            "interpretation_version": self.interpretation_version,
            "data_revision": self.data_revision,
            "coverage_revision": self.coverage_revision,
            "role_coverage": {k: v.to_dict() for k, v in self.role_coverage.items()},
            "unknown_reasons": dict(self.unknown_reasons),
            "segment_boundaries": [b.to_dict() for b in self.segment_boundaries],
            "interpretation_consistent": self.interpretation_consistent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeriesContext:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            layout=data["layout"],
            grid_start=datetime.fromisoformat(data["grid_start"]),
            grid_end=datetime.fromisoformat(data["grid_end"]),
            grid_period_s=float(data.get("grid_period_s", 1.0)),
            expected_slots=int(data.get("expected_slots", 0)),
            dataset_ref=data.get("dataset_ref", ""),
            binding_version=data.get("binding_version"),
            interpretation_version=data.get("interpretation_version"),
            data_revision=data.get("data_revision"),
            coverage_revision=data.get("coverage_revision"),
            role_coverage={
                k: RoleCoverage.from_dict(v) for k, v in data.get("role_coverage", {}).items()
            },
            unknown_reasons=dict(data.get("unknown_reasons", {})),
            segment_boundaries=[
                SegmentBoundary.from_dict(b) for b in data.get("segment_boundaries", [])
            ],
            interpretation_consistent=bool(data.get("interpretation_consistent", True)),
        )


def data_version_for_cache(context: SeriesContext | None) -> str:
    """缓存键的数据版本分量：无上下文=legacy-v1（旧键兼容）；有上下文=布局+身份."""
    if context is None:
        return "legacy-v1"
    return f"{context.layout}:{context.dataset_ref or 'noid'}:dpv{DATA_POLICY_VERSION}"
