"""投自动回路占比必须按去重回路口径（整改 G16 守护）。

背景：performance 的 auto_loop_ratio 原实现为 SUM(auto_mode_rate > 0) / COUNT(*)，
分母是**回路×小时行数**而非回路数，但 docstring 写的是「统计 auto_mode_rate > 0
的回路数占比」——实现与文档不符；同一回路在窗口内多行时被重复计权，各回路成功
小时数不等时结果系统性偏离（24h 窗口最多放大 24 倍量级）。

修复：分子 COUNT(DISTINCT case(auto_mode_rate > 0 -> loop_id))，分母
COUNT(DISTINCT loop_id)。

关于守护强度（如实说明）：真实 PG 断言更可靠，但其聚合函数引用
LoopLedger.score_weight，需同时构造 kpi_snapshot_hourly 与 loop_ledger 关联行；
本文件采用**源码级**守护，断言去重口径的关键表达式存在且旧的按行计数写法不再出现。
它能防止「退回按行计数」这一具体回归，但不能证明聚合结果正确。
"""

from __future__ import annotations

import inspect

from app.services import performance as perf


class TestAutoLoopRatioDistinctSemantics:
    """分子与分母都必须按 DISTINCT loop_id 计数。"""

    def test_uses_distinct_loop_id(self) -> None:
        src = inspect.getsource(perf._aggregate_kpi_summary)
        assert "func.distinct(KpiSnapshotHourly.loop_id)" in src, (
            "投自动回路占比的分母未使用 COUNT(DISTINCT loop_id) —— "
            "会退回「回路×小时行数」口径，24h 窗口最多放大 24 倍（G16 回归）"
        )
        assert "KpiSnapshotHourly.auto_mode_rate > 0, KpiSnapshotHourly.loop_id" in src, (
            "分子未按 DISTINCT loop_id 计数（G16 回归）"
        )

    def test_no_row_count_based_auto_count(self) -> None:
        """不得退回按行计数（func.sum(func.cast(...))）。"""
        src = inspect.getsource(perf._aggregate_kpi_summary)
        assert "func.cast(" not in src, (
            "出现 func.cast(auto_mode_rate > 0) 形式的按行计数 —— G16 回归"
        )

    def test_docstring_matches_implementation(self) -> None:
        """docstring 声称的是「回路数占比」，实现须与之一致。"""
        doc = inspect.getdoc(perf._aggregate_kpi_summary) or ""
        assert "回路数占比" in doc, "docstring 口径描述已变，请同步核对实现"
