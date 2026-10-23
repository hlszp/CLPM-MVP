"""G5 前置立卡项回归守护（2026-10-13 批 1' 受控投递暴露的四个缺陷）.

背景（工具屏障报告 /tmp/batch1p_report.json，06:00 UTC 窗）：
1. 主成功路径快照落库漏传 source/source_task_id → kpi_snapshot_hourly
   1000 行 source=None（早退分支均已透传，唯主路径缺失）；
2. 独立 QUALITY_HF 伪角色查询（粗 BASE 控制类型禁止派生 HF 的路径）
   quality_codes 恒空 → good_value_rate 不可判定 → legacy 回退 0% 判
   INCONCLUSIVE(value=None) → 非 FLOW 控制类型快照全量 PARTIAL；
3. 节点级聚合版本戳复用 performance.ALGORITHM_VERSION（v1.0 仪表盘
   读侧常量）→ kpi_node_snapshot_hourly 恒戳 v1.0，与回路级 v2.0 裂轨。

说明：与 test_grid_downsampling_g12 同策略——目标函数依赖 DB 会话/完整
计算管线，端到端用例成本高于收益处采用**源码级守护**（防具体回归，
不证明全量正确性）；纯函数处用功能用例（_batch_algorithm_version）。
source 判定顺序的功能用例见 test_api_tasks.py TestHourlyWindowMutex。
"""

from __future__ import annotations

import inspect

from app.services import node_performance as np
from app.services.data_source import logical_wide_builder as b
from app.tasks import kpi_calc as kc


class TestMainPathSourceThreading:
    """立卡项1（主路径 source 透传）。"""

    def test_main_persist_passes_source(self) -> None:
        """主成功/部分路径的 _timed_persist 调用必须透传 source/source_task_id.

        主路径由 ``score=final_score`` 唯一标识（其余 persist 调用为早退
        分支，早已显式传参）。
        """
        src = inspect.getsource(kc._calculate_loop_kpi)
        assert "score=final_score" in src, "主路径 persist 调用锚点缺失（函数结构已变，请同步守护）"
        # 截取主路径调用段（score=final_score 起至该调用闭合）
        anchor = src.index("score=final_score")
        segment = src[anchor : anchor + 2500]
        assert "source=source" in segment, (
            "主成功/部分路径 _timed_persist 漏传 source——快照 UPSERT 会把 source "
            "覆盖为 NULL（批 1' 现场 1000 行 source=None 的直接原因）"
        )
        assert "source_task_id=source_task_id" in segment, (
            "主成功/部分路径 _timed_persist 漏传 source_task_id——屏障无法解析"
            "本批 TaskRecord 终态（taskRecordStatus=null）"
        )

    def test_entry_source_judgment_precedes_record_creation(self) -> None:
        """来源判定必须先于 Beat 路径的 TaskRecord 创建（判定顺序守护）.

        判定依据 ``manual_triggered = task_id is not None`` 必须出现在
        ``task_id = await task_tracker.create_task`` 赋值之前。
        """
        src = inspect.getsource(kc._do_hourly_with_tracking)
        idx_judge = src.index("manual_triggered = task_id is not None")
        idx_assign = src.index("task_id = await task_tracker.create_task")
        assert idx_judge < idx_assign, (
            "source 判定发生在创建系统任务记录之后——Beat 路径会恒落 "
            "MANUAL_STANDARD（2026-09 登记「判定顺序问题」的回归形态）"
        )


class TestQualityHfPseudoRoleCodes:
    """立卡项2（QUALITY_HF 伪角色质量码输出）。"""

    def test_pseudo_quality_request_emits_quality_codes(self) -> None:
        """PV_QUALITY 伪角色请求必须同时输出 quality_codes（不止伪信号）.

        回归形态：``if "pv" in roles:`` ——伪角色请求的 roles 已剔除
        pv_quality，条件恒假，质量三态只进 signals["pv_quality"]。

        2026-10-23 全角色透传后输出形态改为逐角色 ``{role}_quality`` 循环 +
        伪角色 setdefault；守护同步为新不变量（旧回归形态仍不得复活，
        功能用例见 test_role_quality_and_anchor_expiry）。
        """
        src = inspect.getsource(b.build_logical_wide)
        assert 'if "pv" in roles:' not in src, (
            "quality_codes 输出回到按 pv 角色门——独立 QUALITY_HF 查询"
            "（BASE>1s 的控制类型）质量码恒空，good_value_rate 不可判定，"
            "非 FLOW 回路快照全量 PARTIAL"
        )
        assert 'quality_codes[f"{r}_quality"]' in src, (
            "逐角色质量码输出循环缺失——非 PV 角色坏质量会在取数链丢失"
        )
        assert 'quality_codes.setdefault("pv_quality"' in src, (
            "伪角色（PV_QUALITY）请求的 pv_quality 输出缺失"
        )

    def test_quality_summary_computes_good_rate_from_codes(self) -> None:
        """端到端语义锚：质量码到位后 summary 必须产出 good_value_rate（功能）."""
        from datetime import datetime, timedelta

        from app.services.preprocessing.quality_summary import compute_quality_summary

        n = 4
        base = datetime(2026, 10, 13, 6, 0, 0)
        validity = {"pv_valid": [True] * n}
        timestamps = [base + timedelta(seconds=i) for i in range(n)]
        summary = compute_quality_summary(
            validity=validity,
            timestamps=timestamps,
            point_count=n,
            quality_codes=[1, 1, 1, 1],
            expected_interval_s=1.0,
        )
        assert summary.good_value_rate == 1.0


class TestProviderPseudoQualityPassthrough:
    """立卡项2 返工（验收 B③）：provider 合并层穿透性功能守护.

    首轮修复只改了 builder 出口条件；``tdengine_provider._point_or_mixed_query``
    的 4 处 ``if "pv" in roles_lower:`` 同构门未修——QUALITY_HF 独立查询的
    tag_roles=["pv_quality"]（clpm_metric_data_requirement 实查口径），质量码
    在 provider 合并层被整体丢弃（qc_len=0），DataBlock good_value_rate 仍为
    None，非 FLOW 回路 PARTIAL 症状端到端不变。本类从 builder 出口形态出发
    穿透 provider→pipeline→DataBlock→计算器全链（不依赖 DB/TDengine，
    fake builder 模拟 TDengine 质量码行）。
    """

    N = 3601  # 1s 网格 1 小时（含首尾）

    def _fake_raw(self, roles_requested: list[str]):
        """构造 builder 出口形态的 RawTimeSeries（全 Good 质量码行）."""
        from datetime import datetime, timedelta

        from app.contracts.data_types import RawTimeSeries

        start = datetime(2026, 10, 13, 6, 0)
        ts = [start + timedelta(seconds=i) for i in range(self.N)]
        codes = [1] * self.N
        signals: dict[str, list] = {}
        quality_codes: dict[str, list[int]] = {}
        # PV 在请求集（BASE）→ 两处都输出；仅伪角色（QUALITY_HF）→ 伪信号 +
        # builder 修复后的质量码
        if "pv" in [r.lower() for r in roles_requested]:
            signals["pv"] = [50.0] * self.N
            quality_codes["pv_quality"] = list(codes)
        if "pv_quality" in [r.lower() for r in roles_requested]:
            signals["pv_quality"] = list(codes)
            quality_codes["pv_quality"] = list(codes)
        return RawTimeSeries(
            timestamps=ts, signals=signals, quality_codes=quality_codes, series_context=None
        )

    async def _run_provider(self, monkeypatch, tag_roles: list[str]):
        from datetime import datetime
        from types import SimpleNamespace

        from app.services.data_source import logical_wide_builder as lwb
        from app.services.data_source.tdengine_provider import _point_or_mixed_query

        start = datetime(2026, 10, 13, 6, 0)
        end = datetime(2026, 10, 13, 7, 0)

        async def fake_build_logical_wide(db, loop_id, roles, s, e, interval_s):  # noqa: ARG001
            return self._fake_raw(list(roles))

        monkeypatch.setattr(lwb, "build_logical_wide", fake_build_logical_wide)
        return await _point_or_mixed_query(
            db=None,
            loop_id="loop-g5b",
            tag_roles=tag_roles,
            start_dt=start,
            end_dt=end,
            interval_s=1,
            layouts=[SimpleNamespace(start=start, end=end)],
        )

    async def test_quality_hf_query_keeps_codes_through_provider_to_datablock(
        self, monkeypatch
    ) -> None:
        """QUALITY_HF 独立查询（tag_roles=["PV_QUALITY"]）穿透全链出 good_value_rate.

        回归形态：provider 4 处 ``"pv" in roles_lower`` 门 → 出口
        quality_codes={} → DataBlock good_value_rate=None → 计算器 INCONCLUSIVE
        (value=None) → 快照 PARTIAL（非 FLOW 控制类型全量）。
        """
        from app.contracts.data_types import (
            ControlType,
            DataLineage,
            LoopPreprocessConfig,
            MetricDataBundle,
            TagGroup,
        )
        from app.services.metric_calculator import get_calculator
        from app.services.preprocessing.pipeline import PreprocessingPipeline

        raw_out = await self._run_provider(monkeypatch, ["PV_QUALITY"])

        # provider 出口：质量码不再被 "pv" 角色门丢弃
        qc = raw_out.quality_codes.get("pv_quality") or []
        assert len(qc) == self.N, (
            f"provider 出口质量码长度 {len(qc)} ≠ {self.N}——合并层伪角色门回归"
            "（builder 输出被丢弃，验收 B③ 复现形态）"
        )
        assert set(qc) == {1}

        # pipeline：DataBlock quality_summary.good_value_rate 可算
        config = LoopPreprocessConfig(
            loop_id="loop-g5b",
            control_type=ControlType.TEMPERATURE,  # BASE=5s>1s → 独立 QUALITY_HF 路径
            range_min=0.0,
            range_max=400.0,
        )
        block = PreprocessingPipeline(config).process(raw_out, TagGroup.QUALITY_HF, window=None)
        assert block.quality_summary.good_value_rate == 1.0, (
            "provider→pipeline 穿透后 good_value_rate 仍为 None——非 FLOW 回路快照将维持 PARTIAL"
        )

        # 快照语义层：好值率计算器出值（required_kpis 三项齐 → PARTIAL 恢复 SUCCESS）
        bundle = MetricDataBundle(
            metric_code="good_value_rate",
            data_block=block,
            mask_expression="",
            masked_indices=list(range(self.N)),
            lineage=DataLineage(),
        )
        result = get_calculator("good_value_rate").calculate(bundle)
        assert result.value == 100.0, (
            f"好值率计算器 value={result.value}——legacy 回退 0% 判 INCONCLUSIVE 的 PARTIAL 症状仍在"
        )

    async def test_base_query_pv_role_codes_unchanged(self, monkeypatch) -> None:
        """BASE 查询（roles 含 PV，FLOW 派生路径的取数形态）质量码透传不变.

        FLOW（base=1s）的 QUALITY_HF 从 BASE DataBlock 派生，BASE 查询含
        真实 PV 角色——本用例守护 ``"pv" in roles_lower`` 原分支行为不因
        伪角色条件放宽而回归。
        """
        raw_out = await self._run_provider(monkeypatch, ["PV", "SP", "OP", "MODE"])
        qc = raw_out.quality_codes.get("pv_quality") or []
        assert len(qc) == self.N and set(qc) == {1}
        assert "pv" in raw_out.signals  # PV 数值信号照常
        assert "pv_quality" not in raw_out.signals  # 未请求伪角色不额外输出

    def test_provider_source_no_bare_pv_role_gate_left(self) -> None:
        """源码守护：_point_or_mixed_query 不得残留裸 ``"pv" in roles_lower`` 门.

        2026-10-23 全角色透传后 provider 改为整体透传 builder 质量键
        （wants_pv_quality 门整体移除）；守护同步为新不变量。
        """
        from app.services.data_source import tdengine_provider as tp

        src = inspect.getsource(tp._point_or_mixed_query)
        assert 'if "pv" in roles_lower:' not in src, (
            "provider 合并层残留裸 pv 角色门——伪角色请求（QUALITY_HF）质量码"
            "会在此被丢弃（返工目标就是消灭这 4 处门）"
        )
        assert "wants_pv_quality" not in src, (
            "provider 合并层不得重新引入按 pv 挑选质量键的门——非 PV 角色"
            "（op/sp/mode 等）质量码会在此被丢弃"
        )
        assert "raw.quality_codes.items()" in src, (
            "provider 合并层必须整体透传 builder 输出的全部质量键"
        )


class TestNodeSnapshotVersionStamp:
    """立卡项3（节点快照版本戳随批）。"""

    def test_node_performance_not_reusing_dashboard_v1_constant(self) -> None:
        """node_performance 不得再导入 performance.ALGORITHM_VERSION（v1.0）."""
        src = inspect.getsource(np)
        assert "from app.services.performance import ALGORITHM_VERSION" not in src, (
            "节点级聚合复用了仪表盘读侧 v1.0 常量——kpi_node_snapshot_hourly "
            "版本戳与回路级裂轨（容量屏障 nodeBadVersion 全量违约）"
        )

    def test_node_fallback_version_matches_loop_version(self) -> None:
        """空名册回落常量必须与回路级当前算法版本一致."""
        assert np.ALGORITHM_VERSION == kc.ALGORITHM_VERSION, (
            f"节点聚合回落版本 {np.ALGORITHM_VERSION} 与回路级 {kc.ALGORITHM_VERSION} "
            "不一致——版本升级时需同步（或改为单一事实源）"
        )

    def test_batch_algorithm_version_derives_from_input(self) -> None:
        """主路径版本随批：取聚合输入回路快照的实际版本."""

        class _Snap:
            def __init__(self, version: str | None) -> None:
                self.algorithm_version = version

        assert np._batch_algorithm_version({"a": _Snap("KPI_CALC_v2.0")}) == "KPI_CALC_v2.0"
        # 多输入取最后一条非空版本
        assert (
            np._batch_algorithm_version(
                {"a": _Snap(None), "b": _Snap("KPI_CALC_v9.9"), "c": _Snap(None)}
            )
            == "KPI_CALC_v9.9"
        )
        # 无输入回落常量（空名册路径）
        assert np._batch_algorithm_version({}) == np.ALGORITHM_VERSION
        assert np._batch_algorithm_version([]) == np.ALGORITHM_VERSION

    def test_batch_aggregate_returns_derived_version(self) -> None:
        """aggregate_node_snapshot(s)_with_presets 主返回用随批版本（源码守护）."""
        src = inspect.getsource(np)
        assert src.count('"algorithm_version": _batch_algorithm_version(latest_snaps),') >= 2, (
            "单节点/批量两条主聚合路径的版本戳应随聚合输入（_batch_algorithm_version）"
        )


class TestHourlyWindowLockSemantics:
    """立卡项4（同窗互斥语义澄清，登记性守护）.

    现场结论：锁键 ``task:hourly_calc_lock:<YYYYMMDDHH>`` 仅按窗口构成，
    不含 source/task 维度——批 1'（.delay 直投）与 Beat（15:00:01）同窗
    为**同一把锁**；SETNX 执行互斥工作正常（批 1' 账本 751s 闭合后释放
    锁，Beat 随后取锁串行重算并按 DEC-09 同窗重评语义覆盖投影+新增账本
    record）。此处守护锁键构成不引入 source/task 维度（防止"不同任务
    不同键"的真并发回归）。
    """

    def test_lock_key_is_window_scoped_only(self) -> None:
        key_manual = kc._hourly_window_lock_key("2026-10-13T06:00:00Z")
        key_beat_default_style = "2026-10-13T06:00:00"  # Beat 无参路径解析同窗
        assert kc._hourly_window_lock_key(key_beat_default_style) == key_manual
        assert key_manual == "task:hourly_calc_lock:2026101306"
        # 不同触发（带/不带 tz、不同 ISO 形态）落同一窗口键
        assert kc._hourly_window_lock_key("2026-10-13T06:00:00+00:00") == key_manual
