#!/usr/bin/env python3
"""KPI 算法验证测试数据生成器。

生成 7 种业务场景的模拟时序数据，专门支持以下算法的完整功能验证：
    1. ARMA 稳态时间计算（含已知 AR 参数的标准信号）
    2. 快速率算法（含 SP 阶跃响应）
    3. 振荡率 IAE 零交叉相似率法
    4. 综合评分 4 指标加权

数据格式与 kpi_calc.py 的 aligned 输入一致：
    [{"ts": float, "pv": float, "sp": float, "op": float, "mode": int, "pv_quality": int}, ...]

用法::

    cd backend && uv run python scripts/generate_kpi_test_data.py
    # 输出: tests/fixtures/kpi_test_data.json
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

# ============================================================================
# 全局参数
# ============================================================================

SAMPLE_INTERVAL = 1.0  # 采样间隔（秒）
DURATION_SEC = 7200  # 数据时长（2 小时）
N_POINTS = int(DURATION_SEC / SAMPLE_INTERVAL)
RANDOM_SEED = 42

np.random.seed(RANDOM_SEED)

# ============================================================================
# 场景定义
# ============================================================================

SCENARIOS = {
    # ------------------------------------------------------------------
    # 场景 1: 快速响应回路
    # AR(1) a1=-0.3，Green 函数快速衰减，稳态时间 ~10s
    # 预期：快速率 > 80，平稳率 > 85
    # ------------------------------------------------------------------
    "fast_response": {
        "description": "快速响应回路：PV 在 10s 内跟随 SP 变化",
        "ar_coeffs": [-0.3],  # 已知 AR 参数（供验证）
        "expected_settling_sec": 10,  # 预期稳态时间
        "expected_fast_rate": (80, 100),
        "base_sp": 100.0,
        "base_pv": 100.0,
        "base_op": 50.0,
        "pv_range": 200.0,  # 量程
        "control_type": "FAST",
    },
    # ------------------------------------------------------------------
    # 场景 2: 慢速响应回路
    # AR(1) a1=-0.95，Green 函数缓慢衰减，稳态时间 ~60s
    # 预期：快速率 < 50
    # ------------------------------------------------------------------
    "slow_response": {
        "description": "慢速响应回路：PV 需要 60s+ 才能跟随 SP",
        "ar_coeffs": [-0.95],
        "expected_settling_sec": 60,
        "expected_fast_rate": (0, 50),
        "base_sp": 350.0,
        "base_pv": 348.0,
        "base_op": 55.0,
        "pv_range": 100.0,
        "control_type": "STABLE",
    },
    # ------------------------------------------------------------------
    # 场景 3: 振荡回路
    # 正弦波周期 600s + 噪声，IAE 零交叉应检测到振荡
    # 预期：振荡率 > 40，平稳率 < 50
    # ------------------------------------------------------------------
    "oscillation": {
        "description": "振荡回路：PV 正弦振荡，周期 600s",
        "ar_coeffs": None,  # 非纯 AR 信号
        "oscillation_period": 600.0,
        "oscillation_amplitude": 3.5,
        "expected_oscillation_rate": (30, 80),
        "base_sp": 85.0,
        "base_pv": 85.0,
        "base_op": 48.0,
        "pv_range": 50.0,
        "control_type": "STABLE",
    },
    # ------------------------------------------------------------------
    # 场景 4: OP 饱和回路
    # OP 长期待在 95-100%，饱和率高
    # 预期：饱和率 > 30，有效自控率低
    # ------------------------------------------------------------------
    "op_saturation": {
        "description": "OP 饱和回路：输出长时间限位",
        "ar_coeffs": [-0.5],
        "expected_saturation_rate": (25, 50),
        "base_sp": 50.0,
        "base_pv": 50.0,
        "base_op": 97.0,
        "pv_range": 100.0,
        "control_type": "SLOW",
    },
    # ------------------------------------------------------------------
    # 场景 5: 正常回路
    # PV 紧跟 SP，各项指标良好
    # 预期：综合评分 > 80
    # ------------------------------------------------------------------
    "normal": {
        "description": "正常回路：PV 紧跟 SP，各项指标良好",
        "ar_coeffs": [-0.4],
        "expected_settling_sec": 15,
        "expected_fast_rate": (70, 100),
        "base_sp": 120.0,
        "base_pv": 120.0,
        "base_op": 55.0,
        "pv_range": 200.0,
        "control_type": "STABLE",
    },
    # ------------------------------------------------------------------
    # 场景 6: 手动模式回路
    # MODE=0（手动），自控率 ~0%
    # 预期：自控率 < 5，有效自控率 < 5
    # ------------------------------------------------------------------
    "manual_mode": {
        "description": "手动模式回路：MODE=0，自控率极低",
        "ar_coeffs": [-0.6],
        "expected_auto_rate": (0, 5),
        "base_sp": 60.0,
        "base_pv": 58.0,
        "base_op": 35.0,
        "pv_range": 100.0,
        "control_type": "STABLE",
    },
    # ------------------------------------------------------------------
    # 场景 7: 纯 AR(2) 标准信号（ARMA 辨识精度验证）
    # 已知参数 a1=-0.5, a2=0.3，用于验证 AR 辨识准确性
    # ------------------------------------------------------------------
    "pure_ar2": {
        "description": "纯 AR(2) 标准信号：已知参数 a1=-0.5, a2=0.3",
        "ar_coeffs": [-0.5, 0.3],  # 已知 AR(2) 参数
        "expected_ar_coeffs": [-0.5, 0.3],  # 期望辨识结果
        "expected_settling_sec": 20,
        "base_sp": 0.0,  # 纯偏差信号，SP=0
        "base_pv": 0.0,
        "base_op": 50.0,
        "pv_range": 100.0,
        "control_type": "STABLE",
    },
}


# ============================================================================
# 数据生成函数
# ============================================================================


def _gen_ar_signal(
    ar_coeffs: list[float],
    n: int,
    noise_std: float = 0.1,
) -> np.ndarray:
    """生成 AR(p) 信号。

    x(t) = -Σ aᵢ·x(t-i) + e(t)

    Args:
        ar_coeffs: AR 系数 [a₁, a₂, ..., aₚ]
        n: 信号长度
        noise_std: 白噪声标准差

    Returns:
        AR 信号数组
    """
    p = len(ar_coeffs)
    signal = np.zeros(n)
    noise = np.random.randn(n) * noise_std

    for t in range(p, n):
        s = 0.0
        for i in range(1, p + 1):
            s += ar_coeffs[i - 1] * signal[t - i]
        signal[t] = -s + noise[t]

    return signal


def _gen_sp_schedule(
    base_sp: float,
    n: int,
    interval: float,
    change_ratio: float = 0.05,
) -> np.ndarray:
    """生成 SP 调度：基值 + 周期性阶跃变化（用于快速率测试）。

    每 1800s（30 分钟）发生一次阶跃变化，幅度为量程的 10%。
    """
    sp = np.full(n, base_sp, dtype=float)
    # 每 1800s 阶跃一次
    step_period = int(1800 / interval)
    step_amplitude = base_sp * change_ratio * 2  # 10% 量程

    for i in range(step_period, n, step_period * 2):
        sp[i : i + step_period] = base_sp + step_amplitude

    return sp


def _gen_oscillation_signal(
    base_sp: float,
    n: int,
    interval: float,
    period: float,
    amplitude: float,
    noise_std: float = 0.3,
) -> np.ndarray:
    """生成振荡信号：基值 + 正弦波 + 噪声。"""
    t = np.arange(n) * interval
    signal = base_sp + amplitude * np.sin(2 * math.pi * t / period) + np.random.randn(n) * noise_std
    return signal


def _gen_op_saturation(
    base_op: float,
    n: int,
    interval: float,
    sat_ratio: float = 0.35,
) -> np.ndarray:
    """生成 OP 饱和信号：35% 时间处于 95-100% 饱和区。"""
    op = np.full(n, base_op, dtype=float)
    sat_period = int(1800 / interval)  # 30 分钟饱和
    norm_period = int(3600 / interval)  # 60 分钟正常

    t = 0
    while t < n:
        # 饱和期
        end = min(t + sat_period, n)
        op[t:end] = 97.0 + np.random.randn(end - t) * 0.5
        op[t:end] = np.clip(op[t:end], 95, 100)
        t = end

        # 正常期
        end = min(t + norm_period, n)
        op[t:end] = 50.0 + np.random.randn(end - t) * 2.0
        op[t:end] = np.clip(op[t:end], 10, 90)
        t = end

    return op


def generate_scenario_data(scenario_name: str, config: dict) -> dict:
    """生成单个场景的完整时序数据。

    Returns:
        {
            "scenario": str,
            "description": str,
            "config": dict,
            "expected": dict,      # 预期结果范围
            "data": list[dict],    # 时序数据（与 kpi_calc aligned 格式一致）
            "ar_signal": list[float],  # 纯 AR 偏差信号（供 ARMA 验证）
        }
    """
    n = N_POINTS
    interval = SAMPLE_INTERVAL
    base_sp = config["base_sp"]
    base_op = config["base_op"]
    pv_range = config["pv_range"]

    # 生成 SP 调度（含阶跃变化）
    sp_values = _gen_sp_schedule(base_sp, n, interval)

    # 生成 PV 偏差信号
    ar_coeffs = config.get("ar_coeffs")
    if ar_coeffs is not None:
        # 纯 AR 信号
        ar_signal = _gen_ar_signal(ar_coeffs, n, noise_std=pv_range * 0.002)
        pv_values = sp_values + ar_signal
    elif scenario_name == "oscillation":
        # 振荡信号
        ar_signal = _gen_oscillation_signal(
            0,
            n,
            interval,
            config["oscillation_period"],
            config["oscillation_amplitude"],
        )
        pv_values = sp_values + ar_signal
    else:
        # 默认：小噪声
        ar_signal = np.random.randn(n) * pv_range * 0.003
        pv_values = sp_values + ar_signal

    # 生成 OP
    if scenario_name == "op_saturation":
        op_values = _gen_op_saturation(base_op, n, interval)
    else:
        # OP 跟踪 SP 偏差 + 漂移
        op_values = np.full(n, base_op, dtype=float)
        for i in range(1, n):
            op_values[i] = (
                op_values[i - 1]
                + (sp_values[i] - pv_values[i - 1]) * 0.02
                + np.random.randn() * 0.3
            )
        op_values = np.clip(op_values, 0, 100)

    # 生成 MODE
    if scenario_name == "manual_mode":
        mode_values = np.zeros(n, dtype=int)  # 全手动
    else:
        mode_values = np.ones(n, dtype=int)  # 全自动
        # 5% 时间切手动（模拟短暂手动操作）
        manual_start = np.random.randint(0, n - 300, size=3)
        for ms in manual_start:
            mode_values[ms : ms + 100] = 0

    # 生成 PV 质量码（99.5% Good）
    pv_quality = np.ones(n, dtype=int)
    bad_indices = np.random.choice(n, size=int(n * 0.005), replace=False)
    pv_quality[bad_indices] = 0

    # 组装时序数据
    data = []
    for i in range(n):
        data.append(
            {
                "ts": float(i * interval),
                "pv": round(float(pv_values[i]), 4),
                "sp": round(float(sp_values[i]), 4),
                "op": round(float(op_values[i]), 4),
                "mode": int(mode_values[i]),
                "pv_quality": int(pv_quality[i]),
            }
        )

    # 预期结果
    expected = {}
    if "expected_settling_sec" in config:
        expected["settling_time_sec"] = config["expected_settling_sec"]
    if "expected_fast_rate" in config:
        expected["fast_rate_range"] = config["expected_fast_rate"]
    if "expected_oscillation_rate" in config:
        expected["oscillation_rate_range"] = config["expected_oscillation_rate"]
    if "expected_saturation_rate" in config:
        expected["saturation_rate_range"] = config["expected_saturation_rate"]
    if "expected_auto_rate" in config:
        expected["auto_rate_range"] = config["expected_auto_rate"]
    if "expected_ar_coeffs" in config:
        expected["ar_coeffs"] = config["expected_ar_coeffs"]

    return {
        "scenario": scenario_name,
        "description": config["description"],
        "sample_interval_sec": interval,
        "duration_sec": DURATION_SEC,
        "n_points": n,
        "pv_range": pv_range,
        "control_type": config.get("control_type", "STABLE"),
        "expected": expected,
        "ar_signal": [round(float(v), 6) for v in ar_signal],  # 纯 AR 偏差信号
        "data": data,
    }


# ============================================================================
# 主函数
# ============================================================================


def main() -> None:
    """生成全部场景测试数据并写入 JSON 文件。"""
    output_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "kpi_test_data.json"

    all_data = {}
    for scenario_name, config in SCENARIOS.items():
        print(f"生成场景 [{scenario_name}]: {config['description']}...")
        all_data[scenario_name] = generate_scenario_data(scenario_name, config)
        settling = all_data[scenario_name]["expected"].get("settling_time_sec", "N/A")
        print(f"  → {all_data[scenario_name]['n_points']} 点, 预期稳态时间={settling}s")

    # 写入 JSON
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    file_size = os.path.getsize(output_file) / 1024 / 1024
    print(f"\n测试数据已生成: {output_file}")
    print(f"文件大小: {file_size:.1f} MB")
    print(f"场景数量: {len(all_data)}")
    print(f"每场景数据点: {N_POINTS}（{DURATION_SEC}s @ {SAMPLE_INTERVAL}Hz）")

    # 打印预期结果汇总
    print("\n预期结果汇总:")
    print("-" * 80)
    for name, data in all_data.items():
        exp = data["expected"]
        print(f"  {name:20s} | {data['description']}")
        for k, v in exp.items():
            print(f"  {'':22s} |   {k}: {v}")
    print("-" * 80)


if __name__ == "__main__":
    main()
