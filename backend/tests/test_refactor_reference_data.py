"""P0-4 参考数据生成器自检（确定性/结构/反例场景不变量）.

这些断言固定参考数据自身的结构不变量，是后续等价性差分的地基：
生成器错了，所有以它为预期的测试都会系统性偏移。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.refactor import reference_data as rd


@pytest.fixture(scope="module")
def dense_ds() -> rd.ReferenceDataset:
    return rd.build_reference_dataset(hours=0.5)  # 1800s，快速自检


@pytest.fixture(scope="module")
def cx_ds() -> rd.ReferenceDataset:
    return rd.build_counterexamples()


class TestDeterminism:
    def test_same_seed_same_output(self):
        a = rd.build_reference_dataset(hours=0.1, seed=42)
        b = rd.build_reference_dataset(hours=0.1, seed=42)
        assert a.dense.keys() == b.dense.keys()
        for lid in a.dense:
            for role in a.dense[lid]:
                assert a.dense[lid][role] == b.dense[lid][role]
        assert a.events.keys() == b.events.keys()
        for pid in a.events:
            assert [(e.ts_ms, e.value, e.quality_class, e.quality_raw) for e in a.events[pid]] == [
                (e.ts_ms, e.value, e.quality_class, e.quality_raw) for e in b.events[pid]
            ]


class TestDenseStructure:
    def test_grid_full_coverage(self, dense_ds):
        n = dense_ds.end_s - dense_ds.start_s + 1
        for roles in dense_ds.dense.values():
            for series in roles.values():
                assert len(series) == n
                # 全覆盖 Good（主数据集不含 UNKNOWN）
                assert all(q == rd.QC_GOOD for _, _, q in series)
                assert all(v is not None for _, v, _ in series)

    def test_dynamic_excitation(self, dense_ds):
        """非常值回路 PV/OP 必须逐秒变化；常值回路（LIC-401）全程恒定."""
        for lid, loop in dense_ds.loops.items():
            pv = dense_ds.dense[lid]["PV"]
            values = {v for _, v, _ in pv}
            if loop.tag_name == "REF-LIC-401":
                assert len(values) == 1  # 纯常值（V15 反例）
            else:
                # 1800s 窗口内 PV 唯一值应远超 100（逐秒变化 + 噪声）
                assert len(values) > 100

    def test_sp_mode_pid_steps(self, dense_ds):
        for lid, loop in dense_ds.loops.items():
            if loop.tag_name == "REF-LIC-401":
                continue
            sp_vals = {v for _, v, _ in dense_ds.dense[lid]["SP"]}
            assert len(sp_vals) >= 3  # 两次阶跃
            mode_vals = {v for _, v, _ in dense_ds.dense[lid]["MODE"]}
            assert mode_vals == {0, 1}  # 手/自动切换
            pid_p_vals = {v for _, v, _ in dense_ds.dense[lid]["PID_P"]}
            assert len(pid_p_vals) == 2  # 一次参数阶跃

    def test_shared_point_single_copy(self, dense_ds):
        """共享 PID 点事件只写一份，且两个回路引用同一 point_id."""
        pic = next(lp for lp in dense_ds.loops.values() if lp.tag_name == "REF-PIC-201")
        tic = next(lp for lp in dense_ds.loops.values() if lp.tag_name == "REF-TIC-301")
        for role in ("PID_P", "PID_I", "PID_D"):
            assert pic.points[role].point_id == tic.points[role].point_id
            events = dense_ds.events[pic.points[role].point_id]
            ts_list = [e.ts_ms for e in events]
            assert len(ts_list) == len(set(ts_list))  # 无重复写入

    def test_cov_events_sparse_but_complete(self, dense_ds):
        """PV/OP 逐秒变化事件≈密集点数；SP/MODE/PID 稀疏；首事件先于窗口起点."""
        for lid, loop in dense_ds.loops.items():
            for role in rd.ROLES:
                point = dense_ds.loop(lid).points[role]
                events = dense_ds.events[point.point_id]
                n_dense = len(dense_ds.dense[lid][role])
                low_freq = role in ("SP", "MODE", "PID_P", "PID_I", "PID_D")
                if low_freq and loop.tag_name != "REF-LIC-401":
                    # 低频角色仅阶跃处发事件（含窗口前 seed）
                    assert len(events) < 20
                else:
                    # PV/OP 带噪声逐秒变化（或常值回路单事件）：不超过密集+seed
                    assert len(events) <= n_dense + 1
                first = events[0]
                assert first.ts_ms <= dense_ds.start_s * 1000
                assert first.source_kind == rd.SOURCE_KIND_SNAPSHOT


class TestReferenceRawSeries:
    def test_hold_semantics_full_window(self, dense_ds):
        lid = next(iter(dense_ds.loops))
        base = datetime(2026, 9, 6)
        start = base + timedelta(seconds=300)
        end = base + timedelta(seconds=600)
        raw = rd.build_reference_raw_series(dense_ds, lid, ["PV", "SP", "OP"], start, end)
        assert len(raw.timestamps) == 301
        assert set(raw.signals.keys()) == {"pv", "sp", "op"}
        assert len(raw.quality_codes["pv_quality"]) == 301
        # 与真值逐点一致（idx 用绝对 epoch 秒）
        grid = dense_ds.grid_seconds()
        idx = {t: i for i, t in enumerate(grid)}
        for k, off in enumerate(range(300, 601)):
            i = idx[dense_ds.start_s + off]
            assert raw.signals["pv"][k] == dense_ds.dense[lid]["PV"][i][1]
            assert raw.quality_codes["pv_quality"][k] == dense_ds.dense[lid]["PV"][i][2]

    def test_wide_rows_legacy_layout(self, dense_ds):
        """宽表行数=密集点数；列序 9 列；ts 为 +8 墙钟串."""
        lid = next(iter(dense_ds.loops))
        rows = rd.wide_rows_from_dense(dense_ds, lid)
        assert len(rows) == dense_ds.end_s - dense_ds.start_s + 1
        assert all(len(r) == 9 for r in rows)
        assert rows[0][0].endswith(("000", "999")) or "." in rows[0][0]
        # 首行 ts：UTC 2026-09-06 00:00:00 → +8 墙钟 08:00:00.000
        assert rows[0][0] == "2026-09-06 08:00:00.000"


class TestCounterexamples:
    def test_unknown_windows_registered(self, cx_ds):
        lid = next(iter(cx_ds.loops))
        windows = cx_ds.unknown_windows[lid]
        assert (0, 299) in windows
        assert (600, 899) in windows
        assert (2400, 2699) in windows

    def test_no_initial_value_before_first_event(self, cx_ds):
        """PV 首 300s 无事件且无窗口前事件（V03）."""
        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        pv_events = cx_ds.events[loop.points["PV"].point_id]
        first_ts = pv_events[0].ts_ms // 1000
        assert first_ts >= cx_ds.start_s + 300

    def test_gap_no_events_and_recovery_snapshot(self, cx_ds):
        """断线窗口无事件；恢复时刻事件为 snapshot（V06）."""
        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        pv_events = cx_ds.events[loop.points["PV"].point_id]
        in_gap = [
            e for e in pv_events if cx_ds.start_s + 600 <= e.ts_ms // 1000 < cx_ds.start_s + 900
        ]
        assert in_gap == []
        recovery = [e for e in pv_events if e.ts_ms // 1000 == cx_ds.start_s + 900]
        assert len(recovery) == 1
        assert recovery[0].source_kind == rd.SOURCE_KIND_SNAPSHOT

    def test_quality_only_transitions_persisted(self, cx_ds):
        """值不变、质量 Good→Bad→Uncertain 段均有质量事件（V05）."""
        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        pv_events = cx_ds.events[loop.points["PV"].point_id]
        classes = [e.quality_class for e in pv_events]
        assert 0 in classes  # BAD
        assert -1 in classes  # Uncertain→UNKNOWN 三态
        # Bad 段事件的原码是 AAS 0
        bad_events = [e for e in pv_events if e.quality_class == 0]
        assert all(e.quality_raw == 0 for e in bad_events)

    def test_rebinding_split_points(self, cx_ds):
        """改绑：旧点事件止于 2400s 前，新点事件始于 2700s（V09）."""
        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        old_pid, new_pid = loop.rebinding["PV"]
        assert old_pid[0] == cx_ds.start_s and new_pid[0] == cx_ds.start_s + 2400
        old_events = cx_ds.events[loop.points["PV"].point_id]
        new_point_id = new_pid[1]
        new_events = cx_ds.events[new_point_id]
        assert max(e.ts_ms for e in old_events) // 1000 < cx_ds.start_s + 2400
        assert min(e.ts_ms for e in new_events) // 1000 >= cx_ds.start_s + 2700

    def test_op_no_initial_value(self, cx_ds):
        """OP 首事件 2100s，此前无窗口前事件（V03）."""
        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        op_events = cx_ds.events[loop.points["OP"].point_id]
        assert op_events[0].ts_ms // 1000 == cx_ds.start_s + 2100
        assert op_events[0].source_kind == rd.SOURCE_KIND_SNAPSHOT
