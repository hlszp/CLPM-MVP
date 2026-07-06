#!/usr/bin/env python3
"""DEBUG 模式运行 kpi_calc 计算流程，验证日志覆盖所有关键节点。

使用 generate_kpi_test_data.py 生成的测试数据，模拟一次完整的 KPI 计算，
检查新增的 DEBUG 日志是否覆盖：
  - [平稳率] σ/U/σ_norm/osc_ratio/k/steady_rate
  - [准确率] |Ē|/|E|max/比值/accuracy_rate
  - [快速率] 实际稳态时间/理想稳态时间/fast_rate
  - [振荡率] 零交叉点数/正负面积相似率/osc_rate
  - [综合评分] 4 分项加权明细/weighted_sum/weight_total/score
  - [KPI计算] 汇总
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 配置 DEBUG 日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
# 降低第三方库日志级别
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from app.tasks.kpi_calc import _compute_composite_score, _compute_kpis  # noqa: E402

# 加载测试数据
FIXTURE_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "kpi_test_data.json"


def load_scenario(name: str) -> list[dict]:
    """加载指定场景的测试数据。"""
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    if name not in data:
        raise KeyError(f"场景 {name} 不存在")
    return data[name]["data"]


def make_metric_configs() -> dict:
    """构造国标 4 分项指标配置（与 seed_data.sql 对齐）。"""
    from decimal import Decimal
    from unittest.mock import MagicMock

    def _cfg(code: str, weight: str) -> MagicMock:
        c = MagicMock()
        c.metric_code = code
        c.weight = Decimal(weight)
        c.is_enabled = True
        return c

    return {
        "good_value_rate": _cfg("good_value_rate", "0"),
        "auto_mode_rate": _cfg("auto_mode_rate", "0"),
        "effective_auto_rate": _cfg("effective_auto_rate", "20"),
        "steady_rate": _cfg("steady_rate", "30"),
        "accuracy_rate": _cfg("accuracy_rate", "30"),
        "fast_rate": _cfg("fast_rate", "20"),
        "oscillation_rate": _cfg("oscillation_rate", "0"),
        "saturation_rate": _cfg("saturation_rate", "0"),
    }


def run_scenario(name: str) -> None:
    """运行单个场景的 KPI 计算。"""
    print(f"\n{'=' * 72}")
    print(f"场景: {name}")
    print(f"{'=' * 72}")

    data = load_scenario(name)
    print(f"数据点数: {len(data)}")

    # 构造 metric_configs
    configs = make_metric_configs()

    # 计算 KPI
    from decimal import Decimal

    good_value_rate = Decimal("100.0")
    kpi_values = _compute_kpis(data, configs, good_value_rate=good_value_rate)

    # 计算综合评分
    score = _compute_composite_score(kpi_values, configs)

    print("\n--- 结果汇总 ---")
    print(f"  综合评分 P = {score}")
    print(f"  准确率 A   = {kpi_values.get('accuracy_rate')}")
    print(f"  快速率 F   = {kpi_values.get('fast_rate')}")
    print(f"  平稳率 S   = {kpi_values.get('steady_rate')}")
    print(f"  有效自控率 R = {kpi_values.get('effective_auto_rate')}")
    print(f"  振荡率     = {kpi_values.get('oscillation_rate')}")
    print(f"  饱和率     = {kpi_values.get('saturation_rate')}")
    print(f"  自控率     = {kpi_values.get('auto_mode_rate')}")
    print(f"  好值率     = {kpi_values.get('good_value_rate')}")


def main() -> None:
    """主入口：运行所有场景验证。"""
    if not FIXTURE_PATH.exists():
        print(f"错误: 测试数据文件不存在: {FIXTURE_PATH}")
        print("请先运行: uv run python scripts/generate_kpi_test_data.py")
        sys.exit(1)

    scenarios = [
        "fast_response",
        "slow_response",
        "oscillation",
        "op_saturation",
        "normal",
        "manual_mode",
        "pure_ar2",
    ]

    for name in scenarios:
        try:
            run_scenario(name)
        except Exception as e:
            print(f"场景 {name} 失败: {e}")
            import traceback

            traceback.print_exc()

    print(f"\n{'=' * 72}")
    print("DEBUG 日志验证完成 — 检查上方日志是否覆盖所有关键节点")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
