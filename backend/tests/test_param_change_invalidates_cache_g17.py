"""可调参数变更必须失效计算缓存（整改 G17 键版本部分）。

背景：异常值检测参数与可信度阈值可经 UI 运行时修改，但其进程内缓存与
L1/L2/L3 计算缓存的键中，pre_version 恒为常量 PREPROCESS_VERSION（"pre_v1"），
**不含运行时参数版本**。于是改参数后：旧口径的结果仍会被复用最长 1h（L1 TTL），
同一批 KPI 中新旧口径混合，且问题不可复现（取决于缓存命中时机）。

本周期采用「参数变更即失效全部计算缓存」的处置（与点表元数据变更同款），
而非改造缓存键版本维度——后者需改动受保护文件 preprocessing/pipeline.py。

说明：本用例是**源码级**守护，确保失效调用不被误删；它不能证明失效真的生效
（那需要 Redis 集成断言，已登记为后续补强项）。
"""

from __future__ import annotations

from pathlib import Path

_ENDPOINTS = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "endpoints"


class TestParamChangeInvalidatesCache:
    """参数保存/回滚后必须失效计算缓存。"""

    def test_outlier_config_invalidates(self) -> None:
        src = (_ENDPOINTS / "outlier_config.py").read_text(encoding="utf-8")
        assert "invalidate_all()" in src, (
            "异常值检测参数保存后未失效计算缓存——改参数后旧口径结果最长 1h 仍被复用"
        )

    def test_confidence_config_invalidates(self) -> None:
        src = (_ENDPOINTS / "confidence_config.py").read_text(encoding="utf-8")
        assert "invalidate_all()" in src, (
            "可信度阈值保存/回滚后未失效计算缓存——同一批 KPI 会新旧口径混合"
        )
