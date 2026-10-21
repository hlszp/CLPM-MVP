"""R1（2026-10-12 界面审核修复批）：held_too_long 阈值按角色分层回归。

依据：审核反馈诊断报告 §0/§1b——慢角色（MODE/SP/PID_*，COV 稀疏信号，
dev 库 48h 实测事件间隔 p50≈408s / p95≈2222s / p99≈3891s / 正常态最大
≈4806s）在统一 60s 阈值下全部正常保持段被误标 unknown，自控率
100%→8.33%、综合分崩塌。分层后：

- 慢角色（mode/sp/pid_p/pid_i/pid_d）：容忍下限 7200s（≈1.8× 正常态
  最大间隔，覆盖 p99/p95 余量；超限仍是 unknown——有界兜底不撤销）；
- 快角色（pv/op）：维持 max(3×步长, 60s)（R2 冻结/缺口反例不放松）。

契约边界（08b/c 三态语义零变化）：本批只改阈值分层的判定输入；
OBSERVED ⊆ known、HELD 合法保持计入 known、gap/no_initial/rebind/unbound
归因与游程结构全部不变（test_p3_08bc_time_quality / test_held_mask_r2r3
为既有语义护栏，本文件不重复断言其内容）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.data_source import logical_wide_builder as lwb

LOOP = "loop-r1"

FAST_LIMITS = {"fast": 60.0, "slow": 7200.0}


class _Seg:
    """最小 segment 替身（_active_segment 需要 start/end/point_id）。

    end=None 开放段：builder 内部恒补 PV 角色（共享同一 pid 的事件流），
    闭合段会让末槽落 unbound 干扰计数——真实绑定段多为开放段，此处对齐。
    """

    def __init__(self, pid: str, start: datetime, _end: datetime) -> None:
        self.point_id = pid
        self.start = start
        self.end = None
        self.initial_check = start


async def _build(
    monkeypatch,
    role: str,
    events: list[tuple[datetime, float]],
    t0: datetime,
    t1: datetime,
    initial_state: dict | None = None,
    limits: dict[str, float] | None = None,
):
    pid = "point-r1"

    async def _fake_segments(_db, _loop_id, roles, grid_start, grid_end):
        return {r.upper(): [_Seg(pid, grid_start, grid_end)] for r in roles}

    async def _fake_read_events(point_ids, start, end, **_kw):
        out = {p: [] for p in point_ids}
        for ts, val in events:
            if start <= ts <= end:
                out[pid].append({"ts": ts, "value": val, "quality_class": 1})
        return out

    async def _fake_last_states(_pids, before):
        return {pid: initial_state} if initial_state else {}

    async def _fake_anchors(_db, **_kw):
        return {}

    async def _fake_gap_windows(_db, _start, _end):
        return []

    async def _fake_limits(_step_s):
        return limits if limits is not None else FAST_LIMITS

    monkeypatch.setattr(lwb, "_resolve_role_segments", _fake_segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", _fake_gap_windows)
    monkeypatch.setattr(lwb.repo, "read_events", _fake_read_events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", _fake_last_states)
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", _fake_anchors)
    monkeypatch.setattr(lwb, "_held_limits_s", _fake_limits)
    return await lwb.build_logical_wide(None, LOOP, [role], t0, t1, 1)


class TestSlowRoleTieredHeld:
    """MODE（COV 稀疏慢角色）：25~35 分钟正常间隔 = HELD（known），非 unknown。"""

    async def test_mode_sparse_intervals_all_known(self, monkeypatch) -> None:
        """间隔 30/35 分钟（诊断报告实测形态）→ 全窗 known（HELD 保持），零 held_too_long。"""
        t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=7200)
        events = [
            (t0, 1.0),  # AUTO
            (t0 + timedelta(seconds=1800), 1.0),  # +30min
            (t0 + timedelta(seconds=3900), 1.0),  # +35min
        ]
        raw = await _build(monkeypatch, "mode", events, t0, t1)
        ctx = raw.series_context
        # 全窗 known：正常保持（HELD）不误标（断言按角色作用域——
        # unknown_reasons 是跨角色聚合，内部补入的 PV 同事件流走快档必产生
        # held_too_long，恰为本批"同数据不同角色不同判定"的分层对照）
        assert ctx.unknown_slots("mode") == 0
        assert ctx.role_coverage["mode"].unknown == [], "慢角色正常保持段不得出现 unknown"
        assert ctx.known_slots("mode") == ctx.expected_slots == 7201
        # 值与整数语义照旧（COV 保持携带旧值）
        assert all(v == 1 for v in raw.signals["mode"])
        # 三态细分不受影响：observed ⊆ known，稀疏事件仅事件槽为 OBSERVED
        cov = ctx.role_coverage["mode"]
        assert len(cov.observed) == 3  # 每事件 1 槽
        assert sum(1 for _ in cov.known) >= 1  # HELD 段计入 known 游程
        # 对照：同一事件流下 PV（快档）大量 held_too_long——分层真实生效
        assert ctx.unknown_slots("pv") > 0

    async def test_mode_window_after_initial_state_known(self, monkeypatch) -> None:
        """跨窗口：窗口内零事件、仅窗口前初值（40 分钟前）→ 全窗 HELD known。"""
        t0 = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=3600)
        initial = {
            "ts": t0 - timedelta(seconds=2400),  # 40min 前（>60s 旧阈值、<7200s 新慢档）
            "value": 1.0,
            "quality_class": 1,
        }
        raw = await _build(monkeypatch, "mode", [], t0, t1, initial_state=initial)
        ctx = raw.series_context
        assert ctx.unknown_slots("mode") == 0, "初值保持窗不得产生任何 unknown"
        assert ctx.role_coverage["mode"].unknown == []
        assert ctx.known_slots("mode") == ctx.expected_slots == 3601
        assert all(v == 1 for v in raw.signals["mode"])

    async def test_mode_held_beyond_slow_limit_still_unknown(self, monkeypatch) -> None:
        """有界兜底不撤销：MODE 无事件保持 >7200s → 超限段仍标 held_too_long。"""
        t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=7800)
        events = [(t0, 1.0), (t0 + timedelta(seconds=7500), 1.0)]
        raw = await _build(monkeypatch, "mode", events, t0, t1)
        ctx = raw.series_context
        # 距首事件 7200s（含）以内 = 合法 HELD；7201~7499s 超限 → 299 槽
        # unknown(held_too_long)；7500s 次事件起恢复 OBSERVED
        assert ctx.unknown_slots("mode") == 299
        unknown_runs = ctx.role_coverage["mode"].unknown
        assert unknown_runs and all(r.reason == "held_too_long" for r in unknown_runs)
        # 值保留（只改暴露，不抹值）
        assert all(v == 1 for v in raw.signals["mode"])

    async def test_sp_and_pid_roles_in_slow_tier(self, monkeypatch) -> None:
        """慢档覆盖 SP/PID_*（组态量：'长期无变化'即正常）。"""
        t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=3600)
        events = [(t0, 50.0), (t0 + timedelta(seconds=2100), 50.0)]  # 35min 间隔
        for role in ("sp", "pid_p", "pid_i", "pid_d"):
            raw = await _build(monkeypatch, role, events, t0, t1)
            assert raw.series_context.role_coverage[role].unknown == [], f"{role} 应走慢档"


class TestFastTierNotRelaxed:
    """快角色（PV/OP）：R2 快档维持，不因分层放松。"""

    async def test_pv_sparse_gap_still_unknown(self, monkeypatch) -> None:
        """PV 事件间隔 600s（>60s 快档）→ 保持段仍标 held_too_long（R2 反例）。"""
        t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=1199)
        events = [(t0, 1.0), (t0 + timedelta(seconds=600), 2.0)]
        raw = await _build(monkeypatch, "pv", events, t0, t1)
        ctx = raw.series_context
        assert ctx.unknown_slots("pv") > 0
        assert ctx.role_coverage["pv"].unknown

    async def test_op_long_hold_still_unknown(self, monkeypatch) -> None:
        """OP 停写 2 小时（真实缺口形态）→ 快档不放松（未知不得伪装成保持）。"""
        t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=3600)
        initial = {
            "ts": t0 - timedelta(seconds=7000),
            "value": 49.0,
            "quality_class": 1,
        }
        raw = await _build(monkeypatch, "op", [], t0, t1, initial_state=initial)
        ctx = raw.series_context
        assert ctx.unknown_slots("op") == 3601
        assert ctx.known_slots("op") == 0


class TestHeldLimitsConfig:
    """sys_config 两档下限：覆盖生效 + 失败回退默认 + 非法值防御。"""

    @staticmethod
    def _reset_cache():
        lwb._held_cfg_cache.clear()
        lwb._held_cfg_read_at = 0.0

    async def test_sysconfig_override_respected(self, monkeypatch) -> None:
        """sys_config 配置覆盖两档下限（慢 300s → 35min 间隔的 MODE 也会超限）。"""
        self._reset_cache()

        class _Result:
            @staticmethod
            def all():
                return [
                    (lwb.SYSCONF_HELD_FAST_S, "60"),
                    (lwb.SYSCONF_HELD_SLOW_S, "300"),
                ]

        async def _fake_exec(_stmt):
            return _Result()

        monkeypatch.setattr(lwb, "_metadata_execute", _fake_exec)
        limits = await lwb._held_limits_s(1)
        assert limits == {"fast": 60.0, "slow": 300.0}
        self._reset_cache()

    async def test_config_failure_falls_back_to_defaults(self, monkeypatch) -> None:
        """配置读取失败（无 PG/表缺失）→ 默认两档，不炸不阻塞构建。"""
        self._reset_cache()

        async def _boom(_stmt):
            raise RuntimeError("no db")

        monkeypatch.setattr(lwb, "_metadata_execute", _boom)
        limits = await lwb._held_limits_s(1)
        assert limits == {"fast": 60.0, "slow": 7200.0}
        self._reset_cache()

    def test_positive_float_defense(self) -> None:
        assert lwb._positive_float(None, 60.0) == 60.0
        assert lwb._positive_float("abc", 60.0) == 60.0
        assert lwb._positive_float("0", 60.0) == 60.0  # 非正数回默认
        assert lwb._positive_float("-5", 60.0) == 60.0
        assert lwb._positive_float("90", 60.0) == 90.0

    async def test_step_floor_still_applies(self, monkeypatch) -> None:
        """max(3×步长, 档位下限) 的步长底限保留（粗网格容忍抖动语义不变）。"""
        self._reset_cache()

        class _Result:
            @staticmethod
            def all():
                return []

        async def _fake_exec(_stmt):
            return _Result()

        monkeypatch.setattr(lwb, "_metadata_execute", _fake_exec)
        limits = await lwb._held_limits_s(60)  # 60s 网格步长
        assert limits == {"fast": 180.0, "slow": 7200.0}
        self._reset_cache()
