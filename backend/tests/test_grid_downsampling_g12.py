"""降采样网格必须使用调用方传入的 interval_s（整改 G12 降采样部分）。

背景：logical_wide_builder 的网格步长此前硬编码为 1 秒，而参数 interval_s 标注
ARG001 被完全忽略。30 天窗口即 259 万槽 × 7 角色，纯 Python 逐槽循环 + 线性扫段/
扫 gap，内存与 CPU 都不可行——与 AGENTS.md「LTTB maxPoints=2000，30 天窗口」的
性能边界直接冲突。而调用方 tdengine_provider 早已在传该参数（data_planner 的
查询任务本就带采样间隔），只是被丢弃。

说明：本用例是**源码级**守护（builder 依赖 DB 会话，构造完整集成用例成本高于
收益）。它能防止「步长又被写死」这一具体回归，但不能证明网格点数正确；
渲染级/集成级断言（构造 30 天窗口断言 len(timestamps) <= maxPoints）已登记为
后续补强项。
"""

from __future__ import annotations

import inspect

from app.services.data_source import logical_wide_builder as b


class TestGridDownsampling:
    """网格步长必须来自 interval_s。"""

    def test_grid_step_uses_interval_s(self) -> None:
        """源码中必须用 interval_s 推进网格，不得写死 1 秒。"""
        src = inspect.getsource(b.build_logical_wide)
        assert "interval_s" in src, "build_logical_wide 未使用 interval_s（降采样失效）"
        assert "timedelta(seconds=1)" not in src, (
            "网格步长仍写死为 1 秒——30 天窗口会产生 259 万槽，与 maxPoints=2000 冲突"
        )
