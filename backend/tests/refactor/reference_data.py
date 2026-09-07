"""P0-4 独立密集参考数据生成器（测点子表重构契约基线）.

职责与红线（整改计划 P0-4 / 设计 §8）：
- 生成**多类回路 × 七角色 × 1 秒密集**的真值网格，含 SP/MODE/PID 阶跃与
  足够动态激励（PV/OP 逐秒变化），供算法等价性差分使用（V01/V02/V15）；
- 另生成仅用于缺口/坏质量/无初值/改绑的反例数据（V03~V10）；
- **独立于生产 builder**：参考 RawTimeSeries 由本模块直接从密集真值构造
  （平凡因果保持），绝不调用待测的 logical_wide_builder /
  point_history_repository 充当自身预期（计划 §5.2-1）；
- 确定性：固定 ``random.Random(seed)``，同一 seed 生成完全一致的字节级输出。

质量体系（设计 §4.1/§5.4）：
- 真值网格用 ``quality_class`` 三态（1=GOOD / 0=BAD / -1=UNKNOWN）；
- COV 事件流携带 ``quality_raw``（AAS 枚举原码 0/1/2/3）与
  ``quality_schema``（1=AAS 枚举 / 2=OPC DA 位编码），由消费侧按体系解码，
  本模块不把两个体系的原码混用。

时间口径（设计 §5.3）：
- 网格固定 UTC 整秒；事件 ts 为 epoch 毫秒（默认秒对齐，反例可含毫秒偏移）；
- 所有 datetime 为 naive UTC（与 RawTimeSeries 下游既有表达一致）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 七角色固定顺序（与 loop_tag_mapping.tag_role / 宽表列一致）
ROLES: tuple[str, ...] = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")

#: 质量三态（设计 §4.1：本层固定三态，细节在原码）
QC_GOOD = 1
QC_BAD = 0
QC_UNKNOWN = -1

#: 质量原码体系（设计 §4.1 quality_schema）
QSCHEMA_AAS = 1  # AAS 枚举：0=Bad 1=Good 2=Uncertain 3=离线（语义按 §4.1 以解码层确认登记为准）
QSCHEMA_OPCDA = 2  # OPC DA 位编码：192=Good(0xC0) 等

#: 来源类别（设计 §4.1 source_kind）
SOURCE_KIND_COV = 1  # 实时变化
SOURCE_KIND_SNAPSHOT = 2  # 首次/恢复快照
SOURCE_KIND_HISTORY = 3  # 确认的原始历史事件

_UTC = UTC


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefPoint:
    """参考测点：稳定点身份（tag_registry.id 口径的 UUID）。"""

    point_id: str  # UUID（去连字符小写，与子表名 p_<id> 对齐）
    tag_name: str  # 位号（如 REF1_PV）
    quality_schema: int = QSCHEMA_AAS


@dataclass
class RefEvent:
    """单测点一次状态事件（值或质量变化）。"""

    ts_ms: int  # epoch 毫秒
    value: float | int | None
    quality_class: int  # 1/0/-1
    quality_raw: int | None
    source_kind: int = SOURCE_KIND_COV


@dataclass
class RefLoop:
    """参考回路元数据（PG 口径）。"""

    loop_id: str  # UUID
    tag_name: str  # 回路位号（loop_part，d_loop_* 子表名派生源）
    unit_id: str
    control_type: str  # FC/PC/TC/LC
    range_min: float
    range_max: float
    points: dict[str, RefPoint]  # role → point
    # 改绑场景：role → [(valid_from_s, point_id)]；简单场景由 points 全程生效
    rebinding: dict[str, list[tuple[int, str]]] = field(default_factory=dict)


@dataclass
class ReferenceDataset:
    """一套完整参考数据集。"""

    start_s: int  # epoch 秒（含）
    end_s: int  # epoch 秒（含）
    loops: dict[str, RefLoop] = field(default_factory=dict)  # loop_id → meta
    # 密集真值：loop_id → role → [ (ts_s, value, quality_class) ... ]
    # 时间轴为 [start_s, end_s] 的全部 UTC 整秒，长度恒等于 end_s-start_s+1
    dense: dict[str, dict[str, list[tuple[int, Any, int]]]] = field(default_factory=dict)
    # COV 事件流：point_id → 事件列表（升序）
    events: dict[str, list[RefEvent]] = field(default_factory=dict)
    # 已知缺口/未知窗口登记：loop_id → [(start_s, end_s)]（该窗口真值即 UNKNOWN）
    unknown_windows: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # 改绑产生的新点（不在 points 角色键内）：point_id → RefPoint
    extra_points: dict[str, RefPoint] = field(default_factory=dict)

    def loop(self, loop_id: str) -> RefLoop:
        return self.loops[loop_id]

    def grid_seconds(self) -> list[int]:
        return list(range(self.start_s, self.end_s + 1))

    def grid_datetimes(self) -> list[datetime]:
        base = datetime.fromtimestamp(self.start_s, _UTC).replace(tzinfo=None)
        return [base + timedelta(seconds=i) for i in range(self.end_s - self.start_s + 1)]


# ---------------------------------------------------------------------------
# 信号合成（确定性）
# ---------------------------------------------------------------------------


def _pid_response(
    rng: random.Random,
    n: int,
    sp_steps: dict[int, float],
    base_pv: float,
    gain: float,
    tau_s: float,
    noise: float,
) -> list[float]:
    """一阶惯性 + 噪声的 PV 序列（对 SP 阶跃的因果响应，不看未来）."""
    pv = base_pv
    out: list[float] = []
    sp_series: list[float] = []
    sp = next(iter(sp_steps.values())) if sp_steps else base_pv
    for i in range(n):
        t_at = next((v for tt, v in sorted(sp_steps.items()) if tt <= i), sp)
        sp = t_at
        sp_series.append(sp)
        # 一阶惯性追踪 SP（OP 反向调节的简化替代；仅作激励源，不模拟真实控制回路）
        pv += (sp - pv) * min(1.0, 1.0 / tau_s)
        pv += rng.gauss(0.0, noise)
        out.append(pv)
    return out


def _cov_encode(
    series: list[tuple[int, Any, int]],
    *,
    quality_raw_of: Any,
    first_source_kind: int = SOURCE_KIND_SNAPSHOT,
) -> list[RefEvent]:
    """把 1s 密集序列编码为 COV 事件流（值或质量变化才发事件）.

    首个事件标记为 snapshot（初始状态），后续为 COV。质量类到原码的映射由
    ``quality_raw_of(quality_class, i)`` 提供（同一体系内映射）。
    """
    events: list[RefEvent] = []
    last_v: Any = object()
    last_q: int | None = None
    for i, (ts_s, v, q) in enumerate(series):
        v_changed = v != last_v
        q_changed = q != last_q
        if v_changed or q_changed:
            events.append(
                RefEvent(
                    ts_ms=ts_s * 1000,
                    value=v,
                    quality_class=q,
                    quality_raw=quality_raw_of(q, i),
                    source_kind=(first_source_kind if not events else SOURCE_KIND_COV),
                )
            )
            last_v, last_q = v, q
    return events


def _quality_raw_aas(qc: int, _i: int = 0) -> int:
    """AAS 枚举原码：Good=1 / Bad=0 / Uncertain=2（UNKNOWN 无原码 → None）."""
    if qc == QC_GOOD:
        return 1
    if qc == QC_BAD:
        return 0
    if qc == QC_UNKNOWN:
        return None
    raise ValueError(qc)


# ---------------------------------------------------------------------------
# 主生成入口
# ---------------------------------------------------------------------------


def _fixed_uuid(tag: str, salt: int = 0) -> str:
    """确定性 UUID（带连字符的标准 36 字符形式，与 PG UUID 列兼容）.

    测点子表命名（设计 §4.1 ``p_<去连字符小写 UUID>``）在子表名派生处
    再 strip("-")，点身份本身保持标准形式。
    """
    import hashlib

    digest = hashlib.sha1(f"clpm-refactor:{salt}:{tag}".encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def build_reference_dataset(
    *,
    hours: float = 2.0,
    seed: int = 20260906,
    start_s: int | None = None,
) -> ReferenceDataset:
    """生成主参考数据集：4 回路（FC/PC/TC/LC）× 七角色 × 全覆盖 Good 质量.

    特性（V01/V02/V15 主用例）：
    - PV/OP 逐秒变化（噪声 + 阶跃响应），SP/MODE/PID 少量阶跃；
    - 全窗口有初值（窗口前事件，事件流含 t0 前一拍的初始状态）；
    - PV 质量 Good 全程；PID 参数窗口前已存在并在中途阶跃一次；
    - loop_c（TC）为共享点场景：PID_P/I/D 与 loop_b（PC）共享同一组 PID 测点；
    - loop_d（LC）为纯常值反例（V15：冻结/准入门禁），全程无激励。
    """
    rng = random.Random(seed)
    n = int(hours * 3600)
    if start_s is None:
        # 固定锚点：2026-09-06 00:00:00 UTC（可复现）
        start_s = int(datetime(2026, 9, 6, tzinfo=_UTC).timestamp())
    end_s = start_s + n - 1
    ds = ReferenceDataset(start_s=start_s, end_s=end_s)

    loop_specs = [
        ("REF-FIC-101", "FC", 0.0, 100.0, _fixed_uuid("unit:ref-1")),
        ("REF-PIC-201", "PC", 0.0, 2.5, _fixed_uuid("unit:ref-1")),
        ("REF-TIC-301", "TC", 0.0, 400.0, _fixed_uuid("unit:ref-2")),
        ("REF-LIC-401", "LC", 0.0, 100.0, _fixed_uuid("unit:ref-2")),
    ]
    # 共享点场景（V09）：TIC-301 的 PID 三角色与 PIC-201 共用同一组物理测点。
    # 共享点事件只写一份（由首个引用方 PIC-201 写入，两回路 PID 序列刻意相同）。
    shared_pid_points: dict[str, RefPoint] = {
        role: RefPoint(_fixed_uuid(f"shared:{role}"), f"REF_SHARED_{role}")
        for role in ("PID_P", "PID_I", "PID_D")
    }  # noqa: C416
    shared_written_ids: set[str] = set()

    for tag_name, ctype, rmin, rmax, unit in loop_specs:
        loop_id = _fixed_uuid(f"loop:{tag_name}")
        span = rmax - rmin
        base = rmin + span * 0.5
        is_constant = tag_name == "REF-LIC-401"

        points: dict[str, RefPoint] = {
            role: (
                shared_pid_points[role]
                if role in shared_pid_points and tag_name in ("REF-PIC-201", "REF-TIC-301")
                else RefPoint(
                    _fixed_uuid(f"{tag_name}:{role}"),
                    f"{tag_name.replace('-', '_')}_{role}",
                )
            )
            for role in ROLES
        }
        ds.loops[loop_id] = RefLoop(
            loop_id=loop_id,
            tag_name=tag_name,
            unit_id=unit,
            control_type=ctype,
            range_min=rmin,
            range_max=rmax,
            points=points,
        )

        grid = ds.grid_seconds()
        if is_constant:
            # 纯常值：PV=SP=base，OP 恒定，MODE=1，PID 恒定（V15 反例）
            pv = [(t, round(base, 6), QC_GOOD) for t in grid]
            sp = pv
            op = [(t, round(base * 0.8, 6), QC_GOOD) for t in grid]
            mode = [(t, 1, QC_GOOD) for t in grid]
            pidp = [(t, 0.8, QC_GOOD) for t in grid]
            pidi = [(t, 0.2, QC_GOOD) for t in grid]
            pidd = [(t, 0.05, QC_GOOD) for t in grid]
        else:
            # 阶跃时刻（秒偏移）：SP 两次阶跃 + MODE 手/自动切换
            sp_step_at = {0: base, n // 3: base + span * 0.1, (2 * n) // 3: base - span * 0.05}
            pv_series = _pid_response(rng, n, sp_step_at, base, 1.0, tau_s=45.0, noise=span * 0.004)
            pv = [(grid[i], round(v, 6), QC_GOOD) for i, v in enumerate(pv_series)]
            sp_map = dict(sp_step_at)
            sp_vals: list[float] = []
            cur = base
            for i in range(n):
                cur = sp_map.get(i, cur)
                sp_vals.append(cur)
            sp = [(grid[i], round(v, 6), QC_GOOD) for i, v in enumerate(sp_vals)]
            # OP：与 PV 偏离反向的调节量 + 噪声
            op = [
                (
                    grid[i],
                    round(
                        min(
                            rmax,
                            max(
                                rmin,
                                base
                                - (pv_series[i] - sp_vals[i]) * 2.0
                                + rng.gauss(0, span * 0.003),
                            ),
                        ),
                        6,
                    ),
                    QC_GOOD,
                )
                for i in range(n)
            ]
            mode = [
                (t, (0 if n // 4 <= (t - start_s) < n // 4 + 600 else 1), QC_GOOD) for t in grid
            ]
            pidp = [(t, (0.6 if (t - start_s) < n // 2 else 0.9), QC_GOOD) for t in grid]
            pidi = [(t, (0.15 if (t - start_s) < n // 2 else 0.25), QC_GOOD) for t in grid]
            pidd = [(t, 0.05, QC_GOOD) for t in grid]

        dense = {
            "PV": pv,
            "SP": sp,
            "OP": op,
            "MODE": mode,
            "PID_P": pidp,
            "PID_I": pidi,
            "PID_D": pidd,
        }
        ds.dense[loop_id] = dense

        # COV 编码（含窗口前初值事件：t0-1s 的初始状态，保证窗口有初值）
        pre = start_s - 1
        for role, series in dense.items():
            first = series[0]
            seed_state = (pre, first[1], first[2])
            encoded = _cov_encode([seed_state, *series], quality_raw_of=_quality_raw_aas)
            encoded[0].source_kind = SOURCE_KIND_SNAPSHOT
            point = ds.loops[loop_id].points[role]
            is_shared = point.point_id in {p.point_id for p in shared_pid_points.values()}
            if point.point_id in shared_written_ids:
                continue  # 共享点只写一份（按 point_id 判重，不按角色）
            ds.events.setdefault(point.point_id, []).extend(encoded)
            if is_shared:
                shared_written_ids.add(point.point_id)

    return ds


def _dedupe_events(events: list[RefEvent]) -> list[RefEvent]:
    """同 ts 同 payload 去重（共享点两路写入的幂等合并）."""
    seen: dict[int, RefEvent] = {}
    for e in sorted(events, key=lambda x: x.ts_ms):
        key = e.ts_ms
        if key in seen and (
            seen[key].value == e.value and seen[key].quality_class == e.quality_class
        ):
            continue
        seen[key] = e
    return list(seen.values())


# ---------------------------------------------------------------------------
# 反例数据（缺口 / 坏质量 / 无初值 / 质量变化 / 改绑）
# ---------------------------------------------------------------------------


def build_counterexamples(seed: int = 20260906) -> ReferenceDataset:
    """反例数据集（V03~V10）。单回路 REF-XIC-901，七角色.

    场景（时间窗 [start, start+3600) 内，秒偏移）：
    - [0, 300) 无任何事件（无窗口前初值 + 首事件晚于窗口开始 → V03 前段 UNKNOWN）；
    - [600, 900) 断线缺口（前一状态 Good，恢复快照在 900s 到达，值同旧值 →
      该窗口真值 UNKNOWN，V06：不得被快照填平）；
    - [1200, 1500) PV 坏质量（值不变，质量 Good→Bad→Uncertain→Good 中的
      Bad 段与 Uncertain 段分开：1200~1350 Bad，1350~1500 Uncertain → V04/V05）；
    - [1800, 2100) OP 无初值（OP 首事件在 2100s → V03）；
    - t=2400s 回路 PV 改绑到新测点（新点无 anchor → V09：改绑后未知直至新点事件）。
    """
    rng = random.Random(seed + 1)
    start_s = int(datetime(2026, 9, 7, tzinfo=_UTC).timestamp())
    n = 3600
    end_s = start_s + n - 1
    ds = ReferenceDataset(start_s=start_s, end_s=end_s)

    loop_id = _fixed_uuid("loop:REF-XIC-901")
    old_pv_point = RefPoint(_fixed_uuid("REF-XIC-901:PV:old"), "REF_XIC_901_PV")
    new_pv_point = RefPoint(_fixed_uuid("REF-XIC-901:PV:new"), "REF_XIC_901_PV_NEW")
    points = {
        "PV": old_pv_point,
        "SP": RefPoint(_fixed_uuid("REF-XIC-901:SP"), "REF_XIC_901_SP"),
        "OP": RefPoint(_fixed_uuid("REF-XIC-901:OP"), "REF_XIC_901_OP"),
        "MODE": RefPoint(_fixed_uuid("REF-XIC-901:MODE"), "REF_XIC_901_MODE"),
        "PID_P": RefPoint(_fixed_uuid("REF-XIC-901:PID_P"), "REF_XIC_901_PID_P"),
        "PID_I": RefPoint(_fixed_uuid("REF-XIC-901:PID_I"), "REF_XIC_901_PID_I"),
        "PID_D": RefPoint(_fixed_uuid("REF-XIC-901:PID_D"), "REF_XIC_901_PID_D"),
    }
    rebind_at = 2400
    ds.loops[loop_id] = RefLoop(
        loop_id=loop_id,
        tag_name="REF-XIC-901",
        unit_id=_fixed_uuid("unit:ref-x"),
        control_type="FC",
        range_min=0.0,
        range_max=100.0,
        points=points,
        rebinding={
            "PV": [
                (start_s, old_pv_point.point_id),
                (start_s + rebind_at, new_pv_point.point_id),
            ]
        },
    )
    ds.extra_points[new_pv_point.point_id] = new_pv_point

    grid = ds.grid_seconds()

    def _dense_unknown(t: int, role: str = "PV") -> tuple[int, None, int]:
        return (t, None, QC_UNKNOWN)

    # PV 真值：300s 前未知；300~600 Good 波动；600~900 未知（断线）；
    # 900~1200 Good（恢复，含旧常值续持）；1200~1350 BAD（值同前）；1350~1500
    # UNKNOWN（Uncertain 段按三态落 UNKNOWN，原码 2 保留在事件流）；1500~2400 Good；
    # 2400 后未知（改绑，新点无事件直至 2700s 首事件）
    pv: list[tuple[int, Any, int]] = []
    base_pv = 55.0
    for t in grid:
        off = t - start_s
        if off < 300:
            pv.append(_dense_unknown(t))
        elif off < 600:
            pv.append((t, round(base_pv + rng.gauss(0, 0.3), 6), QC_GOOD))
        elif off < 900:
            pv.append(_dense_unknown(t))
        elif off < 1200:
            pv.append((t, round(base_pv + rng.gauss(0, 0.3), 6), QC_GOOD))
        elif off < 1350:
            pv.append((t, 52.0, QC_BAD))  # 值不变质量坏（V04：不得沿用旧 Good）
        elif off < 1500:
            pv.append((t, 52.0, QC_UNKNOWN))  # Uncertain → 三态 UNKNOWN（V05）
        elif off < 2400:
            pv.append((t, round(52.0 + rng.gauss(0, 0.3), 6), QC_GOOD))
        elif off < 2700:
            pv.append(_dense_unknown(t))  # 改绑后新点无 anchor（V09）
        else:
            pv.append((t, round(61.0 + rng.gauss(0, 0.3), 6), QC_GOOD))

    # SP：全程已知（窗口前初值 50.0），中途阶跃到 55.0
    sp = [(t, (50.0 if (t - start_s) < 1800 else 55.0), QC_GOOD) for t in grid]
    # OP：2100s 前无事件（无初值 → UNKNOWN），此后 Good 波动
    op: list[tuple[int, Any, int]] = []
    for t in grid:
        off = t - start_s
        if off < 2100:
            op.append(_dense_unknown(t, "OP"))
        else:
            op.append((t, round(48.0 + rng.gauss(0, 0.4), 6), QC_GOOD))
    mode = [(t, 1, QC_GOOD) for t in grid]
    pidp = [(t, 0.7, QC_GOOD) for t in grid]
    pidi = [(t, 0.18, QC_GOOD) for t in grid]
    pidd = [(t, 0.05, QC_GOOD) for t in grid]

    ds.dense[loop_id] = {
        "PV": pv,
        "SP": sp,
        "OP": op,
        "MODE": mode,
        "PID_P": pidp,
        "PID_I": pidi,
        "PID_D": pidd,
    }
    ds.unknown_windows[loop_id] = [(0, 299), (600, 899), (2400, 2699)]

    # 事件流：真值逐秒编码，但按场景裁剪——
    # - 断线窗口（600~900）：完全不发事件（恢复时刻 900 发 snapshot，
    #   source_kind=SNAPSHOT，值为 900s 起的真值——快照只证明恢复时刻状态）；
    # - 无初值段（PV<300 / OP<2100）：无窗口前事件、窗口内无事件；
    # - 改绑：旧 PV 点事件止于 2399s；新 PV 点自 2700s 起；
    # - 质量变化（1200 Bad / 1350 Uncertain）：值不变也发质量事件（V05）。
    def _raw(qc: int, i: int) -> int | None:  # noqa: ARG001
        return _quality_raw_aas(qc, i)

    # PV 事件（按旧点/新点分段）
    old_pv_events: list[RefEvent] = []
    new_pv_events: list[RefEvent] = []
    last_sig: tuple[Any, int] | None = None
    for i, (t, v, q) in enumerate(pv):
        off = t - start_s
        if v is None and q == QC_UNKNOWN:
            continue  # 未知（无观测）不发事件
        if 600 <= off < 900:
            continue  # 断线窗口无事件
        if off == 900:
            old_pv_events.append(RefEvent(t * 1000, v, q, _raw(q, i), SOURCE_KIND_SNAPSHOT))
            last_sig = (v, q)
            continue
        sig = (v, q)
        if sig != last_sig:
            ev = RefEvent(t * 1000, v, q, _raw(q, i), SOURCE_KIND_COV)
            if off < 2400:
                old_pv_events.append(ev)
            elif off >= 2700:
                new_pv_events.append(ev)
            # 2400~2700：新点无事件（真值 UNKNOWN）
            last_sig = sig
    ds.events[old_pv_point.point_id] = old_pv_events
    ds.events[new_pv_point.point_id] = new_pv_events

    # 其余角色：窗口前初值 + COV
    other_roles = (("SP", sp), ("MODE", mode), ("PID_P", pidp), ("PID_I", pidi), ("PID_D", pidd))
    for role, series in other_roles:
        pre = (start_s - 1, series[0][1], QC_GOOD)
        ds.events[points[role].point_id] = _cov_encode([pre, *series], quality_raw_of=_raw)
    # OP：无窗口前初值；2100s 首事件为 snapshot
    op_events: list[RefEvent] = []
    last_sig = None
    for i, (t, v, q) in enumerate(op):
        if v is None:
            continue
        sig = (v, q)
        if sig != last_sig:
            op_events.append(
                RefEvent(
                    t * 1000,
                    v,
                    q,
                    _raw(q, i),
                    SOURCE_KIND_SNAPSHOT if not op_events else SOURCE_KIND_COV,
                )
            )
            last_sig = sig
    ds.events[points["OP"].point_id] = op_events

    return ds


# ---------------------------------------------------------------------------
# 独立参考 RawTimeSeries 构造器（等价性差分的预期侧）
# ---------------------------------------------------------------------------


def build_reference_raw_series(
    ds: ReferenceDataset,
    loop_id: str,
    roles: list[str],
    start: datetime,
    end: datetime,
    interval_s: int = 1,
) -> Any:
    """从密集真值直接构造参考 RawTimeSeries（平凡因果保持，非生产 builder）.

    规则（与设计 §5.2 对齐，作为**预期**固定）：
    - 网格 = [start, end] 全部 UTC 整秒（含两端）；
    - 每角色每 t 取该秒真值（dense 网格本身即因果保持后的状态）；
    - signals 键为角色小写；quality_codes 仅 PV 输出 ``pv_quality``（当前
      Provider 契约：非 PV 角色无质量键）；
    - 未知真值输出 None 值 + pv_quality=-1（PV 角色）。

    interval_s 仅参与网格步长（v1 恒 1s；保留参数对齐 QueryFn 契约）。
    """
    from app.contracts.data_types import RawTimeSeries  # noqa: PLC0415 — 契约结构允许

    if interval_s != 1:
        raise NotImplementedError("参考实现仅支持 1s 网格（v1 契约）")
    # naive 一律按 UTC 解释（禁止 naive.timestamp()——它按进程本地时区解释）
    start_s = int(
        start.replace(tzinfo=_UTC).timestamp() if start.tzinfo is None else start.timestamp()
    )
    end_s = int(end.replace(tzinfo=_UTC).timestamp() if end.tzinfo is None else end.timestamp())
    if start_s < ds.start_s or end_s > ds.end_s:
        raise ValueError(f"查询窗 [{start_s},{end_s}] 超出参考数据范围 [{ds.start_s},{ds.end_s}]")

    dense = ds.dense[loop_id]
    grid = ds.grid_seconds()
    idx = {t: i for i, t in enumerate(grid)}
    timestamps: list[datetime] = []
    signals: dict[str, list[Any]] = {r.lower(): [] for r in roles}
    pv_quality: list[int] = []

    t = start_s
    base = datetime.fromtimestamp(ds.start_s, _UTC).replace(tzinfo=None)
    while t <= end_s:
        i = idx[t]
        timestamps.append(base + timedelta(seconds=(t - ds.start_s)))
        for r in roles:
            v, q = dense[r][i][1], dense[r][i][2]
            signals[r.lower()].append(v)
            if r.upper() == "PV":
                pv_quality.append(q)
        t += interval_s

    quality_codes: dict[str, list[int]] = {}
    if "PV" in [r.upper() for r in roles]:
        quality_codes["pv_quality"] = pv_quality
    return RawTimeSeries(
        timestamps=timestamps,
        signals=signals,
        quality_codes=quality_codes,
    )


def wide_rows_from_dense(
    ds: ReferenceDataset,
    loop_id: str,
) -> list[tuple]:
    """密集真值 → legacy 宽表行（st_loop_data 列序，+8 墙钟 ts 串）.

    用于把参考数据种进隔离 TDengine 的 legacy 布局（P0-6 基线测量、P3 差分）。
    行格式与 realtime_subscriber._build_row / data_import._build_wide_row 一致：
    (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)。
    """
    from datetime import timedelta as _td

    dense = ds.dense[loop_id]
    grid = ds.grid_seconds()
    rows: list[tuple] = []
    tz8 = timezone(_td(hours=8))
    for i, t in enumerate(grid):
        pv = dense["PV"][i]
        sp = dense["SP"][i]
        op = dense["OP"][i]
        mode = dense["MODE"][i]
        pp = dense["PID_P"][i]
        pi = dense["PID_I"][i]
        pd_ = dense["PID_D"][i]
        ts_str = (
            datetime.fromtimestamp(t, _UTC).astimezone(tz8).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        )
        # legacy 宽表语义：pv_quality NULL=未知原码；本参考数据质量类为三态，
        # 仅 GOOD→1、BAD→0 可表达，UNKNOWN → None（与导入 _map_quality 缺失口径一致）
        pq = None if pv[2] == QC_UNKNOWN else pv[2]
        rows.append(
            (
                ts_str,
                pv[1] if pv[2] != QC_UNKNOWN else None,
                sp[1] if sp[2] != QC_UNKNOWN else None,
                op[1] if op[2] != QC_UNKNOWN else None,
                mode[1] if mode[2] != QC_UNKNOWN else None,
                pp[1] if pp[2] != QC_UNKNOWN else None,
                pi[1] if pi[2] != QC_UNKNOWN else None,
                pd_[1] if pd_[2] != QC_UNKNOWN else None,
                pq,
            )
        )
    return rows


def cov_events_for_loop(ds: ReferenceDataset, loop_id: str) -> list[tuple[str, RefEvent]]:
    """回路当前绑定下全部 (point_id, event) 对（升序），供点表写入."""
    out: list[tuple[str, RefEvent]] = []
    loop = ds.loop(loop_id)
    for role in ROLES:
        # 简单数据集无改绑；改绑场景按事件流本身分段（build_counterexamples 已拆）
        point = loop.points.get(role)
        if point is None:
            continue
        for ev in ds.events.get(point.point_id, []):
            out.append((point.point_id, ev))
    out.sort(key=lambda x: (x[1].ts_ms, x[0]))
    return out
